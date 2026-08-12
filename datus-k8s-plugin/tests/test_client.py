from __future__ import annotations

import sys
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from datetime import datetime, timedelta, timezone

from datus_k8s_plugin.cloud_contract import (
    ClusterConnection,
    ExecCredential,
    KubernetesAccess,
)
from datus_k8s_plugin.client import (
    LoadedClient,
    ManagedCredentialProvider,
    _provider_command,
    exec_exit_code,
)
from datus_k8s_plugin.config import Settings
from datus_k8s_plugin.errors import ApiError, UsageError


def settings(path, **overrides):
    data = {
        "kubeconfig": str(path),
        "namespace": "analytics",
        "allowed_namespaces": "analytics",
    }
    data.update(overrides)
    return Settings.from_profile(data)


def fake_import(active_name="dev"):
    class Config:
        loaded = None

        @staticmethod
        def list_kube_config_contexts(config_file):
            return ([{"name": "dev"}, {"name": "prod"}], {"name": active_name})

        @staticmethod
        def load_kube_config(config_file, context):
            Config.loaded = (config_file, context)

    api_client = object()

    class Client:
        @staticmethod
        def ApiClient():
            return api_client

    dynamic_client = SimpleNamespace(resources=SimpleNamespace())

    class Dynamic:
        @staticmethod
        def DynamicClient(value):
            assert value is api_client
            return dynamic_client

    return (
        SimpleNamespace(),
        Client,
        Config,
        Dynamic,
        SimpleNamespace(),
        Exception,
        Exception,
        Exception,
    )


def test_context_falls_back_to_kubeconfig_current_context(tmp_path, monkeypatch):
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    imported = fake_import("dev")
    monkeypatch.setattr("datus_k8s_plugin.client._import_kubernetes", lambda: imported)

    loaded = LoadedClient.load(settings(kubeconfig))
    assert loaded.context_name == "dev"
    assert imported[2].loaded == (str(kubeconfig), "dev")


def test_explicit_context_wins(tmp_path, monkeypatch):
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    imported = fake_import("dev")
    monkeypatch.setattr("datus_k8s_plugin.client._import_kubernetes", lambda: imported)

    loaded = LoadedClient.load(settings(kubeconfig, context="prod"))
    assert loaded.context_name == "prod"


def test_managed_provider_uses_cloud_plugin_contract_and_caches_token(monkeypatch):
    configured = Settings.from_profile(
        {"name": "dev", "provider": "eks", "namespace": "analytics"}
    )
    calls = []

    def fake_run(_settings, *args):
        calls.append(args)
        if args == ("kubernetes", "cluster"):
            return ClusterConnection(
                "eks", "orders", "https://cluster.example", "Q0E="
            ).to_dict()
        return ExecCredential(
            "secret-token", datetime.now(timezone.utc) + timedelta(minutes=10)
        ).to_dict()

    monkeypatch.setattr("datus_k8s_plugin.client.run_cloud_provider", fake_run)
    provider = ManagedCredentialProvider(configured)
    assert provider.connection().server == "https://cluster.example"
    assert provider.credential().token == "secret-token"
    assert provider.credential().token == "secret-token"
    assert calls == [
        ("kubernetes", "cluster"),
        ("kubernetes", "credential"),
    ]


def test_managed_provider_propagates_explicit_provider_config():
    configured = Settings.from_profile(
        {
            "name": "dev",
            "provider": "eks",
            "namespace": "analytics",
            "provider_config": "/etc/datus/agent.yml",
        }
    )
    assert _provider_command(configured, "kubernetes", "credential") == [
        "datus",
        "eks",
        "--config",
        "/etc/datus/agent.yml",
        "--profile",
        "dev",
        "kubernetes",
        "credential",
    ]


def test_managed_provider_accepts_cluster_owned_by_provider(monkeypatch):
    configured = Settings.from_profile(
        {"name": "dev", "provider": "eks", "namespace": "analytics"}
    )
    monkeypatch.setattr(
        "datus_k8s_plugin.client.run_cloud_provider",
        lambda *_args: ClusterConnection(
            "eks", "another", "https://cluster.example", "Q0E="
        ).to_dict(),
    )
    assert ManagedCredentialProvider(configured).connection().cluster == "another"


def test_managed_profile_builds_real_kubernetes_configuration(monkeypatch):
    configured = Settings.from_profile(
        {"name": "dev", "provider": "eks", "namespace": "analytics"}
    )

    def fake_run(_settings, *args):
        if args == ("kubernetes", "cluster"):
            return ClusterConnection(
                "eks", "orders", "https://cluster.example", "Q0E="
            ).to_dict()
        return ExecCredential(
            "secret-token", datetime.now(timezone.utc) + timedelta(minutes=10)
        ).to_dict()

    monkeypatch.setattr("datus_k8s_plugin.client.run_cloud_provider", fake_run)
    loaded = LoadedClient.load(configured)
    assert loaded.kubeconfig_path is None
    assert loaded.context_name == "eks:orders"
    assert loaded.managed_cluster == "orders"
    assert loaded.api_client.configuration.host == "https://cluster.example"
    assert loaded.api_client.configuration.get_api_key_with_prefix("authorization") == (
        "Bearer secret-token"
    )
    assert loaded.credential_provider is not None
    loaded.close()


def test_managed_profile_installs_and_cleans_up_client_certificate(monkeypatch):
    configured = Settings.from_profile(
        {"name": "dev", "provider": "ack", "namespace": "analytics"}
    )
    certificate = "-----BEGIN CERTIFICATE-----\nCERT\n-----END CERTIFICATE-----\n"
    private_key = "-----BEGIN PRIVATE KEY-----\nKEY\n-----END PRIVATE KEY-----\n"

    calls = []

    def fake_run(_settings, *args):
        calls.append(args)
        credential = ExecCredential(
            None,
            datetime.now(timezone.utc) + timedelta(minutes=10),
            certificate,
            private_key,
        )
        if args == ("kubernetes", "access"):
            return KubernetesAccess(
                ClusterConnection(
                    "ack", "orders", "https://cluster.example", "Q0E="
                ),
                credential,
            ).to_dict()
        return credential.to_dict()

    monkeypatch.setattr("datus_k8s_plugin.client.run_cloud_provider", fake_run)
    loaded = LoadedClient.load(configured)
    configuration = loaded.api_client.configuration
    cert_file = Path(configuration.cert_file)
    key_file = Path(configuration.key_file)
    assert cert_file.read_text() == certificate
    assert key_file.read_text() == private_key
    assert stat.S_IMODE(cert_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(cert_file.parent.stat().st_mode) == 0o700
    assert "authorization" not in configuration.api_key
    assert calls == [("kubernetes", "access")]
    loaded.close()
    assert not cert_file.parent.exists()


def test_managed_provider_refreshes_expiring_client_certificate_in_place(monkeypatch):
    configured = Settings.from_profile(
        {"name": "dev", "provider": "ack", "namespace": "analytics"}
    )
    old = ExecCredential(
        None,
        datetime.now(timezone.utc) + timedelta(seconds=30),
        "-----BEGIN CERTIFICATE-----\nOLD\n-----END CERTIFICATE-----\n",
        "-----BEGIN PRIVATE KEY-----\nOLD\n-----END PRIVATE KEY-----\n",
    )
    new = ExecCredential(
        None,
        datetime.now(timezone.utc) + timedelta(minutes=10),
        "-----BEGIN CERTIFICATE-----\nNEW\n-----END CERTIFICATE-----\n",
        "-----BEGIN PRIVATE KEY-----\nNEW\n-----END PRIVATE KEY-----\n",
    )
    calls = []

    def fake_run(_settings, *args):
        calls.append(args)
        return new.to_dict()

    monkeypatch.setattr("datus_k8s_plugin.client.run_cloud_provider", fake_run)
    provider = ManagedCredentialProvider(configured)
    provider._credential = old
    configuration = SimpleNamespace(
        api_key={}, api_key_prefix={}, cert_file=None, key_file=None
    )
    assert provider.refresh(configuration) is True
    cert_file = Path(configuration.cert_file)
    key_file = Path(configuration.key_file)
    assert "NEW" in cert_file.read_text()
    assert "NEW" in key_file.read_text()
    assert calls == [("kubernetes", "credential")]
    provider.close()
    assert not cert_file.parent.exists()


def test_cluster_scoped_resource_is_rejected_before_use(tmp_path):
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    resources = SimpleNamespace(
        get=lambda **_kwargs: SimpleNamespace(namespaced=False, kind="Node")
    )
    loaded = LoadedClient(
        settings(kubeconfig),
        kubeconfig,
        "dev",
        object(),
        SimpleNamespace(),
        object(),
        object(),
        SimpleNamespace(resources=resources),
    )
    with pytest.raises(UsageError, match="cluster-scoped"):
        loaded.resource("nodes")


def test_api_resources_deduplicates_discovery_results(tmp_path):
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    pod = SimpleNamespace(
        namespaced=True,
        name="pods",
        short_names=["po"],
        group_version="v1",
        kind="Pod",
        verbs=["get", "list"],
    )
    pod_list = SimpleNamespace(
        namespaced=True,
        name="pods",
        short_names=["po"],
        group_version="v1",
        kind="PodList",
        verbs=["get", "list"],
    )
    resources = SimpleNamespace(search=lambda: [pod, pod, pod_list])
    loaded = LoadedClient(
        settings(kubeconfig),
        kubeconfig,
        "dev",
        object(),
        SimpleNamespace(),
        object(),
        object(),
        SimpleNamespace(resources=resources),
    )
    assert loaded.api_resources() == [
        {
            "name": "pods",
            "shortNames": ["po"],
            "apiVersion": "v1",
            "kind": "Pod",
            "verbs": ["get", "list"],
        }
    ]


def test_resource_resolves_singular_and_short_names(tmp_path):
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    deployment = SimpleNamespace(
        namespaced=True,
        name="deployments",
        singular_name="deployment",
        short_names=["deploy"],
        group="apps",
        group_version="apps/v1",
        kind="Deployment",
        preferred=True,
    )

    class Resources:
        def get(self, **kwargs):
            if kwargs.get("name") == "deployments":
                return deployment
            raise LookupError(kwargs)

        def search(self):
            return [deployment]

    loaded = LoadedClient(
        settings(kubeconfig),
        kubeconfig,
        "dev",
        object(),
        SimpleNamespace(),
        object(),
        object(),
        SimpleNamespace(resources=Resources()),
    )
    assert loaded.resource("deployment") is deployment
    assert loaded.resource("deploy") is deployment


def _discovery_dynamic_module():
    class Resource:
        def __init__(
            self,
            *,
            prefix,
            group,
            api_version,
            kind,
            namespaced,
            verbs,
            name,
            preferred,
            client,
            singularName=None,
            shortNames=None,
            categories=None,
            subresources=None,
            **_kwargs,
        ):
            self.prefix = prefix
            self.group = group
            self.api_version = api_version
            self.group_version = f"{group}/{api_version}" if group else api_version
            self.kind = kind
            self.namespaced = namespaced
            self.verbs = verbs
            self.name = name
            self.preferred = preferred
            self.client = client
            self.singular_name = singularName or ""
            self.short_names = shortNames or []
            self.categories = categories or []
            self.subresources = subresources or {}

    class DynamicClient:
        def __init__(self, api_client):
            self.api_client = api_client

    return SimpleNamespace(Resource=Resource, DynamicClient=DynamicClient)


def _loaded_for_discovery(tmp_path, api_client):
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    return LoadedClient(
        settings(kubeconfig),
        kubeconfig,
        "dev",
        api_client,
        _discovery_dynamic_module(),
        object(),
        object(),
    )


def test_resource_uses_aggregated_discovery_without_visiting_each_group(tmp_path):
    calls = []
    core = {
        "kind": "APIGroupDiscoveryList",
        "apiVersion": "apidiscovery.k8s.io/v2",
        "items": [
            {
                "metadata": {},
                "versions": [
                    {
                        "version": "v1",
                        "freshness": "Current",
                        "resources": [
                            {
                                "resource": "pods",
                                "responseKind": {"kind": "Pod"},
                                "scope": "Namespaced",
                                "singularResource": "pod",
                                "verbs": ["get", "list"],
                                "shortNames": ["po"],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    groups = {
        "kind": "APIGroupDiscoveryList",
        "apiVersion": "apidiscovery.k8s.io/v2",
        "items": [
            {
                "metadata": {"name": "metrics"},
                "versions": [
                    {
                        "version": "v1alpha1",
                        "freshness": "Current",
                        "resources": [],
                    }
                ],
            }
        ],
    }

    class ApiClient:
        def call_api(self, path, *_args, **_kwargs):
            calls.append(path)
            if path == "/api":
                return core
            if path == "/apis":
                return groups
            raise AssertionError(f"aggregated discovery must not request {path}")

    resource = _loaded_for_discovery(tmp_path, ApiClient()).resource("pods")
    assert resource.group_version == "v1"
    assert resource.kind == "Pod"
    assert calls == ["/api", "/apis"]


def test_legacy_discovery_skips_malformed_unrelated_api_group(tmp_path):
    calls = []

    class ApiClient:
        def call_api(self, path, *_args, **_kwargs):
            calls.append(path)
            if path == "/api":
                return {"kind": "APIVersions", "versions": ["v1"]}
            if path == "/api/v1":
                return {
                    "kind": "APIResourceList",
                    "groupVersion": "v1",
                    "resources": [
                        {
                            "name": "pods",
                            "singularName": "pod",
                            "namespaced": True,
                            "kind": "Pod",
                            "verbs": ["get", "list"],
                            "shortNames": ["po"],
                        },
                        {
                            "name": "pods/status",
                            "singularName": "",
                            "namespaced": True,
                            "kind": "Pod",
                            "verbs": ["get", "patch", "update"],
                        }
                    ],
                }
            if path == "/apis":
                return {
                    "kind": "APIGroupList",
                    "groups": [
                        {
                            "name": "metrics",
                            "preferredVersion": {
                                "groupVersion": "metrics/v1alpha1",
                                "version": "v1alpha1",
                            },
                            "versions": [
                                {
                                    "groupVersion": "metrics/v1alpha1",
                                    "version": "v1alpha1",
                                }
                            ],
                        }
                    ],
                }
            if path == "/apis/metrics/v1alpha1":
                return {"Text": "alibabacloud metrics-server is running."}
            raise AssertionError(path)

    resource = _loaded_for_discovery(tmp_path, ApiClient()).resource("pods")
    assert resource.group_version == "v1"
    assert resource.kind == "Pod"
    assert resource.subresources["status"]["kind"] == "Pod"
    assert "/apis/metrics/v1alpha1" in calls


def test_openapi_all_of_reference_is_expanded(tmp_path):
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    resource = SimpleNamespace(
        namespaced=True,
        name="deployments",
        kind="Deployment",
        group_version="apps/v1",
    )
    index = {
        "paths": {
            "apis/apps/v1": {
                "serverRelativeURL": "/openapi/v3/apis/apps/v1?hash=test"
            }
        }
    }
    document = {
        "components": {
            "schemas": {
                "Deployment": {
                    "x-kubernetes-group-version-kind": [
                        {"group": "apps", "version": "v1", "kind": "Deployment"}
                    ],
                    "properties": {
                        "spec": {"allOf": [{"$ref": "#/components/schemas/DeploymentSpec"}]}
                    },
                },
                "DeploymentSpec": {
                    "type": "object",
                    "properties": {"replicas": {"type": "integer"}},
                },
            }
        }
    }

    class ApiClient:
        def call_api(self, path, *_args, **_kwargs):
            return index if path == "/openapi/v3" else document

    loaded = LoadedClient(
        settings(kubeconfig),
        kubeconfig,
        "dev",
        ApiClient(),
        SimpleNamespace(),
        object(),
        object(),
        SimpleNamespace(resources=SimpleNamespace()),
    )
    schema = loaded.explain_schema(resource, "spec.replicas")
    assert schema["type"] == "integer"


class FakeExecSession:
    """Stand-in for kubernetes.stream's WSClient with a scripted set of channels."""

    def __init__(self, stdout="", stderr="", status="{\"status\": \"Success\"}"):
        self.frames = {1: stdout, 2: stderr, 3: status}
        self.ran_with = None
        self.closed = False

    def run_forever(self, timeout=None):
        self.ran_with = timeout

    def read_stdout(self, timeout=None):
        return self.frames.pop(1, "")

    def read_stderr(self, timeout=None):
        return self.frames.pop(2, "")

    def read_channel(self, channel, timeout=None):
        return self.frames.pop(channel, "")

    def close(self):
        self.closed = True


def loaded_client_for_exec(tmp_path, monkeypatch, session):
    """A LoadedClient whose CoreV1Api/stream pair is fully faked.

    ``kubernetes`` is injected through ``monkeypatch.setitem`` so the stub is
    removed afterwards and cannot make a later test believe the real client is
    installed (or missing).
    """
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    connect = object()
    typed = SimpleNamespace(
        CoreV1Api=lambda _api_client: SimpleNamespace(
            connect_get_namespaced_pod_exec=connect
        )
    )
    calls = {}

    def open_stream(func, pod, namespace, **kwargs):
        calls.update({"func": func, "pod": pod, "namespace": namespace, **kwargs})
        return session

    stream_module = SimpleNamespace(stream=open_stream)
    monkeypatch.setitem(sys.modules, "kubernetes", SimpleNamespace(stream=stream_module))
    monkeypatch.setitem(sys.modules, "kubernetes.stream", stream_module)
    loaded = LoadedClient(
        settings(kubeconfig),
        kubeconfig,
        "dev",
        object(),
        SimpleNamespace(),
        typed,
        object(),
        SimpleNamespace(resources=SimpleNamespace()),
    )
    return loaded, calls, connect


def test_exec_in_pod_disables_stdin_and_tty_and_returns_the_exit_code(tmp_path, monkeypatch):
    session = FakeExecSession(stdout="CLASSPATH=/opt/lib/a.jar\n")
    loaded, calls, connect = loaded_client_for_exec(tmp_path, monkeypatch, session)

    stdout, stderr, code = loaded.exec_in_pod(
        pod="fe-0",
        namespace="analytics",
        command=["sh", "-c", "echo hi"],
        container="fe",
        timeout=30.0,
    )

    assert (stdout, stderr, code) == ("CLASSPATH=/opt/lib/a.jar\n", "", 0)
    assert calls["func"] is connect
    assert (calls["pod"], calls["namespace"]) == ("fe-0", "analytics")
    assert calls["command"] == ["sh", "-c", "echo hi"]
    assert calls["container"] == "fe"
    assert calls["stdin"] is False and calls["tty"] is False
    assert calls["_preload_content"] is False
    assert session.ran_with == 30.0
    assert session.closed is True


def test_exec_in_pod_omits_the_container_when_none_is_requested(tmp_path, monkeypatch):
    loaded, calls, _connect = loaded_client_for_exec(tmp_path, monkeypatch, FakeExecSession())
    loaded.exec_in_pod(pod="fe-0", namespace="analytics", command=["ls"])
    assert "container" not in calls


def test_exec_in_pod_closes_the_session_even_when_the_status_frame_is_missing(tmp_path, monkeypatch):
    session = FakeExecSession(status="")
    loaded, _calls, _connect = loaded_client_for_exec(tmp_path, monkeypatch, session)
    with pytest.raises(ApiError, match="without a status frame"):
        loaded.exec_in_pod(pod="fe-0", namespace="analytics", command=["ls"])
    assert session.closed is True


def test_exec_exit_code_reads_a_non_zero_exit_from_the_status_frame():
    payload = yaml.safe_dump(
        {
            "status": "Failure",
            "reason": "NonZeroExitCode",
            "details": {"causes": [{"reason": "ExitCode", "message": "127"}]},
        }
    )
    assert exec_exit_code(payload) == 127


def test_exec_exit_code_surfaces_a_server_rejection_instead_of_inventing_a_code():
    payload = yaml.safe_dump(
        {"status": "Failure", "message": "container fe is not valid for pod fe-0"}
    )
    with pytest.raises(ApiError, match="not valid for pod"):
        exec_exit_code(payload)


def test_exec_exit_code_rejects_an_unreadable_status_frame():
    with pytest.raises(ApiError, match="unreadable status frame"):
        exec_exit_code("Success")

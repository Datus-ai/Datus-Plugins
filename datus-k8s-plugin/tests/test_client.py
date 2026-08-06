from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
import yaml

from datus_k8s_plugin.client import LoadedClient, exec_exit_code
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

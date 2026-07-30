from __future__ import annotations

from types import SimpleNamespace

import pytest

from datus_k8s_plugin.client import LoadedClient
from datus_k8s_plugin.config import Settings
from datus_k8s_plugin.errors import UsageError


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
    resources = SimpleNamespace(search=lambda: [pod, pod])
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

from __future__ import annotations

import pytest

from datus_k8s_plugin.cli import main


class FakeResource:
    namespaced = True
    name = "pods"
    kind = "Pod"
    group_version = "v1"

    def __init__(self):
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        name = kwargs.get("name", "pod-a")
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": name, "namespace": kwargs["namespace"]},
            "status": {"phase": "Running"},
        }

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return kwargs["body"]

    def patch(self, **kwargs):
        self.calls.append(("patch", kwargs))
        body = kwargs["body"]
        body.setdefault("metadata", {}).setdefault("name", kwargs.get("name", "pod-a"))
        return body


class FakeClient:
    def __init__(self, settings):
        self.settings = settings
        self.resource_obj = FakeResource()

    def namespace(self, requested):
        return self.settings.check_namespace(requested)

    def resource(self, *_args, **_kwargs):
        return self.resource_obj

    def get(self, resource, names, namespace, **_kwargs):
        if names:
            items = [self.resource_obj.get(name=name, namespace=namespace) for name in names]
        else:
            items = [self.resource_obj.get(namespace=namespace)]
        return {"apiVersion": "v1", "kind": "List", "items": items}

    def manifest_resource(self, document):
        metadata = document.setdefault("metadata", {})
        namespace = self.settings.check_namespace(metadata.get("namespace"))
        metadata["namespace"] = namespace
        return self.resource_obj, namespace, metadata["name"]


class FakeContext:
    last = None

    def __init__(self, settings):
        self.settings = settings
        self.client = FakeClient(settings)
        FakeContext.last = self


@pytest.fixture
def patch_context(monkeypatch):
    monkeypatch.setattr("datus_k8s_plugin.cli.Context", FakeContext)


def profile():
    return {
        "kubeconfig": "./conf/kubeconfig.yaml",
        "namespace": "analytics",
        "allowed_namespaces": "analytics,analytics-staging",
    }


def test_get_uses_allowed_namespace_and_json_output(patch_context, capsys):
    assert main(["get", "pods", "pod-a", "-n", "analytics", "-o", "json"], profile()) == 0
    output = capsys.readouterr().out
    assert '"name": "pod-a"' in output
    assert '"namespace": "analytics"' in output


def test_all_namespaces_is_rejected_before_api_call(patch_context, capsys):
    assert main(["get", "pods", "-A"], profile()) == 2
    assert "blocked" in capsys.readouterr().err
    assert FakeContext.last.client.resource_obj.calls == []


def test_out_of_scope_namespace_is_rejected_before_api_call(patch_context, capsys):
    assert main(["get", "pods", "-n", "other"], profile()) == 2
    assert "outside" in capsys.readouterr().err
    assert FakeContext.last.client.resource_obj.calls == []


def test_apply_uses_server_side_content_type(patch_context, tmp_path, capsys):
    manifest = tmp_path / "pod.yaml"
    manifest.write_text(
        "apiVersion: v1\nkind: Pod\nmetadata:\n  name: pod-a\nspec: {}\n",
        encoding="utf-8",
    )
    assert main(["apply", "-f", str(manifest), "-n", "analytics"], profile()) == 0
    method, kwargs = FakeContext.last.client.resource_obj.calls[-1]
    assert method == "patch"
    assert kwargs["content_type"] == "application/apply-patch+yaml"
    assert kwargs["field_manager"] == "datus-k8s"
    assert kwargs["force_conflicts"] is False
    assert kwargs["body"]["metadata"]["namespace"] == "analytics"


def test_manifest_namespace_cannot_escape_profile(patch_context, tmp_path, capsys):
    manifest = tmp_path / "pod.yaml"
    manifest.write_text(
        "apiVersion: v1\nkind: Pod\nmetadata:\n  name: pod-a\n  namespace: other\n",
        encoding="utf-8",
    )
    assert main(["create", "-f", str(manifest)], profile()) == 2
    assert "outside" in capsys.readouterr().err
    assert FakeContext.last.client.resource_obj.calls == []


def test_unknown_command_is_usage_error(capsys):
    assert main(["frobnicate"], profile()) == 2


def test_missing_profile_is_config_error(capsys):
    assert main(["get", "pods"], {}) == 3
    assert "kubeconfig is required" in capsys.readouterr().err

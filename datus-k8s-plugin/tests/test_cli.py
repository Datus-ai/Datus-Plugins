from __future__ import annotations

from types import SimpleNamespace

import pytest

from datus_k8s_plugin.cli import main


class FakeResource:
    namespaced = True
    name = "pods"
    kind = "Pod"
    group_version = "v1"
    #: Overridden per test with monkeypatch so the value never leaks between tests.
    status_payload = {"phase": "Running"}

    def __init__(self):
        self.calls = []

    spec_payload = {
        "initContainers": [{"name": "copy-runtime"}, {"name": "download-assets"}],
        "containers": [{"name": "flink-main-container"}],
    }

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        name = kwargs.get("name", "pod-a")
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": name, "namespace": kwargs["namespace"]},
            "spec": dict(type(self).spec_payload),
            "status": dict(type(self).status_payload),
        }

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return kwargs["body"]

    def patch(self, **kwargs):
        self.calls.append(("patch", kwargs))
        body = kwargs["body"]
        body.setdefault("metadata", {}).setdefault("name", kwargs.get("name", "pod-a"))
        return body


class FakeCoreV1Api:
    #: A container the API server refuses to serve a log for, the way it does for
    #: one that has not started yet.
    unavailable = ("download-assets",)

    def read_namespaced_pod_log(self, **kwargs):
        container = kwargs.get("container")
        if container in type(self).unavailable:
            raise RuntimeError(f"container {container} is waiting to start")
        return f"line from {container}\n"


class FakeClient:
    #: Overridden per test with monkeypatch so the value never leaks between tests.
    exec_result = ("classpath=/opt/lib/a.jar\n", "", 0)

    def __init__(self, settings):
        self.settings = settings
        self.resource_obj = FakeResource()
        self.exec_calls = []
        self.api_client = object()
        self.typed = SimpleNamespace(CoreV1Api=lambda _api_client: FakeCoreV1Api())

    def namespace(self, requested):
        return self.settings.check_namespace(requested)

    def request_timeout(self):
        return self.settings.timeout_seconds or None

    def exec_in_pod(self, **kwargs):
        self.exec_calls.append(kwargs)
        return self.exec_result

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


def test_exec_passes_the_command_after_the_separator(patch_context, capsys):
    argv = ["exec", "pod-a", "-c", "fe", "-n", "analytics", "--", "sh", "-c", "ls -1 /opt/lib"]
    assert main(argv, profile()) == 0
    call = FakeContext.last.client.exec_calls[-1]
    assert call["pod"] == "pod-a"
    assert call["container"] == "fe"
    assert call["namespace"] == "analytics"
    assert call["command"] == ["sh", "-c", "ls -1 /opt/lib"]
    assert "classpath=/opt/lib/a.jar" in capsys.readouterr().out


def test_exec_propagates_the_remote_exit_code_with_an_unambiguous_message(
    patch_context, monkeypatch, capsys
):
    monkeypatch.setattr(FakeClient, "exec_result", ("", "jar: not found\n", 127))
    assert main(["exec", "pod-a", "--", "jar", "tf", "x.jar"], profile()) == 127
    captured = capsys.readouterr()
    assert "jar: not found" in captured.err
    assert "command terminated with exit code 127" in captured.err


def test_exec_without_a_command_is_a_usage_error(patch_context, capsys):
    assert main(["exec", "pod-a", "-n", "analytics"], profile()) == 2
    assert "after `--`" in capsys.readouterr().err
    assert FakeContext.last.client.exec_calls == []


def test_exec_cannot_escape_the_profile_namespace(patch_context, capsys):
    assert main(["exec", "pod-a", "-n", "other", "--", "ls"], profile()) == 2
    assert "outside" in capsys.readouterr().err
    assert FakeContext.last.client.exec_calls == []


def test_logs_all_containers_reads_init_containers_first_and_survives_one_failure(
    patch_context, capsys
):
    """A multi-container pod is the normal case for an operator-managed workload,
    and the init container is usually the one that broke."""
    assert main(["logs", "pod-a", "-n", "analytics", "--all-containers"], profile()) == 1
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "==== pod-a/copy-runtime ====",
        "line from copy-runtime",
        "==== pod-a/download-assets ====",
        "==== pod-a/flink-main-container ====",
        "line from flink-main-container",
    ]
    assert "download-assets is waiting to start" in captured.err


def test_logs_all_containers_conflicts_are_usage_errors(patch_context, capsys):
    argv = ["logs", "pod-a", "-n", "analytics", "--all-containers", "-c", "app"]
    assert main(argv, profile()) == 2
    assert "cannot be combined with -c/--container" in capsys.readouterr().err
    assert main(argv[:-2] + ["-f"], profile()) == 2
    assert "cannot be combined with -f/--follow" in capsys.readouterr().err


FAILED_JOB_STATUS = {
    "jobManagerDeploymentStatus": "READY",
    "jobStatus": {"state": "FAILED"},
    "error": '{"message":"NoClassDefFoundError: org/apache/hadoop/conf/Configuration"}',
}


def test_get_jsonpath_output_prints_one_field_instead_of_the_whole_object(
    patch_context, monkeypatch, capsys
):
    monkeypatch.setattr(FakeResource, "status_payload", FAILED_JOB_STATUS)
    argv = ["get", "flinkdeployment", "job-a", "-n", "analytics", "-o", "jsonpath={.status.error}"]
    assert main(argv, profile()) == 0
    output = capsys.readouterr().out
    assert output.strip() == FAILED_JOB_STATUS["error"]


def test_get_rejects_an_output_format_it_cannot_render(patch_context, capsys):
    assert main(["get", "pods", "-n", "analytics", "-o", "gotemplate"], profile()) == 2
    assert "unsupported output format" in capsys.readouterr().err


def test_wait_is_not_a_command(capsys):
    assert main(["wait", "pods", "pod-a"], profile()) == 2
    assert "invalid choice: 'wait'" in capsys.readouterr().err


def test_unknown_command_is_usage_error(capsys):
    assert main(["frobnicate"], profile()) == 2


def test_missing_profile_is_config_error(capsys):
    assert main(["get", "pods"], {}) == 3
    assert "exactly one of kubeconfig or provider" in capsys.readouterr().err

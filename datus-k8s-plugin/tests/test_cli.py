from __future__ import annotations

import pytest

from datus_k8s_plugin.cli import _condition_met, main
from datus_k8s_plugin.errors import UsageError


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
    #: Overridden per test with monkeypatch so the value never leaks between tests.
    exec_result = ("classpath=/opt/lib/a.jar\n", "", 0)

    def __init__(self, settings):
        self.settings = settings
        self.resource_obj = FakeResource()
        self.exec_calls = []

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


def test_wait_accepts_a_jsonpath_condition_on_a_custom_status_field(patch_context, capsys):
    argv = ["wait", "pods", "pod-a", "-n", "analytics", "--for", "jsonpath={.status.phase}=Running"]
    assert main(argv, profile()) == 0
    assert "condition met" in capsys.readouterr().out


def test_wait_times_out_when_the_jsonpath_value_never_matches(patch_context, capsys):
    argv = [
        "wait",
        "pods",
        "pod-a",
        "-n",
        "analytics",
        "--for",
        "jsonpath={.status.phase}=Succeeded",
        "--timeout",
        "1s",
    ]
    assert main(argv, profile()) == 1
    assert "timed out waiting for" in capsys.readouterr().err


def test_wait_rejects_an_unsupported_for_expression(patch_context, capsys):
    argv = ["wait", "pods", "pod-a", "-n", "analytics", "--for", "phase=Running"]
    assert main(argv, profile()) == 2
    assert "--for must be" in capsys.readouterr().err


def test_unknown_command_is_usage_error(capsys):
    assert main(["frobnicate"], profile()) == 2


def test_missing_profile_is_config_error(capsys):
    assert main(["get", "pods"], {}) == 3
    assert "kubeconfig is required" in capsys.readouterr().err


@pytest.mark.parametrize(
    "condition,expected",
    [
        # A FlinkDeployment-shaped status: readiness lives outside status.conditions.
        ("jsonpath={.status.jobStatus.state}=RUNNING", True),
        ("jsonpath={.status.jobStatus.state}=FAILED", False),
        ("jsonpath={.status.jobManagerDeploymentStatus}", True),
        ("jsonpath={.status.absent}", False),
        ("jsonpath={.status.replicas[0].name}=tm-0", True),
        ("jsonpath={.status.replicas[9].name}=tm-0", False),
        ("jsonpath=.status.jobStatus.state=RUNNING", True),
        ("condition=Ready", True),
        ("condition=Ready=False", False),
    ],
)
def test_condition_met_supports_conditions_and_the_jsonpath_subset(condition, expected):
    item = {
        "status": {
            "jobManagerDeploymentStatus": "READY",
            "jobStatus": {"state": "RUNNING"},
            "replicas": [{"name": "tm-0"}],
            "conditions": [{"type": "Ready", "status": "True"}],
        }
    }
    assert _condition_met(item, condition) is expected


@pytest.mark.parametrize(
    "condition",
    [
        "jsonpath={.status.state",  # unterminated brace
        "jsonpath={.status.a[*]}",  # wildcard is not in the supported subset
        "jsonpath={.status.a}!=b",  # only =VALUE may follow the expression
        "jsonpath=status.state",  # must start at the object root
        "phase=Running",  # not one of the supported --for forms
    ],
)
def test_condition_met_rejects_expressions_it_cannot_evaluate_exactly(condition):
    with pytest.raises(UsageError):
        _condition_met({"status": {"a": ["x"]}}, condition)

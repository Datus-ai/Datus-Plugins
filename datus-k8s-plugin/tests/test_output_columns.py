"""The table has to show a failure, because it is the first thing anyone reads."""

from __future__ import annotations

import pytest

from datus_k8s_plugin.errors import UsageError
from datus_k8s_plugin.output import output_format, render, resource_list


def pod(
    name: str = "pod-a",
    *,
    phase: str = "Running",
    containers: list[dict] | None = None,
    init_containers: list[dict] | None = None,
    **status,
) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": "analytics", "creationTimestamp": None},
        "spec": {"nodeName": "ip-10-0-0-1"},
        "status": {
            "phase": phase,
            "podIP": "10.0.1.5",
            "containerStatuses": containers if containers is not None else [],
            "initContainerStatuses": init_containers or [],
            **status,
        },
    }


def running(name: str = "app", restarts: int = 0) -> dict:
    return {"name": name, "ready": True, "restartCount": restarts, "state": {"running": {}}}


def waiting(reason: str, name: str = "app", restarts: int = 0) -> dict:
    return {
        "name": name,
        "ready": False,
        "restartCount": restarts,
        "state": {"waiting": {"reason": reason}},
    }


def terminated(exit_code: int, name: str = "init", reason: str = "Completed") -> dict:
    return {
        "name": name,
        "ready": False,
        "restartCount": 0,
        "state": {"terminated": {"exitCode": exit_code, "reason": reason}},
    }


def columns(text: str) -> list[list[str]]:
    return [line.split() for line in text.splitlines()]


def test_pod_table_reports_readiness_and_restarts():
    text = render(resource_list([pod(containers=[running(restarts=3)])]), "table", "pods")
    assert columns(text)[0][:4] == ["NAME", "READY", "STATUS", "RESTARTS"]
    assert columns(text)[1][:4] == ["pod-a", "1/1", "Running", "3"]


def test_pod_table_shows_a_crash_looping_init_container_instead_of_pending():
    """The pod that stalled a real deployment: phase says Pending, the init
    container is the thing that is actually broken, and its restart count is the
    proof. Reading `status.phase` alone reports a healthy-looking `Pending`."""
    item = pod(
        phase="Pending",
        containers=[waiting("PodInitializing")],
        init_containers=[
            terminated(0, name="copy-runtime"),
            waiting("CrashLoopBackOff", name="download-paimon", restarts=6),
        ],
    )
    row = columns(render(resource_list([item]), "table", "pods"))[1]
    assert row[:4] == ["pod-a", "0/1", "Init:CrashLoopBackOff", "6"]


def test_pod_table_reports_a_failed_init_container_exit():
    item = pod(
        phase="Pending",
        containers=[waiting("PodInitializing")],
        init_containers=[terminated(1, name="download", reason="Error")],
    )
    assert columns(render(resource_list([item]), "table", "pods"))[1][2] == "Init:Error"


def test_pod_wide_reports_ip_and_node_rather_than_api_version():
    text = render(resource_list([pod(containers=[running()])]), "wide", "pods")
    assert columns(text)[0][-2:] == ["IP", "NODE"]
    assert columns(text)[1][-2:] == ["10.0.1.5", "ip-10-0-0-1"]


def test_pod_being_deleted_reports_terminating():
    item = pod(containers=[running()])
    item["metadata"]["deletionTimestamp"] = "2026-08-06T17:24:48Z"
    assert columns(render(resource_list([item]), "table", "pods"))[1][2] == "Terminating"


def test_event_table_carries_the_reason_and_the_message():
    """An event's entire value is its reason and message. Rendering NAME/STATUS
    prints a page of opaque hashes and a blank column."""
    item = {
        "apiVersion": "v1",
        "kind": "Event",
        "metadata": {"name": "pod-a.18c94744e546ac13", "namespace": "analytics"},
        "involvedObject": {"kind": "Pod", "name": "pod-a"},
        "type": "Warning",
        "reason": "BackOff",
        "message": "Back-off restarting failed container=download-paimon",
        "lastTimestamp": None,
        "count": 6,
    }
    text = render(resource_list([item]), "table", "Event")
    assert columns(text)[0][:2] == ["LAST", "SEEN"]  # "LAST SEEN" splits on the space
    assert "Warning" in text and "BackOff" in text
    assert "pod/pod-a" in text
    assert "Back-off restarting failed container=download-paimon" in text
    assert "18c94744e546ac13" not in text


def test_event_wide_adds_the_namespace_and_the_repeat_count():
    item = {
        "kind": "Event",
        "metadata": {"name": "e1", "namespace": "analytics"},
        "involvedObject": {"kind": "Pod", "name": "pod-a"},
        "type": "Warning",
        "reason": "Failed",
        "message": "boom",
        "count": 6,
    }
    text = render(resource_list([item]), "wide", "Event")
    assert columns(text)[0][:2] == ["NAMESPACE", "COUNT"]
    assert columns(text)[1][:2] == ["analytics", "6"]


#: A FlinkDeployment: readiness and failure both live outside status.phase and
#: status.conditions, so the generic renderer used to print a blank STATUS.
FLINK_DEPLOYMENT = {
    "apiVersion": "flink.apache.org/v1beta1",
    "kind": "FlinkDeployment",
    "metadata": {"name": "orders", "namespace": "analytics"},
    "status": {
        "lifecycleState": "FAILED",
        "jobManagerDeploymentStatus": "READY",
        "jobStatus": {"state": "FAILED"},
        "error": '{"message":"NoClassDefFoundError: org/apache/hadoop/conf/Configuration"}',
    },
}


def test_custom_resource_status_is_not_a_blank_column():
    text = render(resource_list([FLINK_DEPLOYMENT]), "table", "flinkdeployments")
    assert columns(text)[1][:2] == ["orders", "FAILED"]


def test_custom_resource_wide_surfaces_the_failure_text():
    text = render(resource_list([FLINK_DEPLOYMENT]), "wide", "flinkdeployments")
    assert columns(text)[0][-1] == "MESSAGE"
    assert "NoClassDefFoundError" in text


def test_a_false_condition_message_is_used_when_there_is_no_status_error():
    item = {
        "kind": "Job",
        "metadata": {"name": "daily-etl", "namespace": "analytics"},
        "status": {
            "conditions": [
                {"type": "Failed", "status": "False", "message": "backoff limit exceeded"}
            ]
        },
    }
    assert "backoff limit exceeded" in render(resource_list([item]), "wide", "jobs")


def test_a_long_failure_text_is_truncated_in_the_table_only():
    item = {
        "kind": "FlinkDeployment",
        "metadata": {"name": "orders", "namespace": "analytics"},
        "status": {"error": "x" * 500},
    }
    wide = render(resource_list([item]), "wide", "flinkdeployments")
    assert "…" in wide
    # The full value stays reachable through the field itself.
    assert render(resource_list([item]), "jsonpath={.status.error}", None) == "x" * 500


def test_jsonpath_output_prints_one_line_per_object_and_blanks_a_missing_field():
    items = resource_list(
        [
            {"metadata": {"name": "a"}, "status": {"phase": "Running"}},
            {"metadata": {"name": "b"}, "status": {}},
        ]
    )
    assert render(items, "jsonpath={.status.phase}", None) == "Running\n"


def test_jsonpath_output_renders_a_structured_field_as_json():
    items = resource_list([{"status": {"jobStatus": {"state": "RUNNING"}}}])
    assert render(items, "jsonpath={.status.jobStatus}", None) == '{"state": "RUNNING"}'


@pytest.mark.parametrize("value", ["table", "wide", "json", "yaml", "name", "jsonpath={.a}"])
def test_output_format_accepts_every_renderable_form(value):
    assert output_format(value) == value


@pytest.mark.parametrize("value", ["gotemplate", "custom-columns=A:.a", "JSON", ""])
def test_output_format_rejects_what_the_renderer_cannot_produce(value):
    with pytest.raises(UsageError):
        output_format(value)

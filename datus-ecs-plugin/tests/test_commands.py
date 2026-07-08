"""Command tests for datus ecs."""

from __future__ import annotations


def test_clusters_list(run_cli, clients, capsys):
    clients["ecs"].set_pages("list_clusters", [{"clusterArns": ["arn:aws:ecs:::cluster/c1"]}])
    clients["ecs"].set(
        "describe_clusters",
        {"clusters": [{"clusterName": "c1", "status": "ACTIVE", "runningTasksCount": 2, "activeServicesCount": 1}]},
    )
    assert run_cli(["clusters", "list"]) == 0
    assert "c1" in capsys.readouterr().out


def test_services_scale(run_cli, clients):
    assert run_cli(["services", "scale", "prod", "web", "4"]) == 0
    kwargs = clients["ecs"].calls_to("update_service")[0]["kwargs"]
    assert kwargs["desiredCount"] == 4 and kwargs["service"] == "web"


def test_tasks_run_wait_success(run_cli, clients, capsys):
    clients["ecs"].set("run_task", {"tasks": [{"taskArn": "arn:aws:ecs:::task/t1"}]})
    clients["ecs"].set(
        "describe_tasks",
        [
            {"tasks": [{"lastStatus": "RUNNING", "containers": []}]},
            {"tasks": [{"lastStatus": "STOPPED", "containers": [{"exitCode": 0}]}]},
        ],
    )
    rc = run_cli(["tasks", "run", "prod", "--task-def", "etl:3", "--launch-type", "FARGATE", "--wait", "--interval", "0"])
    assert rc == 0
    kwargs = clients["ecs"].calls_to("run_task")[0]["kwargs"]
    assert kwargs["taskDefinition"] == "etl:3" and kwargs["launchType"] == "FARGATE"


def test_tasks_run_nonzero_exit_returns_1(run_cli, clients):
    clients["ecs"].set("run_task", {"tasks": [{"taskArn": "arn:aws:ecs:::task/t2"}]})
    clients["ecs"].set("describe_tasks", {"tasks": [{"lastStatus": "STOPPED", "containers": [{"exitCode": 1}]}]})
    assert run_cli(["tasks", "run", "prod", "--task-def", "etl:3", "--wait", "--interval", "0"]) == 1


def test_tasks_run_uses_default_cluster_and_awsvpc(run_cli, clients):
    clients["ecs"].set("run_task", {"tasks": [{"taskArn": "arn:t3"}]})
    rc = run_cli(
        ["tasks", "run", "--task-def", "etl:3", "--subnet", "subnet-1", "--security-group", "sg-1", "--assign-public-ip"],
        {"region": "us-east-1", "cluster": "default-cluster"},
    )
    assert rc == 0
    kwargs = clients["ecs"].calls_to("run_task")[0]["kwargs"]
    assert kwargs["cluster"] == "default-cluster"
    vpc = kwargs["networkConfiguration"]["awsvpcConfiguration"]
    assert vpc["subnets"] == ["subnet-1"] and vpc["securityGroups"] == ["sg-1"] and vpc["assignPublicIp"] == "ENABLED"


def test_tasks_stop(run_cli, clients):
    assert run_cli(["tasks", "stop", "prod", "arn:t1"]) == 0
    assert clients["ecs"].calls_to("stop_task")[0]["kwargs"]["task"] == "arn:t1"


def test_tasks_logs(run_cli, clients, capsys):
    clients["logs"].set("get_log_events", {"events": [{"timestamp": 1, "message": "hello"}]})
    rc = run_cli(["tasks", "logs", "prod", "arn:aws:ecs:::task/abc123", "--container", "web"],
                 {"region": "us-east-1", "log_group": "/ecs/app"})
    assert rc == 0
    assert "hello" in capsys.readouterr().out
    assert clients["logs"].calls_to("get_log_events")[0]["kwargs"]["logStreamName"] == "ecs/web/abc123"


def test_tasks_logs_needs_log_group(run_cli, clients):
    assert run_cli(["tasks", "logs", "prod", "t1", "--container", "web"]) == 2


def test_taskdefs_list(run_cli, clients, capsys):
    clients["ecs"].set_pages("list_task_definitions", [{"taskDefinitionArns": ["arn:td/etl:1"]}])
    assert run_cli(["task-defs", "list"]) == 0
    assert "etl:1" in capsys.readouterr().out

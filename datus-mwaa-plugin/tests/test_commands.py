"""Command tests for datus mwaa."""

from __future__ import annotations

import base64


def test_environments_list(run_cli, clients, capsys):
    clients["mwaa"].set_pages("list_environments", [{"Environments": ["prod", "staging"]}])
    assert run_cli(["environments", "list"]) == 0
    out = capsys.readouterr().out
    assert "prod" in out and "staging" in out


def test_environments_get(run_cli, clients, capsys):
    clients["mwaa"].set("get_environment", {"Environment": {"Name": "prod", "Status": "AVAILABLE"}})
    assert run_cli(["environments", "get", "prod"]) == 0
    assert "AVAILABLE" in capsys.readouterr().out


def test_token_cli(run_cli, clients, capsys):
    clients["mwaa"].set("create_cli_token", {"CliToken": "tok123", "WebServerHostname": "host.example.com"})
    assert run_cli(["token", "cli", "prod"]) == 0
    out = capsys.readouterr().out
    assert "tok123" in out and "host.example.com" in out


def test_token_web_login(run_cli, clients, capsys):
    clients["mwaa"].set("create_web_login_token", {"WebToken": "wt", "WebServerHostname": "host.example.com"})
    assert run_cli(["token", "web-login", "prod"]) == 0
    assert "host.example.com" in capsys.readouterr().out


def test_token_uses_default_environment(run_cli, clients):
    clients["mwaa"].set("create_cli_token", {"CliToken": "t", "WebServerHostname": "h"})
    assert run_cli(["token", "cli"], {"region": "us-east-1", "environment": "prod"}) == 0
    assert clients["mwaa"].calls_to("create_cli_token")[0]["kwargs"]["Name"] == "prod"


def test_token_missing_env_is_usage_error(run_cli, clients):
    assert run_cli(["token", "cli"]) == 2  # no env arg, none in config


def test_cli_run_invokes_rest_and_decodes(run_cli, clients, monkeypatch, capsys):
    clients["mwaa"].set("create_cli_token", {"CliToken": "tok", "WebServerHostname": "host"})
    import datus_mwaa_plugin.cli.cli_cmd as cli_cmd

    captured = {}

    def fake_invoke(hostname, token, command, timeout=30.0):
        captured.update(hostname=hostname, token=token, command=command)
        return {"stdout": base64.b64encode(b"dag_a\ndag_b").decode(), "stderr": ""}

    monkeypatch.setattr(cli_cmd, "_invoke_cli", fake_invoke)
    assert run_cli(["cli", "run", "dags list", "--env", "prod"]) == 0
    assert "dag_a" in capsys.readouterr().out
    assert captured == {"hostname": "host", "token": "tok", "command": "dags list"}

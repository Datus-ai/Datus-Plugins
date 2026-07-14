"""Command tests for datus iam."""

from __future__ import annotations


def test_whoami(run_cli, clients, capsys):
    clients["sts"].set("get_caller_identity", {"Account": "123456789012", "Arn": "arn:aws:iam::1:user/me", "UserId": "AID"})
    assert run_cli(["whoami"]) == 0
    out = capsys.readouterr().out
    assert "123456789012" in out and "arn:aws:iam::1:user/me" in out


def test_roles_list(run_cli, clients, capsys):
    clients["iam"].set_pages("list_roles", [{"Roles": [{"RoleName": "svc", "Arn": "arn:aws:iam::1:role/svc"}]}])
    assert run_cli(["roles", "list"]) == 0
    assert "svc" in capsys.readouterr().out


def test_roles_trust(run_cli, clients, capsys):
    clients["iam"].set("get_role", {"Role": {"RoleName": "svc", "AssumeRolePolicyDocument": {"Version": "2012-10-17"}}})
    assert run_cli(["roles", "trust", "svc"]) == 0
    assert "2012-10-17" in capsys.readouterr().out


def test_policies_document_two_step(run_cli, clients, capsys):
    clients["iam"].set("get_policy", {"Policy": {"DefaultVersionId": "v3"}})
    clients["iam"].set("get_policy_version", {"PolicyVersion": {"Document": {"Statement": [{"Effect": "Allow"}]}}})
    assert run_cli(["policies", "document", "arn:aws:iam::1:policy/p"]) == 0
    assert "Allow" in capsys.readouterr().out
    assert clients["iam"].calls_to("get_policy_version")[0]["kwargs"]["VersionId"] == "v3"


def test_simulate_principal(run_cli, clients, capsys):
    clients["iam"].set(
        "simulate_principal_policy",
        {"EvaluationResults": [{"EvalActionName": "s3:GetObject", "EvalDecision": "allowed", "EvalResourceName": "*"}]},
    )
    rc = run_cli(["simulate", "principal", "arn:aws:iam::1:role/r", "--action", "s3:GetObject"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "s3:GetObject" in out and "allowed" in out
    kwargs = clients["iam"].calls_to("simulate_principal_policy")[0]["kwargs"]
    assert kwargs["ActionNames"] == ["s3:GetObject"]
    assert kwargs["ResourceArns"] == ["*"]

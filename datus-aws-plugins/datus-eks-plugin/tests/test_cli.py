from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import datus_eks_plugin.cli as cli
from datus_eks_plugin.cloud_contract import ClusterConnection, ExecCredential


class FakeEks:
    def can_paginate(self, _operation):
        return False

    def list_clusters(self, **_kwargs):
        return {"clusters": ["dev", "prod"]}

    def describe_cluster(self, **kwargs):
        return {"cluster": {"name": kwargs["name"], "status": "ACTIVE"}}

    def list_nodegroups(self, **kwargs):
        assert kwargs["clusterName"] == "dev"
        return {"nodegroups": ["workers"]}

    def describe_nodegroup(self, **kwargs):
        return {"nodegroup": kwargs}

    def list_addons(self, **_kwargs):
        return {"addons": ["coredns"]}

    def describe_addon(self, **kwargs):
        return {"addon": kwargs}

    def list_access_entries(self, **_kwargs):
        return {"accessEntries": ["arn:aws:iam::123:role/dev"]}

    def describe_access_entry(self, **kwargs):
        return {"accessEntry": kwargs}

    def list_fargate_profiles(self, **_kwargs):
        return {"fargateProfileNames": ["serverless"]}

    def describe_fargate_profile(self, **kwargs):
        return {"fargateProfile": kwargs}

    def list_updates(self, **_kwargs):
        return {"updateIds": ["update-1"]}

    def describe_update(self, **kwargs):
        return {"update": kwargs}

    def list_insights(self, **_kwargs):
        return {"insights": [{"id": "i-1", "status": "PASSING"}]}

    def describe_insight(self, **kwargs):
        return {"insight": kwargs}


class FakeSts:
    def get_caller_identity(self):
        return {"Account": "123", "UserId": "user", "Arn": "arn:caller"}


class FakeContext:
    def __init__(self, settings):
        self.settings = settings
        self.eks = FakeEks()
        self.sts = FakeSts()

    def client(self, service):
        return self.eks if service == "eks" else self.sts

    def cluster_connection(self):
        return ClusterConnection("eks", "dev", "https://cluster.example", "Q0E=")

    def exec_credential(self):
        return ExecCredential(
            "secret-token",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )


def profile():
    return {"name": "dev", "cluster": "dev", "region": "us-east-1"}


def test_read_only_resource_commands(monkeypatch, capsys):
    monkeypatch.setattr(cli, "EksContext", FakeContext)
    for argv, expected in (
        (["clusters", "list", "-o", "json"], "prod"),
        (["clusters", "describe", "-o", "json"], "ACTIVE"),
        (["nodegroups", "list", "-o", "json"], "workers"),
        (["addons", "describe", "coredns", "-o", "json"], "coredns"),
        (["access-entries", "list", "-o", "json"], "role/dev"),
        (["fargate-profiles", "list", "-o", "json"], "serverless"),
        (["updates", "describe", "update-1", "-o", "json"], "update-1"),
        (["insights", "list", "-o", "json"], "PASSING"),
        (["auth", "whoami", "-o", "json"], "arn:caller"),
    ):
        assert cli.main(argv, profile()) == 0
        assert expected in capsys.readouterr().out


def test_machine_cluster_contract(monkeypatch, capsys):
    monkeypatch.setattr(cli, "EksContext", FakeContext)
    assert cli.main(["kubernetes", "cluster"], profile()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "apiVersion": "cloud-k8s.datus.ai/v1",
        "kind": "ClusterConnection",
        "provider": "eks",
        "cluster": "dev",
        "server": "https://cluster.example",
        "certificateAuthorityData": "Q0E=",
    }


def test_machine_credential_contract(monkeypatch, capsys):
    monkeypatch.setattr(cli, "EksContext", FakeContext)
    assert cli.main(["kubernetes", "credential"], profile()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["apiVersion"] == "client.authentication.k8s.io/v1"
    assert payload["status"]["token"] == "secret-token"

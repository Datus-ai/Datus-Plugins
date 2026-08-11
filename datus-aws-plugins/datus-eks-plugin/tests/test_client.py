from __future__ import annotations

from types import SimpleNamespace

import pytest

from datus_aws_common import ApiError
from datus_eks_plugin.client import EksContext
from datus_eks_plugin.config import Settings


class FakeEks:
    def __init__(self, response):
        self.response = response

    def describe_cluster(self, name):
        assert name == "dev"
        return self.response


def context(response):
    value = EksContext(Settings.from_profile({"cluster": "dev", "region": "us-east-1"}))
    value._clients["eks"] = FakeEks(response)
    return value


def test_cluster_connection_uses_eks_provider_name():
    value = context(
        {
            "cluster": {
                "status": "ACTIVE",
                "endpoint": "https://cluster.example",
                "certificateAuthority": {"data": "Q0E="},
            }
        }
    ).cluster_connection()
    assert value.provider == "eks"
    assert value.cluster == "dev"


def test_cluster_connection_rejects_unusable_cluster():
    with pytest.raises(ApiError, match="not usable"):
        context({"cluster": {"status": "FAILED"}}).cluster_connection()


def test_cluster_connection_requires_https_and_ca():
    with pytest.raises(ApiError, match="https"):
        context(
            {
                "cluster": {
                    "status": "ACTIVE",
                    "endpoint": "http://cluster.example",
                    "certificateAuthority": {"data": "Q0E="},
                }
            }
        ).cluster_connection()

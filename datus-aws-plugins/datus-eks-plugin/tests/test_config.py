from __future__ import annotations

import pytest

from datus_aws_common import ConfigError
from datus_eks_plugin.config import Settings


def test_cluster_is_required():
    with pytest.raises(ConfigError, match="cluster is required"):
        Settings.from_profile({"region": "us-east-1"})


def test_profile_builds_aws_settings():
    settings = Settings.from_profile(
        {
            "name": "dev",
            "cluster": "datus-dev-eks-cluster",
            "region": "us-east-1",
            "role_arn": "arn:aws:iam::123:role/dev",
        }
    )
    assert settings.cluster == "datus-dev-eks-cluster"
    assert settings.profile_name == "dev"
    assert settings.aws.region == "us-east-1"
    assert settings.aws.role_arn == "arn:aws:iam::123:role/dev"


def test_unknown_field_is_rejected():
    with pytest.raises(ConfigError, match="bogus"):
        Settings.from_profile({"cluster": "dev", "bogus": True})

"""Shared helpers for the QuickSight command modules."""

from __future__ import annotations

import dataclasses

from datus_aws_common import build_client


def acct(ctx) -> str:
    return ctx.settings.account()


def qs(ctx):
    """Regional QuickSight client (assets, ingestions, embed, ...)."""
    return ctx.client("quicksight")


def qs_identity(ctx):
    """QuickSight client pinned to the identity region (users/groups/namespaces).

    QuickSight identity operations must target the region the account was
    created in; `identity_region` overrides the profile region when set.
    """
    key = "quicksight-identity"
    if key not in ctx._clients:
        region = ctx.settings.identity_region or ctx.settings.aws.region
        aws = dataclasses.replace(ctx.settings.aws, region=region)
        ctx._clients[key] = build_client(aws, "quicksight")
    return ctx._clients[key]


def namespace(ctx) -> str:
    return ctx.settings.namespace

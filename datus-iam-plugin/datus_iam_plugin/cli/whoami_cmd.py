"""`datus iam whoami` — the caller identity (STS)."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, render_one


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("whoami", help="show the current caller identity (STS)")
    add_output_option(p)
    p.set_defaults(func=cmd_whoami)


def cmd_whoami(ctx, ns) -> int:
    resp = call(ctx.client("sts").get_caller_identity)
    view = {"Account": resp.get("Account"), "UserId": resp.get("UserId"), "Arn": resp.get("Arn")}
    print(render_one(view, ns.output))
    return 0

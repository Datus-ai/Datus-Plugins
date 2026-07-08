"""`datus quicksight account ...` — account settings and subscription."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, render_one

from ._helpers import acct, qs


def register(sub: argparse._SubParsersAction) -> None:
    account = sub.add_parser("account", help="QuickSight account: settings, subscription")
    group = account.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("settings", help="show account settings")
    add_output_option(p)
    p.set_defaults(func=cmd_settings)

    p = group.add_parser("subscription", help="show the account subscription/edition")
    add_output_option(p)
    p.set_defaults(func=cmd_subscription)


def cmd_settings(ctx, ns) -> int:
    resp = call(qs(ctx).describe_account_settings, AwsAccountId=acct(ctx))
    print(render_one(resp.get("AccountSettings", {}), ns.output))
    return 0


def cmd_subscription(ctx, ns) -> int:
    resp = call(qs(ctx).describe_account_subscription, AwsAccountId=acct(ctx))
    print(render_one(resp.get("AccountInfo", {}), ns.output))
    return 0

"""`datus quicksight themes ...` — full theme lifecycle."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, confirm, paginate, parse_json_arg, render_one, render_rows

from ._helpers import acct, qs


def register(sub: argparse._SubParsersAction) -> None:
    themes = sub.add_parser("themes", help="QuickSight themes: list/describe/create/update/delete")
    group = themes.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list themes")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one theme")
    p.add_argument("theme_id")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)

    p = group.add_parser("create", help="create a theme from a JSON request body")
    p.add_argument("--cli-input", required=True, help="CreateTheme request as JSON (without AwsAccountId)")
    p.set_defaults(func=cmd_create)

    p = group.add_parser("update", help="update a theme from a JSON request body")
    p.add_argument("--cli-input", required=True, help="UpdateTheme request as JSON (without AwsAccountId)")
    p.set_defaults(func=cmd_update)

    p = group.add_parser("delete", help="delete a theme")
    p.add_argument("theme_id")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_delete)


def cmd_list(ctx, ns) -> int:
    rows = paginate(qs(ctx), "list_themes", "ThemeSummaryList", limit=ns.limit, AwsAccountId=acct(ctx))
    print(render_rows(rows, ["ThemeId", "Name", "LatestVersionNumber", "LastUpdatedTime"], ns.output))
    return 0


def cmd_describe(ctx, ns) -> int:
    theme = call(qs(ctx).describe_theme, AwsAccountId=acct(ctx), ThemeId=ns.theme_id)["Theme"]
    print(render_one(theme, ns.output))
    return 0


def cmd_create(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).create_theme, AwsAccountId=acct(ctx), **body)
    print(f"created theme {resp.get('ThemeId')}")
    return 0


def cmd_update(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).update_theme, AwsAccountId=acct(ctx), **body)
    print(f"updated theme {resp.get('ThemeId')}")
    return 0


def cmd_delete(ctx, ns) -> int:
    if not confirm(f"delete theme {ns.theme_id}?", ns.yes):
        print("aborted")
        return 1
    call(qs(ctx).delete_theme, AwsAccountId=acct(ctx), ThemeId=ns.theme_id)
    print(f"deleted theme {ns.theme_id}")
    return 0

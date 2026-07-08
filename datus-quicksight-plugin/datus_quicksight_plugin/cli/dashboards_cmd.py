"""`datus quicksight dashboards ...` — lifecycle, versions, permissions, embed URLs."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, confirm, paginate, parse_json_arg, render_one, render_rows

from ._helpers import acct, qs


def register(sub: argparse._SubParsersAction) -> None:
    dashboards = sub.add_parser("dashboards", help="QuickSight dashboards: lifecycle, versions, permissions, embed")
    group = dashboards.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list dashboards")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one dashboard")
    p.add_argument("dashboard_id")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)

    p = group.add_parser("versions", help="list a dashboard's versions")
    p.add_argument("dashboard_id")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_versions)

    p = group.add_parser("permissions", help="show a dashboard's permissions")
    p.add_argument("dashboard_id")
    add_output_option(p)
    p.set_defaults(func=cmd_permissions)

    p = group.add_parser("create", help="create a dashboard from a JSON request body")
    p.add_argument("--cli-input", required=True, help="CreateDashboard request as JSON (without AwsAccountId)")
    p.set_defaults(func=cmd_create)

    p = group.add_parser("update", help="update a dashboard from a JSON request body")
    p.add_argument("--cli-input", required=True, help="UpdateDashboard request as JSON (without AwsAccountId)")
    p.set_defaults(func=cmd_update)

    p = group.add_parser("publish", help="set the published version of a dashboard")
    p.add_argument("dashboard_id")
    p.add_argument("version_number", type=int)
    p.set_defaults(func=cmd_publish)

    p = group.add_parser("delete", help="delete a dashboard")
    p.add_argument("dashboard_id")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_delete)

    p = group.add_parser("set-permissions", help="grant/revoke dashboard permissions from a JSON body")
    p.add_argument("dashboard_id")
    p.add_argument("--cli-input", required=True, help="UpdateDashboardPermissions body as JSON")
    p.set_defaults(func=cmd_set_permissions)

    p = group.add_parser("embed-url", help="embed URL for a registered user")
    p.add_argument("dashboard_id")
    p.add_argument("--user-arn", required=True)
    p.add_argument("--session-lifetime", type=int)
    p.set_defaults(func=cmd_embed_url)

    p = group.add_parser("embed-url-anonymous", help="embed URL for an anonymous user")
    p.add_argument("dashboard_id")
    p.add_argument("--namespace", default="default")
    p.add_argument("--authorized-resource-arn", action="append", dest="authorized_arns",
                   help="ARN the anonymous session may access (repeatable; default the dashboard)")
    p.add_argument("--session-lifetime", type=int)
    p.set_defaults(func=cmd_embed_url_anonymous)


def cmd_list(ctx, ns) -> int:
    rows = paginate(qs(ctx), "list_dashboards", "DashboardSummaryList", limit=ns.limit, AwsAccountId=acct(ctx))
    print(render_rows(rows, ["DashboardId", "Name", "LastUpdatedTime", "PublishedVersionNumber"], ns.output))
    return 0


def cmd_describe(ctx, ns) -> int:
    dashboard = call(qs(ctx).describe_dashboard, AwsAccountId=acct(ctx), DashboardId=ns.dashboard_id)["Dashboard"]
    print(render_one(dashboard, ns.output))
    return 0


def cmd_versions(ctx, ns) -> int:
    rows = paginate(
        qs(ctx), "list_dashboard_versions", "DashboardVersionSummaryList",
        limit=ns.limit, AwsAccountId=acct(ctx), DashboardId=ns.dashboard_id,
    )
    print(render_rows(rows, ["VersionNumber", "Status", "CreatedTime", "SourceEntityArn"], ns.output))
    return 0


def cmd_permissions(ctx, ns) -> int:
    resp = call(qs(ctx).describe_dashboard_permissions, AwsAccountId=acct(ctx), DashboardId=ns.dashboard_id)
    print(render_rows(resp.get("Permissions", []), ["Principal", "Actions"], ns.output))
    return 0


def cmd_create(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).create_dashboard, AwsAccountId=acct(ctx), **body)
    print(f"created dashboard {resp.get('DashboardId')}")
    return 0


def cmd_update(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).update_dashboard, AwsAccountId=acct(ctx), **body)
    print(f"updated dashboard {resp.get('DashboardId')} (version {resp.get('VersionArn')})")
    return 0


def cmd_publish(ctx, ns) -> int:
    call(qs(ctx).update_dashboard_published_version,
         AwsAccountId=acct(ctx), DashboardId=ns.dashboard_id, VersionNumber=ns.version_number)
    print(f"published dashboard {ns.dashboard_id} version {ns.version_number}")
    return 0


def cmd_delete(ctx, ns) -> int:
    if not confirm(f"delete dashboard {ns.dashboard_id}?", ns.yes):
        print("aborted")
        return 1
    call(qs(ctx).delete_dashboard, AwsAccountId=acct(ctx), DashboardId=ns.dashboard_id)
    print(f"deleted dashboard {ns.dashboard_id}")
    return 0


def cmd_set_permissions(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    call(qs(ctx).update_dashboard_permissions, AwsAccountId=acct(ctx), DashboardId=ns.dashboard_id, **body)
    print(f"updated permissions for dashboard {ns.dashboard_id}")
    return 0


def cmd_embed_url(ctx, ns) -> int:
    kwargs = {
        "AwsAccountId": acct(ctx),
        "UserArn": ns.user_arn,
        "ExperienceConfiguration": {"Dashboard": {"InitialDashboardId": ns.dashboard_id}},
    }
    if ns.session_lifetime:
        kwargs["SessionLifetimeInMinutes"] = ns.session_lifetime
    resp = call(qs(ctx).generate_embed_url_for_registered_user, **kwargs)
    print(resp.get("EmbedUrl"))
    return 0


def cmd_embed_url_anonymous(ctx, ns) -> int:
    account = acct(ctx)
    arns = ns.authorized_arns or [f"arn:aws:quicksight:*:{account}:dashboard/{ns.dashboard_id}"]
    kwargs = {
        "AwsAccountId": account,
        "Namespace": ns.namespace,
        "AuthorizedResourceArns": arns,
        "ExperienceConfiguration": {"Dashboard": {"InitialDashboardId": ns.dashboard_id}},
    }
    if ns.session_lifetime:
        kwargs["SessionLifetimeInMinutes"] = ns.session_lifetime
    resp = call(qs(ctx).generate_embed_url_for_anonymous_user, **kwargs)
    print(resp.get("EmbedUrl"))
    return 0

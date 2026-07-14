"""`datus quicksight analyses ...` — full analysis lifecycle."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, confirm, paginate, parse_json_arg, render_one, render_rows

from ._helpers import acct, qs


def register(sub: argparse._SubParsersAction) -> None:
    analyses = sub.add_parser("analyses", help="QuickSight analyses: list/describe/create/update/delete/restore")
    group = analyses.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list analyses")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("describe", help="describe one analysis")
    p.add_argument("analysis_id")
    add_output_option(p)
    p.set_defaults(func=cmd_describe)

    p = group.add_parser("create", help="create an analysis from a JSON request body")
    p.add_argument("--cli-input", required=True, help="CreateAnalysis request as JSON (without AwsAccountId)")
    p.set_defaults(func=cmd_create)

    p = group.add_parser("update", help="update an analysis from a JSON request body")
    p.add_argument("--cli-input", required=True, help="UpdateAnalysis request as JSON (without AwsAccountId)")
    p.set_defaults(func=cmd_update)

    p = group.add_parser("delete", help="delete an analysis")
    p.add_argument("analysis_id")
    p.add_argument("--force", action="store_true", help="skip the recovery window (ForceDeleteWithoutRecovery)")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_delete)

    p = group.add_parser("restore", help="restore a deleted analysis (within the recovery window)")
    p.add_argument("analysis_id")
    p.set_defaults(func=cmd_restore)


def cmd_list(ctx, ns) -> int:
    rows = paginate(qs(ctx), "list_analyses", "AnalysisSummaryList", limit=ns.limit, AwsAccountId=acct(ctx))
    print(render_rows(rows, ["AnalysisId", "Name", "Status", "LastUpdatedTime"], ns.output))
    return 0


def cmd_describe(ctx, ns) -> int:
    analysis = call(qs(ctx).describe_analysis, AwsAccountId=acct(ctx), AnalysisId=ns.analysis_id)["Analysis"]
    print(render_one(analysis, ns.output))
    return 0


def cmd_create(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).create_analysis, AwsAccountId=acct(ctx), **body)
    print(f"created analysis {resp.get('AnalysisId')}")
    return 0


def cmd_update(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).update_analysis, AwsAccountId=acct(ctx), **body)
    print(f"updated analysis {resp.get('AnalysisId')}")
    return 0


def cmd_delete(ctx, ns) -> int:
    if not confirm(f"delete analysis {ns.analysis_id}?", ns.yes):
        print("aborted")
        return 1
    kwargs = {"AwsAccountId": acct(ctx), "AnalysisId": ns.analysis_id}
    if ns.force:
        kwargs["ForceDeleteWithoutRecovery"] = True
    call(qs(ctx).delete_analysis, **kwargs)
    print(f"deleted analysis {ns.analysis_id}")
    return 0


def cmd_restore(ctx, ns) -> int:
    call(qs(ctx).restore_analysis, AwsAccountId=acct(ctx), AnalysisId=ns.analysis_id)
    print(f"restored analysis {ns.analysis_id}")
    return 0

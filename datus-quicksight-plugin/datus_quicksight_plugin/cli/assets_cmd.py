"""`datus quicksight assets ...` — asset-bundle export/import (migration)."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, parse_json_arg, render_one

from ._helpers import acct, qs


def register(sub: argparse._SubParsersAction) -> None:
    assets = sub.add_parser("assets", help="QuickSight asset bundles: export/import")
    group = assets.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("export", help="start an asset-bundle export job from a JSON body")
    p.add_argument("--cli-input", required=True, help="StartAssetBundleExportJob request as JSON (without AwsAccountId)")
    add_output_option(p)
    p.set_defaults(func=cmd_export)

    p = group.add_parser("export-status", help="describe an export job (download URL when ready)")
    p.add_argument("job_id")
    add_output_option(p)
    p.set_defaults(func=cmd_export_status)

    p = group.add_parser("import", help="start an asset-bundle import job from a JSON body")
    p.add_argument("--cli-input", required=True, help="StartAssetBundleImportJob request as JSON (without AwsAccountId)")
    add_output_option(p)
    p.set_defaults(func=cmd_import)

    p = group.add_parser("import-status", help="describe an import job")
    p.add_argument("job_id")
    add_output_option(p)
    p.set_defaults(func=cmd_import_status)


def cmd_export(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).start_asset_bundle_export_job, AwsAccountId=acct(ctx), **body)
    print(f"started export job {resp.get('AssetBundleExportJobId')}")
    return 0


def cmd_export_status(ctx, ns) -> int:
    resp = call(qs(ctx).describe_asset_bundle_export_job, AwsAccountId=acct(ctx), AssetBundleExportJobId=ns.job_id)
    print(render_one(resp, ns.output))
    return 0


def cmd_import(ctx, ns) -> int:
    body = parse_json_arg(ns.cli_input, "--cli-input")
    resp = call(qs(ctx).start_asset_bundle_import_job, AwsAccountId=acct(ctx), **body)
    print(f"started import job {resp.get('AssetBundleImportJobId')}")
    return 0


def cmd_import_status(ctx, ns) -> int:
    resp = call(qs(ctx).describe_asset_bundle_import_job, AwsAccountId=acct(ctx), AssetBundleImportJobId=ns.job_id)
    print(render_one(resp, ns.output))
    return 0

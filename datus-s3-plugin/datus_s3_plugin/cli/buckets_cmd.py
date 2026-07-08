"""`datus s3 buckets ...` — list buckets and get a bucket's region."""

from __future__ import annotations

import argparse

from datus_aws_common import add_output_option, call, render_one, render_rows


def register(sub: argparse._SubParsersAction) -> None:
    buckets = sub.add_parser("buckets", help="list buckets / get bucket region")
    group = buckets.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list all buckets")
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("location", help="get a bucket's region")
    p.add_argument("bucket")
    add_output_option(p)
    p.set_defaults(func=cmd_location)


def cmd_list(ctx, ns) -> int:
    resp = call(ctx.client("s3").list_buckets)
    print(render_rows(resp.get("Buckets", []), ["Name", "CreationDate"], ns.output))
    return 0


def cmd_location(ctx, ns) -> int:
    resp = call(ctx.client("s3").get_bucket_location, Bucket=ns.bucket)
    region = resp.get("LocationConstraint") or "us-east-1"
    print(render_one({"Bucket": ns.bucket, "Region": region}, ns.output))
    return 0

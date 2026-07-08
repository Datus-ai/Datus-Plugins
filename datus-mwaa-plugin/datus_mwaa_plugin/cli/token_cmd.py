"""`datus mwaa token ...` — mint short-lived web-login and CLI tokens."""

from __future__ import annotations

import argparse

from datus_aws_common import UsageError, add_output_option, call, render_one


def register(sub: argparse._SubParsersAction) -> None:
    token = sub.add_parser("token", help="mint MWAA tokens: web-login, cli")
    group = token.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("web-login", help="create a one-time Airflow UI login URL")
    p.add_argument("name", nargs="?", help="environment (default: profile's environment)")
    add_output_option(p)
    p.set_defaults(func=cmd_web_login)

    p = group.add_parser("cli", help="create a CLI token + web server hostname")
    p.add_argument("name", nargs="?", help="environment (default: profile's environment)")
    add_output_option(p)
    p.set_defaults(func=cmd_cli)


def _resolve_env(ctx, name):
    env = name or ctx.settings.environment
    if not env:
        raise UsageError("no environment (arg or config environment)")
    return env


def cmd_web_login(ctx, ns) -> int:
    env = _resolve_env(ctx, ns.name)
    resp = call(ctx.client("mwaa").create_web_login_token, Name=env)
    host = resp.get("WebServerHostname")
    token = resp.get("WebToken")
    view = {"LoginUrl": f"https://{host}/aws_mwaa/aws-console-sso?login=true#{token}" if host and token else None,
            "WebServerHostname": host}
    print(render_one(view, ns.output))
    return 0


def cmd_cli(ctx, ns) -> int:
    env = _resolve_env(ctx, ns.name)
    resp = call(ctx.client("mwaa").create_cli_token, Name=env)
    view = {"CliToken": resp.get("CliToken"), "WebServerHostname": resp.get("WebServerHostname")}
    print(render_one(view, ns.output))
    return 0

"""`datus mwaa cli run ...` — run an Airflow CLI command over the MWAA REST API.

This mints a CLI token, then POSTs the command to
``https://<hostname>/aws_mwaa/cli`` and decodes the base64 stdout/stderr. It is
an opaque passthrough (the wrapped command may be destructive), so it is always
confirmed by the agent — prefer the dedicated `datus airflow` plugin for
fine-grained, permission-classified DAG operations.
"""

from __future__ import annotations

import argparse
import base64

import requests

from datus_aws_common import PluginError, UsageError, call, eprint


def register(sub: argparse._SubParsersAction) -> None:
    cli = sub.add_parser("cli", help="run the Airflow CLI over MWAA's REST endpoint")
    group = cli.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("run", help="run an Airflow CLI command, e.g. 'dags list'")
    p.add_argument("command", help="the Airflow CLI command (a single quoted string)")
    p.add_argument("--env", help="environment (default: profile's environment)")
    p.add_argument("--timeout", type=float, default=30.0)
    p.set_defaults(func=cmd_run)


def _invoke_cli(hostname: str, token: str, command: str, timeout: float = 30.0) -> dict:
    resp = requests.post(
        f"https://{hostname}/aws_mwaa/cli",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"},
        data=command,
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise PluginError(f"MWAA CLI HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    return resp.json()


def cmd_run(ctx, ns) -> int:
    env = ns.env or ctx.settings.environment
    if not env:
        raise UsageError("no environment (--env or config environment)")
    token = call(ctx.client("mwaa").create_cli_token, Name=env)
    result = _invoke_cli(token["WebServerHostname"], token["CliToken"], ns.command, timeout=ns.timeout)
    stdout = base64.b64decode(result.get("stdout", "") or "").decode("utf-8", "replace")
    stderr = base64.b64decode(result.get("stderr", "") or "").decode("utf-8", "replace")
    if stdout:
        print(stdout.rstrip("\n"))
    if stderr:
        eprint(stderr.rstrip("\n"))
    return 0

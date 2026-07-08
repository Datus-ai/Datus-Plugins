"""`datus statsig describe <group> <subcommand>` — print a write command's body template.

Read-only and free of side effects, so the agent can learn the request-body
shape for a mutating command (which itself needs confirmation) without a prompt.
"""

from __future__ import annotations

import argparse

from ..errors import UsageError
from ..schemas import BODY_TEMPLATES, describe_text
from . import Context


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "describe",
        help="print the request-body template for a write command",
        description="Print the request-body template for a write command, "
        "e.g. `datus statsig describe metric-source create`.",
    )
    p.add_argument("path", nargs="+", metavar="<group> <subcommand>", help="e.g. metrics create")
    p.set_defaults(func=cmd_describe)


def cmd_describe(ctx: Context, ns) -> int:
    key = " ".join(ns.path)
    try:
        print(describe_text(key))
    except KeyError:
        available = "\n  ".join(sorted(BODY_TEMPLATES))
        raise UsageError(
            f"no body template for {key!r}. Commands with a describable body:\n  {available}"
        )
    return 0

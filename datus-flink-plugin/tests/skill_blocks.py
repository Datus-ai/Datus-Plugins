"""Extract the file templates a single-file SKILL.md carries inline.

A Datus plugin skill is exactly one `SKILL.md`: a skill directory cannot ship an
`assets/` or `references/` subdirectory, because Datus discovers only the skill
file itself. Every template this plugin's skills hand to a project therefore
lives in a fenced code block introduced by a `### <filename>` heading, and these
helpers let the tests render and check those blocks the way a file on disk would
be checked.
"""

from __future__ import annotations

import re
from pathlib import Path

BLOCK = re.compile(
    r"^#{2,4}[ \t]+(?P<name>[A-Za-z0-9._-]+\.[A-Za-z0-9]+)[ \t]*$\n+"
    r"```(?P<language>[a-z]*)\n(?P<body>.*?)^```$",
    re.MULTILINE | re.DOTALL,
)


def blocks(skill_md: Path) -> dict[str, str]:
    """Map `### <filename>` heading -> the fenced block body that follows it."""
    found = {
        match.group("name"): match.group("body")
        for match in BLOCK.finditer(skill_md.read_text(encoding="utf-8"))
    }
    assert found, f"no inline file blocks found in {skill_md}"
    return found


def languages(skill_md: Path) -> dict[str, str]:
    """Map `### <filename>` heading -> the fence's language tag."""
    return {
        match.group("name"): match.group("language")
        for match in BLOCK.finditer(skill_md.read_text(encoding="utf-8"))
    }


def render(body: str, values: dict[str, str]) -> str:
    """Replace every `__PLACEHOLDER__` and assert none is left behind."""
    for placeholder, value in values.items():
        body = body.replace(placeholder, value)
    assert re.search(r"__[A-Za-z0-9_]+__", body) is None, body
    return body

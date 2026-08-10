"""Normalize Datus print-mode JSONL into stable process diagnostics."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


COMMAND_KEYS = ("command", "cmd", "shell_command")
TOOL_KEYS = ("tool_name", "tool", "name")


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def load_payloads(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    payloads: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.exists():
        return payloads, ["stdout.jsonl is missing"]
    for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {number}: {exc.msg}")
            continue
        if isinstance(value, dict):
            payloads.append(value)
        else:
            errors.append(f"line {number}: payload is not an object")
    return payloads, errors


def diagnose(payloads: list[dict[str, Any]], usage: dict[str, Any] | None = None) -> dict[str, Any]:
    tools: list[str] = []
    commands: list[str] = []
    failed: list[dict[str, str]] = []
    seen_nodes: set[int] = set()
    streamed_usage: dict[str, int] = {}

    for payload in payloads:
        for node in _walk(payload):
            marker = id(node)
            if marker in seen_nodes:
                continue
            seen_nodes.add(marker)
            node_type = str(node.get("type") or node.get("action_type") or "").lower()
            if node_type == "call-tool" and isinstance(node.get("payload"), dict):
                detail = node["payload"]
                tool = detail.get("toolName") if isinstance(detail.get("toolName"), str) else "unknown"
                params = detail.get("toolParams") if isinstance(detail.get("toolParams"), dict) else {}
                tools.append(tool)
                command = next((params.get(key) for key in COMMAND_KEYS if isinstance(params.get(key), str)), None)
                if command:
                    commands.append(command.strip())
                continue
            if node_type == "call-tool-result" and isinstance(node.get("payload"), dict):
                detail = node["payload"]
                result = detail.get("result")
                result_data = result if isinstance(result, dict) else {}
                seen_nodes.add(id(result_data))
                status = str(result_data.get("status") or "").lower()
                if status in {"failed", "failure", "error"} or result_data.get("success") is False:
                    failed.append({"tool": str(detail.get("toolName") or "unknown"), "status": status or "false"})
                continue
            if node_type == "usage" and isinstance(node.get("payload"), dict):
                for field in ("requests", "input_tokens", "output_tokens", "total_tokens"):
                    try:
                        streamed_usage[field] = max(streamed_usage.get(field, 0), int(node["payload"].get(field) or 0))
                    except (TypeError, ValueError):
                        pass
                continue

            tool = next((node.get(key) for key in TOOL_KEYS if isinstance(node.get(key), str)), None)
            arguments = node.get("arguments") if isinstance(node.get("arguments"), dict) else node.get("input")
            arguments = arguments if isinstance(arguments, dict) else {}
            command = next((node.get(key) for key in COMMAND_KEYS if isinstance(node.get(key), str)), None)
            if command is None:
                command = next((arguments.get(key) for key in COMMAND_KEYS if isinstance(arguments.get(key), str)), None)
            if tool and ("tool" in node_type or command or "arguments" in node or "input" in node):
                tools.append(tool)
            if tool and command:
                commands.append(command.strip())
            status = str(node.get("status") or "").lower()
            success = node.get("success")
            if status in {"failed", "failure", "error"} or success is False:
                failed.append({"tool": tool or "unknown", "status": status or "false"})

    command_counts = Counter(commands)
    duplicates = [{"command": cmd, "count": count} for cmd, count in command_counts.items() if count > 1]
    usage = usage or streamed_usage
    return {
        "tool_sequence": tools,
        "tool_calls": len(tools),
        "tool_counts": dict(Counter(tools)),
        "commands": commands,
        "duplicate_commands": duplicates,
        "unexpected_failures": failed,
        "llm_turns": int(usage.get("requests") or usage.get("llm_turns") or 0),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def check_efficiency(process: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    bounds = {
        "maxToolCalls": ("tool_calls", "tool calls"),
        "maxLlmTurns": ("llm_turns", "LLM turns"),
        "maxTokens": ("total_tokens", "tokens"),
        "maxUnexpectedFailures": ("unexpected_failures", "unexpected failed calls"),
    }
    for option, (field, label) in bounds.items():
        if option not in contract:
            continue
        value = process.get(field, 0)
        count = len(value) if isinstance(value, list) else int(value or 0)
        if count > contract[option]:
            failures.append(f"{label}: {count} exceeds {contract[option]}")
    commands = process.get("commands") or []
    for pattern in contract.get("forbiddenCommands") or []:
        if any(re.search(pattern, command) for command in commands):
            failures.append(f"forbidden command matched: {pattern}")
    for pattern in contract.get("expectedCommands") or []:
        if not any(re.search(pattern, command) for command in commands):
            failures.append(f"expected command not observed: {pattern}")
    return failures

"""Call helpers around boto3: error mapping, pagination, and a --wait poller.

This is the AWS analogue of datus-airflow-plugin's ``client.py`` +
``_wait_for_run``/``verify_dags``: instead of wrapping ``requests``, it wraps
boto3 calls, mapping botocore exceptions to :class:`ApiError` (so the CLI exits
1 with a readable ``Code: Message``) and offering the same monotonic-deadline
polling used by ``--wait``.
"""

from __future__ import annotations

import sys
import time
from typing import Any, Callable, Dict, List, Optional

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
    NoRegionError,
)

from .errors import ApiError, PluginError

_UNSET = object()


def _to_api_error(exc: Exception) -> ApiError:
    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {}) if isinstance(exc.response, dict) else {}
        code = err.get("Code", "ClientError")
        message = err.get("Message", str(exc))
        status = None
        if isinstance(exc.response, dict):
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return ApiError(f"{code}: {message}", status_code=status)
    if isinstance(exc, NoCredentialsError):
        return ApiError(
            "no AWS credentials found — set them in the profile or the standard AWS "
            "chain (env vars, ~/.aws, instance profile / IRSA)"
        )
    if isinstance(exc, NoRegionError):
        return ApiError("no AWS region configured — set `region` in the profile")
    if isinstance(exc, EndpointConnectionError):
        return ApiError(f"cannot reach the AWS endpoint: {exc}")
    return ApiError(f"AWS SDK error: {exc}")


def call(fn: Callable[..., Any], **kwargs) -> Any:
    """Invoke a boto3 client method, mapping botocore errors to ApiError."""
    try:
        return fn(**kwargs)
    except (ClientError, BotoCoreError) as exc:
        raise _to_api_error(exc) from exc


def paginate(
    client: Any,
    operation: str,
    key: str,
    limit: Optional[int] = None,
    **kwargs,
) -> List[Any]:
    """Collect every page of a list ``operation`` into a flat list under ``key``.

    Uses boto3's paginator when the operation supports it, otherwise a single
    call. ``limit`` stops early (approximately, at page granularity).
    """
    items: List[Any] = []
    try:
        if client.can_paginate(operation):
            paginator = client.get_paginator(operation)
            for page in paginator.paginate(**kwargs):
                items.extend(page.get(key, []) or [])
                if limit is not None and len(items) >= limit:
                    return items[:limit]
            return items
    except (ClientError, BotoCoreError) as exc:
        raise _to_api_error(exc) from exc

    resp = call(getattr(client, operation), **kwargs)
    items.extend(resp.get(key, []) or [])
    return items[:limit] if limit is not None else items


def wait_until(
    poll: Callable[[], Any],
    is_terminal: Callable[[Any], bool],
    *,
    timeout: float,
    interval: float,
    on_change: Optional[Callable[[Any], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Poll ``poll()`` until ``is_terminal(state)``; return the terminal state.

    Emits state changes through ``on_change`` (progress goes to stderr so that
    ``-o json`` stdout stays machine-parseable). Raises :class:`PluginError` on
    timeout, measured against a ``time.monotonic()`` deadline (clock-skew safe).
    ``sleep`` is injectable so tests can run instantly.
    """
    deadline = time.monotonic() + timeout
    last = _UNSET
    while True:
        state = poll()
        if state != last:
            if on_change is not None:
                on_change(state)
            last = state
        if is_terminal(state):
            return state
        if time.monotonic() >= deadline:
            raise PluginError(f"timed out after {timeout:.0f}s waiting (last state: {state!r})")
        sleep(interval)


def eprint(message: str) -> None:
    """Print a progress line to stderr (keeps stdout clean for -o json)."""
    print(message, file=sys.stderr)

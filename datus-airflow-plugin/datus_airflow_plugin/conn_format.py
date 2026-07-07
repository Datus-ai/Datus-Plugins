"""Airflow connection URI <-> field-dict conversion.

Follows Airflow's own convention: the URI scheme is the conn_type with ``_``
replaced by ``-``; login/password/host/schema are percent-encoded; query
params become the JSON ``extra`` (a single ``__extra__`` param carries
non-flat JSON verbatim).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from .errors import UsageError

CONN_FIELDS = ("conn_type", "description", "host", "login", "password", "schema", "port", "extra")


def parse_conn_uri(uri: str) -> Dict[str, Any]:
    parts = urlsplit(uri)
    if not parts.scheme:
        raise UsageError(f"invalid connection URI (no scheme): {uri!r}")
    fields: Dict[str, Any] = {"conn_type": parts.scheme.replace("-", "_")}

    if parts.hostname is not None:
        fields["host"] = unquote(parts.hostname)
    if parts.username is not None:
        fields["login"] = unquote(parts.username)
    if parts.password is not None:
        fields["password"] = unquote(parts.password)
    if parts.port is not None:
        fields["port"] = parts.port
    if parts.path and parts.path != "/":
        fields["schema"] = unquote(parts.path.lstrip("/"))

    if parts.query:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        keys = [k for k, _ in pairs]
        if keys == ["__extra__"]:
            fields["extra"] = pairs[0][1]
        else:
            fields["extra"] = json.dumps(dict(pairs), ensure_ascii=False)
    return fields


def build_conn_uri(fields: Dict[str, Any]) -> str:
    conn_type = fields.get("conn_type")
    if not conn_type:
        raise UsageError("cannot build a connection URI without conn_type")
    scheme = str(conn_type).replace("_", "-")

    auth = ""
    login = fields.get("login")
    password = fields.get("password")
    if login is not None or password is not None:
        auth = quote(str(login or ""), safe="")
        if password is not None:
            auth += ":" + quote(str(password), safe="")
        auth += "@"

    host = quote(str(fields.get("host") or ""), safe="")
    port = fields.get("port")
    netloc = f"{auth}{host}" + (f":{port}" if port not in (None, "") else "")

    path = ""
    schema = fields.get("schema")
    if schema:
        path = "/" + quote(str(schema), safe="")

    query = ""
    extra = fields.get("extra")
    if extra:
        query = "?" + _extra_to_query(extra)

    return f"{scheme}://{netloc}{path}{query}"


def _extra_to_query(extra: Any) -> str:
    if isinstance(extra, (dict, list)):
        extra = json.dumps(extra, ensure_ascii=False)
    try:
        parsed = json.loads(extra)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict) and all(isinstance(v, str) for v in parsed.values()):
        return urlencode(parsed)
    return urlencode({"__extra__": extra})


def normalize_conn_fields(data: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Map an airflow-style export dict onto API body fields, dropping nulls."""
    aliases = {"conn_id": None, "connection_id": None, "type": "conn_type"}
    out: Dict[str, Any] = {}
    for key, value in data.items():
        key = aliases.get(key, key) if key in aliases else key
        if key is None:
            continue
        if key not in CONN_FIELDS:
            raise UsageError(f"unknown connection field {key!r} in {source}")
        if value is None:
            continue
        if key == "extra" and isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if key == "port" and value != "":
            value = int(value)
        out[key] = value
    return out

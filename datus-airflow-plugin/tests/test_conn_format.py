"""Connection URI parsing/building round trips."""

from __future__ import annotations

import json

import pytest

from datus_airflow_plugin.conn_format import build_conn_uri, normalize_conn_fields, parse_conn_uri
from datus_airflow_plugin.errors import UsageError


def test_parse_full_uri():
    fields = parse_conn_uri("postgres://user:p%40ss@db.example.com:5432/warehouse?sslmode=require")
    assert fields == {
        "conn_type": "postgres",
        "host": "db.example.com",
        "login": "user",
        "password": "p@ss",
        "port": 5432,
        "schema": "warehouse",
        "extra": json.dumps({"sslmode": "require"}, ensure_ascii=False),
    }


def test_parse_conn_type_dash_to_underscore():
    assert parse_conn_uri("google-cloud-platform://")["conn_type"] == "google_cloud_platform"


def test_parse_extra_passthrough():
    payload = json.dumps({"nested": {"a": 1}})
    from urllib.parse import quote

    fields = parse_conn_uri(f"http://host?__extra__={quote(payload)}")
    assert json.loads(fields["extra"]) == {"nested": {"a": 1}}


def test_parse_rejects_schemeless():
    with pytest.raises(UsageError):
        parse_conn_uri("not a uri")


def test_build_round_trip():
    original = {
        "conn_type": "mysql",
        "host": "db",
        "login": "u",
        "password": "p:w@d",
        "port": 3306,
        "schema": "s",
        "extra": json.dumps({"charset": "utf8"}),
    }
    rebuilt = parse_conn_uri(build_conn_uri(original))
    assert rebuilt == original


def test_build_uri_with_nested_extra_uses_extra_param():
    uri = build_conn_uri({"conn_type": "aws", "extra": json.dumps({"config": {"retries": 3}})})
    assert "__extra__=" in uri
    assert parse_conn_uri(uri)["extra"] == json.dumps({"config": {"retries": 3}})


def test_normalize_conn_fields_maps_aliases_and_types():
    fields = normalize_conn_fields(
        {"conn_type": "http", "port": "80", "extra": {"a": "b"}, "conn_id": "ignored"},
        "test",
    )
    assert fields == {"conn_type": "http", "port": 80, "extra": '{"a": "b"}'}
    with pytest.raises(UsageError):
        normalize_conn_fields({"bogus": 1}, "test")

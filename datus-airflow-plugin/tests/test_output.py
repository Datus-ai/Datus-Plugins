"""Rendering: table alignment, plain, json (full objects), single-entity view."""

from __future__ import annotations

import json

from datus_airflow_plugin.output import render_one, render_rows

ROWS = [
    {"dag_id": "etl_sales", "is_paused": False, "owners": ["airflow"], "extra_field": 1},
    {"dag_id": "x", "is_paused": True, "owners": [], "extra_field": None},
]


def test_table_selects_columns_and_aligns():
    text = render_rows(ROWS, ["dag_id", "is_paused", "owners"], "table")
    lines = text.splitlines()
    assert lines[0].split(" | ") == ["dag_id   ", "is_paused", "owners     "]
    assert set(lines[1]) <= {"=", "+"}
    assert lines[2].startswith("etl_sales | False")
    assert '["airflow"]' in lines[2]


def test_plain_is_space_separated():
    text = render_rows(ROWS, ["dag_id", "is_paused"], "plain")
    assert text.splitlines()[1] == "etl_sales False"


def test_json_emits_full_objects_ignoring_column_selection():
    text = render_rows(ROWS, ["dag_id"], "json")
    data = json.loads(text)
    assert data[0]["extra_field"] == 1


def test_yaml_round_trips():
    import yaml

    data = yaml.safe_load(render_rows(ROWS, None, "yaml"))
    assert data[1]["is_paused"] is True


def test_empty_rows_render_header_only():
    text = render_rows([], ["a", "bb"], "table")
    assert text.splitlines()[0] == "a | bb"


def test_none_columns_uses_union_of_keys():
    text = render_rows([{"a": 1}, {"b": 2}], None, "table")
    assert text.splitlines()[0].split(" | ") == ["a", "b"]


def test_render_one_table_is_property_list():
    text = render_one({"dag_id": "x", "is_paused": False}, "table")
    assert "property" in text.splitlines()[0]
    assert any(line.startswith("dag_id") for line in text.splitlines())


def test_render_one_json():
    assert json.loads(render_one({"a": [1, 2]}, "json")) == {"a": [1, 2]}

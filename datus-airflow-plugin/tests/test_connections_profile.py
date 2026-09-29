"""The host-managed `connections` profile field (datasource name -> conn_id)."""

from __future__ import annotations

import pytest

from conftest import BASE_URL
from datus_airflow_plugin.config import Settings
from datus_airflow_plugin.errors import ConfigError


def test_connections_default_to_empty():
    assert Settings.from_profile({"api_base_url": BASE_URL}).connections == {}
    assert Settings.from_profile({"api_base_url": BASE_URL, "connections": None}).connections == {}


def test_connections_are_parsed_as_a_string_mapping():
    settings = Settings.from_profile(
        {
            "api_base_url": BASE_URL,
            "connections": {"aviation": "datus__abc__def", " sales ": " datus__abc__xyz "},
        }
    )
    assert settings.connections == {"aviation": "datus__abc__def", "sales": "datus__abc__xyz"}


@pytest.mark.parametrize(
    "raw",
    [
        "aviation=datus__abc",
        ["datus__abc"],
        {"aviation": ""},
        {"aviation": 42},
        {"aviation": None},
        {"": "datus__abc"},
    ],
)
def test_malformed_connections_are_config_errors(raw):
    with pytest.raises(ConfigError) as exc:
        Settings.from_profile({"api_base_url": BASE_URL, "connections": raw})
    assert "connections" in str(exc.value)

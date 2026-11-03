from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests

from datus_tableau_plugin.client import TableauClient
from datus_tableau_plugin.config import Settings
from datus_tableau_plugin.errors import ConfigError, MetadataError


PROFILE = {
    "server_url": "https://tableau.example.test",
    "token_name": "automation",
    "token_secret": "secret",
    "api_version": "3.21",
}


def test_real_sdk_http_contract(monkeypatch):
    """Exercise real TSC serializers, pager, download options and auth headers without a network."""
    calls = []
    pages = 0
    ns = 'xmlns="http://tableau.com/api"'

    def request(session, method, url, **kwargs):
        nonlocal pages
        calls.append((method, url, kwargs))
        response = requests.Response()
        response.url = url
        response.status_code = 200
        response.headers["Content-Type"] = "application/xml"
        if url.endswith("/auth/signin"):
            response._content = f'<tsResponse {ns}><credentials token="session-token"><site id="site-luid" contentUrl=""/><user id="user-luid"/></credentials></tsResponse>'.encode()
        elif url.endswith("/auth/signout"):
            response.status_code = 204
            response._content = b""
        elif urlsplit(url).path.endswith("/workbooks"):
            pages += 1
            response._content = f'<tsResponse {ns}><pagination pageNumber="{pages}" pageSize="1" totalAvailable="2"/><workbooks><workbook id="book-{pages}" name="Dashboard {pages}" contentUrl="Book{pages}"><project id="project" name="Sales"/></workbook></workbooks></tsResponse>'.encode()
        elif "/metadata/graphql" in url:
            response.headers["Content-Type"] = "application/json"
            response._content = json.dumps({"data": {"__typename": "Query"}}).encode()
        elif "/workbooks/book-1/content" in url:
            response.headers["Content-Disposition"] = 'attachment; filename="demo.twb"'
            response._content = b'<workbook name="demo"/>'
        else:
            raise AssertionError(f"unexpected request {method} {url}")
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    with TableauClient(Settings.from_profile(PROFILE)) as client:
        records = client.list_resources("workbooks")
        assert [row["id"] for row in records] == ["book-1", "book-2"]
        assert client.metadata(
            "query Check($id: ID!) { __typename }", {"id": "opaque"}
        ) == {"__typename": "Query"}
        assert client.download("workbooks", "book-1") == b'<workbook name="demo"/>'
    sign_in = calls[0]
    assert b'personalAccessTokenName="automation"' in sign_in[2]["data"]
    assert b'contentUrl=""' in sign_in[2]["data"]
    assert calls[-1][1].endswith("/auth/signout")
    for _, _, options in calls[1:]:
        assert options["headers"]["x-tableau-auth"] == "session-token"
        assert options["timeout"] == 60
        assert options["verify"] is True
    metadata_call = next(call for call in calls if "/metadata/graphql" in call[1])
    assert json.loads(metadata_call[2]["data"])["variables"] == {"id": "opaque"}
    download = next(call for call in calls if "/content" in call[1])
    assert (
        "includeExtract=False" in download[1]
        or (download[2].get("params") or {}).get("includeExtract") is False
    )
    assert not list(Path.cwd().glob("demo.twb"))


def test_http_options_before_version_negotiation_and_password_signout():
    events = []

    class Server:
        def __init__(self, url):
            self.auth = SimpleNamespace(
                sign_in=lambda auth: events.append(("signin", auth)),
                sign_out=lambda: events.append(("signout",)),
            )

        def add_http_options(self, options):
            events.append(("options", options))

        def use_server_version(self):
            events.append(("version",))

    sdk = SimpleNamespace(Server=Server, TableauAuth=lambda *args, **kw: (args, kw))
    profile = {
        "server_url": "https://tableau.example.test",
        "auth_type": "password",
        "username": "analyst",
        "password": "private",
    }
    with (
        pytest.raises(RuntimeError),
        TableauClient(Settings.from_profile(profile), sdk=sdk),
    ):
        raise RuntimeError("downstream processing failed")
    assert events[0][0] == "options" and events[1][0] == "version"
    assert events[2] == ("signin", (("analyst", "private"), {"site_id": ""}))
    assert events[3] == ("signout",)


@pytest.mark.parametrize(
    "update",
    [
        {"server_url": "https://user:password@tableau.example.test"},
        {"server_url": "file:///tmp"},
        {"token_secret": "${UNRESOLVED}"},
        {"timeout": 0},
        {"timeout": "nan"},
        {"verify_ssl": "maybe"},
        {"auth_type": "sso"},
        {"api_version": "latest"},
    ],
)
def test_invalid_config(update):
    with pytest.raises(ConfigError):
        Settings.from_profile({**PROFILE, **update})


def test_graphql_http_success_with_errors_is_failure_without_leaking_secrets():
    client = object.__new__(TableauClient)
    client.server = SimpleNamespace(
        metadata=SimpleNamespace(
            query=lambda *args, **kw: {
                "data": {},
                "errors": [{"message": "password=never-print"}],
            }
        )
    )
    with pytest.raises(MetadataError) as error:
        client.metadata("query { workbooks { id } }")
    assert "never-print" not in str(error.value)


def test_nested_lineage_connections_each_page_independently():
    client = object.__new__(TableauClient)
    calls = []

    def metadata(query, variables):
        calls.append((query, variables.copy()))
        connection = next(
            name
            for name in (
                "upstreamDatasourcesConnection",
                "upstreamFlowsConnection",
                "upstreamTablesConnection",
            )
            if name in query
        )
        n = 2 if variables["after"] else 1
        return {
            "workbooks": [
                {
                    "id": "metadata-id",
                    "luid": "rest-luid",
                    connection: {
                        "nodes": [{"id": f"{connection}:{n}"}],
                        "pageInfo": {"hasNextPage": n == 1, "endCursor": "page2"},
                    },
                }
            ]
        }

    client.metadata = metadata
    lineage = client.workbook_lineage("rest-luid")
    assert len(calls) == 6
    assert [len(lineage[group]) for group in ("datasources", "flows", "tables")] == [
        2,
        2,
        2,
    ]
    assert [variables["after"] for _, variables in calls] == [None, "page2"] * 3
    assert lineage["workbook"]["id"] == "metadata-id"


def test_custom_sql_queries_only_the_requested_workbook():
    client = object.__new__(TableauClient)
    detail_ids = []

    def metadata(query, variables):
        if "DatusCustomSQLIndex" in query:
            return {
                "customSQLTablesConnection": {
                    "nodes": [
                        {"id": "ours", "downstreamWorkbooks": [{"luid": "book"}]},
                        {"id": "other", "downstreamWorkbooks": []},
                    ],
                    "pageInfo": {"hasNextPage": False},
                }
            }
        detail_ids.append(variables["id"])
        return {
            "customSQLTables": [
                {
                    "id": "ours",
                    "name": "Revenue SQL",
                    "query": "SELECT 42",
                    "isUnsupportedCustomSql": False,
                }
            ]
        }

    client.metadata = metadata
    assert client.workbook_custom_sql("book")[0]["query"] == "SELECT 42"
    assert detail_ids == ["ours"]

"""Read-only Tableau Server Client facade with explicit pagination and session ownership."""

from __future__ import annotations

import importlib
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urljoin, urlsplit

from .config import Settings
from .errors import MetadataError, MissingDependency, PluginError, UsageError

# Each nested relationship has its own cursor: a top-level pager cannot page nested lists.
RELATION_FIELDS = {
    "datasources": ("upstreamDatasourcesConnection", "id luid name"),
    "flows": ("upstreamFlowsConnection", "id luid name"),
    "tables": ("upstreamTablesConnection", "id name fullName connectionType"),
}


class TableauClient:
    def __init__(self, settings: Settings, *, sdk: Any = None):
        self.settings = settings
        if sdk is None:
            try:
                sdk = importlib.import_module("tableauserverclient")
            except ImportError:
                raise MissingDependency(
                    "install tableauserverclient to connect to Tableau"
                ) from None
        self.sdk = sdk
        self.server = sdk.Server(settings.server_url)
        self.server.add_http_options(
            {
                "verify": settings.ca_bundle or settings.verify_ssl,
                "timeout": settings.timeout,
            }
        )
        # Apply TLS and timeout options before this first network request.
        if settings.api_version == "auto":
            self.server.use_server_version()
        else:
            self.server.version = settings.api_version
        self._signed_in = False

    def __enter__(self):
        cfg = self.settings
        # An unconfigured site signs in to the default site: that is the only
        # entry point from which ``sites list`` can enumerate the server.
        site = cfg.site_content_url or ""
        auth = (
            self.sdk.PersonalAccessTokenAuth(
                cfg.token_name, cfg.token_secret, site_id=site
            )
            if cfg.auth_type == "pat"
            else self.sdk.TableauAuth(cfg.username, cfg.password, site_id=site)
        )
        self.server.auth.sign_in(auth)
        self._signed_in = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self):
        if self._signed_in:
            self._signed_in = False
            self.server.auth.sign_out()

    @staticmethod
    def _record(item: Any) -> dict:
        fields = (
            "id",
            "name",
            "content_url",
            "project_id",
            "project_name",
            "owner_id",
            "created_at",
            "updated_at",
            "webpage_url",
            "description",
        )
        return {
            key: str(value) if key in {"created_at", "updated_at"} else value
            for key in fields
            if (value := getattr(item, key, None)) is not None
        }

    def list_resources(
        self, group: str, *, project_id: str | None = None
    ) -> list[dict]:
        endpoint = getattr(self.server, group)
        rows = [self._record(item) for item in self.sdk.Pager(endpoint)]
        return [
            row
            for row in rows
            if project_id is None or row.get("project_id") == project_id
        ]

    def list_sites(self) -> list[dict]:
        """Enumerate sites so an operator can choose ``site_content_url``.

        Tableau only lets Server Administrators list sites; everyone else can
        see nothing but their own. Report that limit as guidance rather than a
        masked transport failure.
        """
        try:
            items = list(self.sdk.Pager(self.server.sites))
        except self.sdk.ServerResponseError as exc:
            code = str(getattr(exc, "code", ""))
            if code.startswith(("401", "403")):
                raise PluginError(
                    "listing sites requires a Server Administrator account; "
                    "ask your Tableau admin for the site content URL and pass "
                    "--site CONTENT_URL or save site_content_url in the profile"
                ) from None
            raise
        return [
            {
                "id": item.id,
                "name": item.name,
                "content_url": item.content_url or "",
                "state": getattr(item, "state", None),
                "default": not item.content_url,
            }
            for item in items
        ]

    def get_resource(self, group: str, identifier: str) -> dict:
        item = getattr(self.server, group).get_by_id(identifier)
        record = self._record(item)
        if group in {"workbooks", "datasources", "flows"}:
            endpoint = getattr(self.server, group)
            endpoint.populate_connections(item)
            record["connections"] = [
                {
                    key: getattr(connection, key, None)
                    for key in (
                        "id",
                        "connection_type",
                        "server_address",
                        "server_port",
                        "dbname",
                        "datasource_id",
                    )
                    if getattr(connection, key, None) is not None
                }
                for connection in item.connections
            ]
        return record

    def metadata(self, query: str, variables: dict | None = None) -> dict:
        response = self.server.metadata.query(
            query, variables=variables, abort_on_error=False
        )
        if not isinstance(response, dict) or response.get("errors"):
            # Never echo API error bodies: GraphQL errors may include source text or connection credentials.
            raise MetadataError(
                "Metadata API returned errors; check enablement, permissions and Server/schema compatibility"
            )
        data = response.get("data")
        if not isinstance(data, dict):
            raise MetadataError("Metadata API returned no data object")
        return data

    def rest_get(self, path: str, params: dict[str, str] | None = None) -> Any:
        """Read-only same-origin GET against the signed-in Tableau REST API.

        Only GET is exposed: this escape hatch reads untyped REST resources the
        typed commands do not cover, and never mutates server state. The path is
        resolved against the versioned REST base and must not escape that origin.
        """
        if not self._signed_in:
            raise PluginError("a Tableau session is required for api call")
        parts = urlsplit(path)
        if parts.scheme or parts.netloc:
            raise UsageError(
                "path must be relative to the Tableau REST API, without scheme or host"
            )
        base = self.server.baseurl.rstrip("/") + "/"
        url = urljoin(base, path.lstrip("/"))
        if not url.startswith(base):
            raise UsageError("path escapes the Tableau REST API base")
        response = self.server._session.get(
            url,
            headers={
                "X-Tableau-Auth": self.server.auth_token,
                "Accept": "application/json",
            },
            params=params or None,
            verify=self.settings.ca_bundle or self.settings.verify_ssl,
            timeout=self.settings.timeout,
        )
        response.raise_for_status()
        content = response.content
        if len(content) > 16 * 1024 * 1024:
            raise PluginError("REST response exceeds 16 MiB read limit")
        try:
            return response.json()
        except ValueError:
            return {
                "status_code": response.status_code,
                "content_type": response.headers.get("Content-Type"),
                "text": content.decode("utf-8", "replace")[:65536],
            }

    def status(self) -> dict:
        self.metadata("query DatusMetadataStatus { __typename }")
        return {
            "server_url": self.settings.server_url,
            "site_content_url": self.settings.site_content_url,
            "site_configured": self.settings.site_content_url is not None,
            "api_version": str(self.server.version),
            "site_luid": self.server.site_id,
            "metadata": "available",
        }

    def workbook_lineage(self, identifier: str) -> dict:
        result: dict[str, list] = {}
        for name, (connection, fields) in RELATION_FIELDS.items():
            query = f"""query DatusWorkbookLineage($luid: String!, $first: Int!, $after: String) {{
              workbooks(filter: {{luid: $luid}}) {{ id luid name
                {connection}(first: $first, after: $after) {{ nodes {{ {fields} }} pageInfo {{ hasNextPage endCursor }} }}
              }} }}"""
            after = None
            seen: set[str] = set()
            rows: list[dict] = []
            while True:
                data = self.metadata(
                    query, {"luid": identifier, "first": 100, "after": after}
                )
                books = data.get("workbooks")
                if (
                    not isinstance(books, list)
                    or len(books) != 1
                    or books[0].get("luid") != identifier
                ):
                    raise MetadataError(
                        "workbook is missing, ambiguous or hidden in Metadata API"
                    )
                book = books[0]
                page = book.get(connection)
                if not isinstance(page, dict) or not isinstance(
                    page.get("nodes"), list
                ):
                    raise MetadataError(f"Metadata API hid or omitted {name} lineage")
                rows.extend(page["nodes"])
                info = page.get("pageInfo") or {}
                if not info.get("hasNextPage"):
                    break
                after = info.get("endCursor")
                if not isinstance(after, str) or after in seen:
                    raise MetadataError("Metadata API pagination did not advance")
                seen.add(after)
            result[name] = rows
            result["workbook"] = {key: book.get(key) for key in ("id", "luid", "name")}
        return result

    def datasource_lineage(self, identifier: str) -> dict:
        """Read independently paged lineage for one exact published datasource.

        The upstream relationships are shortcuts, not a claim of direct production.
        https://help.tableau.com/current/api/metadata_api/en-us/reference/publisheddatasource.doc.html
        """
        result = {}
        identity = None
        for name in ("flows", "tables"):
            connection, fields = RELATION_FIELDS[name]
            query = f"""query DatusDatasourceLineage($luid: String!, $first: Int!, $after: String) {{
              publishedDatasources(filter: {{luid: $luid}}) {{ id luid name
                {connection}(first: $first, after: $after) {{ nodes {{ {fields} }} pageInfo {{ hasNextPage endCursor }} }}
              }} }}"""
            rows, seen, after = [], set(), None
            while True:
                data = self.metadata(
                    query, {"luid": identifier, "first": 100, "after": after}
                )
                sources = data.get("publishedDatasources")
                if (
                    not isinstance(sources, list)
                    or len(sources) != 1
                    or not isinstance(sources[0], dict)
                    or sources[0].get("luid") != identifier
                    or not sources[0].get("id")
                ):
                    raise MetadataError(
                        "published datasource is missing, ambiguous or hidden in Metadata API"
                    )
                source = sources[0]
                current = {key: source.get(key) for key in ("id", "luid", "name")}
                if identity is not None and current["id"] != identity["id"]:
                    raise MetadataError(
                        "published datasource Metadata identity changed during pagination"
                    )
                identity = current
                page = source.get(connection)
                if not isinstance(page, dict) or not isinstance(
                    page.get("nodes"), list
                ):
                    raise MetadataError(
                        f"Metadata API hid or omitted datasource {name} lineage"
                    )
                if any(
                    not isinstance(row, dict) or not row.get("id")
                    for row in page["nodes"]
                ):
                    raise MetadataError("datasource lineage contains a hidden identity")
                rows.extend(page["nodes"])
                info = page.get("pageInfo")
                if len(rows) > 100000:
                    raise MetadataError("datasource lineage exceeds row resource limit")
                if not isinstance(info, dict) or not isinstance(
                    info.get("hasNextPage"), bool
                ):
                    raise MetadataError(
                        "datasource lineage pagination completeness is unavailable"
                    )
                if not info["hasNextPage"]:
                    break
                after = info.get("endCursor")
                if not isinstance(after, str) or not after or after in seen:
                    raise MetadataError("datasource lineage pagination did not advance")
                seen.add(after)
                if len(seen) >= 1000:
                    raise MetadataError(
                        "datasource lineage exceeds pagination resource limit"
                    )
            result[name] = rows
        result["datasource"] = identity
        return result

    def workbook_custom_sql(self, identifier: str) -> list[dict]:
        """Scope SQL by proven downstream workbook identity before retrieving query text."""
        query = """query DatusCustomSQLIndex($luid: String!, $first: Int!, $after: String) {
          customSQLTablesConnection(first: $first, after: $after) {
            nodes { id downstreamWorkbooks(filter: {luid: $luid}) { luid } }
            pageInfo { hasNextPage endCursor }
          }
        }"""
        after = None
        seen: set[str] = set()
        identifiers: set[str] = set()
        while True:
            page = self.metadata(
                query, {"luid": identifier, "first": 100, "after": after}
            ).get("customSQLTablesConnection")
            if not isinstance(page, dict) or not isinstance(page.get("nodes"), list):
                raise MetadataError("custom SQL catalog is hidden or unavailable")
            for row in page["nodes"]:
                if any(
                    book.get("luid") == identifier
                    for book in row.get("downstreamWorkbooks") or []
                ):
                    identifiers.add(row["id"])
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
            if not isinstance(after, str) or after in seen:
                raise MetadataError("custom SQL pagination did not advance")
            seen.add(after)
        result = []
        for table_id in sorted(identifiers):
            response = self.metadata(
                """query DatusCustomSQL($id: ID!) {
              customSQLTables(filter: {id: $id}) { id name query isUnsupportedCustomSql }
            }""",
                {"id": table_id},
            )
            rows = response.get("customSQLTables")
            if (
                not isinstance(rows, list)
                or len(rows) != 1
                or rows[0].get("id") != table_id
            ):
                raise MetadataError(
                    "selected custom SQL identity is missing or ambiguous"
                )
            result.append(rows[0])
        return result

    def download(self, group: str, identifier: str) -> bytes:
        endpoint = getattr(self.server, group)
        with TemporaryDirectory(prefix="datus-tableau-") as directory:
            options: dict[str, Any] = {"filepath": str(Path(directory) / "definition")}
            if group == "workbooks":
                options["include_extract"] = False
            elif group == "datasources":
                options["include_extract"] = False
            filename = Path(endpoint.download(identifier, **options)).resolve()
            if (
                not filename.is_relative_to(Path(directory).resolve())
                or not filename.is_file()
            ):
                raise PluginError("SDK download escaped its temporary directory")
            if filename.stat().st_size > 64 * 1024 * 1024:
                raise PluginError("native definition exceeds 64 MiB download limit")
            return filename.read_bytes()

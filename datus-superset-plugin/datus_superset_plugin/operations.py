"""Declared Superset data-plane REST operations used to build the CLI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    args: tuple[str, ...] = ()
    body: bool = False
    binary: bool = False
    upload: bool = False


def op(
    method: str, path: str, *args: str, body: bool = False,
    binary: bool = False, upload: bool = False,
) -> Operation:
    return Operation(method, path, args, body, binary, upload)


# The names intentionally follow the official REST resource vocabulary. Complex
# cross-resource behaviours (panel layout, compiled query export, safe raw API)
# live in cli.py rather than this table.
OPERATIONS: dict[str, dict[str, Operation]] = {
    "status": {
        # /api/v1/me/ and /api/v1/me/roles/ are deliberately absent: they carry no
        # @protect(), so they read identity from the Flask-Login session instead of the
        # bearer token and always answer 401 to an API client like this one.
        "health": op("GET", "/health"),
        "openapi": op("GET", "/api/v1/_openapi"),
    },
    "dashboards": {
        "list": op("GET", "/api/v1/dashboard/"),
        "get": op("GET", "/api/v1/dashboard/{id}", "id"),
        "charts": op("GET", "/api/v1/dashboard/{id}/charts", "id"),
        "datasets": op("GET", "/api/v1/dashboard/{id}/datasets", "id"),
        "tabs": op("GET", "/api/v1/dashboard/{id}/tabs", "id"),
        "create": op("POST", "/api/v1/dashboard/", body=True),
        "copy": op("POST", "/api/v1/dashboard/{id}/copy/", "id", body=True),
        "update": op("PUT", "/api/v1/dashboard/{id}", "id", body=True),
        "delete": op("DELETE", "/api/v1/dashboard/{id}", "id"),
        "bulk-delete": op("DELETE", "/api/v1/dashboard/", body=True),
        "export": op("GET", "/api/v1/dashboard/export/", binary=True),
        "import": op("POST", "/api/v1/dashboard/import/", upload=True),
        "favorite-status": op("GET", "/api/v1/dashboard/favorite_status/"),
        "favorite-add": op("POST", "/api/v1/dashboard/{id}/favorites/", "id"),
        "favorite-remove": op("DELETE", "/api/v1/dashboard/{id}/favorites/", "id"),
        "embedded-get": op("GET", "/api/v1/dashboard/{id}/embedded", "id"),
        "embedded-set": op("POST", "/api/v1/dashboard/{id}/embedded", "id", body=True),
        "embedded-update": op("PUT", "/api/v1/dashboard/{id}/embedded", "id", body=True),
        "embedded-delete": op("DELETE", "/api/v1/dashboard/{id}/embedded", "id"),
        "thumbnail": op("GET", "/api/v1/dashboard/{id}/thumbnail/{digest}/", "id", "digest", binary=True),
        "screenshot": op("GET", "/api/v1/dashboard/{id}/screenshot/{digest}/", "id", "digest", binary=True),
        "cache-screenshot": op("POST", "/api/v1/dashboard/{id}/cache_dashboard_screenshot/", "id"),
        "permalink-create": op("POST", "/api/v1/dashboard/{id}/permalink", "id", body=True),
        "permalink-get": op("GET", "/api/v1/dashboard/permalink/{key}", "key"),
        "filter-state-create": op("POST", "/api/v1/dashboard/{id}/filter_state", "id", body=True),
        "filter-state-get": op("GET", "/api/v1/dashboard/{id}/filter_state/{key}", "id", "key"),
        "filter-state-delete": op("DELETE", "/api/v1/dashboard/{id}/filter_state/{key}", "id", "key"),
    },
    "charts": {
        "list": op("GET", "/api/v1/chart/"),
        "get": op("GET", "/api/v1/chart/{id}", "id"),
        "create": op("POST", "/api/v1/chart/", body=True),
        "update": op("PUT", "/api/v1/chart/{id}", "id", body=True),
        "delete": op("DELETE", "/api/v1/chart/{id}", "id"),
        "bulk-delete": op("DELETE", "/api/v1/chart/", body=True),
        "data": op("GET", "/api/v1/chart/{id}/data/", "id"),
        "query": op("POST", "/api/v1/chart/data", body=True),
        "query-cache": op("GET", "/api/v1/chart/data/{key}", "key"),
        "export": op("GET", "/api/v1/chart/export/", binary=True),
        "import": op("POST", "/api/v1/chart/import/", upload=True),
        "warm-cache": op("PUT", "/api/v1/chart/warm_up_cache", body=True),
        "favorite-status": op("GET", "/api/v1/chart/favorite_status/"),
        "favorite-add": op("POST", "/api/v1/chart/{id}/favorites/", "id"),
        "favorite-remove": op("DELETE", "/api/v1/chart/{id}/favorites/", "id"),
        "thumbnail": op("GET", "/api/v1/chart/{id}/thumbnail/{digest}/", "id", "digest", binary=True),
        "screenshot": op("GET", "/api/v1/chart/{id}/screenshot/{digest}/", "id", "digest", binary=True),
        "cache-screenshot": op("GET", "/api/v1/chart/{id}/cache_screenshot/", "id"),
        "add-to-dashboard": op("PUT", "/api/v1/chart/{id}", "id", body=True),
    },
    "datasets": {
        "list": op("GET", "/api/v1/dataset/"),
        "get": op("GET", "/api/v1/dataset/{id}", "id"),
        "related": op("GET", "/api/v1/dataset/{id}/related_objects", "id"),
        "distinct": op("GET", "/api/v1/dataset/distinct/{column}", "column"),
        "create": op("POST", "/api/v1/dataset/", body=True),
        "get-or-create": op("POST", "/api/v1/dataset/get_or_create/", body=True),
        "duplicate": op("POST", "/api/v1/dataset/duplicate", body=True),
        "update": op("PUT", "/api/v1/dataset/{id}", "id", body=True),
        "delete": op("DELETE", "/api/v1/dataset/{id}", "id"),
        "bulk-delete": op("DELETE", "/api/v1/dataset/", body=True),
        "refresh": op("PUT", "/api/v1/dataset/{id}/refresh", "id"),
        "column-delete": op("DELETE", "/api/v1/dataset/{id}/column/{column_id}", "id", "column_id"),
        "metric-delete": op("DELETE", "/api/v1/dataset/{id}/metric/{metric_id}", "id", "metric_id"),
        "warm-cache": op("PUT", "/api/v1/dataset/warm_up_cache", body=True),
        "export": op("GET", "/api/v1/dataset/export/", binary=True),
        "import": op("POST", "/api/v1/dataset/import/", upload=True),
    },
    "databases": {
        "list": op("GET", "/api/v1/database/"),
        "get": op("GET", "/api/v1/database/{id}", "id"),
        "available": op("GET", "/api/v1/database/available/"),
        "connection": op("GET", "/api/v1/database/{id}/connection", "id"),
        "catalogs": op("GET", "/api/v1/database/{id}/catalogs/", "id"),
        "schemas": op("GET", "/api/v1/database/{id}/schemas/", "id"),
        "tables": op("GET", "/api/v1/database/{id}/tables/", "id"),
        "table": op("GET", "/api/v1/database/{id}/table/{table}/{schema}/", "id", "table", "schema"),
        "table-metadata": op("GET", "/api/v1/database/{id}/table_metadata/", "id"),
        "table-extra": op("GET", "/api/v1/database/{id}/table_extra/{table}/{schema}/", "id", "table", "schema"),
        "select-star": op("GET", "/api/v1/database/{id}/select_star/{table}/{schema}/", "id", "table", "schema"),
        "functions": op("GET", "/api/v1/database/{id}/function_names/", "id"),
        "related": op("GET", "/api/v1/database/{id}/related_objects/", "id"),
        "create": op("POST", "/api/v1/database/", body=True),
        "update": op("PUT", "/api/v1/database/{id}", "id", body=True),
        "delete": op("DELETE", "/api/v1/database/{id}", "id"),
        "test": op("POST", "/api/v1/database/test_connection/", body=True),
        "validate-parameters": op("POST", "/api/v1/database/validate_parameters/", body=True),
        # Syntax-only, and only where SQL_VALIDATORS_BY_ENGINE has an entry (PostgreSQL
        # and Presto upstream); every other engine answers 422. It does not check that
        # tables or columns exist, so it cannot stand in for executing the query.
        "validate-sql": op("POST", "/api/v1/database/{id}/validate_sql/", "id", body=True),
        "export": op("GET", "/api/v1/database/export/", binary=True),
        "import": op("POST", "/api/v1/database/import/", upload=True),
    },
    "sql-lab": {
        "execute": op("POST", "/api/v1/sqllab/execute/", body=True),
        "estimate": op("POST", "/api/v1/sqllab/estimate/", body=True),
        "results": op("GET", "/api/v1/sqllab/results/"),
        "export": op("GET", "/api/v1/sqllab/export/{client_id}/", "client_id", binary=True),
        "format": op("POST", "/api/v1/sqllab/format_sql/", body=True),
    },
    "queries": {
        "list": op("GET", "/api/v1/query/"),
        "get": op("GET", "/api/v1/query/{id}", "id"),
        "updated-since": op("GET", "/api/v1/query/updated_since"),
        "stop": op("POST", "/api/v1/query/stop", body=True),
        "distinct": op("GET", "/api/v1/query/distinct/{column}", "column"),
    },
    "saved-queries": {
        "list": op("GET", "/api/v1/saved_query/"),
        "get": op("GET", "/api/v1/saved_query/{id}", "id"),
        "create": op("POST", "/api/v1/saved_query/", body=True),
        "update": op("PUT", "/api/v1/saved_query/{id}", "id", body=True),
        "delete": op("DELETE", "/api/v1/saved_query/{id}", "id"),
        "bulk-delete": op("DELETE", "/api/v1/saved_query/", body=True),
        "export": op("GET", "/api/v1/saved_query/export/", binary=True),
        "import": op("POST", "/api/v1/saved_query/import/", upload=True),
    },
    "explore": {
        "get": op("GET", "/api/v1/explore/"),
        "form-data-create": op("POST", "/api/v1/explore/form_data", body=True),
        "form-data-get": op("GET", "/api/v1/explore/form_data/{key}", "key"),
        "form-data-delete": op("DELETE", "/api/v1/explore/form_data/{key}", "key"),
        "permalink-create": op("POST", "/api/v1/explore/permalink", body=True),
        "permalink-get": op("GET", "/api/v1/explore/permalink/{key}", "key"),
    },
    "datasources": {
        "column-values": op("GET", "/api/v1/datasource/{type}/{id}/column/{column}/values/", "type", "id", "column"),
    },
    "advanced-types": {
        "list": op("GET", "/api/v1/advanced_data_type/types"),
        # Conversion is a GET with rison arguments, e.g. --param "q=(type:port,values:!(http))".
        "convert": op("GET", "/api/v1/advanced_data_type/convert"),
    },
    "tags": {
        "list": op("GET", "/api/v1/tag/"), "get": op("GET", "/api/v1/tag/{id}", "id"),
        "bulk-create": op("POST", "/api/v1/tag/bulk_create", body=True),
        "objects": op("GET", "/api/v1/tag/get_objects/"),
        # {type} is the numeric ObjectType: 1 query, 2 chart, 3 dashboard, 4 dataset.
        # Attach reads {"properties": {"tags": [...]}} — Superset's own OpenAPI documents a
        # bare {"tags": [...]} but the handler only looks under "properties".
        # Detach names the single tag in the path instead.
        "attach": op("POST", "/api/v1/tag/{type}/{id}/", "type", "id", body=True),
        "detach": op("DELETE", "/api/v1/tag/{type}/{id}/{tag}/", "type", "id", "tag"),
        "favorite-status": op("GET", "/api/v1/tag/favorite_status/"),
    },
    "reports": {
        "list": op("GET", "/api/v1/report/"), "get": op("GET", "/api/v1/report/{id}", "id"),
        "create": op("POST", "/api/v1/report/", body=True),
        "update": op("PUT", "/api/v1/report/{id}", "id", body=True),
        "delete": op("DELETE", "/api/v1/report/{id}", "id"),
        "logs": op("GET", "/api/v1/report/{id}/log/", "id"),
        "log-get": op("GET", "/api/v1/report/{id}/log/{log_id}", "id", "log_id"),
        "slack-channels": op("GET", "/api/v1/report/slack_channels/"),
    },
    "annotations": {
        "layers-list": op("GET", "/api/v1/annotation_layer/"),
        "layers-get": op("GET", "/api/v1/annotation_layer/{id}", "id"),
        "layers-create": op("POST", "/api/v1/annotation_layer/", body=True),
        "layers-update": op("PUT", "/api/v1/annotation_layer/{id}", "id", body=True),
        "layers-delete": op("DELETE", "/api/v1/annotation_layer/{id}", "id"),
        # Annotations are nested under their layer; there is no top-level /annotation/ resource.
        "list": op("GET", "/api/v1/annotation_layer/{layer_id}/annotation/", "layer_id"),
        "get": op("GET", "/api/v1/annotation_layer/{layer_id}/annotation/{id}", "layer_id", "id"),
        "create": op("POST", "/api/v1/annotation_layer/{layer_id}/annotation/", "layer_id", body=True),
        "update": op("PUT", "/api/v1/annotation_layer/{layer_id}/annotation/{id}", "layer_id", "id", body=True),
        "delete": op("DELETE", "/api/v1/annotation_layer/{layer_id}/annotation/{id}", "layer_id", "id"),
    },
    "rls": {
        "list": op("GET", "/api/v1/rowlevelsecurity/"), "get": op("GET", "/api/v1/rowlevelsecurity/{id}", "id"),
        "create": op("POST", "/api/v1/rowlevelsecurity/", body=True),
        "update": op("PUT", "/api/v1/rowlevelsecurity/{id}", "id", body=True),
        "delete": op("DELETE", "/api/v1/rowlevelsecurity/{id}", "id"),
        "bulk-delete": op("DELETE", "/api/v1/rowlevelsecurity/", body=True),
    },
    "assets": {"export": op("GET", "/api/v1/assets/export/", binary=True), "import": op("POST", "/api/v1/assets/import/", upload=True)},
    "cache": {"invalidate": op("POST", "/api/v1/cachekey/invalidate", body=True)},
    "audit": {
        "list": op("GET", "/api/v1/log/"), "get": op("GET", "/api/v1/log/{id}", "id"),
        "recent": op("GET", "/api/v1/log/recent_activity/"),
    },
}


READ_METHODS = {"GET", "HEAD", "OPTIONS"}

"""Declared Grafana OSS data-plane operations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    args: tuple[str, ...] = ()
    body: bool = False
    binary: bool = False


def op(method: str, path: str, *args: str, body: bool = False, binary: bool = False) -> Operation:
    return Operation(method, path, args, body, binary)


OPERATIONS: dict[str, dict[str, Operation]] = {
    "status": {
        "health": op("GET", "/api/health"), "version": op("GET", "/api/health"),
        "whoami": op("GET", "/api/user"),
    },
    "dashboards": {
        "list": op("GET", "/api/search"), "search": op("GET", "/api/search"),
        "get": op("GET", "/api/dashboards/uid/{uid}", "uid"),
        "delete": op("DELETE", "/api/dashboards/uid/{uid}", "uid"),
        "import": op("POST", "/api/dashboards/import", body=True),
        "tags": op("GET", "/api/dashboards/tags"), "home": op("GET", "/api/dashboards/home"),
        "versions": op("GET", "/api/dashboards/uid/{uid}/versions", "uid"),
        "version-get": op("GET", "/api/dashboards/uid/{uid}/versions/{version}", "uid", "version"),
        "restore": op("POST", "/api/dashboards/uid/{uid}/restore", "uid", body=True),
        "permissions-get": op("GET", "/api/dashboards/uid/{uid}/permissions", "uid"),
        "permissions-set": op("POST", "/api/dashboards/uid/{uid}/permissions", "uid", body=True),
        "public-list": op("GET", "/api/dashboards/public-dashboards"),
        "public-get": op("GET", "/api/dashboards/uid/{uid}/public-dashboards", "uid"),
        "public-enable": op("POST", "/api/dashboards/uid/{uid}/public-dashboards", "uid", body=True),
        "public-update": op("PATCH", "/api/dashboards/uid/{uid}/public-dashboards/{public_uid}", "uid", "public_uid", body=True),
        "public-delete": op("DELETE", "/api/dashboards/uid/{uid}/public-dashboards/{public_uid}", "uid", "public_uid"),
        "snapshot-create": op("POST", "/api/snapshots", body=True),
        "snapshot-list": op("GET", "/api/dashboard/snapshots"),
        "snapshot-get": op("GET", "/api/snapshots/{key}", "key"),
        "snapshot-delete": op("DELETE", "/api/snapshots-delete/{key}", "key"),
    },
    "folders": {
        "list": op("GET", "/api/folders"), "get": op("GET", "/api/folders/{uid}", "uid"),
        "create": op("POST", "/api/folders", body=True),
        "update": op("PUT", "/api/folders/{uid}", "uid", body=True),
        "move": op("POST", "/api/folders/{uid}/move", "uid", body=True),
        "delete": op("DELETE", "/api/folders/{uid}", "uid"),
        "counts": op("GET", "/api/folders/{uid}/counts", "uid"),
        "permissions-get": op("GET", "/api/folders/{uid}/permissions", "uid"),
        "permissions-set": op("POST", "/api/folders/{uid}/permissions", "uid", body=True),
    },
    "datasources": {
        "list": op("GET", "/api/datasources"), "get": op("GET", "/api/datasources/uid/{uid}", "uid"),
        "get-by-name": op("GET", "/api/datasources/name/{name}", "name"),
        "create": op("POST", "/api/datasources", body=True),
        "update": op("PUT", "/api/datasources/uid/{uid}", "uid", body=True),
        "delete": op("DELETE", "/api/datasources/uid/{uid}", "uid"),
        "health": op("GET", "/api/datasources/uid/{uid}/health", "uid"),
        "resources": op("GET", "/api/datasources/uid/{uid}/resources/{route}", "uid", "route"),
        "proxy-get": op("GET", "/api/datasources/proxy/uid/{uid}/{route}", "uid", "route"),
        "proxy-post": op("POST", "/api/datasources/proxy/uid/{uid}/{route}", "uid", "route", body=True),
    },
    "queries": {
        "run": op("POST", "/api/ds/query", body=True),
        "history-list": op("GET", "/api/query-history"),
        "history-create": op("POST", "/api/query-history", body=True),
        "history-update": op("PATCH", "/api/query-history/{uid}", "uid", body=True),
        "history-delete": op("DELETE", "/api/query-history/{uid}", "uid"),
        "star": op("POST", "/api/query-history/star/{uid}", "uid"),
        "unstar": op("DELETE", "/api/query-history/star/{uid}", "uid"),
    },
    "annotations": {
        "list": op("GET", "/api/annotations"), "get": op("GET", "/api/annotations/{id}", "id"),
        "create": op("POST", "/api/annotations", body=True),
        "create-graphite": op("POST", "/api/annotations/graphite", body=True),
        "update": op("PUT", "/api/annotations/{id}", "id", body=True),
        "patch": op("PATCH", "/api/annotations/{id}", "id", body=True),
        "delete": op("DELETE", "/api/annotations/{id}", "id"),
        "mass-delete": op("POST", "/api/annotations/mass-delete", body=True),
        "tags": op("GET", "/api/annotations/tags"),
    },
    "library-elements": {
        "list": op("GET", "/api/library-elements"), "get": op("GET", "/api/library-elements/{uid}", "uid"),
        "get-by-name": op("GET", "/api/library-elements/name/{name}", "name"),
        "create": op("POST", "/api/library-elements", body=True),
        "update": op("PATCH", "/api/library-elements/{uid}", "uid", body=True),
        "delete": op("DELETE", "/api/library-elements/{uid}", "uid"),
        "connections": op("GET", "/api/library-elements/{uid}/connections/", "uid"),
    },
    "playlists": {
        "list": op("GET", "/api/playlists"), "get": op("GET", "/api/playlists/{uid}", "uid"),
        "create": op("POST", "/api/playlists", body=True),
        "update": op("PUT", "/api/playlists/{uid}", "uid", body=True),
        "delete": op("DELETE", "/api/playlists/{uid}", "uid"),
        "items": op("GET", "/api/playlists/{uid}/items", "uid"),
    },
    "correlations": {
        "list": op("GET", "/api/datasources/uid/{source}/correlations", "source"),
        "get": op("GET", "/api/datasources/uid/{source}/correlations/{uid}", "source", "uid"),
        "create": op("POST", "/api/datasources/uid/{source}/correlations", "source", body=True),
        "update": op("PATCH", "/api/datasources/uid/{source}/correlations/{uid}", "source", "uid", body=True),
        "delete": op("DELETE", "/api/datasources/uid/{source}/correlations/{uid}", "source", "uid"),
    },
    "alert-rules": {
        "list": op("GET", "/api/v1/provisioning/alert-rules"),
        "get": op("GET", "/api/v1/provisioning/alert-rules/{uid}", "uid"),
        "create": op("POST", "/api/v1/provisioning/alert-rules", body=True),
        "update": op("PUT", "/api/v1/provisioning/alert-rules/{uid}", "uid", body=True),
        "delete": op("DELETE", "/api/v1/provisioning/alert-rules/{uid}", "uid"),
        "export": op("GET", "/api/v1/provisioning/alert-rules/export"),
    },
    "alert-groups": {
        "get": op("GET", "/api/v1/provisioning/folder/{folder}/rule-groups/{group}", "folder", "group"),
        "replace": op("PUT", "/api/v1/provisioning/folder/{folder}/rule-groups/{group}", "folder", "group", body=True),
        "delete": op("DELETE", "/api/v1/provisioning/folder/{folder}/rule-groups/{group}", "folder", "group"),
        "export": op("GET", "/api/v1/provisioning/folder/{folder}/rule-groups/{group}/export", "folder", "group"),
    },
    "contact-points": {
        "list": op("GET", "/api/v1/provisioning/contact-points"), "create": op("POST", "/api/v1/provisioning/contact-points", body=True),
        "update": op("PUT", "/api/v1/provisioning/contact-points/{uid}", "uid", body=True),
        "delete": op("DELETE", "/api/v1/provisioning/contact-points/{uid}", "uid"),
        "export": op("GET", "/api/v1/provisioning/contact-points/export"),
    },
    "notification-policies": {
        "get": op("GET", "/api/v1/provisioning/policies"), "replace": op("PUT", "/api/v1/provisioning/policies", body=True),
        "reset": op("DELETE", "/api/v1/provisioning/policies"), "export": op("GET", "/api/v1/provisioning/policies/export"),
    },
    "mute-timings": {
        "list": op("GET", "/api/v1/provisioning/mute-timings"), "get": op("GET", "/api/v1/provisioning/mute-timings/{name}", "name"),
        "create": op("POST", "/api/v1/provisioning/mute-timings", body=True),
        "update": op("PUT", "/api/v1/provisioning/mute-timings/{name}", "name", body=True),
        "delete": op("DELETE", "/api/v1/provisioning/mute-timings/{name}", "name"),
        "export": op("GET", "/api/v1/provisioning/mute-timings/export"),
    },
    "alert-templates": {
        "list": op("GET", "/api/v1/provisioning/templates"), "get": op("GET", "/api/v1/provisioning/templates/{name}", "name"),
        "put": op("PUT", "/api/v1/provisioning/templates/{name}", "name", body=True),
        "delete": op("DELETE", "/api/v1/provisioning/templates/{name}", "name"),
    },
    "recording-rules": {
        "list": op("GET", "/api/recording-rules"), "create": op("POST", "/api/recording-rules", body=True),
        "update": op("PUT", "/api/recording-rules", body=True),
        "delete": op("DELETE", "/api/recording-rules/{id}", "id"),
        "test": op("POST", "/api/recording-rules/test", body=True),
        "writer-get": op("GET", "/api/recording-rules/writer"),
        "writer-set": op("POST", "/api/recording-rules/writer", body=True),
        "writer-delete": op("DELETE", "/api/recording-rules/writer"),
    },
    "silences": {
        "list": op("GET", "/api/alertmanager/grafana/api/v2/silences"),
        "get": op("GET", "/api/alertmanager/grafana/api/v2/silence/{id}", "id"),
        "create": op("POST", "/api/alertmanager/grafana/api/v2/silences", body=True),
        "update": op("POST", "/api/alertmanager/grafana/api/v2/silences", body=True),
        "delete": op("DELETE", "/api/alertmanager/grafana/api/v2/silence/{id}", "id"),
    },
}


READ_METHODS = {"GET", "HEAD", "OPTIONS"}

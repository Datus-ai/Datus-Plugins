"""Lazy Kubernetes client construction and namespace-scoped resource helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

from .config import Settings
from .errors import ApiError, ConfigError, MissingDependencyError, UsageError
from .output import plain

#: SPDY/WebSocket channel the API server uses for the exec termination status.
EXEC_ERROR_CHANNEL = 3


def _import_kubernetes():
    try:
        import kubernetes
        from kubernetes import client, config, dynamic, watch
        from kubernetes.client.exceptions import ApiException
        from kubernetes.dynamic.exceptions import DynamicApiError, ResourceNotFoundError
    except ImportError as exc:
        raise MissingDependencyError(
            "the `kubernetes` dependency is not installed; reinstall datus-k8s-plugin"
        ) from exc
    return kubernetes, client, config, dynamic, watch, ApiException, DynamicApiError, ResourceNotFoundError


def exec_exit_code(payload: str) -> int:
    """Translate the exec error channel's status frame into a process exit code.

    The API server closes a successful exec with ``status: Success`` and a failed
    one with a ``NonZeroExitCode`` cause carrying the code. An empty frame means
    the stream ended without the server reporting anything — a timeout or a lost
    connection — and must never be reported as success.
    """
    text = str(payload or "").strip()
    if not text:
        raise ApiError("pod exec ended without a status frame (timed out or connection lost)")
    try:
        status = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ApiError(f"pod exec returned an unreadable status frame: {text}") from exc
    if not isinstance(status, dict):
        raise ApiError(f"pod exec returned an unreadable status frame: {text}")
    if str(status.get("status")) == "Success":
        return 0
    for cause in (status.get("details") or {}).get("causes") or []:
        if not isinstance(cause, dict) or str(cause.get("reason")) != "ExitCode":
            continue
        try:
            return int(str(cause.get("message")))
        except (TypeError, ValueError):
            break
    raise ApiError(str(status.get("message") or text))


@dataclass
class LoadedClient:
    settings: Settings
    kubeconfig_path: Path
    context_name: str
    api_client: Any
    dynamic_module: Any
    typed: Any
    watch_module: Any
    _dynamic: Any = None

    @classmethod
    def load(cls, settings: Settings) -> "LoadedClient":
        (
            _kubernetes,
            client,
            config,
            dynamic,
            watch,
            _api_exc,
            _dynamic_exc,
            _not_found,
        ) = _import_kubernetes()
        path = settings.resolve_kubeconfig()
        try:
            contexts, active = config.list_kube_config_contexts(config_file=str(path))
        except Exception as exc:
            raise ConfigError(f"cannot read kubeconfig {path}: {exc}") from exc
        available = {str(item.get("name")) for item in contexts or []}
        context = settings.context or str((active or {}).get("name") or "")
        if not context:
            raise ConfigError(
                f"no context configured and kubeconfig {path} has no current-context"
            )
        if context not in available:
            raise ConfigError(
                f"context {context!r} does not exist in kubeconfig {path}; "
                f"available: {', '.join(sorted(available)) or '<none>'}"
            )
        try:
            config.load_kube_config(config_file=str(path), context=context)
            api_client = client.ApiClient()
        except Exception as exc:
            raise ConfigError(f"cannot load context {context!r} from {path}: {exc}") from exc
        return cls(settings, path, context, api_client, dynamic, client, watch)

    @property
    def dynamic(self) -> Any:
        if self._dynamic is None:
            self._dynamic = self.dynamic_module.DynamicClient(self.api_client)
        return self._dynamic

    def resource(
        self,
        name: str | None = None,
        *,
        api_version: str | None = None,
        kind: str | None = None,
    ) -> Any:
        token = str(name or "").split("/", 1)[0]
        try:
            if api_version and kind:
                resource = self.dynamic.resources.get(api_version=api_version, kind=kind)
            elif api_version and name:
                resource = self.dynamic.resources.get(
                    api_version=api_version, name=token
                )
            else:
                try:
                    resource = self.dynamic.resources.get(name=token)
                except Exception:
                    candidates = []
                    for candidate in self.dynamic.resources.search():
                        candidate_kind = str(getattr(candidate, "kind", "") or "")
                        if candidate_kind.endswith("List"):
                            continue
                        aliases = {
                            str(getattr(candidate, "name", "") or "").casefold(),
                            str(getattr(candidate, "singular_name", "") or "").casefold(),
                            candidate_kind.casefold(),
                            *{
                                str(value).casefold()
                                for value in (getattr(candidate, "short_names", []) or [])
                            },
                        }
                        if token.casefold() in aliases:
                            candidates.append(candidate)
                    if not candidates:
                        raise
                    candidates.sort(
                        key=lambda item: (
                            not bool(getattr(item, "preferred", False)),
                            bool(getattr(item, "group", "")),
                            str(getattr(item, "group_version", "")),
                        )
                    )
                    resource = candidates[0]
        except Exception as exc:
            raise UsageError(f"unknown Kubernetes resource {kind or name!r}: {exc}") from exc
        if not bool(getattr(resource, "namespaced", False)):
            display = kind or name or getattr(resource, "kind", "resource")
            raise UsageError(
                f"cluster-scoped resource {display!r} is blocked by this k8s profile"
            )
        return resource

    def namespace(self, requested: str | None) -> str:
        return self.settings.check_namespace(requested)

    def request_timeout(self) -> float | None:
        value = self.settings.timeout_seconds
        return value or None

    def get(
        self,
        resource_name: str,
        names: list[str],
        namespace: str,
        *,
        label_selector: str | None = None,
        field_selector: str | None = None,
    ) -> Any:
        resource = self.resource(resource_name)
        kwargs: dict[str, Any] = {"namespace": namespace}
        if label_selector:
            kwargs["label_selector"] = label_selector
        if field_selector:
            kwargs["field_selector"] = field_selector
        if self.request_timeout():
            kwargs["_request_timeout"] = self.request_timeout()
        if not names:
            return resource.get(**kwargs)
        values = []
        for name in names:
            values.append(plain(resource.get(name=name, **kwargs)))
        return {"apiVersion": "v1", "kind": "List", "items": values}

    def stream_get(
        self,
        resource_name: str,
        namespace: str,
        *,
        label_selector: str | None = None,
        field_selector: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        resource = self.resource(resource_name)
        watcher = self.watch_module.Watch()
        kwargs: dict[str, Any] = {"namespace": namespace}
        if label_selector:
            kwargs["label_selector"] = label_selector
        if field_selector:
            kwargs["field_selector"] = field_selector
        if self.request_timeout():
            kwargs["timeout_seconds"] = max(1, int(self.request_timeout() or 0))
        for event in watcher.stream(resource.get, **kwargs):
            yield {"type": event.get("type"), "object": plain(event.get("object"))}

    def exec_in_pod(
        self,
        *,
        pod: str,
        namespace: str,
        command: list[str],
        container: str | None = None,
        timeout: float | None = None,
    ) -> tuple[str, str, int]:
        """Run one non-interactive command in a pod; return (stdout, stderr, exit_code).

        stdin and TTY are always disabled. This exists so a diagnostic probe can
        read what a running JVM actually sees — its classpath, the JARs on disk,
        an SPI file — and never as an interactive shell. The caller decides what
        to do with a non-zero exit code; a rejection by the API server itself
        (missing container, denied subresource) raises :class:`ApiError`.
        """
        try:
            from kubernetes.stream import stream as open_stream
        except ImportError as exc:  # pragma: no cover - mirrors _import_kubernetes
            raise MissingDependencyError(
                "the `kubernetes` dependency is not installed; reinstall datus-k8s-plugin"
            ) from exc
        api = self.typed.CoreV1Api(self.api_client)
        kwargs: dict[str, Any] = {
            "command": list(command),
            "stdout": True,
            "stderr": True,
            "stdin": False,
            "tty": False,
            "_preload_content": False,
        }
        if container:
            kwargs["container"] = container
        session = open_stream(
            api.connect_get_namespaced_pod_exec,
            pod,
            namespace,
            **kwargs,
        )
        try:
            session.run_forever(timeout=timeout)
            stdout = session.read_stdout(timeout=1) or ""
            stderr = session.read_stderr(timeout=1) or ""
            status = session.read_channel(EXEC_ERROR_CHANNEL, timeout=1) or ""
        finally:
            session.close()
        return stdout, stderr, exec_exit_code(status)

    def manifest_resource(self, document: dict[str, Any]) -> tuple[Any, str, str]:
        api_version = str(document.get("apiVersion") or "")
        kind = str(document.get("kind") or "")
        metadata = document.get("metadata")
        if not api_version or not kind or not isinstance(metadata, dict):
            raise UsageError("each manifest must contain apiVersion, kind, and metadata")
        resource = self.resource(api_version=api_version, kind=kind)
        namespace = self.namespace(metadata.get("namespace"))
        metadata["namespace"] = namespace
        name = str(metadata.get("name") or "")
        if not name:
            raise UsageError(f"{kind} manifest is missing metadata.name")
        return resource, namespace, name

    def api_versions(self) -> list[str]:
        versions = ["v1"]
        try:
            data = self.api_client.call_api(
                "/apis",
                "GET",
                response_type="object",
                auth_settings=["BearerToken"],
                _return_http_data_only=True,
            )
            body = plain(data)
            for group in body.get("groups", []) if isinstance(body, dict) else []:
                preferred = group.get("preferredVersion") or {}
                value = preferred.get("groupVersion")
                if value:
                    versions.append(str(value))
        except Exception as exc:
            raise ApiError(f"cannot discover API versions: {exc}") from exc
        return sorted(set(versions))

    def api_resources(self) -> list[dict[str, Any]]:
        try:
            found = self.dynamic.resources.search()
        except Exception as exc:
            raise ApiError(f"cannot discover API resources: {exc}") from exc
        rows = []
        seen: set[tuple[str, str, str]] = set()
        for resource in found:
            if not bool(getattr(resource, "namespaced", False)):
                continue
            kind = str(getattr(resource, "kind", "") or "")
            if kind.endswith("List"):
                continue
            row = {
                "name": getattr(resource, "name", ""),
                "shortNames": list(getattr(resource, "short_names", []) or []),
                "apiVersion": getattr(resource, "group_version", ""),
                "kind": kind,
                "verbs": list(getattr(resource, "verbs", []) or []),
            }
            key = (str(row["name"]), str(row["apiVersion"]), str(row["kind"]))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        return sorted(rows, key=lambda row: (row["name"], row["apiVersion"]))

    def explain_schema(self, resource: Any, field_path: str | None = None) -> dict[str, Any]:
        """Fetch and traverse the server's OpenAPI v3 schema for a discovered kind."""
        group_version = str(getattr(resource, "group_version", "") or "")
        index = plain(
            self.api_client.call_api(
                "/openapi/v3",
                "GET",
                response_type="object",
                auth_settings=["BearerToken"],
                _return_http_data_only=True,
            )
        )
        key = f"api/{group_version}" if "/" not in group_version else f"apis/{group_version}"
        entry = (index.get("paths") or {}).get(key) if isinstance(index, dict) else None
        if not isinstance(entry, dict) or not entry.get("serverRelativeURL"):
            raise ApiError(f"OpenAPI v3 schema is unavailable for {group_version}")
        document = plain(
            self.api_client.call_api(
                entry["serverRelativeURL"],
                "GET",
                response_type="object",
                auth_settings=["BearerToken"],
                _return_http_data_only=True,
            )
        )
        schemas = ((document.get("components") or {}).get("schemas") or {})
        kind = str(getattr(resource, "kind", "") or "")
        selected: dict[str, Any] | None = None
        for schema in schemas.values():
            gvks = schema.get("x-kubernetes-group-version-kind") or []
            if any(
                str(gvk.get("kind")) == kind
                and f"{gvk.get('group')}/{gvk.get('version')}".strip("/") == group_version
                for gvk in gvks
            ):
                selected = schema
                break
        if selected is None:
            raise ApiError(f"OpenAPI schema for {group_version} {kind} was not found")
        def expand(node: dict[str, Any], seen: frozenset[str] = frozenset(), depth: int = 0):
            if depth > 10:
                return node
            if isinstance(node.get("allOf"), list):
                merged = {key: value for key, value in node.items() if key != "allOf"}
                merged_properties = dict(merged.get("properties") or {})
                for branch in node["allOf"]:
                    if not isinstance(branch, dict):
                        continue
                    branch_value = expand(branch, seen, depth + 1)
                    merged_properties.update(branch_value.get("properties") or {})
                    for key, value in branch_value.items():
                        if key != "properties":
                            merged.setdefault(key, value)
                if merged_properties:
                    merged["properties"] = merged_properties
                return expand(merged, seen, depth + 1)
            reference = node.get("$ref")
            if reference and reference.startswith("#/components/schemas/"):
                schema_name = reference.rsplit("/", 1)[-1]
                if schema_name in seen:
                    return {"type": "object", "description": f"recursive reference to {schema_name}"}
                target = schemas.get(schema_name)
                if isinstance(target, dict):
                    return expand(target, seen | {schema_name}, depth + 1)
            result = dict(node)
            if isinstance(result.get("properties"), dict):
                result["properties"] = {
                    name: expand(value, seen, depth + 1)
                    for name, value in result["properties"].items()
                    if isinstance(value, dict)
                }
            if isinstance(result.get("items"), dict):
                result["items"] = expand(result["items"], seen, depth + 1)
            return result

        current = expand(selected)
        for segment in (field_path or "").split("."):
            if not segment:
                continue
            properties = current.get("properties") or {}
            if segment not in properties:
                raise UsageError(f"field {field_path!r} does not exist on {kind}")
            current = properties[segment]
        return current


class Context:
    """A lazily loaded client, so `--help` never touches kubeconfig or imports Kubernetes."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: LoadedClient | None = None

    @property
    def client(self) -> LoadedClient:
        if self._client is None:
            self._client = LoadedClient.load(self.settings)
        return self._client


def format_api_exception(exc: Exception) -> ApiError:
    status = getattr(exc, "status", None)
    reason = getattr(exc, "reason", None)
    body = getattr(exc, "body", None)
    detail = body or reason or str(exc)
    prefix = f"Kubernetes API error {status}" if status else "Kubernetes API error"
    return ApiError(f"{prefix}: {detail}")

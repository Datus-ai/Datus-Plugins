"""Lazy Kubernetes client construction and namespace-scoped resource helpers."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml

from .cloud_contract import (
    ClusterConnection,
    ContractError,
    ExecCredential,
    KubernetesAccess,
)

from .config import Settings
from .errors import ApiError, ConfigError, MissingDependencyError, UsageError
from .output import plain

#: SPDY/WebSocket channel the API server uses for the exec termination status.
EXEC_ERROR_CHANNEL = 3

# Kubernetes 1.30+ serves the same aggregated discovery document used by
# kubectl's RESTMapper.  Older servers ignore this Accept header and return the
# legacy APIVersions/APIGroupList documents, which we detect and fall back from.
AGGREGATED_DISCOVERY_ACCEPT = (
    "application/json;g=apidiscovery.k8s.io;v=v2;as=APIGroupDiscoveryList,"
    "application/json;g=apidiscovery.k8s.io;v=v2beta1;as=APIGroupDiscoveryList,"
    "application/json"
)

logger = logging.getLogger(__name__)


def _provider_command(settings: Settings, *args: str) -> list[str]:
    command = [
        "datus",
        str(settings.provider),
    ]
    if settings.provider_config:
        command.extend(("--config", settings.provider_config))
    command.extend(("--profile", str(settings.provider_profile), *args))
    return command


def run_cloud_provider(settings: Settings, *args: str) -> dict[str, Any]:
    """Invoke a cloud plugin's versioned managed-Kubernetes machine command."""
    command = _provider_command(settings, *args)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(5.0, settings.timeout_seconds or 30.0),
        )
    except FileNotFoundError as exc:
        raise ConfigError("the `datus` executable is unavailable to the k8s plugin") from exc
    except subprocess.TimeoutExpired as exc:
        raise ConfigError(
            f"{settings.provider} Kubernetes provider timed out"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise ConfigError(
            f"{settings.provider} Kubernetes provider failed: {detail}"
        )
    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        raise ConfigError(
            f"{settings.provider} Kubernetes provider returned invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise ConfigError(
            f"{settings.provider} Kubernetes provider returned a non-object response"
        )
    return payload


class ManagedCredentialProvider:
    """Cloud-plugin bridge with short-lived credential caching and refresh."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._credential: ExecCredential | None = None
        self._credential_dir: tempfile.TemporaryDirectory[str] | None = None
        self._cert_file: Path | None = None
        self._key_file: Path | None = None

    def connection(self) -> ClusterConnection:
        payload = run_cloud_provider(self.settings, "kubernetes", "cluster")
        try:
            return ClusterConnection.from_dict(
                payload,
                expected_provider=str(self.settings.provider),
            )
        except ContractError as exc:
            raise ConfigError(f"unsafe managed Kubernetes connection: {exc}") from exc

    def access(self) -> KubernetesAccess:
        payload = run_cloud_provider(self.settings, "kubernetes", "access")
        try:
            access = KubernetesAccess.from_dict(
                payload, expected_provider=str(self.settings.provider)
            )
        except ContractError as exc:
            raise ConfigError(f"unsafe managed Kubernetes access: {exc}") from exc
        self._credential = access.credential
        return access

    def credential(self) -> ExecCredential:
        now = datetime.now(timezone.utc)
        if (
            self._credential is not None
            and self._credential.expiration_timestamp > now + timedelta(seconds=60)
        ):
            return self._credential
        payload = run_cloud_provider(self.settings, "kubernetes", "credential")
        try:
            self._credential = ExecCredential.from_dict(payload)
        except ContractError as exc:
            raise ConfigError(f"unsafe managed Kubernetes credential: {exc}") from exc
        return self._credential

    @staticmethod
    def _write_secret(path: Path, value: str) -> None:
        temporary = path.with_suffix(path.suffix + ".new")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except Exception:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise

    def _install_certificate(self, credential: ExecCredential) -> None:
        if self._credential_dir is None:
            self._credential_dir = tempfile.TemporaryDirectory(prefix="datus-k8s-")
            os.chmod(self._credential_dir.name, 0o700)
            self._cert_file = Path(self._credential_dir.name) / "client.crt"
            self._key_file = Path(self._credential_dir.name) / "client.key"
        self._write_secret(self._cert_file, str(credential.client_certificate_data))
        self._write_secret(self._key_file, str(credential.client_key_data))

    def refresh(self, configuration: Any) -> bool:
        previous = self._credential
        credential = self.credential()
        changed = credential is not previous
        if credential.token:
            configuration.cert_file = None
            configuration.key_file = None
            configuration.api_key["authorization"] = credential.token
            configuration.api_key_prefix["authorization"] = "Bearer"
        else:
            if changed or self._cert_file is None or self._key_file is None:
                self._install_certificate(credential)
            configuration.api_key.pop("authorization", None)
            configuration.api_key_prefix.pop("authorization", None)
            configuration.cert_file = str(self._cert_file)
            configuration.key_file = str(self._key_file)
        return changed

    def close(self) -> None:
        if self._credential_dir is not None:
            self._credential_dir.cleanup()
            self._credential_dir = None
            self._cert_file = None
            self._key_file = None


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
    kubeconfig_path: Path | None
    context_name: str
    api_client: Any
    dynamic_module: Any
    typed: Any
    watch_module: Any
    _dynamic: Any = None
    _discovered_resources: list[Any] | None = None
    credential_provider: Any = None
    managed_cluster: str | None = None

    def close(self) -> None:
        close = getattr(self.api_client, "close", None)
        if close is not None:
            close()
        pool = getattr(getattr(self.api_client, "rest_client", None), "pool_manager", None)
        if pool is not None:
            pool.clear()
        if self.credential_provider is not None:
            self.credential_provider.close()

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
        if settings.managed:
            return cls._load_managed(settings, client, config, dynamic, watch)
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

    @classmethod
    def _load_managed(
        cls,
        settings: Settings,
        client: Any,
        config: Any,
        dynamic: Any,
        watch: Any,
    ) -> "LoadedClient":
        provider = ManagedCredentialProvider(settings)
        if settings.provider == "ack":
            access = provider.access()
            connection = access.connection
            credential = access.credential
        else:
            connection = provider.connection()
            credential = provider.credential()
        config_dict = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {
                    "name": "managed",
                    "cluster": {
                        "server": connection.server,
                        "certificate-authority-data": connection.certificate_authority_data,
                    },
                }
            ],
            "users": [{"name": "managed", "user": {}}],
            "contexts": [
                {
                    "name": "managed",
                    "context": {
                        "cluster": "managed",
                        "user": "managed",
                        "namespace": settings.namespace,
                    },
                }
            ],
            "current-context": "managed",
        }
        configuration = client.Configuration()
        try:
            loader = config.kube_config.KubeConfigLoader(
                config_dict=config_dict, active_context="managed"
            )
            loader.load_and_set(configuration)
        except Exception as exc:
            raise ConfigError(
                f"cannot construct Kubernetes client for {settings.provider} "
                f"provider profile {settings.provider_profile!r}: {exc}"
            ) from exc
        provider.refresh(configuration)
        if credential.token:
            configuration.refresh_api_key_hook = provider.refresh
        try:
            api_client = client.ApiClient(configuration=configuration)
        except Exception:
            provider.close()
            raise
        original_call_api = api_client.call_api

        def call_api_with_credential_refresh(*args: Any, **kwargs: Any) -> Any:
            if provider.refresh(configuration) and provider.credential().certificate_based:
                pool = getattr(getattr(api_client, "rest_client", None), "pool_manager", None)
                if pool is not None:
                    pool.clear()
            return original_call_api(*args, **kwargs)

        api_client.call_api = call_api_with_credential_refresh
        return cls(
            settings,
            None,
            f"{settings.provider}:{connection.cluster}",
            api_client,
            dynamic,
            client,
            watch,
            credential_provider=provider,
            managed_cluster=connection.cluster,
        )

    @property
    def dynamic(self) -> Any:
        if self._dynamic is None:
            self._dynamic = self.dynamic_module.DynamicClient(self.api_client)
        return self._dynamic

    def _discovery_call(self, path: str, *, aggregated: bool = False) -> Any:
        headers = {"Accept": AGGREGATED_DISCOVERY_ACCEPT} if aggregated else None
        return plain(
            self.api_client.call_api(
                path,
                "GET",
                header_params=headers,
                response_type="object",
                auth_settings=["BearerToken"],
                _return_http_data_only=True,
            )
        )

    def _make_resource(
        self,
        *,
        prefix: str,
        group: str,
        version: str,
        preferred: bool,
        raw: dict[str, Any],
        aggregated: bool,
    ) -> Any | None:
        if aggregated:
            name = str(raw.get("resource") or "")
            response_kind = raw.get("responseKind") or {}
            kind = str(response_kind.get("kind") or "")
            namespaced = raw.get("scope") == "Namespaced"
            singular = raw.get("singularResource")
            short_names = raw.get("shortNames")
            categories = raw.get("categories")
            subresources = {
                str(item.get("subresource")): {
                    "kind": str((item.get("responseKind") or {}).get("kind") or kind),
                    "name": f"{name}/{item.get('subresource')}",
                    "subresource": str(item.get("subresource")),
                    "namespaced": namespaced,
                    "verbs": list(item.get("verbs") or []),
                    "singularName": "",
                }
                for item in raw.get("subresources") or []
                if isinstance(item, dict) and item.get("subresource")
            }
        else:
            name = str(raw.get("name") or "")
            kind = str(raw.get("kind") or "")
            namespaced = bool(raw.get("namespaced"))
            singular = raw.get("singularName")
            short_names = raw.get("shortNames")
            categories = raw.get("categories")
            subresources = raw.get("subresources") or {}
        if not name or not kind or "/" in name:
            return None
        return self.dynamic_module.Resource(
            prefix=prefix,
            group=group,
            api_version=version,
            kind=kind,
            namespaced=namespaced,
            verbs=list(raw.get("verbs") or []),
            name=name,
            preferred=preferred,
            client=self.dynamic,
            singularName=singular,
            shortNames=list(short_names or []),
            categories=list(categories or []),
            subresources=subresources,
        )

    def _aggregated_resources(
        self, core: dict[str, Any], groups: dict[str, Any]
    ) -> list[Any] | None:
        expected = {"v2", "v2beta1"}
        documents = (("api", core), ("apis", groups))
        if any(
            not isinstance(document, dict)
            or document.get("kind") != "APIGroupDiscoveryList"
            or str(document.get("apiVersion") or "").rsplit("/", 1)[-1] not in expected
            for _prefix, document in documents
        ):
            return None
        found = []
        for prefix, document in documents:
            for group_item in document.get("items") or []:
                if not isinstance(group_item, dict):
                    continue
                group = str((group_item.get("metadata") or {}).get("name") or "")
                for index, version_item in enumerate(group_item.get("versions") or []):
                    if not isinstance(version_item, dict):
                        continue
                    version = str(version_item.get("version") or "")
                    if not version or version_item.get("freshness") == "Stale":
                        continue
                    for raw in version_item.get("resources") or []:
                        if not isinstance(raw, dict):
                            continue
                        resource = self._make_resource(
                            prefix=prefix,
                            group=group,
                            version=version,
                            preferred=index == 0,
                            raw=raw,
                            aggregated=True,
                        )
                        if resource is not None:
                            found.append(resource)
        return found

    def _legacy_resource_list(
        self, path: str, *, prefix: str, group: str, version: str, preferred: bool
    ) -> list[Any]:
        document = self._discovery_call(path)
        if not isinstance(document, dict) or document.get("kind") != "APIResourceList":
            actual = document.get("kind") if isinstance(document, dict) else type(document).__name__
            logger.warning(
                "skipping malformed Kubernetes discovery endpoint %s: "
                "expected APIResourceList, got %r",
                path,
                actual,
            )
            return []
        raw_resources = [
            raw for raw in document.get("resources") or [] if isinstance(raw, dict)
        ]
        subresources: dict[str, dict[str, dict[str, Any]]] = {}
        for raw in raw_resources:
            raw_name = str(raw.get("name") or "")
            if "/" not in raw_name:
                continue
            parent, subresource = raw_name.split("/", 1)
            subresources.setdefault(parent, {})[subresource] = {
                **raw,
                "subresource": subresource,
            }
        found = []
        for raw in raw_resources:
            if "/" in str(raw.get("name") or ""):
                continue
            raw = {
                **raw,
                "subresources": subresources.get(str(raw.get("name") or ""), {}),
            }
            resource = self._make_resource(
                prefix=prefix,
                group=group,
                version=version,
                preferred=preferred,
                raw=raw,
                aggregated=False,
            )
            if resource is not None:
                found.append(resource)
        return found

    def _legacy_resources(
        self, core: dict[str, Any], groups: dict[str, Any]
    ) -> list[Any]:
        found = []
        versions = core.get("versions") if isinstance(core, dict) else []
        for index, version in enumerate(versions or []):
            value = str(version or "")
            if value:
                found.extend(
                    self._legacy_resource_list(
                        f"/api/{value}",
                        prefix="api",
                        group="",
                        version=value,
                        preferred=index == 0,
                    )
                )
        for group_item in groups.get("groups", []) if isinstance(groups, dict) else []:
            if not isinstance(group_item, dict):
                continue
            group = str(group_item.get("name") or "")
            preferred_version = str(
                (group_item.get("preferredVersion") or {}).get("version") or ""
            )
            for version_item in group_item.get("versions") or []:
                if not isinstance(version_item, dict):
                    continue
                version = str(version_item.get("version") or "")
                if version:
                    found.extend(
                        self._legacy_resource_list(
                            f"/apis/{group}/{version}",
                            prefix="apis",
                            group=group,
                            version=version,
                            preferred=version == preferred_version,
                        )
                    )
        return found

    def _resource_catalog(self) -> list[Any] | None:
        if self._discovered_resources is not None:
            return self._discovered_resources
        if not callable(getattr(self.api_client, "call_api", None)):
            return None
        try:
            core = self._discovery_call("/api", aggregated=True)
            groups = self._discovery_call("/apis", aggregated=True)
        except Exception as exc:
            if getattr(exc, "status", None) != 406:
                raise
            core = self._discovery_call("/api")
            groups = self._discovery_call("/apis")
        resources = self._aggregated_resources(core, groups)
        if resources is None:
            resources = self._legacy_resources(core, groups)
        self._discovered_resources = resources
        return resources

    @staticmethod
    def _resource_aliases(resource: Any) -> set[str]:
        return {
            str(getattr(resource, "name", "") or "").casefold(),
            str(getattr(resource, "singular_name", "") or "").casefold(),
            str(getattr(resource, "kind", "") or "").casefold(),
            *{
                str(value).casefold()
                for value in (getattr(resource, "short_names", []) or [])
            },
        }

    def _catalog_resource(
        self,
        resources: list[Any],
        token: str,
        *,
        api_version: str | None,
        kind: str | None,
    ) -> Any:
        folded = token.casefold()
        candidates = []
        for candidate in resources:
            aliases = self._resource_aliases(candidate)
            group = str(getattr(candidate, "group", "") or "").casefold()
            qualified = {f"{alias}.{group}" for alias in aliases if alias and group}
            if kind and str(getattr(candidate, "kind", "")).casefold() != kind.casefold():
                continue
            if api_version and str(getattr(candidate, "group_version", "")) != api_version:
                continue
            if not kind and folded not in aliases and folded not in qualified:
                continue
            candidates.append(candidate)
        if not candidates:
            raise LookupError(f"No matches found for {kind or token!r}")
        candidates.sort(
            key=lambda item: (
                not bool(getattr(item, "preferred", False)),
                bool(getattr(item, "group", "")),
                str(getattr(item, "group_version", "")),
            )
        )
        return candidates[0]

    def resource(
        self,
        name: str | None = None,
        *,
        api_version: str | None = None,
        kind: str | None = None,
    ) -> Any:
        token = str(name or "").split("/", 1)[0]
        try:
            catalog = self._resource_catalog()
            if catalog is not None:
                resource = self._catalog_resource(
                    catalog, token, api_version=api_version, kind=kind
                )
            elif api_version and kind:
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
                        aliases = self._resource_aliases(candidate)
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
            found = self._resource_catalog()
            if found is None:
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

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def format_api_exception(exc: Exception) -> ApiError:
    status = getattr(exc, "status", None)
    reason = getattr(exc, "reason", None)
    body = getattr(exc, "body", None)
    detail = body or reason or str(exc)
    prefix = f"Kubernetes API error {status}" if status else "Kubernetes API error"
    return ApiError(f"{prefix}: {detail}")

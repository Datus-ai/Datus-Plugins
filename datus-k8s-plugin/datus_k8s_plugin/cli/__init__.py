"""kubectl-style CLI parser and command handlers for `datus k8s`."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

import yaml

from .. import jsonpath
from ..client import Context, format_api_exception
from ..config import Settings
from ..errors import EXIT_USAGE, ApiError, ConfigError, PluginError, UsageError
from ..output import FORMATS, output_format, plain, print_rendered

PROG = "datus k8s"

#: Default `wait --fail-on`. Standard resources never set `status.error`; a custom
#: resource that does is reporting a failure, and waiting past it is pointless.
FAIL_ON_STATUS_ERROR = "jsonpath={.status.error}"


def _output(parser: argparse.ArgumentParser, default: str = "table") -> None:
    parser.add_argument(
        "-o",
        "--output",
        type=output_format,
        default=default,
        metavar="FORMAT",
        help=(
            f"one of {', '.join(FORMATS)}, or jsonpath={{.path.to.field}} to print "
            "a single field per object instead of the whole document"
        ),
    )


def _namespace(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-n", "--namespace")
    parser.add_argument(
        "-A",
        "--all-namespaces",
        action="store_true",
        help="unsupported by this namespace-scoped plugin",
    )


def _selectors(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-l", "--selector")
    parser.add_argument("--field-selector")


def _target(parser: argparse.ArgumentParser, names: str = "names") -> None:
    parser.add_argument("resource")
    parser.add_argument(names, nargs="*")
    _namespace(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Inspect and operate namespace-scoped Kubernetes resources with a "
            "kubectl-style command surface."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("version", help="show server, client, plugin, and effective context")
    p.add_argument("--client", action="store_true")
    p.add_argument("-o", "--output", choices=("json", "yaml"), default="json")
    p.set_defaults(func=_cmd_version)

    p = sub.add_parser("api-resources", help="list namespaced API resources")
    p.add_argument("--api-group")
    p.add_argument("--verbs")
    _output(p)
    p.set_defaults(func=_cmd_api_resources)

    p = sub.add_parser("api-versions", help="list API group versions")
    p.set_defaults(func=_cmd_api_versions)

    p = sub.add_parser("explain", help="show discovered resource information")
    p.add_argument("resource")
    p.add_argument("--api-version")
    p.add_argument("--recursive", action="store_true")
    p.set_defaults(func=_cmd_explain)

    p = sub.add_parser("get", help="display one or many resources")
    _target(p)
    _selectors(p)
    p.add_argument("-w", "--watch", action="store_true")
    p.add_argument("--no-headers", action="store_true")
    _output(p)
    p.set_defaults(func=_cmd_get)

    p = sub.add_parser("describe", help="show resource details")
    _target(p)
    _selectors(p)
    p.set_defaults(func=_cmd_describe)

    p = sub.add_parser("logs", help="print pod logs")
    p.add_argument("pod")
    p.add_argument("-c", "--container")
    p.add_argument(
        "--all-containers",
        dest="all_containers",
        action="store_true",
        help="print every container's log in turn, init containers first",
    )
    p.add_argument("-f", "--follow", action="store_true")
    p.add_argument("--previous", action="store_true")
    p.add_argument("--since")
    p.add_argument("--tail", type=int)
    p.add_argument("--timestamps", action="store_true")
    p.add_argument("--prefix", action="store_true")
    _namespace(p)
    p.set_defaults(func=_cmd_logs)

    p = sub.add_parser(
        "exec",
        help="run one non-interactive command in a pod",
        description=(
            "Run a single command in a pod and print its output. Put the command "
            "after `--`, for example: exec pod-a -c app -- sh -c 'ls -1 /opt/lib'. "
            "stdin and TTY are always disabled; the pod's exit code is returned."
        ),
    )
    p.add_argument("pod")
    p.add_argument("-c", "--container")
    p.add_argument("--timeout", help="give up after this duration, such as 30s or 2m")
    p.add_argument("command", nargs="*", metavar="-- COMMAND [ARG ...]")
    _namespace(p)
    p.set_defaults(func=_cmd_exec)

    p = sub.add_parser("events", help="list namespace events")
    p.add_argument("--for", dest="for_object")
    p.add_argument("--types")
    p.add_argument("-w", "--watch", action="store_true")
    _namespace(p)
    _output(p)
    p.set_defaults(func=_cmd_events)

    top = sub.add_parser("top", help="display resource usage")
    top_sub = top.add_subparsers(dest="top_command", required=True)
    p = top_sub.add_parser("pod", help="display pod usage")
    p.add_argument("name", nargs="?")
    p.add_argument("--containers", action="store_true")
    p.add_argument("--sort-by", choices=("cpu", "memory"))
    p.add_argument("--no-headers", action="store_true")
    _selectors(p)
    _namespace(p)
    p.set_defaults(func=_cmd_top_pod)

    auth = sub.add_parser("auth", help="inspect authorization")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    p = auth_sub.add_parser("can-i", help="check whether an action is allowed")
    p.add_argument("verb", nargs="?")
    p.add_argument("resource", nargs="?")
    p.add_argument("--subresource")
    p.add_argument("--list", action="store_true")
    p.add_argument("--quiet", action="store_true")
    _namespace(p)
    p.set_defaults(func=_cmd_auth_can_i)

    p = sub.add_parser("wait", help="wait for a resource condition")
    _target(p)
    p.add_argument(
        "--for",
        dest="condition",
        required=True,
        metavar="CONDITION",
        help=(
            "create, delete, condition=NAME[=VALUE], or "
            "jsonpath={.path.to.field}[=VALUE] for custom resources that report "
            "readiness outside status.conditions"
        ),
    )
    p.add_argument(
        "--fail-on",
        dest="fail_on",
        action="append",
        metavar="CONDITION",
        help=(
            "abort as soon as this condition holds instead of waiting out the "
            "timeout; same forms as --for, repeatable. Defaults to "
            f"{FAIL_ON_STATUS_ERROR}, which aborts once the resource reports a "
            "non-empty status.error. Pass --fail-on=none to wait regardless."
        ),
    )
    p.add_argument("--timeout", default="30s")
    p.add_argument(
        "--quiet",
        action="store_true",
        help="do not report observed values on stderr while waiting",
    )
    _selectors(p)
    p.set_defaults(func=_cmd_wait)

    rollout = sub.add_parser("rollout", help="manage workload rollout")
    rollout_sub = rollout.add_subparsers(dest="rollout_command", required=True)
    p = rollout_sub.add_parser("status", help="show rollout status")
    p.add_argument("resource")
    p.add_argument("--watch", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--timeout", default="0")
    _namespace(p)
    p.set_defaults(func=_cmd_rollout_status)
    p = rollout_sub.add_parser("restart", help="restart a workload")
    p.add_argument("resource")
    p.add_argument("-l", "--selector")
    _namespace(p)
    _output(p, "name")
    p.set_defaults(func=_cmd_rollout_restart)

    p = sub.add_parser("create", help="create resources from manifests")
    _manifest_args(p)
    p.set_defaults(func=_cmd_create)

    p = sub.add_parser("apply", help="server-side apply manifests")
    _manifest_args(p)
    p.add_argument("--field-manager")
    p.add_argument("--force-conflicts", action="store_true")
    p.set_defaults(func=_cmd_apply)

    p = sub.add_parser("delete", help="delete resources")
    p.add_argument("resource", nargs="?")
    p.add_argument("names", nargs="*")
    p.add_argument("-f", "--filename", action="append")
    p.add_argument("-l", "--selector")
    p.add_argument("--all", action="store_true")
    p.add_argument("--cascade", choices=("background", "foreground", "orphan"), default="background")
    p.add_argument("--grace-period", type=int)
    p.add_argument("--force", action="store_true")
    p.add_argument("--wait", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--timeout", default="30s")
    _namespace(p)
    _output(p, "name")
    p.set_defaults(func=_cmd_delete)

    p = sub.add_parser("patch", help="patch a resource")
    p.add_argument("resource")
    p.add_argument("name")
    patch_source = p.add_mutually_exclusive_group(required=True)
    patch_source.add_argument("-p", "--patch")
    patch_source.add_argument("--patch-file")
    p.add_argument("--type", choices=("json", "merge", "strategic"), default="strategic")
    _namespace(p)
    _output(p, "name")
    p.set_defaults(func=_cmd_patch)

    p = sub.add_parser("scale", help="set workload replicas")
    p.add_argument("resources", nargs="+")
    p.add_argument("--replicas", type=int, required=True)
    p.add_argument("--current-replicas", type=int)
    p.add_argument("--resource-version")
    _namespace(p)
    _output(p, "name")
    p.set_defaults(func=_cmd_scale)

    for command, noun, handler in (
        ("label", "labels", _cmd_label),
        ("annotate", "annotations", _cmd_annotate),
    ):
        p = sub.add_parser(command, help=f"update resource {noun}")
        p.add_argument("resource")
        p.add_argument("name")
        p.add_argument("assignments", nargs="+")
        p.add_argument("--overwrite", action="store_true")
        p.add_argument("--resource-version")
        _namespace(p)
        _output(p, "name")
        p.set_defaults(func=handler)
    return parser


def _manifest_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-f", "--filename", action="append", required=True)
    parser.add_argument("--dry-run", choices=("none", "server"), default="none")
    _namespace(parser)
    _output(parser, "name")


def _scope(ctx: Context, ns: argparse.Namespace) -> str:
    if getattr(ns, "all_namespaces", False):
        raise UsageError("-A/--all-namespaces is blocked; select an allowed namespace with -n")
    return ctx.settings.check_namespace(getattr(ns, "namespace", None))


def _split_resource(value: str) -> tuple[str, list[str]]:
    if "/" not in value:
        return value, []
    resource, name = value.split("/", 1)
    return resource, [name]


def _documents(filenames: list[str] | None) -> list[dict[str, Any]]:
    if not filenames:
        raise UsageError("at least one -f/--filename is required")
    docs: list[dict[str, Any]] = []
    stdin_used = False
    for raw in filenames:
        if raw == "-":
            if stdin_used:
                raise UsageError("stdin (-) may only be specified once")
            stdin_used = True
            text = sys.stdin.read()
        else:
            path = Path(raw)
            if not path.is_file():
                raise UsageError(f"manifest is not a local file: {raw}")
            text = path.read_text(encoding="utf-8")
        try:
            loaded = list(yaml.safe_load_all(text))
        except yaml.YAMLError as exc:
            raise UsageError(f"invalid YAML in {raw}: {exc}") from exc
        for doc in loaded:
            if doc is None:
                continue
            if not isinstance(doc, dict):
                raise UsageError(f"manifest document in {raw} must be an object")
            docs.append(doc)
    if not docs:
        raise UsageError("no manifest documents found")
    return docs


def _cmd_version(ctx: Context, ns: argparse.Namespace) -> int:
    client = ctx.client
    try:
        plugin = package_version("datus-k8s-plugin")
    except PackageNotFoundError:
        plugin = "0.1.0"
    data: dict[str, Any] = {
        "pluginVersion": plugin,
        "pythonClientVersion": package_version("kubernetes"),
        "context": client.context_name,
        "namespace": ctx.settings.namespace,
        "authenticationProvider": ctx.settings.provider or "kubeconfig",
    }
    if ctx.settings.provider:
        data["cluster"] = client.managed_cluster
        data["providerProfile"] = ctx.settings.provider_profile
    if not ns.client:
        api = client.typed.VersionApi(client.api_client)
        data["serverVersion"] = plain(api.get_code(_request_timeout=client.request_timeout()))
    print_rendered(data, ns.output)
    return 0


def _cmd_api_resources(ctx: Context, ns: argparse.Namespace) -> int:
    rows = ctx.client.api_resources()
    if ns.api_group:
        rows = [row for row in rows if str(row["apiVersion"]).split("/", 1)[0] == ns.api_group]
    if ns.verbs:
        required = {v.strip() for v in ns.verbs.split(",") if v.strip()}
        rows = [row for row in rows if required <= set(row["verbs"])]
    if ns.output in {"json", "yaml"}:
        print_rendered(rows, ns.output)
    elif ns.output == "name":
        print("\n".join(row["name"] for row in rows))
    else:
        for row in rows:
            short = ",".join(row["shortNames"])
            print(f"{row['name']}\t{short}\t{row['apiVersion']}\t{row['kind']}")
    return 0


def _cmd_api_versions(ctx: Context, _ns: argparse.Namespace) -> int:
    print("\n".join(ctx.client.api_versions()))
    return 0


def _cmd_explain(ctx: Context, ns: argparse.Namespace) -> int:
    base, _, field_path = ns.resource.partition(".")
    resource = ctx.client.resource(base, api_version=ns.api_version)
    print(f"KIND:     {getattr(resource, 'kind', '')}")
    print(f"VERSION:  {getattr(resource, 'group_version', '')}")
    print("SCOPE:    Namespaced")
    print(f"RESOURCE: {getattr(resource, 'name', base)}")
    schema = ctx.client.explain_schema(resource, field_path or None)
    if field_path:
        print(f"FIELD:    {field_path}")
    if schema.get("type"):
        print(f"TYPE:     {schema['type']}")
    if schema.get("description"):
        print(f"\n{schema['description']}")
    properties = schema.get("properties") or {}
    if properties:
        print("\nFIELDS:")
        if ns.recursive:
            def walk(values: dict[str, Any], prefix: str = ""):
                for name, field in sorted(values.items()):
                    path = f"{prefix}.{name}".strip(".")
                    kind = field.get("type") or "<unknown>"
                    description = str(field.get("description") or "").splitlines()[0]
                    print(f"  {path}\t<{kind}>\t{description}")
                    children = field.get("properties") or {}
                    if children:
                        walk(children, path)

            walk(properties)
        else:
            for name, field in sorted(properties.items()):
                kind = field.get("type") or "<unknown>"
                description = str(field.get("description") or "").splitlines()[0]
                print(f"  {name}\t<{kind}>\t{description}")
    return 0


def _cmd_get(ctx: Context, ns: argparse.Namespace) -> int:
    namespace = _scope(ctx, ns)
    resource, embedded = _split_resource(ns.resource)
    names = embedded + ns.names
    if ns.watch:
        for event in ctx.client.stream_get(
            resource, namespace, label_selector=ns.selector, field_selector=ns.field_selector
        ):
            print_rendered(event, "json")
        return 0
    data = ctx.client.get(
        resource,
        names,
        namespace,
        label_selector=ns.selector,
        field_selector=ns.field_selector,
    )
    text_format = "wide" if ns.output == "wide" else ns.output
    rendered_data = data
    if ns.no_headers and text_format in {"table", "wide"}:
        from ..output import render

        lines = render(rendered_data, text_format, resource).splitlines()
        if len(lines) > 1:
            print("\n".join(lines[1:]))
    else:
        print_rendered(rendered_data, text_format, resource)
    return 0


def _cmd_describe(ctx: Context, ns: argparse.Namespace) -> int:
    namespace = _scope(ctx, ns)
    resource, embedded = _split_resource(ns.resource)
    data = ctx.client.get(
        resource,
        embedded + ns.names,
        namespace,
        label_selector=ns.selector,
        field_selector=ns.field_selector,
    )
    print_rendered(data, "yaml", resource)
    return 0


def _pod_containers(ctx: Context, pod: str, namespace: str) -> list[str]:
    """Every container in a pod, init containers first, in start order."""
    spec = plain(ctx.client.get("pods", [pod], namespace))["items"][0].get("spec") or {}
    names = [
        container.get("name")
        for group in ("initContainers", "init_containers", "containers")
        for container in spec.get(group) or []
    ]
    return [str(name) for name in dict.fromkeys(names) if name]


def _cmd_logs(ctx: Context, ns: argparse.Namespace) -> int:
    namespace = _scope(ctx, ns)
    if ns.all_containers:
        if ns.container:
            raise UsageError("--all-containers cannot be combined with -c/--container")
        if ns.follow:
            raise UsageError("--all-containers cannot be combined with -f/--follow")
        failures = 0
        for container in _pod_containers(ctx, ns.pod, namespace):
            one = argparse.Namespace(**vars(ns))
            one.all_containers, one.container = False, container
            print(f"==== {ns.pod}/{container} ====")
            try:
                _cmd_logs(ctx, one)
            except Exception as exc:
                # One container that has not started yet must not hide the logs of
                # the container that explains why.
                print(f"error: {format_api_exception(exc)}", file=sys.stderr)
                failures += 1
        return 0 if failures == 0 else 1
    kwargs: dict[str, Any] = {
        "name": ns.pod,
        "namespace": namespace,
        "container": ns.container,
        "follow": ns.follow,
        "previous": ns.previous,
        "timestamps": ns.timestamps,
        "_preload_content": not ns.follow,
        "_request_timeout": ctx.client.request_timeout(),
    }
    if ns.tail is not None:
        kwargs["tail_lines"] = ns.tail
    if ns.since:
        try:
            from ..config import duration_seconds

            kwargs["since_seconds"] = int(duration_seconds(ns.since))
        except ConfigError:
            try:
                datetime.fromisoformat(ns.since.replace("Z", "+00:00"))
                kwargs["since_time"] = ns.since
            except ValueError as exc:
                raise UsageError("--since must be a duration such as 5m or an ISO timestamp") from exc
    api = ctx.client.typed.CoreV1Api(ctx.client.api_client)
    response = api.read_namespaced_pod_log(**kwargs)
    if ns.follow and hasattr(response, "stream"):
        for chunk in response.stream():
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    else:
        text = str(response)
        if ns.prefix:
            prefix = f"{ns.pod}/{ns.container or '<default>'} "
            text = "\n".join(prefix + line for line in text.splitlines())
        print(text, end="" if text.endswith("\n") else "\n")
    return 0


def _cmd_exec(ctx: Context, ns: argparse.Namespace) -> int:
    namespace = _scope(ctx, ns)
    command = [str(token) for token in (ns.command or [])]
    if not command:
        raise UsageError(
            "exec requires a command after `--`, for example: exec pod-a -- ls -1 /opt/flink/lib"
        )
    timeout = _parse_wait_timeout(ns.timeout) if ns.timeout else ctx.client.request_timeout()
    stdout, stderr, code = ctx.client.exec_in_pod(
        pod=ns.pod,
        namespace=namespace,
        container=ns.container,
        command=command,
        timeout=timeout or None,
    )
    if stdout:
        print(stdout, end="" if stdout.endswith("\n") else "\n")
    if stderr:
        print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
    if code:
        # Mirror kubectl's wording so a non-zero exit is never mistaken for one of
        # this CLI's own exit codes (2 usage, 3 config, 8 missing dependency).
        print(f"error: command terminated with exit code {code}", file=sys.stderr)
    return code


def _cmd_events(ctx: Context, ns: argparse.Namespace) -> int:
    namespace = _scope(ctx, ns)
    selector = None
    if ns.for_object:
        resource_name, names = _split_resource(ns.for_object)
        if not names:
            raise UsageError("--for must be TYPE/NAME")
        obj = ctx.client.get(resource_name, names, namespace)
        item = plain(obj)["items"][0]
        selector = f"involvedObject.uid={item['metadata']['uid']}"
    api = ctx.client.typed.CoreV1Api(ctx.client.api_client)
    if ns.watch:
        watcher = ctx.client.watch_module.Watch()
        for event in watcher.stream(
            api.list_namespaced_event,
            namespace,
            field_selector=selector,
            timeout_seconds=max(1, int(ctx.client.request_timeout() or 30)),
        ):
            print_rendered(
                {"type": event.get("type"), "object": plain(event.get("object"))},
                "json",
            )
        return 0
    data = plain(api.list_namespaced_event(
        namespace,
        field_selector=selector,
        _request_timeout=ctx.client.request_timeout(),
    ))
    if ns.types:
        allowed = {value.strip() for value in ns.types.split(",")}
        data["items"] = [item for item in data.get("items", []) if item.get("type") in allowed]
    print_rendered(data, ns.output, "Event")
    return 0


def _quantity_cpu(value: str) -> float:
    if value.endswith("n"):
        return float(value[:-1]) / 1_000_000
    if value.endswith("u"):
        return float(value[:-1]) / 1_000
    if value.endswith("m"):
        return float(value[:-1])
    return float(value) * 1000


def _quantity_memory(value: str) -> float:
    units = {"Ki": 1, "Mi": 1024, "Gi": 1024 * 1024}
    for unit, factor in units.items():
        if value.endswith(unit):
            return float(value[: -len(unit)]) * factor
    return float(value)


def _cmd_top_pod(ctx: Context, ns: argparse.Namespace) -> int:
    namespace = _scope(ctx, ns)
    api = ctx.client.typed.CustomObjectsApi(ctx.client.api_client)
    kwargs = {"group": "metrics.k8s.io", "version": "v1beta1", "namespace": namespace, "plural": "pods"}
    if ns.name:
        data = {"items": [api.get_namespaced_custom_object(name=ns.name, **kwargs)]}
    else:
        data = api.list_namespaced_custom_object(
            label_selector=ns.selector,
            field_selector=ns.field_selector,
            **kwargs,
        )
    rows = []
    for pod in data.get("items", []):
        containers = pod.get("containers", [])
        selected = containers if ns.containers else [{"name": "", "usage": {
            "cpu": f"{sum(_quantity_cpu(c['usage']['cpu']) for c in containers):g}m",
            "memory": f"{sum(_quantity_memory(c['usage']['memory']) for c in containers):g}Ki",
        }}]
        for container in selected:
            rows.append(
                {
                    "name": pod["metadata"]["name"],
                    "container": container.get("name", ""),
                    "cpu": container["usage"]["cpu"],
                    "memory": container["usage"]["memory"],
                }
            )
    if ns.sort_by:
        fn = _quantity_cpu if ns.sort_by == "cpu" else _quantity_memory
        rows.sort(key=lambda row: fn(row[ns.sort_by]), reverse=True)
    headers = ["NAME"] + (["CONTAINER"] if ns.containers else []) + ["CPU(cores)", "MEMORY(bytes)"]
    if not ns.no_headers:
        print("\t".join(headers))
    for row in rows:
        values = [row["name"]] + ([row["container"]] if ns.containers else []) + [row["cpu"], row["memory"]]
        print("\t".join(values))
    return 0


def _cmd_auth_can_i(ctx: Context, ns: argparse.Namespace) -> int:
    namespace = _scope(ctx, ns)
    api = ctx.client.typed.AuthorizationV1Api(ctx.client.api_client)
    if ns.list:
        spec = ctx.client.typed.V1SelfSubjectRulesReviewSpec(namespace=namespace)
        body = ctx.client.typed.V1SelfSubjectRulesReview(spec=spec)
        result = plain(api.create_self_subject_rules_review(body))
        print_rendered(result, "yaml")
        return 0
    if not ns.verb or not ns.resource:
        raise UsageError("auth can-i requires VERB RESOURCE, or --list")
    resource = ctx.client.resource(ns.resource)
    attrs = ctx.client.typed.V1ResourceAttributes(
        namespace=namespace,
        verb=ns.verb,
        group=str(getattr(resource, "group", "") or ""),
        version=str(getattr(resource, "api_version", "") or ""),
        resource=str(getattr(resource, "name", ns.resource)),
        subresource=ns.subresource,
    )
    body = ctx.client.typed.V1SelfSubjectAccessReview(
        spec=ctx.client.typed.V1SelfSubjectAccessReviewSpec(resource_attributes=attrs)
    )
    result = api.create_self_subject_access_review(body)
    allowed = bool(result.status.allowed)
    if not ns.quiet:
        print("yes" if allowed else "no")
    return 0 if allowed else 1


def _parse_wait_timeout(raw: str) -> float:
    if raw == "0":
        return 0
    from ..config import duration_seconds

    return duration_seconds(raw)


def _condition_met(item: dict[str, Any], condition: str) -> bool:
    if condition.startswith("condition="):
        raw = condition[len("condition=") :]
        expected = "true"
        if "=" in raw:
            name, expected = raw.split("=", 1)
        else:
            name = raw
        for value in (item.get("status") or {}).get("conditions") or []:
            if str(value.get("type", "")).casefold() == name.casefold():
                return str(value.get("status", "")).casefold() == expected.casefold()
        return False
    if condition.startswith("jsonpath="):
        # Custom resources such as FlinkDeployment report readiness in their own
        # status fields and never populate status.conditions, so condition= alone
        # cannot express "wait until this job is RUNNING".
        expression, expected = jsonpath.split_condition(condition[len("jsonpath=") :])
        value = jsonpath.resolve(item, expression)
        if expected is None:
            return value not in (None, "", [], {}, False)
        return str(value) == expected
    raise UsageError(
        "--for must be create, delete, condition=NAME[=VALUE], "
        "or jsonpath={.path.to.field}[=VALUE]"
    )


def _observed(item: dict[str, Any], condition: str) -> str:
    """Describe what the resource currently reports for ``condition``.

    A wait that prints nothing is indistinguishable from a hung command, and a
    timeout that names only the resource does not say why it never arrived. Both
    need the value that was actually read.
    """
    if condition.startswith("jsonpath="):
        expression, _ = jsonpath.split_condition(condition[len("jsonpath=") :])
        body = expression.strip().strip("{}").lstrip(".")
        return f"{body}={jsonpath.display(jsonpath.resolve(item, expression)) or '<none>'}"
    if condition.startswith("condition="):
        name = condition[len("condition=") :].split("=", 1)[0]
        for value in (item.get("status") or {}).get("conditions") or []:
            if str(value.get("type", "")).casefold() == name.casefold():
                reason = value.get("reason") or ""
                seen = f"{name}={value.get('status')}"
                return f"{seen} ({reason})" if reason else seen
        return f"{name}=<absent>"
    return "exists"


def _fail_on_conditions(raw: list[str] | None) -> list[str]:
    """Resolve ``--fail-on`` into the conditions that abort a wait.

    Waiting for success without watching for failure is what turns a job that
    died in ten seconds into a ten-minute silence, so a default is on: no
    standard resource carries ``status.error``, and a custom resource that does
    is reporting a failure by definition. ``--fail-on=none`` opts out.
    """
    if raw is None:
        return [FAIL_ON_STATUS_ERROR]
    conditions = [value for value in raw if value != "none"]
    if any(value == "none" for value in raw) and conditions:
        raise UsageError("--fail-on=none cannot be combined with another --fail-on condition")
    return conditions


def _failure_detail(item: dict[str, Any]) -> str:
    """The failure text a resource carries, for the message that ends the wait."""
    error = (item.get("status") or {}).get("error")
    if isinstance(error, (dict, list)):
        error = jsonpath.display(error)
    text = " ".join(str(error or "").split())
    return text if len(text) <= 400 else text[:399] + "…"


def _cmd_wait(ctx: Context, ns: argparse.Namespace) -> int:
    namespace = _scope(ctx, ns)
    resource_name, embedded = _split_resource(ns.resource)
    names = embedded + ns.names
    if not names:
        raise UsageError("wait requires at least one named resource")
    fail_on = _fail_on_conditions(ns.fail_on)
    timeout = _parse_wait_timeout(ns.timeout)
    deadline = None if timeout == 0 else time.monotonic() + timeout
    started = time.monotonic()
    pending = set(names)
    reported: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    while pending:
        for name in list(pending):
            item: dict[str, Any] = {}
            try:
                data = ctx.client.get(resource_name, [name], namespace)
                item = plain(data)["items"][0]
                met = ns.condition == "create" or _condition_met(item, ns.condition)
            except Exception as exc:
                status = getattr(exc, "status", None)
                if ns.condition == "delete" and status == 404:
                    met = True
                elif ns.condition == "create" and status == 404:
                    met = False
                else:
                    raise
            if item:
                last_seen[name] = _observed(item, ns.condition)
                # Success in the same observation wins: a resource that already
                # satisfies --for has arrived, whatever else its status carries.
                for failure in [] if met else fail_on:
                    if not _condition_met(item, failure):
                        continue
                    detail = _failure_detail(item)
                    message = (
                        f"{resource_name}/{name} failed while waiting: {last_seen[name]}"
                        f" matched --fail-on={failure}"
                    )
                    raise ApiError(f"{message}: {detail}" if detail else message)
                if not ns.quiet and reported.get(name) != last_seen[name]:
                    reported[name] = last_seen[name]
                    elapsed = int(time.monotonic() - started)
                    print(
                        f"waiting for {resource_name}/{name}: {last_seen[name]} ({elapsed}s)",
                        file=sys.stderr,
                    )
            if met:
                print(f"{resource_name}/{name} condition met")
                pending.remove(name)
        if pending:
            if deadline is not None and time.monotonic() >= deadline:
                observed = ", ".join(
                    f"{name} ({last_seen.get(name, 'never observed')})" for name in sorted(pending)
                )
                raise ApiError(f"timed out after {ns.timeout} waiting for: {observed}")
            time.sleep(1)
    return 0


def _rollout_complete(item: dict[str, Any]) -> bool:
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    desired = int(spec.get("replicas") or 0)
    generation = int((item.get("metadata") or {}).get("generation") or 0)
    observed = int(status.get("observedGeneration") or 0)
    updated = int(status.get("updatedReplicas") or status.get("updatedNumberScheduled") or 0)
    available = int(status.get("availableReplicas") or status.get("numberAvailable") or 0)
    return observed >= generation and updated >= desired and available >= desired


def _cmd_rollout_status(ctx: Context, ns: argparse.Namespace) -> int:
    namespace = _scope(ctx, ns)
    resource, names = _split_resource(ns.resource)
    if len(names) != 1:
        raise UsageError("rollout status requires TYPE/NAME")
    timeout = _parse_wait_timeout(ns.timeout)
    deadline = None if timeout == 0 else time.monotonic() + timeout
    while True:
        item = plain(ctx.client.get(resource, names, namespace))["items"][0]
        if _rollout_complete(item):
            print(f"{resource}/{names[0]} successfully rolled out")
            return 0
        if not ns.watch:
            print(f"{resource}/{names[0]} rollout is in progress")
            return 1
        if deadline is not None and time.monotonic() >= deadline:
            raise ApiError(f"timed out waiting for rollout {resource}/{names[0]}")
        time.sleep(1)


def _cmd_rollout_restart(ctx: Context, ns: argparse.Namespace) -> int:
    namespace = _scope(ctx, ns)
    resource_name, names = _split_resource(ns.resource)
    resource = ctx.client.resource(resource_name)
    if str(getattr(resource, "kind", "")).lower() not in {
        "deployment",
        "statefulset",
        "daemonset",
    }:
        raise UsageError("rollout restart supports deployment, statefulset, and daemonset")
    if not names:
        listed = plain(resource.get(namespace=namespace, label_selector=ns.selector))
        names = [item["metadata"]["name"] for item in listed.get("items", [])]
    stamp = datetime.now(timezone.utc).isoformat()
    results = []
    for name in names:
        body = {"spec": {"template": {"metadata": {"annotations": {
            "kubectl.kubernetes.io/restartedAt": stamp
        }}}}}
        results.append(
            plain(resource.patch(
                name=name,
                namespace=namespace,
                body=body,
                content_type="application/strategic-merge-patch+json",
                field_manager=ctx.settings.field_manager,
            ))
        )
    print_rendered({"apiVersion": "v1", "kind": "List", "items": results}, ns.output)
    return 0


def _cmd_create(ctx: Context, ns: argparse.Namespace) -> int:
    _scope(ctx, ns)
    results = []
    for document in _documents(ns.filename):
        resource, namespace, _name = ctx.client.manifest_resource(document)
        kwargs = {"body": document, "namespace": namespace}
        if ns.dry_run == "server":
            kwargs["dry_run"] = "All"
        results.append(plain(resource.create(**kwargs)))
    print_rendered({"apiVersion": "v1", "kind": "List", "items": results}, ns.output)
    return 0


def _cmd_apply(ctx: Context, ns: argparse.Namespace) -> int:
    _scope(ctx, ns)
    results = []
    for document in _documents(ns.filename):
        resource, namespace, name = ctx.client.manifest_resource(document)
        kwargs = {
            "name": name,
            "namespace": namespace,
            "body": document,
            "content_type": "application/apply-patch+yaml",
            "field_manager": ns.field_manager or ctx.settings.field_manager,
            "force_conflicts": ns.force_conflicts,
        }
        if ns.dry_run == "server":
            kwargs["dry_run"] = "All"
        results.append(plain(resource.patch(**kwargs)))
    print_rendered({"apiVersion": "v1", "kind": "List", "items": results}, ns.output)
    return 0


def _delete_options(ctx: Context, ns: argparse.Namespace) -> Any:
    propagation = ns.cascade.capitalize() if ns.cascade != "orphan" else "Orphan"
    return ctx.client.typed.V1DeleteOptions(
        grace_period_seconds=0 if ns.force else ns.grace_period,
        propagation_policy=propagation,
    )


def _cmd_delete(ctx: Context, ns: argparse.Namespace) -> int:
    namespace = _scope(ctx, ns)
    targets: list[tuple[Any, str, str]] = []
    if ns.filename:
        for document in _documents(ns.filename):
            resource, doc_namespace, name = ctx.client.manifest_resource(document)
            targets.append((resource, doc_namespace, name))
    else:
        if not ns.resource:
            raise UsageError("delete requires TYPE [NAME...] or -f FILE")
        resource_name, embedded = _split_resource(ns.resource)
        resource = ctx.client.resource(resource_name)
        names = embedded + ns.names
        if ns.all or ns.selector:
            listed = plain(resource.get(namespace=namespace, label_selector=ns.selector))
            names.extend(item["metadata"]["name"] for item in listed.get("items", []))
        if not names:
            raise UsageError("delete requires names, --all, -l, or -f")
        targets.extend((resource, namespace, name) for name in dict.fromkeys(names))
    items = []
    options = _delete_options(ctx, ns)
    for resource, target_namespace, name in targets:
        resource.delete(name=name, namespace=target_namespace, body=options)
        items.append({"apiVersion": getattr(resource, "group_version", ""), "kind": getattr(resource, "kind", ""), "metadata": {"name": name, "namespace": target_namespace}})
        if ns.wait:
            timeout = _parse_wait_timeout(ns.timeout)
            deadline = None if timeout == 0 else time.monotonic() + timeout
            while True:
                try:
                    resource.get(name=name, namespace=target_namespace)
                except Exception as exc:
                    if getattr(exc, "status", None) == 404:
                        break
                    raise
                if deadline is not None and time.monotonic() >= deadline:
                    raise ApiError(f"timed out waiting for deletion of {name}")
                time.sleep(1)
    print_rendered({"apiVersion": "v1", "kind": "List", "items": items}, ns.output)
    return 0


def _cmd_patch(ctx: Context, ns: argparse.Namespace) -> int:
    namespace = _scope(ctx, ns)
    resource = ctx.client.resource(ns.resource)
    raw = ns.patch if ns.patch is not None else Path(ns.patch_file).read_text(encoding="utf-8")
    try:
        body = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise UsageError(f"invalid patch: {exc}") from exc
    content_types = {
        "json": "application/json-patch+json",
        "merge": "application/merge-patch+json",
        "strategic": "application/strategic-merge-patch+json",
    }
    result = resource.patch(
        name=ns.name,
        namespace=namespace,
        body=body,
        content_type=content_types[ns.type],
        field_manager=ctx.settings.field_manager,
    )
    print_rendered(result, ns.output)
    return 0


def _cmd_scale(ctx: Context, ns: argparse.Namespace) -> int:
    namespace = _scope(ctx, ns)
    if ns.replicas < 0:
        raise UsageError("--replicas must be >= 0")
    results = []
    for value in ns.resources:
        resource_name, names = _split_resource(value)
        if len(names) != 1:
            raise UsageError("scale targets must use TYPE/NAME")
        resource = ctx.client.resource(resource_name)
        if str(getattr(resource, "kind", "")).lower() not in {
            "deployment",
            "statefulset",
            "replicaset",
            "replicationcontroller",
        }:
            raise UsageError(
                "scale supports deployment, statefulset, replicaset, and replicationcontroller"
            )
        current = plain(resource.get(name=names[0], namespace=namespace))
        spec = current.get("spec") or {}
        meta = current.get("metadata") or {}
        if ns.current_replicas is not None and int(spec.get("replicas") or 0) != ns.current_replicas:
            raise ApiError(f"{value} current replicas do not match {ns.current_replicas}")
        if ns.resource_version and str(meta.get("resourceVersion")) != ns.resource_version:
            raise ApiError(f"{value} resourceVersion does not match {ns.resource_version}")
        body: dict[str, Any] = {"spec": {"replicas": ns.replicas}}
        if ns.resource_version:
            body["metadata"] = {"resourceVersion": ns.resource_version}
        results.append(plain(resource.patch(
            name=names[0],
            namespace=namespace,
            body=body,
            content_type="application/merge-patch+json",
        )))
    print_rendered({"apiVersion": "v1", "kind": "List", "items": results}, ns.output)
    return 0


def _metadata_patch(ctx: Context, ns: argparse.Namespace, field: str) -> int:
    namespace = _scope(ctx, ns)
    resource = ctx.client.resource(ns.resource)
    current = plain(resource.get(name=ns.name, namespace=namespace))
    values = dict((current.get("metadata") or {}).get(field) or {})
    patch_values: dict[str, Any] = {}
    for assignment in ns.assignments:
        if assignment.endswith("-") and "=" not in assignment:
            patch_values[assignment[:-1]] = None
            continue
        if "=" not in assignment:
            raise UsageError(f"invalid {field[:-1]} assignment: {assignment!r}")
        key, value = assignment.split("=", 1)
        if key in values and not ns.overwrite and values[key] != value:
            raise UsageError(f"{field[:-1]} {key!r} already exists; pass --overwrite")
        patch_values[key] = value
    metadata: dict[str, Any] = {field: patch_values}
    if ns.resource_version:
        metadata["resourceVersion"] = ns.resource_version
    result = resource.patch(
        name=ns.name,
        namespace=namespace,
        body={"metadata": metadata},
        content_type="application/merge-patch+json",
    )
    print_rendered(result, ns.output)
    return 0


def _cmd_label(ctx: Context, ns: argparse.Namespace) -> int:
    return _metadata_patch(ctx, ns, "labels")


def _cmd_annotate(ctx: Context, ns: argparse.Namespace) -> int:
    return _metadata_patch(ctx, ns, "annotations")


def main(argv: list[str], profile: dict[str, Any]) -> int:
    parser = build_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) else EXIT_USAGE
    except PluginError as exc:
        # An argument validator (such as -o) rejected a value; report it the way
        # every other usage error is reported rather than as a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    try:
        settings = Settings.from_profile(profile)
        return int(ns.func(Context(settings), ns) or 0)
    except PluginError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        error = format_api_exception(exc)
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code

"""DAG deployment: copy DAG files to the dags folder behind Airflow.

Two target kinds are supported, selected by the destination URI:

* ``s3://bucket/prefix/`` — uploaded with boto3 (optional dependency,
  ``pip install 'datus-airflow-plugin[s3]'``). Covers MWAA-style setups and
  any deployment that syncs its dags folder from S3.
* any other path — treated as a local/mounted dags folder and copied to.

``verify_dags`` polls the REST API after an upload until the scheduler has
re-parsed the deployed DAGs. Freshness is detected by *change* against the
state captured before the upload (never by comparing server timestamps to
the local clock, which would break on clock skew).
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from .client import AirflowClient
from .config import Settings
from .errors import ApiError, MissingDependencyError, PluginError, UsageError

DEFAULT_PATTERNS = (".py", ".zip")
Log = Callable[[str], None]


@dataclass(frozen=True)
class DeployItem:
    source: Path
    rel: str  # posix-style path relative to the destination root


def collect_files(sources: Iterable[str], all_files: bool = False) -> List[DeployItem]:
    items: Dict[str, Path] = {}

    def add(path: Path, rel: str) -> None:
        existing = items.get(rel)
        if existing is not None and existing.resolve() != path.resolve():
            raise UsageError(
                f"conflicting sources for destination path {rel!r}: {existing} and {path}"
            )
        items[rel] = path

    for raw in sources:
        src = Path(raw).expanduser()
        if src.is_file():
            add(src, src.name)
        elif src.is_dir():
            for child in sorted(src.rglob("*")):
                if not child.is_file():
                    continue
                rel_parts = child.relative_to(src).parts
                if any(p.startswith(".") or p == "__pycache__" for p in rel_parts):
                    continue
                if child.suffix == ".pyc":
                    continue
                if not all_files and child.suffix not in DEFAULT_PATTERNS:
                    continue
                add(child, str(PurePosixPath(*rel_parts)))
        else:
            raise UsageError(f"source not found: {raw}")

    return [DeployItem(source=items[rel], rel=rel) for rel in sorted(items)]


# ---------------------------------------------------------------- targets


def parse_s3_uri(uri: str) -> Tuple[str, str]:
    rest = uri[len("s3://"):]
    bucket, _, prefix = rest.partition("/")
    if not bucket:
        raise UsageError(f"invalid S3 URI (no bucket): {uri!r}")
    prefix = prefix.lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix


class S3Target:
    def __init__(self, uri: str, settings: Settings, client: Any = None):
        self.bucket, self.prefix = parse_s3_uri(uri)
        self._client = client or self._build_client(settings)

    @staticmethod
    def _build_client(settings: Settings) -> Any:
        try:
            import boto3
        except ImportError as exc:
            raise MissingDependencyError(
                "boto3 is required for S3 deployment — install with "
                "`pip install 'datus-airflow-plugin[s3]'`"
            ) from exc
        s3 = settings.s3
        session_kwargs: Dict[str, Any] = {}
        if s3.profile:
            session_kwargs["profile_name"] = s3.profile
        if s3.region:
            session_kwargs["region_name"] = s3.region
        if s3.access_key_id:
            session_kwargs["aws_access_key_id"] = s3.access_key_id
            session_kwargs["aws_secret_access_key"] = s3.secret_access_key
            if s3.session_token:
                session_kwargs["aws_session_token"] = s3.session_token
        session = boto3.session.Session(**session_kwargs)

        if s3.role_arn:
            # base credentials (chain/profile/keys) only bootstrap the AssumeRole;
            # a deploy is short-lived, so the 1h temp credentials never need refresh
            assume_kwargs: Dict[str, Any] = {
                "RoleArn": s3.role_arn,
                "RoleSessionName": s3.role_session_name or "datus-airflow-plugin",
            }
            if s3.external_id:
                assume_kwargs["ExternalId"] = s3.external_id
            creds = session.client("sts").assume_role(**assume_kwargs)["Credentials"]
            session = boto3.session.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
                region_name=s3.region or session.region_name,
            )

        client_kwargs: Dict[str, Any] = {}
        if s3.endpoint_url:
            client_kwargs["endpoint_url"] = s3.endpoint_url
        return session.client("s3", **client_kwargs)

    def describe(self, rel: str) -> str:
        return f"s3://{self.bucket}/{self.prefix}{rel}"

    def upload(self, items: List[DeployItem], log: Log) -> None:
        for item in items:
            self._client.upload_file(str(item.source), self.bucket, self.prefix + item.rel)
            log(f"uploaded {item.source} -> {self.describe(item.rel)}")

    def list_keys(self) -> Set[str]:
        keys: Set[str] = set()
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/"):
                    continue
                keys.add(key[len(self.prefix):])
        return keys

    def delete(self, rels: List[str], log: Log) -> None:
        keys = [self.prefix + rel for rel in rels]
        for start in range(0, len(keys), 1000):
            chunk = keys[start : start + 1000]
            self._client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True},
            )
            for key in chunk:
                log(f"deleted s3://{self.bucket}/{key}")


class LocalTarget:
    def __init__(self, root: str):
        self.root = Path(root).expanduser()

    def describe(self, rel: str) -> str:
        return str(self.root / rel)

    def upload(self, items: List[DeployItem], log: Log) -> None:
        for item in items:
            dest = self.root / item.rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item.source, dest)
            log(f"copied {item.source} -> {dest}")

    def list_keys(self) -> Set[str]:
        if not self.root.is_dir():
            return set()
        return {
            str(PurePosixPath(*p.relative_to(self.root).parts))
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def delete(self, rels: List[str], log: Log) -> None:
        for rel in rels:
            path = self.root / rel
            path.unlink(missing_ok=True)
            log(f"deleted {path}")
            parent = path.parent
            while parent != self.root and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent


def make_target(dest: str, settings: Settings):
    if dest.startswith("s3://"):
        return S3Target(dest, settings)
    return LocalTarget(dest)


# ----------------------------------------------------------------- verify


def _dag_parse_marker(client: AirflowClient, dag_id: str) -> Optional[str]:
    """Current last_parsed_time for a DAG, or None if the DAG is unknown yet."""
    try:
        dag = client.request("GET", f"/dags/{dag_id}")
    except ApiError as exc:
        if exc.status_code == 404:
            return None
        raise
    return (dag or {}).get("last_parsed_time") or ""


def capture_parse_state(client: AirflowClient, dag_ids: List[str]) -> Dict[str, Optional[str]]:
    return {dag_id: _dag_parse_marker(client, dag_id) for dag_id in dag_ids}


def _matching_import_errors(
    client: AirflowClient, deployed_rels: List[str]
) -> List[Dict[str, Any]]:
    names = {PurePosixPath(rel).name for rel in deployed_rels}
    errors = client.paginate("/importErrors", "import_errors")
    return [
        err
        for err in errors
        if PurePosixPath(str(err.get("filename", ""))).name in names
    ]


def verify_dags(
    client: AirflowClient,
    dag_ids: List[str],
    pre_state: Dict[str, Optional[str]],
    deployed_rels: List[str],
    pre_error_state: Dict[int, str],
    timeout: float,
    interval: float,
    log: Log,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Wait until every DAG in dag_ids has been (re-)parsed after the deploy.

    Fails fast when a new or changed import error appears for one of the
    deployed files. Raises PluginError on failure or timeout.
    """
    deadline = time.monotonic() + timeout
    pending = list(dag_ids)
    while True:
        for err in _matching_import_errors(client, deployed_rels):
            err_id = err.get("import_error_id")
            marker = str(err.get("timestamp", ""))
            if pre_error_state.get(err_id) == marker:
                continue  # pre-existing, unchanged error — not caused by this deploy
            raise PluginError(
                f"import error in {err.get('filename')}:\n{err.get('stack_trace', '').rstrip()}"
            )

        still_pending = []
        for dag_id in pending:
            marker = _dag_parse_marker(client, dag_id)
            if marker is None or marker == pre_state.get(dag_id):
                still_pending.append(dag_id)
            else:
                log(f"dag {dag_id} parsed (last_parsed_time={marker})")
        pending = still_pending
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise PluginError(
                f"timed out after {timeout:.0f}s waiting for DAG(s) to be parsed: "
                f"{', '.join(pending)} — possible causes: the dags folder syncs with a "
                "delay, the dag_id does not match the deployed file, or Airflow's "
                "DAG_DISCOVERY_SAFE_MODE skipped the file (it only parses files "
                "containing both the strings 'airflow' and 'dag')"
            )
        sleep(interval)


def capture_import_error_state(client: AirflowClient) -> Dict[int, str]:
    try:
        errors = client.paginate("/importErrors", "import_errors")
    except ApiError:
        return {}
    return {
        err.get("import_error_id"): str(err.get("timestamp", ""))
        for err in errors
        if err.get("import_error_id") is not None
    }

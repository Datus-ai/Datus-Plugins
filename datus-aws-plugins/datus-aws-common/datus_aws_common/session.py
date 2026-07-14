"""Build boto3 sessions and clients from :class:`AwsSettings`.

Credential resolution mirrors datus-airflow-plugin's ``S3Target._build_client``:
a base session from the profile/keys (or the standard boto3 chain when none are
given), optionally bootstrapping a short-lived STS AssumeRole. ``endpoint_url``
and botocore retry/timeout config are applied when the client is built.
"""

from __future__ import annotations

from typing import Any, Optional

from .awsconfig import AwsSettings
from .errors import MissingDependencyError


def _import_boto3():
    try:
        import boto3

        return boto3
    except ImportError as exc:  # pragma: no cover - boto3 is a hard dependency
        raise MissingDependencyError(
            "boto3 is required for this plugin — install the plugin package to pull it in"
        ) from exc


def build_session(settings: AwsSettings) -> Any:
    """A boto3 Session honouring profile/keys/region, with optional AssumeRole."""
    boto3 = _import_boto3()
    session_kwargs = {}
    if settings.profile:
        session_kwargs["profile_name"] = settings.profile
    if settings.region:
        session_kwargs["region_name"] = settings.region
    if settings.access_key_id:
        session_kwargs["aws_access_key_id"] = settings.access_key_id
        session_kwargs["aws_secret_access_key"] = settings.secret_access_key
        if settings.session_token:
            session_kwargs["aws_session_token"] = settings.session_token
    session = boto3.session.Session(**session_kwargs)

    if settings.role_arn:
        # base credentials (chain/profile/keys) only bootstrap the AssumeRole;
        # CLI invocations are short-lived, so the temp credentials never refresh
        assume_kwargs = {
            "RoleArn": settings.role_arn,
            "RoleSessionName": settings.role_session_name or "datus-aws-plugin",
        }
        if settings.external_id:
            assume_kwargs["ExternalId"] = settings.external_id
        creds = session.client("sts").assume_role(**assume_kwargs)["Credentials"]
        session = boto3.session.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=settings.region or session.region_name,
        )

    return session


def build_client(settings: AwsSettings, service: str, session: Optional[Any] = None, **client_kwargs) -> Any:
    """A boto3 client for ``service`` with retry/timeout config and endpoint override."""
    _import_boto3()
    from botocore.config import Config

    session = session or build_session(settings)
    if "config" not in client_kwargs:
        client_kwargs["config"] = Config(
            retries={"max_attempts": settings.max_attempts, "mode": "standard"},
            connect_timeout=settings.timeout,
            read_timeout=settings.timeout,
        )
    if settings.endpoint_url and "endpoint_url" not in client_kwargs:
        client_kwargs["endpoint_url"] = settings.endpoint_url
    return session.client(service, **client_kwargs)

"""Small, deployment-configured access controls for the Incident surface.

The incident service deliberately keeps operator access separate from integration
credentials.  Operator credentials are only used for the browser and ordinary
API surface; Alertmanager, provider ingestion, and catalog administration keep
their route-specific credentials in ``app.py``.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from dataclasses import dataclass

from fastapi import Request


def _enabled(value: str | None) -> bool:
    if value is None or not value.strip():
        return False
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("APM_INCIDENT_OPERATOR_AUTH_ENABLED must be a boolean")


def _constant_time_equal(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


@dataclass(frozen=True)
class SurfaceAuth:
    """Environment-only settings kept outside ``IncidentSettings`` for now."""

    operator_auth_enabled: bool
    operator_user: str
    operator_password: str
    metrics_token: str

    @classmethod
    def from_environment(cls) -> "SurfaceAuth":
        auth = cls(
            operator_auth_enabled=_enabled(
                os.environ.get("APM_INCIDENT_OPERATOR_AUTH_ENABLED")
            ),
            operator_user=os.environ.get("DEMO_AUTH_USER", ""),
            operator_password=os.environ.get("DEMO_AUTH_PASSWORD", ""),
            metrics_token=os.environ.get("APM_INCIDENT_METRICS_TOKEN", ""),
        )
        if auth.operator_auth_enabled:
            if not auth.operator_user.strip() or ":" in auth.operator_user:
                raise ValueError(
                    "DEMO_AUTH_USER must be a non-empty HTTP Basic username"
                )
            if len(auth.operator_password) < 12:
                raise ValueError(
                    "DEMO_AUTH_PASSWORD must contain at least 12 characters"
                )
        if auth.metrics_token and len(auth.metrics_token) < 20:
            raise ValueError(
                "APM_INCIDENT_METRICS_TOKEN must contain at least 20 characters"
            )
        return auth

    def operator_credentials_match(self, authorization: str) -> bool:
        """Check HTTP Basic credentials without exposing which value was wrong."""
        scheme, _, encoded = authorization.partition(" ")
        if scheme.casefold() != "basic" or not encoded:
            return False
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (UnicodeDecodeError, binascii.Error):
            return False
        username, separator, password = decoded.partition(":")
        if not separator:
            return False
        username_matches = _constant_time_equal(username, self.operator_user)
        password_matches = _constant_time_equal(password, self.operator_password)
        return username_matches & password_matches

    def metrics_token_matches(self, authorization: str) -> bool:
        scheme, _, candidate = authorization.partition(" ")
        return (
            bool(self.metrics_token)
            and scheme.casefold() == "bearer"
            and _constant_time_equal(candidate, self.metrics_token)
        )


def is_integration_or_catalog_path(path: str) -> bool:
    return path in {
        "/api/v1/integrations/alertmanager",
        "/api/v1/provider-events",
    } or path.startswith("/api/v1/catalog")


def is_operator_authorized(request: Request, auth: SurfaceAuth) -> bool:
    return auth.operator_credentials_match(request.headers.get("authorization", ""))

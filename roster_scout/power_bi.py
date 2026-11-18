"""Fail-closed Power BI Embedded provider boundary.

The browser supplies no Power BI authority. Workspace, report, semantic-model,
role, effective identity, and embed origin are fixed by validated server
configuration; callers supply only canonical account/organization/snapshot
UUIDs already authorized by the application.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
import uuid
from urllib.parse import urlsplit

import requests


_LOGIN_HOST = "login.microsoftonline.com"
_API_HOST = "api.powerbi.com"
_EMBED_HOSTS = frozenset({"app.powerbi.com"})
_ROLE = "RosterScoutSnapshotReader"
_HTTP_TIMEOUT_SECONDS = (3.05, 12)
_MAX_TOKEN_SECONDS = 60 * 60
_REFRESH_TERMINAL = frozenset({"Completed", "Failed", "Cancelled", "Disabled"})


class PowerBIFailure(RuntimeError):
    """One fixed, sanitized provider failure category."""

    def __init__(self, category: str):
        if category not in {
            "disabled",
            "configuration",
            "capacity_unverified",
            "provider_unavailable",
            "provider_response",
        }:
            category = "provider_unavailable"
        self.category = category
        super().__init__(category)


def snapshot_security_key(organization_id, snapshot_id) -> str:
    """Return the exact opaque RLS key shared by SQL views and embed tokens."""
    organization = uuid.UUID(str(organization_id))
    snapshot = uuid.UUID(str(snapshot_id))
    return f"{organization}|{snapshot}"


def _uuid(value: str | None, *, field: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise PowerBIFailure("configuration") from None
    if str(parsed) != str(value).lower():
        raise PowerBIFailure("configuration")
    return str(parsed)


def _embed_url(value: str | None) -> str:
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        raise PowerBIFailure("configuration") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _EMBED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path.startswith("/reportEmbed")
    ):
        raise PowerBIFailure("configuration")
    return parsed.geturl()


@dataclass(frozen=True)
class PowerBISettings:
    mode: str = "disabled"
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    workspace_id: str | None = None
    report_id: str | None = None
    semantic_model_id: str | None = None
    embed_url: str | None = None
    role_name: str = _ROLE
    capacity_verified: bool = False

    @classmethod
    def from_environ(cls, environ=None):
        values = os.environ if environ is None else environ
        mode = str(values.get("POWER_BI_MODE", "disabled")).strip().lower()
        if mode not in {"disabled", "fake", "live"}:
            raise PowerBIFailure("configuration")
        if mode == "disabled":
            return cls()
        if mode == "fake":
            if values.get("FLY_APP_NAME") or values.get("APP_ENV") == "production":
                raise PowerBIFailure("configuration")
            return cls(mode="fake")
        role = str(values.get("POWER_BI_RLS_ROLE", _ROLE)).strip()
        if role != _ROLE:
            raise PowerBIFailure("configuration")
        secret = str(values.get("POWER_BI_CLIENT_SECRET", ""))
        if not secret or len(secret) > 4096:
            raise PowerBIFailure("configuration")
        return cls(
            mode="live",
            tenant_id=_uuid(values.get("POWER_BI_TENANT_ID"), field="tenant_id"),
            client_id=_uuid(values.get("POWER_BI_CLIENT_ID"), field="client_id"),
            client_secret=secret,
            workspace_id=_uuid(values.get("POWER_BI_WORKSPACE_ID"), field="workspace_id"),
            report_id=_uuid(values.get("POWER_BI_REPORT_ID"), field="report_id"),
            semantic_model_id=_uuid(
                values.get("POWER_BI_SEMANTIC_MODEL_ID"), field="semantic_model_id"
            ),
            embed_url=_embed_url(values.get("POWER_BI_EMBED_URL")),
            role_name=role,
            capacity_verified=str(values.get("POWER_BI_CAPACITY_VERIFIED", "")).lower()
            == "true",
        )

    @property
    def configured(self) -> bool:
        return self.mode in {"fake", "live"}


@dataclass(frozen=True)
class EmbedConfiguration:
    report_id: str
    embed_url: str
    access_token: str
    expires_at: datetime
    synthetic: bool = False

    def as_dict(self):
        return {
            "reportId": self.report_id,
            "embedUrl": self.embed_url,
            "accessToken": self.access_token,
            "expiresAt": self.expires_at.astimezone(UTC).isoformat(),
            "synthetic": self.synthetic,
        }


@dataclass(frozen=True)
class RefreshConfiguration:
    correlation_id: str
    status: str
    provider_status: str


def _correlation_id(value) -> str:
    candidate = str(value or "").strip()
    if (
        not candidate
        or len(candidate) > 120
        or not candidate[0].isalnum()
        or any(not (character.isalnum() or character in "._:-") for character in candidate)
    ):
        raise PowerBIFailure("provider_response")
    return candidate


class PowerBIClient:
    """Minimal app-owns-data client with injected HTTP for deterministic tests."""

    def __init__(self, settings: PowerBISettings, *, http=None, now=None):
        self.settings = settings
        self._http = http or requests.Session()
        self._now = now or (lambda: datetime.now(UTC))

    @classmethod
    def from_environ(cls, environ=None, **kwargs):
        return cls(PowerBISettings.from_environ(environ), **kwargs)

    @property
    def configured(self):
        return self.settings.configured

    def _application_token(self) -> str:
        settings = self.settings
        url = (
            f"https://{_LOGIN_HOST}/{settings.tenant_id}"
            "/oauth2/v2.0/token"
        )
        try:
            response = self._http.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.client_id,
                    "client_secret": settings.client_secret,
                    "scope": "https://analysis.windows.net/powerbi/api/.default",
                },
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
            token = payload.get("access_token")
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            raise PowerBIFailure("provider_unavailable") from None
        if not isinstance(token, str) or not token or len(token) > 16384:
            raise PowerBIFailure("provider_response")
        return token

    def generate_embed_configuration(
        self,
        *,
        account_id,
        organization_id,
        snapshot_id,
    ) -> EmbedConfiguration:
        settings = self.settings
        if settings.mode == "disabled":
            raise PowerBIFailure("disabled")
        if settings.mode == "fake":
            expires = self._now() + timedelta(minutes=30)
            return EmbedConfiguration(
                report_id="00000000-0000-0000-0000-000000000005",
                embed_url="https://app.powerbi.com/reportEmbed?reportId=00000000-0000-0000-0000-000000000005",
                access_token="development-synthetic-token",
                expires_at=expires,
                synthetic=True,
            )
        if not settings.capacity_verified:
            raise PowerBIFailure("capacity_unverified")

        account = str(uuid.UUID(str(account_id)))
        security_key = snapshot_security_key(organization_id, snapshot_id)
        application_token = self._application_token()
        request_body = {
            "reports": [{"id": settings.report_id, "allowEdit": False}],
            "datasets": [{"id": settings.semantic_model_id}],
            "targetWorkspaces": [{"id": settings.workspace_id}],
            "identities": [{
                "username": account,
                "roles": [settings.role_name],
                "datasets": [settings.semantic_model_id],
                "customData": security_key,
            }],
            "lifetimeInMinutes": 60,
        }
        try:
            response = self._http.post(
                f"https://{_API_HOST}/v1.0/myorg/GenerateToken",
                json=request_body,
                headers={
                    "Authorization": f"Bearer {application_token}",
                    "Content-Type": "application/json",
                },
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            raise PowerBIFailure("provider_unavailable") from None
        token = payload.get("token")
        expiration = payload.get("expiration")
        try:
            expires_at = datetime.fromisoformat(str(expiration).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise PowerBIFailure("provider_response") from None
        now = self._now()
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 16384
            or expires_at.tzinfo is None
            or expires_at <= now
            or expires_at > now + timedelta(seconds=_MAX_TOKEN_SECONDS + 60)
        ):
            raise PowerBIFailure("provider_response")
        return EmbedConfiguration(
            report_id=settings.report_id,
            embed_url=settings.embed_url,
            access_token=token,
            expires_at=expires_at.astimezone(UTC),
        )

    def start_refresh(self) -> RefreshConfiguration:
        """Start one fixed semantic-model refresh; no browser authority is accepted."""
        settings = self.settings
        if settings.mode == "disabled":
            raise PowerBIFailure("disabled")
        if settings.mode == "fake":
            return RefreshConfiguration(
                correlation_id="development-synthetic-refresh",
                status="completed",
                provider_status="Completed",
            )
        if not settings.capacity_verified:
            raise PowerBIFailure("capacity_unverified")
        application_token = self._application_token()
        try:
            response = self._http.post(
                "https://api.powerbi.com/v1.0/myorg/groups/"
                f"{settings.workspace_id}/datasets/{settings.semantic_model_id}/refreshes",
                json={"notifyOption": "NoNotification"},
                headers={
                    "Authorization": f"Bearer {application_token}",
                    "Content-Type": "application/json",
                },
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except (ValueError, TypeError, AttributeError):
                payload = {}
            headers = getattr(response, "headers", {}) or {}
            location = headers.get("Location") or headers.get("location") or ""
            value = (
                payload.get("requestId")
                or payload.get("id")
                or headers.get("RequestId")
                or headers.get("requestId")
                or headers.get("x-ms-request-id")
                or str(location).rstrip("/").rsplit("/", 1)[-1]
            )
            correlation = _correlation_id(value)
        except PowerBIFailure:
            raise
        except (requests.RequestException, TypeError, AttributeError):
            raise PowerBIFailure("provider_unavailable") from None
        return RefreshConfiguration(
            correlation_id=correlation,
            status="pending",
            provider_status="Unknown",
        )

    def refresh_status(self, correlation_id: str) -> RefreshConfiguration:
        """Poll the fixed semantic-model refresh using a validated provider id."""
        settings = self.settings
        correlation = _correlation_id(correlation_id)
        if settings.mode == "disabled":
            raise PowerBIFailure("disabled")
        if settings.mode == "fake":
            return RefreshConfiguration(correlation, "completed", "Completed")
        if not settings.capacity_verified:
            raise PowerBIFailure("capacity_unverified")
        application_token = self._application_token()
        try:
            response = self._http.get(
                "https://api.powerbi.com/v1.0/myorg/groups/"
                f"{settings.workspace_id}/datasets/{settings.semantic_model_id}"
                f"/refreshes/{correlation}",
                headers={"Authorization": f"Bearer {application_token}"},
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
            provider_status = payload.get("status")
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            raise PowerBIFailure("provider_unavailable") from None
        if provider_status not in {
            "Unknown", "NotStarted", "InProgress", "Completed",
            "Failed", "Cancelled", "Disabled",
        }:
            raise PowerBIFailure("provider_response")
        if provider_status == "Completed":
            status = "completed"
        elif provider_status in _REFRESH_TERMINAL:
            status = "failed"
        else:
            status = "pending"
        return RefreshConfiguration(correlation, status, provider_status)

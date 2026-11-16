"""Minimal Google Drive OAuth boundary for native Sheets workbook copies."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
from urllib.parse import urlencode, urlsplit

import requests


DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/drive/v3/files"
_SHEET_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")


class GoogleSheetsFailure(RuntimeError):
    """Fixed-category integration error that never carries provider payloads."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def _callback_uri(public_origin: str) -> str:
    origin = str(public_origin or "").strip().rstrip("/")
    parsed = urlsplit(origin)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme not in ({"http", "https"} if local else {"https"})
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise RuntimeError("Google OAuth requires one canonical public origin")
    return f"{origin}/integrations/google/callback"


@dataclass(frozen=True)
class GoogleSheetsOAuthClient:
    """Authorize one user and convert one XLSX artifact into a native Sheet."""

    client_id: str | None
    client_secret: str | None = field(repr=False)
    redirect_uri: str | None
    http: object = field(default=requests, repr=False, compare=False)

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    @classmethod
    def from_environ(cls, environ=None):
        environ = os.environ if environ is None else environ
        client_id = str(environ.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
        client_secret = str(environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
        if bool(client_id) != bool(client_secret):
            raise RuntimeError("Google OAuth client configuration is incomplete")
        if not client_id:
            return cls(None, None, None)
        if len(client_id) > 500 or len(client_secret) > 500:
            raise RuntimeError("Google OAuth client configuration is invalid")
        return cls(
            client_id,
            client_secret,
            _callback_uri(environ.get("ROSTER_SCOUT_PUBLIC_ORIGIN", "")),
        )

    def authorization_url(self, *, state: str) -> str:
        if not self.configured or not state or len(state) > 2000:
            raise GoogleSheetsFailure("configuration")
        return f"{_AUTHORIZATION_ENDPOINT}?{urlencode({
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': DRIVE_FILE_SCOPE,
            'include_granted_scopes': 'true',
            'access_type': 'online',
            'state': state,
        })}"

    def exchange_code(self, code: str) -> str:
        if not self.configured or not isinstance(code, str) or not 1 <= len(code) <= 4096:
            raise GoogleSheetsFailure("authorization")
        try:
            response = self.http.post(
                _TOKEN_ENDPOINT,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri,
                },
                timeout=15,
            )
            response.raise_for_status()
            if len(response.content) > 64 * 1024:
                raise ValueError("token response is too large")
            payload = response.json()
            access_token = payload.get("access_token")
            granted = set(str(payload.get("scope") or DRIVE_FILE_SCOPE).split())
            if (
                not isinstance(access_token, str)
                or not 1 <= len(access_token) <= 8192
                or DRIVE_FILE_SCOPE not in granted
            ):
                raise ValueError("required scope was not granted")
            return access_token
        except (requests.RequestException, ValueError, TypeError, json.JSONDecodeError):
            raise GoogleSheetsFailure("authorization") from None

    def import_workbook(
        self,
        *,
        access_token: str,
        workbook_name: str,
        payload: bytes,
    ) -> str:
        if (
            not self.configured
            or not isinstance(access_token, str)
            or not 1 <= len(access_token) <= 8192
            or not isinstance(payload, bytes)
            or not payload
            or len(payload) > 64 * 1024 * 1024
        ):
            raise GoogleSheetsFailure("upload")
        title = str(workbook_name or "Opponent Stats").removesuffix(".xlsx")[:200]
        try:
            response = self.http.post(
                _UPLOAD_ENDPOINT,
                params={"uploadType": "multipart", "fields": "id"},
                headers={"Authorization": f"Bearer {access_token}"},
                files={
                    "metadata": (
                        None,
                        json.dumps(
                            {
                                "name": title,
                                "mimeType": "application/vnd.google-apps.spreadsheet",
                            }
                        ),
                        "application/json; charset=UTF-8",
                    ),
                    "file": (
                        workbook_name,
                        payload,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                },
                timeout=30,
            )
            response.raise_for_status()
            if len(response.content) > 64 * 1024:
                raise ValueError("upload response is too large")
            sheet_id = response.json().get("id")
            if not isinstance(sheet_id, str) or not _SHEET_ID.fullmatch(sheet_id):
                raise ValueError("invalid sheet id")
            return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        except (requests.RequestException, ValueError, TypeError, json.JSONDecodeError):
            raise GoogleSheetsFailure("upload") from None

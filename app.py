#!/usr/bin/env python3
"""Axis Baseball Roster Scout web app.

Accept a team and division, collect the roster and season statistics, and
return a formatted Excel workbook containing Hitters and Pitchers worksheets.
"""

import base64
import binascii
import ipaddress
import io
import logging
import os
import re
import secrets
import uuid
from datetime import date
from functools import lru_cache
from hashlib import sha256
from urllib.parse import urlsplit

import idna
import requests
import xlsxwriter
from PIL import Image, ImageDraw, UnidentifiedImageError
from flask import Flask, g, jsonify, redirect, render_template, request, send_file
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.exceptions import RequestEntityTooLarge
from xlsxwriter.utility import xl_pixel_width
from usage_limits import BrowserCapacityError, check_request_limit

from roster_scout.auth_routes import register_auth_routes
from roster_scout.authorization import (
    AuthorizationFailure,
    list_active_memberships,
    require_context,
)
from roster_scout.database import create_session_factory, get_engine
from roster_scout.evidence import (
    FailureEvidenceConfigurationError,
    get_evidence_purge_readiness,
    get_failure_evidence_retention,
)
from roster_scout.usage import record_workbook_download_served
from roster_scout.identity import (
    FakeIdentityProvider,
    IdentityFailure,
    WorkOSIdentityProvider,
)
from roster_scout.organization_routes import register_organization_routes
from roster_scout.report_routes import register_report_routes
from roster_scout.sessions import SESSION_COOKIE_NAME, load_account_session
from roster_scraper import (
    HITTER_HEADERS,
    PITCHER_HEADERS,
    NcaaError,
    list_ncaa_teams,
    scrape_juco_by_name,
    scrape_ncaa_by_id,
    scrape_ncaa_by_name,
    split_roster,
)


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
app.config["WTF_CSRF_CHECK_DEFAULT"] = False
app.config["WTF_CSRF_TIME_LIMIT"] = 3600
_CSRF = CSRFProtect(app)


class _UnavailableIdentityProvider:
    """Fail-closed provider used when hosted identity config is incomplete."""

    @staticmethod
    def _raise():
        raise IdentityFailure("identity_unavailable")

    def authorization_url(self, *, state, invitation_token=None):
        self._raise()

    def exchange_code(self, *, code):
        self._raise()

    def refresh(self, *, sealed_session):
        self._raise()

    def logout_url(self, *, sealed_session):
        self._raise()


def _valid_identity_redirect_uri(value: str, *, hosted: bool) -> bool:
    """Accept only an exact callback URL, requiring HTTPS in hosted runtimes."""
    try:
        parsed = urlsplit(value)
        parsed.port
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.hostname
        and not parsed.username
        and not parsed.password
        and parsed.path == "/auth/callback"
        and not parsed.query
        and not parsed.fragment
        and (parsed.scheme == "https" if hosted else parsed.scheme in {"http", "https"})
    )


def _identity_configuration():
    """Return provider, CSRF signing key, and deterministic readiness status."""
    hosted = bool(os.environ.get("FLY_APP_NAME", "").strip())
    mode = os.environ.get(
        "ROSTER_SCOUT_IDENTITY_MODE",
        "workos" if hosted else "fake",
    ).strip().lower()
    session_secret = os.environ.get("ROSTER_SCOUT_SESSION_SECRET", "").strip()
    workos_values = {
        "api_key": os.environ.get("WORKOS_API_KEY", "").strip(),
        "client_id": os.environ.get("WORKOS_CLIENT_ID", "").strip(),
        "redirect_uri": os.environ.get("WORKOS_REDIRECT_URI", "").strip(),
        "cookie_password": os.environ.get("WORKOS_COOKIE_PASSWORD", "").strip(),
    }

    if mode == "fake" and not hosted:
        return FakeIdentityProvider(), session_secret or secrets.token_urlsafe(32), True
    if (
        mode == "workos"
        and session_secret
        and all(workos_values.values())
        and _valid_identity_redirect_uri(
            workos_values["redirect_uri"],
            hosted=hosted,
        )
    ):
        try:
            provider = WorkOSIdentityProvider(**workos_values)
        except Exception:
            return _UnavailableIdentityProvider(), secrets.token_urlsafe(32), False
        return provider, session_secret, True
    return _UnavailableIdentityProvider(), session_secret or secrets.token_urlsafe(32), False


(
    _IDENTITY_PROVIDER,
    _SESSION_SIGNING_KEY,
    _AUTH_CONFIGURATION_READY,
) = _identity_configuration()
_AUTH_VERIFICATION_STATUS = (
    "code_verified" if _AUTH_CONFIGURATION_READY else "configuration_required"
)
app.secret_key = _SESSION_SIGNING_KEY

_DOWNLOAD_ACK_PURPOSE = "roster-scout-workbook-download-v1"
_AUTHENTICATED_DOWNLOAD_ACK_PURPOSE = (
    "roster-scout-authenticated-snapshot-download-v1"
)
_DOWNLOAD_ACK_SALT = "roster-scout-download-ack-v1"
_DOWNLOAD_ACK_MAX_AGE_SECONDS = 15 * 60


def _decode_download_ack_secret(value: str) -> bytes:
    """Decode one URL-safe base64 secret without exposing it in errors."""
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("Download acknowledgement secret is invalid.")
    candidate = value.strip()
    if (
        candidate != value
        or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", candidate)
        or len(candidate) % 4 == 1
    ):
        raise RuntimeError("Download acknowledgement secret is invalid.")
    try:
        decoded = base64.b64decode(
            candidate + ("=" * (-len(candidate) % 4)),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(
            "Download acknowledgement secret is invalid."
        ) from exc
    if len(decoded) < 32:
        raise RuntimeError("Download acknowledgement secret is invalid.")
    return decoded


def _canonical_public_origin(value: str, *, require_https: bool) -> str:
    """Validate and canonicalize one configured browser origin."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RuntimeError("Roster Scout public origin is invalid.")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("Roster Scout public origin is invalid.") from exc

    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or (require_https and scheme != "https")
        or not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
        or any(character.isspace() or ord(character) < 32 for character in hostname)
    ):
        raise RuntimeError("Roster Scout public origin is invalid.")

    canonical_host = _canonical_public_hostname(hostname)
    default_port = (scheme == "https" and port == 443) or (
        scheme == "http" and port == 80
    )
    canonical_port = "" if port is None or default_port else f":{port}"
    return f"{scheme}://{canonical_host}{canonical_port}"


def _canonical_public_hostname(hostname: str) -> str:
    """Match the browser serialization used by the Origin header."""
    if "%" in hostname:
        raise RuntimeError("Roster Scout public origin is invalid.")

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None

    if isinstance(address, ipaddress.IPv6Address):
        return f"[{address.compressed}]"
    if isinstance(address, ipaddress.IPv4Address):
        canonical_ipv4 = str(address)
        if hostname != canonical_ipv4:
            raise RuntimeError("Roster Scout public origin is invalid.")
        return canonical_ipv4

    if ":" in hostname:
        raise RuntimeError("Roster Scout public origin is invalid.")

    try:
        canonical_dns = idna.encode(
            hostname,
            uts46=True,
            std3_rules=True,
        ).decode("ascii").lower()
    except idna.IDNAError:
        raise RuntimeError("Roster Scout public origin is invalid.") from None

    # A-labels must be canonical under the same rules used for Unicode input.
    # This rejects superficially well-formed Punycode that browsers reject.
    try:
        for label in canonical_dns.split("."):
            if label.startswith("xn--"):
                decoded = idna.decode(label, uts46=True, std3_rules=True)
                if (
                    idna.encode(
                        decoded,
                        uts46=True,
                        std3_rules=True,
                    ).decode("ascii").lower()
                    != label
                ):
                    raise RuntimeError(
                        "Roster Scout public origin is invalid."
                    )
    except idna.IDNAError:
        raise RuntimeError("Roster Scout public origin is invalid.") from None

    numeric_label = r"(?:[0-9]+|0[xX][0-9A-Fa-f]+)"
    if re.fullmatch(rf"{numeric_label}(?:\.{numeric_label})*", canonical_dns):
        raise RuntimeError("Roster Scout public origin is invalid.")

    labels = canonical_dns.split(".")
    if (
        len(canonical_dns) > 253
        or any(
            not label
            or len(label) > 63
            or not re.fullmatch(r"[a-z0-9-]+", label)
            or label.startswith("-")
            or label.endswith("-")
            for label in labels
        )
    ):
        raise RuntimeError("Roster Scout public origin is invalid.")
    return canonical_dns


def _configure_download_acknowledgements(
) -> tuple[URLSafeTimedSerializer, str | None]:
    """Validate runtime mode and create the process acknowledgement signer."""
    raw_concurrency = os.environ.get("WEB_CONCURRENCY", "1").strip()
    try:
        web_concurrency = int(raw_concurrency)
    except ValueError as exc:
        raise RuntimeError("WEB_CONCURRENCY must be a positive integer.") from exc
    if web_concurrency <= 0:
        raise RuntimeError("WEB_CONCURRENCY must be a positive integer.")

    hosted_production = bool(os.environ.get("FLY_APP_NAME", "").strip())
    shared_secret_required = hosted_production or web_concurrency > 1
    configured_origin = os.environ.get("ROSTER_SCOUT_PUBLIC_ORIGIN", "")
    if not configured_origin.strip():
        if shared_secret_required:
            raise RuntimeError(
                "A trusted Roster Scout public origin is required."
            )
        public_origin = None
    else:
        public_origin = _canonical_public_origin(
            configured_origin,
            require_https=hosted_production,
        )

    configured_secret = os.environ.get("ROSTER_SCOUT_DOWNLOAD_ACK_SECRET", "")
    if not configured_secret.strip():
        if shared_secret_required:
            raise RuntimeError(
                "A shared download acknowledgement secret is required."
            )
        configured_secret = secrets.token_urlsafe(32)

    secret_key = _decode_download_ack_secret(configured_secret)
    return (
        URLSafeTimedSerializer(secret_key, salt=_DOWNLOAD_ACK_SALT),
        public_origin,
    )


(
    _DOWNLOAD_ACK_SERIALIZER,
    _DOWNLOAD_ACK_PUBLIC_ORIGIN,
) = _configure_download_acknowledgements()


def _download_ack_payload(download_event_id: str) -> dict[str, str]:
    return {
        "purpose": _DOWNLOAD_ACK_PURPOSE,
        "download_event_id": download_event_id,
    }


def _issue_download_ack_proof(download_event_id: str) -> str:
    """Sign one recent, nonidentity workbook-preparation receipt."""
    return _DOWNLOAD_ACK_SERIALIZER.dumps(
        _download_ack_payload(download_event_id)
    )


def _verify_download_ack_proof(proof: str, download_event_id: str) -> bool:
    """Return whether a proof is recent and bound to the submitted UUID."""
    try:
        payload = _DOWNLOAD_ACK_SERIALIZER.loads(
            proof,
            max_age=_DOWNLOAD_ACK_MAX_AGE_SECONDS,
        )
    except BadSignature:
        return False
    return payload == _download_ack_payload(download_event_id)


def _authenticated_download_binding(
    *,
    account_id,
    organization_id,
    report_id,
    snapshot_id,
    artifact_id,
) -> str:
    identifiers = (
        account_id,
        organization_id,
        report_id,
        snapshot_id,
        artifact_id,
    )
    canonical = ":".join(str(uuid.UUID(str(value))) for value in identifiers)
    return sha256(canonical.encode("ascii")).hexdigest()


def _authenticated_download_ack_payload(
    download_event_id: str,
    **scope,
) -> dict[str, str]:
    return {
        "purpose": _AUTHENTICATED_DOWNLOAD_ACK_PURPOSE,
        "download_event_id": str(uuid.UUID(download_event_id)),
        "scope_binding": _authenticated_download_binding(**scope),
    }


def _issue_authenticated_download_ack_proof(
    download_event_id: str,
    **scope,
) -> str:
    """Sign a recent opaque receipt bound to one authorized artifact scope."""
    return _DOWNLOAD_ACK_SERIALIZER.dumps(
        _authenticated_download_ack_payload(download_event_id, **scope)
    )


def _verify_authenticated_download_ack_proof(
    proof: str,
    download_event_id: str,
    **scope,
) -> bool:
    """Verify a recent receipt without placing raw identity IDs in its payload."""
    try:
        payload = _DOWNLOAD_ACK_SERIALIZER.loads(
            proof,
            max_age=_DOWNLOAD_ACK_MAX_AGE_SECONDS,
        )
        expected = _authenticated_download_ack_payload(
            download_event_id,
            **scope,
        )
    except (BadSignature, TypeError, ValueError, AttributeError):
        return False
    return payload == expected

_ALLOWED_DIVISIONS = {"", "d1", "d2", "d3", "juco"}
_MAX_TEAM_LENGTH = 120

_INTEGER_COLUMNS = {
    "#", "AB", "H", "2B", "HR", "RBI", "R", "BB", "SO", "SB",
    "W", "L", "SV", "ER",
}
_THREE_DECIMAL_COLUMNS = {"AVG", "OBP", "SLG"}
_TWO_DECIMAL_COLUMNS = {"ERA", "WHIP"}
_DEFAULT_PALETTE = {
    "primary": "#1E3A5F",
    "primary_text": "#FFFFFF",
    "accent": "#C9A227",
    "band": "#F2F5F8",
    "border": "#D9E0E7",
}
_SAFE_EVIDENCE_PURGE_STATUSES = {
    "expired_bytes_present",
    "fresh",
    "never_succeeded",
    "stale",
}


def _json_error(message: str, status: int):
    """Return the API's consistent JSON error shape."""
    return jsonify({"error": message}), status


def _string_field(body: dict, key: str, *, max_length: int) -> str:
    """Read a bounded string field from an untrusted JSON object."""
    value = body.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"'{key}' must be a string.")
    value = value.strip()
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"'{key}' cannot contain control characters.")
    if len(value) > max_length:
        raise ValueError(f"'{key}' must be {max_length} characters or fewer.")
    return value


@app.after_request
def add_security_headers(response):
    """Apply a conservative browser policy to every response."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
    )
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault(
        "Strict-Transport-Security",
        "max-age=31536000; includeSubDomains",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "base-uri 'self'; "
        "connect-src 'self'; "
        "font-src 'self' https://fonts.gstatic.com; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data: https://www.ncaa.com; "
        "object-src 'none'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    )
    return response


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(_error):
    return _json_error("Request body is too large.", 413)


@app.route("/")
def index():
    if getattr(g, "authorization_context", None) is not None:
        return redirect("/reports")
    return render_template("index.html")


@app.route("/demo")
def demo():
    """Render the unlisted product demo landing page."""
    response = app.make_response(render_template("demo.html"))
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@lru_cache(maxsize=1)
def _evidence_purge_session_factory():
    """Construct persistence lazily so shallow liveness stays connection-free."""
    return create_session_factory(get_engine())


@lru_cache(maxsize=1)
def _usage_session_factory():
    """Construct usage persistence lazily for successful workbook responses."""
    return create_session_factory(get_engine())


@lru_cache(maxsize=1)
def _auth_session_factory():
    """Construct auth persistence lazily so shallow liveness stays connection-free."""
    return create_session_factory(get_engine())


@lru_cache(maxsize=1)
def _report_session_factory():
    """Construct report persistence lazily for protected report pages."""
    return create_session_factory(get_engine())


register_auth_routes(
    app,
    identity_provider=_IDENTITY_PROVIDER,
    session_factory=_auth_session_factory,
)
register_organization_routes(app)
register_report_routes(
    app,
    session_factory=_report_session_factory,
    issue_download_ack_proof=_issue_authenticated_download_ack_proof,
)


_PUBLIC_ENDPOINTS = frozenset({
    "static",
    "healthz",
    "readyz",
    "demo",
    "acknowledge_workbook_download",
    "auth.login",
    "auth.start",
    "auth.callback",
    "auth.error",
})
_ACCOUNT_ONLY_ENDPOINTS = frozenset({
    "auth.no_membership",
    "auth.logout",
    "organization.accept",
    "organization.select_organization",
    "organization.switch_organization",
})


def _authentication_enforced() -> bool:
    return not app.testing or bool(
        app.config.get("ROSTER_SCOUT_TEST_AUTH_ENFORCEMENT")
    )


def _json_auth_response(category: str):
    return jsonify({"error": category}), 401 if category == "authentication_required" else 404


def _load_account_only_context(cookie_value: str):
    with _auth_session_factory().begin() as session:
        loaded = load_account_session(
            session,
            cookie_value=cookie_value,
            touch=True,
        )
        memberships = (
            list_active_memberships(session, account_id=loaded.account_id)
            if loaded is not None
            else []
        )
    return loaded, memberships


@app.before_request
def require_authenticated_organization():
    """Default-deny every current and future route outside the short allowlist."""
    if not _authentication_enforced() or request.endpoint in _PUBLIC_ENDPOINTS:
        return None

    cookie_value = request.cookies.get(SESSION_COOKIE_NAME, "")
    try:
        if request.endpoint in _ACCOUNT_ONLY_ENDPOINTS:
            loaded, memberships = _load_account_only_context(cookie_value)
            if loaded is None:
                raise AuthorizationFailure("authentication_required")
            g.account_session = loaded
            g.active_memberships = memberships
        else:
            with _auth_session_factory().begin() as session:
                g.authorization_context = require_context(
                    session,
                    cookie_value=cookie_value,
                )
    except AuthorizationFailure as exc:
        if exc.category == "authentication_required":
            if request.path.startswith("/api/") or request.path == "/scrape":
                return _json_auth_response("authentication_required")
            return redirect("/login", code=302)
        try:
            loaded, memberships = _load_account_only_context(cookie_value)
        except Exception:
            loaded, memberships = None, []
        if loaded is None:
            if request.path.startswith("/api/") or request.path == "/scrape":
                return _json_auth_response("authentication_required")
            return redirect("/login", code=302)
        if request.path.startswith("/api/") or request.path == "/scrape":
            return _json_auth_response("not_found")
        if memberships:
            return redirect("/organizations/select", code=302)
        return redirect("/organizations/none", code=302)
    except Exception:
        app.logger.warning("identity_authorization_failed")
        if request.path.startswith("/api/") or request.path == "/scrape":
            return _json_auth_response("authentication_required")
        return redirect("/login", code=302)

    if request.method in app.config["WTF_CSRF_METHODS"]:
        _CSRF.protect()
    return None


@app.errorhandler(CSRFError)
def csrf_rejected(_error):
    if request.path.startswith("/api/") or request.path == "/scrape":
        return jsonify({"error": "request_rejected"}), 400
    return render_template("auth_error.html", state="failure"), 400


def _record_workbook_download(event_key):
    """Best-effort commit of one acknowledged anonymous workbook download."""
    try:
        with _usage_session_factory().begin() as session:
            record_workbook_download_served(
                session,
                event_key=event_key,
                actor_account_id=None,
                organization_id=None,
            )
    except Exception:
        app.logger.warning(
            "usage_event_dropped route=download_ack reason=database_unavailable"
        )
        return "dropped"
    return "recorded"


@app.route("/readyz")
def readyz():
    """Fail closed until persisted failure-evidence purge health is ready."""
    try:
        get_failure_evidence_retention()
    except FailureEvidenceConfigurationError:
        return jsonify({
            "status": "not_ready",
            "evidence_purge": "unavailable",
        }), 503

    try:
        readiness = get_evidence_purge_readiness(
            _evidence_purge_session_factory()
        )
    except Exception:
        return jsonify({
            "status": "not_ready",
            "evidence_purge": "unavailable",
        }), 503

    evidence_purge = (
        readiness.status
        if readiness.status in _SAFE_EVIDENCE_PURGE_STATUSES
        else "unavailable"
    )
    ready = readiness.ready and evidence_purge == "fresh"
    return jsonify({
        "status": "ready" if ready else "not_ready",
        "evidence_purge": evidence_purge,
    }), 200 if ready else 503


@app.route("/api/teams")
def teams():
    division = (request.args.get("division") or "").strip().lower()
    try:
        catalog = list_ncaa_teams(division)
        year = catalog[0]["year"] if catalog else None
        return jsonify({"division": division, "year": year, "teams": catalog})
    except NcaaError as exc:
        return jsonify({"error": str(exc)}), 400


def _canonical_download_event_id(value) -> str:
    """Return an exact canonical UUID string for an acknowledgement."""
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("Download acknowledgement is invalid.")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Download acknowledgement is invalid.") from exc
    if canonical != value:
        raise ValueError("Download acknowledgement is invalid.")
    return canonical


@app.route("/api/downloads/ack", methods=["POST"])
def acknowledge_workbook_download():
    """Record one recent same-origin browser acknowledgement idempotently."""
    if request.mimetype != "application/json":
        return _json_error("Content-Type must be application/json.", 415)
    if request.content_length is not None and request.content_length > 1024:
        return _json_error("Request body is too large.", 413)
    if len(request.get_data(cache=True)) > 1024:
        return _json_error("Request body is too large.", 413)

    expected_origin = (
        _DOWNLOAD_ACK_PUBLIC_ORIGIN
        or f"{request.scheme}://{request.host}"
    )
    if request.headers.get("Origin") != expected_origin:
        return _json_error("Acknowledgement origin is not allowed.", 403)
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return _json_error("Acknowledgement origin is not allowed.", 403)

    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {
        "download_event_id",
        "acknowledgement_proof",
    }:
        return _json_error("Download acknowledgement is invalid.", 400)
    try:
        download_event_id = _canonical_download_event_id(
            body.get("download_event_id")
        )
    except ValueError:
        return _json_error("Download acknowledgement is invalid.", 400)

    proof = body.get("acknowledgement_proof")
    if (
        not isinstance(proof, str)
        or not proof
        or len(proof) > 512
        or any(ord(character) < 32 for character in proof)
    ):
        return _json_error("Acknowledgement proof is invalid.", 400)
    if not _verify_download_ack_proof(proof, download_event_id):
        return _json_error("Acknowledgement proof is invalid.", 400)

    persisted = _record_workbook_download(download_event_id) == "recorded"
    return jsonify({
        "acknowledged": True,
        "persisted": persisted,
    }), 200 if persisted else 202


@lru_cache(maxsize=1024)
def _ncaa_logo_png(team_id):
    url = f"https://web2.ncaa.org/ncaa_style/img/All_Logos/sm/{team_id}.gif"
    response = requests.get(url, timeout=8, stream=True)
    content = bytearray()
    try:
        response.raise_for_status()
        for chunk in response.iter_content(16384):
            content.extend(chunk)
            if len(content) > 256_000:
                raise ValueError("Logo is too large")
    finally:
        response.close()
    image = Image.open(io.BytesIO(content))
    if image.width * image.height > 1_000_000:
        raise ValueError("Logo dimensions are too large")
    image = image.convert("RGBA")
    # Remove only the near-white region connected to the outside edge, so
    # white details enclosed inside the actual school mark remain intact.
    for x in range(image.width):
        for y in (0, image.height - 1):
            if min(image.getpixel((x, y))[:3]) >= 245:
                ImageDraw.floodfill(image, (x, y), (255, 255, 255, 0), thresh=25)
    for y in range(image.height):
        for x in (0, image.width - 1):
            if min(image.getpixel((x, y))[:3]) >= 245 and image.getpixel((x, y))[3]:
                ImageDraw.floodfill(image, (x, y), (255, 255, 255, 0), thresh=25)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@app.route("/api/team-logo/<team_id>")
def team_logo(team_id):
    if not team_id.isascii() or not team_id.isdigit() or len(team_id) > 6:
        return "", 404
    try:
        logo = _ncaa_logo_png(team_id)
    except (requests.RequestException, OSError, ValueError, UnidentifiedImageError):
        return "", 404
    return send_file(io.BytesIO(logo), mimetype="image/png", max_age=86400)


@app.route("/scrape", methods=["POST"])
def scrape():
    if not request.is_json:
        return _json_error("Content-Type must be application/json.", 415)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _json_error("Request body must be a JSON object.", 400)

    try:
        team = _string_field(body, "team", max_length=_MAX_TEAM_LENGTH)
        division = _string_field(body, "division", max_length=8).lower()
        season_team_id = _string_field(body, "season_team_id", max_length=12)
        _string_field(body, "team_id", max_length=12)
        _string_field(body, "display_name", max_length=_MAX_TEAM_LENGTH)
        if "download_event_id" in body and body["download_event_id"] is not None:
            download_event_id = _string_field(
                body,
                "download_event_id",
                max_length=36,
            )
            try:
                download_event_id = str(uuid.UUID(download_event_id))
            except (ValueError, AttributeError) as exc:
                raise ValueError("'download_event_id' must be a UUID.") from exc
        else:
            download_event_id = str(uuid.uuid4())
    except ValueError as exc:
        return _json_error(str(exc), 400)

    if division not in _ALLOWED_DIVISIONS:
        return _json_error("Choose D1, D2, D3, or JUCO.", 400)
    if season_team_id and (
        not season_team_id.isascii() or not season_team_id.isdigit()
    ):
        return _json_error("'season_team_id' must contain only digits.", 400)

    if not team and not season_team_id:
        return _json_error("Please enter a team name.", 400)

    try:
        client = request.remote_addr or "unknown"
        if os.environ.get("TRUST_FLY_CLIENT_IP") == "1":
            client = request.headers.get("Fly-Client-IP", client)
        retry_after = check_request_limit(client)
        if retry_after:
            response = jsonify({"error": "Too many requests. Please try again later."})
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            return response
        if division == "juco":
            rows, team_label = scrape_juco_by_name(team)
        elif division in {"d1", "d2", "d3"}:
            if season_team_id:
                rows, team_label = scrape_ncaa_by_id(
                    season_team_id,
                    division=division,
                )
            else:
                rows, team_label = scrape_ncaa_by_name(team, division=division)
        else:
            # Backwards-compatible auto-detection for clients that omit a division.
            try:
                rows, team_label = scrape_ncaa_by_name(team)
            except NcaaError as ncaa_exc:
                try:
                    rows, team_label = scrape_juco_by_name(team)
                except ValueError:
                    raise ncaa_exc

        hitters, pitchers = split_roster(rows)
        branding = None
        if division in {"d1", "d2", "d3"}:
            branding = _ncaa_workbook_branding(body, team_label)
        workbook_bytes = _create_workbook(hitters, pitchers, branding=branding)
        filename = f"{_safe_filename(team_label)}_Baseball_{date.today().isoformat()}.xlsx"

        response = send_file(
            workbook_bytes,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            max_age=0,
        )
        response.headers["X-Roster-Scout-Players"] = str(len(rows))
        response.headers["X-Roster-Scout-Hitters"] = str(len(hitters))
        response.headers["X-Roster-Scout-Pitchers"] = str(len(pitchers))
        response.headers["X-Roster-Scout-Download-Event-ID"] = download_event_id
        response.headers["X-Roster-Scout-Download-Ack-Proof"] = (
            _issue_download_ack_proof(download_event_id)
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    except BrowserCapacityError as exc:
        response = jsonify({"error": str(exc)})
        response.status_code = 503
        response.headers["Retry-After"] = "30"
        return response
    except (NcaaError, ValueError) as exc:
        return _json_error(str(exc), 502)
    except requests.RequestException:
        app.logger.warning("Upstream request failed while building a workbook", exc_info=True)
        return _json_error("A roster data source is temporarily unavailable.", 502)
    except Exception:
        app.logger.exception("Unexpected workbook generation failure")
        return _json_error("Roster Scout could not build that workbook.", 500)


def _safe_filename(value: str) -> str:
    """Return a readable filename component without path or shell characters."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")
    return cleaned or "Roster"


def _excel_value(header: str, value):
    """Convert stat strings to useful Excel number types where semantics allow."""
    if value is None or value == "":
        return ""
    text = str(value).strip()
    try:
        if header in _INTEGER_COLUMNS and re.fullmatch(r"-?\d+", text):
            return int(text)
        if header in _THREE_DECIMAL_COLUMNS | _TWO_DECIMAL_COLUMNS:
            return float(text)
    except ValueError:
        pass
    # IP intentionally remains text: 7.2 means seven and two-thirds innings.
    return text


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _mix_rgb(
    first: tuple[int, int, int],
    second: tuple[int, int, int],
    second_weight: float,
) -> tuple[int, int, int]:
    return tuple(
        round(a * (1 - second_weight) + b * second_weight)
        for a, b in zip(first, second)
    )


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    channels = []
    for channel in rgb:
        value = channel / 255
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    light, dark = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _logo_palette(logo_png: bytes | None) -> dict[str, str]:
    """Extract a readable workbook palette from the visible colors in a logo."""
    if not logo_png:
        return dict(_DEFAULT_PALETTE)
    try:
        image = Image.open(io.BytesIO(logo_png)).convert("RGBA")
        image.thumbnail((160, 160), Image.Resampling.LANCZOS)
        image_pixels = image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()
        opaque_pixels = [
            pixel[:3]
            for pixel in image_pixels
            if pixel[3] >= 96 and not all(channel >= 245 for channel in pixel[:3])
        ]
        if not opaque_pixels:
            return dict(_DEFAULT_PALETTE)

        sample = Image.new("RGB", (len(opaque_pixels), 1))
        sample.putdata(opaque_pixels)
        quantized = sample.quantize(colors=12, method=Image.Quantize.MEDIANCUT)
        palette = quantized.getpalette()
        candidates = []
        for count, index in quantized.getcolors() or []:
            rgb = tuple(palette[index * 3:index * 3 + 3])
            brightness = sum(rgb) / (3 * 255)
            saturation = (max(rgb) - min(rgb)) / 255
            if brightness > 0.96:
                continue
            score = count * (0.65 + 1.8 * saturation)
            candidates.append((score, count, rgb))
        if not candidates:
            return dict(_DEFAULT_PALETTE)

        candidates.sort(reverse=True)
        primary = candidates[0][2]
        accent = next(
            (
                rgb for _, _, rgb in candidates[1:]
                if sum((a - b) ** 2 for a, b in zip(primary, rgb)) ** 0.5 >= 70
            ),
            _mix_rgb(primary, (255, 255, 255), 0.38),
        )
        white = (255, 255, 255)
        dark_text = (24, 32, 42)
        primary_text = white if _contrast_ratio(primary, white) >= _contrast_ratio(primary, dark_text) else dark_text
        return {
            "primary": _rgb_to_hex(primary),
            "primary_text": _rgb_to_hex(primary_text),
            "accent": _rgb_to_hex(accent),
            "band": _rgb_to_hex(_mix_rgb(primary, white, 0.93)),
            "border": _rgb_to_hex(_mix_rgb(primary, white, 0.82)),
        }
    except (OSError, ValueError, TypeError):
        return dict(_DEFAULT_PALETTE)


def _ncaa_workbook_branding(body: dict, team_label: str) -> dict:
    team_id = str(body.get("team_id") or "").strip()
    logo_png = None
    if team_id.isascii() and team_id.isdigit() and len(team_id) <= 6:
        try:
            logo_png = _ncaa_logo_png(team_id)
        except (requests.RequestException, OSError, ValueError, UnidentifiedImageError):
            pass

    display_name = re.sub(
        r"[\x00-\x1f]+",
        " ",
        str(body.get("display_name") or team_label),
    ).strip()[:120]
    return {
        "school_name": display_name or team_label,
        "generated_on": date.today(),
        "logo_png": logo_png,
        "palette": _logo_palette(logo_png),
    }


def _column_width_pixels(width: float) -> int:
    """Approximate the Excel-rendered pixel width for a configured column."""
    return round(width * 7 + 5)


def _lockup_last_column(
    column_widths: list[float],
    school_name: str,
    date_text: str,
    text_start_col: int = 1,
) -> int:
    """Return a native-cell header span wide enough for fixed-size lockup text."""
    title_pixels = round(xl_pixel_width(school_name) * 1.6)
    date_pixels = round(xl_pixel_width(date_text) * 1.08)
    required_width = max(title_pixels, date_pixels) + 24
    accumulated_width = 0
    last_col = min(max(3, text_start_col), len(column_widths) - 1)
    for col_index in range(text_start_col, len(column_widths)):
        width = column_widths[col_index]
        accumulated_width += _column_width_pixels(width)
        if col_index >= last_col and accumulated_width >= required_width:
            return col_index
    return len(column_widths) - 1


def _write_worksheet(
    workbook,
    title: str,
    rows: list[dict],
    headers: list[str],
    branding: dict | None = None,
):
    worksheet = workbook.add_worksheet(title)
    worksheet.hide_gridlines(2)
    branding = branding or {}
    palette = branding.get("palette") or _DEFAULT_PALETTE
    school_name = branding.get("school_name", "Axis Baseball")
    generated_on = branding.get("generated_on")
    logo_png = branding.get("logo_png")
    first_header_row = 2 if branding else 0
    first_data_row = first_header_row + 1
    last_col = len(headers) - 1
    worksheet.freeze_panes(first_data_row, 0)
    worksheet.set_tab_color(palette["primary"])
    worksheet.set_zoom(90)

    header_format = workbook.add_format({
        "bold": True,
        "font_name": "Arial",
        "font_size": 10,
        "font_color": palette["primary_text"],
        "bg_color": palette["primary"],
        "align": "left",
        "valign": "vcenter",
        "bottom": 2,
        "bottom_color": palette["accent"],
    })
    title_format = workbook.add_format({
        "bold": True,
        "font_name": "Arial",
        "font_size": 16,
        "font_color": palette["primary"],
        "align": "left",
        "valign": "bottom",
    })
    subtitle_format = workbook.add_format({
        "bold": True,
        "font_name": "Arial",
        "font_size": 10.5,
        "font_color": palette["primary"],
        "align": "left",
        "valign": "top",
    })
    lockup_format = workbook.add_format({
        "bg_color": "#FFFFFF",
        "align": "left",
        "valign": "vcenter",
    })

    def body_format(number_format: str | None = None, banded: bool = False, bold: bool = False):
        properties = {
            "font_name": "Arial",
            "font_size": 10,
            "font_color": "#20262E",
            "align": "left",
            "valign": "vcenter",
            "bottom": 1,
            "bottom_color": palette["border"],
        }
        if number_format:
            properties["num_format"] = number_format
        if banded:
            properties["bg_color"] = palette["band"]
        if bold:
            properties["bold"] = True
        return workbook.add_format(properties)

    formats = {}
    for banded in (False, True):
        for kind, number_format in (("text", None), ("integer", "0"), ("three", "0.000"), ("two", "0.00")):
            formats[(kind, banded, False)] = body_format(number_format, banded)
            formats[(kind, banded, True)] = body_format(number_format, banded, bold=True)

    column_widths = []
    for col_index, header in enumerate(headers):
        content_width = max(
            [len(header)] + [len(str(player.get(header, "") or "")) for player in rows]
        )
        if header == "Name":
            width = min(max(content_width + 2, 20), 34)
        elif header in {"Position", "Class"}:
            width = min(max(content_width + 2, 10), 18)
        else:
            width = min(max(content_width + 2, 7), 12)
        column_widths.append(width)
        worksheet.set_column(col_index, col_index, width)

    if branding:
        title_row_height = 36
        subtitle_row_height = 30
        worksheet.set_row(0, title_row_height)
        worksheet.set_row(1, subtitle_row_height)
        if isinstance(generated_on, date):
            date_text = f"{generated_on.strftime('%B')} {generated_on.day}, {generated_on.year}".upper()
        else:
            date_text = str(generated_on or date.today().isoformat()).upper()
        if logo_png:
            with Image.open(io.BytesIO(logo_png)) as logo:
                width, height = logo.size
            scale = min(82 / max(width, 1), 72 / max(height, 1))
            logo_width = width * scale
            logo_height = height * scale
            first_column_pixels = _column_width_pixels(column_widths[0])
            first_two_rows_pixels = round((title_row_height + subtitle_row_height) * 4 / 3)
            logo_x_offset = max(0, round((first_column_pixels - logo_width) / 2))
            logo_y_offset = max(0, round((first_two_rows_pixels - logo_height) / 2))
            lockup_last_col = _lockup_last_column(
                column_widths,
                school_name,
                date_text,
            )
            worksheet.merge_range(0, 0, 1, 0, "", lockup_format)
            worksheet.merge_range(0, 1, 0, lockup_last_col, school_name, title_format)
            worksheet.merge_range(1, 1, 1, lockup_last_col, date_text, subtitle_format)
            worksheet.insert_image(0, 0, "school-logo.png", {
                "image_data": io.BytesIO(logo_png),
                "x_offset": logo_x_offset,
                "y_offset": logo_y_offset,
                "x_scale": scale,
                "y_scale": scale,
                "object_position": 1,
                "description": f"{school_name} logo",
            })
        else:
            worksheet.merge_range(0, 0, 0, last_col, school_name, title_format)
            worksheet.merge_range(1, 0, 1, last_col, date_text, subtitle_format)

    worksheet.set_row(first_header_row, 26)
    for col, header in enumerate(headers):
        worksheet.write(first_header_row, col, header, header_format)

    for row_index, player in enumerate(rows, start=first_data_row):
        worksheet.set_row(row_index, 21)
        banded = (row_index - first_data_row) % 2 == 1
        for col_index, header in enumerate(headers):
            value = _excel_value(header, player.get(header, ""))
            kind = "text"
            if header in _INTEGER_COLUMNS and isinstance(value, int):
                kind = "integer"
            elif header in _THREE_DECIMAL_COLUMNS and isinstance(value, float):
                kind = "three"
            elif header in _TWO_DECIMAL_COLUMNS and isinstance(value, float):
                kind = "two"
            cell_format = formats[(kind, banded, header == "Name")]
            worksheet.write(row_index, col_index, value, cell_format)

    if rows:
        worksheet.add_table(first_header_row, 0, first_header_row + len(rows), last_col, {
            "columns": [{"header": header, "header_format": header_format} for header in headers],
            "style": None,
            "banded_rows": False,
            "name": f"{title}Table",
        })
    else:
        worksheet.autofilter(first_header_row, 0, first_header_row, last_col)

    last_row = first_header_row + max(len(rows), 1)
    worksheet.set_landscape()
    worksheet.fit_to_pages(1, 0)
    worksheet.repeat_rows(first_header_row, first_header_row)
    worksheet.set_margins(left=0.35, right=0.35, top=0.55, bottom=0.55)
    worksheet.print_area(0, 0, last_row, last_col)
    worksheet.set_footer(f"&L{school_name} Baseball&C{title}&RPage &P of &N")


def _create_workbook(
    hitters: list[dict],
    pitchers: list[dict],
    branding: dict | None = None,
) -> io.BytesIO:
    """Build the two-tab Roster Scout workbook entirely in memory."""
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {
        "in_memory": True,
        "strings_to_formulas": False,
        "strings_to_urls": False,
    })
    school_name = (branding or {}).get("school_name", "Axis Baseball")
    workbook.set_properties({
        "title": f"{school_name} Baseball Roster Scout",
        "subject": "Baseball roster and season statistics",
        "author": "Axis Baseball",
        "company": "Axis Baseball",
    })
    _write_worksheet(workbook, "Hitters", hitters, HITTER_HEADERS, branding=branding)
    _write_worksheet(workbook, "Pitchers", pitchers, PITCHER_HEADERS, branding=branding)
    workbook.close()
    output.seek(0)
    return output


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, port=port)

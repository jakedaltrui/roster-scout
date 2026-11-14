"""Thin Flask composition for provider-neutral passwordless authentication."""

from __future__ import annotations

import hashlib
import hmac
import os
from urllib.parse import urlencode, urlsplit

from flask import Blueprint, current_app, jsonify, redirect, render_template, request
from flask.sessions import SessionInterface, SessionMixin
from itsdangerous import URLSafeTimedSerializer
from werkzeug.datastructures import CallbackDict

from .accounts import (
    AccountLinkFailure,
    link_verified_identity,
    normalize_verified_email,
)
from .audit import append_security_audit_event
from .authorization import list_active_memberships
from .identity import (
    FakeIdentityProvider,
    IdentityFailure,
    begin_auth_transaction,
    begin_magic_auth_transaction,
    check_auth_request_limit,
    consume_auth_transaction,
    consume_magic_auth_transaction,
    exchange_authorization_code,
    get_magic_auth_transaction,
    resolve_provider_invitation_token,
)
from .sessions import (
    SESSION_COOKIE_NAME,
    complete_provider_logout,
    create_account_session,
    load_account_session,
    prepare_account_session_logout,
    session_cookie_options,
)
from .organizations import (
    OrganizationFailure,
    accept_invitation,
    institutional_domain_from_verified_email,
    resolve_verified_institutional_membership,
    resolve_provider_invitation_reference,
)


auth_blueprint = Blueprint("auth", __name__)

_DEFAULT_RETURN_PATH = "/organizations/none"
_ALLOWED_RETURN_PATHS = frozenset({
    _DEFAULT_RETURN_PATH,
    "/?resume=download",
    "/organizations/select",
    "/reports",
})
_MAX_CALLBACK_VALUE_LENGTH = 512
_MAX_INVITATION_TOKEN_LENGTH = 2048
_AUTH_START_REQUEST_LIMIT = 10
_AUTH_CALLBACK_REQUEST_LIMIT = 20
_MAGIC_START_REQUEST_LIMIT = 6
_MAGIC_VERIFY_REQUEST_LIMIT = 20
_MAX_AUTH_JSON_BYTES = 2048


class RosterScoutBrowserSession(CallbackDict, SessionMixin):
    """Read-only Flask session projection backed by AccountSession."""

    def __init__(self, initial=None):
        super().__init__(initial or {})
        self.modified = False


class RosterScoutSessionInterface(SessionInterface):
    """Expose the persisted CSRF secret without creating a second cookie."""

    def open_session(self, app, request):
        cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
        if not cookie_value:
            return RosterScoutBrowserSession()
        configured = app.extensions.get("roster_scout_auth_session_factory")
        if configured is None:
            return RosterScoutBrowserSession()
        factory = configured if hasattr(configured, "begin") else configured()
        try:
            with factory.begin() as database_session:
                loaded = load_account_session(
                    database_session,
                    cookie_value=cookie_value,
                    touch=False,
                )
        except Exception:
            app.logger.warning("identity_session_load_failed")
            return RosterScoutBrowserSession()
        if loaded is None:
            return RosterScoutBrowserSession()
        return RosterScoutBrowserSession({
            "csrf_token": loaded.csrf_secret,
            "account_id": str(loaded.account_id),
        })

    def save_session(self, app, session, response):
        # The opaque account-session cookie is issued and revoked explicitly by
        # the auth routes. Flask-WTF writes no browser-side session material.
        return None


def _identity_provider():
    return current_app.extensions.get(
        "roster_scout_identity_provider",
        FakeIdentityProvider(),
    )


def _session_factory():
    configured = current_app.extensions["roster_scout_auth_session_factory"]
    if hasattr(configured, "begin"):
        return configured
    return configured()


def _return_path(value: str | None) -> str:
    candidate = value or _DEFAULT_RETURN_PATH
    if candidate not in _ALLOWED_RETURN_PATHS:
        raise IdentityFailure("invalid_return_path")
    return candidate


def _bounded_callback_value(value: str | None) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_CALLBACK_VALUE_LENGTH
        or any(ord(character) < 32 for character in value)
    ):
        raise IdentityFailure("invalid_callback")
    return value


def _bounded_invitation_token(value: str | None) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_INVITATION_TOKEN_LENGTH
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        raise IdentityFailure("invalid_invitation")
    return value


def _auth_failure(*, status: int):
    return render_template("auth_error.html", state="failure"), status


def _auth_throttled(*, retry_after: int):
    response = current_app.make_response(
        (render_template("auth_error.html", state="failure"), 429)
    )
    response.headers["Retry-After"] = str(retry_after)
    return response


def _auth_client_digest() -> str:
    client = request.remote_addr or "unknown"
    if os.environ.get("TRUST_FLY_CLIENT_IP") == "1":
        client = request.headers.get("Fly-Client-IP", client)
    secret = current_app.secret_key
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if not isinstance(secret, bytes) or not secret:
        raise RuntimeError("auth limiter secret unavailable")
    return hmac.new(secret, client.encode("utf-8"), hashlib.sha256).hexdigest()


def _auth_retry_after(*, scope: str, limit: int) -> int:
    with _session_factory().begin() as session:
        return check_auth_request_limit(
            session,
            scope=scope,
            client_digest=_auth_client_digest(),
            limit=limit,
        )


def _record_sign_in_failure(reason: str) -> None:
    """Best-effort sanitized failure evidence when persistence is available."""
    try:
        with _session_factory().begin() as session:
            append_security_audit_event(
                session,
                target_type="session",
                action="identity.sign_in",
                outcome="failed",
                reason=reason,
            )
    except Exception:
        current_app.logger.warning("identity_sign_in_audit_failed")


def _auth_json_error(category: str, status: int):
    return jsonify({"error": category}), status


def _same_origin_json_request() -> bool:
    if request.mimetype != "application/json":
        return False
    if request.content_length is not None and request.content_length > _MAX_AUTH_JSON_BYTES:
        return False
    if len(request.get_data(cache=True)) > _MAX_AUTH_JSON_BYTES:
        return False
    if request.headers.get("Sec-Fetch-Site", "").casefold() == "cross-site":
        return False
    try:
        origin = urlsplit(request.headers.get("Origin", ""))
        origin.port
    except (TypeError, ValueError):
        return False
    if (
        not origin.hostname
        or origin.username
        or origin.password
        or origin.path
        or origin.query
        or origin.fragment
        or origin.netloc.casefold() != request.host.casefold()
    ):
        return False
    if os.environ.get("FLY_APP_NAME", "").strip():
        return origin.scheme == "https"
    return origin.scheme in {"http", "https"}


def _request_identity_signals() -> tuple[str | None, str | None]:
    client_ip = request.remote_addr
    if os.environ.get("TRUST_FLY_CLIENT_IP") == "1":
        client_ip = request.headers.get("Fly-Client-IP", client_ip)
    user_agent = request.headers.get("User-Agent")
    return client_ip, user_agent[:500] if user_agent else None


def _browser_csrf_token(raw_secret: str) -> str:
    signing_key = current_app.config.get("WTF_CSRF_SECRET_KEY") or current_app.secret_key
    return URLSafeTimedSerializer(
        signing_key,
        salt="wtf-csrf-token",
    ).dumps(raw_secret)


def _finish_verified_identity(identity, *, invitation_id=None):
    """Create the local account, school membership, and opaque browser session."""
    with _session_factory().begin() as session:
        account = link_verified_identity(session, identity)
        if invitation_id is not None:
            accept_invitation(
                session,
                invitation_id=invitation_id,
                account_id=account.id,
            )
        institutional_resolution = resolve_verified_institutional_membership(
            session,
            account_id=account.id,
        )
        memberships = list_active_memberships(
            session,
            account_id=account.id,
        )
        active_organization_id = institutional_resolution.membership.organization_id
        credential = create_account_session(
            session,
            account_id=account.id,
            active_organization_id=active_organization_id,
            sealed_provider_state=identity.sealed_session,
            rotate_cookie_value=request.cookies.get(SESSION_COOKIE_NAME),
        )
        append_security_audit_event(
            session,
            actor_account_id=account.id,
            organization_id=active_organization_id,
            target_type="account",
            target_id=account.id,
            action="identity.sign_in",
            outcome="succeeded",
            reason="authorized",
        )
    return credential, memberships, active_organization_id, account.normalized_email


@auth_blueprint.get("/login")
def login():
    try:
        return_path = _return_path(request.args.get("return_to"))
    except IdentityFailure:
        return _auth_failure(status=400)
    query = urlencode({"sign_in": "1", "return_to": return_path})
    return redirect(f"/?{query}#builder", code=302)


@auth_blueprint.post("/auth/magic/start")
def magic_start():
    """Send a six-digit code for an inline, verified institutional sign-in."""
    if not _same_origin_json_request():
        return _auth_json_error("request_rejected", 400)
    try:
        retry_after = _auth_retry_after(
            scope="magic_start",
            limit=_MAGIC_START_REQUEST_LIMIT,
        )
    except Exception:
        current_app.logger.warning("identity_magic_start_limit_failed")
        return _auth_json_error("identity_unavailable", 503)
    if retry_after:
        response = jsonify({"error": "throttled"})
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response

    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {"email", "return_to"}:
            raise IdentityFailure("invalid_identity_response")
        return_path = _return_path(payload.get("return_to"))
        normalized_email = normalize_verified_email(payload.get("email"))
        institutional_domain_from_verified_email(normalized_email)
        with _session_factory().begin() as session:
            transaction = begin_magic_auth_transaction(
                session,
                normalized_email=normalized_email,
                return_path=return_path,
            )
        client_ip, user_agent = _request_identity_signals()
        provider_expiry = _identity_provider().send_magic_auth(
            email=normalized_email,
            ip_address=client_ip,
            user_agent=user_agent,
        )
    except (AccountLinkFailure, IdentityFailure, OrganizationFailure) as exc:
        category = getattr(exc, "category", "invalid_identity_response")
        if category == "institutional_domain_required":
            return _auth_json_error("school_email_required", 400)
        if category == "throttled":
            return _auth_json_error("throttled", 429)
        if category == "identity_unavailable":
            return _auth_json_error("identity_unavailable", 503)
        return _auth_json_error("invalid_email", 400)
    except Exception:
        current_app.logger.warning("identity_magic_start_failed")
        return _auth_json_error("identity_unavailable", 503)

    expires_at = min(transaction.expires_at, provider_expiry)
    response_payload = {
        "challenge": transaction.challenge,
        "expires_at": expires_at.isoformat(),
    }
    if current_app.debug and isinstance(_identity_provider(), FakeIdentityProvider):
        response_payload["development_code"] = "123456"
    return jsonify(response_payload)


@auth_blueprint.post("/auth/magic/verify")
def magic_verify():
    """Verify one Magic Auth code and establish the normal secure session."""
    if not _same_origin_json_request():
        return _auth_json_error("request_rejected", 400)
    try:
        retry_after = _auth_retry_after(
            scope="magic_verify",
            limit=_MAGIC_VERIFY_REQUEST_LIMIT,
        )
    except Exception:
        current_app.logger.warning("identity_magic_verify_limit_failed")
        return _auth_json_error("identity_unavailable", 503)
    if retry_after:
        response = jsonify({"error": "throttled"})
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response

    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {"challenge", "code"}:
            raise IdentityFailure("invalid_callback")
        challenge = _bounded_callback_value(payload.get("challenge"))
        code = payload.get("code")
        if (
            not isinstance(code, str)
            or len(code) != 6
            or not code.isascii()
            or not code.isdigit()
        ):
            raise IdentityFailure("invalid_callback")
        with _session_factory().begin() as session:
            pending = get_magic_auth_transaction(
                session,
                challenge=challenge,
            )
        client_ip, user_agent = _request_identity_signals()
        identity = _identity_provider().verify_magic_auth(
            email=pending.normalized_email,
            code=code,
            ip_address=client_ip,
            user_agent=user_agent,
        )
        if (
            not identity.email_verified
            or normalize_verified_email(identity.email) != pending.normalized_email
        ):
            raise IdentityFailure("invalid_identity_response")
        with _session_factory().begin() as session:
            consume_magic_auth_transaction(
                session,
                transaction_id=pending.transaction_id,
            )
        credential, _memberships, _organization_id, normalized_email = (
            _finish_verified_identity(identity)
        )
    except IdentityFailure as exc:
        if exc.category == "throttled":
            return _auth_json_error("throttled", 429)
        if exc.category == "identity_unavailable":
            return _auth_json_error("identity_unavailable", 503)
        if exc.category == "invalid_auth_state":
            return _auth_json_error("challenge_expired", 400)
        _record_sign_in_failure("invalid_callback")
        return _auth_json_error("invalid_code", 400)
    except (AccountLinkFailure, OrganizationFailure):
        _record_sign_in_failure("identity_conflict")
        return _auth_json_error("sign_in_failed", 400)
    except Exception:
        current_app.logger.warning("identity_magic_verify_failed")
        _record_sign_in_failure("identity_unavailable")
        return _auth_json_error("identity_unavailable", 503)

    response = jsonify({
        "authenticated": True,
        "return_to": pending.return_path,
        "csrf_token": _browser_csrf_token(credential.csrf_secret),
        "account_initial": normalized_email[0].upper(),
    })
    response.set_cookie(
        SESSION_COOKIE_NAME,
        credential.cookie_value,
        **session_cookie_options(),
    )
    return response


@auth_blueprint.get("/auth/start")
def start():
    try:
        retry_after = _auth_retry_after(
            scope="start",
            limit=_AUTH_START_REQUEST_LIMIT,
        )
    except Exception:
        current_app.logger.warning("identity_start_limit_failed")
        return _auth_failure(status=503)
    if retry_after:
        return _auth_throttled(retry_after=retry_after)
    try:
        return_path = _return_path(request.args.get("return_to"))
        raw_invitation_token = request.args.get("invitation_token")
        invitation_token = None
        invitation_reference = None
        if raw_invitation_token is not None:
            invitation_token = _bounded_invitation_token(raw_invitation_token)
            invitation_reference = resolve_provider_invitation_token(
                _identity_provider(),
                invitation_token=invitation_token,
                logger=current_app.logger,
            )
        with _session_factory().begin() as session:
            invitation = (
                resolve_provider_invitation_reference(
                    session,
                    reference=invitation_reference,
                )
                if invitation_reference is not None
                else None
            )
            transaction = begin_auth_transaction(
                session,
                invitation_id=(invitation.id if invitation is not None else None),
                return_path=return_path,
            )
        destination = _identity_provider().authorization_url(
            state=transaction.state,
            invitation_token=invitation_token,
        )
    except (IdentityFailure, OrganizationFailure):
        return _auth_failure(status=400)
    except Exception:
        current_app.logger.warning("identity_start_failed")
        return _auth_failure(status=503)
    return redirect(destination, code=302)


@auth_blueprint.get("/auth/callback")
def callback():
    try:
        retry_after = _auth_retry_after(
            scope="callback",
            limit=_AUTH_CALLBACK_REQUEST_LIMIT,
        )
    except Exception:
        current_app.logger.warning("identity_callback_limit_failed")
        return _auth_failure(status=503)
    if retry_after:
        return _auth_throttled(retry_after=retry_after)
    transaction_consumed = False
    try:
        state = _bounded_callback_value(request.args.get("state"))
        # Commit consumption before any provider exchange so failed exchanges and
        # retries cannot replay a bearer callback state.
        with _session_factory().begin() as session:
            transaction = consume_auth_transaction(session, state=state)
        transaction_consumed = True
        return_path = _return_path(transaction.return_path)
        code = _bounded_callback_value(request.args.get("code"))
    except IdentityFailure as exc:
        current_app.logger.warning("identity_callback_failed")
        if transaction_consumed:
            _record_sign_in_failure("invalid_callback")
        elif exc.audit_worthy:
            _record_sign_in_failure("invalid_auth_state")
        return _auth_failure(status=400)
    except Exception:
        current_app.logger.warning("identity_callback_failed")
        _record_sign_in_failure("identity_unavailable")
        return _auth_failure(status=503)

    try:
        identity = exchange_authorization_code(
            _identity_provider(),
            code=code,
            logger=current_app.logger,
        )
        with _session_factory().begin() as session:
            account = link_verified_identity(session, identity)
            if transaction.invitation_id is not None:
                accept_invitation(
                    session,
                    invitation_id=transaction.invitation_id,
                    account_id=account.id,
                )
            institutional_resolution = None
            try:
                institutional_resolution = resolve_verified_institutional_membership(
                    session,
                    account_id=account.id,
                )
            except OrganizationFailure as exc:
                if exc.category != "institutional_domain_required":
                    raise
            memberships = list_active_memberships(
                session,
                account_id=account.id,
            )
            active_organization_id = (
                institutional_resolution.membership.organization_id
                if institutional_resolution is not None
                else (
                    memberships[0].organization_id
                    if len(memberships) == 1
                    else None
                )
            )
            credential = create_account_session(
                session,
                account_id=account.id,
                active_organization_id=active_organization_id,
                sealed_provider_state=identity.sealed_session,
                rotate_cookie_value=request.cookies.get(SESSION_COOKIE_NAME),
            )
            append_security_audit_event(
                session,
                actor_account_id=account.id,
                organization_id=active_organization_id,
                target_type="account",
                target_id=account.id,
                action="identity.sign_in",
                outcome="succeeded",
                reason="authorized",
            )
    except IdentityFailure:
        current_app.logger.warning("identity_callback_failed")
        _record_sign_in_failure("identity_unavailable")
        return _auth_failure(status=503)
    except AccountLinkFailure as exc:
        current_app.logger.warning("identity_callback_failed")
        reason = (
            exc.category
            if exc.category in {"identity_not_verified", "identity_conflict"}
            else "identity_conflict"
        )
        _record_sign_in_failure(reason)
        return _auth_failure(status=400)
    except OrganizationFailure:
        current_app.logger.warning("identity_callback_failed")
        _record_sign_in_failure("identity_conflict")
        return _auth_failure(status=400)
    except Exception:
        current_app.logger.warning("identity_callback_failed")
        _record_sign_in_failure("identity_unavailable")
        return _auth_failure(status=503)

    if not memberships:
        destination = _DEFAULT_RETURN_PATH
    elif return_path == "/?resume=download" and active_organization_id is not None:
        destination = return_path
    elif len(memberships) > 1:
        destination = "/organizations/select"
    else:
        destination = return_path if return_path != _DEFAULT_RETURN_PATH else "/reports"
    response = redirect(destination, code=302)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        credential.cookie_value,
        **session_cookie_options(),
    )
    return response


@auth_blueprint.get("/auth/error")
def error():
    return _auth_failure(status=400)


@auth_blueprint.get("/organizations/none")
def no_membership():
    return render_template("auth_error.html", state="no_membership")


@auth_blueprint.post("/logout")
def logout():
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME, "")
    try:
        with _session_factory().begin() as session:
            prepared = prepare_account_session_logout(
                session,
                cookie_value=cookie_value,
            )
    except Exception:
        current_app.logger.warning("identity_logout_failed")
        return _auth_failure(status=503)

    destination = complete_provider_logout(
        _identity_provider(),
        prepared,
        logger=current_app.logger,
    )
    response = redirect(destination or "/login", code=302)
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        secure=True,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


def register_auth_routes(app, *, identity_provider, session_factory) -> None:
    """Register auth routes and injectable provider/persistence boundaries."""
    app.extensions["roster_scout_identity_provider"] = identity_provider
    app.extensions["roster_scout_auth_session_factory"] = session_factory
    app.session_interface = RosterScoutSessionInterface()
    if auth_blueprint.name not in app.blueprints:
        app.register_blueprint(auth_blueprint)

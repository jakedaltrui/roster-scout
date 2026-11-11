"""Thin Flask composition for provider-neutral passwordless authentication."""

from __future__ import annotations

from flask import Blueprint, current_app, redirect, render_template, request
from flask.sessions import SessionInterface, SessionMixin
from werkzeug.datastructures import CallbackDict

from .accounts import AccountLinkFailure, link_verified_identity
from .authorization import list_active_memberships
from .identity import (
    FakeIdentityProvider,
    IdentityFailure,
    begin_auth_transaction,
    consume_auth_transaction,
    exchange_authorization_code,
)
from .sessions import (
    SESSION_COOKIE_NAME,
    complete_provider_logout,
    create_account_session,
    load_account_session,
    prepare_account_session_logout,
    session_cookie_options,
)


auth_blueprint = Blueprint("auth", __name__)

_DEFAULT_RETURN_PATH = "/organizations/none"
_ALLOWED_RETURN_PATHS = frozenset({
    _DEFAULT_RETURN_PATH,
    "/organizations/select",
    "/reports",
})
_MAX_CALLBACK_VALUE_LENGTH = 512


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


def _auth_failure(*, status: int):
    return render_template("auth_error.html", state="failure"), status


@auth_blueprint.get("/login")
def login():
    try:
        return_path = _return_path(request.args.get("return_to"))
    except IdentityFailure:
        return _auth_failure(status=400)
    return render_template("sign_in.html", return_path=return_path)


@auth_blueprint.get("/auth/start")
def start():
    try:
        return_path = _return_path(request.args.get("return_to"))
        with _session_factory().begin() as session:
            transaction = begin_auth_transaction(
                session,
                return_path=return_path,
            )
        destination = _identity_provider().authorization_url(
            state=transaction.state
        )
    except IdentityFailure:
        return _auth_failure(status=400)
    except Exception:
        current_app.logger.warning("identity_start_failed")
        return _auth_failure(status=503)
    return redirect(destination, code=302)


@auth_blueprint.get("/auth/callback")
def callback():
    try:
        state = _bounded_callback_value(request.args.get("state"))
        # Commit consumption before any provider exchange so failed exchanges and
        # retries cannot replay a bearer callback state.
        with _session_factory().begin() as session:
            transaction = consume_auth_transaction(session, state=state)
        return_path = _return_path(transaction.return_path)
        code = _bounded_callback_value(request.args.get("code"))
    except IdentityFailure:
        current_app.logger.warning("identity_callback_failed")
        return _auth_failure(status=400)
    except Exception:
        current_app.logger.warning("identity_callback_failed")
        return _auth_failure(status=503)

    try:
        identity = exchange_authorization_code(
            _identity_provider(),
            code=code,
            logger=current_app.logger,
        )
        with _session_factory().begin() as session:
            account = link_verified_identity(session, identity)
            memberships = list_active_memberships(
                session,
                account_id=account.id,
            )
            active_organization_id = (
                memberships[0].organization_id
                if len(memberships) == 1
                else None
            )
            credential = create_account_session(
                session,
                account_id=account.id,
                active_organization_id=active_organization_id,
                sealed_provider_state=identity.sealed_session,
                rotate_cookie_value=request.cookies.get(SESSION_COOKIE_NAME),
            )
    except IdentityFailure:
        current_app.logger.warning("identity_callback_failed")
        return _auth_failure(status=503)
    except AccountLinkFailure:
        current_app.logger.warning("identity_callback_failed")
        return _auth_failure(status=400)
    except Exception:
        current_app.logger.warning("identity_callback_failed")
        return _auth_failure(status=503)

    if not memberships:
        destination = _DEFAULT_RETURN_PATH
    elif len(memberships) > 1:
        destination = "/organizations/select"
    else:
        destination = "/reports"
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

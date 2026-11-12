"""Protected, active-organization report-library routes."""

from __future__ import annotations

import uuid
from datetime import timedelta

from flask import Blueprint, current_app, g, redirect, render_template, request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from roster_scraper import NcaaError, list_ncaa_teams

from .jobs import drain_report_dispatch_outbox
from .models import Account, Organization
from .reports import (
    CanonicalReportRequest,
    ReportAcceptanceFailure,
    ReportLibraryFailure,
    accept_authorized_report_request,
    load_report_library,
)
from .tasks import dispatch_report_job


report_blueprint = Blueprint("reports", __name__)
_DIVISIONS = frozenset({"d1", "d2", "d3"})
_SELECTOR_ERROR = "Choose an NCAA team from the list."
_SERVICE_ERROR = "We couldn’t accept the report request. Try again."


def _session_factory():
    configured = current_app.extensions["roster_scout_report_session_factory"]
    return configured if hasattr(configured, "begin") else configured()


def _page_number():
    value = request.args.get("page", "1")
    if not value.isascii() or not value.isdigit() or len(value) > 5:
        raise ReportLibraryFailure("not_found")
    page = int(value)
    if page < 1:
        raise ReportLibraryFailure("not_found")
    return page


def _shell_context(session, authorization_context):
    organization = session.scalar(
        select(Organization).where(
            Organization.id == authorization_context.organization_id
        )
    )
    account = session.scalar(
        select(Account).where(Account.id == authorization_context.account_id)
    )
    if organization is None:
        raise ReportLibraryFailure("not_found")
    return {
        "organization_name": organization.name,
        "current_role": authorization_context.role,
        "account_email": account.normalized_email if account else None,
    }


def _canonical_action_id(value):
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("invalid action")
    parsed = uuid.UUID(value)
    if str(parsed) != value:
        raise ValueError("invalid action")
    return value


def _selected_catalog_team():
    division = (request.form.get("division") or "").strip().lower()
    season_team_id = (request.form.get("season_team_id") or "").strip()
    submitted_name = (request.form.get("team_name") or "").strip()
    if (
        division not in _DIVISIONS
        or not season_team_id.isascii()
        or not season_team_id.isdigit()
        or len(season_team_id) > 100
        or not submitted_name
        or len(submitted_name) > 200
    ):
        raise ValueError("invalid selector")

    matches = [
        team for team in list_ncaa_teams(division)
        if team.get("division") == division
        and str(team.get("season_team_id") or "") == season_team_id
    ]
    if len(matches) != 1:
        raise ValueError("invalid selector")
    team = matches[0]
    canonical_name = str(team.get("display_name") or team.get("name") or "").strip()
    if not canonical_name or submitted_name != canonical_name:
        raise ValueError("invalid selector")

    year = team.get("year")
    if (
        not isinstance(year, int)
        or isinstance(year, bool)
        or not 1900 <= year <= 2200
    ):
        raise ValueError("invalid selector")
    source_team_id = str(team.get("team_id") or season_team_id)
    return CanonicalReportRequest(
        source_team_id=source_team_id,
        team_name=canonical_name,
        source_season_id=str(year),
        season_year=year,
        source_season_team_id=season_team_id,
        division=division,
    )


def _render_create_report(
    *,
    shell,
    status=200,
    error=None,
    selected_division="d1",
    team_name="",
    season_team_id="",
    idempotency_key=None,
):
    return render_template(
        "report_new.html",
        **shell,
        error=error,
        selected_division=(
            selected_division if selected_division in _DIVISIONS else "d1"
        ),
        team_name=team_name[:200],
        season_team_id=season_team_id[:100],
        idempotency_key=idempotency_key or str(uuid.uuid4()),
    ), status


@report_blueprint.get("/reports")
def library():
    """Render the active organization's shared, persistent report library."""
    context = g.authorization_context
    try:
        page_number = _page_number()
        with _session_factory().begin() as session:
            shell = _shell_context(session, context)
            library_page = load_report_library(
                session,
                context.organization_id,
                page=page_number,
            )
    except ReportLibraryFailure:
        return render_template(
            "invitation_outcome.html",
            outcome="not_found",
            organization_name=None,
            current_role=context.role,
            account_email=None,
        ), 404
    except (SQLAlchemyError, RuntimeError):
        return render_template(
            "report_library.html",
            organization_name=None,
            current_role=context.role,
            account_email=None,
            library_page=None,
            load_error=True,
        ), 503
    return render_template(
        "report_library.html",
        **shell,
        library_page=library_page,
        load_error=False,
    )


@report_blueprint.route("/reports/new", methods=("GET", "POST"))
def create_report():
    """Accept one exact, organization-scoped NCAA report request."""
    context = g.authorization_context
    selected_division = (request.form.get("division") or "d1").strip().lower()
    team_name = (request.form.get("team_name") or "").strip()
    season_team_id = (request.form.get("season_team_id") or "").strip()
    submitted_action_id = (request.form.get("idempotency_key") or "").strip()

    try:
        with _session_factory().begin() as session:
            shell = _shell_context(session, context)
    except (ReportLibraryFailure, SQLAlchemyError, RuntimeError):
        return render_template(
            "report_new.html",
            organization_name=None,
            current_role=context.role,
            account_email=None,
            error=_SERVICE_ERROR,
            selected_division="d1",
            team_name="",
            season_team_id="",
            idempotency_key=str(uuid.uuid4()),
        ), 503

    if request.method == "GET":
        return _render_create_report(shell=shell)

    try:
        action_id = _canonical_action_id(submitted_action_id)
        canonical_request = _selected_catalog_team()
    except (NcaaError, ValueError, TypeError):
        try:
            safe_action_id = _canonical_action_id(submitted_action_id)
        except (ValueError, TypeError, AttributeError):
            safe_action_id = str(uuid.uuid4())
        return _render_create_report(
            shell=shell,
            status=400,
            error=_SELECTOR_ERROR,
            selected_division=selected_division,
            team_name=team_name,
            season_team_id="",
            idempotency_key=safe_action_id,
        )

    try:
        with _session_factory().begin() as session:
            accepted = accept_authorized_report_request(
                session,
                actor_account_id=context.account_id,
                organization_id=context.organization_id,
                request=canonical_request,
                idempotency_key=action_id,
            )
    except ReportAcceptanceFailure:
        return _render_create_report(
            shell=shell,
            status=403,
            error=_SERVICE_ERROR,
            selected_division=selected_division,
            team_name=team_name,
            season_team_id="",
            idempotency_key=action_id,
        )
    except (SQLAlchemyError, RuntimeError, ValueError):
        return _render_create_report(
            shell=shell,
            status=503,
            error=_SERVICE_ERROR,
            selected_division=selected_division,
            team_name=team_name,
            season_team_id="",
            idempotency_key=action_id,
        )

    try:
        drain_report_dispatch_outbox(
            _session_factory(),
            dispatch=current_app.extensions["roster_scout_report_dispatch"],
            limit=1,
            failure_backoff=timedelta(0),
            job_id=accepted.job_id,
        )
    except (SQLAlchemyError, RuntimeError):
        current_app.logger.warning(
            "Report dispatch remains queued after request acceptance."
        )
    destination = f"/reports/{accepted.report_id}"
    if not accepted.created_report:
        destination += "?notice=active"
    return redirect(destination, code=303)


def register_report_routes(app, *, session_factory=None):
    app.extensions["roster_scout_report_session_factory"] = (
        session_factory
        or app.extensions.get("roster_scout_auth_session_factory")
    )
    app.extensions.setdefault("roster_scout_report_dispatch", dispatch_report_job)
    app.register_blueprint(report_blueprint)

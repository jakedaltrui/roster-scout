"""Protected, active-organization report-library routes."""

from __future__ import annotations

from hashlib import sha256
from hmac import compare_digest
from io import BytesIO
import uuid
from datetime import timedelta

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from roster_scraper import NcaaError, list_ncaa_teams

from .audit import append_security_audit_event
from .authorization import record_scoped_not_found
from .jobs import drain_report_dispatch_outbox
from .models import Account, Organization
from .reports import (
    CanonicalReportRequest,
    ReportAcceptanceFailure,
    ReportLibraryFailure,
    accept_authorized_report_refresh,
    accept_authorized_report_request,
    accept_authorized_report_retry,
    load_report_detail,
    load_report_library,
    load_report_status,
    resolve_authorized_artifact,
)
from .tasks import dispatch_report_job


report_blueprint = Blueprint("reports", __name__)
_DIVISIONS = frozenset({"d1", "d2", "d3"})
_SELECTOR_ERROR = "Choose an NCAA team from the list."
_SERVICE_ERROR = "We couldn’t accept the report request. Try again."
_ARTIFACT_ERROR = "The saved workbook is temporarily unavailable. Try again."
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


def _unconfigured_artifact_reader(_object_key):
    raise RuntimeError("artifact reader is not configured")


def _session_factory():
    configured = current_app.extensions["roster_scout_report_session_factory"]
    return configured if hasattr(configured, "begin") else configured()


def _record_not_found(context, *, target_type, target_id=None):
    """Best-effort append of one fixed denial without changing its response."""
    try:
        with _session_factory().begin() as session:
            record_scoped_not_found(
                session,
                context=context,
                target_type=target_type,
                target_id=target_id,
            )
    except (SQLAlchemyError, RuntimeError, ValueError):
        current_app.logger.warning("authorization_denial_audit_failed")


def _html_not_found(context, *, target_type, target_id=None):
    _record_not_found(
        context,
        target_type=target_type,
        target_id=target_id,
    )
    return render_template(
        "invitation_outcome.html",
        outcome="not_found",
        organization_name=None,
        current_role=context.role,
        account_email=None,
    ), 404


def _json_not_found(context, *, target_type, target_id=None):
    _record_not_found(
        context,
        target_type=target_type,
        target_id=target_id,
    )
    return jsonify(error="not_found"), 404


def _page_number(parameter="page"):
    value = request.args.get(parameter, "1")
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


@report_blueprint.get("/reports/<uuid:report_id>")
def detail(report_id):
    """Render one tenant-scoped report with the saved snapshot kept primary."""
    context = g.authorization_context
    try:
        history_page = _page_number("history_page")
        with _session_factory().begin() as session:
            shell = _shell_context(session, context)
            report_detail = load_report_detail(
                session,
                context.organization_id,
                report_id,
                history_page=history_page,
            )
            if report_detail.latest_snapshot is not None:
                append_security_audit_event(
                    session,
                    actor_account_id=context.account_id,
                    organization_id=context.organization_id,
                    target_type="snapshot",
                    target_id=report_detail.latest_snapshot.id,
                    action="snapshot.access",
                    outcome="allowed",
                    reason="snapshot_opened",
                )
    except ReportLibraryFailure:
        return _html_not_found(
            context,
            target_type="report",
            target_id=report_id,
        )
    except (SQLAlchemyError, RuntimeError, ValueError):
        return render_template(
            "report_detail.html",
            organization_name=None,
            current_role=context.role,
            account_email=None,
            report_detail=None,
            load_error=True,
        ), 503
    return render_template(
        "report_detail.html",
        **shell,
        report_detail=report_detail,
        load_error=False,
        retry_action_id=str(uuid.uuid4()),
    )


@report_blueprint.route(
    "/reports/<uuid:report_id>/refresh", methods=("GET", "POST")
)
def refresh_report(report_id):
    """Confirm and atomically accept one explicit tenant-scoped refresh."""
    context = g.authorization_context
    submitted_action_id = (request.form.get("idempotency_key") or "").strip()
    try:
        with _session_factory().begin() as session:
            shell = _shell_context(session, context)
            report_detail = load_report_detail(
                session,
                context.organization_id,
                report_id,
            )
    except ReportLibraryFailure:
        return _html_not_found(
            context,
            target_type="report",
            target_id=report_id,
        )
    except (SQLAlchemyError, RuntimeError, ValueError):
        return render_template(
            "report_detail.html",
            organization_name=None,
            current_role=context.role,
            account_email=None,
            report_detail=None,
            load_error=True,
        ), 503

    if report_detail.active_build is not None:
        return redirect(f"/reports/{report_id}?notice=active", code=303)
    if request.method == "GET":
        return render_template(
            "report_detail.html",
            **shell,
            report_detail=report_detail,
            load_error=False,
            refresh_confirmation=True,
            refresh_action_id=str(uuid.uuid4()),
        )

    try:
        action_id = _canonical_action_id(submitted_action_id)
    except (ValueError, TypeError, AttributeError):
        return render_template(
            "report_detail.html",
            **shell,
            report_detail=report_detail,
            load_error=False,
            refresh_confirmation=True,
            refresh_action_id=str(uuid.uuid4()),
            refresh_error=_SERVICE_ERROR,
        ), 400

    try:
        with _session_factory().begin() as session:
            accepted = accept_authorized_report_refresh(
                session,
                actor_account_id=context.account_id,
                organization_id=context.organization_id,
                report_id=report_id,
                idempotency_key=action_id,
            )
    except ReportLibraryFailure:
        return _html_not_found(
            context,
            target_type="report",
            target_id=report_id,
        )
    except ReportAcceptanceFailure:
        return _html_not_found(
            context,
            target_type="report",
            target_id=report_id,
        )
    except (SQLAlchemyError, RuntimeError, ValueError):
        return render_template(
            "report_detail.html",
            **shell,
            report_detail=report_detail,
            load_error=False,
            refresh_confirmation=True,
            refresh_action_id=action_id,
            refresh_error=_SERVICE_ERROR,
        ), 503

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
            "Report dispatch remains queued after refresh acceptance."
        )
    notice = "refresh_started" if accepted.created_job else "active"
    return redirect(f"/reports/{accepted.report_id}?notice={notice}", code=303)


@report_blueprint.post(
    "/reports/<uuid:report_id>/builds/<uuid:failed_job_id>/retry"
)
def retry_report(report_id, failed_job_id):
    """Accept one new attempt while preserving the selected failed attempt."""
    context = g.authorization_context
    submitted_action_id = (request.form.get("idempotency_key") or "").strip()
    try:
        with _session_factory().begin() as session:
            shell = _shell_context(session, context)
            report_detail = load_report_detail(
                session,
                context.organization_id,
                report_id,
            )
    except ReportLibraryFailure:
        return _html_not_found(
            context,
            target_type="report",
            target_id=report_id,
        )
    except (SQLAlchemyError, RuntimeError, ValueError):
        return render_template(
            "report_detail.html",
            organization_name=None,
            current_role=context.role,
            account_email=None,
            report_detail=None,
            load_error=True,
        ), 503

    try:
        action_id = _canonical_action_id(submitted_action_id)
    except (ValueError, TypeError, AttributeError):
        return render_template(
            "report_detail.html",
            **shell,
            report_detail=report_detail,
            load_error=False,
            retry_error=_SERVICE_ERROR,
            retry_action_id=str(uuid.uuid4()),
        ), 400

    try:
        with _session_factory().begin() as session:
            accepted = accept_authorized_report_retry(
                session,
                actor_account_id=context.account_id,
                organization_id=context.organization_id,
                report_id=report_id,
                failed_job_id=failed_job_id,
                idempotency_key=action_id,
            )
    except (ReportLibraryFailure, ReportAcceptanceFailure):
        return _html_not_found(
            context,
            target_type="job",
            target_id=failed_job_id,
        )
    except (SQLAlchemyError, RuntimeError, ValueError):
        return render_template(
            "report_detail.html",
            **shell,
            report_detail=report_detail,
            load_error=False,
            retry_error=_SERVICE_ERROR,
            retry_action_id=action_id,
        ), 503

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
            "Report dispatch remains queued after retry acceptance."
        )
    notice = "retry_started" if accepted.created_job else "active"
    return redirect(f"/reports/{accepted.report_id}?notice={notice}", code=303)


@report_blueprint.post(
    "/reports/<uuid:report_id>/snapshots/<uuid:snapshot_id>"
    "/artifacts/<uuid:artifact_id>/download"
)
def download_snapshot_artifact(report_id, snapshot_id, artifact_id):
    """Prepare and stream only one exact, already-stored historical workbook."""
    context = g.authorization_context
    try:
        with _session_factory().begin() as session:
            artifact = resolve_authorized_artifact(
                session,
                organization_id=context.organization_id,
                report_id=report_id,
                snapshot_id=snapshot_id,
                artifact_id=artifact_id,
            )
            payload = current_app.extensions[
                "roster_scout_artifact_reader"
            ](artifact.object_key)
            if (
                not isinstance(payload, bytes)
                or not payload
                or len(payload) > _MAX_ARTIFACT_BYTES
                or not compare_digest(sha256(payload).hexdigest(), artifact.sha256)
            ):
                raise RuntimeError("stored artifact failed integrity validation")
            download_event_id = str(uuid.uuid4())
            proof = current_app.extensions[
                "roster_scout_download_ack_proof_issuer"
            ](
                download_event_id,
                account_id=context.account_id,
                organization_id=context.organization_id,
                report_id=report_id,
                snapshot_id=snapshot_id,
                artifact_id=artifact.id,
            )
            if (
                not isinstance(proof, str)
                or not proof
                or len(proof) > 512
                or any(ord(character) < 32 for character in proof)
            ):
                raise RuntimeError("download acknowledgement proof is invalid")
            append_security_audit_event(
                session,
                actor_account_id=context.account_id,
                organization_id=context.organization_id,
                target_type="artifact",
                target_id=artifact.id,
                action="artifact.download",
                outcome="pending",
                reason="download_prepared",
            )
    except ReportLibraryFailure:
        return _html_not_found(
            context,
            target_type="artifact",
            target_id=artifact_id,
        )
    except Exception:
        current_app.logger.warning(
            "artifact_download_unavailable reason=storage_or_report_service"
        )
        return jsonify(error="artifact_unavailable", message=_ARTIFACT_ERROR), 503

    response = send_file(
        BytesIO(payload),
        mimetype=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        as_attachment=True,
        download_name="roster-scout-snapshot.xlsx",
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Roster-Scout-Download-Event-ID"] = download_event_id
    response.headers["X-Roster-Scout-Download-Ack-Proof"] = proof
    return response


@report_blueprint.get("/reports/<uuid:report_id>/status")
def detail_status(report_id):
    """Return only a fixed, safe status view model for the active tenant."""
    context = g.authorization_context
    try:
        with _session_factory().begin() as session:
            status = load_report_status(
                session,
                context.organization_id,
                report_id,
            )
    except ReportLibraryFailure:
        return _json_not_found(
            context,
            target_type="report",
            target_id=report_id,
        )
    except (SQLAlchemyError, RuntimeError, ValueError):
        return jsonify(error="report_service_unavailable"), 503
    return jsonify(status.as_dict())


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


def register_report_routes(
    app,
    *,
    session_factory=None,
    issue_download_ack_proof=None,
    artifact_reader=None,
):
    app.extensions["roster_scout_report_session_factory"] = (
        session_factory
        or app.extensions.get("roster_scout_auth_session_factory")
    )
    app.extensions.setdefault("roster_scout_report_dispatch", dispatch_report_job)
    if issue_download_ack_proof is not None:
        app.extensions["roster_scout_download_ack_proof_issuer"] = (
            issue_download_ack_proof
        )
    app.extensions.setdefault(
        "roster_scout_artifact_reader",
        artifact_reader or _unconfigured_artifact_reader,
    )
    app.register_blueprint(report_blueprint)

"""Protected, active-organization report-library routes."""

from __future__ import annotations

from flask import Blueprint, current_app, g, render_template, request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .models import Account, Organization
from .reports import ReportLibraryFailure, load_report_library


report_blueprint = Blueprint("reports", __name__)


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


def register_report_routes(app, *, session_factory=None):
    app.extensions["roster_scout_report_session_factory"] = (
        session_factory
        or app.extensions.get("roster_scout_auth_session_factory")
    )
    app.register_blueprint(report_blueprint)

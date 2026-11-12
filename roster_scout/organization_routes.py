"""Thin Flask composition for organization selection and administration."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from flask import Blueprint, current_app, g, redirect, render_template, request
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import select

from .audit import append_security_audit_event
from .authorization import AuthorizationFailure, require_admin
from .models import (
    Account,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
)
from .organizations import (
    OrganizationFailure,
    accept_invitation,
    change_membership_role,
    create_invitation,
    deliver_invitation,
    remove_membership,
    revoke_invitation,
)
from .sessions import (
    SESSION_COOKIE_NAME,
    rotate_account_session_organization,
    session_cookie_options,
)


organization_blueprint = Blueprint("organization", __name__)


def _session_factory():
    configured = current_app.extensions["roster_scout_auth_session_factory"]
    return configured if hasattr(configured, "begin") else configured()


def _identity_provider():
    return current_app.extensions["roster_scout_identity_provider"]


def _membership_serializer():
    return URLSafeTimedSerializer(
        current_app.secret_key,
        salt="organization-membership-candidate",
    )


def _membership_token(membership_id):
    return _membership_serializer().dumps(str(membership_id))


def _membership_candidate(value):
    if not isinstance(value, str) or not value or len(value) > 500:
        raise OrganizationFailure("validation_failed")
    try:
        decoded = _membership_serializer().loads(value, max_age=3600)
        return uuid.UUID(str(decoded))
    except (BadData, ValueError, TypeError, AttributeError):
        try:
            return uuid.UUID(value)
        except (ValueError, TypeError, AttributeError):
            raise OrganizationFailure("validation_failed") from None


def _admin_context():
    try:
        return require_admin(g.authorization_context)
    except (AttributeError, AuthorizationFailure):
        return None


def _members_view_model(context):
    with _session_factory().begin() as session:
        organization = session.get(Organization, context.organization_id)
        if organization is None:
            raise OrganizationFailure("not_found")
        member_rows = list(session.execute(
            select(OrganizationMembership, Account)
            .join(Account, Account.id == OrganizationMembership.account_id)
            .where(
                OrganizationMembership.organization_id == context.organization_id,
                OrganizationMembership.status == "active",
            )
            .order_by(Account.normalized_email, OrganizationMembership.id)
        ))
        invitation_rows = list(session.execute(
            select(OrganizationInvitation, Account)
            .outerjoin(
                Account,
                Account.id == OrganizationInvitation.invited_by_account_id,
            )
            .where(
                OrganizationInvitation.organization_id == context.organization_id,
                OrganizationInvitation.status == "pending",
                OrganizationInvitation.expires_at > datetime.now(UTC),
            )
            .order_by(
                OrganizationInvitation.created_at,
                OrganizationInvitation.id,
            )
        ))
        members = [
            {
                "token": _membership_token(membership.id),
                "email": account.normalized_email,
                "role": membership.role,
                "joined_at": membership.created_at,
                "is_current": membership.account_id == context.account_id,
            }
            for membership, account in member_rows
        ]
        invitations = [
            {
                "id": invitation.id,
                "email": invitation.normalized_email,
                "role": invitation.role,
                "invited_by": (
                    inviter.normalized_email if inviter is not None else "Roster Scout"
                ),
                "created_at": invitation.created_at,
                "expires_at": invitation.expires_at,
                "status": invitation.status,
            }
            for invitation, inviter in invitation_rows
        ]
        account = session.get(Account, context.account_id)
        return {
            "organization_name": organization.name,
            "current_role": context.role,
            "account_email": account.normalized_email if account else None,
            "members": members,
            "invitations": invitations,
        }


def _selection_view_model(loaded):
    with _session_factory().begin() as session:
        rows = list(session.execute(
            select(OrganizationMembership, Organization)
            .join(
                Organization,
                Organization.id == OrganizationMembership.organization_id,
            )
            .where(
                OrganizationMembership.account_id == loaded.account_id,
                OrganizationMembership.status == "active",
            )
            .order_by(Organization.name, OrganizationMembership.id)
        ))
        account = session.get(Account, loaded.account_id)
    organizations = [
        {
            "token": _membership_token(membership.id),
            "name": organization.name,
            "role": membership.role,
            "is_current": (
                membership.organization_id == loaded.active_organization_id
            ),
        }
        for membership, organization in rows
    ]
    current = next(
        (item for item in organizations if item["is_current"]),
        None,
    )
    return {
        "organizations": organizations,
        "organization_name": current["name"] if current else None,
        "current_role": current["role"] if current else None,
        "account_email": account.normalized_email if account else None,
    }


def _render_selection(loaded, *, status=200, error=None):
    return (
        render_template(
            "organization_select.html",
            **_selection_view_model(loaded),
            error=error,
        ),
        status,
    )


def _render_members(
    context,
    *,
    status=200,
    banner=None,
    banner_error=False,
    form_error=False,
):
    try:
        view = _members_view_model(context)
    except OrganizationFailure:
        return _not_found()
    return (
        render_template(
            "organization_members.html",
            **view,
            banner=banner,
            banner_error=banner_error,
            form_error=form_error,
        ),
        status,
    )


def _permission_denied():
    return render_template(
        "organization_members.html",
        permission_denied=True,
        current_role="member",
        organization_name=None,
        account_email=None,
        members=(),
        invitations=(),
        banner=None,
        banner_error=False,
        form_error=False,
    ), 403


def _not_found():
    return render_template(
        "invitation_outcome.html",
        outcome="not_found",
        organization_name=None,
        current_role=None,
        account_email=None,
    ), 404


@organization_blueprint.get("/organization/members")
def members():
    context = _admin_context()
    if context is None:
        return _permission_denied()
    return _render_members(context)


@organization_blueprint.get("/organizations/select")
def select_organization():
    loaded = getattr(g, "account_session", None)
    if loaded is None:
        return _not_found()
    return _render_selection(loaded)


@organization_blueprint.post("/organizations/switch")
def switch_organization():
    loaded = getattr(g, "account_session", None)
    if loaded is None:
        return _not_found()
    try:
        membership_id = _membership_candidate(
            request.form.get("membership_id", "")
        )
        with _session_factory().begin() as session:
            membership = session.scalar(
                select(OrganizationMembership).where(
                    OrganizationMembership.id == membership_id,
                    OrganizationMembership.account_id == loaded.account_id,
                    OrganizationMembership.status == "active",
                )
            )
            if membership is None:
                raise OrganizationFailure("not_found")
            credential = rotate_account_session_organization(
                session,
                cookie_value=request.cookies.get(SESSION_COOKIE_NAME, ""),
                active_organization_id=membership.organization_id,
            )
            append_security_audit_event(
                session,
                actor_account_id=loaded.account_id,
                organization_id=membership.organization_id,
                target_type="membership",
                target_id=membership.id,
                action="organization.switch",
                outcome="succeeded",
                reason="authorized",
            )
    except (OrganizationFailure, ValueError):
        return _render_selection(
            loaded,
            status=404,
            error="That organization is no longer available.",
        )

    response = redirect("/reports", code=302)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        credential.cookie_value,
        **session_cookie_options(),
    )
    return response


@organization_blueprint.post("/organization/members/role")
def change_role():
    context = _admin_context()
    if context is None:
        return _permission_denied()
    try:
        membership_id = _membership_candidate(
            request.form.get("membership_id", "")
        )
        with _session_factory().begin() as session:
            membership = change_membership_role(
                session,
                organization_id=context.organization_id,
                actor_account_id=context.account_id,
                membership_id=membership_id,
                role=request.form.get("role", ""),
            )
            changed_current_admin = (
                membership.account_id == context.account_id
                and membership.role != "admin"
            )
    except OrganizationFailure as exc:
        if exc.category == "forbidden":
            return _permission_denied()
        if exc.category == "last_admin":
            return _render_members(
                context,
                status=409,
                banner="Every organization must keep at least one admin.",
                banner_error=True,
            )
        if exc.category == "not_found":
            return _not_found()
        return _render_members(
            context,
            status=400,
            banner="Choose a valid role and try again.",
            banner_error=True,
        )
    if changed_current_admin:
        return redirect("/reports", code=302)
    return _render_members(context, banner="Member role updated.")


@organization_blueprint.post("/organization/members/remove")
def remove_member():
    context = _admin_context()
    if context is None:
        return _permission_denied()
    try:
        membership_id = _membership_candidate(
            request.form.get("membership_id", "")
        )
        with _session_factory().begin() as session:
            membership = remove_membership(
                session,
                organization_id=context.organization_id,
                actor_account_id=context.account_id,
                membership_id=membership_id,
            )
            removed_current_admin = membership.account_id == context.account_id
    except OrganizationFailure as exc:
        if exc.category == "forbidden":
            return _permission_denied()
        if exc.category == "last_admin":
            return _render_members(
                context,
                status=409,
                banner="Every organization must keep at least one admin.",
                banner_error=True,
            )
        if exc.category == "not_found":
            return _not_found()
        return _render_members(
            context,
            status=400,
            banner="That member could not be removed.",
            banner_error=True,
        )
    if removed_current_admin:
        return redirect("/organizations/select", code=302)
    return _render_members(context, banner="Member removed.")


@organization_blueprint.post("/organization/invitations")
def invite():
    context = _admin_context()
    if context is None:
        return _permission_denied()

    try:
        with _session_factory().begin() as session:
            invitation = create_invitation(
                session,
                organization_id=context.organization_id,
                actor_account_id=context.account_id,
                email=request.form.get("email", ""),
                role=request.form.get("role", ""),
            )
            invitation_id = invitation.id
            normalized_email = invitation.normalized_email
            expires_at = invitation.expires_at
    except OrganizationFailure as exc:
        if exc.category == "invitation_exists":
            return _render_members(
                context,
                status=409,
                banner="An active invitation already exists for this email.",
                banner_error=True,
            )
        if exc.category == "forbidden":
            return _permission_denied()
        return _render_members(
            context,
            status=400,
            form_error=True,
        )

    try:
        deliver_invitation(
            _session_factory(),
            provider=_identity_provider(),
            invitation_id=invitation_id,
            logger=current_app.logger,
        )
    except OrganizationFailure:
        return _render_members(
            context,
            status=503,
            banner=(
                "Invitation sign-in is temporarily unavailable. "
                "Try again in a few minutes. The invitation has not been used."
            ),
            banner_error=True,
        )

    expiry = expires_at.astimezone(UTC).strftime("%b %-d, %Y at %-I:%M %p UTC")
    return _render_members(
        context,
        status=201,
        banner=f"Invitation sent to {normalized_email}. It expires {expiry}.",
    )


@organization_blueprint.post(
    "/organization/invitations/<uuid:invitation_id>/revoke"
)
def revoke(invitation_id: uuid.UUID):
    context = _admin_context()
    if context is None:
        return _permission_denied()
    try:
        with _session_factory().begin() as session:
            scoped = session.scalar(select(OrganizationInvitation.id).where(
                OrganizationInvitation.id == invitation_id,
                OrganizationInvitation.organization_id == context.organization_id,
            ))
            if scoped is None:
                raise OrganizationFailure("not_found")
            revoke_invitation(
                session,
                invitation_id=invitation_id,
                actor_account_id=context.account_id,
            )
    except OrganizationFailure as exc:
        if exc.category == "forbidden":
            return _permission_denied()
        return _not_found()
    return _render_members(context, banner="Invitation revoked.")


@organization_blueprint.post("/invitations/<uuid:invitation_id>/accept")
def accept(invitation_id: uuid.UUID):
    loaded = getattr(g, "account_session", None)
    if loaded is None:
        return _not_found()
    try:
        with _session_factory().begin() as session:
            membership = accept_invitation(
                session,
                invitation_id=invitation_id,
                account_id=loaded.account_id,
            )
            organization = session.get(Organization, membership.organization_id)
            organization_name = organization.name if organization is not None else None
    except OrganizationFailure as exc:
        outcome = (
            "email_mismatch"
            if exc.category == "invitation_email_mismatch"
            else "unavailable"
        )
        return render_template(
            "invitation_outcome.html",
            outcome=outcome,
            organization_name=None,
            current_role=None,
            account_email=None,
        ), 409
    return render_template(
        "invitation_outcome.html",
        outcome="accepted",
        organization_name=organization_name,
        current_role=membership.role,
        account_email=None,
    )


def register_organization_routes(app) -> None:
    if organization_blueprint.name not in app.blueprints:
        app.register_blueprint(organization_blueprint)

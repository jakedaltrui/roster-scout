"""Thin Flask composition for canonical organization invitations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from flask import Blueprint, current_app, g, render_template, request
from sqlalchemy import select

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
    create_invitation,
    deliver_invitation,
    revoke_invitation,
)


organization_blueprint = Blueprint("organization", __name__)


def _session_factory():
    configured = current_app.extensions["roster_scout_auth_session_factory"]
    return configured if hasattr(configured, "begin") else configured()


def _identity_provider():
    return current_app.extensions["roster_scout_identity_provider"]


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

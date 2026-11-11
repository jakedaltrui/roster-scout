"""Add exact-address invitations, provider mirrors, and security audit events.

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organization_identity_connections",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("provider_organization_id", sa.String(length=255), nullable=False),
        sa.Column("mirrored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_org_identity_connections_organization",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_identity_connections"),
        sa.UniqueConstraint(
            "organization_id", name="uq_org_identity_connections_organization"
        ),
        sa.UniqueConstraint(
            "provider_organization_id",
            name="uq_org_identity_connections_provider_organization",
        ),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_org_identity_connections_id_org"
        ),
    )
    op.create_table(
        "organization_invitations",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("invited_by_account_id", sa.Uuid(), nullable=True),
        sa.Column("accepted_account_id", sa.Uuid(), nullable=True),
        sa.Column("provider_invitation_id", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('admin', 'member')",
            name="ck_organization_invitations_role",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked', 'expired')",
            name="ck_organization_invitations_status",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_organization_invitations_expiry",
        ),
        sa.CheckConstraint(
            "(status = 'accepted' AND accepted_account_id IS NOT NULL "
            "AND accepted_at IS NOT NULL AND revoked_at IS NULL) OR "
            "(status = 'revoked' AND revoked_at IS NOT NULL "
            "AND accepted_account_id IS NULL AND accepted_at IS NULL) OR "
            "(status IN ('pending', 'expired') AND accepted_account_id IS NULL "
            "AND accepted_at IS NULL AND revoked_at IS NULL)",
            name="ck_organization_invitations_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_organization_invitations_organization",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_account_id", "organization_id"],
            [
                "organization_memberships.account_id",
                "organization_memberships.organization_id",
            ],
            name="fk_organization_invitations_inviter_membership",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_account_id", "organization_id"],
            [
                "organization_memberships.account_id",
                "organization_memberships.organization_id",
            ],
            name="fk_organization_invitations_accepted_membership",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_invitations"),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_organization_invitations_id_org"
        ),
    )
    op.create_index(
        "uq_organization_invitations_active_email",
        "organization_invitations",
        ["organization_id", "normalized_email"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "uq_organization_invitations_provider_id",
        "organization_invitations",
        ["provider_invitation_id"],
        unique=True,
        postgresql_where=sa.text("provider_invitation_id IS NOT NULL"),
    )
    op.create_index(
        "ix_organization_invitations_org_status",
        "organization_invitations",
        ["organization_id", "status"],
    )
    op.create_table(
        "security_audit_events",
        sa.Column("actor_account_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "target_type IN ('account', 'organization', 'invitation', "
            "'membership', 'report', 'job', 'snapshot', 'artifact', 'session')",
            name="ck_security_audit_events_target_type",
        ),
        sa.CheckConstraint(
            "action IN ('identity.sign_in', 'organization.bootstrap', "
            "'organization.switch', 'invitation.create', 'invitation.deliver', "
            "'invitation.accept', 'invitation.revoke', 'membership.create', "
            "'membership.remove', 'membership.role_change', 'report.create', "
            "'report.refresh', 'report.retry', 'snapshot.access', "
            "'artifact.download', 'authorization.deny')",
            name="ck_security_audit_events_action",
        ),
        sa.CheckConstraint(
            "outcome IN ('allowed', 'denied', 'succeeded', 'failed', 'pending')",
            name="ck_security_audit_events_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["actor_account_id"],
            ["accounts.id"],
            name="fk_security_audit_events_actor",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_security_audit_events_organization",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_security_audit_events"),
    )
    op.create_index(
        "ix_security_audit_events_organization_time",
        "security_audit_events",
        ["organization_id", "occurred_at"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_security_audit_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'security audit events are append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_security_audit_events_append_only
        BEFORE UPDATE OR DELETE ON security_audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_security_audit_event_mutation()
        """
    )
    op.create_foreign_key(
        "fk_auth_transactions_invitation",
        "auth_transactions",
        "organization_invitations",
        ["invitation_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint(
        "fk_auth_transactions_invitation",
        "auth_transactions",
        type_="foreignkey",
    )
    op.execute(
        "DROP TRIGGER trg_security_audit_events_append_only "
        "ON security_audit_events"
    )
    op.execute("DROP FUNCTION reject_security_audit_event_mutation()")
    op.drop_index(
        "ix_security_audit_events_organization_time",
        table_name="security_audit_events",
    )
    op.drop_table("security_audit_events")
    op.drop_index(
        "ix_organization_invitations_org_status",
        table_name="organization_invitations",
    )
    op.drop_index(
        "uq_organization_invitations_provider_id",
        table_name="organization_invitations",
    )
    op.drop_index(
        "uq_organization_invitations_active_email",
        table_name="organization_invitations",
    )
    op.drop_table("organization_invitations")
    op.drop_table("organization_identity_connections")

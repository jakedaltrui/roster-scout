"""Add canonical accounts, external identities, auth state, and sessions.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="active",
            nullable=False,
        ),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_accounts_status",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND disabled_at IS NULL) OR "
            "(status = 'disabled' AND disabled_at IS NOT NULL)",
            name="ck_accounts_disabled_at",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
        sa.UniqueConstraint(
            "normalized_email", name="uq_accounts_normalized_email"
        ),
    )
    op.create_table(
        "external_identities",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("provider_user_id", sa.String(length=255), nullable=False),
        sa.Column("last_verified_email", sa.String(length=320), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_external_identities_account",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_external_identities"),
        sa.UniqueConstraint(
            "issuer",
            "subject",
            name="uq_external_identities_issuer_subject",
        ),
        sa.UniqueConstraint(
            "provider_user_id",
            name="uq_external_identities_provider_user",
        ),
    )
    op.create_index(
        "ix_external_identities_account_id",
        "external_identities",
        ["account_id"],
    )
    op.create_table(
        "auth_transactions",
        sa.Column("state_digest", sa.String(length=64), nullable=False),
        sa.Column("invitation_id", sa.Uuid(), nullable=True),
        sa.Column(
            "return_path",
            sa.String(length=500),
            server_default="/",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "char_length(state_digest) = 64",
            name="ck_auth_transactions_state_digest_length",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_auth_transactions_expiry",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_transactions"),
        sa.UniqueConstraint(
            "state_digest", name="uq_auth_transactions_state_digest"
        ),
    )
    op.create_index(
        "ix_auth_transactions_expires_at",
        "auth_transactions",
        ["expires_at"],
    )
    op.create_table(
        "account_sessions",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("lookup_id", sa.Uuid(), nullable=False),
        sa.Column("verifier_digest", sa.String(length=64), nullable=False),
        sa.Column("sealed_provider_state", sa.Text(), nullable=False),
        sa.Column("csrf_secret", sa.String(length=200), nullable=False),
        sa.Column("rotation_family", sa.Uuid(), nullable=False),
        sa.Column("active_organization_id", sa.Uuid(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(verifier_digest) = 64",
            name="ck_account_sessions_verifier_digest_length",
        ),
        sa.CheckConstraint(
            "idle_expires_at > created_at "
            "AND absolute_expires_at > created_at "
            "AND idle_expires_at <= absolute_expires_at",
            name="ck_account_sessions_expiry",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_account_sessions_account",
        ),
        sa.ForeignKeyConstraint(
            ["active_organization_id"],
            ["organizations.id"],
            name="fk_account_sessions_active_organization",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_sessions"),
        sa.UniqueConstraint("lookup_id", name="uq_account_sessions_lookup_id"),
        sa.UniqueConstraint(
            "verifier_digest", name="uq_account_sessions_verifier_digest"
        ),
    )
    op.create_index(
        "ix_account_sessions_account_id",
        "account_sessions",
        ["account_id"],
    )
    op.create_index(
        "ix_account_sessions_absolute_expires_at",
        "account_sessions",
        ["absolute_expires_at"],
    )


def downgrade():
    op.drop_index(
        "ix_account_sessions_absolute_expires_at",
        table_name="account_sessions",
    )
    op.drop_index("ix_account_sessions_account_id", table_name="account_sessions")
    op.drop_table("account_sessions")
    op.drop_index(
        "ix_auth_transactions_expires_at",
        table_name="auth_transactions",
    )
    op.drop_table("auth_transactions")
    op.drop_index(
        "ix_external_identities_account_id",
        table_name="external_identities",
    )
    op.drop_table("external_identities")
    op.drop_table("accounts")

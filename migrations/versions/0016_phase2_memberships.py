"""Add canonical local organization memberships.

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organization_memberships",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.String(length=16),
            server_default="member",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="active",
            nullable=False,
        ),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('admin', 'member')",
            name="ck_memberships_role",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_memberships_status",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND deactivated_at IS NULL) OR "
            "(status = 'inactive' AND deactivated_at IS NOT NULL)",
            name="ck_memberships_deactivated_at",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_memberships_organization",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_memberships_account",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_memberships"),
        sa.UniqueConstraint(
            "organization_id",
            "account_id",
            name="uq_memberships_organization_account",
        ),
        sa.UniqueConstraint(
            "account_id",
            "organization_id",
            name="uq_memberships_account_organization",
        ),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            name="uq_memberships_id_organization",
        ),
    )
    op.create_index(
        "ix_memberships_account_status",
        "organization_memberships",
        ["account_id", "status"],
    )
    op.create_index(
        "ix_memberships_organization_status_role",
        "organization_memberships",
        ["organization_id", "status", "role"],
    )
    op.drop_constraint(
        "fk_account_sessions_active_organization",
        "account_sessions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_account_sessions_active_membership",
        "account_sessions",
        "organization_memberships",
        ["account_id", "active_organization_id"],
        ["account_id", "organization_id"],
    )


def downgrade():
    op.drop_constraint(
        "fk_account_sessions_active_membership",
        "account_sessions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_account_sessions_active_organization",
        "account_sessions",
        "organizations",
        ["active_organization_id"],
        ["id"],
    )
    op.drop_index(
        "ix_memberships_organization_status_role",
        table_name="organization_memberships",
    )
    op.drop_index(
        "ix_memberships_account_status",
        table_name="organization_memberships",
    )
    op.drop_table("organization_memberships")

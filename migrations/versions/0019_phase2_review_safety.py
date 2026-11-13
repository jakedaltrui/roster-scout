"""Add bounded provider refresh and invitation delivery claims.

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "account_sessions",
        sa.Column("provider_refreshed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE account_sessions SET provider_refreshed_at = created_at "
        "WHERE provider_refreshed_at IS NULL"
    )
    op.alter_column(
        "account_sessions", "provider_refreshed_at", nullable=False
    )

    op.add_column(
        "organization_invitations",
        sa.Column("delivery_claim_token", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "organization_invitations",
        sa.Column("delivery_claimed_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_organization_invitations_delivery_claim",
        "organization_invitations",
        "(delivery_claim_token IS NULL AND delivery_claimed_until IS NULL) OR "
        "(delivery_claim_token IS NOT NULL AND delivery_claimed_until IS NOT NULL)",
    )


def downgrade():
    op.drop_constraint(
        "ck_organization_invitations_delivery_claim",
        "organization_invitations",
        type_="check",
    )
    op.drop_column("organization_invitations", "delivery_claimed_until")
    op.drop_column("organization_invitations", "delivery_claim_token")
    op.drop_column("account_sessions", "provider_refreshed_at")

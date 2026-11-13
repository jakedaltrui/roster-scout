"""Persist invitation delivery recovery start time.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa
from alembic import op


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "organization_invitations",
        sa.Column("delivery_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("organization_invitations", "delivery_started_at")

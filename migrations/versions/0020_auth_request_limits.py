"""Add shared bounded authentication request limits.

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auth_request_limits",
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("client_digest", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope IN ('start', 'callback')",
            name="ck_auth_request_limits_scope",
        ),
        sa.CheckConstraint(
            "char_length(client_digest) = 64",
            name="ck_auth_request_limits_client_digest_length",
        ),
        sa.CheckConstraint(
            "attempt_count > 0",
            name="ck_auth_request_limits_attempt_count",
        ),
        sa.PrimaryKeyConstraint(
            "scope",
            "client_digest",
            name="pk_auth_request_limits",
        ),
    )
    op.create_index(
        "ix_auth_request_limits_updated_at",
        "auth_request_limits",
        ["updated_at"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_auth_request_limits_updated_at",
        table_name="auth_request_limits",
    )
    op.drop_table("auth_request_limits")

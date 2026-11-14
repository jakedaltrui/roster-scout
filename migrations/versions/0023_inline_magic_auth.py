"""Add inline Magic Auth transaction state and limiter scopes.

Revision ID: 0023
Revises: 0022
"""

import sqlalchemy as sa
from alembic import op


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "auth_transactions",
        sa.Column(
            "auth_method",
            sa.String(length=24),
            server_default="authorization_code",
            nullable=False,
        ),
    )
    op.add_column(
        "auth_transactions",
        sa.Column("normalized_email", sa.String(length=320), nullable=True),
    )
    op.create_check_constraint(
        "ck_auth_transactions_method",
        "auth_transactions",
        "(auth_method = 'authorization_code' AND normalized_email IS NULL) OR "
        "(auth_method = 'magic_auth' AND normalized_email IS NOT NULL)",
    )
    op.drop_constraint(
        "ck_auth_request_limits_scope",
        "auth_request_limits",
        type_="check",
    )
    op.create_check_constraint(
        "ck_auth_request_limits_scope",
        "auth_request_limits",
        "scope IN ('start', 'callback', 'magic_start', 'magic_verify')",
    )


def downgrade():
    op.drop_constraint(
        "ck_auth_request_limits_scope",
        "auth_request_limits",
        type_="check",
    )
    op.execute(
        "DELETE FROM auth_request_limits "
        "WHERE scope IN ('magic_start', 'magic_verify')"
    )
    op.create_check_constraint(
        "ck_auth_request_limits_scope",
        "auth_request_limits",
        "scope IN ('start', 'callback')",
    )
    op.drop_constraint(
        "ck_auth_transactions_method",
        "auth_transactions",
        type_="check",
    )
    op.drop_column("auth_transactions", "normalized_email")
    op.drop_column("auth_transactions", "auth_method")

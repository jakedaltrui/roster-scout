"""Add durable workbook artifact retry state.

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa
from alembic import op


revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "report_artifacts",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "report_artifacts",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "report_artifacts",
        sa.Column("failure_category", sa.String(length=100), nullable=True),
    )
    op.create_check_constraint(
        "ck_report_artifacts_attempt_count",
        "report_artifacts",
        "attempt_count >= 0 AND attempt_count <= 10",
    )


def downgrade():
    op.drop_constraint(
        "ck_report_artifacts_attempt_count",
        "report_artifacts",
        type_="check",
    )
    op.drop_column("report_artifacts", "failure_category")
    op.drop_column("report_artifacts", "last_attempt_at")
    op.drop_column("report_artifacts", "attempt_count")

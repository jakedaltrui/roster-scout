"""Persist transactional failure-evidence purge health.

Revision ID: 0009
Revises: 0008
"""

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evidence_purge_state",
        sa.Column(
            "singleton_key",
            sa.SmallInteger(),
            primary_key=True,
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "last_success_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_purged_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "singleton_key = 1",
            name="ck_evidence_purge_state_singleton",
        ),
        sa.CheckConstraint(
            "last_purged_count >= 0",
            name="ck_evidence_purge_state_count_nonnegative",
        ),
    )


def downgrade():
    op.drop_table("evidence_purge_state")

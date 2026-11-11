"""Create the privacy-conscious product usage event ledger.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "product_usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("event_key", sa.String(length=200), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("actor_account_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "event_type IN ('authenticated_use', 'workbook_download_served')",
            name="ck_product_usage_events_type",
        ),
        sa.CheckConstraint(
            "(actor_account_id IS NULL) = (organization_id IS NULL)",
            name="ck_product_usage_events_actor_scope",
        ),
        sa.CheckConstraint(
            "event_type <> 'authenticated_use' "
            "OR (actor_account_id IS NOT NULL AND organization_id IS NOT NULL)",
            name="ck_product_usage_events_authenticated_scope",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_product_usage_events_organization",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_product_usage_events"),
        sa.UniqueConstraint(
            "event_type",
            "event_key",
            name="uq_product_usage_events_type_key",
        ),
    )
    op.create_index(
        "ix_product_usage_events_occurred_at",
        "product_usage_events",
        ["occurred_at"],
    )
    op.create_index(
        "ix_product_usage_events_organization_occurred_at",
        "product_usage_events",
        ["organization_id", "occurred_at"],
    )


def downgrade():
    op.drop_index(
        "ix_product_usage_events_organization_occurred_at",
        table_name="product_usage_events",
    )
    op.drop_index(
        "ix_product_usage_events_occurred_at",
        table_name="product_usage_events",
    )
    op.drop_table("product_usage_events")

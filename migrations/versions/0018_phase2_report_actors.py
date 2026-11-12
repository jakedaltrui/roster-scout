"""Bind report acceptance records to canonical local actors.

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "report_snapshots",
        sa.Column(
            "created_by_account_id",
            sa.Uuid(),
            server_default=sa.text(
                "NULLIF('00000000-0000-0000-0000-000000000000'::uuid, "
                "'00000000-0000-0000-0000-000000000000'::uuid)"
            ),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_report_snapshots_creator_account",
        "report_snapshots",
        "accounts",
        ["created_by_account_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_report_snapshots_creator_membership",
        "report_snapshots",
        "organization_memberships",
        ["created_by_account_id", "organization_id"],
        ["account_id", "organization_id"],
    )

    op.add_column(
        "report_jobs",
        sa.Column(
            "requested_by_account_id",
            sa.Uuid(),
            server_default=sa.text(
                "NULLIF('00000000-0000-0000-0000-000000000000'::uuid, "
                "'00000000-0000-0000-0000-000000000000'::uuid)"
            ),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_report_jobs_requester_account",
        "report_jobs",
        "accounts",
        ["requested_by_account_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_report_jobs_requester_membership",
        "report_jobs",
        "organization_memberships",
        ["requested_by_account_id", "organization_id"],
        ["account_id", "organization_id"],
    )

    op.create_foreign_key(
        "fk_product_usage_events_actor",
        "product_usage_events",
        "accounts",
        ["actor_account_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_product_usage_events_actor_membership",
        "product_usage_events",
        "organization_memberships",
        ["actor_account_id", "organization_id"],
        ["account_id", "organization_id"],
    )


def downgrade():
    op.drop_constraint(
        "fk_product_usage_events_actor_membership",
        "product_usage_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_product_usage_events_actor",
        "product_usage_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_report_jobs_requester_membership",
        "report_jobs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_report_jobs_requester_account",
        "report_jobs",
        type_="foreignkey",
    )
    op.drop_column("report_jobs", "requested_by_account_id")
    op.drop_constraint(
        "fk_report_snapshots_creator_membership",
        "report_snapshots",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_report_snapshots_creator_account",
        "report_snapshots",
        type_="foreignkey",
    )
    op.drop_column("report_snapshots", "created_by_account_id")

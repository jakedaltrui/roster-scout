"""Add fast season builds followed by detailed successor builds.

Revision ID: 0028
Revises: 0027
"""

from alembic import op
import sqlalchemy as sa


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "report_jobs",
        sa.Column(
            "build_profile",
            sa.String(length=16),
            nullable=False,
            server_default="detailed",
        ),
    )
    op.create_check_constraint(
        "ck_report_jobs_build_profile",
        "report_jobs",
        "build_profile IN ('quick', 'detailed')",
    )
    op.drop_constraint(
        "ck_report_snapshots_collection_contract_version",
        "report_snapshots",
        type_="check",
    )
    op.create_check_constraint(
        "ck_report_snapshots_collection_contract_version",
        "report_snapshots",
        "collection_contract_version IN "
        "('legacy-core-v1', 'quick-season-v1', 'phase3-detailed-v1')",
    )


def downgrade():
    op.execute("SET LOCAL roster_scout.retention_purge = 'on'")
    op.execute(
        "UPDATE report_snapshots "
        "SET collection_contract_version = 'legacy-core-v1' "
        "WHERE collection_contract_version = 'quick-season-v1'"
    )
    op.drop_constraint(
        "ck_report_snapshots_collection_contract_version",
        "report_snapshots",
        type_="check",
    )
    op.create_check_constraint(
        "ck_report_snapshots_collection_contract_version",
        "report_snapshots",
        "collection_contract_version IN "
        "('legacy-core-v1', 'phase3-detailed-v1')",
    )
    op.drop_constraint(
        "ck_report_jobs_build_profile", "report_jobs", type_="check"
    )
    op.drop_column("report_jobs", "build_profile")

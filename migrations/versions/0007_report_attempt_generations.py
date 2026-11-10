"""Add durable report-attempt generations and generation-bound dispatches.

Revision ID: 0007
Revises: 0006
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "report_jobs",
        sa.Column(
            "dispatch_generation",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "report_jobs",
        sa.Column(
            "started_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_report_jobs_dispatch_generation_positive",
        "report_jobs",
        "dispatch_generation >= 1",
    )
    op.create_check_constraint(
        "ck_report_jobs_started_generation_nonnegative",
        "report_jobs",
        "started_generation >= 0",
    )
    op.create_check_constraint(
        "ck_report_jobs_started_generation_current",
        "report_jobs",
        "started_generation <= dispatch_generation",
    )

    op.add_column(
        "report_job_dispatch_outbox",
        sa.Column("generation", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE report_job_dispatch_outbox AS dispatch
        SET generation = jobs.dispatch_generation
        FROM report_jobs AS jobs
        WHERE jobs.id = dispatch.job_id
        """
    )
    op.alter_column(
        "report_job_dispatch_outbox", "generation", nullable=False
    )
    op.create_check_constraint(
        "ck_report_job_dispatch_generation_positive",
        "report_job_dispatch_outbox",
        "generation >= 1",
    )


def downgrade():
    op.drop_constraint(
        "ck_report_job_dispatch_generation_positive",
        "report_job_dispatch_outbox",
        type_="check",
    )
    op.drop_column("report_job_dispatch_outbox", "generation")

    op.drop_constraint(
        "ck_report_jobs_started_generation_current",
        "report_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_report_jobs_started_generation_nonnegative",
        "report_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_report_jobs_dispatch_generation_positive",
        "report_jobs",
        type_="check",
    )
    op.drop_column("report_jobs", "started_generation")
    op.drop_column("report_jobs", "dispatch_generation")

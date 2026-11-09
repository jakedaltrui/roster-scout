"""Add transactional report-job dispatch outbox and renewable leases.

Revision ID: 0005
Revises: 0004
"""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "report_job_dispatch_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("message_id", sa.String(100), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("leased_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "publish_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("redispatch_after", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_report_job_dispatch_outbox"),
        sa.ForeignKeyConstraint(
            ["job_id", "organization_id"],
            ["report_jobs.id", "report_jobs.organization_id"],
            name="fk_report_job_dispatch_job_organization",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'acknowledged', 'closed')",
            name="ck_report_job_dispatch_status",
        ),
        sa.CheckConstraint(
            "publish_attempts >= 0",
            name="ck_report_job_dispatch_publish_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "(status = 'leased' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'leased' AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_report_job_dispatch_lease_state",
        ),
    )
    op.create_index(
        "uq_report_job_dispatch_active_job",
        "report_job_dispatch_outbox",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending', 'leased', 'acknowledged')"
        ),
    )
    op.create_index(
        "ix_report_job_dispatch_due",
        "report_job_dispatch_outbox",
        ["status", "next_attempt_at", "lease_expires_at"],
    )
    op.create_index(
        "ix_report_job_dispatch_organization",
        "report_job_dispatch_outbox",
        ["organization_id", "job_id"],
    )
    op.execute(
        """
        INSERT INTO report_job_dispatch_outbox (
            id, organization_id, job_id, status, message_id, next_attempt_at
        )
        SELECT
            gen_random_uuid(), organization_id, id, 'pending',
            gen_random_uuid()::text, now()
        FROM report_jobs
        WHERE status NOT IN ('ready', 'partial', 'failed', 'blocked')
        """
    )


def downgrade():
    op.drop_index(
        "ix_report_job_dispatch_organization",
        table_name="report_job_dispatch_outbox",
    )
    op.drop_index(
        "ix_report_job_dispatch_due", table_name="report_job_dispatch_outbox"
    )
    op.drop_index(
        "uq_report_job_dispatch_active_job",
        table_name="report_job_dispatch_outbox",
    )
    op.drop_table("report_job_dispatch_outbox")

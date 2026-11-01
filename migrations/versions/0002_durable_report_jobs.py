"""Add durable report job delivery and section cache metadata.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _column_names(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _foreign_key_names(table):
    return {
        foreign_key["name"]
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table)
        if foreign_key.get("name")
    }


def _check_constraint_names(table):
    return {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_check_constraints(table)
        if constraint.get("name")
    }


def upgrade():
    op.drop_constraint("ck_report_jobs_status", "report_jobs", type_="check")
    op.alter_column("report_jobs", "status", server_default="queued")
    op.execute("UPDATE report_jobs SET status = 'queued' WHERE status = 'pending'")
    op.execute("UPDATE report_jobs SET status = 'ready' WHERE status = 'succeeded'")
    job_columns = _column_names("report_jobs")
    additions = (
        ("failure_message", sa.Column("failure_message", sa.String(200))),
        ("max_attempts", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3")),
        ("enqueued_at", sa.Column("enqueued_at", sa.DateTime(timezone=True))),
        ("broker_message_id", sa.Column("broker_message_id", sa.String(100))),
        ("started_at", sa.Column("started_at", sa.DateTime(timezone=True))),
        ("finished_at", sa.Column("finished_at", sa.DateTime(timezone=True))),
    )
    for name, column in additions:
        if name not in job_columns:
            op.add_column("report_jobs", column)
    op.create_check_constraint(
        "ck_report_jobs_status",
        "report_jobs",
        "status IN ('queued', 'running', 'retrying', 'ready', 'partial', 'failed', 'blocked')",
    )
    if "ck_report_jobs_max_attempts_bounded" not in _check_constraint_names("report_jobs"):
        op.create_check_constraint(
            "ck_report_jobs_max_attempts_bounded",
            "report_jobs",
            "max_attempts BETWEEN 1 AND 10",
        )

    for name, column in (
        ("job_id", sa.Column("job_id", sa.Uuid())),
        ("source_url", sa.Column("source_url", sa.Text())),
        ("source_team_id", sa.Column("source_team_id", sa.String(100))),
        ("source_season_id", sa.Column("source_season_id", sa.String(100))),
        ("source_season_team_id", sa.Column("source_season_team_id", sa.String(100))),
        ("source_stat_category_id", sa.Column("source_stat_category_id", sa.String(100))),
        ("schema_version", sa.Column("schema_version", sa.String(32))),
        ("parser_version", sa.Column("parser_version", sa.String(32))),
    ):
        if name not in _column_names("report_sections"):
            op.add_column("report_sections", column)
    if "fk_report_sections_job_organization" not in _foreign_key_names("report_sections"):
        op.create_foreign_key(
            "fk_report_sections_job_organization",
            "report_sections",
            "report_jobs",
            ["job_id", "organization_id"],
            ["id", "organization_id"],
        )

    evidence_columns = _column_names("failure_evidence")
    evidence_additions = (
        ("section", sa.Column("section", sa.String(50), nullable=False, server_default="unknown")),
        ("source_url", sa.Column("source_url", sa.Text())),
        ("failure_category", sa.Column("failure_category", sa.String(100), nullable=False, server_default="unknown")),
        ("collected_at", sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())),
    )
    for name, column in evidence_additions:
        if name not in evidence_columns:
            op.add_column("failure_evidence", column)


def downgrade():
    op.drop_column("failure_evidence", "collected_at")
    op.drop_column("failure_evidence", "failure_category")
    op.drop_column("failure_evidence", "source_url")
    op.drop_column("failure_evidence", "section")

    op.drop_constraint(
        "fk_report_sections_job_organization", "report_sections", type_="foreignkey"
    )
    for name in (
        "parser_version",
        "schema_version",
        "source_stat_category_id",
        "source_season_team_id",
        "source_season_id",
        "source_team_id",
        "source_url",
        "job_id",
    ):
        op.drop_column("report_sections", name)

    op.drop_constraint("ck_report_jobs_max_attempts_bounded", "report_jobs", type_="check")
    op.drop_constraint("ck_report_jobs_status", "report_jobs", type_="check")
    for name in (
        "finished_at",
        "started_at",
        "broker_message_id",
        "enqueued_at",
        "max_attempts",
        "failure_message",
    ):
        op.drop_column("report_jobs", name)
    op.execute("UPDATE report_jobs SET status = 'pending' WHERE status IN ('queued', 'retrying')")
    op.execute("UPDATE report_jobs SET status = 'succeeded' WHERE status IN ('ready', 'partial')")
    op.alter_column("report_jobs", "status", server_default="pending")
    op.create_check_constraint(
        "ck_report_jobs_status",
        "report_jobs",
        "status IN ('pending', 'running', 'succeeded', 'failed', 'blocked')",
    )

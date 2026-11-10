"""Enforce the failure-evidence retention window in PostgreSQL.

Revision ID: 0011
Revises: 0010
"""

from alembic import op


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


CONSTRAINT_NAME = "ck_failure_evidence_retention_window"
RETENTION_EXPRESSION = (
    "expires_at > collected_at "
    "AND expires_at <= collected_at + INTERVAL '28 days'"
)


def upgrade():
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM failure_evidence
                WHERE expires_at <= collected_at
                   OR expires_at > collected_at + INTERVAL '28 days'
            ) THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'invalid failure evidence retention history',
                    ERRCODE = '23514',
                    CONSTRAINT = 'ck_failure_evidence_retention_window';
            END IF;
        END;
        $$
        """
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "failure_evidence",
        RETENTION_EXPRESSION,
    )


def downgrade():
    op.drop_constraint(
        CONSTRAINT_NAME,
        "failure_evidence",
        type_="check",
    )

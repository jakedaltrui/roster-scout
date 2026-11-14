"""Add verified institutional-domain organization membership provenance.

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organization_domains",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("root_domain", sa.String(length=253), nullable=False),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "domain = lower(domain) AND root_domain = lower(root_domain)",
            name="ck_organization_domains_canonical",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_organization_domains_organization",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain", name="uq_organization_domains_domain"),
    )
    op.create_index(
        "ix_organization_domains_root",
        "organization_domains",
        ["root_domain"],
        unique=False,
    )
    op.add_column(
        "organization_memberships",
        sa.Column(
            "membership_source",
            sa.String(length=24),
            server_default="invitation",
            nullable=False,
        ),
    )
    op.add_column(
        "organization_memberships",
        sa.Column("source_domain", sa.String(length=253), nullable=True),
    )
    op.create_check_constraint(
        "ck_memberships_source",
        "organization_memberships",
        "(membership_source = 'invitation' AND source_domain IS NULL) OR "
        "(membership_source = 'verified_domain' AND source_domain IS NOT NULL)",
    )
    op.create_index(
        "ix_memberships_source_domain",
        "organization_memberships",
        ["source_domain"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_memberships_source_domain",
        table_name="organization_memberships",
    )
    op.drop_constraint(
        "ck_memberships_source",
        "organization_memberships",
        type_="check",
    )
    op.drop_column("organization_memberships", "source_domain")
    op.drop_column("organization_memberships", "membership_source")
    op.drop_index("ix_organization_domains_root", table_name="organization_domains")
    op.drop_table("organization_domains")

"""create business profiles

Revision ID: d6fb4718ea9d
Revises: 736e8ab04e5c
Create Date: 2026-10-18 09:17:21.893686+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d6fb4718ea9d"
down_revision: str | None = "736e8ab04e5c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_profiles",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("domain", sa.String(length=20), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_business_profiles_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_profiles")),
    )
    op.create_index(
        op.f("ix_business_profiles_tenant_id"), "business_profiles", ["tenant_id"], unique=False
    )
    op.create_table(
        "business_profile_versions",
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("saved_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["business_profiles.id"],
            name=op.f("fk_business_profile_versions_profile_id_business_profiles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["saved_by"],
            ["operators.id"],
            name=op.f("fk_business_profile_versions_saved_by_operators"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_profile_versions")),
        sa.UniqueConstraint(
            "profile_id", "version", name=op.f("uq_business_profile_versions_profile_id")
        ),
    )


def downgrade() -> None:
    op.drop_table("business_profile_versions")
    op.drop_index(op.f("ix_business_profiles_tenant_id"), table_name="business_profiles")
    op.drop_table("business_profiles")

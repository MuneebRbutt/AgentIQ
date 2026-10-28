"""create interactions and turns

Revision ID: 8a3b2df11855
Revises: 3a4d2f01db62
Create Date: 2026-10-18 13:01:03.546279+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8a3b2df11855"
down_revision: str | None = "3a4d2f01db62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interactions",
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("customer_phone", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("memory", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "status IN ('open', 'handed_off', 'closed')", name=op.f("ck_interactions_known_status")
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["business_profiles.id"],
            name=op.f("fk_interactions_profile_id_business_profiles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_interactions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_interactions")),
    )
    op.create_index(
        "ix_interactions_profile_updated",
        "interactions",
        ["profile_id", "updated_at"],
        unique=False,
    )
    op.create_index(op.f("ix_interactions_tenant_id"), "interactions", ["tenant_id"], unique=False)
    op.create_table(
        "turns",
        sa.Column("interaction_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('customer', 'assistant')", name=op.f("ck_turns_known_role")),
        sa.ForeignKeyConstraint(
            ["interaction_id"],
            ["interactions.id"],
            name=op.f("fk_turns_interaction_id_interactions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_turns_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_turns")),
    )
    op.create_index(op.f("ix_turns_tenant_id"), "turns", ["tenant_id"], unique=False)
    op.create_index("uq_turns_interaction_seq", "turns", ["interaction_id", "seq"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_turns_interaction_seq", table_name="turns")
    op.drop_index(op.f("ix_turns_tenant_id"), table_name="turns")
    op.drop_table("turns")
    op.drop_index(op.f("ix_interactions_tenant_id"), table_name="interactions")
    op.drop_index("ix_interactions_profile_updated", table_name="interactions")
    op.drop_table("interactions")

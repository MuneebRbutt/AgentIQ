"""create knowledge documents

Revision ID: 3a4d2f01db62
Revises: a14b67da7d2a
Create Date: 2026-10-18 12:29:28.151462+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3a4d2f01db62"
down_revision: str | None = "a14b67da7d2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
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
            ["profile_id"],
            ["business_profiles.id"],
            name=op.f("fk_knowledge_documents_profile_id_business_profiles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_knowledge_documents_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["operators.id"],
            name=op.f("fk_knowledge_documents_uploaded_by_operators"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_documents")),
    )
    op.create_index(
        op.f("ix_knowledge_documents_tenant_id"), "knowledge_documents", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_knowledge_documents_profile_filename",
        "knowledge_documents",
        ["profile_id", "filename"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_knowledge_documents_profile_filename", table_name="knowledge_documents")
    op.drop_index(op.f("ix_knowledge_documents_tenant_id"), table_name="knowledge_documents")
    op.drop_table("knowledge_documents")

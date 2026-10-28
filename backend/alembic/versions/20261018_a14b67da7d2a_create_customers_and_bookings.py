"""create customers and bookings

Revision ID: a14b67da7d2a
Revises: d6fb4718ea9d
Create Date: 2026-10-18 09:39:05.871157+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a14b67da7d2a"
down_revision: str | None = "d6fb4718ea9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("phone_key", sa.String(length=10), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
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
            name=op.f("fk_customers_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
    )
    op.create_index(op.f("ix_customers_tenant_id"), "customers", ["tenant_id"], unique=False)
    op.create_index(
        "uq_customers_tenant_phone", "customers", ["tenant_id", "phone_key"], unique=True
    )
    op.create_table(
        "bookings",
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("reference", sa.String(length=12), nullable=False),
        sa.Column("service_id", sa.String(length=50), nullable=False),
        sa.Column("resource_id", sa.String(length=50), nullable=False),
        sa.Column("party_size", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('confirmed', 'cancelled')", name=op.f("ck_bookings_known_status")
        ),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_bookings_ends_after_start")),
        sa.CheckConstraint("party_size > 0", name=op.f("ck_bookings_positive_party_size")),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_bookings_customer_id_customers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["business_profiles.id"],
            name=op.f("fk_bookings_profile_id_business_profiles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_bookings_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookings")),
    )
    op.create_index(
        "ix_bookings_availability",
        "bookings",
        ["profile_id", "resource_id", "starts_at"],
        unique=False,
    )
    op.create_index(op.f("ix_bookings_customer_id"), "bookings", ["customer_id"], unique=False)
    op.create_index(op.f("ix_bookings_tenant_id"), "bookings", ["tenant_id"], unique=False)
    op.create_index(
        "uq_bookings_tenant_reference", "bookings", ["tenant_id", "reference"], unique=True
    )
    op.create_table(
        "booking_events",
        sa.Column("booking_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("actor", sa.String(length=80), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name=op.f("fk_booking_events_booking_id_bookings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_booking_events")),
        sa.UniqueConstraint("seq", name=op.f("uq_booking_events_seq")),
    )
    op.create_index(
        op.f("ix_booking_events_booking_id"), "booking_events", ["booking_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_booking_events_booking_id"), table_name="booking_events")
    op.drop_table("booking_events")
    op.drop_index("uq_bookings_tenant_reference", table_name="bookings")
    op.drop_index(op.f("ix_bookings_tenant_id"), table_name="bookings")
    op.drop_index(op.f("ix_bookings_customer_id"), table_name="bookings")
    op.drop_index("ix_bookings_availability", table_name="bookings")
    op.drop_table("bookings")
    op.drop_index("uq_customers_tenant_phone", table_name="customers")
    op.drop_index(op.f("ix_customers_tenant_id"), table_name="customers")
    op.drop_table("customers")

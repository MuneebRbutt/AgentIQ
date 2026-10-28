"""Uploaded knowledge documents (FR-05-01, FR-05-06). Their chunks live in ChromaDB."""

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import CreatedAtMixin, Timestamp, UUIDPrimaryKeyMixin
from app.tenancy.scoping import TenantOwnedMixin


class KnowledgeDocument(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_documents"
    # Uploading a file with the same name replaces it (FR-05-06).
    __table_args__ = (
        Index("uq_knowledge_documents_profile_filename", "profile_id", "filename", unique=True),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("business_profiles.id", ondelete="CASCADE")
    )
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    # The extracted text, kept so the index can be rebuilt (e.g. after changing embeddings).
    text: Mapped[str] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("operators.id", ondelete="SET NULL")
    )
    updated_at: Mapped[Timestamp] = mapped_column(server_default=func.now())

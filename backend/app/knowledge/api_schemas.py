"""Request and response shapes of the knowledge API."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    chunk_count: int
    uploaded_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class UploadOut(DocumentOut):
    # True when the upload replaced an earlier file with the same name (FR-05-06).
    replaced: bool


class DocumentTextOut(DocumentOut):
    text: str  # what was extracted, so operators can check a PDF was read correctly

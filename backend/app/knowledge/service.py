"""Knowledge documents of one business profile: upload, list, delete, search (FR-05).

Uploading parses the file, splits it into passages, embeds them and stores them in the
tenant's Chroma collection. A file with the same name as an existing one replaces it
(FR-05-06). The extracted text is kept in Postgres, the source of truth, so the index can
be rebuilt at any time (`reindex`).
"""

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import PurePath

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.knowledge.chunking import chunk_text
from app.knowledge.embeddings import Embedder
from app.knowledge.extraction import extract_text
from app.knowledge.models import KnowledgeDocument
from app.knowledge.profile_facts import ProfileFactsIndex
from app.knowledge.store import ChromaVectorStore, Passage
from app.profiles.models import BusinessProfile
from app.tenancy.scoping import scoped

log = get_logger(__name__)

DOCUMENT_NOT_FOUND = "Document not found."
MAX_FILENAME_LENGTH = 255


class DocumentTooLargeError(AppError):
    status_code = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    code = "document_too_large"


@dataclass(frozen=True, slots=True)
class Upload:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class UploadResult:
    document: KnowledgeDocument
    replaced: bool


class KnowledgeService:
    def __init__(
        self,
        db: AsyncSession,
        clock: Clock,
        settings: Settings,
        store: ChromaVectorStore,
        embedder: Embedder,
        facts: ProfileFactsIndex | None = None,
    ) -> None:
        self._facts = facts
        self._db = db
        self._clock = clock
        self._settings = settings
        self._store = store
        self._embedder = embedder

    async def upload(
        self, profile: BusinessProfile, upload: Upload, *, uploaded_by: uuid.UUID | None = None
    ) -> UploadResult:
        filename = clean_filename(upload.filename)
        if len(upload.data) > self._settings.max_document_bytes:
            limit_mb = self._settings.max_document_bytes / (1024 * 1024)
            raise DocumentTooLargeError(f"Documents can be at most {limit_mb:g} MB.")

        # Parsing and embedding are CPU work; keep them off the event loop.
        text = await asyncio.to_thread(extract_text, filename, upload.data)
        chunks = chunk_text(text)
        embeddings = await asyncio.to_thread(self._embedder.embed, chunks)

        # Rolling back expires loaded rows, so keep the IDs needed for cleanup at hand.
        tenant_id, profile_id = profile.tenant_id, profile.id
        now = self._clock.now()
        previous = await self._by_filename(profile, filename)
        previous_id = previous.id if previous else None
        document = KnowledgeDocument(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            profile_id=profile_id,
            filename=filename,
            content_type=upload.content_type[:100] or "application/octet-stream",
            size_bytes=len(upload.data),
            sha256=hashlib.sha256(upload.data).hexdigest(),
            text=text,
            chunk_count=len(chunks),
            uploaded_by=uploaded_by,
            created_at=previous.created_at if previous else now,
            updated_at=now,
        )
        # New passages go in first under the new document's ID, so a failure at any step
        # leaves the old version fully searchable.
        document_id = document.id
        await self._store.add(
            tenant_id,
            profile_id=profile_id,
            document_id=document_id,
            filename=filename,
            chunks=chunks,
            embeddings=embeddings,
        )
        try:
            if previous is not None:
                await self._db.delete(previous)
                await self._db.flush()  # free the (profile, filename) slot before inserting
            self._db.add(document)
            await self._db.commit()
        except IntegrityError as exc:
            await self._db.rollback()
            await self._store.delete_document(tenant_id, document_id)
            raise ConflictError(
                f"'{filename}' is being uploaded by someone else; try again."
            ) from exc
        except BaseException:
            await self._db.rollback()
            await self._store.delete_document(tenant_id, document_id)
            raise
        if previous_id is not None:
            await self._store.delete_document(tenant_id, previous_id)

        log.info(
            "knowledge_document_uploaded",
            profile_id=str(profile_id),
            document_id=str(document_id),
            chunks=len(chunks),
            replaced=previous_id is not None,
        )
        return UploadResult(document, replaced=previous_id is not None)

    async def list_documents(self, profile: BusinessProfile) -> list[KnowledgeDocument]:
        statement = (
            scoped(select(KnowledgeDocument), KnowledgeDocument, profile.tenant_id)
            .where(KnowledgeDocument.profile_id == profile.id)
            .order_by(KnowledgeDocument.filename)
        )
        return list(await self._db.scalars(statement))

    async def get(self, profile: BusinessProfile, document_id: uuid.UUID) -> KnowledgeDocument:
        statement = scoped(select(KnowledgeDocument), KnowledgeDocument, profile.tenant_id).where(
            KnowledgeDocument.profile_id == profile.id, KnowledgeDocument.id == document_id
        )
        document = await self._db.scalar(statement)
        if document is None:
            raise NotFoundError(DOCUMENT_NOT_FOUND)
        return document

    async def delete(self, profile: BusinessProfile, document_id: uuid.UUID) -> None:
        document = await self.get(profile, document_id)
        await self._db.delete(document)
        await self._db.commit()
        await self._store.delete_document(profile.tenant_id, document_id)
        log.info("knowledge_document_deleted", document_id=str(document_id))

    async def search(self, profile: BusinessProfile, question: str) -> list[Passage]:
        """Passages that match the question well enough to answer from (FR-05-03/05)."""
        top_k = self._settings.knowledge_top_k
        [embedding] = await asyncio.to_thread(self._embedder.embed, [question])
        passages = await self._store.search(
            profile.tenant_id, profile_id=profile.id, embedding=embedding, top_k=top_k
        )
        if self._facts is not None:
            passages += await asyncio.to_thread(self._facts.search, profile, embedding, top_k)
        passages.sort(key=lambda passage: passage.score, reverse=True)
        return [p for p in passages[:top_k] if p.score >= self._settings.knowledge_min_score]

    async def reindex(self, profile: BusinessProfile) -> int:
        """Rebuild the profile's passages from the stored text; returns the passage count."""
        await self._store.delete_profile(profile.tenant_id, profile.id)
        total = 0
        for document in await self.list_documents(profile):
            chunks = chunk_text(document.text)
            embeddings = await asyncio.to_thread(self._embedder.embed, chunks)
            await self._store.add(
                profile.tenant_id,
                profile_id=profile.id,
                document_id=document.id,
                filename=document.filename,
                chunks=chunks,
                embeddings=embeddings,
            )
            document.chunk_count = len(chunks)
            total += len(chunks)
        await self._db.commit()
        return total

    async def _by_filename(
        self, profile: BusinessProfile, filename: str
    ) -> KnowledgeDocument | None:
        statement = scoped(select(KnowledgeDocument), KnowledgeDocument, profile.tenant_id).where(
            KnowledgeDocument.profile_id == profile.id, KnowledgeDocument.filename == filename
        )
        return await self._db.scalar(statement)


def clean_filename(filename: str) -> str:
    """The bare file name, without any folder part a browser or client may send."""
    name = PurePath(filename.replace("\\", "/")).name.strip()
    if not name:
        raise ValidationError("The file needs a name.")
    if len(name) > MAX_FILENAME_LENGTH:
        raise ValidationError(f"File names can be at most {MAX_FILENAME_LENGTH} characters.")
    return name

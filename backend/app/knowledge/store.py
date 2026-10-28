"""Chunk storage and similarity search in ChromaDB (FR-05-02/03, NFR03-02; ADR-0004).

Each tenant has its own collection, so one business's passages can't turn up in another's
search however a query is built (CLAUDE.md rule 1). Within a tenant, every query is also
filtered by profile. ChromaDB runs embedded in the API process; its calls block, so they
run in a worker thread.
"""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chromadb.api import ClientAPI
    from chromadb.api.models.Collection import Collection


@dataclass(frozen=True, slots=True)
class Passage:
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    text: str
    score: float  # cosine similarity, 1 = identical meaning


class ChromaVectorStore:
    def __init__(self, directory: Path | None, embedder_name: str) -> None:
        import chromadb

        self._client: ClientAPI = (
            chromadb.PersistentClient(path=str(directory))
            if directory is not None
            else chromadb.EphemeralClient()
        )
        self._embedder_name = embedder_name

    def collection_name(self, tenant_id: uuid.UUID) -> str:
        return f"t_{tenant_id.hex}_{self._embedder_name}"

    async def add(
        self,
        tenant_id: uuid.UUID,
        *,
        profile_id: uuid.UUID,
        document_id: uuid.UUID,
        filename: str,
        chunks: Sequence[str],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        if not chunks:
            return
        ids = [f"{document_id}:{index}" for index in range(len(chunks))]
        metadatas: list[Any] = [
            {
                "profile_id": str(profile_id),
                "document_id": str(document_id),
                "filename": filename,
                "chunk_index": index,
            }
            for index in range(len(chunks))
        ]

        vectors: list[Sequence[float]] = [list(vector) for vector in embeddings]

        def upsert() -> None:
            self._collection(tenant_id).upsert(
                ids=ids,
                documents=list(chunks),
                embeddings=vectors,
                metadatas=metadatas,
            )

        await asyncio.to_thread(upsert)

    async def delete_document(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
        await self._delete(tenant_id, {"document_id": str(document_id)})

    async def delete_profile(self, tenant_id: uuid.UUID, profile_id: uuid.UUID) -> None:
        await self._delete(tenant_id, {"profile_id": str(profile_id)})

    async def search(
        self,
        tenant_id: uuid.UUID,
        *,
        profile_id: uuid.UUID,
        embedding: Sequence[float],
        top_k: int,
    ) -> list[Passage]:
        query_vectors: list[Sequence[float]] = [list(embedding)]

        def query() -> list[Passage]:
            result = self._collection(tenant_id).query(
                query_embeddings=query_vectors,
                n_results=top_k,
                where={"profile_id": str(profile_id)},
                include=["documents", "metadatas", "distances"],
            )
            documents = (result["documents"] or [[]])[0]
            metadatas = (result["metadatas"] or [[]])[0]
            distances = (result["distances"] or [[]])[0]
            return [
                Passage(
                    document_id=uuid.UUID(str(metadata["document_id"])),
                    filename=str(metadata["filename"]),
                    chunk_index=int(str(metadata["chunk_index"])),
                    text=text,
                    # Cosine distance is 1 - similarity.
                    score=round(1.0 - float(distance), 4),
                )
                for text, metadata, distance in zip(documents, metadatas, distances, strict=True)
            ]

        return await asyncio.to_thread(query)

    async def count(self, tenant_id: uuid.UUID) -> int:
        return await asyncio.to_thread(lambda: self._collection(tenant_id).count())

    def _collection(self, tenant_id: uuid.UUID) -> "Collection":
        return self._client.get_or_create_collection(
            self.collection_name(tenant_id),
            embedding_function=None,  # we pass our own embeddings
            configuration={"hnsw": {"space": "cosine"}},
        )

    async def _delete(self, tenant_id: uuid.UUID, where: dict[str, Any]) -> None:
        await asyncio.to_thread(lambda: self._collection(tenant_id).delete(where=where))

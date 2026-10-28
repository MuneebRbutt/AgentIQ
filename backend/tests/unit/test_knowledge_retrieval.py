"""Embedders and the per-tenant Chroma store (FR-05-02/03, NFR03-02)."""

import math
import uuid

import pytest

from app.core.config import Settings
from app.knowledge.embeddings import (
    HashingEmbedder,
    MiniLMEmbedder,
    content_words,
    create_embedder,
)
from app.knowledge.store import ChromaVectorStore


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class TestHashingEmbedder:
    def test_vectors_are_deterministic_and_normalised(self) -> None:
        embedder = HashingEmbedder(dimensions=64)
        [first] = embedder.embed(["Free cancellation up to 24 hours ahead"])
        [again] = embedder.embed(["Free cancellation up to 24 hours ahead"])
        assert first == again
        assert len(first) == 64
        assert math.isclose(sum(v * v for v in first), 1.0)

    def test_shared_words_make_texts_similar(self) -> None:
        embedder = HashingEmbedder()
        policy, pets, question = embedder.embed(
            ["Bookings can be cancelled for free.", "Pets are not allowed.", "Can I cancel?"]
        )
        assert _cosine(question, policy) > _cosine(question, pets)

    def test_text_of_only_filler_words_still_has_a_direction(self) -> None:
        [vector] = HashingEmbedder(dimensions=16).embed(["what is it"])
        assert math.isclose(sum(v * v for v in vector), 1.0)

    def test_content_words_drop_filler_and_stem(self) -> None:
        assert content_words("What are the cancellation rules for my bookings?") == [
            "cancel",
            "rule",
            "book",
        ]
        assert content_words("Cancelled, booked, policies") == ["cancel", "book", "policy"]


def test_create_embedder_follows_settings() -> None:
    assert create_embedder(Settings(embedding_provider="hashing")).name == "hashing"
    assert isinstance(create_embedder(Settings(embedding_provider="minilm")), MiniLMEmbedder)


@pytest.fixture
def store() -> ChromaVectorStore:
    return ChromaVectorStore(None, "hashing")


async def _add(
    store: ChromaVectorStore,
    tenant: uuid.UUID,
    profile: uuid.UUID,
    chunks: list[str],
    document: uuid.UUID | None = None,
) -> uuid.UUID:
    document = document or uuid.uuid4()
    await store.add(
        tenant,
        profile_id=profile,
        document_id=document,
        filename="policies.md",
        chunks=chunks,
        embeddings=HashingEmbedder().embed(chunks),
    )
    return document


async def _search(
    store: ChromaVectorStore, tenant: uuid.UUID, profile: uuid.UUID, question: str
) -> list[str]:
    passages = await store.search(
        tenant, profile_id=profile, embedding=HashingEmbedder().embed([question])[0], top_k=3
    )
    return [passage.text for passage in passages]


class TestChromaVectorStore:
    async def test_search_ranks_by_similarity_with_scores(self, store: ChromaVectorStore) -> None:
        tenant, profile = uuid.uuid4(), uuid.uuid4()
        document = await _add(
            store, tenant, profile, ["Breakfast is served at 7.", "Bookings can be cancelled."]
        )
        passages = await store.search(
            tenant,
            profile_id=profile,
            embedding=HashingEmbedder().embed(["cancel my booking"])[0],
            top_k=2,
        )
        assert passages[0].text == "Bookings can be cancelled."
        assert passages[0].document_id == document
        assert passages[0].filename == "policies.md"
        assert passages[0].chunk_index == 1
        assert passages[0].score > passages[1].score
        assert 0 < passages[0].score <= 1

    async def test_tenants_get_separate_collections(self, store: ChromaVectorStore) -> None:
        tenant_a, tenant_b, profile = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await _add(store, tenant_a, profile, ["Tenant A secret policy."])
        assert store.collection_name(tenant_a) != store.collection_name(tenant_b)
        # Even with the same profile ID, another tenant's search finds nothing (NFR03-02).
        assert await _search(store, tenant_b, profile, "secret policy") == []

    async def test_search_is_limited_to_the_profile(self, store: ChromaVectorStore) -> None:
        tenant, hotel, clinic = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await _add(store, tenant, hotel, ["Check-in from 2 PM."])
        await _add(store, tenant, clinic, ["Check-in at the clinic desk."])
        assert await _search(store, tenant, hotel, "check-in") == ["Check-in from 2 PM."]

    async def test_delete_document_and_profile(self, store: ChromaVectorStore) -> None:
        tenant, profile, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        doc = await _add(store, tenant, profile, ["One.", "Two."])
        await _add(store, tenant, profile, ["Three."])
        await _add(store, tenant, other, ["Four."])
        assert await store.count(tenant) == 4
        await store.delete_document(tenant, doc)
        assert await store.count(tenant) == 2
        await store.delete_profile(tenant, profile)
        assert await store.count(tenant) == 1

    async def test_adding_nothing_is_a_no_op(self, store: ChromaVectorStore) -> None:
        tenant = uuid.uuid4()
        await _add(store, tenant, uuid.uuid4(), [])
        assert await store.count(tenant) == 0

    async def test_persistent_store_survives_a_restart(self, tmp_path: object) -> None:
        from pathlib import Path

        directory = Path(str(tmp_path))
        tenant, profile = uuid.uuid4(), uuid.uuid4()
        await _add(ChromaVectorStore(directory, "hashing"), tenant, profile, ["Kept on disk."])
        reopened = ChromaVectorStore(directory, "hashing")
        assert await _search(reopened, tenant, profile, "disk") == ["Kept on disk."]

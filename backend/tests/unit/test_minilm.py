"""Retrieval with the real MiniLM model (TC-05 with production embeddings).

Skipped when the model can't be loaded (no network on first run, unsupported platform),
so the suite stays green offline. CI and a developer's machine run it once the model is
cached.
"""

import uuid
from pathlib import Path

import pytest

from app.agents.contracts import AgentContext, AgentStatus
from app.agents.knowledge.agent import KnowledgeAgent, KnowledgeSubtask
from app.core.config import Settings
from app.knowledge.chunking import chunk_text
from app.knowledge.embeddings import MiniLMEmbedder
from app.knowledge.store import ChromaVectorStore, Passage
from app.profiles.models import BusinessProfile

POLICIES = Path(__file__).resolve().parents[1] / "fixtures" / "documents" / "hotel_policies.md"


@pytest.fixture(scope="module")
def minilm() -> MiniLMEmbedder:
    embedder = MiniLMEmbedder()
    try:
        embedder.embed(["warm up"])
    except Exception as exc:  # download or runtime failure
        pytest.skip(f"MiniLM model unavailable: {exc}")
    return embedder


class Retriever:
    def __init__(self, store: ChromaVectorStore, embedder: MiniLMEmbedder) -> None:
        self.store = store
        self.embedder = embedder
        self.min_score = Settings(_env_file=None).knowledge_min_score

    async def search(self, profile: BusinessProfile, question: str) -> list[Passage]:
        passages = await self.store.search(
            profile.tenant_id,
            profile_id=profile.id,
            embedding=self.embedder.embed([question])[0],
            top_k=4,
        )
        return [p for p in passages if p.score >= self.min_score]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What is your cancellation policy?", "cancelled free of charge"),
        ("Can I cancel my reservation for free?", "cancelled free of charge"),
        ("What time is breakfast served?", "7:00 AM to 10:30 AM"),
        ("When can I check in?", "2:00 PM"),
        ("Do you offer valet parking?", None),
        ("Is there a swimming pool?", None),
    ],
)
async def test_tc05_with_minilm(
    minilm: MiniLMEmbedder, question: str, expected: str | None
) -> None:
    tenant = uuid.uuid4()
    profile = BusinessProfile(id=uuid.uuid4(), tenant_id=tenant)
    store = ChromaVectorStore(None, f"minilm{uuid.uuid4().hex[:6]}")
    chunks = chunk_text(POLICIES.read_text(encoding="utf-8"))
    await store.add(
        tenant,
        profile_id=profile.id,
        document_id=uuid.uuid4(),
        filename="policies.md",
        chunks=chunks,
        embeddings=minilm.embed(chunks),
    )

    agent = KnowledgeAgent(Retriever(store, minilm), llm=None)
    result = await agent.run(
        AgentContext(tenant_id=tenant, profile=profile), KnowledgeSubtask(question=question)
    )

    if expected is None:
        assert result.status is AgentStatus.NO_ANSWER
    else:
        assert result.status is AgentStatus.COMPLETED
        assert expected in result.message

"""FastAPI dependencies for the knowledge base. The store, embedder and model are created
once per app in the lifespan (`app.main`) and shared by all requests."""

from typing import Annotated

from fastapi import Depends, Request

from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.db.session import DbSession
from app.knowledge.embeddings import Embedder
from app.knowledge.profile_facts import ProfileFactsIndex
from app.knowledge.service import KnowledgeService
from app.knowledge.store import ChromaVectorStore
from app.providers.llm import LLMClient


def get_vector_store(request: Request) -> ChromaVectorStore:
    store: ChromaVectorStore = request.app.state.vector_store
    return store


def get_embedder(request: Request) -> Embedder:
    embedder: Embedder = request.app.state.embedder
    return embedder


def get_llm(request: Request) -> LLMClient | None:
    llm: LLMClient | None = request.app.state.llm
    return llm


def get_profile_facts(request: Request) -> ProfileFactsIndex:
    facts: ProfileFactsIndex = request.app.state.profile_facts
    return facts


def get_knowledge_service(
    db: DbSession,
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[ChromaVectorStore, Depends(get_vector_store)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    facts: Annotated[ProfileFactsIndex, Depends(get_profile_facts)],
) -> KnowledgeService:
    return KnowledgeService(db, clock, settings, store, embedder, facts)

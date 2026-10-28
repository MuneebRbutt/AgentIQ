"""FastAPI dependencies for conversations."""

from typing import Annotated

from fastapi import Depends, Request
from starlette.datastructures import State

from app.agents.sentiment.agent import SentimentAgent
from app.conversations.service import ConversationService
from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.db.session import DbSession
from app.knowledge.dependencies import (
    get_embedder,
    get_llm,
    get_profile_facts,
    get_vector_store,
)
from app.knowledge.embeddings import Embedder
from app.knowledge.profile_facts import ProfileFactsIndex
from app.knowledge.store import ChromaVectorStore
from app.orchestrator.service import Agents, Orchestrator
from app.providers.llm import LLMClient
from app.providers.notifications import Notifier
from app.providers.sentiment import SentimentClassifier


def get_conversation_service(
    db: DbSession, clock: Annotated[Clock, Depends(get_clock)]
) -> ConversationService:
    return ConversationService(db, clock)


def get_sentiment_classifier(request: Request) -> SentimentClassifier:
    classifier: SentimentClassifier = request.app.state.sentiment_classifier
    return classifier


def get_notifier(request: Request) -> Notifier:
    notifier: Notifier = request.app.state.notifier
    return notifier


def get_orchestrator(
    request: Request,
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[ChromaVectorStore, Depends(get_vector_store)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    facts: Annotated[ProfileFactsIndex, Depends(get_profile_facts)],
    llm: Annotated[LLMClient | None, Depends(get_llm)],
    classifier: Annotated[SentimentClassifier, Depends(get_sentiment_classifier)],
    notifier: Annotated[Notifier, Depends(get_notifier)],
) -> Orchestrator:
    # Built per request from the app's shared pieces (compiling the graph takes ~10 ms,
    # small next to a model call). The orchestrator opens its own database sessions.
    session_factory = request.app.state.session_factory
    agents = Agents(session_factory, clock, settings, store, embedder, facts, llm)
    sentiment = SentimentAgent(classifier, settings.sentiment_min_confidence)
    return Orchestrator(session_factory, clock, settings, agents, llm, sentiment, notifier)


def build_orchestrator(
    state: State, clock: Clock, settings: Settings, llm: LLMClient | None
) -> Orchestrator:
    """An orchestrator from the app's shared pieces, for work that has no request, such as
    reading the email mailbox (ADR-0011)."""
    agents = Agents(
        state.session_factory,
        clock,
        settings,
        state.vector_store,
        state.embedder,
        state.profile_facts,
        llm,
    )
    sentiment = SentimentAgent(state.sentiment_classifier, settings.sentiment_min_confidence)
    return Orchestrator(
        state.session_factory, clock, settings, agents, llm, sentiment, state.notifier
    )

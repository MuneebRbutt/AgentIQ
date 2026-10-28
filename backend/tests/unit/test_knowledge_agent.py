"""The Knowledge agent with a fake retriever and fake model (FR-05-03…05, NFR04-01)."""

import uuid
from dataclasses import dataclass, field

import pytest

from app.agents.contracts import AgentContext, AgentStatus
from app.agents.knowledge.agent import (
    INSUFFICIENT,
    KnowledgeAgent,
    KnowledgeSubtask,
    answer_prompt,
    extract_answer,
)
from app.knowledge.store import Passage
from app.profiles.models import BusinessProfile
from app.providers.llm import LLMError
from tests.fakes import FakeLLM

POLICY = (
    "Reservations can be cancelled free of charge up to 24 hours before check-in. "
    "Late cancellations are charged one night. Breakfast is served from 7 AM."
)


def _passage(text: str = POLICY, score: float = 0.6) -> Passage:
    return Passage(uuid.uuid4(), "policies.md", 0, text, score)


@dataclass
class FakeRetriever:
    passages: list[Passage]
    questions: list[str] = field(default_factory=list)

    async def search(self, profile: BusinessProfile, question: str) -> list[Passage]:
        self.questions.append(question)
        return self.passages


@pytest.fixture
def context() -> AgentContext:
    tenant = uuid.uuid4()
    return AgentContext(
        tenant_id=tenant, profile=BusinessProfile(id=uuid.uuid4(), tenant_id=tenant)
    )


def _ask(question: str = "Can I cancel for free?") -> KnowledgeSubtask:
    return KnowledgeSubtask(question=question)


async def test_no_matching_passages_means_no_answer(context: AgentContext) -> None:
    llm = FakeLLM("should not be called")
    result = await KnowledgeAgent(FakeRetriever([]), llm).run(context, _ask())
    assert result.status is AgentStatus.NO_ANSWER
    assert result.agent == "knowledge"
    assert llm.calls == []  # nothing to ground an answer in, so no model call (and no cost)


async def test_model_answers_from_the_passages(context: AgentContext) -> None:
    llm = FakeLLM("Yes, free cancellation is possible up to 24 hours before check-in.")
    retriever = FakeRetriever([_passage()])
    result = await KnowledgeAgent(retriever, llm).run(context, _ask())

    assert result.status is AgentStatus.COMPLETED
    assert result.message.startswith("Yes, free cancellation")
    assert result.data["mode"] == "llm"
    assert result.data["sources"][0]["filename"] == "policies.md"
    [messages] = llm.calls
    assert messages[0] == {"role": "system", "content": answer_prompt()}
    assert "Can I cancel for free?" in messages[1]["content"]
    assert "[1] (from policies.md)" in messages[1]["content"]
    assert POLICY in messages[1]["content"]


async def test_model_saying_insufficient_means_no_answer(context: AgentContext) -> None:
    llm = FakeLLM(INSUFFICIENT)
    result = await KnowledgeAgent(FakeRetriever([_passage()]), llm).run(
        context, _ask("Do you offer valet parking?")
    )
    assert result.status is AgentStatus.NO_ANSWER


async def test_model_failure_falls_back_to_quoting(context: AgentContext) -> None:
    llm = FakeLLM(LLMError("timeout"))
    result = await KnowledgeAgent(FakeRetriever([_passage()]), llm).run(context, _ask())
    assert result.status is AgentStatus.COMPLETED
    assert result.data["mode"] == "extractive"
    assert "cancelled free of charge" in result.message


async def test_without_a_model_the_best_sentences_are_quoted(context: AgentContext) -> None:
    result = await KnowledgeAgent(FakeRetriever([_passage()]), None).run(
        context, _ask("When is breakfast?")
    )
    assert result.message == "Breakfast is served from 7 AM."


async def test_without_a_model_unrelated_passages_are_not_quoted(context: AgentContext) -> None:
    result = await KnowledgeAgent(FakeRetriever([_passage()]), None).run(
        context, _ask("Do you offer valet parking?")
    )
    assert result.status is AgentStatus.NO_ANSWER


async def test_profile_of_another_tenant_is_refused(context: AgentContext) -> None:
    foreign = AgentContext(tenant_id=uuid.uuid4(), profile=context.profile)
    with pytest.raises(ValueError, match="tenant"):
        await KnowledgeAgent(FakeRetriever([_passage()]), None).run(foreign, _ask())


def test_extract_prefers_the_passage_sharing_most_words() -> None:
    passages = [
        _passage("Check-in starts at 2 PM.", score=0.7),
        _passage("Pets are not allowed. Service animals are welcome.", score=0.5),
    ]
    assert extract_answer("Are pets or service animals allowed?", passages) == (
        "Pets are not allowed. Service animals are welcome."
    )
    assert extract_answer("parking?", passages) is None


def test_question_is_trimmed_and_required() -> None:
    assert KnowledgeSubtask(question="  hours?  ").question == "hours?"
    with pytest.raises(ValueError):
        KnowledgeSubtask(question="   ")


def test_heading_words_count_for_the_sentences_under_it() -> None:
    passage = _passage(
        "## Check-in\n\nCheck-in starts at 2 PM.\n\n## Cancellation policy\n\n"
        "Bookings are free to cancel until 24 hours ahead. Late changes cost one night."
    )
    assert extract_answer("What is your cancellation policy?", [passage]) == (
        "Bookings are free to cancel until 24 hours ahead. Late changes cost one night."
    )

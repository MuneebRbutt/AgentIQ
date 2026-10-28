"""The Knowledge agent (FR-05-03…05, UC-05).

Answers a customer's question from the business's uploaded documents only. It retrieves
the passages that match well enough (`KNOWLEDGE_MIN_SCORE`); with none, it reports that
it has no answer instead of guessing (FR-05-05). With a language model configured, the
model writes a short answer grounded in those passages and may still decline. Without one,
or when the model fails, the agent quotes the best-matching sentences (NFR04-01).
"""

import re
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.agents.contracts import AgentContext, AgentResult, AgentStatus
from app.knowledge.embeddings import content_words
from app.knowledge.store import Passage
from app.profiles.models import BusinessProfile
from app.providers.llm import ChatMessage, LLMClient, LLMError

AGENT_NAME = "knowledge"
NO_ANSWER_MESSAGE = "The business's information doesn't cover this question."
INSUFFICIENT = "INSUFFICIENT_INFORMATION"
EXTRACT_SENTENCES = 2
HEADING_MAX_WORDS = 6

_PROMPTS = Path(__file__).parent / "prompts"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


class KnowledgeSubtask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]


@cache
def answer_prompt() -> str:
    return (_PROMPTS / "answer.md").read_text(encoding="utf-8")


class Retriever(Protocol):
    async def search(self, profile: BusinessProfile, question: str) -> list[Passage]: ...


class KnowledgeAgent:
    def __init__(self, knowledge: Retriever, llm: LLMClient | None) -> None:
        self._knowledge = knowledge
        self._llm = llm

    async def run(self, context: AgentContext, task: KnowledgeSubtask) -> AgentResult:
        if context.profile.tenant_id != context.tenant_id:  # never cross tenants (NFR03-02)
            raise ValueError("profile does not belong to the context's tenant")
        passages = await self._knowledge.search(context.profile, task.question)
        if not passages:
            return self._no_answer(passages)

        if self._llm is not None:
            try:
                answer = await self._llm.complete(
                    _messages(task.question, passages), max_tokens=800
                )
            except LLMError:
                pass  # fall back to quoting the documents
            else:
                if INSUFFICIENT in answer:
                    return self._no_answer(passages)
                return self._answer(answer, "llm", passages)

        quote = extract_answer(task.question, passages)
        if quote is None:
            return self._no_answer(passages)
        return self._answer(quote, "extractive", passages)

    @staticmethod
    def _answer(answer: str, mode: str, passages: list[Passage]) -> AgentResult:
        return AgentResult(
            agent=AGENT_NAME,
            status=AgentStatus.COMPLETED,
            message=answer,
            data={"mode": mode, "sources": _sources(passages)},
        )

    @staticmethod
    def _no_answer(passages: list[Passage]) -> AgentResult:
        return AgentResult(
            agent=AGENT_NAME,
            status=AgentStatus.NO_ANSWER,
            message=NO_ANSWER_MESSAGE,
            data={"sources": _sources(passages)},
        )


def _messages(question: str, passages: list[Passage]) -> list[ChatMessage]:
    numbered = "\n\n".join(
        f"[{number}] (from {passage.filename})\n{passage.text}"
        for number, passage in enumerate(passages, start=1)
    )
    return [
        {"role": "system", "content": answer_prompt()},
        {"role": "user", "content": f"Question: {question}\n\nPassages:\n\n{numbered}"},
    ]


def extract_answer(question: str, passages: list[Passage]) -> str | None:
    """Quote the sentences that share the most words with the question, in their order.

    Without a model to judge relevance, a passage that is merely similar in meaning could
    be quoted as a wrong answer ("valet parking" -> the check-in times), so only sentences
    that share a word with the question count; with none, there is no answer (FR-05-05).
    Headings aren't quoted, but their words count for the sentences under them, so
    "cancellation policy?" finds the sentences below a "Cancellation policy" heading.
    """
    wanted = set(content_words(question))
    best: tuple[int, float, list[str], list[int]] | None = None
    for passage in passages:
        sentences, overlaps = _quotable(passage.text, wanted)
        top = max(overlaps, default=0)
        if top and (best is None or (top, passage.score) > best[:2]):
            best = (top, passage.score, sentences, overlaps)
    if best is None:
        return None
    _, _, sentences, overlaps = best
    ranked = sorted(range(len(sentences)), key=lambda i: (-overlaps[i], i))
    chosen = sorted(i for i in ranked[:EXTRACT_SENTENCES] if overlaps[i])
    return " ".join(sentences[i] for i in chosen)


def _quotable(text: str, wanted: set[str]) -> tuple[list[str], list[int]]:
    """The passage's sentences, and how many wanted words each (with its heading) has."""
    sentences: list[str] = []
    overlaps: list[int] = []
    heading: set[str] = set()
    for piece in _SENTENCE_END.split(text):
        piece = piece.strip().lstrip("#").strip()
        if not piece:
            continue
        if _is_heading(piece):
            heading = set(content_words(piece))
            continue
        sentences.append(piece)
        overlaps.append(len(wanted & (set(content_words(piece)) | heading)))
    return sentences, overlaps


def _is_heading(piece: str) -> bool:
    return len(piece.split()) <= HEADING_MAX_WORDS and not piece.endswith((".", "!", "?"))


def _sources(passages: list[Passage]) -> list[dict[str, Any]]:
    return [
        {
            "document_id": str(passage.document_id),
            "filename": passage.filename,
            "chunk_index": passage.chunk_index,
            "score": passage.score,
        }
        for passage in passages
    ]

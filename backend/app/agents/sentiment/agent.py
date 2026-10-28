"""The Sentiment & Complaint agent (FR-06-01…04, UC-06; ADR-0006).

Runs on every customer message, beside the understanding step (analysis C-7). It
classifies the message, then applies the profile's escalation rules in code:

- a confident negative reading at or above `negative_sentiment_threshold`;
- any of the profile's `keywords` ("manager", "refund"), whatever the sentiment;
- `negative_turns_in_a_row` upset messages in a row, even if none was enough alone.

A reading below `SENTIMENT_MIN_CONFIDENCE` never escalates on sentiment; the conversation
is flagged for review instead (UC-06 exception). Keywords still apply.
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.agents.contracts import AgentContext, AgentResult, AgentStatus
from app.profiles.schema import EscalationRules
from app.providers.sentiment import SentimentClassifier, SentimentLabel, SentimentScore

AGENT_NAME = "sentiment"

REASON_NEGATIVE = "negative_sentiment"
REASON_KEYWORD = "keyword"
REASON_REPEATED = "repeated_negative"


class SentimentSubtask(BaseModel):
    """Built by the orchestrator from trusted state, never by the model."""

    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    negative_streak: int = 0  # upset customer messages just before this one
    escalation: EscalationRules


@dataclass(frozen=True, slots=True)
class Assessment:
    score: SentimentScore
    low_confidence: bool
    negative: bool  # a negative reading confident enough to count
    keywords: list[str]
    negative_streak: int  # including this message
    reasons: list[str]  # why to escalate; empty means don't

    @property
    def complaint(self) -> bool:
        return self.negative or bool(self.keywords)

    def as_data(self) -> dict[str, Any]:
        return {
            "label": self.score.label.value,
            "confidence": self.score.confidence,
            "scores": self.score.scores,
            "model": self.score.model,
            "low_confidence": self.low_confidence,
            "complaint": self.complaint,
            "keywords": self.keywords,
            "negative_streak": self.negative_streak,
            "escalate": bool(self.reasons),
            "reasons": self.reasons,
        }


def matched_keywords(text: str, keywords: list[str]) -> list[str]:
    lowered = " ".join(text.lower().split())
    return [
        keyword
        for keyword in keywords
        if re.search(rf"(?<!\w){re.escape(keyword.lower())}(?!\w)", lowered)
    ]


def assess(
    score: SentimentScore,
    text: str,
    negative_streak: int,
    rules: EscalationRules,
    min_confidence: float,
) -> Assessment:
    low_confidence = score.confidence < min_confidence
    negative = score.label is SentimentLabel.NEGATIVE and not low_confidence
    keywords = matched_keywords(text, rules.keywords)
    streak = negative_streak + 1 if negative else 0
    reasons: list[str] = []
    if negative and score.confidence >= rules.negative_sentiment_threshold:
        reasons.append(REASON_NEGATIVE)
    if keywords:
        reasons.append(REASON_KEYWORD)
    if streak >= rules.negative_turns_in_a_row:
        reasons.append(REASON_REPEATED)
    return Assessment(score, low_confidence, negative, keywords, streak, reasons)


class SentimentAgent:
    def __init__(self, classifier: SentimentClassifier, min_confidence: float) -> None:
        self._classifier = classifier
        self._min_confidence = min_confidence

    async def run(self, context: AgentContext, task: SentimentSubtask) -> AgentResult:
        if context.profile.tenant_id != context.tenant_id:  # never cross tenants (NFR03-02)
            raise ValueError("profile does not belong to the context's tenant")
        # Model inference is CPU-bound; keep it off the event loop (CLAUDE.md §4).
        score = await asyncio.to_thread(self._classifier.classify, task.text)
        assessment = assess(
            score, task.text, task.negative_streak, task.escalation, self._min_confidence
        )
        return AgentResult(
            agent=AGENT_NAME,
            status=AgentStatus.COMPLETED,
            message=_summary(assessment),
            data=assessment.as_data(),
        )


def _summary(assessment: Assessment) -> str:
    score = assessment.score
    text = f"The customer sounds {score.label.value} ({score.confidence:.0%} sure)."
    if assessment.low_confidence:
        text += " The reading is uncertain, so the conversation is flagged for review."
    if assessment.reasons:
        text += f" Escalate: {', '.join(assessment.reasons)}."
    return text

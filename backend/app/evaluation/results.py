"""What an evaluation run records: transcripts, the bookings each run left, and judgments
(FR-10-01…04). Saved as results.json so a report can be rebuilt and adjudicated later."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.evaluation.scenarios import Category


class Line(BaseModel):
    speaker: Literal["customer", "system"]
    text: str
    latency_ms: float | None = None  # system lines answering the customer (FR-10-02)


class Transcript(BaseModel):
    lines: list[Line] = Field(default_factory=list)
    handed_off: bool = False
    error: str | None = None
    # Estimated length of the call if spoken: speech at 150 words a minute, plus waiting
    # and key presses. Comparable across chat systems and the IVR (ADR-0009).
    est_call_seconds: float = 0.0

    def say(
        self, speaker: Literal["customer", "system"], text: str, latency_ms: float | None = None
    ) -> None:
        self.lines.append(Line(speaker=speaker, text=text, latency_ms=latency_ms))

    def system_text(self) -> str:
        return "\n".join(line.text for line in self.lines if line.speaker == "system")


class BookingEvidence(BaseModel):
    reference: str
    service_id: str
    party_size: int
    start_local: datetime  # wall-clock time at the business
    nights: int | None
    status: str
    setup: bool  # existed before the call


class Judgment(BaseModel):
    id: str
    kind: str
    correct: bool
    detail: str
    adjudicated: bool = False
    note: str | None = None


class ScenarioRun(BaseModel):
    system: str
    scenario_id: str
    domain: str
    category: Category
    repetition: int
    transcript: Transcript
    bookings: list[BookingEvidence] = Field(default_factory=list)
    judgments: list[Judgment] = Field(default_factory=list)  # one per expectation
    guards: list[Judgment] = Field(default_factory=list)  # accuracy-only checks


class SystemInfo(BaseModel):
    name: str
    label: str
    skipped: str | None = None  # why the system wasn't run


class ScenarioInfo(BaseModel):
    id: str
    domain: str
    category: Category
    title: str


class EvaluationRun(BaseModel):
    started_at: datetime
    finished_at: datetime | None = None
    repetitions: int
    environment: dict[str, str] = Field(default_factory=dict)
    systems: list[SystemInfo] = Field(default_factory=list)
    scenarios: list[ScenarioInfo] = Field(default_factory=list)
    runs: list[ScenarioRun] = Field(default_factory=list)

"""Scoring a finished conversation against its ground truth (FR-10-01, 03, 04; analysis A-6).

Scoring looks only at what any system leaves behind (what it said, whether it handed the
customer to a person, and the bookings in the database), so AgentIQ and the baselines are
judged by the same rules. Rules are deterministic; an evaluator can override a judgment
afterwards with an adjudication (UC-10 alternative path, `adjudication.py`).
"""

import re
import unicodedata

from app.evaluation.results import BookingEvidence, Judgment, Transcript
from app.evaluation.scenarios import (
    AnswerExpectation,
    BookingExpectation,
    CancelExpectation,
    EscalationExpectation,
    RefusalExpectation,
    Scenario,
)

# Ways of saying "I don't know that" (or "let me pass you on") in a reply.
REFUSAL = re.compile(
    r"(?i)\b(?:"
    r"(?:do not|don't|doesn't|does not) have (?:any |that |this )?(?:information|details|info)"
    r"|(?:don't|do not) know|not sure|no information"
    r"|not (?:able|available) to (?:say|share|confirm)"
    r"|(?:can't|cannot|couldn't|could not|unable to) (?:find|confirm|say|answer|help with)"
    r"|(?:isn't|is not|aren't|are not) (?:covered|mentioned|listed|something I)"
    r"|insufficient information|contact (?:us|the|our)|call (?:us|the|our)|connect you|transfer"
    r")"
)


_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'"})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return text.translate(_QUOTES)


def mentions(text: str, phrase: str) -> bool:
    """`phrase` appears in `text` as whole words, so "9:00" doesn't match inside "19:00"."""
    pattern = r"(?<![\w:.,])" + re.escape(normalize(phrase)) + r"(?![\w])"
    return re.search(pattern, normalize(text)) is not None


def judge(
    scenario: Scenario, transcript: Transcript, bookings: list[BookingEvidence]
) -> tuple[list[Judgment], list[Judgment]]:
    """Judgments (one per expectation, the task-completion items) and guard checks (extra
    accuracy items: no failed turns, no unwanted booking, no needless hand-off)."""
    said = transcript.system_text()
    new = [b for b in bookings if not b.setup and b.status == "confirmed"]
    unmatched = list(new)
    judgments: list[Judgment] = []
    for item in scenario.expect:
        if isinstance(item, AnswerExpectation):
            judgments.append(_answer(item, said))
        elif isinstance(item, RefusalExpectation):
            judgments.append(_refusal(item, said, transcript.handed_off))
        elif isinstance(item, BookingExpectation):
            judgments.append(_booking(item, unmatched))
        elif isinstance(item, CancelExpectation):
            judgments.append(_cancelled(item, bookings))
        elif isinstance(item, EscalationExpectation):
            judgments.append(_escalated(item, transcript.handed_off))
    return judgments, _guards(scenario, transcript, unmatched)


def _answer(item: AnswerExpectation, said: str) -> Judgment:
    missing = [forms[0] for forms in item.facts if not any(mentions(said, f) for f in forms)]
    wrong = [phrase for phrase in item.forbid if mentions(said, phrase)]
    if wrong:
        detail = f"said {', '.join(repr(w) for w in wrong)}"
    elif missing:
        detail = f"missing {', '.join(repr(m) for m in missing)}"
    else:
        detail = "all facts given"
    return Judgment(id=item.id, kind=item.kind, correct=not missing and not wrong, detail=detail)


def _refusal(item: RefusalExpectation, said: str, handed_off: bool) -> Judgment:
    invented = [p for p in item.forbid_patterns if re.search(p, said)]
    declined = handed_off or REFUSAL.search(said) is not None
    if invented:
        detail = "gave an answer the business never provided"
    elif not declined:
        detail = "neither declined nor passed the customer on"
    else:
        detail = "declined" if not handed_off else "passed to a person"
    return Judgment(id=item.id, kind=item.kind, correct=declined and not invented, detail=detail)


def _booking(item: BookingExpectation, unmatched: list[BookingEvidence]) -> Judgment:
    for booking in unmatched:
        if _matches(item, booking):
            unmatched.remove(booking)
            return Judgment(
                id=item.id, kind=item.kind, correct=True, detail=f"booked {booking.reference}"
            )
    if unmatched:
        found = "; ".join(_describe(b) for b in unmatched)
        return Judgment(id=item.id, kind=item.kind, correct=False, detail=f"wrong booking: {found}")
    return Judgment(id=item.id, kind=item.kind, correct=False, detail="no booking made")


def _matches(item: BookingExpectation, booking: BookingEvidence) -> bool:
    if (booking.service_id, booking.party_size) != (item.service_id, item.party_size):
        return False
    if booking.start_local.date() != item.date:
        return False
    if item.time is not None:
        return booking.start_local.time().replace(second=0, microsecond=0) == item.time
    return booking.nights == item.nights


def _describe(booking: BookingEvidence) -> str:
    when = booking.start_local.strftime("%Y-%m-%d %H:%M")
    nights = f", {booking.nights} nights" if booking.nights else ""
    return f"{booking.service_id} for {booking.party_size} at {when}{nights}"


def _cancelled(item: CancelExpectation, bookings: list[BookingEvidence]) -> Judgment:
    setup = [b for b in bookings if b.setup]
    done = bool(setup) and all(b.status == "cancelled" for b in setup)
    return Judgment(
        id=item.id,
        kind=item.kind,
        correct=done,
        detail="cancelled" if done else "booking still confirmed",
    )


def _escalated(item: EscalationExpectation, handed_off: bool) -> Judgment:
    return Judgment(
        id=item.id,
        kind=item.kind,
        correct=handed_off,
        detail="handed to a person" if handed_off else "not handed to a person",
    )


def _guards(
    scenario: Scenario, transcript: Transcript, unmatched: list[BookingEvidence]
) -> list[Judgment]:
    replied = transcript.error is None and _every_turn_answered(transcript)
    may_hand_off = any(item.kind in {"escalation", "refusal"} for item in scenario.expect)
    needless = transcript.handed_off and not may_hand_off
    return [
        Judgment(
            id="replied",
            kind="guard",
            correct=replied,
            detail="every message answered"
            if replied
            else (transcript.error or "a message got no reply"),
        ),
        Judgment(
            id="no-extra-booking",
            kind="guard",
            correct=not unmatched,
            detail="no unwanted booking"
            if not unmatched
            else "unwanted: " + "; ".join(_describe(b) for b in unmatched),
        ),
        Judgment(
            id="no-needless-handoff",
            kind="guard",
            correct=not needless,
            detail="handed off without need" if needless else "no needless hand-off",
        ),
    ]


def _every_turn_answered(transcript: Transcript) -> bool:
    waiting = False
    for line in transcript.lines:
        if line.speaker == "customer":
            if waiting:
                return False
            waiting = True
        elif line.text.strip():
            waiting = False
    return not waiting

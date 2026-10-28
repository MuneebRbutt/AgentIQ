"""The business profile's own facts as searchable passages (FR-05-03, FR-08-04; ADR-0005).

Opening hours, contact details, services, policies and FAQs are already in the profile,
so the Knowledge agent answers "when are you open?" without an uploaded document. The
passages are built from the profile on demand; their vectors are cached per profile
version, so a saved profile is reflected in the next answer.
"""

import math
import uuid
from collections import OrderedDict
from collections.abc import Sequence
from datetime import time
from decimal import Decimal

from app.knowledge.embeddings import Embedder
from app.knowledge.store import Passage
from app.profiles.models import BusinessProfile
from app.profiles.schema import BusinessProfileConfig, OpeningInterval, Weekday

PROFILE_SOURCE = "business profile"
_CACHE_SIZE = 256


def profile_facts(config: BusinessProfileConfig) -> list[str]:
    facts = [_opening_hours(config)]
    if config.description:
        facts.append(f"About {config.name}: {config.description}")
    contact = config.contact
    reach = [
        f"{label} {value}"
        for label, value in (
            ("phone", contact.phone),
            ("email", contact.email),
            ("address", contact.address),
            ("website", contact.website),
        )
        if value
    ]
    if reach:
        facts.append(f"Contact {config.name}: " + ", ".join(reach) + ".")
    for service in config.services:
        parts = [f"{service.name}."]
        if service.description:
            parts.append(service.description)
        if service.price is not None:
            parts.append(f"Price: {config.currency} {_amount(service.price)}.")
        if service.duration_minutes:
            parts.append(f"Takes {service.duration_minutes} minutes.")
        facts.append(" ".join(parts))
    facts.extend(f"{policy.title} policy: {policy.text}" for policy in config.policies)
    facts.extend(f"{faq.question} {faq.answer}" for faq in config.faqs)
    return facts


def _opening_hours(config: BusinessProfileConfig) -> str:
    """ "Opening hours: Monday to Thursday 12:00 to 15:30 and 19:00 to 23:30; ..." with
    days that share the same hours grouped, so the passage reads well and stays short."""
    groups: list[tuple[list[Weekday], str]] = []
    for day in Weekday:
        hours = _hours(config.opening_hours.get(day, []))
        if groups and groups[-1][1] == hours:
            groups[-1][0].append(day)
        else:
            groups.append(([day], hours))
    parts = []
    for days, hours in groups:
        first, last = days[0].value.capitalize(), days[-1].value.capitalize()
        parts.append(f"{first if len(days) == 1 else f'{first} to {last}'} {hours}")
    return "Opening hours: " + "; ".join(parts) + "."


def _hours(intervals: list[OpeningInterval]) -> str:
    if not intervals:
        return "closed"
    if (
        len(intervals) == 1
        and intervals[0].opens == time(0)
        and intervals[0].closes >= time(23, 59)
    ):
        return "open 24 hours"
    return " and ".join(f"{_clock(i.opens)} to {_clock(i.closes)}" for i in intervals)


def _amount(price: Decimal) -> str:
    return f"{price:,.0f}" if price == price.to_integral_value() else f"{price:,.2f}"


def _clock(moment: time) -> str:
    return moment.strftime("%H:%M")


class ProfileFactsIndex:
    """Embeds a profile's facts once per profile version and scores questions against them."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._cache: OrderedDict[tuple[uuid.UUID, int], tuple[list[str], list[list[float]]]]
        self._cache = OrderedDict()

    def search(
        self, profile: BusinessProfile, question_vector: Sequence[float], top_k: int
    ) -> list[Passage]:
        """Blocking (may embed); call from a worker thread."""
        facts, vectors = self._facts(profile)
        scored = [
            Passage(profile.id, PROFILE_SOURCE, index, text, round(_cosine(question_vector, v), 4))
            for index, (text, v) in enumerate(zip(facts, vectors, strict=True))
        ]
        return sorted(scored, key=lambda p: p.score, reverse=True)[:top_k]

    def _facts(self, profile: BusinessProfile) -> tuple[list[str], list[list[float]]]:
        key = (profile.id, profile.version)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        facts = profile_facts(BusinessProfileConfig.model_validate(profile.config))
        entry = (facts, self._embedder.embed(facts))
        self._cache[key] = entry
        if len(self._cache) > _CACHE_SIZE:
            self._cache.popitem(last=False)
        return entry


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0

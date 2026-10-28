"""Runs the scenario set against each system and scores it (UC-10, TC-10 steps 1-2).

Every run happens in a new tenant made for the evaluation, so it never touches a real
business's data. Each (system, scenario, repetition) gets its own copy of the example
profile, so bookings from one run can't fill the capacity another run needs. The tenant is
deleted afterwards unless `keep_data` is set (handy to read the conversations on the
dashboard's logs).
"""

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete

from app.bookings.availability import BookingRequest
from app.bookings.models import Booking
from app.bookings.service import BookingService, CustomerDetails, NewBooking, to_local
from app.core.clock import SystemClock
from app.core.logging import get_logger
from app.evaluation.environment import (
    EvaluatedSystem,
    ScenarioClock,
    ScenarioEnv,
    Shared,
    scenario_start,
)
from app.evaluation.results import (
    BookingEvidence,
    EvaluationRun,
    ScenarioInfo,
    ScenarioRun,
    SystemInfo,
    Transcript,
)
from app.evaluation.scenarios import DomainSet, Scenario, document_path, load_scenarios
from app.evaluation.scoring import judge
from app.knowledge.service import KnowledgeService, Upload
from app.profiles.examples import load_example
from app.profiles.schema import BookingMode, Domain
from app.profiles.service import ProfileService
from app.tenancy.models import Tenant

log = get_logger(__name__)

Progress = Callable[[str], None]


@dataclass
class RunOptions:
    repeat: int = 1
    domains: set[Domain] | None = None
    scenario_ids: set[str] | None = None
    keep_data: bool = False
    environment: dict[str, str] = field(default_factory=dict)


def select(sets: list[DomainSet], options: RunOptions) -> list[tuple[DomainSet, Scenario]]:
    chosen = []
    for domain_set in sets:
        if options.domains is not None and domain_set.domain not in options.domains:
            continue
        for scenario in domain_set.scenarios:
            if options.scenario_ids is None or scenario.id in options.scenario_ids:
                chosen.append((domain_set, scenario))
    return chosen


async def run_evaluation(
    shared: Shared,
    systems: list[EvaluatedSystem],
    eval_dir: Path,
    options: RunOptions,
    *,
    skipped: list[SystemInfo] | None = None,
    progress: Progress | None = None,
) -> EvaluationRun:
    say = progress or (lambda _message: None)
    chosen = select(load_scenarios(eval_dir), options)
    if not chosen:
        raise ValueError("no scenarios match the selection")
    run = EvaluationRun(
        started_at=datetime.now(UTC),
        repetitions=options.repeat,
        environment=dict(options.environment),
        systems=[SystemInfo(name=s.name, label=s.label) for s in systems] + (skipped or []),
        scenarios=[
            ScenarioInfo(id=s.id, domain=d.domain.value, category=s.category, title=s.title)
            for d, s in chosen
        ],
    )
    tenant_id = await _create_tenant(shared, run.started_at)
    profiles: list[uuid.UUID] = []
    try:
        for repetition in range(1, options.repeat + 1):
            for domain_set, scenario in chosen:
                for system in systems:
                    env = await prepare(shared, tenant_id, domain_set, scenario, eval_dir)
                    profiles.append(env.profile.id)
                    transcript = await _run_one(system, scenario, env)
                    bookings = await evidence(env)
                    judgments, guards = judge(scenario, transcript, bookings)
                    run.runs.append(
                        ScenarioRun(
                            system=system.name,
                            scenario_id=scenario.id,
                            domain=domain_set.domain.value,
                            category=scenario.category,
                            repetition=repetition,
                            transcript=transcript,
                            bookings=bookings,
                            judgments=judgments,
                            guards=guards,
                        )
                    )
                    met = sum(j.correct for j in judgments)
                    say(f"run {repetition} {scenario.id} {system.name}: {met}/{len(judgments)}")
    finally:
        if not options.keep_data:
            await cleanup(shared, tenant_id, profiles)
    run.finished_at = datetime.now(UTC)
    return run


async def _run_one(system: EvaluatedSystem, scenario: Scenario, env: ScenarioEnv) -> Transcript:
    """A system that crashes fails the scenario; it doesn't stop the evaluation."""
    try:
        return await system.run(scenario, env)
    except Exception as exc:
        log.exception("evaluation_system_failed", system=system.name, scenario=scenario.id)
        return Transcript(error=f"{type(exc).__name__}: {exc}"[:300])


async def _create_tenant(shared: Shared, started: datetime) -> uuid.UUID:
    async with shared.session_factory() as db:
        tenant = Tenant(name=f"AgentIQ evaluation {started:%Y-%m-%d %H:%M:%S}")
        db.add(tenant)
        await db.commit()
        return tenant.id


async def prepare(
    shared: Shared, tenant_id: uuid.UUID, domain_set: DomainSet, scenario: Scenario, eval_dir: Path
) -> ScenarioEnv:
    """A fresh profile with the domain's documents and the scenario's setup booking."""
    config = load_example(domain_set.domain)
    clock = ScenarioClock(scenario_start(scenario, config))
    async with shared.session_factory() as db:
        profile = await ProfileService(db, SystemClock()).create(tenant_id, config)
        knowledge = KnowledgeService(db, clock, shared.settings, shared.store, shared.embedder)
        for filename in domain_set.documents:
            data = document_path(eval_dir, domain_set.domain, filename).read_bytes()
            await knowledge.upload(profile, Upload(filename, "text/markdown", data))
        reference = None
        if (setup := scenario.setup_booking) is not None:
            booking = await BookingService(db, clock).create(
                profile,
                NewBooking(
                    request=BookingRequest(
                        setup.service_id,
                        setup.party_size,
                        datetime.combine(setup.date, setup.time or datetime.min.time()),
                        setup.nights,
                    ),
                    customer=CustomerDetails(
                        scenario.caller.name, scenario.caller.phone, scenario.caller.email
                    ),
                    details=dict(scenario.caller.details),
                ),
                actor="operator:evaluation",
                source="dashboard",
            )
            reference = booking.reference
    return ScenarioEnv(
        shared=shared,
        tenant_id=tenant_id,
        profile=profile,
        config=config,
        clock=clock,
        reference=reference,
    )


async def evidence(env: ScenarioEnv) -> list[BookingEvidence]:
    """Every booking of the run's profile, as the scorer needs it."""
    overnight = env.config.booking_rules.mode is BookingMode.OVERNIGHT
    async with env.shared.session_factory() as db:
        rows = await BookingService(db, env.clock).list_bookings(env.profile)
    found = []
    for booking, _customer in rows:
        start = to_local(booking.starts_at, env.config)
        end = to_local(booking.ends_at, env.config)
        found.append(
            BookingEvidence(
                reference=booking.reference,
                service_id=booking.service_id,
                party_size=booking.party_size,
                start_local=start,
                nights=(end.date() - start.date()).days if overnight else None,
                status=booking.status,
                setup=booking.reference == env.reference,
            )
        )
    return found


async def cleanup(shared: Shared, tenant_id: uuid.UUID, profiles: list[uuid.UUID]) -> None:
    async with shared.session_factory() as db:
        # Bookings restrict deleting their customers, so they go first.
        await db.execute(delete(Booking).where(Booking.tenant_id == tenant_id))
        await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await db.commit()
    for profile_id in profiles:
        await shared.store.delete_profile(tenant_id, profile_id)
    log.info("evaluation_data_removed", tenant_id=str(tenant_id), profiles=len(profiles))


def save_run(run: EvaluationRun, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "results.json"
    path.write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_run(path: Path) -> EvaluationRun:
    return EvaluationRun.model_validate(json.loads(path.read_text(encoding="utf-8")))

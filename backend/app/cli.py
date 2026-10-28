"""Administrative commands. There is no public sign-up (analysis AS-4), so accounts are made here.

Run from backend/ with the venv active:

    python -m app.cli create-operator --business "Grand Hotel" --email sara@grand.test --name "Sara"
    python -m app.cli create-operator --tenant-id <uuid> --email staff@grand.test --name "Ali Raza"
    python -m app.cli list-tenants
    python -m app.cli seed-profiles --tenant-id <uuid>              # hotel, restaurant, clinic
    python -m app.cli seed-profiles --tenant-id <uuid> --domain clinic
    python -m app.cli reindex-knowledge [--tenant-id <uuid>]        # after changing embeddings
    python -m app.cli chat --profile-id <uuid> [--phone "+92 300 1234567"]   # talk to the assistant
    python -m app.cli check-llm                                     # test LLM_API_KEY / LLM_MODEL
    python -m app.cli check-sentiment ["I'm furious"]               # test the sentiment model
    python -m app.cli run-followups [--once]                        # send confirmations, reminders
    python -m app.cli check-notifications --phone "+92 300 1234567" # send a test SMS / email
    python -m app.cli export-openapi                                # refresh docs/api/openapi.json
    python -m app.cli evaluate [--system ivr] [--repeat 3]          # AgentIQ vs baselines (FR-10)
    python -m app.cli evaluate-report --results eval/reports/<run>/results.json
    python -m app.cli connect-number --profile-id <uuid> --number "+14155550123"   # phone calls
    python -m app.cli voice-latency [--days 7]                      # per-stage call latency
    python -m app.cli check-voice ["Your table is confirmed."]      # test TTS / STT settings
    python -m app.cli setup-demo --email demo@agentiq.test          # demo business, 3 profiles
    python -m app.cli prefetch-models                               # download models (Docker)
    python -m app.cli prepare                                       # before the API starts
    python -m app.cli sus-score eval/study/sus-responses.csv        # usability study (FR-10-07)

The password is asked for interactively (never pass it on the command line, where it
would land in shell history).
"""

import argparse
import asyncio
import getpass
import json
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.sentiment.agent import SentimentAgent
from app.auth.models import Operator
from app.auth.schemas import validate_new_email, validate_new_password
from app.auth.service import AuthService
from app.channels.voice import commands as voice_commands
from app.core.clock import SystemClock
from app.core.config import PROJECT_ROOT, Settings, get_settings
from app.core.errors import AppError
from app.db.session import create_engine, create_session_factory
from app.deployment import index_is_missing, prefetch_models, setup_demo
from app.evaluation import command as evaluate_command
from app.evaluation import sus
from app.evaluation.runner import RunOptions
from app.knowledge.embeddings import create_embedder
from app.knowledge.profile_facts import ProfileFactsIndex
from app.knowledge.service import KnowledgeService
from app.knowledge.store import ChromaVectorStore
from app.orchestrator.service import Agents, NormalizedRequest, Orchestrator
from app.profiles.examples import load_example
from app.profiles.models import BusinessProfile
from app.profiles.schema import BusinessProfileConfig, Domain
from app.profiles.service import ProfileService
from app.providers.llm import LLMClient, LLMError, create_llm
from app.providers.notifications import (
    Notice,
    NotificationError,
    Notifier,
    close_notifier,
    create_notifier,
)
from app.providers.sentiment import SentimentClassifier, create_sentiment_classifier
from app.tenancy.models import Tenant
from app.workers.followups import FollowUpWorker


def _prompt_password() -> str:
    while True:
        password = getpass.getpass("Password: ")
        try:
            validate_new_password(password)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            continue
        if getpass.getpass("Repeat password: ") != password:
            print("Passwords don't match.", file=sys.stderr)
            continue
        return password


async def _resolve_tenant(db: AsyncSession, args: argparse.Namespace) -> Tenant:
    if args.tenant_id is not None:
        tenant = await db.get(Tenant, args.tenant_id)
        if tenant is None:
            raise SystemExit(f"No tenant with id {args.tenant_id}.")
        return tenant
    tenant = Tenant(name=args.business.strip())
    db.add(tenant)
    await db.flush()
    return tenant


async def create_operator(settings: Settings, args: argparse.Namespace, password: str) -> None:
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as db:
            tenant = await _resolve_tenant(db, args)
            service = AuthService(db, settings, SystemClock())
            operator = await service.create_operator(
                tenant=tenant, email=args.email, full_name=args.name, password=password
            )
            print(f"Created operator {operator.email} ({operator.id})")
            print(f"Business: {tenant.name} (tenant id {tenant.id})")
    finally:
        await engine.dispose()


async def list_tenants(settings: Settings) -> None:
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as db:
            rows = await db.execute(
                select(Tenant.id, Tenant.name, func.count(Operator.id))
                .outerjoin(Operator, Operator.tenant_id == Tenant.id)
                .group_by(Tenant.id)
                .order_by(Tenant.created_at)
            )
            for tenant_id, name, operator_count in rows:
                print(f"{tenant_id}  {name}  ({operator_count} operators)")
    finally:
        await engine.dispose()


async def seed_profiles(settings: Settings, tenant_id: uuid.UUID, domains: list[Domain]) -> None:
    """Add the example profiles to a business, for demos and local development."""
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as db:
            if await db.get(Tenant, tenant_id) is None:
                raise SystemExit(f"No tenant with id {tenant_id}.")
            service = ProfileService(db, SystemClock())
            for domain in domains:
                profile = await service.create(tenant_id, load_example(domain))
                print(f"Created {domain.value} profile {profile.name!r} ({profile.id})")
    finally:
        await engine.dispose()


async def reindex_knowledge(settings: Settings, tenant_id: uuid.UUID | None) -> None:
    """Rebuild the search index from the documents' stored text (ADR-0004).

    Needed after changing EMBEDDING_PROVIDER or the chunking, or if the Chroma folder was
    lost. Stop the API first: the embedded Chroma shouldn't be written by two processes.
    """
    engine = create_engine(settings)
    embedder = create_embedder(settings)
    store = ChromaVectorStore(settings.chroma_directory, embedder.name)
    try:
        async with create_session_factory(engine)() as db:
            statement = select(BusinessProfile).order_by(BusinessProfile.name)
            if tenant_id is not None:
                statement = statement.where(BusinessProfile.tenant_id == tenant_id)
            service = KnowledgeService(db, SystemClock(), settings, store, embedder)
            for profile in await db.scalars(statement):
                passages = await service.reindex(profile)
                print(f"{profile.name} ({profile.id}): {passages} passages")
    finally:
        await engine.dispose()


async def chat(
    settings: Settings,
    profile_id: uuid.UUID,
    phone: str | None,
    read: Callable[[str], str] = input,
) -> None:
    """Talk to the assistant of one profile in the terminal, as a customer would (M5)."""
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    embedder = create_embedder(settings)
    llm = create_llm(settings)
    notifier = create_notifier(settings)
    try:
        async with session_factory() as db:
            profile = await db.get(BusinessProfile, profile_id)
            if profile is None:
                raise SystemExit(f"No profile with id {profile_id}.")
            tenant_id = profile.tenant_id
            config = BusinessProfileConfig.model_validate(profile.config)
        agents = Agents(
            session_factory,
            SystemClock(),
            settings,
            ChromaVectorStore(settings.chroma_directory, embedder.name),
            embedder,
            ProfileFactsIndex(embedder),
            llm,
        )
        sentiment = SentimentAgent(
            create_sentiment_classifier(settings), settings.sentiment_min_confidence
        )
        orchestrator = Orchestrator(
            session_factory, SystemClock(), settings, agents, llm, sentiment, notifier
        )
        if llm is None:
            print(
                "(No LLM_API_KEY set: every message is answered from the business's information.)"
            )
        print(f"Assistant: {config.assistant.greeting}")
        session_id = None
        while True:
            try:
                text = (await asyncio.to_thread(read, "You: ")).strip()
            except EOFError:
                break
            if text.lower() in {"", "quit", "exit", "bye"}:
                break
            outcome = await orchestrator.handle(
                NormalizedRequest(
                    tenant_id=tenant_id,
                    profile_id=profile_id,
                    session_id=session_id,
                    text=text,
                    customer_phone=phone,
                )
            )
            session_id = outcome.session_id
            mood = ""
            if outcome.sentiment is not None:
                data = outcome.sentiment.data
                mood = f", {data['label']} {data['confidence']:.2f}"
            print(f"Assistant: {outcome.reply}   [{outcome.timings['total']:.0f} ms{mood}]")
            if outcome.escalation_id is not None:
                print("(Handed over to a person: the conversation is now closed to new messages.)")
                break
    finally:
        if llm is not None:
            await llm.aclose()
        await close_notifier(notifier)
        await engine.dispose()


async def run_followups(settings: Settings, *, once: bool) -> None:
    """Run the Follow-Up worker in this process (FR-07, ADR-0007). Ctrl+C stops it.

    The API runs the same worker in the background unless FOLLOWUP_WORKER_ENABLED=false;
    running both is safe (they never take the same message)."""
    engine = create_engine(settings)
    notifier = create_notifier(settings)
    worker = FollowUpWorker(create_session_factory(engine), SystemClock(), settings, notifier)
    try:
        if once:
            report = await worker.run_once()
            print(
                f"Closed {report.closed_idle} idle conversation(s); handled "
                f"{report.booking_events} booking event(s) and {report.conversations} ended "
                f"conversation(s); queued {report.queued}; delivered {report.delivered or 'none'}."
            )
        else:
            print(f"Follow-up worker running every {settings.followup_poll_seconds:.0f} s.")
            await worker.run_forever(asyncio.Event())
    finally:
        await close_notifier(notifier)
        await engine.dispose()


async def check_notifications(
    settings: Settings,
    phone: str | None,
    email: str | None,
    notifier: Notifier | None = None,
) -> bool:
    """Send one test message, to check the SMS and email settings."""
    notifier = notifier or create_notifier(settings)
    sms = "Twilio" if settings.twilio_account_sid and settings.twilio_from_number else "log only"
    mail = settings.smtp_host or "log only"
    print(f"SMS: {sms}; email: {mail}")
    try:
        delivery = await notifier.send(
            Notice(
                subject="AgentIQ test message",
                body="This is a test message from AgentIQ. No reply is needed.",
                phone=phone,
                email=email,
            )
        )
    except NotificationError as exc:
        print(f"  failed: {exc}")
        return False
    finally:
        await close_notifier(notifier)
    if delivery == "logged":
        print("  logged only: set TWILIO_* or SMTP_* in .env to really send it.")
    else:
        print("  sent.")
    return True


async def check_llm(settings: Settings, llm: LLMClient | None = None) -> bool:
    """Send two tiny requests (plain and JSON) to the configured model and report."""
    llm = llm or create_llm(settings)
    if llm is None:
        print("No LLM_API_KEY is set in .env, so the assistant runs without a model.")
        return False
    print(f"Model {settings.llm_model} at {settings.llm_base_url}")
    try:
        answer = await llm.complete(
            [{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=200
        )
        print(f"  plain answer: {answer[:80]!r}")
        raw = await llm.complete(
            [{"role": "user", "content": 'Reply with this JSON object only: {"ok": true}'}],
            json_mode=True,
            max_tokens=200,
        )
        print(f"  JSON answer:  {raw[:80]!r}")
    except LLMError as exc:
        print(f"  failed: {exc}. Check LLM_API_KEY, LLM_BASE_URL and LLM_MODEL in .env.")
        return False
    finally:
        await llm.aclose()
    print("The model works.")
    return True


SENTIMENT_EXAMPLES = [
    "This is the third time my booking has been wrong, I'm furious.",  # TC-06
    "What time do you close today?",
    "Thank you so much, that was really helpful!",
]


def check_sentiment(
    settings: Settings, texts: list[str], classifier: SentimentClassifier | None = None
) -> None:
    """Classify a few messages with the configured sentiment model and print the result."""
    classifier = classifier or create_sentiment_classifier(settings)
    for text in texts:
        score = classifier.classify(text)
        scores = ", ".join(f"{label} {value:.2f}" for label, value in score.scores.items())
        print(f"{score.label.value:>8} {score.confidence:.2f}  ({scores})  {text}")
    print(f"Classifier: {classifier.name}")


OPENAPI_PATH = PROJECT_ROOT / "docs" / "api" / "openapi.json"


def openapi_document() -> str:
    """The API schema the dashboard's typed client is generated from (CLAUDE.md §5).

    Built from default settings, so it doesn't depend on anyone's .env."""
    from app.main import create_app

    schema = create_app(Settings(_env_file=None)).openapi()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export_openapi(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(openapi_document(), encoding="utf-8", newline="\n")
    print(f"Wrote {output}. Then run `npm run gen:api` in frontend/.")


async def demo(settings: Settings, email: str, password: str, business: str) -> None:
    setup = await setup_demo(settings, email, password, business)
    if setup.created_operator:
        print(f"Created operator {email} for {business!r} (tenant id {setup.tenant_id}).")
    else:
        print(f"Operator {email} already exists (tenant id {setup.tenant_id}); password unchanged.")
    for name in setup.created_profiles:
        print(f"Created profile {name!r}")


async def prepare(settings: Settings) -> None:
    """Run by the container before the API starts (ADR-0012)."""
    if index_is_missing(settings):
        print("Knowledge index missing: rebuilding it from the stored documents.")
        await reindex_knowledge(settings, None)
    email, password = settings.demo_operator_email, settings.demo_operator_password
    if email and password:
        await demo(settings, email, password.get_secret_value(), settings.demo_business_name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-operator", help="Create an operator account")
    owner = create.add_mutually_exclusive_group(required=True)
    owner.add_argument("--business", help="Create a new business (tenant) with this name")
    owner.add_argument("--tenant-id", type=uuid.UUID, help="Add to an existing business")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True, help="Operator's full name")

    commands.add_parser("list-tenants", help="List businesses and their operator counts")

    seed = commands.add_parser("seed-profiles", help="Add the example business profiles")
    seed.add_argument("--tenant-id", type=uuid.UUID, required=True)
    seed.add_argument(
        "--domain",
        type=Domain,
        choices=list(Domain),
        action="append",
        help="Only this domain (repeatable); default: all of them",
    )

    reindex = commands.add_parser(
        "reindex-knowledge", help="Rebuild the knowledge search index from stored documents"
    )
    reindex.add_argument("--tenant-id", type=uuid.UUID, help="Only this business")

    talk = commands.add_parser("chat", help="Talk to a profile's assistant in the terminal")
    talk.add_argument("--profile-id", type=uuid.UUID, required=True)
    talk.add_argument("--phone", help="Pretend to call from this number (caller ID)")

    commands.add_parser("check-llm", help="Check that the configured language model answers")

    mood = commands.add_parser(
        "check-sentiment", help="Classify messages with the configured sentiment model"
    )
    mood.add_argument("texts", nargs="*", metavar="TEXT", help="Messages; default: examples")

    follow = commands.add_parser(
        "run-followups", help="Send due confirmations, reminders and feedback requests"
    )
    follow.add_argument("--once", action="store_true", help="One pass, then exit")

    notify = commands.add_parser(
        "check-notifications", help="Send a test SMS and/or email with the configured settings"
    )
    notify.add_argument("--phone", help="Send a test SMS to this number")
    notify.add_argument("--email", help="Send a test email to this address")

    schema = commands.add_parser(
        "export-openapi", help="Write the API schema for the dashboard's generated client"
    )
    schema.add_argument("--output", type=Path, default=OPENAPI_PATH)

    evaluation = commands.add_parser(
        "evaluate", help="Run the evaluation scenarios on AgentIQ and the baselines (FR-10)"
    )
    evaluation.add_argument(
        "--system",
        choices=evaluate_command.SYSTEMS,
        action="append",
        help="Only this system (repeatable); default: all of them",
    )
    evaluation.add_argument(
        "--domain", type=Domain, choices=list(Domain), action="append", help="Only this domain"
    )
    evaluation.add_argument("--scenario", action="append", help="Only this scenario id")
    evaluation.add_argument("--repeat", type=int, default=1, help="Runs per scenario (default 1)")
    evaluation.add_argument("--output", type=Path, help="Folder for the report")
    evaluation.add_argument(
        "--keep-data", action="store_true", help="Keep the evaluation's business and conversations"
    )
    _evaluation_extras(evaluation)

    rebuild = commands.add_parser(
        "evaluate-report", help="Rebuild an evaluation report from its results.json"
    )
    rebuild.add_argument("--results", type=Path, required=True)
    rebuild.add_argument("--output", type=Path, help="Folder; default: next to results.json")
    _evaluation_extras(rebuild)

    number = commands.add_parser(
        "connect-number", help="Route calls to a Twilio number to a business profile"
    )
    number.add_argument("--profile-id", type=uuid.UUID, required=True)
    number.add_argument("--number", required=True, help='E.g. "+14155550123"')

    latency = commands.add_parser(
        "voice-latency", help="Per-stage response times of recent phone calls (FR-02-06)"
    )
    latency.add_argument("--profile-id", type=uuid.UUID, help="Only this profile")
    latency.add_argument("--days", type=int, default=7, help="Calls from the last N days")

    voice = commands.add_parser(
        "check-voice", help="Speak a sentence with the configured voice and transcribe it back"
    )
    voice.add_argument("text", nargs="?", default=voice_commands.SAMPLE_REPLY)

    demo_parser = commands.add_parser(
        "setup-demo", help="Create the demo business with the three example profiles"
    )
    demo_parser.add_argument("--email", required=True, help="The demo operator's login")
    demo_parser.add_argument("--business", default="AgentIQ Demo")

    commands.add_parser("prefetch-models", help="Download the embedding and sentiment models")
    commands.add_parser(
        "prepare", help="Rebuild a missing knowledge index; create the demo if DEMO_* are set"
    )

    sus = commands.add_parser("sus-score", help="Score the usability study's SUS answers")
    sus.add_argument("responses", type=Path, help="CSV: participant, q1..q10 (1-5)")
    sus.add_argument("--tasks", type=Path, help="CSV of task results (eval/study/README.md)")
    return parser


def _evaluation_extras(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--adjudications", type=Path, help="YAML file of evaluator overrides (eval/README.md)"
    )
    command.add_argument("--human", type=Path, help="CSV of human role-play results")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    try:
        if args.command == "create-operator":
            try:
                validate_new_email(args.email)  # fail before asking for a password
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            asyncio.run(create_operator(settings, args, _prompt_password()))
        elif args.command == "list-tenants":
            asyncio.run(list_tenants(settings))
        elif args.command == "seed-profiles":
            asyncio.run(seed_profiles(settings, args.tenant_id, args.domain or list(Domain)))
        elif args.command == "reindex-knowledge":
            asyncio.run(reindex_knowledge(settings, args.tenant_id))
        elif args.command == "chat":
            asyncio.run(chat(settings, args.profile_id, args.phone))
        elif args.command == "check-llm":
            return 0 if asyncio.run(check_llm(settings)) else 1
        elif args.command == "check-sentiment":
            check_sentiment(settings, args.texts or SENTIMENT_EXAMPLES)
        elif args.command == "run-followups":
            try:
                asyncio.run(run_followups(settings, once=args.once))
            except KeyboardInterrupt:
                print("Stopped.")
        elif args.command == "export-openapi":
            export_openapi(args.output)
        elif args.command == "evaluate":
            if args.repeat < 1:
                print("Error: --repeat must be at least 1.", file=sys.stderr)
                return 1
            options = RunOptions(
                repeat=args.repeat,
                domains=set(args.domain) if args.domain else None,
                scenario_ids=set(args.scenario) if args.scenario else None,
                keep_data=args.keep_data,
            )
            report = asyncio.run(
                evaluate_command.evaluate(
                    settings,
                    systems=args.system or list(evaluate_command.SYSTEMS),
                    options=options,
                    output=args.output or evaluate_command.default_output(),
                    adjudications=args.adjudications,
                    human=args.human,
                )
            )
            print(f"Report: {report}")
        elif args.command == "evaluate-report":
            report = evaluate_command.rebuild_report(
                args.results, args.output, args.adjudications, args.human
            )
            print(f"Report: {report}")
        elif args.command == "connect-number":
            connected = asyncio.run(
                voice_commands.connect_number(settings, args.profile_id, args.number)
            )
            print(f"Calls to {connected} now reach profile {args.profile_id}.")
        elif args.command == "voice-latency":
            asyncio.run(voice_commands.voice_latency(settings, args.profile_id, args.days))
        elif args.command == "check-voice":
            return 0 if asyncio.run(voice_commands.check_voice(settings, args.text)) else 1
        elif args.command == "setup-demo":
            try:
                email = validate_new_email(args.email)
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            asyncio.run(demo(settings, email, _prompt_password(), args.business))
        elif args.command == "prefetch-models":
            print("Downloaded:", ", ".join(prefetch_models(settings)) or "nothing to download")
        elif args.command == "prepare":
            asyncio.run(prepare(settings))
        elif args.command == "sus-score":
            try:
                summary = sus.summarize(sus.read_responses(args.responses))
                tasks = sus.read_tasks(args.tasks) if args.tasks else None
            except (OSError, sus.StudyDataError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            print(sus.report(summary, tasks), end="")
        elif args.command == "check-notifications":
            if not (args.phone or args.email):
                print("Error: give --phone, --email or both.", file=sys.stderr)
                return 1
            return 0 if asyncio.run(check_notifications(settings, args.phone, args.email)) else 1
    except AppError as exc:
        print(f"Error: {exc.detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

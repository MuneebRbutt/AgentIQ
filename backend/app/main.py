"""FastAPI application factory.

Run in development with `fastapi dev app/main.py` (the CLI finds the module-level `app`).
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.channels.email.mailbox import ImapMailbox
from app.channels.email.service import EmailChannel, EmailPoller
from app.channels.inbound import InboundHandler
from app.channels.voice.service import TwilioRecordings
from app.conversations.dependencies import build_orchestrator
from app.core.clock import SystemClock
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import register_middleware
from app.core.redis import create_redis
from app.core.spa import mount_dashboard
from app.db.session import create_engine, create_session_factory
from app.knowledge.embeddings import create_embedder
from app.knowledge.profile_facts import ProfileFactsIndex
from app.knowledge.store import ChromaVectorStore
from app.providers.llm import create_llm
from app.providers.notifications import SmtpEmailSender, close_notifier, create_notifier
from app.providers.sentiment import create_sentiment_classifier
from app.providers.speech import create_speech_to_text, create_text_to_speech
from app.workers.followups import FollowUpWorker

VERSION = "0.1.0"

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Clients connect lazily, so the API starts even if a dependency is down;
        # the health check then reports it as unavailable.
        app.state.engine = create_engine(settings)
        app.state.session_factory = create_session_factory(app.state.engine)
        app.state.redis = create_redis(settings)
        app.state.embedder = create_embedder(settings)
        app.state.vector_store = ChromaVectorStore(
            settings.chroma_directory, app.state.embedder.name
        )
        app.state.profile_facts = ProfileFactsIndex(app.state.embedder)
        app.state.llm = create_llm(settings)
        app.state.sentiment_classifier = create_sentiment_classifier(settings)
        app.state.notifier = create_notifier(settings)
        # Phone calls (ADR-0010): None = Twilio's own recogniser and voice.
        app.state.speech_to_text = create_speech_to_text(settings)
        app.state.text_to_speech = create_text_to_speech(settings)
        app.state.twilio_recordings = _twilio_recordings(settings)
        # Confirmations, reminders and feedback requests (FR-07, ADR-0007).
        stop_worker = asyncio.Event()
        worker_task = None
        if settings.followup_worker_enabled:
            worker = FollowUpWorker(
                app.state.session_factory, SystemClock(), settings, app.state.notifier
            )
            worker_task = asyncio.create_task(worker.run_forever(stop_worker))
        # Customers' emails (FR-02-03, ADR-0011), when a mailbox is configured.
        poller_task = None
        if settings.email_inbound_enabled:
            poller = _email_poller(app, settings)
            poller_task = asyncio.create_task(poller.run_forever(stop_worker))
        log.info("startup", environment=settings.environment.value, version=VERSION)
        try:
            yield
        finally:
            stop_worker.set()
            for task in (worker_task, poller_task):
                if task is not None:
                    await task
            await close_notifier(app.state.notifier)
            for client in (
                app.state.speech_to_text,
                app.state.text_to_speech,
                app.state.twilio_recordings,
            ):
                if client is not None:
                    await client.aclose()
            if app.state.llm is not None:
                await app.state.llm.aclose()
            await app.state.redis.aclose()
            await app.state.engine.dispose()
            log.info("shutdown")

    app = FastAPI(
        title=settings.app_name,
        version=VERSION,
        lifespan=lifespan,
        # Interactive docs are a development aid; don't publish them in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origin,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    # Dependencies that ask for settings get this app's settings, not the process-wide ones,
    # so an app built with custom settings (tests, tools) is consistent end to end.
    app.dependency_overrides[get_settings] = lambda: settings
    register_middleware(app)
    register_error_handlers(app)
    app.include_router(api_router)

    if settings.frontend_directory is not None:
        # One web service is the whole app in deployment (ADR-0012).
        mount_dashboard(app, settings.frontend_directory)
    else:

        @app.get("/", include_in_schema=False)
        async def root() -> dict[str, str]:
            return {"message": "AgentIQ API is running"}

    return app


def _email_poller(app: FastAPI, settings: Settings) -> EmailPoller:
    state = app.state
    clock = SystemClock()
    orchestrator = build_orchestrator(state, clock, settings, state.llm)
    handler = InboundHandler(state.session_factory, clock, settings, orchestrator)
    assert settings.smtp_host and settings.imap_host and settings.imap_username  # noqa: S101
    assert settings.imap_password  # noqa: S101  # checked by email_inbound_enabled
    replies = SmtpEmailSender(
        settings.smtp_host,
        settings.smtp_port,
        settings.smtp_from or settings.imap_username,
        username=settings.smtp_username,
        password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
        starttls=settings.smtp_starttls,
    )
    mailbox = ImapMailbox(
        settings.imap_host,
        settings.imap_port,
        settings.imap_username,
        settings.imap_password.get_secret_value(),
        settings.imap_folder,
    )
    channel = EmailChannel(state.session_factory, settings, handler, replies)
    return EmailPoller(mailbox, channel, settings.email_poll_seconds)


def _twilio_recordings(settings: Settings) -> TwilioRecordings | None:
    """Whisper transcribes the call recordings, which need the account's credentials."""
    if settings.twilio_account_sid is None or settings.twilio_auth_token is None:
        return None
    return TwilioRecordings(
        settings.twilio_account_sid, settings.twilio_auth_token.get_secret_value()
    )


app = create_app()

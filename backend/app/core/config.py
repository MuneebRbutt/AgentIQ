"""Application settings, loaded from environment variables and the repository-root `.env`.

Every setting is documented in `.env.example`. Connection strings use `SecretStr` so
passwords are never printed by accident (logs, tracebacks, `repr`).
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]

_SYNC_POSTGRES_PREFIXES = ("postgresql+psycopg://", "postgresql+psycopg2://", "postgresql://")

_DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-in-env"  # noqa: S105
_MIN_JWT_SECRET_LENGTH = 32


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    CONSOLE = "console"
    JSON = "json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AgentIQ API"
    environment: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE

    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://agentiq:agentiq_dev_password@localhost:55432/agentiq"
    )
    redis_url: SecretStr = SecretStr("redis://localhost:56379/0")

    # One origin, or several separated by commas.
    frontend_origin: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # Upper bound for each dependency probe in the health check (NFR04-01: never hang).
    health_check_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # --- Operator authentication (FR-01, NFR03) ---
    # Signs access tokens. The default is for local development only; production refuses it.
    jwt_secret_key: SecretStr = SecretStr(_DEV_JWT_SECRET)
    access_token_ttl_minutes: int = Field(default=60, ge=5, le=24 * 60)
    # After this many wrong passwords in a row the account is locked for a while (UC-01).
    login_max_failed_attempts: int = Field(default=5, ge=1, le=50)
    login_lockout_minutes: int = Field(default=15, ge=1, le=24 * 60)

    # --- Knowledge base (FR-05, FR-09-03; ADR-0004) ---
    # Where ChromaDB keeps its files. Relative paths are inside backend/; empty = in memory.
    chroma_path: str = "data/chroma"
    # minilm: all-MiniLM-L6-v2 (local, downloaded once, ~80 MB). hashing: a crude offline
    # stand-in used by tests; never use it for real answers.
    embedding_provider: Literal["minilm", "hashing"] = "minilm"
    knowledge_top_k: int = Field(default=4, ge=1, le=20)
    # Chunks less similar than this (0-1) don't count; below it the agent says it doesn't know.
    knowledge_min_score: float = Field(default=0.30, ge=0, le=1)
    max_document_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)

    # --- Language model (Q-1: any OpenAI-compatible chat API; ADR-0004) ---
    # Without a key, knowledge answers fall back to quoting the best-matching passage.
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = Field(default=20.0, gt=0, le=120)

    # --- Orchestrator (FR-03; ADR-0005) ---
    # Below this confidence (0-1) in its reading of a message, the assistant asks instead.
    orchestrator_min_confidence: float = Field(default=0.5, ge=0, le=1)
    # Earlier messages given to the model for context.
    conversation_history_turns: int = Field(default=10, ge=0, le=50)

    # --- Sentiment & complaints (FR-06; ADR-0006) ---
    # model: a three-class transformer from the Hugging Face Hub, run with ONNX (downloaded
    # once, ~135 MB; falls back to the lexicon if it can't load). lexicon: a word list.
    sentiment_provider: Literal["model", "lexicon"] = "model"
    sentiment_model: str = "Xenova/distilbert-base-multilingual-cased-sentiments-student"
    sentiment_model_file: str = "onnx/model_quantized.onnx"
    sentiment_model_base_url: str = "https://huggingface.co"
    # Where downloaded models are kept. Relative paths are inside backend/.
    sentiment_model_path: str = "data/models"
    # Below this confidence a negative reading never escalates; the conversation is
    # flagged for review instead (UC-06 exception).
    sentiment_min_confidence: float = Field(default=0.55, ge=0, le=1)

    # --- Follow-ups and notifications (FR-07, FR-06-04; ADR-0007) ---
    # The API process runs the follow-up worker in the background; turn it off when you
    # run `python -m app.cli run-followups` as a separate process instead.
    followup_worker_enabled: bool = True
    followup_poll_seconds: float = Field(default=30.0, ge=1, le=3600)
    # A failed message is tried once more after this long (UC-07 exception).
    followup_retry_minutes: int = Field(default=5, ge=1, le=24 * 60)
    # An open conversation with no message for this long counts as ended, which is when
    # feedback is requested (analysis M-14). 0 = only when closed explicitly.
    conversation_idle_minutes: int = Field(default=30, ge=0, le=7 * 24 * 60)
    # SMS through Twilio (analysis AS-9); all three must be set, otherwise SMS is logged only.
    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_from_number: str | None = None
    # Added to local numbers ("0300 1234567") to make them international for the gateway.
    sms_default_country_code: str = Field(default="+92", pattern=r"^\+?[0-9]{1,4}$")
    # Email over SMTP; without a host, email is logged only.
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True

    # --- Voice calls (FR-02; ADR-0010) ---
    # The address Twilio reaches this API at (an ngrok tunnel in development, the deployed
    # URL in production), without a trailing slash. Webhook signatures are checked against
    # it and synthesised audio is served from it. Empty = the URL of each request.
    public_base_url: str | None = None
    # Reject webhooks without a valid X-Twilio-Signature (needs TWILIO_AUTH_TOKEN). Turn off
    # only to try the webhooks by hand in development; production refuses it.
    twilio_validate_signature: bool = True
    # Speech to text. twilio: Twilio's own recogniser through <Gather> (no extra key, the
    # fastest). whisper: the call is recorded and sent to an OpenAI-compatible
    # /audio/transcriptions endpoint (Whisper on OpenAI or Groq).
    stt_provider: Literal["twilio", "whisper"] = "twilio"
    stt_api_key: SecretStr | None = None
    stt_base_url: str = "https://api.openai.com/v1"
    stt_model: str = "whisper-1"
    # Text to speech. twilio: <Say> with `voice_say_voice` (no extra key). openai: an
    # OpenAI-compatible /audio/speech endpoint (tts-1, analysis Q-3), played with <Play>.
    tts_provider: Literal["twilio", "openai"] = "twilio"
    tts_api_key: SecretStr | None = None
    tts_base_url: str = "https://api.openai.com/v1"
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"
    speech_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    # Twilio's speech language and <Say> voice (e.g. en-IN with Polly.Aditi for South Asian
    # English).
    voice_language: str = "en-US"
    voice_say_voice: str = "Polly.Joanna"
    # Times the caller is asked again after saying nothing, before the call ends.
    voice_max_silences: int = Field(default=2, ge=0, le=5)

    # --- Text messages and email from customers (FR-02-02/03; ADR-0011) ---
    # Messages one sender may send one business in an hour; more get no answer (analysis
    # M-15: a runaway sender would otherwise run up model and SMS costs).
    inbound_max_messages_per_hour: int = Field(default=20, ge=1, le=1000)
    # The mailbox customers write to, read over IMAP (e.g. imap.gmail.com with an app
    # password). Replies go out through the SMTP_* settings. Empty host = no email channel.
    imap_host: str | None = None
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_username: str | None = None
    imap_password: SecretStr | None = None
    imap_folder: str = "INBOX"
    email_poll_seconds: float = Field(default=30.0, ge=5, le=3600)

    # --- Deployment (FR-10-08; ADR-0012) ---
    # The dashboard's built files (frontend/dist). When set, the API serves the dashboard
    # too, so one web service is the whole app. Relative paths are inside the repository.
    frontend_dist: str | None = None
    # When both are set, `python -m app.cli prepare` (run by the container before the API
    # starts) creates this demo business once: the operator and the three example profiles.
    demo_operator_email: str | None = None
    demo_operator_password: SecretStr | None = None
    demo_business_name: str = "AgentIQ Demo"

    @field_validator(
        "public_base_url",
        "twilio_account_sid",
        "twilio_from_number",
        "smtp_host",
        "smtp_username",
        "smtp_from",
        "imap_host",
        "imap_username",
        "frontend_dist",
        "demo_operator_email",
        mode="before",
    )
    @classmethod
    def _blank_means_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator(
        "llm_api_key",
        "twilio_auth_token",
        "smtp_password",
        "stt_api_key",
        "tts_api_key",
        "imap_password",
        "demo_operator_password",
        mode="before",
    )
    @classmethod
    def _blank_key_means_none(cls, value: object) -> object:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        return None if isinstance(raw, str) and not raw.strip() else value

    @property
    def sentiment_model_directory(self) -> Path:
        path = Path(self.sentiment_model_path)
        return path if path.is_absolute() else PROJECT_ROOT / "backend" / path

    @property
    def chroma_directory(self) -> Path | None:
        if not self.chroma_path:
            return None
        path = Path(self.chroma_path)
        return path if path.is_absolute() else PROJECT_ROOT / "backend" / path

    @property
    def frontend_directory(self) -> Path | None:
        if not self.frontend_dist:
            return None
        path = Path(self.frontend_dist)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def email_inbound_enabled(self) -> bool:
        """Customers can email a business: the mailbox can be read and replies sent."""
        return bool(self.imap_host and self.imap_username and self.imap_password and self.smtp_host)

    @field_validator("database_url", mode="before")
    @classmethod
    def _use_asyncpg_driver(cls, value: object) -> object:
        # The app needs an async driver. asyncpg works with Windows' default event loop,
        # unlike async psycopg, so older `.env` files and plain `postgresql://` URLs
        # (as cloud hosts provide) are switched to it here.
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(raw, str):
            for prefix in _SYNC_POSTGRES_PREFIXES:
                if raw.startswith(prefix):
                    return "postgresql+asyncpg://" + raw.removeprefix(prefix)
        return value

    @field_validator("jwt_secret_key", mode="before")
    @classmethod
    def _blank_secret_means_default(cls, value: object) -> object:
        # `.env.example` ships `JWT_SECRET_KEY=` empty; never sign tokens with an empty key.
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(raw, str) and not raw.strip():
            return SecretStr(_DEV_JWT_SECRET)
        return value

    @field_validator("frontend_origin", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _require_real_secret_in_production(self) -> "Settings":
        if self.environment is Environment.PRODUCTION:
            secret = self.jwt_secret_key.get_secret_value()
            if secret == _DEV_JWT_SECRET or len(secret) < _MIN_JWT_SECRET_LENGTH:
                raise ValueError(
                    "JWT_SECRET_KEY must be set to a random value of at least "
                    f"{_MIN_JWT_SECRET_LENGTH} characters in production"
                )
        return self

    @model_validator(mode="after")
    def _require_signed_webhooks_in_production(self) -> "Settings":
        if self.environment is Environment.PRODUCTION and not self.twilio_validate_signature:
            raise ValueError("TWILIO_VALIDATE_SIGNATURE can't be turned off in production")
        if self.public_base_url:
            self.public_base_url = self.public_base_url.rstrip("/")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


@lru_cache
def get_settings() -> Settings:
    return Settings()

# CLAUDE.md: AgentIQ

> **Status: M0–M12 built (foundation, operator auth and tenancy, business profiles, Booking agent, Knowledge agent, orchestrator, conversation API, sentiment and escalation, follow-ups, operator dashboard, evaluation and baselines, voice pipeline, SMS/email channels, deployment and usability-study kit). M12's exit criteria (live demo, guide reproduced by an outsider, SUS results) need the team to deploy and run the study.** Sections describe the conventions in force; items marked **(planned)** arrive with later milestones, and **(pending Q-n)** items depend on an open decision in `docs/AgentIQ_Project_Analysis.md` §0 (defaults recorded in `docs/adr/0001-stack-and-foundation.md`). Update this file whenever a decision is made or a convention changes.

## 1. Project overview

AgentIQ is a multi-agent AI customer-support **calling agent** for small and medium service businesses (hotels, restaurants, clinics), built as a COMSATS Lahore final-year project (2026–27).

- **B2B2C, multi-tenant.** Operators configure a *business profile* on a web dashboard; their customers call or text the system.
- **Supervisor pattern.** A LangGraph **Orchestrator** classifies intents, decomposes multi-intent requests, and delegates to four specialist agents: **Booking**, **Knowledge (RAG)**, **Sentiment & Complaint**, **Follow-Up**.
- **Config, not code.** All domain behaviour comes from the business profile. Adding or switching a domain must not require changes to core agent code (NFR05-02, FR-08-07).
- **Text first, voice last.** Every feature is built and tested over text. The Twilio → Whisper → TTS voice layer is integrated last.
- **Research deliverable.** The system is evaluated against a single-agent baseline and IVR/human baselines on task completion, response time, retrieval accuracy and response accuracy.

Specification: `docs/spec/AgentIQ_FYP_Proposal.docx`, `docs/spec/AgentIQ_FYP1_Report.docx`. The Report wins on conflict. Requirement IDs (`FR-04-02`, `NFR03-02`) come from Report §2.3 and **must be referenced in code comments for non-obvious logic, in tests, and in PR descriptions**.

## 2. Repository conventions

```
agentiq/
├── CLAUDE.md
├── README.md
├── compose.yaml                # backing services: postgres, redis (Chroma runs embedded, ADR-0004)
├── scripts/check.ps1           # runs every quality gate locally (section 11)
├── .github/workflows/ci.yml    # same gates in CI
├── .env.example                # every env var, documented; copy to .env, never commit it
├── docs/
│   ├── spec/                   # proposal + FYP reports (source of truth)
│   ├── adr/                    # architecture decision records: NNNN-title.md
│   ├── api/openapi.json        # API schema, generated (`python -m app.cli export-openapi`)
│   └── deployment-guide.md     # FR-10-08
├── backend/
│   ├── pyproject.toml          # deps + ruff/mypy/pytest config
│   ├── requirements*.txt       # pinned, generated from pyproject.toml
│   ├── alembic/                # DB migrations
│   ├── app/
│   │   ├── main.py             # FastAPI app factory
│   │   ├── core/               # config (pydantic-settings), logging, security, errors
│   │   ├── db/                 # SQLAlchemy models, session, repositories
│   │   ├── tenancy/            # tenant context, scoping helpers
│   │   ├── auth/               # FR-01
│   │   ├── profiles/           # FR-08 business-profile schema, validation, service, examples/*.yaml
│   │   ├── bookings/           # FR-04 customers, bookings, availability rules, booking service
│   │   ├── knowledge/          # FR-05 documents: extraction, chunking, embeddings, Chroma store, profile facts
│   │   ├── conversations/      # FR-09-05 interactions (sessions + memory) and turns
│   │   ├── api/                # FastAPI routers only (thin)
│   │   ├── orchestrator/       # FR-03 service (entry point), graph, understanding, memory, planning, prompts/
│   │   ├── agents/             # contracts.py: AgentContext / AgentResult (ADR-0003)
│   │   │   ├── booking/        # FR-04
│   │   │   ├── knowledge/      # FR-05 Knowledge agent + prompts/
│   │   │   ├── sentiment/      # FR-06
│   │   │   └── followup/       # FR-07
│   │   ├── channels/           # FR-02 channels: twilio.py (signatures), inbound.py (SMS + email path, ADR-0011)
│   │   │   ├── voice/          # phone calls: numbers, TwiML, call service (ADR-0010)
│   │   │   ├── sms/            # text messages: Twilio webhook service
│   │   │   └── email/          # email: addresses, parsing, IMAP mailbox, poller + SMTP replies
│   │   ├── escalations/        # FR-06-04 escalation records, notification, operator handling
│   │   ├── followups/          # FR-07 follow-up queue (follow_up_tasks), operator API service
│   │   ├── analytics/          # FR-08-03 overview figures (SQL aggregates)
│   │   ├── evaluation/         # FR-10 harness: scenarios, baselines (single_agent, ivr), scoring, metrics, report
│   │   ├── providers/          # adapters: llm (M4), sentiment (M7), notifications: Twilio SMS + SMTP (M8), speech: Whisper STT + TTS (M11)
│   │   ├── workers/            # background jobs: the Follow-Up worker (M8)
│   │   └── deployment.py       # prepare a container: rebuild a missing index, create the demo (ADR-0012)
│   └── tests/
│       ├── unit/               # mirrors app/ layout
│       ├── integration/        # real Postgres/Redis/Chroma via docker
│       └── fixtures/           # profiles, documents, fake LLM transcripts
├── frontend/                   # React + Vite + Tailwind (TypeScript; ADR-0008)
│   └── src/{api,components,lib,pages,test}
├── eval/                       # FR-10 data (code in backend/app/evaluation, ADR-0009)
│   ├── scenarios/{hotel,restaurant,clinic}.yaml   # scenarios with their ground truth
│   ├── documents/<domain>/     # uploaded to each run's profile
│   ├── human/                  # human role-play results (CSV)
│   ├── reports/<timestamp>/    # report.md + summary.csv kept; results.json git-ignored
│   └── study/                  # usability study results (SUS answers, task sheets; FR-10-07)
└── ml/sentiment/               # DistilBERT training/eval scripts, model card
```

- **Branches:** `main` is always deployable. Work on `feat/<module>-<short-desc>`, `fix/...`, `docs/...`, `chore/...`.
- **Commits:** Conventional Commits (`feat(booking): propose alternative slots (FR-04-02)`).
- **PRs:** one module concern per PR, linked to requirement IDs, at least one teammate review, CI green.
- **ADRs:** any change to stack, data model, agent contracts or profile schema gets an ADR in `docs/adr/`.
- **Generated or large files** (model weights, Chroma data, eval outputs, audio) are git-ignored.

## 3. Architecture summary

```
Channels (Twilio voice + STT/TTS, SMS, dashboard text console)
   │  NormalizedRequest {tenant_id, profile_id, session_id, channel, text, customer_ref}
   ▼
Orchestrator (LangGraph StateGraph per turn; memory in Postgres `interactions`, ADR-0005)
   load memory → understand (intents + subtasks, one structured LLM call)
     → fan-out: Booking | Knowledge   (parallel when independent, sequenced when dependent)
     + always: Sentiment on every customer turn, beside understand (side branch, ADR-0006)
     → escalate when the profile's rules fire (hand-off message, escalation record, notify)
     → reply → save memory + turns
   on low confidence → clarify (hand-off after repeated attempts)
   Domain events (booking_events rows, ended interactions) → Follow-Up worker (Postgres queue, ADR-0007)
   ▼
Data: PostgreSQL (source of truth + follow-up queue) · ChromaDB (per-tenant collections) · Redis (cache)
Dashboard (React) ⇄ FastAPI REST (JWT) · Eval harness ⇄ same orchestrator entrypoint
```

**Architectural rules (enforced in review):**
1. **Tenant isolation.** Every repository method and vector query takes `tenant_id` explicitly. Tenant ID comes from the JWT or the Twilio number mapping, **never from LLM output**. Each tenant gets its own Chroma collection. (NFR03-02)
2. **Agents are pure-ish modules.** An agent receives a typed `Subtask` + `AgentContext` (profile, customer, session snapshot) and returns a typed `AgentResult`. It does not mutate graph state directly; the orchestrator merges results. (NFR05-01)
3. **Business rules live in tools, not prompts.** Availability, capacity, cancellation windows and tenant checks are enforced in Python tool functions. The LLM picks tools and fills arguments; tools validate everything.
4. **Providers behind interfaces.** LLM, embeddings, STT, TTS, SMS/email go through `app/providers/*` protocols so tests use fakes and vendors can be swapped (pending Q-1/Q-2/Q-3).
5. **No domain `if` statements in core code.** `if domain == "clinic"` in `orchestrator/` or `agents/` is a bug. Put differences in the profile (resource types, required booking fields, policies, prompt snippets).
6. **Postgres is the source of truth.** Conversation state is checkpointed to Postgres; Redis is a cache only (NFR04-02/03).
7. **Graceful degradation.** Every external call has a timeout and a fallback reply; the customer never gets silence or a stack trace (NFR04-01).
8. **Latency is measured, not guessed.** Each turn records per-stage timings (STT, understand, each agent, aggregate, TTS) (FR-02-06, FR-10-02).

**Auth and tenancy conventions (M1, in force):**
- A route becomes login-only by taking `auth: CurrentAuth` (`app/auth/dependencies.py`). `auth.tenant_id` is the only tenant ID a request may use.
- Tenant-owned models inherit `TenantOwnedMixin`; every query on them goes through `scoped(select(Model), Model, auth.tenant_id)` (`app/tenancy/scoping.py`). Another tenant's row is answered with 404, exactly like a missing one.
- Tokens are HS256 JWTs (`sub` operator, `sid` session) backed by an `operator_sessions` row; logout revokes the row, and deactivating an operator ends their sessions at once (FR-01-04).
- Every login failure (unknown email, wrong password, inactive, locked) returns the same 401 "Invalid email or password." (FR-01-03). 5 wrong passwords lock the account for 15 minutes (configurable).
- Accounts are created with `python -m app.cli create-operator`; there is no public sign-up (analysis AS-4).
- Time comes from the injectable `Clock` (`app/core/clock.py`), never `datetime.now()` in services; tests override `get_clock`.

**Business profiles (M2, in force; ADR-0002):**
- A profile is one validated `BusinessProfileConfig` document (`app/profiles/schema.py`) stored as JSONB. Add settings there, never as domain branches in code.
- Bookable capacity is generic: `resources` (quantity x capacity, optional `service_ids`) plus `booking_rules.mode` = `time_slot` or `overnight`. Agents read these; they never read `domain`.
- Every save bumps `version` and writes a snapshot; `PUT /profiles/{id}` must send the version it edited, else 409.
- `app/profiles/examples/*.yaml` are the starter templates and seed data; keep them valid (unit-tested).

**Bookings and agents (M3, in force; ADR-0003):**
- All availability logic is in `app/bookings/availability.py` (pure, no DB). Change rules there, never in prompts or agents.
- Create/change go through `BookingService`, which re-checks availability under a per-profile advisory lock and writes a `BookingEvent` for every change.
- Customers change or cancel with reference + matching phone; the cancellation-notice rule applies to customers, operators may override.
- Agents take `AgentContext` + a typed subtask and return `AgentResult` (`completed`, `needs_info`, `needs_confirmation`, `unavailable`, `rejected`, and `no_answer` from M4). Nothing is booked without `confirmed=true` (FR-04-03). Trusted facts (tenant, profile, caller phone) come from the context, never from the model.

**Knowledge base (M4, in force; ADR-0004):**
- Documents live in `knowledge_documents` (Postgres keeps the extracted text); passages live in ChromaDB, **embedded** in the API process (`CHROMA_PATH`, default `backend/data/chroma`), one collection per tenant, every query also filtered by `profile_id`.
- All Chroma access goes through `ChromaVectorStore`; embeddings through an `Embedder` (`minilm` for real use, `hashing` for tests). Changing the embedder or chunking means running `python -m app.cli reindex-knowledge`.
- Uploading a file with an existing name replaces it (FR-05-06). Supported: .pdf, .docx, .txt, .md up to `MAX_DOCUMENT_BYTES`.
- The Knowledge agent answers only from passages above `KNOWLEDGE_MIN_SCORE`; otherwise `no_answer` (FR-05-05). The LLM (any OpenAI-compatible API, `app/providers/llm.py`) must reply `INSUFFICIENT_INFORMATION` when the passages don't answer; without a key, or on failure, the agent quotes matching sentences instead.
- Prompts live in `app/agents/<agent>/prompts/*.md`.
- The Knowledge agent also answers from the profile itself (hours, contact, services, policies, FAQs; `profile_facts.py`, ADR-0005).

**Orchestrator (M5, in force; ADR-0005):**
- Every channel calls `Orchestrator.handle(NormalizedRequest)`; tenant and profile come from the channel, never the conversation.
- Conversation state is our own: `interactions.memory` (booking draft, awaiting confirmation, clarification count) and `turns`. No LangGraph checkpointer (it needs async psycopg).
- Booking confirmation is enforced in `orchestrator/memory.py` + `planning.py`: commit only after a read-back and an unchanged "yes". The model can never set `confirmed`.
- Without a model, messages go to the Knowledge agent and replies come from templates (`phrasing.py`); every failure path still produces a reply.
- Agents run in parallel, each with its own DB session; `depends_on` tasks run after, and only if their dependencies completed.

**Conversation API (M6, in force):**
- `/api/v1/profiles/{id}/sessions` (`app/api/v1/sessions.py`): start, send a message, list, read with turns, close. It is the text channel and the test console's backend (FR-08-02/03); the orchestrator is built per request by `get_orchestrator` (`app/conversations/dependencies.py`), so tests swap the model by overriding `get_llm`.
- Every reply returns, and every assistant turn stores, `timings` in ms: `understand`, `agent.<agent>.<task id>`, `reply`, `total` (FR-10-02).
- Only `open` conversations take messages; `handed_off` and `closed` ones answer 409.

**Sentiment & escalation (M7, in force; ADR-0006):**
- The Sentiment agent (`app/agents/sentiment/agent.py`) runs on every customer message in parallel with understanding; `decide` in the graph applies its verdict first. Escalation rules are code (`assess`); their numbers live in the profile's `escalation` section (threshold, keywords, `negative_turns_in_a_row`).
- Classifiers sit behind `SentimentClassifier` (`app/providers/sentiment.py`): `model` (DistilBERT three-class via ONNX, downloaded once, falls back to the lexicon) or `lexicon` (tests). Check with `python -m app.cli check-sentiment`.
- Uncertain readings (< `SENTIMENT_MIN_CONFIDENCE`) never escalate on sentiment; they set `interactions.needs_review`.
- Escalating = conversation `handed_off` + `escalations` row + `Notifier` call after commit (retried once by the Follow-Up worker if it fails) + the profile's hand-off message. Every customer message gets a `sentiment_records` row.

**Follow-ups (M8, in force; ADR-0007):**
- Event-driven, never inside a turn: the worker (`app/workers/followups.py`) consumes `booking_events` and ended interactions (rows with `followed_up_at` null), asks the pure `FollowUpAgent` what to send, and queues rows in `follow_up_tasks`. Postgres is the queue (`FOR UPDATE SKIP LOCKED`); don't add a Redis job queue.
- Timing, channel and wording come from the profile's `follow_up` section (templates with a fixed placeholder list, checked on save). No model writes these messages.
- Every send goes through `Notifier` (`create_notifier`: Twilio SMS and/or SMTP email from settings, else `LogNotifier` → status `logged`). A failure is retried once after `FOLLOWUP_RETRY_MINUTES`, then `failed`.
- The API runs the worker in the background (`FOLLOWUP_WORKER_ENABLED`); `python -m app.cli run-followups [--once]` runs it alone. Conversations quiet for `CONVERSATION_IDLE_MINUTES` are closed. Tests disable the background worker and call `run_once()` with the fake clock.

**Operator dashboard (M9, in force; ADR-0008):**
- `frontend/`: React Router screens in `src/pages` (`profile/` = the tabs of one profile), server data through TanStack Query, every call through the typed `api` client (`src/api/client.ts`) generated from `docs/api/openapi.json`. After changing an API schema: `python -m app.cli export-openapi`, then `npm run gen:api`, and commit both (CI fails on drift).
- The profile editor never copies schema rules: it validates the draft with `POST /profiles/validate` and shows each error under its field (`lib/errors.ts` turns pydantic errors into plain words, NFR02-02). Add a profile setting by adding its field to `components/editor/ProfileEditor.tsx`.
- Shared look lives in `components/ui.tsx` (Tailwind); status colours in `lib/status.ts`. Files that export components export only components (oxlint `only-export-components`); hooks and helpers go in their own modules.
- Page tests render the whole app with `renderApp` against `fakeApi` (`src/test/`); don't mock modules.
- `GET /profiles/{id}/analytics?days=` (`app/analytics/service.py`) computes the overview from existing tables.

**Evaluation (M10, in force; ADR-0009):**
- `python -m app.cli evaluate` runs every scenario on AgentIQ, the single-agent baseline and the IVR baseline, each in a fresh copy of the example profile in a throwaway tenant (deleted after, unless `--keep-data`), and writes `eval/reports/<timestamp>/`. Notifications are always `LogNotifier`.
- Ground truth lives in the scenario (`expect`: answer, refusal, booking, cancelled, escalation); files are validated against the example profiles on load. Add a scenario by adding it to its domain's YAML, never by special-casing code.
- Scoring (`scoring.py`) looks only at the transcript, hand-off and bookings, so every system is judged alike. Overrides go in an adjudications YAML (`evaluate-report --adjudications`), never into `results.json`.
- The single agent shares AgentIQ's model, retrieval and Booking agent on purpose; keep it that way so the comparison isolates the architecture. Metric definitions and the human-baseline protocol: `eval/README.md`.

**Voice (M11, in force; ADR-0010):**
- Turn-based Twilio calls: `/api/v1/voice/{incoming,turn,silence,status}` webhooks answer TwiML built in `channels/voice/twiml.py`; `VoiceService` (`channels/voice/service.py`) does the work and hands each utterance to `Orchestrator.handle` with `channel="voice"`. Streaming only if profiling demands it (Q-4).
- A call's tenant and profile come from the dialled number (`phone_numbers`, managed on the dashboard's Calls, SMS & email tab), never from the call's content. Every webhook needs a valid `X-Twilio-Signature` checked against `PUBLIC_BASE_URL`; production refuses to turn that off.
- Speech is pluggable (`app/providers/speech.py`): Twilio's recogniser and `<Say>` by default, Whisper (`STT_PROVIDER=whisper`) and an OpenAI-compatible voice (`TTS_PROVIDER=openai`, audio served from Redis) when configured. A failing speech service falls back to Twilio's; a failing webhook still answers 200 with a spoken apology.
- Per-stage timings live on `voice_calls.timings` and the assistant turn (`stt`, `tts`); `python -m app.cli voice-latency` reports them. Replies pass through `speakable()` (no markdown, codes spelled out).

**Text messages and email (M12, in force; ADR-0011):**
- Both go through `InboundHandler` (`channels/inbound.py`): recorded once per provider id in `inbound_messages` (retries are never answered twice), rate-limited per sender (`INBOUND_MAX_MESSAGES_PER_HOUR`), continued in the sender's open conversation (`customer_phone` for SMS, `customer_email` for email) or a new one, then `Orchestrator.handle` with `channel="sms"|"email"`.
- SMS: `/api/v1/sms/incoming`, signature-checked like the voice webhooks (`channels/twilio.py`), routed by the same `phone_numbers`, answered with `<Message>` TwiML; carrier keywords (STOP, HELP) get no reply.
- Email: `EmailPoller` reads one IMAP mailbox in the API process when `IMAP_*` and `SMTP_*` are set; `email_addresses` (often plus addresses) route messages to profiles. Only new text reaches the assistant (quotes and signatures cut); automatic mail and AgentIQ's own addresses are never answered; replies thread and carry `Auto-Submitted: auto-replied`.
- Operators manage numbers and addresses on the dashboard's **Calls, SMS & email** tab.

**Deployment (M12, in force; ADR-0012):**
- One image, one process: the API serves the dashboard when `FRONTEND_DIST` is set (`core/spa.py`; `/api/...` never falls back to `index.html`). `backend/docker/start.sh` migrates, runs `app.cli prepare`, then uvicorn. Keep a single process while Chroma is embedded and the workers run in it.
- The Chroma index is disposable: `prepare` rebuilds it from Postgres when missing. Models are baked into the image (`prefetch-models`).

**Core domain entities** (profile and resources settled in ADR-0002, bookings in ADR-0003; the rest as their milestones land): `Operator`, `Tenant`, `BusinessProfile` (versioned), `Resource` (room type / table / practitioner), `Slot`/capacity rules, `Customer`, `Booking` + `BookingEvent`, `Interaction` (session) + `Turn`, `SentimentRecord`, `Escalation`, `KnowledgeDocument` + chunks, `FollowUpTask`, `PhoneNumber` and `EmailAddress`→profile mappings, `InboundMessage`, `EvalRun`/`EvalResult`.

## 4. Coding standards

**Python (backend, eval, ml)**
- Python 3.12. Type hints everywhere; `mypy --strict` on `app/`.
- Formatting and lint: `ruff format` + `ruff check` (rules: E, F, I, B, UP, S, ASYNC, SIM).
- Pydantic v2 for all API, agent and profile schemas. SQLAlchemy 2.x (async, **asyncpg** driver: async psycopg can't run on Windows' default event loop) + Alembic.
- Async I/O end to end in FastAPI paths. No blocking calls (model inference, file parsing) on the event loop; use a thread pool or a worker.
- Routers are thin: validate, call a service, map errors. Business logic lives in services and agents.
- Errors: domain exceptions in `core/errors.py`, mapped to HTTP problem responses in one place. Never leak internals or whether an email exists (FR-01-03).
- Logging: structured JSON via `structlog`, always with `tenant_id`, `session_id`, `request_id`. **Never log secrets, passwords, full phone numbers or raw audio.**
- Prompts live in versioned files under each agent (`prompts/*.md` or `.jinja`), never inline in long string literals.
- LLM calls: temperature 0 for routing/extraction, structured output (JSON schema / tool calling), explicit timeouts and retry limits.
- Names: `snake_case` modules and functions, `PascalCase` classes, agents named `<Name>Agent`, tools as verbs (`check_availability`, `create_booking`).

**TypeScript/React (frontend)**
- Vite + React 19 + strict TypeScript + Tailwind v4, React Router, TanStack Query, openapi-fetch. Lint with **oxlint** (`--deny-warnings`); tests with Vitest + Testing Library.
- Typed API client generated from the backend OpenAPI schema; no hand-written fetch shapes.
- Forms validated client-side and server-side; server is authoritative (FR-08-05).
- Functional components and hooks; server state via TanStack Query.

**General**
- Comment *why*, not *what*. Cite requirement IDs where logic exists because of a requirement.
- No secrets in code or fixtures. Config via environment variables documented in `.env.example`.

## 5. Build rules

- `docker compose up -d` brings up the backing services (Postgres 18, Redis; ChromaDB runs embedded in the API, ADR-0004) from a clean clone with `.env` copied from `.env.example`. The API and frontend run on the host during development. `docker compose --profile app up -d --build` adds the deployable image (root `Dockerfile`: API serving the built dashboard, ADR-0012), which Render runs from `render.yaml` (guide: `docs/deployment.md`).
- Backend: direct dependencies in `backend/pyproject.toml`; exact pins in `requirements.txt` (runtime) and `requirements-dev.txt` (runtime + tools), both generated with `uv pip compile --universal` and committed, so Windows, macOS, Linux and CI install identical versions with plain `pip`. Never hand-edit or `pip freeze` into them (a PowerShell `>` redirect writes UTF-16, which pip can't read). Frontend: **npm** with `package-lock.json` committed.
- DB schema changes **only** via Alembic migrations. Migrations must apply to an empty DB and upgrade the previous release; no editing merged migrations.
- The OpenAPI schema is regenerated on build and the frontend client regenerated from it; CI fails if they drift.
- Images are multi-stage, run as non-root, and contain no `.env` or test data.
- Seed command loads the three example profiles and synthetic data so any dev can demo in minutes.

## 6. Dependency guidelines

- **Pin everything** through lockfiles. LangGraph/LangChain change fast: pin exact versions and upgrade deliberately in a dedicated PR with the golden-scenario suite green.
- Prefer the stack the spec names: FastAPI, LangGraph, LangChain (tool-calling only), ChromaDB, PostgreSQL, Redis, HuggingFace Transformers (DistilBERT), Twilio SDK, Whisper (API) and an OpenAI-compatible TTS.
- Adding a new runtime dependency requires a one-line justification in the PR and must be actively maintained with a permissive licence (MIT/BSD/Apache).
- Don't pull in a framework for something the stack already does (no second ORM, no second agent framework, no second vector DB).
- Heavy ML deps (`torch`, `transformers`) are isolated in an optional extra/the worker image so the API image stays small.
- External paid APIs are always called through `app/providers`, with a fake implementation for tests and a cost note in the provider docstring.

## 7. Testing strategy

| Layer | What | Tools | LLM? |
|---|---|---|---|
| **Unit** | Tools, services, profile validation, booking rules, chunking, sentiment rules, prompt rendering, graph routing with fake LLM | pytest, pytest-asyncio | **Never.** Use `FakeLLM` with scripted structured outputs |
| **Integration** | Repositories against real Postgres; Chroma ingestion/retrieval; Redis queue; API endpoints with auth; **tenant isolation suite** | pytest + docker compose services, httpx `AsyncClient` | Fake |
| **Contract** | Every agent's `Subtask → AgentResult` schema; Twilio webhook payloads; OpenAPI ↔ frontend client | pytest, schemathesis (optional) | Fake |
| **Golden scenarios** | The evaluation scenarios run on AgentIQ alone, checked for task completion versus `main` | `python -m app.cli evaluate --system agentiq` | **Real**, temperature 0, before merge to `main` on agent/prompt changes |
| **Evaluation** | Full scenario sets vs baselines (FR-10); harness tested with scripted models (TC-10) | `python -m app.cli evaluate` | Real; repeated runs, report mean ± sd |
| **Frontend** | Components and forms; key flows (login, create profile, upload doc, test console) | Vitest + Testing Library; Playwright for e2e | n/a |
| **Voice** | Webhook/TwiML unit tests; recorded-audio STT tests; manual call checklist | pytest, recorded fixtures | Real for manual only |

- Planned test cases TC-01…TC-10 (Report Ch. 4) are implemented as automated tests named `test_tc01_...` where feasible.
- Every bug fix lands with a regression test.
- Time-dependent logic (reminders, cancellation windows) uses an injectable clock (TC-07 "advance system clock").
- Mandatory negative tests: invalid login, cross-tenant access, unavailable slot, out-of-scope knowledge question ("insufficient information"), low-confidence sentiment, provider timeout fallback.

## 8. Quality gates

A PR may merge only when:
1. `scripts/check.ps1` (or CI) passes: ruff format + lint, mypy strict, oxlint, tsc. No new `# type: ignore` / lint-disable comment without a reason.
2. pytest and Vitest pass. Coverage ≥ **80 %** on `app/agents`, `app/orchestrator`, `app/profiles`, `app/auth`, `app/tenancy`; ≥ 70 % overall.
3. Tenant-isolation and auth integration tests pass (NFR03).
4. Migrations apply cleanly up from empty; the OpenAPI client is in sync.
5. If prompts, agents, orchestrator or profile schema changed: golden-scenario suite passes with no regression in task-completion rate versus `main`.
6. No secrets detected (`gitleaks`), no high-severity dependency vulnerabilities (`pip-audit`, `npm audit`).
7. A teammate approved, and the PR description lists requirement IDs and how they were verified.

Release/milestone gates (provisional until profiling, see analysis AS-15): task completion ≥ 70 % on the scenario set; text reply p95 ≤ 5 s; retrieval ≤ 300 ms; voice p95 target set after the first profiling run; SUS ≥ 68 in the usability study.

## 9. Development workflow

1. **Pick a roadmap item** (section 10) and its requirement IDs; open or claim an issue.
2. **Design first for anything cross-module**: agent contract, schema or profile changes get a short ADR or issue comment agreed before coding.
3. Branch from `main`, write tests alongside code (test-first for tools and rules).
4. Run `.\scripts\check.ps1` locally with `docker compose up -d` running (so integration tests run too).
5. Open a PR using the template (requirement IDs, what changed, how verified, screenshots for UI).
6. Review → CI green → squash merge.
7. After merging agent/prompt changes, check the nightly golden-scenario report.

**Rules for Claude working in this repo**
- Read this file and the relevant requirement IDs before changing code. If the spec is ambiguous, check `docs/AgentIQ_Project_Analysis.md` for the recorded assumption; if none exists, ask rather than invent.
- Never hard-code domain behaviour, never take tenant IDs from model output, never call real paid APIs in tests.
- Keep changes scoped to one module; don't refactor unrelated code in the same PR.
- Don't add dependencies, change the stack, or alter the profile/DB schema without an ADR the team has approved.
- Never commit `.env`, keys, real customer data, or real patient data. Seed and test data are synthetic.

## 10. Module implementation roadmap

Ordered to de-risk early: data model and profile first, then a text-only vertical slice, voice last.

| Milestone | Scope | Requirements | Exit criteria |
|---|---|---|---|
| **M0 Foundation** ✅ | Repo layout, compose (Postgres + Redis), settings, structured logging, request IDs, problem-JSON errors, health checks, Alembic, CI, check script, ADR-0001 | NFR05-03, NFR04-01 | `docker compose up -d` + `scripts/check.ps1` green on a clean clone |
| **M1 Auth & tenancy** ✅ | Operator model, bcrypt, JWT + revocation, login/logout, tenant context, seed operator CLI | FR-01, NFR03 | TC-01 passes; cross-tenant tests pass |
| **M2 Business profile & data model** ✅ | ADR-0002 profile schema + generic resource/slot model; CRUD API; validation; 3 example profiles | FR-08-04/05/06/07, FR-09-01/02 | All three seed profiles validate; invalid profile blocked (TC-08 step 2) |
| **M3 Booking agent (text)** ✅ | Tools: check_availability, create/modify/cancel, propose alternatives, cancellation rules; booking events | FR-04 | TC-04 passes in all three domains with no domain branches |
| **M4 Knowledge agent** ✅ | Ingestion (parse, chunk, embed, per-tenant collection), retrieval with threshold, grounded answer, refusal, re-upload replaces | FR-05, FR-09-03 | TC-05 passes; refusal on out-of-scope questions |
| **M5 Orchestrator v1** ✅ | LangGraph graph, state + Postgres checkpointer, understand node, routing, parallel fan-out, dependency sequencing, aggregation, clarification | FR-03, FR-09-05, NFR04-03 | TC-03 passes; multi-turn session survives backend restart (TC-09 step 3) |
| **M6 Text channel & test console API** ✅ | `/sessions` + `/messages` endpoints, per-stage timing | FR-08-02, FR-02-06 (text), FR-10-02 | End-to-end text conversation via API |
| **M7 Sentiment & complaint** ✅ | Baseline HF model + rules; per-turn side branch; escalation records + operator notification; then fine-tuned DistilBERT if needed | FR-06 | TC-06 passes; low-confidence path flags for review |
| **M8 Follow-up** ✅ | Event consumers, scheduled reminders, feedback requests, SMS/email providers, retry-once | FR-07 | TC-07 passes with injected clock |
| **M9 Operator dashboard** ✅ | Login, profile editor, doc upload, test console, logs/escalations/analytics views | FR-08, NFR02 | TC-08 passes; new operator sets up a profile unaided |
| **M10 Evaluation & baselines** ✅ | Scenario sets per domain, ground truth, single-agent baseline, IVR baseline, metrics, reports | FR-10-01…06 | TC-10 produces a comparative report |
| **M11 Voice pipeline** ✅ | Twilio number→profile mapping, webhooks, Whisper STT, TTS, fallbacks, latency profiling; streaming only if needed (Q-4) | FR-02-01/04/05/06, NFR01-01 | TC-02 passes; latency report with per-stage breakdown |
| **M12 Cross-domain deployment & study** ✅ (built; deploy and study pending) | Deploy (Render), usability study (SUS), deployment guide, optional SMS/email inbound | FR-10-07/08, FR-02-02/03 | Three live demo profiles; guide reproduced by someone outside the team |

Parallelization for three people after M2: Booking (M3) ∥ Knowledge (M4) ∥ Orchestrator skeleton with fake agents (M5). Then Sentiment ∥ Follow-up ∥ Dashboard.

## 11. Standard development commands

Windows PowerShell, from the repository root unless noted. (macOS/Linux: same commands; activate with `source .venv/bin/activate`.)

```powershell
# One-time setup
Copy-Item .env.example .env
docker compose up -d                       # Postgres :55432, Redis :56379
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1                # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements-dev.txt
alembic upgrade head
cd ..\frontend
npm ci

# Daily
docker compose up -d                       # start services     | docker compose stop  (keeps data)
cd backend; fastapi dev app\main.py        # API on :8000, docs at /docs
cd frontend; npm run dev                   # dashboard on :5173

# Quality gates (same as CI)
.\scripts\check.ps1                        # everything
.\scripts\check.ps1 -Backend               # or -Frontend
.\scripts\check.ps1 -Fix                   # auto-format, then check

# Backend, from backend\ with the venv active
ruff format . ; ruff check --fix .
mypy
pytest                                     # unit + integration (uses a separate agentiq_test DB; skips if services are down)
pytest -m "not integration"                # unit only
pytest --cov                               # with coverage
alembic revision --autogenerate -m "add bookings"
alembic upgrade head                       # alembic downgrade -1 to roll back one step
python -m app.cli create-operator --business "Grand Hotel" --email owner@grand.test --name "Sara Khan"
python -m app.cli list-tenants
python -m app.cli seed-profiles --tenant-id <id>     # example hotel, restaurant and clinic profiles
python -m app.cli reindex-knowledge                  # rebuild the Chroma index (API stopped)
python -m app.cli chat --profile-id <id>             # talk to a profile's assistant (needs LLM_API_KEY for bookings)
python -m app.cli check-llm                          # check the configured model answers (plain and JSON)
python -m app.cli check-sentiment ["text"]           # classify messages with the sentiment model (downloads it once)
python -m app.cli run-followups [--once]             # send due confirmations, reminders, feedback requests
python -m app.cli check-notifications --phone "+92 300 1234567"   # test the SMS / email settings
python -m app.cli export-openapi                     # rewrite docs/api/openapi.json after an API change
python -m app.cli connect-number --profile-id <id> --number "+14155550123"   # route a Twilio number's calls and texts (or the dashboard tab)
python -m app.cli voice-latency [--days 7]           # per-stage response times of recent calls (FR-02-06)
python -m app.cli check-voice ["text"]               # speak with the configured TTS and transcribe it back
python -m app.cli setup-demo --email demo@agentiq.test   # demo business with the three example profiles
python -m app.cli prefetch-models                    # download the embedding and sentiment models (image build)
python -m app.cli prepare                            # container start: rebuild a missing index, create the demo from DEMO_*
python -m app.cli sus-score ..\eval\study\sus-responses.csv [--tasks ..\eval\study\tasks.csv]   # FR-10-07
python -m app.cli evaluate [--system ivr] [--domain hotel] [--scenario R05] [--repeat 3] [--keep-data]   # FR-10 (needs LLM_API_KEY for real numbers)
python -m app.cli evaluate-report --results ..\eval\reports\<run>\results.json [--adjudications ..\eval\adjudications.yaml] [--human ..\eval\human\results.csv]
uv pip compile pyproject.toml --extra dev --universal --python-version 3.12 -o requirements-dev.txt   # after changing deps (and the same without --extra dev for requirements.txt)

# Frontend, from frontend\
npm run lint ; npm run typecheck ; npm test ; npm run build
npm run gen:api                            # regenerate src/api/schema.d.ts from docs/api/openapi.json
```

For phone calls in development: `ngrok http 8000`, put the https URL in `PUBLIC_BASE_URL`, restart the API, and paste the webhook URLs from the Calls, SMS & email tab into the Twilio number (calls and texts).

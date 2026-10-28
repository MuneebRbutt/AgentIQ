# ADR-0001: Stack defaults and project foundation

- **Status:** Accepted (defaults; the team may revisit any row by superseding this ADR). Dashboard libraries: see ADR-0008.
- **Date:** 2026-10-17
- **Context source:** `docs/AgentIQ_Project_Analysis.md` §0 (open decisions Q-1…Q-10)

## Context

The proposal and FYP-I report disagree on parts of the stack, and several choices were left
open. Implementation was approved without answers to the open decisions, so the
recommended defaults are adopted here so that work can proceed. The repository already had
a minimal FastAPI + Postgres 18 + Vite/React scaffold developed on Windows/PowerShell.

## Decisions

### Open decisions (defaults adopted)

| ID | Decision | Adopted default | Takes effect |
|---|---|---|---|
| Q-1 | LLM | Provider-agnostic wrapper; exact model ID pinned in config | M3/M5 |
| Q-2 | Embeddings | Sentence-Transformers (local, free) | M4 |
| Q-3 | TTS | OpenAI `tts-1` (lower latency than `tts-1-hd`) | M11 |
| Q-4 | Voice architecture | Turn-based Twilio first; streaming only if latency targets are missed | M11 |
| Q-5 | Baselines | Single-agent (code), IVR (scripted decision tree), human (small timed role-play) | M10 |
| Q-6 | Escalation | Escalation record + dashboard flag + operator notification + customer told they'll be contacted | M7 |
| Q-7 | Booking model | Generic resource + slot/capacity model configured by the business profile | M2 (ADR-0002) |
| Q-8 | Task queue | Redis-backed queue (arq or Celery, chosen in M8) | M8 |
| Q-9 | Frontend language | TypeScript (already used by the existing scaffold) | now |
| Q-10 | Channels | Dashboard text console + optional Twilio SMS; inbound email deferred | M6/M12 |

### Foundation choices made in M0

1. **Keep the team's scaffold and Windows workflow.** Postgres 18 via `compose.yaml` on port
   55432, the API run on the host with `fastapi dev`, the frontend with `npm run dev`, a
   single repository-root `.env`.
2. **Async SQLAlchemy with asyncpg.** The API will spend most of its time waiting on LLMs and
   other services, so request handling is async end to end. Async psycopg refuses to run on
   Windows' default (Proactor) event loop; asyncpg works there. Settings convert existing
   `postgresql+psycopg://` / `postgresql://` URLs automatically, so old `.env` files keep
   working.
3. **Dependencies:** `pyproject.toml` holds direct dependencies and tool config;
   `requirements.txt` / `requirements-dev.txt` are generated, universal (all OSes) pins
   installed with plain `pip`. This replaces the previous `pip freeze` output, which was
   UTF-16 encoded by PowerShell and Windows-specific.
4. **Redis now, Chroma later.** Redis joins compose in M0 (health-checked, used from M5/M8);
   ChromaDB is added with the Knowledge agent in M4 to avoid pulling a service nobody uses yet.
   (ADR-0004 later chose to run Chroma embedded in the API instead of as a compose service.)
5. **Cross-cutting conventions:** structlog (console locally, JSON in servers), a request ID
   on every request and log line, RFC 9457 problem-JSON errors from one handler module,
   health checks that probe each dependency with a timeout and answer 503 when degraded.
6. **Quality gates:** ruff (format + lint), mypy strict, pytest (+ integration tests that skip
   when services are down), oxlint, tsc, Vitest, Vite build. Same gates in
   `scripts/check.ps1` and GitHub Actions CI.
7. **No Makefile.** The team develops on Windows, where `make` isn't installed by default; the
   PowerShell check script and documented commands replace it.

## Consequences

- Developers need Python 3.12+, Node 22+, and Docker Desktop.
- Existing local databases are untouched (same compose volume); there are no migrations yet.
- Any row in the first table can be changed later by writing a superseding ADR before that
  milestone starts.

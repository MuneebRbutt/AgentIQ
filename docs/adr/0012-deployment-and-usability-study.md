# ADR-0012: Deployment and the usability study

- **Status:** Accepted (proposed default; the team may revise by superseding this ADR)
- **Date:** 2026-10-21
- **Requirements:** FR-10-07 (usability study), FR-10-08 (deployment guide), NFR02; analysis C-12, AS-15, M-9, R-7, R-14

## Context

M12 asks for a live deployment with three demo profiles, a deployment guide someone outside the
team can follow, and a usability study (SUS). The Proposal names Render (C-12). The app is one
FastAPI process with background work (follow-ups, the email poller) and an embedded Chroma index
(ADR-0004), plus Postgres and Redis.

## Decisions

### 1. One image, one web service

A multi-stage `Dockerfile` builds the dashboard with Node, then installs the API on
`python:3.12-slim` and copies the built dashboard in. With `FRONTEND_DIST` set, the API serves it
(`app/core/spa.py`): unknown non-API paths get `index.html`, `/api/...` stays JSON. One service
means one URL for operators, Twilio and the API, no CORS, and one thing to deploy.

It runs a single uvicorn process: the follow-up worker, the email poller and embedded Chroma
each expect one writer. That is ample for a demo and the study; scaling out would move Chroma
to its server mode and the workers to their own service (ADR-0004, ADR-0007).

### 2. Ready on every start

`backend/docker/start.sh` runs `alembic upgrade head`, then `python -m app.cli prepare`, then
uvicorn:

- `prepare` rebuilds the knowledge index from the documents' stored text when the index folder
  is empty. Render's disk is wiped on every deploy unless a paid disk is attached, so this makes
  the index disposable: Postgres stays the only source of truth.
- With `DEMO_OPERATOR_EMAIL` and `DEMO_OPERATOR_PASSWORD` set, it creates the demo business once:
  an operator and the hotel, restaurant and clinic profiles. Later starts change nothing, and an
  existing operator keeps their password.
- The embedding and sentiment models are downloaded while the image is built
  (`prefetch-models`), so starts never wait for a download. `--build-arg
  SENTIMENT_PROVIDER=lexicon` leaves the sentiment model out.

### 3. Render Blueprint

`render.yaml` declares the web service (Docker, health check `/api/v1/health/live`), Postgres
and Key Value (Redis), all in Singapore, the region closest to Pakistan. Secrets are
`sync: false` (Render asks for them); `JWT_SECRET_KEY` is generated. The web service uses the
Starter plan because a free service sleeps after 15 idle minutes and takes about a minute to
wake, longer than Twilio waits for a webhook (15 s), and the background workers sleep with it.
Postgres uses Basic (Render's free database expires after 30 days). Measured in the image: about
175 MB of memory with MiniLM loaded; the sentiment model adds roughly 150 to 250 MB, inside
Starter's 512 MB.

`docker compose --profile app up --build` runs the same image locally, which is how the guide
can be tested before deploying.

### 4. Usability study

`docs/study/` holds the protocol (5 to 8 operators, six timed tasks, optional customer calls and
texts), the consent form and the SUS questionnaire. `python -m app.cli sus-score` scores the
answers (Brooke's formula), gives mean, median, standard deviation, a t-based 95% confidence
interval and the Sauro-Lewis grade against the 68 target (AS-15), and summarises task completion,
assistance, time and errors as Markdown for the report. Participants use synthetic scenarios only
(M-9, R-14).

## Consequences

- A live demo costs roughly US$14 a month on Render at 2025 prices (Starter web service and
  Basic Postgres; Key Value free; check render.com/pricing), plus Twilio and model usage.
  Switching both to free plans works for a short trial, with the cold-start and 30-day caveats
  above.
- Uploaded documents survive deploys (their text is in Postgres) but each deploy re-embeds them,
  so a start takes a little longer as the knowledge base grows.
- The guide must be reproduced by someone outside the team (M12 exit criterion); their notes go
  into the guide.

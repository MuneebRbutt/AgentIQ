#!/bin/sh
# The container's entry point (ADR-0012): migrate, prepare, then serve.
set -eu

python -m alembic upgrade head
# Rebuilds a missing knowledge index; creates the demo business if DEMO_* are set.
python -m app.cli prepare
# One process: the follow-up worker, the email poller and the embedded search index each
# expect a single writer. Behind Render's proxy, trust its forwarded headers.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
    --proxy-headers --forwarded-allow-ips "*"

# ADR-0005: Orchestrator, conversation memory and the turn graph

- **Status:** Accepted (proposed default; the team may revise by superseding this ADR). Amended by ADR-0006: a sentiment branch runs beside `understand`, and escalation can end a turn.
- **Date:** 2026-10-18
- **Requirements:** FR-03-01…05, FR-09-05, FR-04-03, NFR04-01, NFR04-03, NFR03-02; TC-03, TC-09 step 3; analysis D-8; amends ADR-0004 (profile facts)

## Context

M5 connects the Booking and Knowledge agents into one assistant. A customer's message can
hold several requests ("what are your hours, and book a table for four"); a booking is
usually completed over several turns; a conversation must continue after a backend
restart; and the assistant must still answer when the language model is unavailable.

## Decisions

### 1. One entry point for every channel

`Orchestrator.handle(NormalizedRequest)` (`app/orchestrator/service.py`) takes the tenant,
profile, optional session ID, channel, text and caller phone, and returns the reply with
the agent results and per-stage timings. The dashboard console and API (M6), SMS and voice
(M11) and the evaluation harness (M10) all call it. Tenant and profile come from the
channel (login or phone-number mapping), never from the conversation; a session of
another tenant or profile is "not found".

### 2. LangGraph runs the turn; Postgres keeps the conversation

Each turn runs a LangGraph `StateGraph`:

```
understand ──unclear──▶ clarify ──▶ end
    │
    ▼
dispatch ──ready tasks, in parallel (Send)──▶ run_agent ──▶ dispatch ...
    │
    └──nothing left──▶ reply ──▶ end
```

We don't use LangGraph's Postgres checkpointer. It needs async psycopg, which doesn't run
on Windows' default event loop (the reason the app uses asyncpg, CLAUDE.md §4), and a
checkpoint of the whole graph state is more than a conversation needs. Instead:

| Table | Holds |
|---|---|
| `interactions` | One per conversation: tenant, profile, channel, caller phone, status (`open`, `handed_off`, `closed`), turn count and **memory**: the booking being put together, whether a read-back awaits confirmation, unclear turns in a row. |
| `turns` | Every customer message and assistant reply, in order. Assistant turns also store the understanding, agent results, skipped tasks and per-stage timings (FR-09-05, FR-10-02). |

The service loads memory and the last `CONVERSATION_HISTORY_TURNS` (10) messages, runs the
graph, and saves both in one transaction, so a restart loses nothing (TC-09 step 3). The
interaction row is locked for the turn, so two messages in one conversation are handled in
order. Agents each get their own database session, so they can run in parallel.

### 3. Understanding (FR-03-01/02)

One model call at temperature 0 in JSON mode (`app/orchestrator/prompts/understand.md`)
returns subtasks for `knowledge` (a standalone question) and `booking` (the
`BookingSubtask` fields the customer gave), each with optional `depends_on`, plus
`confirmation` (yes/no to a pending read-back), an optional clarification question and a
confidence. The output is validated with Pydantic (`understanding.py`); fields the model
may not set, such as `confirmed`, are dropped. An invalid answer is retried once with the
error; after that the turn degrades as in §6.

### 4. Memory and confirmation are rules in code (FR-04-03, CLAUDE.md rule 3)

- Booking details accumulate across turns in the memory's draft (`memory.py`). Asking
  about another time keeps a new booking a new booking; switching to changing or
  cancelling an existing booking starts a fresh draft.
- A booking is committed only when the previous reply read it back (`needs_confirmation`)
  **and** the customer said yes **without changing anything**. A "yes" from the model at
  any other time confirms nothing; a change re-runs the read-back.
- Completed creations, changes and cancellations clear the draft.

### 5. Running order (FR-03-03/04)

Tasks without dependencies run in parallel. A task with `depends_on` runs after its
dependencies and only if they all completed; otherwise it is skipped (also tasks waiting in
a cycle), and the reply says that part wasn't done. Several booking subtasks in one turn
are folded into one booking. At most three knowledge questions run per turn.

### 6. Graceful degradation (NFR04-01)

| Situation | Behaviour |
|---|---|
| No `LLM_API_KEY` | Every message goes to the Knowledge agent; replies are built from templates. Bookings need a model. |
| Understanding fails or times out twice | The message goes to the Knowledge agent. |
| An agent raises | That task returns `rejected` ("couldn't be done right now"); the other tasks still answer. |
| Reply phrasing fails | A template reply is built from the results (`phrasing.py`). |
| Unclear message | A clarification question. After `escalation.max_clarification_attempts` unclear turns in a row, the profile's hand-off message, and the conversation is marked `handed_off`. Escalation records and notifications come with M7. |

### 7. Replies

One model call (temperature 0.3, `prompts/reply.md`) phrases the reply from the agent
results in the profile's tone, short enough to be read aloud. The model may use only facts
in the results and may not claim a booking was made unless a result says so.

### 8. Profile facts answer questions too (amends ADR-0004)

"What are your opening hours?" must work without an uploaded document (TC-03). The
Knowledge agent now also searches passages built from the profile: opening hours (days
with the same hours grouped), description, contact details, services with prices and
durations, policies and FAQs (`app/knowledge/profile_facts.py`). Their vectors are cached
in memory per profile version, so an edited profile is used on the next question. They
compete with document passages on the same similarity score and threshold.

### 9. Trying it

`python -m app.cli chat --profile-id <id> [--phone ...]` talks to a profile's assistant in
the terminal. Over HTTP (M6), `/api/v1/profiles/{id}/sessions` starts a conversation and
`.../sessions/{session id}/messages` sends messages; replies carry the per-stage timings.
Only `open` conversations take messages: a `handed_off` or `closed` one answers 409.

## Consequences

- TC-03 (two intents in one message, then a booking over three turns) and restart
  survival are integration-tested with a scripted model. The prompts themselves still need
  checking against a real model, which the golden scenarios (M10) will do.
- The graph has no hidden state, so the evaluation harness can call the same entry point.
- New runtime dependency: `langgraph` 1.2 (MIT), pinned to the minor version.
- Each turn makes two model calls when a model is configured (understand and reply), plus
  one per knowledge question that finds passages.

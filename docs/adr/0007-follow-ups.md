# ADR-0007: Follow-ups: confirmations, reminders, feedback and retries

- **Status:** Accepted (proposed default; the team may revise by superseding this ADR)
- **Date:** 2026-10-19
- **Requirements:** FR-07-01…04, FR-06-04, NFR04-01/02, NFR03-02; UC-07; TC-07; analysis Q-8, M-7, M-14, C-8, AS-9; amends ADR-0002 (profile schema) and ADR-0006 (escalation notices)

## Context

M8 adds the Follow-Up agent. The Report says it "runs as a scheduled/event-driven task
(booking-created event → confirmation; time-based trigger → reminder) using a lightweight
task queue" (§5.4), dispatches confirmations, reminders and feedback requests "without a
human trigger" (UC-07), and on a delivery failure "logs the failure and, where feasible,
retries once". It names no queue (Q-8), no channel or provider (M-7) and doesn't say when
a conversation has ended (M-14). TC-07 must pass by advancing the system clock.

## Decisions

### 1. Event-driven, outside the conversation turn (C-8)

The turn graph (ADR-0005) doesn't schedule anything. Two kinds of event start follow-ups:

- **Booking events** (`booking_events`, written by `BookingService` in the same
  transaction as the change since M3): `created`, `modified`, `cancelled`. Bookings made
  by the assistant and by operators on the dashboard are treated alike.
- **Ended conversations**: an interaction whose status is no longer `open`.

Each event row gets a `followed_up_at` column; the worker handles rows where it is null and
sets it in the same transaction as the messages it queues, so an event is handled exactly
once. The migration marks existing rows as handled, so upgrading never messages old
customers.

### 2. Postgres is the queue (Q-8)

Messages are rows in `follow_up_tasks` (kind, channel, recipient, subject, body, `due_at`,
status, attempts, last error), not jobs in Redis. The worker takes due rows with
`SELECT … FOR UPDATE SKIP LOCKED`, one per transaction. Reasons:

- Postgres is the source of truth (NFR04-02, CLAUDE.md §3 rule 6). A reminder scheduled
  three days ahead must survive restarts and a Redis flush; with arq or Celery it would
  live in Redis.
- It needs no new dependency or process, and several workers can run side by side.
- The rows are the log UC-07 asks for ("dispatched and logged"), and the operator can
  list and cancel them through the API.
- Time comes from the injectable `Clock`, so TC-07 advances a fake clock and runs one pass.

At prototype scale (AS-14) a 30-second poll is plenty. Delivery is at-least-once: a crash
between the provider accepting a message and the commit can send it twice.

### 3. The worker

`FollowUpWorker` (`app/workers/followups.py`) makes passes. Each pass:

1. closes conversations that have been quiet for `CONVERSATION_IDLE_MINUTES` (default 30;
   this answers M-14 for SMS and voice, where nobody presses "close");
2. hands each new booking event to the agent and queues what it plans;
3. does the same for each ended conversation;
4. sends what is due.

The API runs it as a background task (`FOLLOWUP_WORKER_ENABLED`, `FOLLOWUP_POLL_SECONDS`).
`python -m app.cli run-followups [--once]` runs it on its own, for a separate worker
process in deployment (M12) or to trigger a pass by hand. The worker serves every business:
each row carries its tenant, and everything loaded for it is scoped to that tenant.

### 4. The agent decides; the profile configures (FR-07-01…04)

`FollowUpAgent` (`app/agents/followup/agent.py`) is pure. It receives the facts of a
booking or a conversation plus the profile, and returns a `FollowUpPlan`: messages with
their due times, whether earlier unsent ones are stale, and why anything was skipped.
The worker logs each decision (`followup_decided`), including "nothing to do". The plan
is typed rather than an `AgentResult`, because nothing phrases it for a customer.

| Event | Plan |
|---|---|
| booking created | confirmation now (FR-07-02); a reminder per `reminder_hours_before` rule (FR-07-03), skipped if that moment has passed |
| booking modified | withdraw unsent confirmation and reminders; send the "changed" message; plan reminders for the new time |
| booking cancelled | withdraw unsent confirmation and reminders |
| conversation closed | feedback request after `feedback_delay_minutes`, if `feedback` is on (FR-07-04) |
| conversation handed off | nothing: a person is about to call |
| no customer message, or no contact | nothing (UC-07 alternative path) |

The new profile section `follow_up` holds `confirmation`, `reminder_hours_before` (up to
three, 1-336 hours, default `[24]`), `feedback` (default off), `feedback_delay_minutes`,
`channel` (`sms` or `email`, falling back to the other when that contact is missing) and
four message templates. Templates may use only the placeholders listed in the schema
(`{business}`, `{name}`, `{reference}`, `{service}`, `{when}`, `{party_size}`,
`{business_phone}`; feedback has fewer), which is checked when the profile is saved.
Messages are templates, not model output: they are factual, instant and free. The three
example profiles show different wording, timings and channels with no code changes
(NFR05-02). Every field has a default, so existing profiles stay valid.

When a message is due, the worker checks once more that it still makes sense (the booking
is still confirmed and hasn't started); otherwise it is cancelled instead of sent.

### 5. Channels and providers (M-7, AS-9)

`create_notifier` (`app/providers/notifications.py`) picks a `Notifier` from the settings:

- **SMS through Twilio** when `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and
  `TWILIO_FROM_NUMBER` are set. Called with plain httpx (one POST doesn't justify the SDK).
  Local numbers become international with `SMS_DEFAULT_COUNTRY_CODE` (`+92`).
- **Email over SMTP** when `SMTP_HOST` and `SMTP_FROM` are set, with standard-library
  `smtplib` in a worker thread. Any provider works.
- **Neither**: `LogNotifier`, and messages are recorded as `logged`, never `sent`.

`python -m app.cli check-notifications --phone … --email …` sends a test message. Errors
keep the provider's error code but never its response body or credentials.

Consent and opt-out (M-7) are not handled: the prototype messages synthetic customers
only. A real deployment needs a "STOP" handler before messaging real people.

### 6. Retry once (UC-07), for escalation notices too (ADR-0006)

A failed send is logged with its error, tried again after `FOLLOWUP_RETRY_MINUTES`
(default 5) and marked `failed` if the retry fails too. Every attempt is counted.

An escalation notice to the business is still sent at once after the turn (ADR-0006). If
that fails, an `escalation` follow-up row is queued with one attempt already counted; when
the retry gets through, the escalation's `notification` becomes `sent` (or `logged`).
The retry uses the business's contact details as they are at that moment.

### 7. API (FR-08-03)

- `GET /api/v1/profiles/{id}/follow-ups[?status=&kind=&booking_id=&session_id=]`, latest
  due first.
- `GET .../follow-ups/{fid}`.
- `POST .../follow-ups/{fid}/cancel`, only while pending (409 otherwise). The row is
  locked, so a message the worker is sending at that moment counts as sent.

Everything is tenant-scoped; another business's follow-up is 404.

## Consequences

- TC-07 passes as an API test with the fake clock (`tests/integration/test_followups.py`);
  the agent's rules are unit-tested (`tests/unit/test_followup_agent.py`) and so are the
  providers, against a mock Twilio and a fake SMTP server (`tests/unit/test_notifications.py`).
  Real delivery needs Twilio or SMTP credentials and is checked by hand with
  `check-notifications`.
- New table `follow_up_tasks` and columns `booking_events.followed_up_at` and
  `interactions.followed_up_at` (migration `3840e757726a`). New profile section `follow_up`.
- An open conversation is closed after 30 quiet minutes, including in the test console.
- Real SMS costs money per message (see the provider docstring); a Twilio trial account
  only reaches verified numbers.

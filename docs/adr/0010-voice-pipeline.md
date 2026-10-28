# ADR-0010: Voice pipeline

- **Status:** Accepted (proposed default; the team may revise by superseding this ADR)
- **Date:** 2026-10-21
- **Requirements:** FR-02-01/04/05/06, NFR01-01, NFR03-02, NFR04-01; UC-02; TC-02; analysis Q-3, Q-4, M-5, M-15, D-12, R-1

## Context

M11 puts the phone in front of the orchestrator that M5–M10 built and tested over text.
The Report asks the system to answer inbound English calls (FR-02-01), turn speech into
text and replies into speech with configured services (FR-02-04/05), record voice latency
(FR-02-06), and fall back gracefully when a speech service fails (UC-02 exception). It
leaves open whether calls are turn-based (`<Gather>`/`<Say>`/`<Play>`, §5.4) or streamed
(§5.6, Q-4), which TTS model to use (Q-3), and how a dialled number finds its business
(M-5).

## Decisions

### 1. Turn-based calls (Q-4)

Twilio drives the call with TwiML; each step is a webhook that answers with the next
TwiML document: `incoming` (greet and listen), `turn` (the caller spoke), `silence`, and
`status` (the call ended). It needs no WebSocket server, works behind any HTTPS host and
is easy to test. Streaming (Media Streams) is deferred until profiling shows turn-based
latency misses the target (AS-15: end of speech to first audio p95 ≤ 3 s, stretch).

### 2. Speech services are pluggable, Twilio's are the default

| | Default (`twilio`) | Optional |
|---|---|---|
| Speech to text | `<Gather input="speech">`: Twilio's recogniser, no extra key, the lowest latency, and the caller can talk over the prompt | `whisper`: `<Record>`, download the recording, send it to any OpenAI-compatible `/audio/transcriptions` (OpenAI `whisper-1`, Groq `whisper-large-v3`) |
| Text to speech | `<Say>` with a Polly voice | `openai`: any OpenAI-compatible `/audio/speech`, `tts-1` (Q-3: the low-latency model, not `tts-1-hd`), served to `<Play>` from Redis |

The defaults give a working phone line with only a Twilio account. The Report's Whisper
and OpenAI voice are a setting away, so the latency of each choice can be compared. Both
adapters live in `app/providers/speech.py` (CLAUDE.md rule 4) with fakes in tests.

### 3. Numbers route calls (M-5)

A `phone_numbers` row maps an E.164 number to one profile; a number belongs to one
business. Operators manage numbers on the dashboard's Phone tab (or `connect-number`),
which also shows the webhook URLs to paste into the Twilio console. The tenant and profile
of a call come only from the dialled number (NFR03-02).

### 4. Every webhook is signed and always answers

Webhooks are public, so each must carry a valid `X-Twilio-Signature` (HMAC-SHA1 with
`TWILIO_AUTH_TOKEN`) over `PUBLIC_BASE_URL` + path, or it gets 403. Without a token calls
are refused; turning the check off is possible in development only. Every webhook answers
TwiML with HTTP 200 even when something fails: a speech service that fails falls back to
Twilio's own, the orchestrator already degrades (NFR04-01), and anything unexpected ends
with a spoken apology instead of Twilio's "application error".

### 5. Calls are conversations

A call opens an `interactions` row with `channel = voice` and the caller ID as the
customer's phone (AS-5), and a `voice_calls` row linking Twilio's `CallSid` to it. Each
turn goes through `Orchestrator.handle`, like every channel. A hand-off speaks the
profile's hand-off message and hangs up (the escalation notice already asks the business
to call back). Hanging up closes the conversation, so feedback follow-ups work as for text.
Two silences in a row (configurable) end the call politely.

### 6. Latency is recorded per stage (FR-02-06)

Each answered turn stores `stt_ms` (Whisper only; Twilio's recogniser works on Twilio's
side and isn't visible), `orchestrator_ms`, `tts_ms` and `server_ms` (the whole webhook)
on the call, and adds `stt`/`tts` to the assistant turn's timings beside the
orchestrator's. `python -m app.cli voice-latency` prints mean, p50 and p95 per stage.

### 7. Speakable replies

Replies are cleaned for speech: markdown removed, codes with digits such as booking
references read letter by letter, and length capped at a sentence boundary.

## Consequences

- A phone demo needs a Twilio voice number, `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN` and a
  public URL (ngrok in development, the deployed API in M12). A trial account only
  answers verified caller numbers and plays a trial notice first.
- With Twilio's recogniser, end-to-end latency includes recognition time we can't
  measure; for the latency report, compare against the `whisper` setting or time test
  calls from the caller's side.
- `<Record>` mode can't be interrupted while the reply plays; `<Gather>` mode can.
- No call audio is stored by AgentIQ; Whisper recordings stay in the Twilio account
  (delete them there, or set a retention policy, for the usability study, analysis M-9).
- SMS and email inbound (FR-02-02/03) remain for M12.

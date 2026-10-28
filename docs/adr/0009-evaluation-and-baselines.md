# ADR-0009: Evaluation and baselines

- **Status:** Accepted (proposed default; the team may revise by superseding this ADR)
- **Date:** 2026-10-20
- **Requirements:** FR-10-01…06, FR-04-03, FR-05-05, NFR04-01; UC-10; TC-10; analysis Q-5, A-6, M-16, D-13

## Context

M10 is the research deliverable: AgentIQ is compared with a single-agent baseline and with
IVR and human baselines on task completion, response time, knowledge (retrieval) accuracy
and response accuracy, over a scenario set per domain with ground truth (FR-10-01…06).
TC-10 runs the scenarios and expects a comparative report. The comparison only means
something if every system sees exactly the same callers, data and rules, and if a run can
be repeated and audited.

## Decisions

### 1. Layout

Data in `eval/` (scenarios, documents, reports), code in `backend/app/evaluation`, so the
harness is type-checked and tested with the rest of the backend and calls the orchestrator
directly. The planned `eval/harness`, `eval/baselines` and `eval/ground_truth` folders are
dropped: ground truth sits inside each scenario (`expect`), one YAML file per domain.

### 2. Scenarios

Ten per domain, the same mix in each: four knowledge questions (two from the profile, two
only from an uploaded document), one booking, one cancellation by reference, two messages
with two requests, one complaint, one unanswerable question. Each scenario fixes the
local start time (the clock is a `ScenarioClock` that advances 20 s per turn), so relative
dates are stable. Files are validated with pydantic and against the example profile
(service ids, by-time versus by-night, required booking details, documents present)
before anything runs.

### 3. Like-for-like systems

- **AgentIQ** is called through `Orchestrator.handle`, like every channel.
- **Single agent:** one prompt, one model, a JSON action loop (`search`, `booking`,
  `handoff`, `reply`, at most five steps a message). It uses the same `KnowledgeService`
  search and the same `BookingAgent` and booking rules, so the comparison isolates the
  architecture: no separate understanding step, no parallel specialists, no sentiment
  model, and the model itself decides when a booking is confirmed (in AgentIQ, code does,
  FR-04-03). Skipped, and said so in the report, when no model is configured.
- **IVR:** a keypad menu generated from the profile; keypad bookings go through
  `BookingService`. Email addresses and free-text booking fields can't be typed, so those
  bookings, and anything no menu covers, go to the operator (a hand-off). The simulated IVR
  caller knows the scenario's goals and presses what a sensible caller would.
- **Human:** a timed role-play recorded as a CSV (protocol in `eval/README.md`); the harness
  only reads it into the report.

Every (system, scenario, repetition) gets a fresh copy of the example profile in a new
evaluation tenant, deleted afterwards unless `--keep-data`. Notifications always go to
`LogNotifier`: an evaluation never messages anyone.

### 4. The simulated caller

Rule-based, not a second model: scripted messages, then up to three follow-ups (details
when asked, "Yes, please go ahead." to a read-back). A model-played caller would add cost
and its own variance, and would behave differently towards different systems.

### 5. Scoring and metrics

Judged only from what any system leaves behind (its words, whether it handed off, the
bookings in the database), with deterministic rules: facts as whole-word matches with
alternative forms, refusal phrases plus forbidden patterns, exact booking matches. Guard
checks (every message answered, no unwanted booking, no needless hand-off) count towards
response accuracy only. Metrics are defined in `eval/README.md`; repeated runs report mean
± sample standard deviation. Call length is estimated (150 words a minute, 2 s per key
press) so the IVR, whose prompts are pre-recorded, can be compared on something fair.

### 6. Adjudication and outputs

`results.json` keeps the raw automatic judgments. Evaluator overrides (UC-10 alternative
path) live in a YAML file with a note each and are applied when the report is built, so
they are visible and reversible; `evaluate-report` rebuilds the report without rerunning
anything. Outputs: `report.md` (overall table, AgentIQ versus each baseline, by domain, by
category, per scenario, every failure, adjudications, method), `summary.csv` and
`results.json` (git-ignored).

## Consequences

- Real numbers need `LLM_API_KEY` and cost a few hundred model calls per run; tests use
  scripted models (TC-10 integration test) and never call a paid API.
- Without a key AgentIQ is scored in its no-model mode, which is useful as a floor but is
  not the result to report.
- Rule-based scoring can mark a correctly worded answer wrong; adjudications exist for that
  and must be listed in the FYP report.
- The IVR is generous to itself in one way (its caller never presses a wrong key) and strict
  in another (no keypad entry for emails or free text); both are stated in the report.

# Evaluation (FR-10, TC-10)

This folder holds what AgentIQ is evaluated on: scripted customer scenarios with their
ground truth, the documents those scenarios ask about, and the reports each run produces.
The harness itself is in `backend/app/evaluation` (ADR-0009).

```
eval/
├── scenarios/{restaurant,hotel,clinic}.yaml   # 10 scenarios each, with ground truth
├── documents/<domain>/*.md                     # uploaded to the profile before each run
├── adjudications.example.yaml                  # evaluator overrides (copy and edit)
├── human/results-template.csv                  # human role-play results (copy and fill in)
└── reports/<timestamp>/                        # written by each run
    ├── report.md      # the comparative report (TC-10 step 3)
    ├── summary.csv    # one row per system and repetition, every metric as a number
    └── results.json   # transcripts, bookings and every judgment (git-ignored)
```

## What is compared (FR-10-05/06)

| System | What it is |
|---|---|
| **AgentIQ** | The real orchestrator and agents, through `Orchestrator.handle`, the same entry point every channel uses. |
| **Single-agent baseline** | One language model with one prompt, deciding on its own when to search, book, hand off or reply. Same model, same retrieval and the same Booking agent as AgentIQ, but no separate understanding step, no parallel specialists, no sentiment model and no code that holds a booking back until the customer says yes. Needs `LLM_API_KEY`. |
| **IVR baseline** | A keypad menu built from the profile: 1 bookings, 2 opening hours, 3 contact, 4 services and prices, 5 policies and FAQs, 0 operator. Bookings typed on the keypad go through the same booking rules. It can't search documents, take an email address or free text, or understand speech, so for those the caller presses 0. |
| **Human agent** | A timed role-play by the team (protocol below), entered as a CSV. |

## Scenarios and ground truth (FR-10-01)

Each domain has the same mix: 4 knowledge questions (2 answered by the profile, 2 only by
the uploaded document), 1 booking, 1 cancellation by reference, 2 messages with two
requests at once, 1 complaint and 1 question nothing in the business's information
answers. A scenario says who calls, what they say, the local time the call happens (the
clock is fixed to it, so "Friday" always means the same date) and what a correct system
achieves:

| `kind` | Met when |
|---|---|
| `answer` | every fact appears in what the system said, in one of its listed forms, and nothing in `forbid` does |
| `refusal` | the system declined or passed the caller to a person, and no `forbid_patterns` match (no invented price) |
| `booking` | a confirmed booking with exactly that service, party size, date and time (or nights) exists after the call |
| `cancelled` | the booking made before the call (`setup_booking`) is cancelled |
| `escalation` | the conversation was handed to a person |

`topic` on an answer says which IVR menu holds it; without one the IVR caller asks for the
operator. Caller `details` are the profile's extra booking fields (a clinic patient's age
and reason for visit); the simulated caller gives them when asked. Scenario files are
checked against their example profile when loaded: an unknown service, a time on a
by-night profile or a missing required detail stops the run before anything is scored.

## The simulated caller

Chat systems (AgentIQ and the single agent) hear the scripted messages in order. When the
script runs out and the reply waits for an answer, the caller gives their name, phone,
email and extra details if asked for them, and says "Yes, please go ahead." to a
read-back, at most three times. The IVR caller presses the keys a sensible caller would
for the scenario's goals: menus first, then bookings, then the operator if anything is
left. Every system gets a fresh copy of the example profile in a new evaluation business,
which is deleted afterwards; follow-up and escalation messages are only logged, so no one
is ever contacted.

## Metrics (FR-10-01…04)

| Metric | Definition |
|---|---|
| Task completion | expectations met / expectations |
| Scenario success | scenarios with every expectation met / scenarios |
| Knowledge accuracy | `answer` and `refusal` expectations met / those expectations |
| Response accuracy | expectations plus guard checks met / all of them. Guards: every message got a reply, no booking nobody asked for, no hand-off without a reason |
| Response time | from the customer's message to the reply: mean and p95 over every reply (FR-10-02) |
| Est. call length | the call if spoken: speech at 150 words a minute, plus waiting time, plus 2 s per key press |

The IVR's replies are recorded prompts, so its response time is close to zero; compare
call length and task completion instead. With `--repeat N` every metric is computed per
repetition and reported as mean ± standard deviation.

## Running it

From `backend` with the venv active and `docker compose up -d` running:

```powershell
python -m app.cli evaluate                          # all 30 scenarios, all systems
python -m app.cli evaluate --repeat 3               # for mean ± sd (costs 3x the model calls)
python -m app.cli evaluate --system ivr --domain restaurant
python -m app.cli evaluate --scenario R05 --scenario H07 --keep-data
```

- Set `LLM_API_KEY` in `.env` for real numbers. Without it AgentIQ runs in its no-model
  mode (knowledge answers by quoting, no bookings) and the single-agent baseline is
  skipped; the report says so. A full run makes a few hundred model calls (AgentIQ and the single agent together).
- Use `EMBEDDING_PROVIDER=minilm` (the default) for the real retrieval; `hashing` is only
  for tests.
- `--keep-data` keeps the evaluation business, so you can read every conversation on the
  dashboard (log in as an operator of that tenant, or look in the database).
- The report goes to `eval/reports/<timestamp>/` unless you pass `--output`.

## Adjudication (UC-10 alternative path)

The scoring rules are deterministic, so a correct answer worded in an unexpected way can
be marked wrong. Don't edit `results.json`; copy `adjudications.example.yaml`, add one
entry per judgment you overrule with the reason, and rebuild the report:

```powershell
python -m app.cli evaluate-report --results ..\eval\reports\<run>\results.json --adjudications ..\eval\adjudications.yaml
```

The report lists every override under "Adjudications". `id` is the expectation id from the
scenario file or a guard (`replied`, `no-extra-booking`, `no-needless-handoff`).

## Human baseline protocol

1. One team member plays the business's receptionist with the example profile and the
   domain's document open; another plays the caller, reading the scenario's messages and
   answering follow-up questions with the scenario's caller details.
2. Use the same 30 scenarios. Time each call from the first word to the goodbye.
3. After each call the evaluator checks the scenario's expectations and records a row in a
   copy of `human/results-template.csv`: scenario id, participant code (no names),
   expectations met, expectations in total, call length in seconds.
4. Add the file to the report with `--human ..\eval\human\results.csv` on `evaluate` or
   `evaluate-report`. Only task completion, scenario success and call length are compared
   for people.

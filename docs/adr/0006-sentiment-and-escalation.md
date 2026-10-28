# ADR-0006: Sentiment, complaints and escalation

- **Status:** Accepted (proposed default; the team may revise by superseding this ADR). Escalation-notice retries and real SMS/email: see ADR-0007.
- **Date:** 2026-10-19
- **Requirements:** FR-06-01…05, FR-08-03, NFR04-01, NFR03-02; UC-06; TC-06; analysis Q-6, C-7, A-2, A-3, D-11, R-6; amends ADR-0002 (profile schema) and ADR-0005 (turn graph)

## Context

M7 adds the Sentiment & Complaint agent. The Report asks for a fine-tuned DistilBERT
classifier plus "a small rule set (keyword + score threshold)" that decides when the
escalation workflow fires (§5.4, Module 6). It doesn't define the sentiment categories
(A-2), the complaint conditions (A-3), what escalation does when no human agents exist
(Q-6, M-6), or a training dataset (D-11). Low-confidence readings must not escalate and
must be flagged for review (UC-06).

## Decisions

### 1. Where it runs: beside understanding, on every customer message (C-7)

Sentiment is not an intent. The turn graph (ADR-0005) now starts two branches at once:

```
start ──▶ understand ─┐
  └────▶ sentiment ──┴─▶ decide ──escalate──▶ escalate ──▶ end
                           │ ──unclear───▶ clarify ──▶ end (hand-off = escalation too)
                           ▼
                        dispatch ⇄ run_agent ──▶ reply ──▶ end
```

`decide` waits for both. An escalation wins over everything else: no booking or question
is attempted and the reply is the profile's `handoff_message`. A message that counts as
a complaint but doesn't escalate adds the note `customer_upset`, so the reply starts with
an apology (template and model alike). If the sentiment step fails, the turn continues as
if the customer were calm (NFR04-01). Its time is recorded as `agent.sentiment`.

### 2. Categories and classifier (A-2, D-11, R-6)

Three labels, `negative`, `neutral`, `positive`, each with a probability. Classifiers sit
behind `SentimentClassifier` (`app/providers/sentiment.py`):

| `SENTIMENT_PROVIDER` | What |
|---|---|
| `model` (default) | `Xenova/distilbert-base-multilingual-cased-sentiments-student`: a DistilBERT student model (Apache-2.0) with three labels, exported to ONNX. It runs with the ONNX runtime and `tokenizers` that ChromaDB already installs, so the API needs no torch (CLAUDE.md §6). The files (~135 MB) download once from the Hugging Face Hub into `SENTIMENT_MODEL_PATH` (git-ignored). |
| `lexicon` | A short word list with negation ("not good"), intensifiers ("very", capitals, "!") and complaint phrases ("third time", "fed up"). Deterministic; used by tests and offline demos. |

The model is loaded on first use. If it can't be downloaded or loaded, the lexicon takes
over and the reason is logged; each stored reading names the classifier that produced it,
so evaluation never mistakes one for the other. `python -m app.cli check-sentiment` shows
which classifier is active and how it reads the TC-06 messages.

Following R-6, the project starts with this off-the-shelf model. Fine-tuning DistilBERT
on labelled customer-service messages (`ml/sentiment/`) is worth doing only if the M10
evaluation shows a gap; the provider interface means a fine-tuned export drops in by
changing `SENTIMENT_MODEL`. Binary models (SST-2) are refused: TC-06 needs "neutral".

### 3. Complaint conditions and escalation rules (A-3, FR-06-02/04)

Rules are code (`app/agents/sentiment/agent.py`, `assess`), with every number in the
profile's `escalation` section:

| Rule | Escalates when |
|---|---|
| `negative_sentiment` | the reading is negative with confidence ≥ `negative_sentiment_threshold` (default 0.7) |
| `keyword` | the message contains one of `keywords` (whole words, case-insensitive), whatever the sentiment |
| `repeated_negative` | `negative_turns_in_a_row` (new, default 3) negative messages in a row, even if none alone was enough |
| `unclear` | the assistant still couldn't understand after `max_clarification_attempts` (ADR-0005) |

A reading below `SENTIMENT_MIN_CONFIDENCE` (0.55) is "uncertain": it never escalates on
sentiment, doesn't count towards the streak, and sets `needs_review` on the conversation
(UC-06). Keywords still apply, because they don't depend on the model. A message is a
**complaint** when it is confidently negative or contains a keyword.

The count of negative messages in a row lives in the conversation memory
(`negative_streak`), so it survives restarts like the rest of the memory.

### 4. What escalation does (Q-6)

1. The conversation becomes `handed_off` and takes no more messages (409, as in M6).
2. An `escalations` row records the reasons, the customer's message and number, the
   sentiment reading and a status (`open` → `acknowledged` → `resolved`, set by operators
   with an optional note).
3. The business's `notify_email` / `notify_phone` is told through a `Notifier`
   (`app/providers/notifications.py`), after the turn is saved, with a 10-second limit, so
   a slow or failing notifier never delays or loses the customer's reply. The row records
   `none` (no contact set), `logged`, `sent` or `failed`. Until M8 adds SMS and email
   providers, `LogNotifier` writes the notice to the log with the contact masked and the
   row says `logged`, not `sent`. M8 also adds the retry (FR-07 "retry once").
4. The customer hears the profile's `handoff_message`.

### 5. Records (FR-06-03/05)

`sentiment_records` holds one row per customer message: label, confidence, every
probability, classifier name, uncertain flag, complaint flag and escalation reasons,
keyed by conversation and turn number. Assistant turns also store the full sentiment
result and any escalation reasons with the timings (ADR-0005).

### 6. API (FR-08-03)

- `POST .../sessions/{id}/messages` returns `sentiment` (the agent's result) and
  `escalation_id`.
- `GET .../sessions/{id}` adds `needs_review`, `sentiment` (per customer message) and
  `escalations`; the list shows `needs_review` too.
- `GET /api/v1/profiles/{id}/escalations[?status=&session_id=]`, `GET .../{eid}` and
  `PATCH .../{eid}` with `{"status": ..., "note": ...}`. Everything is tenant-scoped; another
  business's escalation is 404.

## Consequences

- TC-06 passes as an API test with the lexicon (`tests/integration/test_escalations_api.py`);
  the real model is tested where it has been downloaded (`tests/unit/test_sentiment.py`,
  skipped otherwise, like MiniLM in ADR-0004). The model's behaviour on real customer
  messages still needs checking in M10.
- Each turn costs one CPU inference (tens of milliseconds) in parallel with understanding;
  no extra paid API call.
- `onnxruntime`, `tokenizers` and `numpy` become direct dependencies (they were already
  installed with ChromaDB; same pinned versions).
- New tables `sentiment_records` and `escalations`, and `interactions.needs_review`
  (migration `d56b96fdb853`). New profile field `escalation.negative_turns_in_a_row` with
  a default, so existing profiles stay valid.

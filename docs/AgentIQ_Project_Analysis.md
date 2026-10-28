# AgentIQ: Project Understanding and Documentation Audit

**Sources analysed** (no `/docs` folder or repository exists, so these two uploads are the specification):

| Short name | File | Status |
|---|---|---|
| **Proposal** | `AgentIQ_FYP_Proposal.docx` (certificate dated 3/12/2026, i.e. March 2026) | Approved proposal |
| **Report** | `AgentIQ_FYP1_Report_3.docx` (FYP-I report, refs accessed Sep 2026) | Latest design document |

**Precedence rule used in this analysis:** where the two documents disagree, the **Report wins** (it is newer and more detailed) unless noted. Every such case is listed in §2.2 so the team can confirm.

Findings are tagged with IDs (`C-` contradiction, `M-` missing, `A-` ambiguity, `D-` incomplete design, `R-` risk, `AS-` assumption, `Q-` decision needed) so they can be referenced from issues and the CLAUDE.md.

---

## 0. Decisions needed before implementation starts

These block or strongly shape the first modules. Each has a recommended default that the CLAUDE.md already assumes; confirm or override.

| ID | Decision | Recommended default |
|---|---|---|
| Q-1 | Which LLM (Report names none; Proposal says GPT-4) | A current, cheap tool-calling model behind a provider-agnostic wrapper; pin the exact model ID in config |
| Q-2 | Embedding model: Sentence-Transformers (Proposal) or OpenAI embeddings (Report §5.4) | Sentence-Transformers (`all-MiniLM-L6-v2` or similar): free, local, matches the architecture figure |
| Q-3 | TTS: OpenAI `tts-1-hd` (Report), OpenAI TTS / ElevenLabs (Proposal figure) | OpenAI `tts-1` (the low-latency variant), not `tts-1-hd` |
| Q-4 | Voice architecture: Twilio `<Gather>`/`<Say>`/`<Play>` turn-based (Report §5.4) or streaming Media Streams (Report §5.6) | Turn-based first (simpler, demo-able), streaming only if latency targets are missed |
| Q-5 | Baselines: IVR + human (Proposal), IVR + single-agent (Report O7), or all three (FR-10-06) | Single-agent baseline built in code; IVR baseline as a scripted decision tree; human baseline as a small timed role-play study |
| Q-6 | What "escalation" actually does (no human agents exist in scope) | Create an escalation record, flag it on the dashboard, notify operator by email/SMS, tell the customer someone will call back |
| Q-7 | Booking/resource model per domain (rooms vs tables vs doctor slots) | Generic `Resource` + `Slot`/capacity model configured by the profile (see D-3) |
| Q-8 | Background task queue for follow-ups ("lightweight task queue", unnamed) | `arq` or Celery on the existing Redis; reminders as scheduled jobs |
| Q-9 | Frontend language: JavaScript (docs) or TypeScript | TypeScript with Vite (still "React.js + Tailwind"); revert to JS if the team prefers |
| Q-10 | Text and email channels: real SMS/email inbound, or only the dashboard text console | Dashboard text console + optional Twilio SMS; inbound email deferred (see C-6) |

---

## 1. Project Understanding

### 1.1 Purpose
AgentIQ is an AI customer-support **calling agent** for small and medium service businesses. It answers inbound phone calls (and text), understands requests that contain several intents at once ("what are your hours, and book me a table for four"), and handles them end to end through a **supervisor/orchestrator agent that delegates to specialist agents** instead of one general chatbot. It is a final-year research prototype: the deliverable is a working system **plus an evaluation** showing it beats IVR and single-agent baselines on task completion, accuracy and response time.

### 1.2 Business domain
Customer support for service-oriented SMBs. Three evaluation verticals: **hotels, restaurants, clinics**. Commercial model (Report): **B2B2C**. SMB operators configure the platform; their customers talk to it.

### 1.3 Intended users

| Actor | Role | Touchpoint |
|---|---|---|
| **End customer** | Books, modifies, cancels, asks questions, complains | Phone call (primary), SMS/text, email (FR-02-03) |
| **Operator** (a.k.a. "Administrator" in FR-01/FR-08 and the diagrams) | Configures a business profile, uploads documents, tests, monitors logs and escalations | Web dashboard |
| **Evaluator / dev team** | Runs scenario sets, baselines, usability sessions | Evaluation harness |
| External services | LLM provider, Twilio, STT/TTS | APIs |

### 1.4 Key workflows
1. **Operator onboarding**: log in → create business profile (domain, services, policies, booking rules, escalation rules) → upload knowledge documents (chunked + embedded) → test in the text console → go live.
2. **Inbound call** (SD-1): Twilio call → STT (Whisper) → backend starts latency timer → orchestrator → text reply → TTS → played to caller. If STT fails: "Sorry, could you repeat that?"
3. **Orchestration turn** (SD-2): load conversation state → classify intents → decompose into subtasks → run Booking / Knowledge / Sentiment **in parallel** where independent → optionally schedule follow-up → save state → one combined reply. If intent is unclear: ask a clarifying question.
4. **Booking** (SD-3): capture details → check availability → confirm with customer → create/modify/cancel → persist. Unavailable slot → propose nearest alternatives. Cancellation outside window → state policy.
5. **Knowledge** (SD-4): embed query → vector search → if relevant chunks found, generate grounded answer; else explicitly say "insufficient information".
6. **Sentiment & complaint** (SD-5): classify each customer turn → store result → if negative/complaint rule fires, trigger escalation; low confidence → don't escalate, flag for review.
7. **Follow-up** (SD-6): on interaction completion read outcome → send confirmation, schedule reminder (e.g. 24 h before), send feedback request if enabled. Delivery failure → log and retry once.
8. **Evaluation** (SD-10): run the same scenario on AgentIQ, the single-agent baseline and a "traditional support system" → compute task completion, response time, retrieval accuracy, overall accuracy → store → report.

### 1.5 Functional requirements (Report §2.3.1, 59 sub-requirements)

| Module | IDs | Summary |
|---|---|---|
| FR-01 Operator Authentication | 01-01…01-04 | Secure login, credential auth, generic failure message, logout |
| FR-02 Voice Pipeline | 02-01…02-06 | Inbound English calls, SMS/text input, email input (subject+body), STT, TTS, latency logging |
| FR-03 Orchestrator | 03-01…03-06 | Normalize input, identify intents, decompose, route, run independent subtasks concurrently, keep multi-turn state |
| FR-04 Booking Agent | 04-01…04-06 | Capture details, check availability in internal DB, create after confirmation, modify, cancel per rules, persist history |
| FR-05 Knowledge Agent | 05-01…05-06 | Upload docs, chunk+embed, retrieve, grounded generation, "insufficient info", update docs+index |
| FR-06 Sentiment Agent | 06-01…06-05 | Classify sentiment, detect complaints, link to interaction, trigger escalation, keep sentiment history |
| FR-07 Follow-Up Agent | 07-01…07-04 | Decide if follow-up needed, auto confirmation, reminders by rule, feedback requests |
| FR-08 Operator Dashboard | 08-01…08-07 | Profile config UI, text test console, logs/outcomes/escalations, create/validate/edit profiles, same workflow across 3 domains |
| FR-09 Backend & Data | 09-01…09-05 | Relational store, records linked to profile+interaction, vector store, REST API, session state + cache |
| FR-10 System Integration / Evaluation | 10-01…10-08 | Task completion, response time, retrieval accuracy, overall accuracy, single-agent baseline, comparative eval, usability study, deployment guide |

### 1.6 Non-functional requirements (Report §2.3.2)

| NFR | Key points | Measurable target given? |
|---|---|---|
| NFR01 Performance | Voice latency "acceptable for natural conversation"; text latency "acceptable"; retrieval adds no perceptible delay | **No**, deferred to profiling |
| NFR02 Usability | New operator can set up without developer help; actionable errors; SUS "acceptable" | **No** (SUS ≥ 68 implied, not stated) |
| NFR03 Security | Hashed credentials; strict tenant isolation; all admin/API endpoints authenticated | Yes (qualitative but testable) |
| NFR04 Reliability | Graceful fallback when LLM/STT/TTS/telephony fail; no record loss on transient failure; state recovery after brief interruption | Partly |
| NFR05 Maintainability | Agents independently testable; domain behaviour only via config; consistent structure/docs | Yes (testable by review) |

Proposal success criteria add: **task completion ≥ 70 %**, outperform baselines on **≥ 3 primary metrics** (or document why not).

### 1.7 Scope boundaries

**In scope:** orchestrator + 4 agents; configurable business-profile layer for 3 domains; operator auth + web dashboard (multi-tenant); text test console; voice pipeline for inbound calls; evaluation vs baselines; usability study; deployment guide.

**Out of scope:** real payments; integration with real PMS/EHR/booking systems; non-English; replacing CRMs; production-grade commercial deployment.

**Explicit strategy:** **text first**. All agent logic is built and validated over text; voice is the highest-risk layer and is integrated last "as time allows".

### 1.8 Technology stack (reconciled)

| Layer | Choice | Source / note |
|---|---|---|
| Backend language | Python | Both |
| API | FastAPI (REST) | Both |
| Orchestration | LangGraph (state graph), LangChain (tool calling) | Both |
| LLM | **Unspecified** (Proposal: GPT-4) | Q-1 |
| Relational DB | PostgreSQL | Both |
| Cache / session | Redis | Proposal table; Report §5.4 (missing from Report tools table) |
| Vector DB | ChromaDB | Both |
| Embeddings | Sentence-Transformers vs OpenAI | **C-1**, Q-2 |
| Sentiment | Fine-tuned DistilBERT (HuggingFace) + keyword/threshold rules | Both |
| Telephony | Twilio Programmable Voice | Both |
| STT | OpenAI Whisper | Both (API vs self-hosted unspecified) |
| TTS | OpenAI `tts-1-hd` / OpenAI TTS / ElevenLabs | **C-2**, Q-3 |
| Auth | bcrypt + JWT | Report §5.4 |
| Frontend | React.js + Tailwind CSS (SPA) | Both |
| Background jobs | "lightweight task queue" | Q-8 |
| Packaging / deploy | Docker; Render (or Railway) | Proposal; Report drops hosting |
| Diagrams | PlantUML, matplotlib | Report |

### 1.9 High-level architecture

```
                    ┌──────────────────────────── Channels ─────────────────────────────┐
 Customer ── call ─▶│ Twilio Voice ─▶ STT (Whisper)        TTS ◀── reply text            │
          ── SMS  ─▶│ Twilio SMS                                                         │
          ── text ─▶│ Dashboard test console                                             │
                    └───────────────┬────────────────────────────────────────────────────┘
                                    ▼  normalized request {tenant, session, channel, text}
                    ┌────────────── Orchestrator (LangGraph) ───────────────┐
                    │ load state → intent classify → decompose → route      │
                    │        ┌───────────┬──────────────┬────────────┐      │
                    │        ▼           ▼              ▼            ▼      │
                    │   Booking     Knowledge (RAG)  Sentiment   Follow-Up  │
                    │        └───────────┴──────┬───────┴────────────┘      │
                    │                aggregate → reply → save state         │
                    └────────────────────────────┬──────────────────────────┘
                                                 ▼
          PostgreSQL (tenants, profiles, bookings, interactions, sentiment, escalations, metrics)
          ChromaDB (per-tenant document chunks)   Redis (session cache, job queue)
                                                 ▲
                    Operator Dashboard (React) ── REST (FastAPI, JWT) ──┘
                    Evaluation harness ── same entrypoint as channels + single-agent baseline
```

Key property: **every agent reads domain behaviour from the business profile**, never from hard-coded domain logic (NFR05-02, FR-08-07).

### 1.10 Major modules and responsibilities

| # | Module | Responsibility | Depends on |
|---|---|---|---|
| 1 | Auth & tenancy | Operator accounts, login/logout, JWT, tenant scoping of every query | Postgres |
| 2 | Business profile | Schema, validation, CRUD, versioning; the single source of domain behaviour | 1 |
| 3 | Backend & data | Models, migrations, repositories, REST API, session store | 1 |
| 4 | Orchestrator | Normalization, intent classification, decomposition, routing, parallel fan-out, aggregation, clarification, state | 2, 3, agents |
| 5 | Booking agent | Availability, create/modify/cancel tools, alternatives, cancellation rules | 2, 3 |
| 6 | Knowledge agent | Ingestion (parse, chunk, embed, index per tenant), retrieval, grounded answer, refusal | 2, Chroma |
| 7 | Sentiment agent | Per-turn classification, complaint rules, escalation trigger, history | 2, 3, model |
| 8 | Follow-up agent | Post-interaction decisions, confirmations, scheduled reminders, feedback, retries | 3, queue, notifications |
| 9 | Channels | Twilio voice webhooks, STT/TTS adapters, SMS, latency logging, fallbacks | 4 |
| 10 | Dashboard | Profile forms, doc upload, test console, logs/escalations/analytics | REST API |
| 11 | Evaluation | Scenario sets, ground truth, single-agent + IVR baselines, metrics, reports | 4, 9 |

---

## 2. Documentation Audit

### 2.1 Missing requirements

| ID | Gap | Why it matters |
|---|---|---|
| M-1 | **No operator registration / account provisioning requirement.** UC-01 assumes "a registered account"; no FR says how it gets created (self sign-up? seeded by a platform admin?). | Blocks auth design and multi-tenancy |
| M-2 | **Password reset and account lockout** appear in UC-01 alternate/exception paths but have no FR. | Scope creep or silent omission |
| M-3 | **No platform-admin role.** "Administrator" and "Operator" are used interchangeably; nobody manages tenants. | Role model unclear |
| M-4 | **Customer identity**: no requirement for how a caller is identified (caller ID? name + phone? booking reference?) or verified before modifying/cancelling a booking. | Anyone could cancel anyone's booking |
| M-5 | **Phone number → tenant routing**: no requirement mapping a Twilio number to a business profile. | Core to multi-tenant voice |
| M-6 | **Escalation target**: FR-06-04 triggers "an escalation workflow" but the workflow is undefined (Q-6). Human agents are not part of the system. | Can't implement or test FR-06-04 |
| M-7 | **Notification channels for follow-ups** (SMS? email? which provider?) and customer consent/opt-out are unspecified. | FR-07 untestable |
| M-8 | **Supported document formats, size limits** for knowledge upload. | Ingestion design |
| M-9 | **Privacy / data retention**: call recordings, transcripts, phone numbers, and **clinic health information** have no retention, consent ("this call may be recorded") or deletion requirements. | Ethics approval for usability study; clinic domain sensitivity |
| M-10 | **Numeric NFR targets** (latency, text response time, SUS, retrieval time) are all deferred. | No pass/fail quality gates |
| M-11 | **Observability**: no requirement for structured logs, traces of agent decisions, or cost tracking per call. | Debugging multi-agent flows and student budget |
| M-12 | **Concurrency/load**: how many simultaneous calls/sessions must be supported? | Sizing, Redis/state design |
| M-13 | **Business hours / after-hours behaviour**, holidays, timezone per tenant. | Booking correctness |
| M-14 | **Conversation end detection**: what marks an interaction "concluded" (hang-up, idle timeout, explicit goodbye)? Follow-up depends on it. | FR-07-01 trigger |
| M-15 | **Rate limiting / abuse** on public channels (prank calls, prompt injection via voice/SMS). | Security, cost |
| M-16 | **Evaluation dataset spec**: number of scenarios per domain, mix of single/multi-intent, who writes ground truth, inter-rater method. | FR-10 reproducibility |
| M-17 | **Profile activation / versioning**: UC-08 says a saved profile is "immediately usable by the live agents", but there is no draft/publish concept or rollback. | Operators could break a live line |

### 2.2 Contradictions

| ID | Topic | Proposal says | Report says | Resolution used |
|---|---|---|---|---|
| C-1 | Embeddings | Sentence-Transformers (tools table + architecture figure) | OpenAI embeddings (§5.4) | Open, Q-2 |
| C-2 | TTS | "OpenAI TTS / ElevenLabs" (figure); OpenAI TTS (table) | OpenAI `tts-1-hd` | Open, Q-3. Note `tts-1-hd` is the higher-latency variant, against NFR01-01 |
| C-3 | LLM | GPT-4 | Not stated | Open, Q-1 |
| C-4 | Baselines | IVR + human agents | O7: IVR + single-agent; FR-10-06: single-agent + IVR/human; SD-10: "Traditional Support System" | Treat as **three** baselines, Q-5 |
| C-5 | Multi-tenancy & auth | Absent | Core scope (O6, FR-01, NFR03) | Report wins |
| C-6 | Channels | Voice, text, **email** (architecture text) | FR-02-03 email input required, but §1.6 "Included" lists only voice + text console | Open, Q-10 |
| C-7 | Sentiment's place in the flow | One of four routed agents | UC-03 routes to it like any agent; SD-2 runs it in **parallel on every turn**; UC-06 "monitors each message" | Treat as an **always-on per-turn side branch**, not an intent |
| C-8 | Follow-up trigger | Post-interaction | SD-2 schedules it inside a turn; SD-6 on "Interaction completed"; §5.4 event-driven + time-based queue | Event-driven: booking events + interaction-closed event, not orchestrator-inline |
| C-9 | Voice implementation | Streaming, minimal latency | §5.4 `<Say>/<Play>` (turn-based) vs §5.6 "will likely require streaming" | Q-4 |
| C-10 | Session mechanism | n/a | SD-8 auth diagram: "Session Manager → Session ID"; §5.4: stateless JWT; FR-01-04 requires secure termination | JWT with server-side revocation (or short-lived access + refresh) |
| C-11 | Redis | In tools table | Missing from Report tools table, but used in §5.4 and TC-09 | Keep Redis |
| C-12 | Hosting | Render (table), Render/Railway (figure), CI/CD pipeline | No hosting stated | Docker Compose locally; Render as default target |
| C-13 | Team assignment | Muneeb: RAG; Abdul: voice | Abdul: RAG + auth + dashboard; Muneeb: booking, sentiment, follow-up, voice. TC-02 author "Syeda/Team" is not a team member | Report wins; fix TC-02 author |
| C-14 | Timeline | Agent dev May–Sep 2026, voice Aug–Oct 2026, orchestrator Sep–Nov 2026 | Implementation in "FYP-II months 1–6"; Report says "no working system exists yet" (Oct 2026) | Proposal schedule has slipped; see R-10 |
| C-15 | Objective list | 7 objectives incl. "≥3 verticals + deployment guide" | O1–O7 drops the guide from objectives but keeps it as FR-10-08 | Keep FR-10-08 |
| C-16 | Domain switching | "Switching domains requires configuration changes only with **minimal** agentic logic or backend modifications" (Phase 5) | "**without** core-agent code changes" (O2, NFR05-02) | Report's stricter rule, but see D-3 |

### 2.3 Ambiguities

| ID | Statement | Ambiguity |
|---|---|---|
| A-1 | "supported" appears in ~30 FRs ("supported bookings", "supported attributes") | The supported set is never enumerated per domain |
| A-2 | "configured sentiment categories" (FR-06-01) | Binary? 3-class? Emotion classes? Complaint as a separate label? |
| A-3 | "configured complaint or negative-sentiment conditions" (FR-06-04) | Single turn vs trend over turns; thresholds; keyword list owner |
| A-4 | "Intent cannot be classified confidently" (UC-03) | No confidence measure defined for an LLM classifier |
| A-5 | "Task completion = correct execution of all subtasks" | Who decides "correct"? Partial credit? |
| A-6 | "Overall response accuracy" vs "knowledge retrieval accuracy" (FR-10-03/04) | Metric definitions and scoring rubric missing |
| A-7 | "Re-indexes only the changed content" (UC-05 alt path) | Requires chunk-level diffing; replace-whole-document is far simpler |
| A-8 | "Business profile" contents (FR-08-04) | Fields named, structure not designed (D-1) |
| A-9 | "Operator switches an existing profile between domains" (UC-08 alt) | What happens to existing bookings/documents? Is domain mutable at all? |
| A-10 | "Graceful fallback" (NFR04-01) | Fallback wording, escalation, retry counts undefined |
| A-11 | "Brief interruption" (NFR04-03, TC-09 step 3) | Duration and which component restarts (API? worker? Redis?) |
| A-12 | Whisper "configured STT service/model" | OpenAI API vs self-hosted Whisper (GPU needed) |
| A-13 | "Same core workflow across domains" | Is per-domain prompt text allowed in the profile? (Assumed yes) |
| A-14 | "Multi-turn" | Max turns, session timeout, memory window |
| A-15 | Proposal scope: "not designed to use actual API connections to external databases" | Sentence unfinished; presumably means no third-party booking systems |

### 2.4 Incomplete designs

| ID | Artifact | State |
|---|---|---|
| D-1 | **Business-profile schema** (the central Phase-1 deliverable) | Not designed anywhere |
| D-2 | **Database / ER diagram** (§3.6) and **class diagram** (§3.5) | Placeholders only. Entities hinted: Operator, Customer, Conversation, Booking, KnowledgeDocument, ComplaintTicket, FollowUpTask |
| D-3 | **Domain-generic booking model** | Hotels (room types × nights, check-in/out), restaurants (tables × party size × time slot), clinics (practitioner × appointment slot) differ structurally. Config-only switching needs a generic resource/capacity/slot abstraction that is not designed |
| D-4 | **Software architecture** (§3.4) and **collaboration diagram** (§3.7) | Placeholders |
| D-5 | **All 10 use-case diagrams and 10 activity diagrams** (§3.1, §3.2) | Placeholders |
| D-6 | **Sequence diagrams** | 9 of 10 exist. SD-9 deliberately omitted. The auth diagram is titled "SD-8: Operator Dashboard and Administration" while the dashboard one is "SD-7"; numbering doesn't match figure order |
| D-7 | **LangGraph state schema**: fields, reducers for parallel branches, checkpointer | Not designed |
| D-8 | **Agent interfaces**: input/output contracts between orchestrator and agents, error shape | Not designed (a team-coordination risk the Report itself flags) |
| D-9 | **REST API spec** | Only `/auth/login` and `GET /api/profiles/{id}` named |
| D-10 | **Prompt design** and grounding/refusal policy | Not designed |
| D-11 | **Sentiment model training plan**: dataset, labels, metrics, threshold calibration | Not designed |
| D-12 | **Twilio call flow**: webhooks, TwiML, number provisioning, hang-up handling, barge-in | Only one sentence |
| D-13 | **IVR baseline**: how a "traditional IVR" is built or simulated | Not designed |
| D-14 | **Tenant isolation in ChromaDB**: collection per tenant vs metadata filter | Flagged as challenge, not designed |
| D-15 | **Test strategy beyond 10 representative cases**: no unit/integration/e2e split, no automation plan | Thin |
| D-16 | **SDLC figure, WBS figure, all screenshots** | Placeholders |

### 2.5 Potential technical risks

| ID | Risk | Likelihood / impact | Mitigation |
|---|---|---|---|
| R-1 | **Voice latency.** Turn = Twilio → STT → *several* LLM calls (classify, decompose, agent(s), aggregate) → TTS → Twilio. Easily 4–8 s with batch Whisper and `tts-1-hd` | High / High | Merge classify+decompose into one structured-output call; fast model for routing; `tts-1`; filler audio ("one moment"); measure per stage from day one |
| R-2 | **Orchestrator reliability**: wrong decomposition, lost context between dependent subtasks (e.g. "what's the cancellation fee, and cancel it") | High / High | Typed subtask schema with explicit dependencies; golden-scenario regression suite run on every change |
| R-3 | **Domain-generic booking** (D-3) turns into hidden per-domain code, breaking O2/NFR05-02 | High / High | Design the profile + resource model before writing the Booking agent |
| R-4 | **Tenant data leakage** across Postgres and Chroma, including via the LLM context | Medium / Critical | Tenant ID mandatory in every repository method and vector query; dedicated isolation tests |
| R-5 | **RAG hallucination** / failing to say "I don't know" | Medium / High | Similarity threshold + "answer only from context" prompt + refusal tests with out-of-scope questions |
| R-6 | **DistilBERT fine-tuning without in-domain labelled data** | Medium / Medium | Start with an off-the-shelf sentiment model; fine-tune only if evaluation shows a gap |
| R-7 | **API cost and free-tier limits** (Twilio trial numbers can only call verified numbers; trial message prefix) | High / Medium | Budget per test run; mock providers in tests; upgrade Twilio for usability study |
| R-8 | **Non-deterministic LLM outputs** make tests and evaluation flaky | High / Medium | Temperature 0, structured outputs, fake LLM in unit tests, repeated runs with reported variance in evaluation |
| R-9 | **Parallel subtasks write conflicting state** in LangGraph | Medium / Medium | Explicit reducers; agents return results, orchestrator alone mutates shared state |
| R-10 | **Schedule slip.** Per the Proposal Gantt the agents should already be built; Report says nothing is implemented yet | High / High | Text-first MVP (orchestrator + booking + knowledge) before anything else; voice strictly last |
| R-11 | **Redis-only session state** loses conversations on restart (NFR04-03) | Medium / Medium | LangGraph Postgres checkpointer as source of truth; Redis only as cache |
| R-12 | **Prompt injection** by callers or by uploaded documents | Medium / Medium | Treat all customer/document text as data; tools enforce business rules server-side, never trust LLM-provided tenant IDs |
| R-13 | **Unfair baseline comparison** (human/IVR baselines hard to run comparably) | Medium / High (research validity) | Fix the protocol early (Q-5); same scenario set, documented limitations |
| R-14 | **Health data in clinic domain** | Low / High | Synthetic data only; no real patient info in usability sessions |
| R-15 | **Library churn** (LangGraph/LangChain APIs change often) | Medium / Low | Pin versions; isolate behind thin adapters |

### 2.6 Assumptions I would need to make

| ID | Assumption |
|---|---|
| AS-1 | The Report is authoritative where it differs from the Proposal (§2.2). |
| AS-2 | Greenfield: no code exists yet. Monorepo with `backend/`, `frontend/`, `eval/`. |
| AS-3 | One operator account owns one or more business profiles; each profile has exactly one domain (hotel/restaurant/clinic), fixed at creation. |
| AS-4 | Operators are created by seed script/CLI for the prototype; self-registration is out of scope unless confirmed (M-1). Password reset and lockout are "should", not "must". |
| AS-5 | Customers are identified by phone number (caller ID / SMS sender) plus name; modifying or cancelling requires the booking reference or matching phone number. |
| AS-6 | Each profile maps to one Twilio number; the dashboard text console selects the profile explicitly. |
| AS-7 | Escalation = persisted escalation record + dashboard alert + operator notification + customer told they'll be contacted (Q-6). No live call transfer. |
| AS-8 | Sentiment runs on every customer turn in parallel with routed subtasks; it is not an intent. |
| AS-9 | Follow-ups are sent by SMS (Twilio) with email optional; reminders are scheduled jobs. |
| AS-10 | Knowledge documents: PDF, DOCX, TXT, MD; updating a document replaces all its chunks (no partial re-index). |
| AS-11 | Per-domain prompt snippets and vocabulary live in the profile/templates, which satisfies "no core-agent code changes". |
| AS-12 | English only, single timezone per profile, synthetic seed data for all three domains. |
| AS-13 | Inbound email (FR-02-03) is deferred to "if time allows". |
| AS-14 | Prototype scale: tens of concurrent sessions, not thousands. |
| AS-15 | Indicative NFR targets until profiling: text reply p95 ≤ 5 s, voice end-of-speech-to-first-audio p95 ≤ 3 s (stretch), retrieval ≤ 300 ms, SUS ≥ 68, task completion ≥ 70 %. |

### 2.7 Document hygiene (non-technical, for the submission)
- Proposal plagiarism certificate names Daoud Haroon as leader but quotes registration **FA23-BSE-006** (Abdul Haseeb's). Report certificate has "[XX]%" and "[Father's Name]" unfilled.
- Proposal citation numbers don't match references (e.g. Sentence-Transformers cited as [9], which is Guo et al.; AutoGen cited for single-agent reliability claims). Proposal Gantt labels two rows "Phase 5" and the evaluation row as Phase 5 vs Phase 6 in the text.
- Report: lists of tables/figures not generated; most Chapter 3 figures are placeholders; Twilio/Whisper/OpenAI TTS references exist but no LLM reference.

---

*Prepared as a pre-implementation review. No code has been written and no repository has been touched.*

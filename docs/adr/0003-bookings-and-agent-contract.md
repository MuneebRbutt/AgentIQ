# ADR-0003: Bookings, availability and the agent contract

- **Status:** Accepted (proposed default; the team may revise by superseding this ADR)
- **Date:** 2026-10-18
- **Requirements:** FR-04-01…06, FR-09-01/02/04, NFR03-02, NFR05-01/02; analysis D-3, D-8, M-4, C-8, AS-5; builds on ADR-0002

## Context

M3 delivers the Booking agent before the LLM layer and orchestrator exist (M5). The agent
must work the same way for all three domains, enforce rules in code rather than prompts
(CLAUDE.md rule 3), and have a contract the orchestrator can rely on. The specification
leaves customer identity (M-4) and the agent interfaces (D-8) open.

## Decisions

### 1. Data

| Table | Holds |
|---|---|
| `customers` | Per business: name, phone, email. Identified by the last 10 digits of the phone (AS-5), so `+92 300 1234567` and `0300-1234567` are one customer. |
| `bookings` | One unit of one profile resource for a period: service, resource, party size, `starts_at`/`ends_at` (UTC), status `confirmed`/`cancelled`, custom-field `details`, a 6-character `reference`, and `source` (agent or dashboard). |
| `booking_events` | Every create, change and cancel with actor and field-level changes (FR-04-06). These are the trigger events the Follow-Up agent consumes in M8 (C-8). |

Bookings belong to a profile and a tenant; every query filters by both (NFR03-02).

### 2. Availability is one generic rule (`app/bookings/availability.py`)

A request (service, party size, local start, nights) is checked in order against: the
service existing, party-size limits, booking mode (slot grid and duration for `time_slot`;
check-in/out times and max nights for `overnight`), opening hours (`time_slot` only;
intervals past midnight belong to the day they open), minimum notice and the advance
window. It is then placed on the **smallest resource that offers the service, fits the
party and has a free unit for the whole period**, counting peak concurrent use rather
than total overlaps. Back-to-back bookings don't clash.

When unavailable, up to three **alternatives** are the nearest acceptable starts in time
(same day and the following week for slots; check-in dates ±7 days for stays). Large
parties are not split across several resources (out of scope).

The checker is pure (profile + booked periods + now), so it is unit-tested without a
database and can be reused by the evaluation harness.

### 3. Consistency

Create and change re-check availability inside the transaction while holding a
per-profile Postgres advisory lock, so two simultaneous requests can't take the last unit
(tested with concurrent requests). Times are stored in UTC and interpreted in the
profile's time zone.

### 4. Change and cancellation rules

- Customers (through the agent) may change or cancel only up to
  `booking_rules.cancellation_notice_hours` before the start (FR-04-05). The agent's
  refusal quotes the business's own cancellation policy text.
- Operators (dashboard API) may override that rule; the change is still recorded with
  the operator as actor.
- Started or cancelled bookings can't be changed.

### 5. Customer identity (M-4, AS-5)

A customer changes or cancels a booking only with its **reference plus a matching phone
number**. On calls and SMS the phone comes from caller ID through `AgentContext`, never
from the model. A wrong pair gets the same "no booking matches" answer as a missing one.
Name and phone are always required to book; `required_details` can add email, and the
profile's required custom fields are enforced too.

### 6. Agent contract (D-8; `app/agents/contracts.py`)

- **In:** `AgentContext` (tenant, profile, caller phone: trusted facts from the channel)
  plus a typed subtask. For booking: action `check | create | modify | cancel`, the
  extracted details, a booking reference for changes, and `confirmed`.
- **Out:** `AgentResult` with a `status`:
  - `completed`
  - `needs_info` (with `missing` field names)
  - `needs_confirmation` (with the details to read back)
  - `unavailable` (with `alternatives`)
  - `rejected` (with the reason)

  It also carries a plain factual `message` and structured `data`.
- The agent never books without `confirmed=true` (FR-04-03). The orchestrator asks the
  customer and repeats the subtask with `confirmed` set.
- Messages use only profile vocabulary (service and resource names, policies). The
  orchestrator phrases the final reply in M5.

### 7. Dashboard API

All routes are under `/api/v1/profiles/{profile_id}`:

- `GET availability`
- `GET`/`POST bookings`
- `GET`/`PATCH bookings/{id}`
- `POST bookings/{id}/cancel`
- `GET bookings/{id}/events`

An unavailable request returns 409 with `reason` and `alternatives`. Missing details
return 422 with a `missing` list.

## Consequences

- TC-04 passes for the hotel, restaurant and clinic profiles with one code path.
- M5 only has to map conversation to `BookingSubtask` and phrase `AgentResult`s.
- `interaction_id` on bookings (FR-09-02) is added when interactions exist (M5/M6).

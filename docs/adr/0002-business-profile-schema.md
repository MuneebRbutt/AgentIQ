# ADR-0002: Business-profile schema and generic booking model

- **Status:** Accepted (proposed default; the team may revise by superseding this ADR)
- **Date:** 2026-10-18
- **Requirements:** FR-08-04…07, FR-09-01/02, NFR05-02; analysis D-1, D-3, M-17, A-9, AS-3, AS-11, AS-12; Q-7

## Context

The business profile is the only place domain behaviour may come from: hotels, restaurants
and clinics must run through the same agent code (FR-08-07, NFR05-02). The specification
lists what a profile contains ("services, policies, booking rules, escalation rules, and
knowledge resources", FR-08-04) but never designs it (analysis D-1). The three domains also
book differently: hotels by night, restaurants by table and time, clinics by practitioner
and appointment slot (D-3).

## Decision

### 1. One validated document per profile

A profile is a Pydantic model, `BusinessProfileConfig` (`backend/app/profiles/schema.py`),
stored as JSONB in `business_profiles.config`. Sections:

| Section | Purpose | Mandatory (FR-08-05) |
|---|---|---|
| `name`, `domain`, `description` | Identity; `domain` is hotel / restaurant / clinic | name, domain |
| `timezone`, `currency`, `contact` | IANA zone (one per profile, AS-12), ISO currency, phone/email/address | timezone |
| `opening_hours` | Per weekday, a list of intervals; a missing day is closed; `closes < opens` runs past midnight | yes, open at least one day |
| `services` | What the business offers, optional duration and price | at least one |
| `resources` | Bookable units (see §2) | at least one |
| `booking_rules` | Mode, slot size, party size, notice, advance window, cancellation notice, details to collect | yes |
| `policies`, `faqs` | Short texts the agents may quote | no |
| `escalation` | Sentiment threshold, trigger keywords, clarification limit, who to notify, hand-off message (Q-6) | defaults provided |
| `assistant` | Greeting, tone, extra instructions: the per-business prompt snippets (AS-11) | greeting |

Unknown keys are rejected so typos surface instead of being ignored. Cross-field rules
(unique IDs, resources referring to real services, booking-mode consistency, overlapping
hours, durations for time-slot services) are validated in the schema, so the API, the
dashboard's live check (`POST /profiles/validate`) and the seed command share one rule set.

A document rather than normalized tables because the profile is read whole by every agent
turn and edited whole by the dashboard form, its shape will evolve during the project, and
nothing queries inside it yet. `name` and `domain` are copied to columns for listing.

### 2. Generic resources instead of domain-specific booking

A **resource** is a kind of bookable unit with a `quantity` of identical units and a
`capacity` (people per unit), optionally limited to some services:

| Domain | Resources (example profiles) |
|---|---|
| Hotel | Standard room x30 (2 guests), Deluxe room x20 (3), Family suite x10 (5) |
| Restaurant | Table for 2 x8, Table for 4 x10, Table for 8 x3, Private room x1 (20) |
| Clinic | Dr. Ayesha x1 (1 patient), Dr. Hamza x1 (1) |

`booking_rules.mode` captures the one real structural difference:

- `time_slot`: a start time on a `slot_minutes` grid, lasting the service's
  `duration_minutes` (or `default_duration_minutes`). Restaurants and clinics.
- `overnight`: a check-in date and a number of nights, with `check_in_time` /
  `check_out_time`. Hotels.

Availability in M3 is then one generic rule: a booking needs a unit of a suitable resource
whose capacity fits the party and which isn't already taken for an overlapping period.
The Booking agent never looks at `domain`.

### 3. Versions and concurrent edits (M-17)

`version` starts at 1 and increases on every save; each save also writes a snapshot to
`business_profile_versions` (with the operator who saved it). An edit (`PUT`) must send the
version it started from and gets **409** if someone saved in between. A saved profile is
live immediately, as UC-08 requires; there is no draft/publish step yet, but the snapshots
make a later rollback feature a small addition.

### 4. Domain can change (A-9)

UC-08's alternative path switches a profile between domains by configuration. Because
`domain` drives no code, an edit may change it along with the rest of the profile. Once
bookings exist (M3), switching keeps them; the team should decide in M3 whether to block
switches while future bookings refer to resources that no longer exist.

### 5. Templates and seed data

`backend/app/profiles/examples/{hotel,restaurant,clinic}.yaml` are complete, valid,
synthetic profiles. They serve as dashboard starter templates (`GET /profiles/templates/{domain}`)
and demo data (`python -m app.cli seed-profiles`). A unit test validates all three.

### 6. Out of scope here

Knowledge documents (FR-08-04 "knowledge resources") get their own table and upload flow in
M4 and attach to a profile by `profile_id`. Phone-number-to-profile mapping (M-5, AS-6)
arrives with voice in M11. Bookings and their tables arrive in M3.

## Consequences

- Every agent receives a typed `BusinessProfileConfig`; adding a fourth domain means a new
  example file, not new code.
- Changing the schema in a way that invalidates stored profiles needs an Alembic data
  migration that rewrites `config` (and a snapshot of what changed).
- `pyyaml` (example files) and `tzdata` (time zones on Windows, which has no system zone
  database) are added as runtime dependencies.

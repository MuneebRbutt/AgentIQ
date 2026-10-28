# ADR-0008: Operator dashboard

- **Status:** Accepted (proposed default; the team may revise by superseding this ADR)
- **Date:** 2026-10-20
- **Requirements:** FR-08-01…07, FR-01, FR-06-04, FR-07, FR-10-02, NFR02-01/02, NFR03-02; UC-08; TC-08; analysis Q-9; amends ADR-0001 (frontend stack)

## Context

M9 builds the operator dashboard on the APIs of M1–M8. The Report asks for a web dashboard
where an operator logs in, configures a business profile "through a form" with mandatory
fields checked before saving (FR-08-01/05, UC-08), uploads knowledge documents, tries the
assistant in a test console (FR-08-02) and reads conversation logs, escalations and
analytics (FR-08-03). NFR02 asks that a new operator can set up a profile without help and
that errors say how to fix them. TC-08 creates a profile, then tries one with a mandatory
field missing.

## Decisions

### 1. Stack (Q-9, ADR-0001)

React 19 + strict TypeScript + Vite stay. Added, each because it removes code we would
otherwise write and maintain:

| Package | Why |
|---|---|
| `react-router` | URLs for every screen, so a profile tab or a conversation can be linked and reloaded |
| `@tanstack/react-query` | caching, refetching and loading/error states for server data (CLAUDE.md §4) |
| `openapi-fetch` + `openapi-typescript` (dev) | a typed client generated from the API's schema (decision 2) |
| `tailwindcss` + `@tailwindcss/vite` (dev) | styling without a component library or hand-written CSS files |

No component library and no chart library: the screens are forms, tables, badges and stat
tiles, which a few shared components (`components/ui.tsx`) cover. The overview shows
numbers and counts rather than charts, since with prototype volumes a chart adds little.

### 2. The API schema is the contract

`python -m app.cli export-openapi` writes `docs/api/openapi.json`, and `npm run gen:api`
turns it into `src/api/schema.d.ts`. Every call goes through `api` in `src/api/client.ts`,
so a backend change that breaks the dashboard fails `npm run typecheck`. Two checks stop
drift: a backend unit test fails when the committed schema differs from the code, and CI
(and `scripts/check.ps1`) regenerates the client and fails on any difference.

### 3. The server validates the profile; the form only shows the result (FR-08-05)

The profile rules live once, in `BusinessProfileConfig` (ADR-0002). The editor never
copies them. It holds the profile as one draft document, edited by path
(`['services', 0, 'name']`), and sends it to `POST /profiles/validate` half a second after
the operator stops typing and again before saving. Each error is shown under the field its
path points to, in plain words (`lib/errors.ts`: "This is required.", "Choose one of the
options.", "Use a three-letter code, like PKR."), with a count on each section's tab.
Saving is blocked until the report is clean (UC-08 exception path). Clearing an optional
field removes the key, so the API sees it as missing rather than as an empty string.

The form has a field for every setting of the schema, grouped as an operator thinks about
them, and nothing in it depends on the business type (FR-08-07). The three examples are
offered as starting templates, plus a blank form.

### 4. Logins

The token from `POST /auth/login` is kept in `sessionStorage`, so it lasts for the browser
tab and is sent as a bearer header. Any 401 clears it and returns the operator to the login
page with "Your session has ended", which covers expiry, logout elsewhere and deactivation
(FR-01-04). The cached server data is cleared on login and logout, so one operator never
sees another's data in a shared browser. An HttpOnly cookie would hide the token from
scripts, but the API is token-based and on another origin in development; revisit with
deployment (M12).

### 5. Analytics endpoint (FR-08-03, FR-10-02)

`GET /api/v1/profiles/{id}/analytics?days=30` (1–365) returns, for that period:
conversation counts by status and needing review, customer messages, reply outcomes, agent
results, reply time (average, median, 95th percentile from the stored `timings.total`),
sentiment counts, and escalations, bookings and follow-ups by status. It is computed with
SQL aggregates over existing tables, so it needs no new table and stays correct by
construction; it is tenant-scoped like every profile route.

### 6. Testing

Page tests render the whole app (router, query client, login) against a fake API
(`src/test/fakeApi.ts`) that answers routes from plain functions and records each call.
TC-08 steps 1 and 2 and the log view (step 3) are tests in `NewProfilePage.test.tsx` and
`profile/profilePages.test.tsx`; the fixture profile is the restaurant example exported
from the backend. A manual check against the real API (log in, blank profile blocked,
template profile created, console conversation with a hand-off, logs, escalations,
overview) found no console errors. Playwright end-to-end tests are left for M12.

## Consequences

- New runtime dependencies: `react-router`, `@tanstack/react-query`, `openapi-fetch`;
  dev: `openapi-typescript` (pinned to the project's TypeScript through an npm override,
  since it declares TS 5), `tailwindcss`, `@tailwindcss/vite`. Run `npm ci` after pulling.
- Changing an API schema means: export the schema, `npm run gen:api`, commit both.
- The editor checks a draft on every pause, which is one small request per pause; fine for
  one operator at a time.
- "New operator sets up a profile unaided" (M9 exit) is judged in the usability study
  (M12); the dashboard gives templates, hints and inline errors to support it.

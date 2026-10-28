# AgentIQ

Multi-agent AI calling agent for customer support in small and medium businesses (hotels,
restaurants, clinics). Final Year Project, COMSATS University Islamabad, Lahore Campus.

An orchestrator agent splits each customer request into subtasks and delegates them to
specialist agents (Booking, Knowledge/RAG, Sentiment & Complaint, Follow-Up), configured per
business through a profile on the operator dashboard.

- **Developer guide and conventions:** [CLAUDE.md](CLAUDE.md)
- **Specification:** [docs/spec](docs/spec) · **Analysis and open questions:** [docs/AgentIQ_Project_Analysis.md](docs/AgentIQ_Project_Analysis.md)
- **Decisions:** [docs/adr](docs/adr)

## Prerequisites

- Python 3.12 or newer
- Node.js 22 or newer
- Docker Desktop

## First-time setup (Windows PowerShell)

Run from the repository folder.

```powershell
# 1. Configuration
Copy-Item .env.example .env

# 2. Database and Redis
docker compose up -d

# 3. Backend
cd backend
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass   # only if activation is blocked
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
alembic upgrade head
cd ..

# 4. Frontend
cd frontend
npm ci
cd ..
```

Then create your first operator account (there is no public sign-up). This makes a new
business and asks for a password:

```powershell
cd backend
python -m app.cli create-operator --business "Grand Hotel" --email owner@grand.test --name "Sara Khan"
python -m app.cli list-tenants                 # businesses and their IDs
# add a colleague to the same business:
python -m app.cli create-operator --tenant-id <id from list-tenants> --email staff@grand.test --name "Ali Raza"
```

To get the three example business profiles (hotel, restaurant, clinic) for that business:

```powershell
python -m app.cli seed-profiles --tenant-id <id from list-tenants>
```

> Upgrading an older checkout? Delete and recreate `backend\.venv`, then run step 3. Your
> existing `.env` keeps working; add `REDIS_URL` and `REDIS_PORT` from `.env.example`.

## Run it

```powershell
# Terminal 1: services (keep running; `docker compose stop` stops them without losing data)
docker compose up -d

# Terminal 2: API on http://localhost:8000 (interactive docs at /docs)
cd backend
.\.venv\Scripts\Activate.ps1
fastapi dev app\main.py

# Terminal 3: dashboard on http://localhost:5173
cd frontend
npm run dev
```

### The dashboard

Open `http://localhost:5173` and log in with the operator account you created. From there:

- **New profile** starts from the hotel, restaurant or clinic example, or from a blank form.
  Every field is checked as you type and again when you save; a missing or wrong field is
  highlighted with what to fix, and nothing is saved until the profile is valid.
- Open a profile for its tabs: **Overview** (conversations, hand-offs, bookings and reply
  times for the last 7, 30 or 90 days), **Settings** (the same editor), **Knowledge**
  (upload `.pdf`, `.docx`, `.txt` or `.md` files), **Test console** (chat with the assistant
  as a customer would), **Conversations** (every message and what each agent did),
  **Escalations** (call back and mark handled), **Bookings** and **Follow-ups**.

The dashboard calls the API at `VITE_API_URL` (default `http://localhost:8000`); the API
accepts the dashboard's address from `FRONTEND_ORIGIN`.

### Using the API directly

Log in at `http://localhost:8000/docs`: call `POST /api/v1/auth/login` with your email and
password, copy `access_token`, click **Authorize** and paste it. `GET /api/v1/auth/me` and
`GET /api/v1/operators` then work, and so do the `/api/v1/profiles` endpoints for business
profiles, `/api/v1/profiles/{id}/bookings` for bookings, `/api/v1/profiles/{id}/documents`
for knowledge documents and `/api/v1/profiles/{id}/analytics?days=30` for the overview
figures. `POST /api/v1/auth/logout` ends the session.

To try the Knowledge agent, upload a `.pdf`, `.docx`, `.txt` or `.md` file with
`POST /api/v1/profiles/{id}/documents`, then ask with `POST /api/v1/profiles/{id}/knowledge/ask`
and a body like `{"question": "What is your cancellation policy?"}`. The first upload
downloads the MiniLM embedding model (~80 MB) once. Without `LLM_API_KEY` in `.env` the
agent answers by quoting the document; with a key it writes a short answer from it.

To talk to a profile's assistant in the terminal (questions, bookings over several
messages), run from `backend`:

```powershell
python -m app.cli chat --profile-id <profile id>
```

Bookings need a language model: set `LLM_API_KEY` (and `LLM_BASE_URL` / `LLM_MODEL` for a
provider other than OpenAI) in `.env`. Without one, the assistant still answers questions
from the profile and uploaded documents.

The same conversation works over the API (the dashboard's test console uses it):

1. `POST /api/v1/profiles/{id}/sessions` with `{}` (or `{"customer_phone": "+92 300 1234567"}`
   to act as a caller) starts a conversation and returns its `id` and the greeting.
2. `POST /api/v1/profiles/{id}/sessions/{session id}/messages` with `{"text": "..."}` returns
   the reply, what each agent did, and `timings`: milliseconds for understanding, each agent,
   the reply and the whole turn.
3. `GET /api/v1/profiles/{id}/sessions` lists conversations (filter with `?status=open`,
   `handed_off` or `closed`); `GET .../sessions/{session id}` shows every turn;
   `POST .../sessions/{session id}/close` ends one.

Every customer message is also checked for sentiment. A very upset customer, a word from
the profile's escalation keywords (such as "refund") or several unhappy messages in a row
hand the conversation to a person: the customer hears the profile's hand-off message, and
the case appears at `GET /api/v1/profiles/{id}/escalations`, where operators mark it
`acknowledged` or `resolved` with `PATCH`. To see how the sentiment model reads messages
(it downloads once, ~135 MB, the first time):

```powershell
python -m app.cli check-sentiment "This is the third time my booking has been wrong, I'm furious."
```

Follow-ups go out without anyone pressing a button: a confirmation when a booking is made
or changed, a reminder before it (24 hours by default) and, if the profile turns it on, a
feedback request after a conversation ends (it is closed, or quiet for 30 minutes). The
profile's `follow_up` section sets the timing and wording. The API sends them in the
background; `GET /api/v1/profiles/{id}/follow-ups` lists them, and
`POST .../follow-ups/{id}/cancel` stops one that hasn't gone yet. Until you add Twilio
(`TWILIO_*`) or email (`SMTP_*`) settings to `.env`, messages are only written to the log
and listed as `logged`. To try your settings:

```powershell
python -m app.cli check-notifications --phone "+92 300 1234567"
python -m app.cli run-followups --once     # one pass now, instead of waiting for the API
```

### Phone calls and text messages

The assistant can answer a real phone number through Twilio, by call and by text message
(FR-02). You need a Twilio account with a voice and SMS number and a public HTTPS address for
the API:

1. In `.env` set `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` (Twilio console, Account Info).
2. Run `ngrok http 8000` and put its `https://...` address in `PUBLIC_BASE_URL`, then
   restart the API.
3. On the dashboard, open a profile's **Calls, SMS & email** tab and add your Twilio number.
   Copy the three URLs it shows into the number's settings in the Twilio console: "A call
   comes in" (Webhook, HTTP POST), "Call status changes" and "A message comes in" (Webhook,
   HTTP POST).
4. Call or text the number. A trial account only talks to numbers you verified in Twilio.

By default Twilio's own speech recognition and voice are used, so nothing else is needed.
To use Whisper and an OpenAI-compatible voice instead, set `STT_PROVIDER=whisper` and
`TTS_PROVIDER=openai` with their keys (see `.env.example`) and check them with
`python -m app.cli check-voice`. `python -m app.cli voice-latency` shows how long each
stage of recent calls took.

### Email from customers

Customers can email a business too (FR-02-03). The API reads one mailbox over IMAP and
answers from SMTP, so it needs no public address. With a Gmail account: turn on 2-step
verification, create an app password, set `IMAP_HOST=imap.gmail.com`,
`SMTP_HOST=smtp.gmail.com` and both usernames and passwords in `.env`, and restart the API.
Then add a plus address of the mailbox (for example `you+clinic@gmail.com`) on the profile's
**Calls, SMS & email** tab and email it.

### Deployment and the usability study

[docs/deployment.md](docs/deployment.md) deploys the whole app to Render from `render.yaml`
(one Docker image serving the API and the dashboard, with the three demo profiles created on
first start). `docker compose --profile app up -d --build` runs the same image locally.
[docs/study/](docs/study/protocol.md) has the usability study protocol, consent form and SUS
questionnaire; `python -m app.cli sus-score` scores the answers (FR-10-07).

### Evaluation

To compare AgentIQ with the single-agent and keypad-menu (IVR) baselines on the 30
scenarios in `eval/scenarios` (FR-10), run from `backend`:

```powershell
python -m app.cli evaluate                 # writes eval\reports\<timestamp>\report.md
```

It needs `LLM_API_KEY` for real numbers (without it, the single-agent baseline is skipped
and AgentIQ runs without a model). It works in a throwaway business that is deleted
afterwards and never sends a message. Metrics, options, adjudication and the human
role-play protocol are in [eval/README.md](eval/README.md).

The dashboard's profile list shows the API, PostgreSQL and Redis status. Directly:
`http://localhost:8000/api/v1/health` (returns 503 and names the failing service if one is down).

## Check your work

```powershell
.\scripts\check.ps1            # everything CI runs: format, lint, types, tests, build
.\scripts\check.ps1 -Fix       # auto-format first
```

Integration tests run when `docker compose up -d` is up and are skipped otherwise. They use
a separate `agentiq_test` database (created automatically), so your development data is safe.

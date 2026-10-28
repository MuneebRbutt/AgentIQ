# Deploying AgentIQ (FR-10-08)

This guide takes you from a copy of the repository to a live AgentIQ on the internet: the
dashboard, the API, the three demo businesses (hotel, restaurant and clinic), and optionally a
phone number customers can call or text and an address they can email. It is written for
someone who hasn't worked on AgentIQ. Allow about an hour, most of it waiting for builds.

The design behind it is in [ADR-0012](adr/0012-deployment-and-usability-study.md).

## What you need

| Account | For | Cost |
|---|---|---|
| [GitHub](https://github.com) | Render deploys from a repository | Free |
| [Render](https://render.com) | Runs the app, Postgres and Redis | About US$14 a month on the plans in `render.yaml` (see step 2) |
| A language-model API key (OpenAI, Groq, xAI...) | Natural replies. Without one the assistant still books and quotes documents, but less naturally | Pay per use; a demo costs cents |
| [Twilio](https://www.twilio.com) (optional) | Phone calls and text messages | Trial credit, then about US$1.15 a month per US number plus usage |
| A Gmail account (optional) | Customers' emails and email notices | Free |

You don't need Docker, Python or Node on your computer for the Render route. Step 1 uses
Docker only if you want to try the app locally first.

## 1. Try it on your computer (optional)

With [Docker Desktop](https://www.docker.com/products/docker-desktop/) running, from the
repository folder:

```bash
cp .env.example .env            # Windows PowerShell: Copy-Item .env.example .env
docker compose --profile app up -d --build
```

The first build takes 5 to 10 minutes. Then create the demo business (it asks for a password):

```bash
docker compose exec app python -m app.cli setup-demo --email demo@agentiq.test
```

Open http://localhost:8000, log in, and you should see the three demo profiles. Stop it all
with `docker compose --profile app down`. This is exactly what Render will run.

## 2. Deploy on Render

1. **Put the code on GitHub.** Push the repository to your GitHub account (a private repository
   is fine). `.env` is ignored by git, so your secrets stay on your computer.
2. **Create the Blueprint.** In the Render dashboard choose **New > Blueprint**, connect GitHub
   and pick the repository. Render reads `render.yaml` and shows three resources: the web service
   `agentiq`, the database `agentiq-db` and the key-value store `agentiq-redis`.
3. **Fill in the values Render asks for.** Leave anything you don't use empty:

   | Variable | What to enter |
   |---|---|
   | `PUBLIC_BASE_URL` | Leave empty for now; you'll know it after step 4 |
   | `DEMO_OPERATOR_EMAIL` | The login for the demo, e.g. `demo@yourteam.dev` |
   | `DEMO_OPERATOR_PASSWORD` | At least 8 characters. Keep it somewhere safe |
   | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | Your model provider's key, base URL and model (the provider's docs list them; OpenAI is `https://api.openai.com/v1` and `gpt-4o-mini`) |
   | `TWILIO_*`, `SMTP_*`, `IMAP_*` | Skip for now; steps 5 and 6 add them |

   Plans: the web service is **Starter** and the database **Basic** because a free web service
   falls asleep after 15 idle minutes, takes about a minute to wake (too slow for a phone call),
   and a free database is deleted after 30 days. To try it for free anyway, change both `plan:`
   lines in `render.yaml` to `free` before step 2.
4. **Apply.** Render builds the image (10 to 15 minutes the first time) and starts it. The
   service's **Logs** tab should end with lines like `Created profile 'Spice Garden'` and
   `Application startup complete`. Its address is at the top of the page, for example
   `https://agentiq-ab12.onrender.com`.
5. **Set the public address.** In the service's **Environment** tab set `PUBLIC_BASE_URL` to that
   address, without a trailing slash, and save. Render restarts the service.

### Check it works

- `https://<your address>/api/v1/health` shows `"status": "ok"` with database and Redis `ok`.
- `https://<your address>` shows the login page. Log in with the demo email and password.
- Open **Spice Garden > Test console**, type *Do you serve halal food?* and send. A reply means
  the assistant, database and search index all work.

## 3. Phone calls and text messages (optional)

1. In the Twilio console, **buy a number** with Voice and SMS capabilities (on a trial account,
   the free trial number works). On a trial account also add the phones you'll test from under
   **Verified Caller IDs**: a trial number only talks to verified numbers.
2. In Render's **Environment** tab set `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` (Twilio
   console home page), and `TWILIO_FROM_NUMBER` (the number, e.g. `+14155550123`) so
   confirmations and reminders go out by SMS.
3. In the dashboard open a profile, then **Calls, SMS & email**, and add the number.
4. The same page lists three URLs. In the Twilio console open the number
   (**Phone Numbers > Manage > Active numbers**) and paste them:
   - *Voice configuration > A call comes in*: Webhook, the first URL, HTTP POST.
   - *Call status changes*: the second URL.
   - *Messaging configuration > A message comes in*: Webhook, the third URL, HTTP POST.
5. Save, then call the number. You should hear the profile's greeting. Text it a question and
   you should get an answer.

A trial account plays a short Twilio notice before the greeting and adds one to text messages.

## 4. Email (optional)

Customers email a Gmail mailbox; each business gets its own plus address of it.

1. Use a Gmail account made for the demo. Turn on **2-Step Verification**, then create an **App
   password** (Google Account > Security > App passwords).
2. In Render's **Environment** tab set:

   | Variable | Value |
   |---|---|
   | `SMTP_HOST` / `SMTP_USERNAME` / `SMTP_PASSWORD` | `smtp.gmail.com` / the Gmail address / the app password |
   | `SMTP_FROM` | The Gmail address |
   | `IMAP_HOST` / `IMAP_USERNAME` / `IMAP_PASSWORD` | `imap.gmail.com` / the Gmail address / the app password |

3. In the dashboard, on each profile's **Calls, SMS & email** page, add a plus address, for
   example `agentiq.demo+clinic@gmail.com` for the clinic. The page suggests one.
4. Email that address from another account. The answer arrives within about a minute, in the
   same thread, from the business's name.

## Updating

Push to the repository's main branch; Render rebuilds and redeploys automatically. Database
changes apply themselves on start (`alembic upgrade head`), and the knowledge search index is
rebuilt from the stored documents, so nothing needs doing by hand.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Deploy fails with `JWT_SECRET_KEY must be set` | The Blueprint generates it; if you created the service by hand, add `JWT_SECRET_KEY` with 32 or more random characters |
| Health shows `database: unavailable` | The database is still being created, or `DATABASE_URL` isn't linked: check it in the Environment tab |
| Login says the email or password is wrong | `DEMO_*` are only used when the demo is first created. Changing them later doesn't change the password. Create another operator from the service's **Shell** tab: `python -m app.cli create-operator --tenant-id <id> --email you@x.dev --name You` (`list-tenants` shows the id) |
| A call says "application error" or nothing | `PUBLIC_BASE_URL` is empty or wrong (the logs show `twilio_signature_rejected`), the webhook URLs weren't saved in Twilio, or the service was asleep on the free plan |
| Calls work but texts get no answer | The Messaging webhook isn't set, the number has no SMS capability, or the sender isn't verified on a trial account |
| Emails get no answer | `IMAP_*` or `SMTP_*` missing (the Calls, SMS & email page says "Emails aren't read yet"), the app password is wrong (logs: `email_mailbox_unavailable`), or the address isn't added to a profile (logs: `email_not_routed`) |
| The service restarts with "out of memory" | Set `SENTIMENT_PROVIDER=lexicon` in the Environment tab, which skips the sentiment model, or move to a larger plan |

The **Logs** tab shows every request and background event as one JSON line, with phone numbers
and emails masked.

## For the person reproducing this guide

The M12 exit criterion is that someone outside the team follows this guide successfully. Please
note the date, how long each section took, and every place you got stuck or had to guess, and
send it to the team so the guide can be fixed.

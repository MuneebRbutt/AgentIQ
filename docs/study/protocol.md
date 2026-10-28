# Usability study protocol (FR-10-07, NFR02)

This study checks two claims from the report: a new operator can set up and run a business
profile **without developer help** (NFR02), and the system is **acceptable to use**, meaning a
mean System Usability Scale score of at least **68** (analysis AS-15). It also gives customers'
first impressions of the phone and text channels.

Run it on the deployed demo (docs/deployment.md), so participants use the same system the
report describes. Allow 45 minutes per participant.

## Participants

- **5 to 8 operators**: people who could plausibly run a small business's front desk
  (shop or clinic staff, students from outside computing). Nobody from the team, and nobody who
  has seen the dashboard before. Five participants find most usability problems; eight give a
  usable confidence interval for SUS.
- **Optional, 3 to 5 customers**: anyone with a phone, for tasks C1 and C2. They can be the
  same people, after their operator session.

## Ethics and data (analysis M-9, R-14)

- Get the university's ethics approval before the first session, if your department requires it.
- Each participant reads and signs the [consent form](consent-form.md) first.
- **Synthetic data only.** Participants act out the scenarios on the task cards; nobody gives
  real health information, real names or real card numbers. Remind clinic-task participants of
  this out loud.
- Record the screen only with consent. Store recordings and notes under participant codes
  (P01, P02, ...), never names, and delete them when the project is marked.
- Twilio keeps call recordings only when STT_PROVIDER=whisper. Delete them in the Twilio console
  after the study, or leave the default (Twilio's recogniser), which keeps no audio.

## Before each session

1. Nothing to reset: each participant names their profile with their code ("CareWell Clinic
   P03"), so sessions don't mix.
2. Have the session sheet ready (`eval/study/tasks-template.csv`, one row per task) and a timer.
3. Open the dashboard's login page on the participant's laptop. Give them the operator login on
   a card. Do not show them around.

## Script

Read this aloud:

> Thank you for helping. We're testing the software, not you, so nothing you do is a mistake.
> Please think aloud as you go: say what you're looking for and what you expect to happen.
> I can't help with the tasks, but if you're stuck for a few minutes, say so and we'll move on.

Then hand over one task card at a time. Start the timer when they finish reading it; stop it
when they say they're done or give up.

## Operator tasks

| Task | Card | Done when |
|---|---|---|
| T1 Log in | "Log in with the email and password on this card." | The profiles page shows |
| T2 Set up a profile | "You run CareWell Clinic. Create its profile from the clinic template, named CareWell Clinic and your code, and make it close at 8 pm instead of 9 pm on Fridays." | Profile saved, Friday evening closing 20:00 |
| T3 Teach it a document | "Upload the patient guide (`eval/documents/clinic/patient_guide.md`, copied to the desktop) so the assistant can answer questions about it. Then use the test console to ask: how long does a fasting blood test need?" | The console answers "10 hours" from the guide |
| T4 Book as a customer | "In the test console, book a general consultation for next Tuesday at 10 am as Ali Raza, 0300 1234567. Then find the booking in Bookings." | Booking listed |
| T5 Handle a complaint | "A customer complained in the test console (type: *I've waited an hour, this is terrible*). Find the escalation and mark it resolved." | Escalation resolved |
| T6 Review a conversation | "Open the conversation from T4 and say which agent made the booking and how long the reply took." | Correct agent and timing named |

Record for each: completed (yes/no), seconds, errors (wrong page or wrong action, counted once
each), assisted (yes if you had to help), and a note of anything they said.

## Customer tasks (optional)

Give them the demo number (Calls, SMS & email tab) and a card:

| Task | Card | Done when |
|---|---|---|
| C1 Call and book | "Call this number and book a table for two tomorrow at 7 pm in your name." | Reference read back |
| C2 Text a question | "Text the same number: do you have vegetarian dishes?" | A correct answer arrives |

## After the tasks

1. The participant fills in the [SUS questionnaire](sus-questionnaire.md) on paper or a form,
   straight away and without discussing it.
2. Five-minute interview: *What was easiest? What was hardest? What would stop you using this
   at work? Anything you expected that wasn't there?*

## Analysis

Type the answers into `eval/study/sus-responses.csv` (copy the template) and the task sheet into
`eval/study/tasks.csv`, then:

```bash
cd backend
python -m app.cli sus-score ../eval/study/sus-responses.csv --tasks ../eval/study/tasks.csv > ../eval/study/results.md
```

`results.md` has the mean SUS with its grade and 95% confidence interval, each participant's
score, and per-task completion, assistance, time and errors, ready for the report. Add the
interview themes by hand: group what participants said, and list each usability problem with
how many participants hit it and how serious it was.

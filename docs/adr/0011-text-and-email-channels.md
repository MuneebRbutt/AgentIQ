# ADR-0011: Text messages and email from customers

- **Status:** Accepted (proposed default; the team may revise by superseding this ADR)
- **Date:** 2026-10-21
- **Requirements:** FR-02-02 (SMS/text input), FR-02-03 (email input: subject and body), NFR03-02, NFR04-01; analysis Q-10, C-6, AS-5, AS-13, M-14, M-15

## Context

The Report lists text and email input beside calls (FR-02-02/03), while its scope section names
only voice and the test console (C-6). The analysis defaulted to "optional Twilio SMS, email
deferred" (Q-10, AS-13). With the orchestrator channel-neutral (ADR-0005) and Twilio already
routing calls by number (ADR-0010), both channels became small additions, so M12 builds them.

## Decisions

### 1. One path for both channels

`InboundHandler` (`app/channels/inbound.py`) takes a message once its business is known and:

1. records it in `inbound_messages` under the provider's id (Twilio's `MessageSid`, the email's
   `Message-ID`), unique per channel, so a retried webhook or a mailbox read twice is answered
   once;
2. refuses to answer a sender who wrote more than `INBOUND_MAX_MESSAGES_PER_HOUR` (20) times to
   that business in the last hour (M-15: a loop or prank would otherwise run up model and SMS
   costs);
3. continues the sender's open conversation on that channel unless it has been idle longer than
   `CONVERSATION_IDLE_MINUTES` (M-14), otherwise starts one;
4. calls `Orchestrator.handle` like every channel.

The business comes only from where the message was sent (number or address), never from its
content (NFR03-02).

### 2. Text messages: Twilio webhook, reply in TwiML

The number's "A message comes in" webhook (`POST /api/v1/sms/incoming`) is signature-checked like
the call webhooks (`app/channels/twilio.py`) and answers `<Message>` TwiML: one outgoing SMS and
no extra API call. The same `phone_numbers` row routes calls and messages. Opt-out and help
keywords (STOP, HELP...) get no reply, because Twilio handles them and blocks replies. A
media-only message is asked to type. Replies drop markdown and stay within 1,600 characters.

### 3. Email: one IMAP mailbox, plus addresses, SMTP replies

Options considered: a provider's inbound webhook (SendGrid Inbound Parse, Mailgun Routes,
Postmark) or reading a mailbox over IMAP. IMAP needs no public URL, domain or new account,
works with Gmail or a university mailbox through an app password, and runs on a laptop, so it
was chosen. The cost is polling (`EMAIL_POLL_SECONDS`, 30 s) instead of instant delivery,
acceptable for email.

- One mailbox serves every business. Each business's address is a row in `email_addresses`,
  typically a plus address of the mailbox (`agentiq.demo+clinic@gmail.com`), managed on the
  dashboard. A message routes by its `To`, `Cc`, `Delivered-To` and `X-Original-To`.
- `EmailPoller` runs in the API process (like the follow-up worker) when `IMAP_*` and `SMTP_*`
  are set. It reads unread messages without marking them, handles them, then marks them read,
  even when one fails, so a broken message never blocks the rest; `inbound_messages` makes a
  re-read harmless.
- Only the customer's new words reach the assistant: the subject (when it starts a conversation),
  then the body without quoted history or signature; HTML-only mail is reduced to text.
- Replies go from the business's address and name, in the customer's thread (`In-Reply-To`,
  `References`), marked `Auto-Submitted: auto-replied`. Automatic mail (out-of-office, bounces,
  lists, no-reply senders) and mail from AgentIQ's own addresses is never answered (RFC 3834),
  which prevents loops between robots.
- An email conversation stores the sender in `interactions.customer_email`; feedback requests
  for it go by email (FR-07-04).

## Consequences

- Text messages need a Twilio number with the SMS capability (shown next to each number in the
  Twilio console; not every country's numbers have it). Trial accounts only exchange messages
  with verified numbers and prefix a trial notice.
- Email is spoofable: the assistant answers whoever the `From` says, which is fine for questions
  and bookings (they still need a phone number, AS-5) but means email isn't an identity.
- Gmail limits sending to about 500 messages a day, ample for a demo and the usability study.
- Bookings by text or email work as in the test console: the assistant asks for a phone number
  when the channel doesn't give one.

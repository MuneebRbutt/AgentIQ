"""The shared mailbox customers' emails arrive in, read over IMAP (ADR-0011).

IMAP works with any provider (Gmail or Outlook with an app password, Zoho, a university
account) and needs no public URL, so the email channel also works on a laptop. `imaplib`
blocks, so the poller calls this from a worker thread (CLAUDE.md §4).
"""

import imaplib
from collections.abc import Callable
from typing import Any, Protocol

IMAP_TIMEOUT_SECONDS = 20.0
FETCH_LIMIT = 20  # per pass, so one pass never runs unbounded


class MailboxError(Exception):
    """The mailbox couldn't be read. Never includes the password or server responses."""


class Mailbox(Protocol):
    def fetch_unseen(self) -> list[tuple[str, bytes]]:
        """Unread messages as (uid, raw bytes), oldest first, without marking them read."""
        ...

    def mark_seen(self, uids: list[str]) -> None: ...


class ImapMailbox:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        folder: str = "INBOX",
        *,
        imap_class: Callable[..., Any] = imaplib.IMAP4_SSL,  # replaceable in tests
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._folder = folder
        self._imap_class = imap_class

    def fetch_unseen(self) -> list[tuple[str, bytes]]:
        messages: list[tuple[str, bytes]] = []
        with self._open() as imap:
            status, data = imap.uid("SEARCH", None, "UNSEEN")
            if status != "OK":
                raise MailboxError("search failed")
            for uid in data[0].split()[:FETCH_LIMIT]:
                status, parts = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if status == "OK" and isinstance(raw, bytes):
                    messages.append((uid.decode(), raw))
        return messages

    def mark_seen(self, uids: list[str]) -> None:
        if not uids:
            return
        with self._open() as imap:
            imap.uid("STORE", ",".join(uids), "+FLAGS", "(\\Seen)")

    def _open(self) -> Any:
        try:
            imap = self._imap_class(self._host, self._port, timeout=IMAP_TIMEOUT_SECONDS)
            imap.login(self._username, self._password)
            status, _ = imap.select(self._folder)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise MailboxError(f"can't open the mailbox ({type(exc).__name__})") from exc
        if status != "OK":
            imap.logout()
            raise MailboxError(f"no folder named {self._folder!r}")
        return imap

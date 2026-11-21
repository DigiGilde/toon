"""The email about new feedback: written as plain text, sent over SMTP.

An email carries metadata only: per item the site, the page, the kind, the
time, whether a screenshot came with it, and a link to the item in the admin
interface. Never the comment, never who sent it: an inbox is not the place
for what a visitor wrote, and the link asks for a session that may read it.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import format_datetime, make_msgid
from typing import Protocol

from toon.config import Settings
from toon.notifications import texts
from toon.notifications.content import Batch, Item, page_shown

# Seconds for the connection and for every reply of the server. A relay that
# hangs costs one attempt, not the job.
SMTP_TIMEOUT_S = 15


class Mailer(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


def _item_line(lang: str, batch: Batch, item: Item) -> str:
    where = (
        texts.t(lang, "where.preview", ref=item.preview_ref)
        if item.target == "preview"
        else texts.t(lang, "where.live")
    )
    line = texts.t(
        lang,
        "item",
        kind=texts.t(lang, f"kind.{item.kind}"),
        page=page_shown(item),
        where=where,
        time=texts.when(lang, item.created_at),
    )
    if item.has_screenshot:
        line += ", " + texts.t(lang, "screenshot")
    return line


def build_email(batch: Batch, *, lang: str, to: str, sender: str, now) -> EmailMessage:
    """One plain-text email to `to` about every item of `batch`. `sender` is
    TOON_SMTP_FROM; the relay on ZAD writes its own From over it."""
    lang = texts.language(lang)
    count = len(batch.items)
    message = EmailMessage(policy=SMTP_POLICY)
    message["Subject"] = texts.t(
        lang, "subject.one" if count == 1 else "subject.many", count=count, site=batch.site_label
    )
    message["From"] = sender
    message["To"] = to
    message["Date"] = format_datetime(now)
    message["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2])
    # RFC 3834: no out-of-office reply to this, please.
    message["Auto-Submitted"] = "auto-generated"
    # RFC 2369. The site's Feedback tab is where the narrowest choice is
    # made, so turning email off there always stops it for this site.
    message["List-Unsubscribe"] = f"<{batch.tab_url}>"

    lines = [texts.t(lang, "intro.one" if count == 1 else "intro.many", count=count, site=batch.site_label), ""]
    for number, item in enumerate(batch.listed, start=1):
        lines += [f"{number}. {_item_line(lang, batch, item)}", f"   {batch.item_url(item)}", ""]
    if batch.unlisted:
        lines += [texts.t(lang, "more", count=batch.unlisted), batch.tab_url, ""]
    lines += [
        "-- ",
        texts.t(lang, "why"),
        batch.tab_url,
        texts.t(lang, "why.profile"),
        batch.profile_url,
    ]
    message.set_content("\n".join(lines) + "\n", charset="utf-8")
    # After set_content, which clears every Content- header before it.
    message["Content-Language"] = lang
    return message


class SmtpMailer:
    """Sends over SMTP from TOON_SMTP_*. With `starttls` a server that does
    not offer STARTTLS, or whose certificate does not verify, is an error:
    smtplib raises before anything is said in the clear, and nothing here
    tries again without it."""

    def __init__(self, settings: Settings) -> None:
        self._host = settings.smtp_host
        self._port = settings.smtp_port_effective
        self._starttls = settings.smtp_tls == "starttls"
        self._username = settings.smtp_username
        self._password = settings.smtp_password

    def __repr__(self) -> str:
        return f"SmtpMailer({self._host}:{self._port}, starttls={self._starttls})"

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send, message)

    def _send(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=SMTP_TIMEOUT_S) as smtp:
            if self._starttls:
                smtp.starttls(context=ssl.create_default_context())
            if self._username:
                smtp.login(self._username, self._password)
            smtp.send_message(message)

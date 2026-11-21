"""The notification job: every minute, new feedback into batches, then send
what is due.

**Batching.** Per channel, every item that has not gone into a batch yet
(`email_batched_at` or `mattermost_batched_at` is NULL) waits for its site's
window: the oldest waiting item of a site starts it, and once it has passed,
one batch takes every waiting item of that site. A window of 0 makes a batch
of each item. Taking items in sets the column and writes the notices in one
transaction, under `FOR NO KEY UPDATE SKIP LOCKED` on the items, so two
replicas never take the same item and a restart loses nothing. Every item is
taken, also on a site without a recipient or a webhook: turning a channel on
later never sends the feedback of before.

**Sending.** A notice is one message to one destination. The job locks one
due notice at a time with `FOR UPDATE SKIP LOCKED`, checks again whether it
still has anything to send and anyone to send it to, sends, and records the
outcome in the same transaction. A failure counts an attempt and tries again
after 2, 4, 8 and 16 minutes; the fifth failure ends the notice and logs a
warning. Each notice fails on its own, so a dead webhook holds up neither
another site nor the email. A process that dies between the send and the
commit sends that one message again after the restart; nothing else is sent
twice.

**The daily cap.** Before an email goes out, the day's counter in
`feedback_email_days` goes up by one, in the database, so every replica
counts against the same TOON_FEEDBACK_EMAIL_DAILY_MAX. When the cap is
reached, every waiting email moves to the next UTC day, one warning says so
for the whole installation, and Mattermost goes on.

`run_once` is the core and can be called on its own by tests; main.py starts
the loop through `notification_job()`, an async context manager, inside its
lifespan.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import smtplib
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import Enum

import httpx
from sqlalchemy import delete, exists, literal, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from toon.config import Settings
from toon.models.feedback import FeedbackItem, FeedbackScreenshot
from toon.models.identity import Group
from toon.models.notifications import (
    FeedbackChannel,
    FeedbackNotice,
    FeedbackNoticeItem,
    FeedbackWebhook,
    NoticeOutcome,
)
from toon.models.publication import Site
from toon.notifications import mattermost, recipients
from toon.notifications.content import Batch, Item
from toon.notifications.mail import Mailer, build_email
from toon.notifications.webhooks import WebhookDeliveryError, WebhookUrlError, check_url, post

INTERVAL_S = 60
# On shutdown the job may finish the message it is sending for this long
# before it is cancelled, so a restart repeats as little as possible.
SHUTDOWN_GRACE_S = 10
MAX_ATTEMPTS = 5
# What one run takes in and sends at most; the rest waits for the next run.
SCAN_LIMIT = 5000
SENDS_PER_RUN = 500
# A finished notice stays this long, so an operator can still see what went
# out, then goes.
FINISHED_KEPT = timedelta(days=7)

_logger = logging.getLogger(__name__)


class _Result(Enum):
    DONE = "done"
    CAPPED = "capped"


@dataclass(frozen=True)
class _Pending:
    id: uuid.UUID
    site_id: uuid.UUID
    created_at: datetime
    author_sub: str | None


def _batched_column(channel: FeedbackChannel):
    return FeedbackItem.email_batched_at if channel is FeedbackChannel.EMAIL else FeedbackItem.mattermost_batched_at


def _window(settings: Settings, channel: FeedbackChannel) -> timedelta:
    minutes = (
        settings.feedback_email_window_minutes
        if channel is FeedbackChannel.EMAIL
        else settings.feedback_mattermost_window_minutes
    )
    return timedelta(minutes=minutes)


async def webhook_for(db: AsyncSession, site: Site) -> FeedbackWebhook | None:
    """The site's own webhook, else its group's, else none."""
    found = list(
        await db.scalars(
            select(FeedbackWebhook).where(
                or_(FeedbackWebhook.site_id == site.id, FeedbackWebhook.group_id == site.group_id)
            )
        )
    )
    return next((hook for hook in found if hook.site_id == site.id), None) or next(iter(found), None)


async def take_into_batches(
    factory: async_sessionmaker[AsyncSession], settings: Settings, channel: FeedbackChannel, now: datetime
) -> int:
    """Takes every waiting item whose site's window has passed into a batch
    and writes its notices. Returns the number of items taken."""
    column = _batched_column(channel)
    window = _window(settings, channel)
    async with factory() as db:
        rows = (
            await db.execute(
                select(FeedbackItem.id, FeedbackItem.site_id, FeedbackItem.created_at, FeedbackItem.author_sub)
                .where(column.is_(None))
                .order_by(FeedbackItem.site_id, FeedbackItem.created_at, FeedbackItem.id)
                .limit(SCAN_LIMIT)
                .with_for_update(skip_locked=True, key_share=True)
            )
        ).all()
        per_site: dict[uuid.UUID, list[_Pending]] = defaultdict(list)
        for row in rows:
            per_site[row.site_id].append(_Pending(row.id, row.site_id, row.created_at, row.author_sub))
        taken = 0
        for site_id, items in per_site.items():
            if items[0].created_at + window > now:
                continue
            site = await db.get(Site, site_id)
            if site is None:  # pragma: no cover - the locked items hold their site in place
                continue
            taken_ids = [item.id for item in items]
            await db.execute(update(FeedbackItem).where(FeedbackItem.id.in_(taken_ids)).values({column: now}))
            batches = [[item] for item in items] if not window else [items]
            await _write_notices(db, settings, channel, site, batches, now)
            taken += len(items)
        await db.commit()
    return taken


async def _write_notices(
    db: AsyncSession,
    settings: Settings,
    channel: FeedbackChannel,
    site: Site,
    batches: list[list[_Pending]],
    now: datetime,
) -> None:
    if channel is FeedbackChannel.EMAIL:
        if not settings.email_available:
            return
        people = await recipients.recipients(db, site)
        for batch in batches:
            for person in people:
                own = [item for item in batch if item.author_sub != person.sso_subject]
                if own:
                    _add_notice(db, site, channel, own, now, member_id=person.member_id)
        return
    if settings.webhook_hosts and await webhook_for(db, site) is not None:
        for batch in batches:
            _add_notice(db, site, channel, batch, now)


def _add_notice(
    db: AsyncSession,
    site: Site,
    channel: FeedbackChannel,
    items: list[_Pending],
    now: datetime,
    *,
    member_id: uuid.UUID | None = None,
) -> None:
    notice_id = uuid.uuid4()
    db.add(FeedbackNotice(id=notice_id, site_id=site.id, channel=channel, member_id=member_id, next_attempt_at=now))
    db.add_all(FeedbackNoticeItem(notice_id=notice_id, item_id=item.id) for item in items)


async def _items(db: AsyncSession, notice: FeedbackNotice, *, leave_out_sub: str | None = None) -> tuple[Item, ...]:
    """The items of `notice` that still exist, oldest first. An email never
    loads the comment."""
    with_comment = notice.channel is FeedbackChannel.MATTERMOST
    statement = (
        select(
            FeedbackItem.id,
            FeedbackItem.kind,
            FeedbackItem.target,
            FeedbackItem.preview_ref,
            FeedbackItem.page_path,
            FeedbackItem.created_at,
            exists().where(FeedbackScreenshot.item_id == FeedbackItem.id).label("has_screenshot"),
            (FeedbackItem.comment if with_comment else literal("")).label("comment"),
        )
        .join(FeedbackNoticeItem, FeedbackNoticeItem.item_id == FeedbackItem.id)
        .where(FeedbackNoticeItem.notice_id == notice.id)
        .order_by(FeedbackItem.created_at, FeedbackItem.id)
    )
    if leave_out_sub is not None:
        statement = statement.where(or_(FeedbackItem.author_sub.is_(None), FeedbackItem.author_sub != leave_out_sub))
    return tuple(
        Item(
            id=row.id,
            kind=str(row.kind),
            target=str(row.target),
            preview_ref=row.preview_ref,
            page_path=row.page_path,
            created_at=row.created_at,
            has_screenshot=row.has_screenshot,
            comment=row.comment,
        )
        for row in await db.execute(statement)
    )


def _finish(notice: FeedbackNotice, outcome: NoticeOutcome, now: datetime) -> None:
    notice.finished_at = now
    notice.outcome = outcome


def _reason(error: BaseException) -> str:
    """What a log line may say about a failure: the class, and an SMTP
    reply code. Never the message, which can hold an address or a URL."""
    if isinstance(error, smtplib.SMTPResponseException):
        return f"{type(error).__name__} {error.smtp_code}"
    return str(error) if isinstance(error, WebhookDeliveryError) else type(error).__name__


def _failed(notice: FeedbackNotice, where: str, error: BaseException, now: datetime) -> None:
    notice.attempts += 1
    reason = _reason(error)
    if notice.attempts >= MAX_ATTEMPTS:
        _finish(notice, NoticeOutcome.FAILED, now)
        _logger.warning(
            "Feedback %s notice %s for %s failed %d times, giving up (%s)",
            notice.channel,
            notice.id,
            where,
            notice.attempts,
            reason,
        )
        return
    notice.next_attempt_at = now + timedelta(minutes=2**notice.attempts)
    _logger.info("Feedback %s notice %s for %s failed, trying again (%s)", notice.channel, notice.id, where, reason)


def _next_day(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(UTC).date() + timedelta(days=1), time(0, 0), tzinfo=UTC)


async def _reserve_email(db: AsyncSession, settings: Settings, now: datetime) -> bool:
    """Counts one email against today's cap; False when the cap is reached."""
    reserved = await db.scalar(
        text(
            "INSERT INTO feedback_email_days (day, sent) VALUES (:day, 1) "
            "ON CONFLICT (day) DO UPDATE SET sent = feedback_email_days.sent + 1 "
            "WHERE feedback_email_days.sent < :cap RETURNING sent"
        ),
        {"day": now.astimezone(UTC).date(), "cap": settings.feedback_email_daily_max},
    )
    return reserved is not None


async def _hold_email_until_tomorrow(db: AsyncSession, settings: Settings, now: datetime) -> None:
    tomorrow = _next_day(now)
    await db.execute(
        update(FeedbackNotice)
        .where(
            FeedbackNotice.channel == FeedbackChannel.EMAIL,
            FeedbackNotice.finished_at.is_(None),
            FeedbackNotice.next_attempt_at < tomorrow,
        )
        .values(next_attempt_at=tomorrow)
    )
    first = await db.scalar(
        text(
            "UPDATE feedback_email_days SET cap_reached_at = :now "
            "WHERE day = :day AND cap_reached_at IS NULL RETURNING day"
        ),
        {"now": now, "day": now.astimezone(UTC).date()},
    )
    if first is not None:
        _logger.warning(
            "Feedback email reached its cap of %d for %s (TOON_FEEDBACK_EMAIL_DAILY_MAX): "
            "no more email until the next day, Mattermost goes on",
            settings.feedback_email_daily_max,
            first.isoformat(),
        )


async def _deliver_email(
    db: AsyncSession,
    settings: Settings,
    notice: FeedbackNotice,
    site: Site,
    group: Group,
    mailer: Mailer | None,
    now: datetime,
) -> _Result:
    assert notice.member_id is not None  # noqa: S101 - ck_feedback_notices_recipient
    person = await recipients.recipient(db, site, notice.member_id) if mailer is not None else None
    if person is None or mailer is None:
        _finish(notice, NoticeOutcome.DROPPED, now)
        return _Result.DONE
    items = await _items(db, notice, leave_out_sub=person.sso_subject)
    if not items:
        _finish(notice, NoticeOutcome.DROPPED, now)
        return _Result.DONE
    if not await _reserve_email(db, settings, now):
        await _hold_email_until_tomorrow(db, settings, now)
        return _Result.CAPPED
    batch = Batch(admin_base=_admin_base(settings), group=group.slug, site=site.slug, items=items)
    try:
        message = build_email(
            batch, lang=person.language or "", to=person.email, sender=settings.smtp_from, now=now
        )
        await mailer.send(message)
    except Exception as error:  # any failure is one failed attempt
        _failed(notice, f"{group.slug}/{site.slug}", error, now)
        return _Result.DONE
    _finish(notice, NoticeOutcome.SENT, now)
    return _Result.DONE


async def _deliver_post(
    db: AsyncSession,
    settings: Settings,
    notice: FeedbackNotice,
    site: Site,
    group: Group,
    http: httpx.AsyncClient,
    now: datetime,
) -> None:
    where = f"{group.slug}/{site.slug}"
    hook = await webhook_for(db, site)
    if hook is None:
        _finish(notice, NoticeOutcome.DROPPED, now)
        return
    try:
        check_url(hook.url, settings.webhook_hosts)
    except WebhookUrlError as refusal:
        _finish(notice, NoticeOutcome.DROPPED, now)
        _logger.warning(
            "Feedback webhook of %s on host %s is no longer allowed (%s), notice %s dropped",
            where,
            hook.host,
            refusal.key,
            notice.id,
        )
        return
    items = await _items(db, notice)
    if not items:
        _finish(notice, NoticeOutcome.DROPPED, now)
        return
    batch = Batch(admin_base=_admin_base(settings), group=group.slug, site=site.slug, items=items)
    try:
        await post(http, hook.url, mattermost.build_post(batch, str(hook.language)))
    except WebhookDeliveryError as error:
        _failed(notice, where, error, now)
        return
    _finish(notice, NoticeOutcome.SENT, now)


def _admin_base(settings: Settings) -> str:
    return (settings.base_url or "").rstrip("/")


async def deliver_due(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    http: httpx.AsyncClient,
    mailer: Mailer | None,
    now: datetime,
    stop: asyncio.Event | None = None,
) -> int:
    """Sends due notices, one transaction each, until none is due or
    SENDS_PER_RUN have been handled. Returns how many were handled."""
    email_held = False
    handled = 0
    while handled < SENDS_PER_RUN and not (stop is not None and stop.is_set()):
        async with factory() as db:
            statement = select(FeedbackNotice).where(
                FeedbackNotice.finished_at.is_(None), FeedbackNotice.next_attempt_at <= now
            )
            if email_held:
                statement = statement.where(FeedbackNotice.channel != FeedbackChannel.EMAIL)
            notice = await db.scalar(
                statement.order_by(FeedbackNotice.next_attempt_at, FeedbackNotice.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if notice is None:
                return handled
            site = await db.get(Site, notice.site_id)
            assert site is not None  # noqa: S101 - the locked notice holds its site in place
            group = await db.get(Group, site.group_id)
            assert group is not None  # noqa: S101 - a site always has its group
            if notice.channel is FeedbackChannel.EMAIL:
                email_held = await _deliver_email(db, settings, notice, site, group, mailer, now) is _Result.CAPPED
            else:
                await _deliver_post(db, settings, notice, site, group, http, now)
            await db.commit()
        handled += 1
    return handled


async def forget_finished(factory: async_sessionmaker[AsyncSession], now: datetime) -> None:
    async with factory() as db:
        await db.execute(delete(FeedbackNotice).where(FeedbackNotice.finished_at < now - FINISHED_KEPT))
        await db.execute(
            text("DELETE FROM feedback_email_days WHERE day < :day"),
            {"day": (now - FINISHED_KEPT).astimezone(UTC).date()},
        )
        await db.commit()


async def run_once(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    http: httpx.AsyncClient,
    mailer: Mailer | None,
    now: datetime | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    now = now or datetime.now(tz=UTC)
    await forget_finished(factory, now)
    for channel in FeedbackChannel:
        await take_into_batches(factory, settings, channel, now)
    await deliver_due(factory, settings, http=http, mailer=mailer, now=now, stop=stop)


async def _run_every(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    http: httpx.AsyncClient,
    mailer: Mailer | None,
    interval: float,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            break
        try:
            await run_once(factory, settings, http=http, mailer=mailer, stop=stop)
        except Exception:
            # A database hiccup must not end the loop; the next run picks up
            # where this one stopped.
            _logger.exception("Feedback notifications failed, trying again in a minute")


@asynccontextmanager
async def notification_job(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    http: httpx.AsyncClient,
    mailer: Mailer | None,
    interval: float = INTERVAL_S,
    grace: float = SHUTDOWN_GRACE_S,
) -> AsyncGenerator[asyncio.Task]:
    """Background task that runs `run_once` every `interval` seconds.

    Meant to be opened inside the lifespan of main.py. On the way out it
    lets a message that is on its way finish for up to `grace` seconds."""
    stop = asyncio.Event()
    task = asyncio.create_task(
        _run_every(factory, settings, http=http, mailer=mailer, interval=interval, stop=stop)
    )
    try:
        yield task
    finally:
        stop.set()
        await asyncio.wait({task}, timeout=grace)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

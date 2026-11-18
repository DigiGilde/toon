"""The feedback widget API on the content host, under `/-/feedback/v1/`.

The widget calls it from the site's own page, whose origin may be opaque (a
shielded site). So every answer carries `Access-Control-Allow-Origin: *` and
the rest of the widget header set, success and problem alike, and never a
reflected Origin or credentials. That is safe because nothing here reads a
cookie: a request authenticates with the page token in `Authorization:
Toon-Feedback <token>`, a scheme the bearer guard leaves alone, and
serving/feedback_access.py checks the token again on every call.

Refusals are problem+json in the language the widget asks for, built in one
place (`widget_problem`), never through ApiError, whose handler would leave
the header set off. Every refusal of the token, an item that is not the
visitor's own included, is one and the same FEEDBACK_UNAVAILABLE. A failure
nobody foresaw still answers with the header set, so the widget can tell it
from a network error.

An item and a reply are checked in serving/feedback_input.py and their
bodies read in serving/feedback_body.py. The open-item cap and the screenshot
quotas are checked inside the create transaction under an advisory lock on
the site, and one for all screenshots when the item has one, and the reply
caps under one on the item, so concurrent writes cannot overshoot them. The
lock for all screenshots holds until the commit, so one create at a time
writes a screenshot: the bodies in flight hold at most the budget, the slack
their parts reserve as they grow, and the database driver's copies of that
one screenshot. A refusal by the platform-wide screenshot cap, unlike one by
a site's, is logged as a warning, once per interval. Two counters in memory
limit the writes, one per author and one per site. An author's counts every
attempt that passes authentication, before its body is read. A site's
reserves a write at that point too, with nothing awaited since the check, and
gets it back unless the write is stored, so concurrent writes cannot take it
past its budget and refused attempts cannot spend it.

No audit rows: giving feedback is like looking at public content.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import math
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy import Select, delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, Response

from toon import messages
from toon.api.errors import PROBLEM_CONTENT_TYPE, locale_from_header, problem_response
from toon.api.schema import iso_utc
from toon.auth.sessions import Session, site_prefix
from toon.config import Settings
from toon.constants import (
    FEEDBACK_BODY_ALLOWANCE,
    FEEDBACK_MAX_GIVER_REPLIES,
    FEEDBACK_MAX_REPLIES,
    FEEDBACK_SITE_MAX_OPEN,
    FEEDBACK_SITE_SCREENSHOT_MAX_BYTES,
    FEEDBACK_TOTAL_SCREENSHOT_MAX_BYTES,
    FEEDBACK_WRITES_PER_ADDRESS,
    FEEDBACK_WRITES_PER_AUTHOR,
    FEEDBACK_WRITES_PER_SITE,
    FEEDBACK_WRITES_WINDOW_S,
    FeedbackIdentity,
    FeedbackKind,
    FeedbackReplyAuthor,
    FeedbackStatus,
    feedback_item_lock_key,
    identity_allows,
)
from toon.messages import Msg
from toon.models.feedback import FeedbackItem, FeedbackReply, FeedbackScreenshot
from toon.models.identity import Member
from toon.models.publication import Preview, Site, Version, VersionTarget
from toon.ratelimit import FEEDBACK_API_HEADERS, InMemoryCounter
from toon.serving.feedback_access import FeedbackContext, verify_page_token
from toon.serving.feedback_body import Screenshot, bad_body, body_room, client_key, create_parts, reply_bytes, too_large
from toon.serving.feedback_images import acceptable_screenshot
from toon.serving.feedback_input import CSS, QUOTE, FeedbackInput, ProblemError, bad_field, parse_item, reply_text

_logger = logging.getLogger(__name__)

MAX_REPLY_BODY_BYTES = 64 * 1024

MAX_MINE = 20
# How many of an item's newest replies the visitor's own items carry.
# replyCount says how many there are in all.
MAX_MINE_REPLIES = 10
# feedback_items.author_name holds at most this. A name from the IdP is cut.
_MAX_STORED_NAME = 200

PREFLIGHT_HEADERS: dict[str, str] = {
    **FEEDBACK_API_HEADERS,
    "Access-Control-Allow-Methods": "GET, POST, DELETE",
    # A wildcard would not cover Authorization, so the list is spelled out.
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept-Language",
    "Access-Control-Max-Age": "600",
}

_AUTH_SCHEME = "toon-feedback"

_OPEN_STATUSES = (FeedbackStatus.NEW, FeedbackStatus.SEEN)


# --- Answers ------------------------------------------------------------------


def widget_problem(request: Request, status: int, message: Msg, *, retry_after: int | None = None) -> JSONResponse:
    """Every problem the widget API answers with: problem+json in the language
    the widget asks for, carrying the whole widget header set."""
    locale = locale_from_header(request.headers.get("accept-language"))
    headers = {**FEEDBACK_API_HEADERS, "Content-Language": locale, "Vary": "Accept-Language"}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return problem_response(
        status, messages.render(locale, message), locale=locale, code=messages.code_of(message.key), headers=headers
    )


def _failure(request: Request, status: int = 500) -> JSONResponse:
    locale = locale_from_header(request.headers.get("accept-language"))
    return JSONResponse(
        {"type": "about:blank", "title": messages.title(locale, status), "status": status},
        status_code=status,
        media_type=PROBLEM_CONTENT_TYPE,
        headers={**FEEDBACK_API_HEADERS, "Content-Language": locale, "Vary": "Accept-Language"},
    )


def _unavailable() -> ProblemError:
    return ProblemError(404, Msg("FEEDBACK_UNAVAILABLE"))


type Handler = Callable[..., Awaitable[Response]]


def _widget_api(handler: Handler) -> Handler:
    @functools.wraps(handler)
    async def answer(request: Request, *args: str) -> Response:
        try:
            return await handler(request, *args)
        except ProblemError as problem:
            return widget_problem(request, problem.status, problem.message, retry_after=problem.retry_after)
        except ClientDisconnect:
            # An upload the client gave up on is no failure of Toon's: one
            # line, not a traceback for every aborted body.
            _logger.info("The client went away during %s %s", request.method, request.url.path)
            return _failure(request, 400)
        except Exception:
            _logger.exception("The feedback widget API failed on %s %s", request.method, request.url.path)
            return _failure(request)

    return answer


def preflight() -> Response:
    """The CORS preflight of every widget API path. It authenticates nothing:
    what it allows still needs the page token."""
    return Response(status_code=204, headers=dict(PREFLIGHT_HEADERS))


# --- Who asks --------------------------------------------------------------------


def presented_token(request: Request) -> str | None:
    """The page token of `Authorization: Toon-Feedback <token>`."""
    scheme, _, value = request.headers.get("authorization", "").strip().partition(" ")
    if scheme.lower() != _AUTH_SCHEME:
        return None
    return value.strip() or None


async def _context(request: Request) -> FeedbackContext:
    context = await verify_page_token(request, presented_token(request))
    if context is None:
        raise _unavailable()
    return context


def _account(context: FeedbackContext) -> Session:
    if context.session is None:
        raise ProblemError(403, Msg("FEEDBACK_ACCOUNT_REQUIRED"))
    return context.session


def _author_columns(item: FeedbackInput, context: FeedbackContext) -> dict[str, str | None]:
    if not identity_allows(context.feedback.identity, item.author):
        raise ProblemError(422, Msg("FEEDBACK_AUTHOR_NOT_ALLOWED"))
    if item.author is FeedbackIdentity.ACCOUNT:
        session = _account(context)
        return {
            "author_sub": session.sub,
            "author_name": session.name[:_MAX_STORED_NAME] if session.name else None,
            "author_email": session.email.lower() if session.email and session.email_verified else None,
        }
    return {"author_sub": None, "author_name": item.author_name, "author_email": None}


# --- The write counters -----------------------------------------------------------

# A clock of its own, so a test can move it without moving asyncio's.
_clock = time.monotonic


async def _clean_every(counters: tuple[InMemoryCounter, ...], interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        for counter in counters:
            counter.cleanup(_clock())


@asynccontextmanager
async def write_counter_sweep(*counters: InMemoryCounter) -> AsyncIterator[asyncio.Task]:
    """Background task that drops the ended windows of the write counters once
    per window, for the lifespan of main.py: without writes that clean them,
    the key of the last giver would stay."""
    task = asyncio.create_task(_clean_every(counters, FEEDBACK_WRITES_WINDOW_S))
    try:
        yield task
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def _author_budget(request: Request, context: FeedbackContext) -> tuple[str, int]:
    """Whom the author counter counts as the author, and how many attempts
    it allows. Every key is one on this site: the site's own scripts hold its
    page token, so a count across sites would let one site spend a visitor's
    budget on every other. A consented session by its sub, and a session for
    the gate alone by its sub too, each with the budget of one person on one
    site. The gate's own namespace keeps its count apart from the consented
    one: a site that sees the person's name would otherwise read how many
    writes without a name that person made. Anyone else by their address,
    with the budget of a network that many people can share."""
    site = f"site:{context.site_id}"
    if context.session is not None:
        return f"{site}:session:{context.session.sub}", FEEDBACK_WRITES_PER_AUTHOR
    if context.gate_sub is not None:
        return f"{site}:gate:{context.gate_sub}", FEEDBACK_WRITES_PER_AUTHOR
    return f"{site}:{client_key(request)}", FEEDBACK_WRITES_PER_ADDRESS


def _too_many_writes(remaining_s: float) -> ProblemError:
    return ProblemError(429, Msg("TOO_MANY_REQUESTS.feedback"), retry_after=max(1, math.ceil(remaining_s)))


@contextmanager
def _admitted_write(request: Request, context: FeedbackContext) -> Iterator[None]:
    """Counts a write attempt on the author's budget, then reserves one write
    on the site's: 429 when either is spent. The site gets its write back
    unless the block ends without an exception, which only a stored write
    does."""
    now_ = _clock()
    author, author_limit = _author_budget(request, context)
    attempt = request.app.state.feedback_author_writes.add(author, FEEDBACK_WRITES_WINDOW_S, now_)
    if attempt.count > author_limit:
        raise _too_many_writes(attempt.remaining_s)
    sites: InMemoryCounter = request.app.state.feedback_site_writes
    site = f"site:{context.site_id}"
    # Before the first await: concurrent writes then cannot all pass the check.
    reserved = sites.add(site, FEEDBACK_WRITES_WINDOW_S, now_)
    stored = False
    try:
        if reserved.count > FEEDBACK_WRITES_PER_SITE:
            raise _too_many_writes(reserved.remaining_s)
        yield
        stored = True
    finally:
        if not stored:
            sites.refund(site, now_)


# --- The routes -------------------------------------------------------------------


@_widget_api
async def create_item(request: Request) -> Response:
    """POST /-/feedback/v1/items: one new item, 201 with its id."""
    context = await _context(request)
    # A site that takes feedback with an account only refuses every create
    # without a session: before its body costs anything.
    if context.feedback.identity is FeedbackIdentity.ACCOUNT:
        _account(context)
    settings: Settings = request.app.state.settings
    screenshot_limit = settings.feedback_screenshot_max_bytes
    with (
        _admitted_write(request, context),
        body_room(request, screenshot_limit + FEEDBACK_BODY_ALLOWANCE, too_large) as room,
    ):
        item_bytes, screenshot_bytes = await create_parts(request, room, screenshot_limit)
        try:
            text = item_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise bad_field("item") from None
        item = parse_item(text, context.root, site_prefix(context.group_slug, context.site_slug))
        author = _author_columns(item, context)
        screenshot = None
        if screenshot_bytes is not None:
            info = acceptable_screenshot(screenshot_bytes)
            if info is None:
                raise ProblemError(422, Msg("FEEDBACK_SCREENSHOT_INVALID"))
            screenshot = Screenshot(screenshot_bytes, info)
        created = await _store(request, context, item, author, screenshot)
    return JSONResponse(
        {"id": str(created.id), "status": created.status.value, "createdAt": iso_utc(created.created_at)},
        status_code=201,
        headers=dict(FEEDBACK_API_HEADERS),
    )


async def _store(
    request: Request,
    context: FeedbackContext,
    item: FeedbackInput,
    author: dict[str, str | None],
    screenshot: Screenshot | None,
) -> Any:
    token = context.token
    site_id = context.site_id
    # Through the database, never as a bare value: a version a redeploy or the
    # cleanup removed after the page view gives NULL, not a foreign key error.
    version = (
        None
        if token.version_id is None
        else select(Version.id)
        .where(Version.id == token.version_id, Version.site_id == site_id)
        .with_for_update(read=True, key_share=True)
        .scalar_subquery()
    )
    async with request.app.state.session_factory() as db:
        await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"toon-feedback-site:{site_id}"))))
        # FOR KEY SHARE, held until the commit: the site cannot be deleted
        # under the insert, and nothing else about it has to wait.
        site = select(Site.id).where(Site.id == site_id).with_for_update(read=True, key_share=True)
        if await db.scalar(site) is None:
            raise _unavailable()
        open_items = await db.scalar(
            select(func.count())
            .select_from(FeedbackItem)
            .where(FeedbackItem.site_id == site_id, FeedbackItem.status.in_(_OPEN_STATUSES))
        )
        if open_items >= FEEDBACK_SITE_MAX_OPEN:
            raise ProblemError(409, Msg("FEEDBACK_SITE_FULL"))
        if screenshot is not None:
            await _check_quota(db, site_id, len(screenshot.data))
        created = (
            await db.execute(
                insert(FeedbackItem)
                .values(
                    id=uuid.uuid4(),
                    site_id=site_id,
                    target=VersionTarget.LIVE if token.ref is None else VersionTarget.PREVIEW,
                    preview_ref=token.ref,
                    version_id=version,
                    page_path=item.page_path,
                    page_title=item.page_title,
                    kind=item.kind,
                    anchor=item.anchor,
                    quote=item.quote,
                    comment=item.comment,
                    author_kind=item.author,
                    viewport=item.viewport,
                    **author,
                )
                .returning(FeedbackItem.id, FeedbackItem.status, FeedbackItem.created_at)
            )
        ).one()
        if screenshot is not None:
            await db.execute(
                insert(FeedbackScreenshot).values(
                    item_id=created.id,
                    media_type=screenshot.info.media_type,
                    data=screenshot.data,
                    size_bytes=len(screenshot.data),
                    width=screenshot.info.width,
                    height=screenshot.info.height,
                )
            )
        await db.commit()
    return created


# A platform-wide cap that is reached stays reached until screenshots go:
# one warning per interval, not one for every create it refuses.
QUOTA_WARNING_INTERVAL_S = 600
_quota_warnings = InMemoryCounter()


async def _check_quota(db: AsyncSession, site_id: uuid.UUID, size: int) -> None:
    # After the site's lock, by every create with a screenshot: two sites
    # cannot pass the platform total together either.
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext("toon-feedback-screenshots"))))
    stored = func.coalesce(func.sum(FeedbackScreenshot.size_bytes), 0)
    site_bytes = await db.scalar(
        select(stored)
        .select_from(FeedbackScreenshot)
        .join(FeedbackItem, FeedbackItem.id == FeedbackScreenshot.item_id)
        .where(FeedbackItem.site_id == site_id)
    )
    total_bytes = await db.scalar(select(stored).select_from(FeedbackScreenshot))
    cap = FEEDBACK_TOTAL_SCREENSHOT_MAX_BYTES
    platform_full = total_bytes + size > cap
    # A site's own cap is the business of its team. This one stops every
    # site, so the operator hears of it, in byte counts only.
    if platform_full and _quota_warnings.add("platform", QUOTA_WARNING_INTERVAL_S, _clock()).count == 1:
        _logger.warning(
            "The platform-wide cap on feedback screenshots is reached: %d of %d bytes are stored, and a "
            "screenshot of %d bytes was refused. Every site refuses a screenshot that does not fit until "
            "space is freed. Logged at most once per %d seconds.",
            total_bytes,
            cap,
            size,
            QUOTA_WARNING_INTERVAL_S,
        )
    if platform_full or site_bytes + size > FEEDBACK_SITE_SCREENSHOT_MAX_BYTES:
        raise ProblemError(409, Msg("FEEDBACK_SCREENSHOT_QUOTA"))


@_widget_api
async def my_items(request: Request) -> Response:
    """GET /-/feedback/v1/items: the visitor's newest own items on this site,
    the ones sent with their account, newest first. Each carries its newest
    replies, oldest first, and how many it has in all. `more` says whether
    older items were left out."""
    context = await _context(request)
    session = _account(context)
    async with request.app.state.session_factory() as db:
        rows = (await db.execute(_mine_rows(context, session))).all()
        items = rows[:MAX_MINE]
        numbered = (
            select(
                FeedbackReply,
                func.row_number()
                .over(
                    partition_by=FeedbackReply.item_id,
                    order_by=(FeedbackReply.created_at.desc(), FeedbackReply.id.desc()),
                )
                .label("newest"),
                func.count().over(partition_by=FeedbackReply.item_id).label("total"),
            )
            .where(FeedbackReply.item_id.in_([item.id for item, _ in items]))
            .subquery()
        )
        reply = aliased(FeedbackReply, numbered)
        replies = (
            await db.execute(
                select(reply, numbered.c.total, Member.name)
                .outerjoin(Member, Member.id == reply.member_id)
                .where(numbered.c.newest <= MAX_MINE_REPLIES)
                .order_by(reply.created_at, reply.id)
            )
        ).all()
    threads: dict[uuid.UUID, list[dict[str, Any]]] = {item.id: [] for item, _ in items}
    counts: dict[uuid.UUID, int] = {}
    for shown, total, name in replies:
        threads[shown.item_id].append(_mine_reply(shown, name))
        counts[shown.item_id] = total
    entries = [
        _mine_item(item, context, threads[item.id], counts.get(item.id, 0), opens=opens) for item, opens in items
    ]
    return JSONResponse({"items": entries, "more": len(rows) > MAX_MINE}, headers=dict(FEEDBACK_API_HEADERS))


def _mine_rows(context: FeedbackContext, session: Session) -> Select[tuple[FeedbackItem, bool]]:
    """The visitor's own items on this site, newest first and one more than
    shown, which tells whether there are more. Each comes with whether the
    preview it was given on still opens: one that is gone or past its expiry
    is the neutral 404 at the gate, so the item gets no link there."""
    opens = (
        select(Preview.id)
        .where(
            Preview.site_id == FeedbackItem.site_id,
            Preview.ref == FeedbackItem.preview_ref,
            or_(Preview.expires_at.is_(None), Preview.expires_at > datetime.now(UTC)),
        )
        .exists()
    )
    return (
        select(FeedbackItem, opens)
        .where(
            FeedbackItem.site_id == context.site_id,
            FeedbackItem.author_kind == FeedbackIdentity.ACCOUNT,
            FeedbackItem.author_sub == session.sub,
        )
        .order_by(FeedbackItem.created_at.desc(), FeedbackItem.id.desc())
        .limit(MAX_MINE + 1)
    )


def highlight_anchor(kind: FeedbackKind, anchor: dict[str, Any] | None) -> dict[str, Any] | None:
    """The reduced form the widget finds a place with again, the one the
    `#toon-feedback=` fragment carries: k, s, x, y, px, py (a point), x, y, w,
    h, px, py, pw, ph (an area) and q, each only where the stored anchor has
    it."""
    if anchor is None:
        return None
    selectors = {selector["type"]: selector for selector in anchor["selectors"]}
    reduced: dict[str, Any] = {"k": kind.value}
    if CSS in selectors:
        reduced["s"] = selectors[CSS]["value"]
    point = anchor.get("point")
    if point is not None:
        reduced |= {"x": point["x"], "y": point["y"], "px": point["pageX"], "py": point["pageY"]}
    area = anchor.get("area")
    if area is not None:
        reduced |= {
            "x": area["x"],
            "y": area["y"],
            "w": area["width"],
            "h": area["height"],
            "px": area["pageX"],
            "py": area["pageY"],
            "pw": area["pageWidth"],
            "ph": area["pageHeight"],
        }
    if QUOTE in selectors:
        reduced["q"] = selectors[QUOTE]["exact"]
    return reduced


def _mine_item(
    item: FeedbackItem, context: FeedbackContext, replies: list[dict[str, Any]], reply_count: int, *, opens: bool
) -> dict[str, Any]:
    """One entry of the visitor's own items. `opens` says whether the preview
    the item was given on still opens. A live item has its link anyway."""
    root = site_prefix(context.group_slug, context.site_slug)
    url: str | None = f"{root}{item.page_path}"
    if item.preview_ref is not None:
        url = f"{root}_preview/{item.preview_ref}/{item.page_path}" if opens else None
    return {
        "id": str(item.id),
        "kind": item.kind.value,
        "status": item.status.value,
        "comment": item.comment,
        "quote": item.quote,
        "anchor": highlight_anchor(item.kind, item.anchor),
        "page": {"path": item.page_path, "title": item.page_title, "url": url},
        "target": item.target.value,
        "previewRef": item.preview_ref,
        "createdAt": iso_utc(item.created_at),
        "replies": replies,
        "replyCount": reply_count,
    }


def _mine_reply(reply: Any, name: str | None) -> dict[str, Any]:
    return {
        "id": str(reply.id),
        "author": "team" if reply.author_kind is FeedbackReplyAuthor.MEMBER else "you",
        "name": name,
        "body": reply.body,
        "createdAt": iso_utc(reply.created_at),
    }


def _item_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise _unavailable() from None


def _own_item(context: FeedbackContext, session: Session, item_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    return select(FeedbackItem.id).where(
        FeedbackItem.id == item_id,
        FeedbackItem.site_id == context.site_id,
        FeedbackItem.author_kind == FeedbackIdentity.ACCOUNT,
        FeedbackItem.author_sub == session.sub,
    )


@_widget_api
async def reply_to_item(request: Request, item_id: str) -> Response:
    """POST /-/feedback/v1/items/{id}/replies: the giver answers on their own
    item, 201 with the reply."""
    context = await _context(request)
    session = _account(context)
    target = _item_id(item_id)
    with _admitted_write(request, context), body_room(request, MAX_REPLY_BODY_BYTES, bad_body) as room:
        raw = await reply_bytes(request, room)
        body = reply_text(raw)
        async with request.app.state.session_factory() as db:
            # FOR KEY SHARE: the item cannot be deleted under the insert, and
            # a change of its status does not wait for it.
            own = _own_item(context, session, target).with_for_update(read=True, key_share=True)
            if await db.scalar(own) is None:
                raise _unavailable()
            if not await _takes_a_giver_reply(db, target):
                raise ProblemError(409, Msg("FEEDBACK_THREAD_FULL"))
            reply = (
                await db.execute(
                    insert(FeedbackReply)
                    .values(id=uuid.uuid4(), item_id=target, author_kind=FeedbackReplyAuthor.GIVER, body=body)
                    .returning(
                        FeedbackReply.id,
                        FeedbackReply.author_kind,
                        FeedbackReply.body,
                        FeedbackReply.created_at,
                    )
                )
            ).one()
            await db.commit()
    return JSONResponse(_mine_reply(reply, None), status_code=201, headers=dict(FEEDBACK_API_HEADERS))


async def _takes_a_giver_reply(db: AsyncSession, item_id: uuid.UUID) -> bool:
    """Whether the item takes one more reply from its giver: fewer than
    FEEDBACK_MAX_GIVER_REPLIES of theirs and FEEDBACK_MAX_REPLIES in all.
    Counted under the item's lock, which the admin's replies take too, held
    until the transaction ends."""
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(feedback_item_lock_key(item_id)))))
    total, from_giver = (
        await db.execute(
            select(func.count(), func.count().filter(FeedbackReply.author_kind == FeedbackReplyAuthor.GIVER))
            .select_from(FeedbackReply)
            .where(FeedbackReply.item_id == item_id)
        )
    ).one()
    return total < FEEDBACK_MAX_REPLIES and from_giver < FEEDBACK_MAX_GIVER_REPLIES


@_widget_api
async def delete_item(request: Request, item_id: str) -> Response:
    """DELETE /-/feedback/v1/items/{id}: the giver deletes their own item,
    with its replies and screenshot."""
    context = await _context(request)
    session = _account(context)
    target = _item_id(item_id)
    with _admitted_write(request, context):
        async with request.app.state.session_factory() as db:
            deleted = await db.execute(
                delete(FeedbackItem)
                .where(FeedbackItem.id.in_(_own_item(context, session, target)))
                .execution_options(synchronize_session=False)
            )
            if deleted.rowcount == 0:
                raise _unavailable()
            await db.commit()
    return Response(status_code=204, headers=dict(FEEDBACK_API_HEADERS))


__all__ = [
    "PREFLIGHT_HEADERS",
    "create_item",
    "delete_item",
    "highlight_anchor",
    "my_items",
    "preflight",
    "presented_token",
    "reply_to_item",
    "widget_problem",
    "write_counter_sweep",
]

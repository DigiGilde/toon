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

Whatever a giver sends is held to one rule for every string, the stored anchor
and viewport are rebuilt from validated fields only, and the body is read from
the stream with a bound on every part, so no input ends in a 500. Every chunk
of a body takes its room in a budget for the whole process before it is read,
and the parts keep the bytes as they arrived, without a copy. A client has at
most a few bodies in flight, and each is read within a deadline, so stalled
uploads cannot add up to more and a body that sends nothing holds nothing. The
open-item cap and the screenshot quotas are checked inside the create
transaction under an advisory lock on the site, and one for all screenshots
when the item has one, and the reply caps under one on the item, so
concurrent writes cannot overshoot them. The lock for all screenshots holds
until the commit, so one create at a time writes a screenshot: the bodies in
flight hold at most the budget, the slack their parts reserve as they grow,
and the database driver's copies of that one screenshot. A refusal by the
platform-wide screenshot cap, unlike one by a site's, is logged as a warning,
once per interval. Two counters in memory limit the accepted writes per
author and per site: checked before a body is read, charged once it has
arrived and refunded when the write is refused, so a body that sends nothing
holds no write, a refused attempt costs nothing and a burst cannot pass the
check together.

No audit rows: giving feedback is like looking at public content.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import math
import re
import time
import unicodedata
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator, Mapping
from contextlib import aclosing, asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NoReturn

from fastapi import Request
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlalchemy import Select, Text, cast, delete, func, insert, null, or_, select
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, Response

from toon import messages, net
from toon.api.errors import PROBLEM_CONTENT_TYPE, locale_from_header, problem_response
from toon.api.schema import iso_utc
from toon.auth.sessions import Session, site_prefix
from toon.config import FEEDBACK_BODY_ALLOWANCE, Settings
from toon.constants import (
    FEEDBACK_MAX_GIVER_REPLIES,
    FEEDBACK_MAX_REPLIES,
    RESERVED_SEGMENTS,
    FeedbackAuthor,
    FeedbackIdentity,
    FeedbackKind,
    FeedbackReplyAuthor,
    FeedbackStatus,
    feedback_item_lock_key,
    identity_allows,
)
from toon.messages import Msg
from toon.models.feedback import FeedbackItem, FeedbackReply, FeedbackScreenshot
from toon.models.publication import Preview, Site, Version, VersionTarget
from toon.ratelimit import FEEDBACK_API_HEADERS, CounterResult, InMemoryCounter
from toon.serving.feedback_access import FeedbackContext, verify_page_token
from toon.serving.feedback_images import ImageInfo, acceptable_screenshot

_logger = logging.getLogger(__name__)

# The JSON of one item, with room to spare.
MAX_ITEM_BYTES = 32 * 1024
MAX_REPLY_BODY_BYTES = 64 * 1024

MAX_COMMENT = 5000
MAX_NAME = 100
MAX_TITLE = 300
MAX_QUOTE = 1000
MAX_AFFIX = 32
MAX_SELECTOR = 1000
MAX_PAGE_PATH = 2048
MAX_COORDINATE = 1_000_000
MAX_DPR = 10
MAX_MINE = 50
# How many of an item's newest replies the visitor's own items carry.
# replyCount says how many there are in all.
MAX_MINE_REPLIES = 10
# The encoded answer of the visitor's own items at most, its newest item
# whatever its size: the bounds above count characters, and one character
# can take four bytes. A maximal item with its replies is about 250 KB.
MAX_MINE_BYTES = 256 * 1024
# feedback_items.author_name holds at most this; a name from the IdP is cut.
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

# Unicode categories no giver string may contain: Cc (control), Cf (format,
# such as U+202E, which reverses the reading order after it, or the tag
# characters a language model reads and a screen does not show), Cs (a lone
# surrogate, which asyncpg cannot encode) and Co (private use). Newline and
# tab stay allowed in a comment and a reply. Cn (unassigned) is not refused:
# the giver's browser can know emoji that this server's Unicode tables do not.
_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co"})
_KEPT_IN_PROSE = frozenset("\n\t")
# The two format characters writing needs, allowed in every string: the
# zero-width non-joiner keeps letters apart in Persian and other scripts, and
# the zero-width joiner makes one emoji of a sequence.
_JOINERS = frozenset("\u200c\u200d")

# What `location.pathname` can hold: printable ASCII without what the URL
# standard percent-encodes in a path (space, ", #, <, >, ?, `, {, }) and
# without the backslash, which a browser turns into a slash.
_PATHNAME = re.compile(r"[!$%&'()*+,\-./0-9:;=@A-Z\[\]^_a-z|~]*")

_KINDS: dict[str, FeedbackKind] = {kind.value: kind for kind in FeedbackKind}
_AUTHORS: dict[str, FeedbackAuthor] = {author.value: author for author in FeedbackAuthor}

_ITEM_KEYS = frozenset({"kind", "comment", "page", "anchor", "viewport", "author", "screenshot"})
_ITEM_REQUIRED = frozenset({"kind", "comment", "page", "author"})
_PAGE_KEYS = frozenset({"path", "title"})
_ANCHOR_KEYS = frozenset({"selectors", "point"})
_POINT_KEYS = frozenset({"x", "y", "pageX", "pageY"})
_VIEWPORT_KEYS = frozenset({"width", "height", "dpr"})
_AUTHOR_KEYS = frozenset({"kind", "name"})
_REGION_KEYS = frozenset({"x", "y", "width", "height"})

_QUOTE = "TextQuoteSelector"
_POSITION = "TextPositionSelector"
_CSS = "CssSelector"
# Per selector type: the keys it may have and the keys it must have.
_SELECTOR_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    _QUOTE: (frozenset({"type", "exact", "prefix", "suffix"}), frozenset({"type", "exact"})),
    _POSITION: (frozenset({"type", "start", "end"}), frozenset({"type", "start", "end"})),
    _CSS: (frozenset({"type", "value"}), frozenset({"type", "value"})),
}
_SELECTOR_TYPES: dict[str, str] = {name: name for name in _SELECTOR_KEYS}


# --- Answers ------------------------------------------------------------------


class _ProblemError(Exception):
    """A refusal, raised wherever it shows up and answered by `_widget_api`."""

    def __init__(self, status: int, message: Msg, *, retry_after: int | None = None) -> None:
        super().__init__(message.key)
        self.status = status
        self.message = message
        self.retry_after = retry_after


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


def _unavailable() -> _ProblemError:
    return _ProblemError(404, Msg("FEEDBACK_UNAVAILABLE"))


def _bad_field(field: str) -> _ProblemError:
    return _ProblemError(422, Msg("FEEDBACK_INVALID", {"field": field}))


def _too_large() -> _ProblemError:
    return _ProblemError(413, Msg("FEEDBACK_SCREENSHOT_TOO_LARGE"))


def _bad_body() -> _ProblemError:
    return _bad_field("body")


type Handler = Callable[..., Awaitable[Response]]


def _widget_api(handler: Handler) -> Handler:
    @functools.wraps(handler)
    async def answer(request: Request, *args: str) -> Response:
        try:
            return await handler(request, *args)
        except _ProblemError as problem:
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
        raise _ProblemError(403, Msg("FEEDBACK_ACCOUNT_REQUIRED"))
    return context.session


def _client_key(request: Request) -> str:
    """The client as the rate limiter counts one that sends no cookie: its
    address, IPv6 by the /64."""
    settings: Settings = request.app.state.settings
    return f"ip:{net.rate_limit_key(net.client_ip(request, bool(settings.behind_proxy)))}"


# --- The write counters -------------------------------------------------------


class WriteCounter(InMemoryCounter):
    """Accepted widget writes per key: charged before a write and refunded
    when it is refused, so a refused attempt spends nothing. A key, an
    address or a sub, is held no longer than the counting needs: it goes
    when its window has ended, swept on every check and charge and by
    `write_counter_sweep`, and when a refund takes its count back to 0."""

    def sweep(self, now_: float) -> None:
        """Drops every bucket whose window has ended. All of them share the
        one window of the counter, and a new window moves its bucket to the
        end, so the ended ones are the first."""
        buckets = self._buckets
        while buckets:
            first = next(iter(buckets.values()))
            if now_ - first.window_start < first.window_s:
                return
            buckets.popitem(last=False)

    def spent(self, key: str, now_: float) -> CounterResult:
        self.sweep(now_)
        bucket = self._buckets.get(key)
        if bucket is None:
            return CounterResult(count=0, remaining_s=0.0)
        return CounterResult(count=bucket.count, remaining_s=bucket.window_s - (now_ - bucket.window_start))

    def charge(self, key: str, window_s: int, now_: float) -> float:
        """One write on `key`. Returns the start of the window it counts in,
        which a refund needs."""
        self.sweep(now_)
        self.add(key, window_s, now_)
        return self._buckets[key].window_start

    def refund(self, key: str, window_start: float) -> None:
        """Takes a charge back, unless its window has ended since: a new
        window never owed it."""
        bucket = self._buckets.get(key)
        if bucket is not None and bucket.window_start == window_start:
            bucket.count -= 1
            if not bucket.count:
                del self._buckets[key]


# A clock of its own, so a test can move it without moving asyncio's.
_clock = time.monotonic


async def _sweep_every(counters: tuple[WriteCounter, ...], interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        now_ = _clock()
        for counter in counters:
            counter.sweep(now_)


@asynccontextmanager
async def write_counter_sweep(counters: tuple[WriteCounter, ...], interval: float) -> AsyncIterator[asyncio.Task]:
    """Background task that sweeps the write counters every `interval`
    seconds, for the lifespan of main.py: without a write that sweeps them,
    the key of the last giver would stay. With the window as `interval`, a
    key is gone at most one window after its window ended."""
    task = asyncio.create_task(_sweep_every(counters, interval))
    try:
        yield task
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def _author_budget(request: Request, settings: Settings, context: FeedbackContext) -> tuple[str, int]:
    """Whom the author's write counter counts, and how many writes it allows.
    Every key is one on this site: the site's own scripts hold its page
    token, so a count across sites would let one site spend a visitor's
    budget on every other. A consented session by its sub, and a session for
    the gate alone by its sub too, each with the budget of one person on one
    site. The gate's own namespace keeps its count apart from the consented
    one: a site that sees the person's name would otherwise read how many
    writes without a name that person made. Anyone else by their address,
    with the budget of a network that many people can share."""
    site = f"site:{context.site_id}"
    if context.session is not None:
        return f"{site}:session:{context.session.sub}", settings.feedback_writes_per_author
    if context.gate_sub is not None:
        return f"{site}:gate:{context.gate_sub}", settings.feedback_writes_per_author
    return f"{site}:{_client_key(request)}", settings.feedback_writes_per_address


def _budgets(request: Request, context: FeedbackContext) -> list[tuple[WriteCounter, str, int]]:
    state = request.app.state
    settings: Settings = state.settings
    author, author_limit = _author_budget(request, settings, context)
    return [
        (state.feedback_author_writes, author, author_limit),
        (state.feedback_site_writes, f"site:{context.site_id}", settings.feedback_writes_per_site),
    ]


def _refuse_spent(budgets: list[tuple[WriteCounter, str, int]], now_: float) -> None:
    """429 when the author's budget or the site's is spent."""
    waits = [
        spent.remaining_s for counter, key, limit in budgets if (spent := counter.spent(key, now_)).count >= limit
    ]
    if waits:
        raise _ProblemError(429, Msg("FEEDBACK_RATE_LIMITED"), retry_after=max(1, math.ceil(max(waits))))


def _within_budget(request: Request, context: FeedbackContext) -> None:
    """429 when either budget is spent, before a body is read, and nothing
    charged: a body that stalls holds no write. Only the check in `_write`,
    once the body has arrived, admits one."""
    _refuse_spent(_budgets(request, context), _clock())


@contextmanager
def _write(request: Request, context: FeedbackContext) -> Iterator[None]:
    """One write charged to the author's budget and the site's for the block,
    or 429 when either is spent. Nothing is awaited between the check and the
    charge, so concurrent writes cannot pass the check together, and a block
    that raises gets the charge back: only an accepted write counts."""
    now_ = _clock()
    budgets = _budgets(request, context)
    _refuse_spent(budgets, now_)
    window = request.app.state.settings.feedback_writes_window_s
    charges = [(counter, key, counter.charge(key, window, now_)) for counter, key, _ in budgets]
    try:
        yield
    except BaseException:
        for counter, key, window_start in charges:
            counter.refund(key, window_start)
        raise


# --- The bodies in flight -----------------------------------------------------


class BodyBudget:
    """The widget API bodies this process holds right now: the bytes that
    arrived of all of them together, and how many each client has in flight.
    A body takes room for each chunk before it is read and gives all of it
    back when its request is done, so however many uploads stall, they hold
    no more than the budget, and a declared length that never arrives holds
    nothing."""

    def __init__(self) -> None:
        self.in_flight = 0
        self._per_client: dict[str, int] = {}

    def admit(self, client: str, *, per_client: int) -> bool:
        """One more body from `client`, or False when it has as many in
        flight as it may have."""
        held = self._per_client.get(client, 0)
        if held >= per_client:
            return False
        self._per_client[client] = held + 1
        return True

    def take(self, size: int, *, budget: int) -> bool:
        """Room for `size` more bytes, or False when the budget cannot hold
        them. Nothing awaits in here, so two chunks cannot both take the
        last room."""
        if self.in_flight + size > budget:
            return False
        self.in_flight += size
        return True

    def give_back(self, client: str, size: int) -> None:
        """A body is done: the `size` bytes it took go back, and so does its
        place among the client's bodies."""
        self.in_flight -= size
        held = self._per_client.pop(client) - 1
        if held:
            self._per_client[client] = held


def _busy(settings: Settings) -> _ProblemError:
    # Every body in flight now is read, or refused, within the deadline.
    return _ProblemError(503, Msg("FEEDBACK_BUSY"), retry_after=settings.feedback_body_deadline_seconds)


class _Room:
    """One body's room in the budget: the bound to read it to, and the bytes
    it has taken so far."""

    def __init__(self, bodies: BodyBudget, settings: Settings, bound: int) -> None:
        self.bound = bound
        self.taken = 0
        self._bodies = bodies
        self._settings = settings

    def take(self, size: int) -> None:
        """Room for a chunk of `size` bytes before it is read, or 503
        FEEDBACK_BUSY when the budget is full."""
        if not self._bodies.take(size, budget=self._settings.feedback_body_budget_bytes):
            raise _busy(self._settings)
        self.taken += size


# 2**64 has 20 digits: a Content-Length with more is beyond any body, and is
# refused before int() is asked to read it.
_MAX_LENGTH_DIGITS = 20


@contextmanager
def _body_room(request: Request, limit: int, refusal: Callable[[], _ProblemError]) -> Iterator[_Room]:
    """This request's body among the bodies in flight until the block ends,
    with room for each chunk as it arrives, read to its declared length, or
    to `limit` when it declares none. A declared length above `limit` is
    refused unread, and a client with as many bodies in flight as it may
    have is 503 FEEDBACK_BUSY."""
    settings: Settings = request.app.state.settings
    declared = request.headers.get("content-length", "")
    bound = limit
    if declared.isascii() and declared.isdigit():
        if len(declared) > _MAX_LENGTH_DIGITS or int(declared) > limit:
            raise refusal()
        bound = int(declared)
    client = _client_key(request)
    bodies: BodyBudget = request.app.state.feedback_bodies
    if not bodies.admit(client, per_client=settings.feedback_bodies_per_client):
        raise _busy(settings)
    room = _Room(bodies, settings, bound)
    try:
        yield room
    finally:
        bodies.give_back(client, room.taken)


# --- Reading what a giver sends ---------------------------------------------


def giver_text(value: object, *, multiline: bool = False) -> str | None:
    """`value` when it is a string whose every character may stand in what a
    giver writes, else None."""
    if not isinstance(value, str):
        return None
    allowed = (_JOINERS | _KEPT_IN_PROSE) if multiline else _JOINERS
    for char in value:
        if char not in allowed and unicodedata.category(char) in _REFUSED_CATEGORIES:
            return None
    return value


def clean_title(value: str) -> str | None:
    """A page title is the page's, not the giver's: cleaned of what giver
    text may not hold and cut, never refused."""
    kept = "".join(
        char for char in value if char in _JOINERS or unicodedata.category(char) not in _REFUSED_CATEGORIES
    )
    return kept.strip()[:MAX_TITLE] or None


def _no_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not a number in JSON")


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    found = dict(pairs)
    if len(found) != len(pairs):
        raise ValueError("a key appears twice")
    return found


def _json(text: str, field: str) -> object:
    try:
        return json.loads(text, parse_constant=_no_constant, object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError):
        raise _bad_field(field) from None


def _shape(value: object, field: str, allowed: frozenset[str], required: frozenset[str]) -> dict[str, Any]:
    """A JSON object with no key outside `allowed` and every key of `required`."""
    if not isinstance(value, dict) or not value.keys() <= allowed or not required <= value.keys():
        raise _bad_field(field)
    return value


def _text(value: object, field: str, *, low: int, high: int, multiline: bool = False, trim: bool = False) -> str:
    text = giver_text(value, multiline=multiline)
    if text is None:
        raise _bad_field(field)
    if trim:
        text = text.strip()
    if not low <= len(text) <= high:
        raise _bad_field(field)
    return text


def _number(value: object, field: str, *, high: float = MAX_COORDINATE) -> int | float:
    # The range check also turns away the infinities that a number such as
    # 1e400 still parses to; NaN and Infinity are refused while parsing.
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= high:
        raise _bad_field(field)
    return value


def _position(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_COORDINATE:
        raise _bad_field(field)
    return value


def _choice[T](value: object, field: str, options: Mapping[str, T]) -> T:
    if not isinstance(value, str) or value not in options:
        raise _bad_field(field)
    return options[value]


@dataclass(frozen=True)
class FeedbackInput:
    """An item as the widget sent it, after every check and with the anchor
    and viewport rebuilt from what passed."""

    kind: FeedbackKind
    comment: str
    # Relative to the target root, without a leading slash.
    page_path: str
    page_title: str | None
    anchor: dict[str, Any] | None
    quote: str | None
    viewport: dict[str, Any] | None
    author: FeedbackAuthor
    author_name: str | None


def parse_item(text: str, root: str, site: str) -> FeedbackInput:
    """The `item` part of a create, against the root of the token's target
    and the root of its site, both at the site's current address."""
    item = _shape(_json(text, "item"), "item", _ITEM_KEYS, _ITEM_REQUIRED)
    kind = _choice(item["kind"], "kind", _KINDS)
    comment = _text(item["comment"], "comment", low=1, high=MAX_COMMENT, multiline=True, trim=True)
    page = _shape(item["page"], "page", _PAGE_KEYS, frozenset({"path"}))
    anchor, quote = _anchor(kind, item.get("anchor"))
    author, name = _author(item["author"])
    _screenshot_note(item.get("screenshot"))
    return FeedbackInput(
        kind=kind,
        comment=comment,
        page_path=_page_path(page["path"], root, site),
        page_title=_title(page.get("title")),
        anchor=anchor,
        quote=quote,
        viewport=_viewport(item.get("viewport")),
        author=author,
        author_name=name,
    )


def _page_path(value: object, root: str, site: str) -> str:
    path = _text(value, "page.path", low=1, high=MAX_PAGE_PATH)
    if not _PATHNAME.fullmatch(path):
        raise _bad_field("page.path")
    # A page served before its site or group got a new address still holds
    # a valid token, which names the site by its id: only a reload gives the
    # widget the address the site is at now.
    if not path.startswith(site):
        raise _ProblemError(422, Msg("FEEDBACK_PAGE_MOVED"))
    if not path.startswith(root):
        raise _bad_field("page.path")
    rest = path[len(root) :]
    segments = rest.split("/")
    # An empty segment anywhere but at the end would make the stored path
    # start with a slash or hide one inside it.
    if segments[0] in RESERVED_SEGMENTS or "" in segments[:-1] or any(map(_dot_segment, segments)):
        raise _bad_field("page.path")
    return rest


def _dot_segment(segment: str) -> bool:
    # The URL standard reads %2e as a dot in a dot segment, in either case.
    return segment.lower().replace("%2e", ".") in (".", "..")


def _title(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _bad_field("page.title")
    return clean_title(value)


def _anchor(kind: FeedbackKind, value: object) -> tuple[dict[str, Any] | None, str | None]:
    """The stored anchor and the quote. Unknown keys and selector types are
    refused, and only what passed is stored."""
    if kind is FeedbackKind.PAGE:
        if value is not None:
            raise _bad_field("anchor")
        return None, None
    anchor = _shape(value, "anchor", _ANCHOR_KEYS, frozenset({"selectors"}))
    selectors = anchor["selectors"]
    if not isinstance(selectors, list) or not 1 <= len(selectors) <= len(_SELECTOR_KEYS):
        raise _bad_field("anchor.selectors")
    found: dict[str, dict[str, Any]] = {}
    for selector in selectors:
        named = selector.get("type") if isinstance(selector, dict) else None
        name = _choice(named, "anchor.selectors", _SELECTOR_TYPES)
        if name in found:
            raise _bad_field("anchor.selectors")
        found[name] = _selector(name, selector)
    point = anchor.get("point")
    if kind is FeedbackKind.POINT:
        if found.keys() != {_CSS}:
            raise _bad_field("anchor.selectors")
        return {"selectors": [found[_CSS]], "point": _point(point)}, None
    if _QUOTE not in found:
        raise _bad_field("anchor.selectors")
    if point is not None:
        raise _bad_field("anchor.point")
    ordered = [found[name] for name in (_QUOTE, _POSITION, _CSS) if name in found]
    return {"selectors": ordered, "point": None}, found[_QUOTE]["exact"]


def _selector(name: str, value: object) -> dict[str, Any]:
    allowed, required = _SELECTOR_KEYS[name]
    selector = _shape(value, "anchor.selectors", allowed, required)
    if name == _QUOTE:
        return {
            "type": name,
            "exact": _text(selector["exact"], "anchor.selectors", low=1, high=MAX_QUOTE),
            "prefix": _affix(selector.get("prefix")),
            "suffix": _affix(selector.get("suffix")),
        }
    if name == _POSITION:
        start = _position(selector["start"], "anchor.selectors")
        end = _position(selector["end"], "anchor.selectors")
        if start >= end:
            raise _bad_field("anchor.selectors")
        return {"type": name, "start": start, "end": end}
    return {"type": name, "value": _text(selector["value"], "anchor.selectors", low=1, high=MAX_SELECTOR)}


def _affix(value: object) -> str:
    return "" if value is None else _text(value, "anchor.selectors", low=0, high=MAX_AFFIX)


def _point(value: object) -> dict[str, Any]:
    point = _shape(value, "anchor.point", _POINT_KEYS, _POINT_KEYS)
    return {
        "x": _number(point["x"], "anchor.point", high=1),
        "y": _number(point["y"], "anchor.point", high=1),
        "pageX": _number(point["pageX"], "anchor.point"),
        "pageY": _number(point["pageY"], "anchor.point"),
    }


def _viewport(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    viewport = _shape(value, "viewport", _VIEWPORT_KEYS, _VIEWPORT_KEYS)
    dpr = _number(viewport["dpr"], "viewport", high=MAX_DPR)
    if dpr == 0:
        raise _bad_field("viewport")
    return {
        "width": _number(viewport["width"], "viewport"),
        "height": _number(viewport["height"], "viewport"),
        "dpr": dpr,
    }


def _author(value: object) -> tuple[FeedbackAuthor, str | None]:
    """The author kind and, for `name` only, the typed name. A name beside
    any other kind is refused: what the item says about its author comes from
    the session or from nowhere."""
    author = _shape(value, "author", _AUTHOR_KEYS, frozenset({"kind"}))
    kind = _choice(author["kind"], "author.kind", _AUTHORS)
    name = author.get("name")
    if kind is FeedbackAuthor.NAME:
        return kind, _text(name, "author.name", low=1, high=MAX_NAME, trim=True)
    if name is not None and (not isinstance(name, str) or name.strip()):
        raise _bad_field("author.name")
    return kind, None


def _screenshot_note(value: object) -> None:
    """What the item says about its screenshot, which is checked and not kept."""
    if value is None:
        return
    region = _shape(value, "screenshot", frozenset({"region"}), frozenset()).get("region")
    if region is None:
        return
    for number in _shape(region, "screenshot", _REGION_KEYS, _REGION_KEYS).values():
        _number(number, "screenshot")


def _author_columns(item: FeedbackInput, context: FeedbackContext) -> dict[str, str | None]:
    if not identity_allows(context.feedback.identity, item.author):
        raise _ProblemError(422, Msg("FEEDBACK_AUTHOR_NOT_ALLOWED"))
    if item.author is FeedbackAuthor.ACCOUNT:
        session = _account(context)
        return {
            "author_sub": session.sub,
            "author_name": session.name[:_MAX_STORED_NAME] if session.name else None,
            "author_email": session.email.lower() if session.email and session.email_verified else None,
        }
    return {"author_sub": None, "author_name": item.author_name, "author_email": None}


# --- The body of a create -----------------------------------------------------


@dataclass(frozen=True)
class _Screenshot:
    data: bytearray
    info: ImageInfo


class _Parts:
    """The two parts of a create, collected as the parser finds them, each
    held to its own bound while it streams in."""

    def __init__(self, screenshot_limit: int) -> None:
        self.found: dict[str, bytearray] = {}
        self.ended = False
        self._screenshot_limit = screenshot_limit
        self._name = ""
        self._header_name = bytearray()
        self._header_value = bytearray()
        self._disposition = b""

    def callbacks(self) -> dict[str, Callable[..., None]]:
        return {
            "on_part_begin": self._part_begin,
            "on_header_field": self._header_field,
            "on_header_value": self._header_value_data,
            "on_header_end": self._header_end,
            "on_headers_finished": self._headers_finished,
            "on_part_data": self._part_data,
            "on_end": self._end,
        }

    def _part_begin(self) -> None:
        self._disposition = b""

    def _header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_name += data[start:end]

    def _header_value_data(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def _header_end(self) -> None:
        if self._header_name.lower() == b"content-disposition":
            self._disposition = bytes(self._header_value)
        self._header_name = bytearray()
        self._header_value = bytearray()

    def _headers_finished(self) -> None:
        disposition, options = parse_options_header(self._disposition)
        name = options.get(b"name", b"").decode("latin-1")
        if disposition != b"form-data" or name not in ("item", "screenshot") or name in self.found:
            raise _bad_field("item")
        self._name = name
        self.found[name] = bytearray()

    def _part_data(self, data: bytes, start: int, end: int) -> None:
        part = self.found[self._name]
        part += data[start:end]
        if self._name == "item":
            if len(part) > MAX_ITEM_BYTES:
                raise _bad_field("item")
        elif len(part) > self._screenshot_limit:
            raise _too_large()

    def _end(self) -> None:
        self.ended = True


# The delimiters a create holds at most: one after the item, one that closes
# the body, and one more when the body opens with a line break before its
# first boundary. RFC 2046 keeps the delimiter out of every part.
_MAX_DELIMITERS = 3


class _Delimited:
    """The parser of a create, handed each chunk once the delimiters in it are
    counted at C speed, those split over two chunks included. The parser
    steps through every delimiter it finds byte by byte in Python, so a body
    packed with them is refused before it is parsed."""

    def __init__(self, parser: MultipartParser, boundary: bytes) -> None:
        self._parser = parser
        self._delimiter = b"\r\n--" + boundary
        self._tail = b""
        self._count = 0

    def write(self, chunk: bytes) -> None:
        keep = len(self._delimiter) - 1
        # One split over two chunks starts in the tail, which is shorter than
        # a delimiter, and ends in the first `keep` bytes of the chunk.
        self._count += (self._tail + chunk[:keep]).count(self._delimiter) + chunk.count(self._delimiter)
        if self._count > _MAX_DELIMITERS:
            raise _bad_field("item")
        self._tail = (self._tail + chunk[-keep:])[-keep:]
        self._parser.write(chunk)


async def _bounded(request: Request, room: _Room, refusal: Callable[[], _ProblemError]) -> AsyncIterator[bytes]:
    """The body as it streams in, each chunk with its room taken before it
    is read, and refused once the body is longer than the room's bound."""
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > room.bound:
            raise refusal()
        room.take(len(chunk))
        yield chunk


async def _read(
    request: Request, room: _Room, refusal: Callable[[], _ProblemError], take: Callable[[bytes], object]
) -> None:
    """Hands the body to `take` as it streams in, at most the room's bound,
    and all of it within the deadline, or 408 FEEDBACK_TIMEOUT."""
    deadline = request.app.state.settings.feedback_body_deadline_seconds
    try:
        # Around the loop rather than inside `_bounded`: a timeout scope must
        # not span the yield of an async generator.
        async with asyncio.timeout(deadline), aclosing(_bounded(request, room, refusal)) as body:
            async for chunk in body:
                take(chunk)
    except TimeoutError:
        raise _ProblemError(408, Msg("FEEDBACK_TIMEOUT")) from None


async def _create_parts(request: Request, room: _Room, screenshot_limit: int) -> tuple[bytearray, bytearray | None]:
    media_type, options = parse_options_header(request.headers.get("content-type"))
    boundary = options.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary:
        raise _bad_field("item")
    parts = _Parts(screenshot_limit)
    try:
        parser = _Delimited(MultipartParser(boundary, parts.callbacks()), boundary)
        await _read(request, room, _too_large, parser.write)
    except FormParserError:
        raise _bad_field("item") from None
    item = parts.found.get("item")
    if not parts.ended or item is None:
        raise _bad_field("item")
    # The parts as the parser filled them: a copy would hold the screenshot a
    # second time, outside the budget.
    return item, parts.found.get("screenshot")


# --- The routes -------------------------------------------------------------------


@_widget_api
async def create_item(request: Request) -> Response:
    """POST /-/feedback/v1/items: one new item, 201 with its id."""
    context = await _context(request)
    # A site that takes feedback with an account only refuses every create
    # without a session: before its body costs anything.
    if context.feedback.identity is FeedbackIdentity.ACCOUNT:
        _account(context)
    _within_budget(request, context)
    settings: Settings = request.app.state.settings
    screenshot_limit = settings.feedback_screenshot_max_bytes
    with _body_room(request, screenshot_limit + FEEDBACK_BODY_ALLOWANCE, _too_large) as room:
        item_bytes, screenshot_bytes = await _create_parts(request, room, screenshot_limit)
        with _write(request, context):
            try:
                text = item_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raise _bad_field("item") from None
            item = parse_item(text, context.root, site_prefix(context.group_slug, context.site_slug))
            author = _author_columns(item, context)
            screenshot = None
            if screenshot_bytes is not None:
                info = acceptable_screenshot(screenshot_bytes)
                if info is None:
                    raise _ProblemError(422, Msg("FEEDBACK_SCREENSHOT_INVALID"))
                screenshot = _Screenshot(screenshot_bytes, info)
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
    screenshot: _Screenshot | None,
) -> Any:
    settings: Settings = request.app.state.settings
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
        if open_items >= settings.feedback_site_max_open:
            raise _ProblemError(409, Msg("FEEDBACK_SITE_FULL"))
        if screenshot is not None:
            await _check_quota(db, settings, site_id, len(screenshot.data))
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
                    # null(): a bare None would be stored as JSON null, which
                    # the anchor CHECK reads as an anchor.
                    anchor=null() if item.anchor is None else item.anchor,
                    quote=item.quote,
                    comment=item.comment,
                    author_kind=item.author,
                    viewport=null() if item.viewport is None else item.viewport,
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


async def _check_quota(db: AsyncSession, settings: Settings, site_id: uuid.UUID, size: int) -> None:
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
    cap = settings.feedback_total_screenshot_max_bytes
    platform_full = total_bytes + size > cap
    # A site's own cap is the business of its team. This one stops every
    # site, so the operator hears of it, in byte counts only.
    if platform_full and _quota_warnings.add("platform", QUOTA_WARNING_INTERVAL_S, _clock()).count == 1:
        _logger.warning(
            "The platform-wide cap on feedback screenshots is reached: %d of %d bytes are stored, and a "
            "screenshot of %d bytes was refused. Every site refuses a screenshot that does not fit until "
            "space is freed or TOON_FEEDBACK_TOTAL_SCREENSHOT_MAX_BYTES is raised. Logged at most once "
            "per %d seconds.",
            total_bytes,
            cap,
            size,
            QUOTA_WARNING_INTERVAL_S,
        )
    if platform_full or site_bytes + size > settings.feedback_site_screenshot_max_bytes:
        raise _ProblemError(409, Msg("FEEDBACK_SCREENSHOT_QUOTA"))


@_widget_api
async def my_items(request: Request) -> Response:
    """GET /-/feedback/v1/items: the visitor's own items on this site, the
    ones sent with their account, newest first. Each carries its newest
    replies, oldest first, and how many it has in all. At most MAX_MINE
    items in at most MAX_MINE_BYTES, and `more` says whether older ones were
    left out. What can fit is worked out from the sizes of the texts first,
    so a call loads the texts of the items it may answer with and no more."""
    context = await _context(request)
    session = _account(context)
    async with request.app.state.session_factory() as db:
        sizes = (await db.execute(_mine_sizes(context, session))).all()
        kept = [item_id for item_id, _ in sizes[: _mine_fitting(size for _, size in sizes[:MAX_MINE])]]
        items = (await db.execute(_mine_rows(kept))).all()
        numbered = (
            select(
                FeedbackReply,
                _newest_reply_first().label("newest"),
                func.count().over(partition_by=FeedbackReply.item_id).label("total"),
            )
            .where(FeedbackReply.item_id.in_(kept))
            .subquery()
        )
        reply = aliased(FeedbackReply, numbered)
        replies = (
            await db.execute(
                select(reply, numbered.c.total)
                .where(numbered.c.newest <= MAX_MINE_REPLIES)
                .order_by(reply.created_at, reply.id)
            )
        ).all()
    threads: dict[uuid.UUID, list[dict[str, Any]]] = {item.id: [] for item, _ in items}
    counts: dict[uuid.UUID, int] = {}
    for shown, total in replies:
        threads[shown.item_id].append(_mine_reply(shown))
        counts[shown.item_id] = total
    entries = (
        _mine_item(item, context, threads[item.id], counts.get(item.id, 0), opens=opens) for item, opens in items
    )
    return Response(
        _mine_answer(entries, more=len(sizes) > len(kept)),
        media_type="application/json",
        headers=dict(FEEDBACK_API_HEADERS),
    )


def _octets(value: Any) -> Any:
    """The bytes a text takes in the database, 0 for NULL, read without
    loading the text."""
    return func.coalesce(func.octet_length(value), 0)


def _selector_octets(selector: str, key: str) -> Any:
    """The bytes of one selector value of the anchor as a JSON string, the
    way the reduced anchor carries it, read without loading the anchor."""
    path = cast(f'$.selectors[*] ? (@.type == "{selector}").{key}', JSONPATH)
    return _octets(cast(func.jsonb_path_query_first(FeedbackItem.anchor, path), Text))


def _newest_reply_first() -> Any:
    """Numbers the replies of each item from its newest."""
    return func.row_number().over(
        partition_by=FeedbackReply.item_id,
        order_by=(FeedbackReply.created_at.desc(), FeedbackReply.id.desc()),
    )


def _mine_sizes(context: FeedbackContext, session: Session) -> Select[tuple[uuid.UUID, int]]:
    """The visitor's own items on this site, newest first and one more than
    shown, each with the bytes of the texts its entry carries: its own, those
    of the reduced anchor and those of the replies it carries."""
    own = (
        select(
            FeedbackItem.id,
            FeedbackItem.created_at,
            (
                _octets(FeedbackItem.comment)
                + _octets(FeedbackItem.quote)
                + _octets(FeedbackItem.page_path)
                + _octets(FeedbackItem.page_title)
                + _selector_octets(_CSS, "value")
                + _selector_octets(_QUOTE, "exact")
            ).label("size"),
        )
        .where(
            FeedbackItem.site_id == context.site_id,
            FeedbackItem.author_kind == FeedbackAuthor.ACCOUNT,
            FeedbackItem.author_sub == session.sub,
        )
        .order_by(FeedbackItem.created_at.desc(), FeedbackItem.id.desc())
        # One more than shown: it tells whether there are more.
        .limit(MAX_MINE + 1)
        .cte("own")
    )
    numbered = (
        select(
            FeedbackReply.item_id,
            (_octets(FeedbackReply.body) + _octets(FeedbackReply.author_name)).label("size"),
            _newest_reply_first().label("newest"),
        )
        .where(FeedbackReply.item_id.in_(select(own.c.id)))
        .subquery()
    )
    carried = (
        select(numbered.c.item_id, func.sum(numbered.c.size).label("size"))
        .where(numbered.c.newest <= MAX_MINE_REPLIES)
        .group_by(numbered.c.item_id)
        .subquery()
    )
    return (
        select(own.c.id, own.c.size + func.coalesce(carried.c.size, 0))
        .outerjoin(carried, carried.c.item_id == own.c.id)
        .order_by(own.c.created_at.desc(), own.c.id.desc())
    )


def _mine_fitting(sizes: Iterable[int]) -> int:
    """How many of the newest items can fit in MAX_MINE_BYTES, the first one
    always, counted as `_mine_answer` counts them. A size is at most what the
    item's entry takes, since the entry's JSON holds every text it counts,
    so an item left out here would not fit once encoded either."""
    kept = 0
    size = _MINE_FRAME
    for octets in sizes:
        size += octets + bool(kept)
        if kept and size > MAX_MINE_BYTES:
            break
        kept += 1
    return kept


def _mine_rows(kept: list[uuid.UUID]) -> Select[tuple[FeedbackItem, bool]]:
    """The kept items, newest first, each with whether the preview it was
    given on still opens: one that is gone or past its expiry is the
    neutral 404 at the gate, so the item gets no link there."""
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
        .where(FeedbackItem.id.in_(kept))
        .order_by(FeedbackItem.created_at.desc(), FeedbackItem.id.desc())
    )


def _json_bytes(value: object) -> bytes:
    """JSON as JSONResponse writes it."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


# The answer around its items, `more` at the longer of its two values.
_MINE_FRAME = len(b'{"items":[],"more":false}')


def _mine_answer(entries: Iterable[dict[str, Any]], *, more: bool) -> bytes:
    """`{"items": [...], "more": ...}` with the newest entries while the
    answer stays within MAX_MINE_BYTES, the first one always, and `more`
    also when an entry was left out for its bytes."""
    parts: list[bytes] = []
    size = _MINE_FRAME
    for entry in entries:
        part = _json_bytes(entry)
        # A comma before every entry but the first.
        size += len(part) + bool(parts)
        if parts and size > MAX_MINE_BYTES:
            more = True
            break
        parts.append(part)
    return b'{"items":[' + b",".join(parts) + b'],"more":' + (b"true" if more else b"false") + b"}"


def highlight_anchor(kind: FeedbackKind, anchor: dict[str, Any] | None) -> dict[str, Any] | None:
    """The reduced form the widget finds a place with again, the one the
    `#toon-feedback=` fragment carries: k, s, x, y, px, py and q, each only
    where the stored anchor has it."""
    if anchor is None:
        return None
    selectors = {selector["type"]: selector for selector in anchor["selectors"]}
    reduced: dict[str, Any] = {"k": kind.value}
    if _CSS in selectors:
        reduced["s"] = selectors[_CSS]["value"]
    point = anchor.get("point")
    if point is not None:
        reduced |= {"x": point["x"], "y": point["y"], "px": point["pageX"], "py": point["pageY"]}
    if _QUOTE in selectors:
        reduced["q"] = selectors[_QUOTE]["exact"]
    return reduced


def _mine_item(
    item: FeedbackItem, context: FeedbackContext, replies: list[dict[str, Any]], reply_count: int, *, opens: bool
) -> dict[str, Any]:
    """One entry of the visitor's own items. `opens` says whether the preview
    the item was given on still opens; a live item has its link anyway."""
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


def _mine_reply(reply: Any) -> dict[str, Any]:
    team = reply.author_kind is FeedbackReplyAuthor.MEMBER
    return {
        "id": str(reply.id),
        "author": "team" if team else "you",
        "name": reply.author_name if team else None,
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
        FeedbackItem.author_kind == FeedbackAuthor.ACCOUNT,
        FeedbackItem.author_sub == session.sub,
    )


async def _reply_bytes(request: Request, room: _Room) -> bytes:
    if parse_options_header(request.headers.get("content-type"))[0] != b"application/json":
        raise _bad_field("body")
    chunks: list[bytes] = []
    await _read(request, room, _bad_body, chunks.append)
    return b"".join(chunks)


def _reply_text(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _bad_field("body") from None
    reply = _shape(_json(text, "body"), "body", frozenset({"body"}), frozenset({"body"}))
    return _text(reply["body"], "body", low=1, high=MAX_COMMENT, multiline=True, trim=True)


@_widget_api
async def reply_to_item(request: Request, item_id: str) -> Response:
    """POST /-/feedback/v1/items/{id}/replies: the giver answers on their own
    item, 201 with the reply."""
    context = await _context(request)
    session = _account(context)
    target = _item_id(item_id)
    _within_budget(request, context)
    with _body_room(request, MAX_REPLY_BODY_BYTES, _bad_body) as room:
        raw = await _reply_bytes(request, room)
        with _write(request, context):
            body = _reply_text(raw)
            async with request.app.state.session_factory() as db:
                # FOR KEY SHARE: the item cannot be deleted under the insert,
                # and a change of its status does not wait for it.
                own = _own_item(context, session, target).with_for_update(read=True, key_share=True)
                if await db.scalar(own) is None:
                    raise _unavailable()
                if not await _takes_a_giver_reply(db, target):
                    raise _ProblemError(409, Msg("FEEDBACK_THREAD_FULL"))
                reply = (
                    await db.execute(
                        insert(FeedbackReply)
                        .values(id=uuid.uuid4(), item_id=target, author_kind=FeedbackReplyAuthor.GIVER, body=body)
                        .returning(
                            FeedbackReply.id,
                            FeedbackReply.author_kind,
                            FeedbackReply.author_name,
                            FeedbackReply.body,
                            FeedbackReply.created_at,
                        )
                    )
                ).one()
                await db.commit()
    return JSONResponse(_mine_reply(reply), status_code=201, headers=dict(FEEDBACK_API_HEADERS))


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
    with _write(request, context):
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
    "BodyBudget",
    "FeedbackInput",
    "WriteCounter",
    "clean_title",
    "create_item",
    "delete_item",
    "giver_text",
    "highlight_anchor",
    "my_items",
    "parse_item",
    "preflight",
    "presented_token",
    "reply_to_item",
    "widget_problem",
    "write_counter_sweep",
]

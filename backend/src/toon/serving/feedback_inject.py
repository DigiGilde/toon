"""The feedback widget in a site's pages on the content host.

A site that collects feedback gets one script tag in its HTML pages: the
widget bundle (serving/feedback_widget.py) with a config that carries a page
token (serving/feedback_token.py). Only a navigation gets it, a request with
`Sec-Fetch-Dest: document`, which page script cannot send, and only on a live
or preview route the site collects feedback on: never on a `_version` view,
never on the site's own 404 page.

The token is this visitor's, so a page that carries one is never stored and
never confirmed with a 304, and every answer for such a page varies with
`Sec-Fetch-Dest`, so a cache cannot hand a copy without the widget to a
navigation (serving/response.py). The token names the visitor's content
session for feedback with their account only once they agreed, on Toon's own
consent page, to give feedback on this site that way. Without that consent it
names the session only where the gate let the visitor in through it, a page
that is not public seen without a secret link, and then for the gate alone.
A request that carries the site cookie twice gets a token without a session
in either role: a cookie set from elsewhere can shadow the visitor's own.
The config itself holds no name, no email address and no readable session
reference. A page's own scripts can still tell from it whether the visitor
consented on this site (`account`) and, on a page that is not public, whether
a secret link let them in (the link ticket in `accountUrl`, and the token's
length, which grows with each session or link it names). Where the
token carries a secret link and no consent, the way to the consent page
carries that link too, as a sealed link ticket: the key cookie stays on the
site's path, and the consent page asks the gate with the link. The token
itself never goes into an address.

A visitor who hid the button on a site carries a hide mark in the cookie
`__Secure-toon-feedback-off` (serving/feedback_consent.py). Only a value that
opens as an unexpired mark for this very site keeps the widget out, whatever
path it was set for and whatever else rides beside it under that name: a
value a page wrote itself, or one meant for another site, does nothing.

The tag goes in front of the document, right after the doctype, found in the
first 64 KiB of the decoded page by skipping what the HTML tokenizer lets pass
before one without leaving standards mode: a UTF-8 byte order mark, ASCII
whitespace, comments and bogus comments. Never in front of a doctype, which
would put the page in quirks mode, and nowhere later, where a script string,
a comment, an attribute value or a template could swallow it. The parser
moves a `defer` script found before `<html>` into the head. A page that starts
with a UTF-16 byte order mark gets no widget: the tag is ASCII.

Every page the widget goes into is decoded on every view, so the cost is
bounded before anything is decoded: the decoded size is the file's own size,
or the ISIZE trailer of the gzip copy every page kept compressed has, and a
page above `feedback_inject_max_bytes` gets no widget. The page then goes out
uncompressed in pieces of at most 512 KiB, each read in a worker thread, and
is never compressed again. A HEAD reads only the start the decision needs.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import uuid
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from toon.access.decision import AccessDecision
from toon.auth import sessions
from toon.constants import (
    FEEDBACK_HIDE_COOKIE,
    HEAD_SCOPE_KEY,
    PATH_FEEDBACK_ACCOUNT,
    PATH_FEEDBACK_API_PREFIX,
    PATH_FEEDBACK_HIDE,
    PATH_FEEDBACK_SHOW,
    PLATFORM_PREFIX,
    FeedbackIdentity,
)
from toon.ingest import precompress
from toon.serving import mime
from toon.serving.feedback_token import mint_link_ticket, mint_page_token, parse_hide_mark
from toon.serving.feedback_widget import WidgetBundle, widget_url
from toon.serving.resolution import NOT_FOUND_FILE, Stored

CONFIG_VERSION = 1

# How much of the decoded page the insertion point is looked for in.
PREFIX_BYTES = 64 * 1024

_CHUNK = 512 * 1024

_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
_WHITESPACE = b"\t\n\x0c\r "
_DOCTYPE = b"<!doctype"
_COMMENT_CLOSE = re.compile(rb"--!?>")


def feedback_page(kind: str, decision: AccessDecision, rel_path: str) -> bool:
    """Whether a file answered with 200 is a page of a feedback route: HTML,
    on a live or preview route of a site that collects feedback there, and
    not the site's own 404 page, not even asked for by name."""
    policy = decision.feedback
    return (
        policy is not None
        and policy.enabled
        and (kind == "preview" or (kind == "live" and policy.live))
        and rel_path != NOT_FOUND_FILE
        and mime.determine(rel_path).startswith("text/html")
    )


def insertion_point(prefix: bytes) -> int | None:
    """Where the tag goes in a page that starts with `prefix`: right after the
    doctype, or else in front of the first token that is neither whitespace
    nor a comment. None when `prefix` ends before that is known."""
    position = len(_UTF8_BOM) if prefix.startswith(_UTF8_BOM) else 0
    while position < len(prefix):
        if prefix[position] in _WHITESPACE:
            position += 1
            continue
        if prefix[position : position + len(_DOCTYPE)].lower() == _DOCTYPE:
            end = prefix.find(b">", position)
            return None if end < 0 else end + 1
        comment_end = _comment_end(prefix, position)
        if comment_end is not None:
            position = comment_end
            continue
        if prefix[position] == ord("<") and position + 1 == len(prefix):
            # A `<` that `prefix` ends on may still open a doctype.
            return None
        return position
    return None


def _comment_end(prefix: bytes, position: int) -> int | None:
    """Where a comment that starts at `position` ends, as the tokenizer reads
    one in front of the doctype (WHATWG HTML, 13.2.5), or None when none starts
    there. One that does not end within `prefix` runs to its end."""
    if prefix.startswith(b"<!--", position):
        # The dashes of the opener count towards `-->`, so `<!-->` and
        # `<!--->` are whole comments. Past them `-->` and `--!>` need dashes
        # of their own, and one search finds the first of either: a search
        # per closer would run to the end of the prefix for every comment
        # when one of the two never comes, quadratic in a page packed with
        # comments.
        for abrupt in (b">", b"->"):
            if prefix.startswith(abrupt, position + 4):
                return position + 4 + len(abrupt)
        close = _COMMENT_CLOSE.search(prefix, position + 4)
        return len(prefix) if close is None else close.end()
    # Bogus comments run to the next `>`: `<?`, `<!` not opening a comment or
    # a doctype, and `</` not followed by a letter (`</>` is no token at all).
    if prefix.startswith((b"<?", b"<!"), position) or (
        prefix.startswith(b"</", position) and not prefix[position + 2 : position + 3].isalpha()
    ):
        end = prefix.find(b">", position + 2)
        return len(prefix) if end < 0 else end + 1
    return None


def examine(source: Path, *, compressed: bool, max_bytes: int) -> tuple[int, int] | None:
    """The decoded size of a page and where the tag goes in it, or None when
    the page gets no widget: larger than `max_bytes`, starting with a UTF-16
    byte order mark, no insertion point in its first PREFIX_BYTES, or a file
    that does not read as it is kept. The size is known before anything is
    decoded, and nothing past the prefix is."""
    try:
        size = _gzip_size(source) if compressed else source.stat().st_size
        if size > max_bytes:
            return None
        prefix = _gzip_prefix(source) if compressed else _plain_prefix(source)
    except (OSError, zlib.error):
        return None
    if prefix.startswith(_UTF16_BOMS):
        return None
    point = insertion_point(prefix)
    return None if point is None else (size, point)


def _gzip_size(source: Path) -> int:
    # ISIZE, the last four bytes of a gzip member (RFC 1952): the size it
    # unpacks to, modulo 2^32, which no file ingest takes comes near.
    with source.open("rb") as file:
        file.seek(-4, os.SEEK_END)
        return int.from_bytes(file.read(4), "little")


def _gzip_prefix(source: Path) -> bytes:
    decompressor = zlib.decompressobj(31)
    prefix = b""
    with source.open("rb") as file:
        while len(prefix) < PREFIX_BYTES and (data := file.read(PREFIX_BYTES)):
            prefix += decompressor.decompress(data, PREFIX_BYTES - len(prefix))
    if len(prefix) < PREFIX_BYTES and not decompressor.eof:
        raise OSError(f"gzip copy ends before its stream does: {source}")
    return prefix


def _plain_prefix(source: Path) -> bytes:
    with source.open("rb") as file:
        return file.read(PREFIX_BYTES)


def page_config(
    *,
    token: str,
    identity: FeedbackIdentity,
    account: bool,
    return_to: str,
    admin_origin: str,
    retention_days: int,
    screenshot_max_bytes: int,
    link: str | None = None,
) -> dict[str, object]:
    """The widget's config. `return_to` is the page's path and query without
    any `key`, `admin_origin` the admin host's origin (TOON_BASE_URL),
    `account` whether the token carries a consented session,
    `screenshot_max_bytes` the largest screenshot the widget API takes, which
    the widget encodes under, and `link` the link ticket that goes along on
    the way to the consent page, when there is one."""
    back = urlencode({"returnTo": return_to})
    to_consent = back if link is None else urlencode({"returnTo": return_to, "link": link})
    return {
        "v": CONFIG_VERSION,
        "token": token,
        "api": PATH_FEEDBACK_API_PREFIX.rstrip("/"),
        "identity": identity.value,
        "account": account,
        "accountUrl": f"{PATH_FEEDBACK_ACCOUNT}?{to_consent}",
        "hideUrl": PATH_FEEDBACK_HIDE,
        "showUrl": f"{PATH_FEEDBACK_SHOW}?{back}",
        "privacyUrl": f"{admin_origin}{PLATFORM_PREFIX}/privacy",
        "retentionDays": retention_days,
        "screenshotMaxBytes": screenshot_max_bytes,
    }


def script_tag(bundle: WidgetBundle, config: dict[str, object]) -> bytes:
    """The tag that goes into the page, ASCII only: the config as base64url
    without padding over its UTF-8 JSON."""
    data = base64.urlsafe_b64encode(json.dumps(config, separators=(",", ":")).encode("utf-8")).rstrip(b"=")
    return b'<script src="%s" defer data-toon-feedback="%s"></script>' % (widget_url(bundle).encode("ascii"), data)


@dataclass(frozen=True)
class Injection:
    """A page that gets the widget: decoded from `source`, unpacked from it
    when `compressed`, it is `size` bytes long and `tag` goes in at `point`."""

    source: Path
    compressed: bool
    size: int
    point: int
    tag: bytes


def button_hidden(request: Request, secret: str, site_id: uuid.UUID, *, now: int) -> bool:
    """Whether some value of the hide cookie this request carries opens as an
    unexpired hide mark for this site. Every value counts: the Cookie header
    names no path, and `request.cookies` keeps one value of a name sent
    twice."""
    for value in sessions.raw_cookie_values(request, FEEDBACK_HIDE_COOKIE):
        mark = parse_hide_mark(secret, value, now=now)
        if mark is not None and mark.site_id == site_id:
            return True
    return False


async def prepare(
    request: Request,
    decision: AccessDecision,
    *,
    kind: str,
    ref: str | None,
    key_cookie: str | None,
    stored: Stored,
    return_to: str,
) -> Injection | None:
    """The widget for this view of a page of a feedback route, or None when
    it gets none: the visitor hid the button on this site, the bundle is not
    there, the page does not take it (`examine`), or no token can be minted
    for it. Asks the database nothing, and minting the token stores nothing."""
    bundle: WidgetBundle | None = getattr(request.app.state, "feedback_widget", None)
    policy = decision.feedback
    site_id = decision.site_id
    if bundle is None or policy is None or site_id is None:
        return None
    settings = request.app.state.settings
    now = int(time.time())
    if button_hidden(request, settings.session_secret, site_id, now=now):
        return None
    # A page kept compressed is decoded from its gzip copy, which is always
    # there, whatever copy the request would have got.
    if stored.plain is not None:
        source, compressed = stored.plain, False
    else:
        source, compressed = stored.encoded[precompress.GZIP], True
    examined = await asyncio.to_thread(
        examine, source, compressed=compressed, max_bytes=settings.feedback_inject_max_bytes
    )
    if examined is None:
        return None
    session = sessions.content_session_from_request(request)
    if session is not None and sessions.content_cookie_is_duplicated(request):
        session = None
    consented = session is not None and str(site_id) in session.feedback_sites
    # What the gate let the visitor in with when the page is not public and
    # no secret link did it: the session.
    access = decision.effective_access
    let_in = access is not None and not access.is_public and decision.key_selector is None
    key_id = key_cookie if decision.key_selector is not None and key_cookie else None
    try:
        token = mint_page_token(
            settings.session_secret,
            site_id=site_id,
            kind=kind,
            ref=ref,
            version_id=decision.version_id,
            session_id=session.id if session is not None and consented else None,
            access_session_id=session.id if session is not None and not consented and let_in else None,
            key_id=key_id,
            now=now,
        )
        # Only beside the way to the consent page: the widget builds the way
        # to stop from the same address, and stopping needs no link.
        link = (
            mint_link_ticket(settings.session_secret, key_id=key_id, site_id=site_id, now=now)
            if key_id is not None and not consented
            else None
        )
    except ValueError:
        return None
    config = page_config(
        token=token,
        identity=policy.identity,
        account=consented,
        return_to=return_to,
        admin_origin=(settings.base_url or "").rstrip("/"),
        retention_days=settings.feedback_resolved_retention_days,
        screenshot_max_bytes=settings.feedback_screenshot_max_bytes,
        link=link,
    )
    size, point = examined
    return Injection(source=source, compressed=compressed, size=size, point=point, tag=script_tag(bundle, config))


def splice(pieces: Iterable[bytes], *, size: int, point: int, tag: bytes) -> Iterator[bytes]:
    """The page from `pieces`, `size` bytes long, with `tag` at `point`.
    Raises as soon as the page turns out to be another size than the one the
    Content-Length was built from: a body that ran past it, or ended short of
    it, would be taken for the page."""
    position = 0
    pending = True
    for piece in pieces:
        end = position + len(piece)
        if end > size:
            raise OSError(f"page runs past the {size} bytes it was examined at")
        if pending and point <= end:
            cut = point - position
            yield piece[:cut] + tag + piece[cut:]
            pending = False
        else:
            yield piece
        position = end
    if position != size:
        raise OSError(f"page ends at {position} of the {size} bytes it was examined at")


def _decoded(source: Path, compressed: bool) -> Iterator[bytes]:
    if compressed:
        yield from precompress.gunzip(source)
        return
    with source.open("rb") as file:
        while piece := file.read(_CHUNK):
            yield piece


class InjectedResponse(StreamingResponse):
    """A page with the widget in it, decoded and spliced on the way out, one
    piece of at most 512 KiB (the one with the tag a little more) per worker
    thread step. A HEAD gets the headers without anything being read."""

    def __init__(self, injection: Injection, *, headers: dict[str, str], media_type: str) -> None:
        pieces = _decoded(injection.source, injection.compressed)
        super().__init__(
            splice(pieces, size=injection.size, point=injection.point, tag=injection.tag),
            status_code=200,
            headers=headers,
            media_type=media_type,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get(HEAD_SCOPE_KEY):
            await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        await super().__call__(scope, receive, send)


__all__ = [
    "CONFIG_VERSION",
    "PREFIX_BYTES",
    "InjectedResponse",
    "Injection",
    "button_hidden",
    "examine",
    "feedback_page",
    "insertion_point",
    "page_config",
    "prepare",
    "script_tag",
    "splice",
]

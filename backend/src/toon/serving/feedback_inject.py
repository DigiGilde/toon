"""The feedback widget in a site's pages on the content host (design.md
§16.2, §16.3).

A navigation (`Sec-Fetch-Dest: document`, which page script cannot send) to an
HTML page of a feedback route gets one script tag with the widget's config and
a page token for this visitor and this view. Such a page is never stored and
never confirmed with a 304, and every answer for it varies with
`Sec-Fetch-Dest`, so a cache cannot hand a copy without the widget, or with
someone else's token, to a navigation.

The tag goes right after the doctype, found by skipping only what the HTML
tokenizer lets pass before one without leaving standards mode. In front of a
doctype it would put the page in quirks mode, and further on a script string,
a comment, an attribute value or a template could swallow it.

Every page the widget goes into is decoded on every view, so its size is
known before anything is decoded (the gzip ISIZE trailer for a page kept
compressed only), and only the first PREFIX_BYTES are examined.
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
from collections.abc import Iterable, Iterator, Sequence
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
    FEEDBACK_INJECT_MAX_BYTES,
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
from toon.serving.feedback_editor import FeedbackEditor, frame_url
from toon.serving.feedback_token import mint_link_ticket, mint_page_token, parse_hide_mark
from toon.serving.feedback_widget import WidgetBundle, widget_url
from toon.serving.resolution import NOT_FOUND_FILE, Stored

CONFIG_VERSION = 1

# How much of the decoded page the insertion point is looked for in.
PREFIX_BYTES = 64 * 1024

# The longest config the widget reads: MAX_RAW_LENGTH in
# frontend/src/feedback-widget/config.ts.
MAX_CONFIG_LENGTH = 16384

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
    return_to: str | None,
    admin_origin: str,
    screenshot_max_bytes: int,
    link: str | None = None,
    editor_url: str | None = None,
) -> dict[str, object]:
    """The widget's config. `return_to` is the way back to the page
    (`feedback_consent.ways_back`), or None when there is none: the config
    then has no way to the consent page and no show link, and the widget
    offers neither feedback with an account nor hiding the button.
    `admin_origin` is the admin host's origin (TOON_BASE_URL), `account`
    whether the token carries a consented session, `screenshot_max_bytes` the
    largest screenshot the widget API takes, which the widget encodes under,
    and `link` the link ticket that goes along on the way to the consent
    page, when there is one. `editor_url` is the editor frame's address, when
    Toon has an editor: without one the widget offers a plain text field."""
    config: dict[str, object] = {
        "v": CONFIG_VERSION,
        "token": token,
        "api": PATH_FEEDBACK_API_PREFIX.rstrip("/"),
        "identity": identity.value,
        "account": account,
        "hideUrl": PATH_FEEDBACK_HIDE,
        "privacyUrl": f"{admin_origin}{PLATFORM_PREFIX}/privacy",
        "screenshotMaxBytes": screenshot_max_bytes,
    }
    if return_to is not None:
        back = urlencode({"returnTo": return_to})
        to_consent = back if link is None else urlencode({"returnTo": return_to, "link": link})
        config["accountUrl"] = f"{PATH_FEEDBACK_ACCOUNT}?{to_consent}"
        config["showUrl"] = f"{PATH_FEEDBACK_SHOW}?{back}"
    if editor_url is not None:
        config["editorUrl"] = editor_url
    return config


def script_tag(bundle: WidgetBundle, config: dict[str, object]) -> bytes | None:
    """The tag that goes into the page, ASCII only: the config as base64url
    without padding over its UTF-8 JSON. None when that is longer than the
    widget reads."""
    data = base64.urlsafe_b64encode(json.dumps(config, separators=(",", ":")).encode("utf-8")).rstrip(b"=")
    if len(data) > MAX_CONFIG_LENGTH:
        return None
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
    ways_back: Sequence[str],
) -> Injection | None:
    """The widget for this view of a page of a feedback route, or None when
    it gets none: TOON_BASE_URL is not set, the visitor hid the button on this
    site, the bundle is not there, the page does not take it (`examine`), no
    token can be minted for it, or its config is longer than the widget reads
    even without a way back. The config takes the first of `ways_back` it
    fits the widget with, else none. Asks the database nothing, and minting
    the token stores nothing."""
    bundle: WidgetBundle | None = getattr(request.app.state, "feedback_widget", None)
    editor: FeedbackEditor | None = getattr(request.app.state, "feedback_editor", None)
    policy = decision.feedback
    site_id = decision.site_id
    settings = request.app.state.settings
    # The widget refuses a privacy link that is not an absolute URL.
    admin_origin: str | None = settings.base_url
    if bundle is None or policy is None or site_id is None or not admin_origin:
        return None
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
        examine, source, compressed=compressed, max_bytes=FEEDBACK_INJECT_MAX_BYTES
    )
    if examined is None:
        return None
    session = sessions.content_session_from_request(request)
    # A second site cookie, set from elsewhere, can shadow the visitor's own.
    if session is not None and len(sessions.raw_cookie_values(request, sessions.CONTENT_SESSION_COOKIE)) > 1:
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
            # Only reached where feedback_page(kind, ...) holds, which admits live and preview.
            kind=kind,  # ty: ignore[invalid-argument-type]
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
    for return_to in (*ways_back, None):
        config = page_config(
            token=token,
            identity=policy.identity,
            account=consented,
            return_to=return_to,
            admin_origin=admin_origin.rstrip("/"),
            screenshot_max_bytes=settings.feedback_screenshot_max_bytes,
            link=link,
            editor_url=None if editor is None else frame_url(editor),
        )
        tag = script_tag(bundle, config)
        if tag is not None:
            break
    else:
        return None
    size, point = examined
    return Injection(source=source, compressed=compressed, size=size, point=point, tag=tag)


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
    "MAX_CONFIG_LENGTH",
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

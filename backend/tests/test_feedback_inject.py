"""Tests for serving/feedback_inject.py and its place in the serving router:
the feedback widget in a site's pages (spec §16.2, §16.3).

First the pieces that need no database: the insertion point in every context
the tokenizer knows in front of a doctype, the decoded size and the limit,
the splice, the config and the tag, and the streaming response. Then the
router against PostgreSQL: every condition on and off, the hide mark among
them, the headers of a page with and without the widget, HEAD, 304 and Range,
the token read back, and the cost of examining a page. Last the
whole content host at once: the consent page and the login after it, the
widget API on the token a page view handed out, and hiding and showing the
button.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import re
import time
import uuid
import zlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from helpers_oidc import MockIdP, complete_login, make_client_jwk, make_oidc_client, set_content_session_cookie
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_feedback_api import assert_problem, auth, item, parts
from test_feedback_api import make_app as make_feedback_app
from test_feedback_consent import is_neutral_404
from test_serving import (
    BASE_URL,
    FULL_CSP,
    SANDBOX_CSP,
    _header_list,
    _make_app,
    _on_the_wire,
    _raw_request,
    _store_compressed,
    make_settings,
)

from toon.access import keys
from toon.auth.sessions import (
    CONTENT_ANCHOR_COOKIE,
    CONTENT_ANCHOR_PATH,
    CONTENT_SESSION_COOKIE,
    KEY_COOKIE,
    SessionKind,
    sign,
    sign_key_cookie,
)
from toon.constants import (
    FEEDBACK_HIDE_COOKIE,
    FEEDBACK_INJECT_MAX_BYTES,
    HEAD_SCOPE_KEY,
    PATH_CONTENT_LOGIN,
    PATH_FEEDBACK_ACCOUNT,
    PATH_FEEDBACK_HIDE,
    AccessBase,
    FeedbackIdentity,
    Role,
)
from toon.head_requests import ContentHeadMiddleware
from toon.ingest.store import ContentStore, version_ref
from toon.models.feedback import FeedbackItem
from toon.models.identity import Group, GroupMember, Member, MemberStatus
from toon.models.publication import Preview, Site, Version, VersionTarget
from toon.platform import pages
from toon.serving import feedback_consent, feedback_inject
from toon.serving import router as serving_router_module
from toon.serving.feedback_editor import frame_url, load_feedback_editor
from toon.serving.feedback_inject import (
    MAX_CONFIG_LENGTH,
    PREFIX_BYTES,
    InjectedResponse,
    Injection,
    examine,
    insertion_point,
    page_config,
    script_tag,
    splice,
)
from toon.serving.feedback_token import (
    HIDE_LIFETIME_S,
    MAX_RETURN_TO_LENGTH,
    TOKEN_LIFETIME_S,
    LinkTicket,
    PageToken,
    mint_consent_nonce,
    mint_hide_mark,
    mint_page_token,
    parse_link_ticket,
    parse_page_token,
)
from toon.serving.feedback_widget import WidgetBundle, load_widget_bundle
from toon.serving.router import router as serving_router

ADMIN_ORIGIN = "https://beheer.toon.example"
# make_settings's TOON_SESSION_SECRET, which seals the page token.
SECRET = "sessie-geheim-van-minstens-32-bytes!"
OTHER_SECRET = "ander-geheim-van-minstens-32-bytes!!"
BUNDLE = b'/*! Toon feedback widget. */(()=>{"use strict";})();\n'
BUNDLE_HASH = hashlib.sha256(BUNDLE).hexdigest()[:16]
NAVIGATION = {"Sec-Fetch-Dest": "document"}
FORM = {**NAVIGATION, "Content-Type": "application/x-www-form-urlencoded"}
ITEMS = "/-/feedback/v1/items"

DOC = (
    b"<!DOCTYPE html>\n"
    b'<html lang="nl">\n'
    b"<head><title>Pagina</title><script>var einde = '</body>';</script></head>\n"
    b'<body><!-- </body> --><p title="</body>">Tekst</p></body>\n'
    b"</html>\n"
)
DOCTYPE_END = len(b"<!DOCTYPE html>")
PREVIEW_DOC = b"<!doctype html>\n<title>Preview</title>\n<p>Voorproef</p>\n"
PREVIEW_DOCTYPE_END = len(b"<!doctype html>")
PAGE_404 = b"<!doctype html>\n<h1>Niet hier</h1>\n"
NO_DOCTYPE = b"<html><body><p>Zonder doctype</p></body></html>\n"
UTF16 = "\ufeff<!doctype html><p>UTF-16</p>".encode("utf-16-le")
LONG_COMMENT = b"<!-- " + b"x" * PREFIX_BYTES + b" -->\n" + DOC
# A page whose path, percent-encoded below its site, is 2048 characters: the
# longest `page.path` the widget API takes, one more than a way back may be.
# No file name is past the 255 bytes a file system takes.
LONG_PATH = "\u00e9" * 120 + "/" + "\u00e9" * 120 + "/" + "\u00e9" * 97 + "ab.html"

TAG = b'<script src="/x.js"></script>'
TAG_RE = re.compile(
    rb'<script src="/-/feedback/widget\.([0-9a-f]{16})\.js" defer data-toon-feedback="([A-Za-z0-9_-]+)"></script>'
)
CONFIG_KEYS = {
    "v",
    "token",
    "api",
    "identity",
    "account",
    "accountUrl",
    "hideUrl",
    "showUrl",
    "privacyUrl",
    "screenshotMaxBytes",
}

# The header set of a page with the widget, values aside.
INJECTED_HEADERS = {
    "content-type",
    "cache-control",
    "x-content-type-options",
    "content-security-policy",
    "referrer-policy",
    "vary",
    "content-length",
}


def _tag_in(body: bytes, original: bytes, point: int) -> bytes:
    """The tag that went into `body` at `point`. Everything around it has to
    be `original`, byte for byte."""
    extra = len(body) - len(original)
    tag = body[point : point + extra]
    assert TAG_RE.fullmatch(tag), body[:300]
    assert body[:point] + body[point + extra :] == original
    return tag


def _config(tag: bytes) -> dict:
    match = TAG_RE.fullmatch(tag)
    assert match is not None
    data = match.group(2)
    return json.loads(base64.urlsafe_b64decode(data + b"=" * (-len(data) % 4)))


def _token(config: dict) -> PageToken:
    token = parse_page_token(SECRET, config["token"], now=int(time.time()))
    assert token is not None
    return token


def _page_file(directory: Path, content: bytes, compressed: bool) -> Path:
    path = directory / ("pagina.html.gz" if compressed else "pagina.html")
    path.write_bytes(gzip.compress(content, mtime=0) if compressed else content)
    return path


# --- Without a database ----------------------------------------------------

MARK = b"^"

# Each document with MARK where the tag goes.
PLACES = [
    pytest.param(b"<!doctype html>^<html><head></head><body></body></html>", id="doctype"),
    pytest.param(b"<!DOCTYPE html>^\n<html>", id="uppercase-doctype"),
    pytest.param(b"<!DocType html>^<p>", id="mixed-case-doctype"),
    pytest.param(b"<!DOCTYPEhtml>^<p>", id="doctype-without-space"),
    pytest.param(
        b'<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd">^\n<html>',
        id="legacy-doctype",
    ),
    pytest.param(b'<!doctype html "a>^b">', id="the-first-gt-ends-a-doctype"),
    pytest.param(b"<!doctype html>^", id="doctype-alone"),
    pytest.param(b"\xef\xbb\xbf<!doctype html>^<html>", id="utf8-bom"),
    pytest.param(b"\xef\xbb\xbf^<p>", id="utf8-bom-without-doctype"),
    pytest.param(b" \t\n\r\x0c<!doctype html>^<html>", id="whitespace"),
    pytest.param(b"<!-- een --> <!-- twee -->\n<!doctype html>^<html>", id="comments"),
    pytest.param(b'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>^<html>', id="xml-declaration"),
    pytest.param(b"<!--><!doctype html>^<html>", id="abruptly-closed-comment"),
    pytest.param(b"<!---><!doctype html>^<html>", id="abruptly-closed-dash-comment"),
    pytest.param(b"<!----><!doctype html>^<html>", id="empty-comment"),
    pytest.param(b"<!-- a --!><!doctype html>^<html>", id="comment-closed-with-a-bang"),
    pytest.param(b"<!-- a ---><!doctype html>^<html>", id="comment-closed-with-three-dashes"),
    pytest.param(b"<!--!><!doctype html>-->^<html>", id="a-bang-after-the-opener-ends-nothing"),
    pytest.param(b"<!---!><!doctype html>-->^<html>", id="a-dash-bang-after-the-opener-ends-nothing"),
    pytest.param(b"<!-- <!--> ^--><!doctype html>", id="a-nested-opener-closes-the-comment"),
    pytest.param(b"<!-- </body> --><!doctype html>^<html>", id="end-tag-in-a-comment"),
    pytest.param(b"<!x><!doctype html>^<html>", id="bogus-comment"),
    pytest.param(b"<!><!doctype html>^<html>", id="empty-bogus-comment"),
    pytest.param(b"<![CDATA[x]]><!doctype html>^<html>", id="cdata-outside-foreign-content"),
    pytest.param(b"</ x><!doctype html>^<html>", id="bogus-end-tag"),
    pytest.param(b"</><!doctype html>^<html>", id="empty-end-tag"),
    pytest.param(b"^<html><head><title>x</title>", id="no-doctype"),
    pytest.param(b"\n<!-- c -->\n^<html>", id="no-doctype-after-a-comment"),
    pytest.param(b"^Hallo", id="text"),
    pytest.param(b"^</p><!doctype html>", id="end-tag-in-front-of-the-doctype"),
    pytest.param(b"^< 3<!doctype html>", id="less-than-as-text"),
    pytest.param(b" ^\xef\xbb\xbf<!doctype html>", id="a-bom-after-whitespace-is-text"),
    pytest.param(
        b"<!doctype html>^<script>var s = '</body>';</script><!-- </body> --><p title=\"</body>\">",
        id="end-tags-in-a-script-string-a-comment-and-an-attribute",
    ),
    pytest.param(
        b"<!doctype html>^<textarea></body></textarea><template></body></template>",
        id="end-tags-in-rcdata-and-a-template",
    ),
]

NO_PLACE = [
    pytest.param(b"", id="empty"),
    pytest.param(b" \n\t", id="whitespace-only"),
    pytest.param(b"\xef\xbb\xbf", id="bom-only"),
    pytest.param(b"<!-- alleen commentaar -->", id="comment-only"),
    pytest.param(b"<!-- zonder einde", id="unterminated-comment"),
    pytest.param(b"<!--", id="comment-opener-only"),
    pytest.param(b"<!-", id="cut-comment-opener"),
    pytest.param(b"<!doctype html", id="unterminated-doctype"),
    pytest.param(b"<!DOCTY", id="cut-doctype"),
    pytest.param(b'<?xml version="1.0"', id="unterminated-bogus-comment"),
    pytest.param(b"</ x", id="unterminated-bogus-end-tag"),
    pytest.param(b"</", id="cut-end-tag"),
    pytest.param(b"<", id="cut-tag"),
    pytest.param(b"<!-- x --> <", id="cut-tag-after-a-comment"),
]


class TestInsertionPoint:
    """Right after the doctype, found the way the HTML tokenizer reads what
    comes in front of one: never in front of it, which would mean quirks
    mode, and never anywhere a script string, a comment or an attribute
    value could hold the tag."""

    @pytest.mark.parametrize("marked", PLACES)
    def test_the_tag_goes_where_the_tokenizer_has_the_first_token(self, marked: bytes):
        assert marked.count(MARK) == 1
        assert insertion_point(marked.replace(MARK, b"")) == marked.index(MARK)

    @pytest.mark.parametrize("prefix", NO_PLACE)
    def test_no_place_while_the_prefix_ends_first(self, prefix: bytes):
        """What follows the end of what was examined is not known: a comment
        or a doctype that has not ended yet, or a `<` that may open one."""
        assert insertion_point(prefix) is None


class TestExamine:
    def test_a_plain_page_is_measured_by_its_size(self, tmp_path: Path):
        page = _page_file(tmp_path, DOC, compressed=False)
        assert examine(page, compressed=False, max_bytes=len(DOC)) == (len(DOC), DOCTYPE_END)

    def test_a_page_kept_compressed_is_measured_by_its_gzip_trailer(self, tmp_path: Path):
        """ISIZE is the size, read before anything is decoded: a trailer that
        says otherwise is believed, because nothing past the start is decoded
        to check it."""
        large = DOC + b"<p>tekst</p>\n" * 100_000
        page = _page_file(tmp_path, large, compressed=True)
        assert examine(page, compressed=True, max_bytes=len(large)) == (len(large), DOCTYPE_END)
        copy = page.read_bytes()
        page.write_bytes(copy[:-4] + (12345).to_bytes(4, "little"))
        assert examine(page, compressed=True, max_bytes=len(large)) == (12345, DOCTYPE_END)

    def test_only_the_prefix_is_decoded(self, tmp_path: Path, monkeypatch):
        """A page that compresses badly, so its copy is longer than one read."""
        noise = b"".join(hashlib.sha256(b"%d" % n).hexdigest().encode() for n in range(6250))
        large = DOC + b"<p>" + noise + b"</p>"
        page = _page_file(tmp_path, large, compressed=True)
        assert page.stat().st_size > 2 * PREFIX_BYTES
        decoded: list[int] = []
        real = zlib.decompressobj

        class Counting:
            def __init__(self, wbits: int) -> None:
                self._decompressor = real(wbits)

            def decompress(self, data: bytes, max_length: int = 0) -> bytes:
                out = self._decompressor.decompress(data, max_length)
                decoded.append(len(out))
                return out

            @property
            def eof(self) -> bool:
                return self._decompressor.eof

        monkeypatch.setattr(zlib, "decompressobj", Counting)
        assert examine(page, compressed=True, max_bytes=len(large)) == (len(large), DOCTYPE_END)
        assert sum(decoded) == PREFIX_BYTES

    def test_a_page_above_the_limit_is_not_decoded_at_all(self, tmp_path: Path, monkeypatch):
        page = _page_file(tmp_path, DOC, compressed=True)

        def refuse(wbits: int):
            raise AssertionError(f"decoded {page} above the limit")

        monkeypatch.setattr(zlib, "decompressobj", refuse)
        assert examine(page, compressed=True, max_bytes=len(DOC) - 1) is None

    @pytest.mark.parametrize("compressed", [False, True])
    def test_the_limit_takes_a_page_of_exactly_its_size(self, tmp_path: Path, compressed: bool):
        page = _page_file(tmp_path, DOC, compressed)
        assert examine(page, compressed=compressed, max_bytes=len(DOC)) == (len(DOC), DOCTYPE_END)
        assert examine(page, compressed=compressed, max_bytes=len(DOC) - 1) is None

    @pytest.mark.parametrize(("bom", "codec"), [(b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")])
    @pytest.mark.parametrize("compressed", [False, True])
    def test_a_utf16_page_gets_no_widget(self, tmp_path: Path, bom: bytes, codec: str, compressed: bool):
        content = bom + "<!doctype html><p>UTF-16</p>".encode(codec)
        page = _page_file(tmp_path, content, compressed)
        assert examine(page, compressed=compressed, max_bytes=len(content)) is None

    @pytest.mark.parametrize("compressed", [False, True])
    def test_a_place_on_the_last_byte_examined_counts_and_one_further_does_not(
        self, tmp_path: Path, compressed: bool
    ):
        doctype = b"<!doctype html>"
        filler = b"x" * (PREFIX_BYTES - len(b"<!---->") - len(doctype))
        within = b"<!--" + filler + b"-->" + doctype + b"<p>tekst</p>" * 10
        assert within.index(doctype) + len(doctype) == PREFIX_BYTES
        page = _page_file(tmp_path, within, compressed)
        assert examine(page, compressed=compressed, max_bytes=len(within) + 1) == (len(within), PREFIX_BYTES)
        beyond = b"<!--x" + within[len(b"<!--") :]
        page = _page_file(tmp_path, beyond, compressed)
        assert examine(page, compressed=compressed, max_bytes=len(beyond)) is None

    @pytest.mark.parametrize("compressed", [False, True])
    def test_no_place_in_the_first_64_kib(self, tmp_path: Path, compressed: bool):
        page = _page_file(tmp_path, LONG_COMMENT, compressed)
        assert examine(page, compressed=compressed, max_bytes=len(LONG_COMMENT)) is None

    @pytest.mark.parametrize(
        "content",
        [b"\x1f\x8b", b"geen gzip" + (100).to_bytes(4, "little")],
        ids=["too-short-for-a-trailer", "no-gzip"],
    )
    def test_a_copy_that_is_no_usable_gzip(self, tmp_path: Path, content: bytes):
        page = tmp_path / "kapot.html.gz"
        page.write_bytes(content)
        assert examine(page, compressed=True, max_bytes=1024) is None

    def test_a_copy_that_ends_before_its_stream(self, tmp_path: Path):
        """What it holds decodes, doctype included, but the stream never
        ends: no page to splice into. The limit is out of the way of
        whatever its last four bytes say."""
        copy = gzip.compress(DOC, mtime=0)
        page = tmp_path / "afgekapt.html.gz"
        page.write_bytes(copy[: len(copy) // 2])
        assert zlib.decompressobj(31).decompress(page.read_bytes()).startswith(b"<!DOCTYPE html>")
        assert examine(page, compressed=True, max_bytes=2**32) is None

    @pytest.mark.parametrize("compressed", [False, True])
    def test_a_file_that_is_gone(self, tmp_path: Path, compressed: bool):
        assert examine(tmp_path / "weg.html", compressed=compressed, max_bytes=1024) is None


class TestSplice:
    PAGE = b"abcdefghijkl"

    @pytest.mark.parametrize("point", [0, 1, 5, 6, 7, 11, 12])
    @pytest.mark.parametrize("cuts", [(12,), (6, 12), (1, 6, 7, 12), (3, 4, 12)])
    def test_the_tag_goes_in_at_its_point(self, point: int, cuts: tuple[int, ...]):
        pieces = [self.PAGE[start:end] for start, end in zip((0, *cuts[:-1]), cuts, strict=True)]
        out = list(splice(pieces, size=len(self.PAGE), point=point, tag=TAG))
        assert b"".join(out) == self.PAGE[:point] + TAG + self.PAGE[point:]
        assert len(out) == len(pieces)

    def test_only_the_piece_with_the_point_changes(self):
        pieces = [b"a" * 10, b"b" * 10, b"c" * 10]
        assert list(splice(pieces, size=30, point=15, tag=TAG)) == [b"a" * 10, b"b" * 5 + TAG + b"b" * 5, b"c" * 10]

    def test_a_page_that_runs_past_its_size_stops_before_the_excess(self):
        out = splice([b"abc", b"def"], size=4, point=0, tag=TAG)
        assert next(out) == TAG + b"abc"
        with pytest.raises(OSError, match="runs past the 4 bytes"):
            next(out)

    def test_a_page_that_ends_short_of_its_size_fails_at_its_end(self):
        out = splice([b"abc"], size=4, point=3, tag=TAG)
        assert next(out) == b"abc" + TAG
        with pytest.raises(OSError, match="ends at 3 of the 4 bytes"):
            next(out)


class TestConfigAndTag:
    BACK = "returnTo=%2Faurora%2Fsite%2Fpagina.html%3Fx%3D1"

    @pytest.mark.parametrize(
        ("extra", "changed"),
        [
            pytest.param({}, {}, id="plain"),
            pytest.param(
                {"link": "kaartje-123_-"},
                {"accountUrl": f"/-/feedback/account?{BACK}&link=kaartje-123_-"},
                id="a-link-ticket-on-the-way-to-the-consent-page-alone",
            ),
            pytest.param(
                {"editor_url": "/-/feedback/editor.0123456789abcdef.html"},
                {"editorUrl": "/-/feedback/editor.0123456789abcdef.html"},
                id="the-editor-frame",
            ),
        ],
    )
    def test_the_config(self, extra: dict[str, str], changed: dict[str, str]):
        config = page_config(
            token="token",
            identity=FeedbackIdentity.ANONYMOUS,
            account=True,
            return_to="/aurora/site/pagina.html?x=1",
            admin_origin="https://beheer.example",
            screenshot_max_bytes=1048576,
            **extra,
        )
        assert config == {
            "v": 1,
            "token": "token",
            "api": "/-/feedback/v1",
            "identity": "anonymous",
            "account": True,
            "accountUrl": f"/-/feedback/account?{self.BACK}",
            "hideUrl": "/-/feedback/hide",
            "showUrl": f"/-/feedback/show?{self.BACK}",
            "privacyUrl": "https://beheer.example/-/privacy",
            "screenshotMaxBytes": 1048576,
            **changed,
        }

    def test_without_a_way_back_no_way_to_the_consent_page_and_no_show_link(self):
        config = page_config(
            token="token",
            identity=FeedbackIdentity.NAME,
            account=False,
            return_to=None,
            admin_origin="https://beheer.example",
            screenshot_max_bytes=1048576,
            link="kaartje-123_-",
        )
        assert config == {
            "v": 1,
            "token": "token",
            "api": "/-/feedback/v1",
            "identity": "name",
            "account": False,
            "hideUrl": "/-/feedback/hide",
            "privacyUrl": "https://beheer.example/-/privacy",
            "screenshotMaxBytes": 1048576,
        }

    def test_the_tag_is_ascii_and_carries_the_config_whatever_is_in_it(self):
        bundle = WidgetBundle(content=BUNDLE, hash=BUNDLE_HASH)
        config = {"naam": ' Café "☕" </script> ', "leeg": "", "lijst": [1, None, True]}
        tag = script_tag(bundle, config)
        assert tag.isascii()
        match = TAG_RE.fullmatch(tag)
        assert match is not None
        assert match.group(1) == BUNDLE_HASH.encode()
        assert _config(tag) == config

    def test_no_tag_for_a_config_longer_than_the_widget_reads(self):
        bundle = WidgetBundle(content=BUNDLE, hash=BUNDLE_HASH)
        # `{"p":""}` is 8 bytes, and 3 bytes are 4 base64url characters.
        longest = {"p": "x" * (MAX_CONFIG_LENGTH * 3 // 4 - 8)}
        tag = script_tag(bundle, longest)
        assert tag is not None
        match = TAG_RE.fullmatch(tag)
        assert match is not None
        assert len(match.group(2)) == MAX_CONFIG_LENGTH
        assert script_tag(bundle, {"p": longest["p"] + "x"}) is None


class TestInjectedResponse:
    """The streaming itself, against a bare ASGI send."""

    @staticmethod
    async def _run(response: InjectedResponse, scope: dict) -> list[dict]:
        sent: list[dict] = []

        async def receive() -> dict:
            # A client that stays connected while the answer goes out.
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            sent.append(message)

        await response({"type": "http", "method": "GET", "headers": [], "extensions": {}, **scope}, receive, send)
        return sent

    @staticmethod
    def _response(source: Path, *, compressed: bool, size: int, point: int) -> InjectedResponse:
        injection = Injection(source=source, compressed=compressed, size=size, point=point, tag=TAG)
        headers = {"Content-Length": str(size + len(TAG))}
        return InjectedResponse(injection, headers=headers, media_type="text/html; charset=utf-8")

    @pytest.mark.parametrize("compressed", [False, True])
    async def test_a_large_page_goes_out_in_bounded_pieces(self, tmp_path: Path, compressed: bool):
        content = DOC + b"<p>tekst</p>\n" * 200_000
        page = _page_file(tmp_path, content, compressed)
        sent = await self._run(
            self._response(page, compressed=compressed, size=len(content), point=DOCTYPE_END), {}
        )
        assert sent[0]["status"] == 200
        assert (b"content-length", str(len(content) + len(TAG)).encode()) in sent[0]["headers"]
        bodies = [m["body"] for m in sent if m["type"] == "http.response.body" and m["body"]]
        assert b"".join(bodies) == content[:DOCTYPE_END] + TAG + content[DOCTYPE_END:]
        assert len(bodies) >= 5
        assert all(len(body) <= 512 * 1024 + len(TAG) for body in bodies)

    async def test_head_sends_the_headers_without_reading_anything(self, tmp_path: Path):
        response = self._response(tmp_path / "bestaat-niet.html", compressed=False, size=10, point=0)
        sent = await self._run(response, {HEAD_SCOPE_KEY: True})
        assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
        assert (b"content-length", str(10 + len(TAG)).encode()) in sent[0]["headers"]
        assert sent[1] == {"type": "http.response.body", "body": b"", "more_body": False}

    async def test_a_get_does_read(self, tmp_path: Path):
        """The counterpart of the HEAD above: the missing file is noticed."""
        response = self._response(tmp_path / "bestaat-niet.html", compressed=False, size=10, point=0)
        with pytest.raises(FileNotFoundError):
            await self._run(response, {})

    async def test_a_page_that_is_not_the_size_it_was_examined_at_breaks_off(self, tmp_path: Path):
        """A trailer that promised more than the copy holds: the answer stops
        rather than end as if the page were whole."""
        page = _page_file(tmp_path, DOC, compressed=True)
        response = self._response(page, compressed=True, size=len(DOC) + 5, point=DOCTYPE_END)
        with pytest.raises(OSError, match="ends at"):
            await self._run(response, {})


# --- Through the router, against PostgreSQL -------------------------------

SITES: dict[str, dict] = {
    # Feedback with a name, on previews and the live site.
    "feedback": {"feedback_enabled": True, "feedback_identity": FeedbackIdentity.NAME, "feedback_live": True},
    # Feedback on previews only.
    "alleen-preview": {
        "feedback_enabled": True,
        "feedback_identity": FeedbackIdentity.ANONYMOUS,
        "feedback_live": False,
    },
    # No feedback.
    "uit": {},
    # Secret links only.
    "geheim": {
        "access_base": AccessBase.NOBODY,
        "access_keys": True,
        "feedback_enabled": True,
        "feedback_identity": FeedbackIdentity.NAME,
        "feedback_live": True,
    },
    # Secret links only, feedback with an account only: the setting a new
    # site gets, on a site shared with people outside the team.
    "gedeeld": {
        "access_base": AccessBase.NOBODY,
        "access_keys": True,
        "feedback_enabled": True,
        "feedback_identity": FeedbackIdentity.ACCOUNT,
        "feedback_live": True,
    },
    # Signed in only, feedback with an account only.
    "intern": {
        "access_base": AccessBase.SSO,
        "feedback_enabled": True,
        "feedback_identity": FeedbackIdentity.ACCOUNT,
        "feedback_live": True,
    },
    # Signed in only, feedback with a name as well.
    "besloten": {
        "access_base": AccessBase.SSO,
        "feedback_enabled": True,
        "feedback_identity": FeedbackIdentity.NAME,
        "feedback_live": True,
    },
}
PREVIEWS = {"feedback": "pr-1", "alleen-preview": "pr-2", "uit": "pr-3", "besloten": "pr-4", "gedeeld": "pr-5"}
LIVE_FILES = {
    "index.html": DOC,
    "stijl.css": b"body{}",
    "404.html": PAGE_404,
    "zonder-doctype.html": NO_DOCTYPE,
    "utf16.html": UTF16,
    "lang-commentaar.html": LONG_COMMENT,
    LONG_PATH: DOC,
}


@dataclass
class World:
    site_ids: dict[str, uuid.UUID]
    live_ids: dict[str, uuid.UUID]
    preview_ids: dict[str, uuid.UUID]
    key_id: uuid.UUID
    # The secret link of `gedeeld`, and what a link to it carries.
    shared_key_id: uuid.UUID
    shared_key: str

    def live_storage(self, slug: str) -> str:
        return version_ref(self.site_ids[slug], self.live_ids[slug])


@dataclass
class Environment:
    factory: async_sessionmaker[AsyncSession]
    store: ContentStore
    world: World
    app: FastAPI


async def _seed(factory: async_sessionmaker[AsyncSession], store: ContentStore) -> World:
    async with factory() as db:
        group = Group(slug="aurora", name="Aurora", default_access_base=AccessBase.PUBLIC)
        db.add(group)
        await db.flush()
        member = Member(sso_subject="lid-actief", email="lid@example.org", status=MemberStatus.ACTIVE)
        db.add(member)
        await db.flush()
        db.add(GroupMember(group_id=group.id, member_id=member.id, role=Role.ADMIN))

        sites = {
            slug: Site(
                group_id=group.id,
                slug=slug,
                title=slug.title(),
                **{"access_base": AccessBase.PUBLIC, "external_sources": False, "sandbox": True, **switches},
            )
            for slug, switches in SITES.items()
        }
        db.add_all(sites.values())
        await db.flush()

        def new_version(site: Site, files: dict[str, bytes], target: VersionTarget) -> Version:
            version_id = uuid.uuid4()
            store.store_version(site.id, version_id, files)
            return Version(id=version_id, site_id=site.id, target=target, member_id=member.id)

        live = {slug: new_version(site, LIVE_FILES, VersionTarget.LIVE) for slug, site in sites.items()}
        previews = {
            slug: new_version(sites[slug], {"index.html": PREVIEW_DOC}, VersionTarget.PREVIEW) for slug in PREVIEWS
        }
        db.add_all([*live.values(), *previews.values()])
        await db.flush()
        for slug, site in sites.items():
            site.live_version_id = live[slug].id
        for slug, ref in PREVIEWS.items():
            db.add(Preview(site_id=sites[slug].id, ref=ref, version_id=previews[slug].id))
        key, _ = await keys.create_key(db, sites["geheim"].id, "testsleutel")
        shared, shared_plain = await keys.create_key(db, sites["gedeeld"].id, "extern")
        world = World(
            site_ids={slug: site.id for slug, site in sites.items()},
            live_ids={slug: version.id for slug, version in live.items()},
            preview_ids={slug: version.id for slug, version in previews.items()},
            key_id=key.id,
            shared_key_id=shared.id,
            shared_key=shared_plain,
        )
        await db.commit()
        return world


@pytest_asyncio.fixture
async def environment(migrated_dsn: str, tmp_path: Path) -> AsyncIterator[Environment]:
    engine = create_async_engine(migrated_dsn)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            factory = async_sessionmaker(
                bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
            )
            content_root = tmp_path / "content"
            store = ContentStore(content_root)
            world = await _seed(factory, store)
            settings = make_settings(content_root).model_copy(update={"base_url": ADMIN_ORIGIN})
            app = _make_app(settings, factory, store)
            bundle = tmp_path / "feedback-widget.js"
            bundle.write_bytes(BUNDLE)
            app.state.feedback_widget = load_widget_bundle(bundle)
            try:
                yield Environment(factory=factory, store=store, world=world, app=app)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, follow_redirects=False)


@pytest_asyncio.fixture
async def client(environment: Environment) -> AsyncIterator[httpx.AsyncClient]:
    async with _client(environment.app) as c:
        yield c


def _mark(environment: Environment, site: str, *, secret: str = SECRET, now: int | None = None) -> str:
    """The hide mark of a site, as /-/feedback/hide sets it."""
    moment = int(time.time()) if now is None else now
    return mint_hide_mark(secret, site_id=environment.world.site_ids[site], now=moment)


def _hide(client: httpx.AsyncClient, environment: Environment, site: str, *, path: str | None = None) -> None:
    """The hide cookie as /-/feedback/hide sets it for a site, or at `path`."""
    at = f"/aurora/{site}/" if path is None else path
    client.cookies.set(FEEDBACK_HIDE_COOKIE, _mark(environment, site), domain="toon.example", path=at)


def _set_limit(monkeypatch: pytest.MonkeyPatch, max_bytes: int) -> None:
    monkeypatch.setattr(feedback_inject, "FEEDBACK_INJECT_MAX_BYTES", max_bytes)


async def _config_of(client: httpx.AsyncClient, path: str = "/aurora/feedback/", **headers: str) -> dict:
    response = await client.get(path, headers={**NAVIGATION, **headers})
    assert response.status_code == 200
    return _config(_tag_in(response.content, DOC, DOCTYPE_END))


def _link_of(config: dict) -> LinkTicket | None:
    """The link ticket on the way to the consent page, opened."""
    (link,) = parse_qs(urlsplit(config["accountUrl"]).query)["link"]
    return parse_link_ticket(SECRET, link, now=int(time.time()))


def _assert_as_published(response: httpx.Response, body: bytes, etag: str, vary: str) -> None:
    """The file as it was published, for a navigation that gets no widget:
    the normal file response, never stored."""
    assert response.status_code == 200
    assert response.content == body
    assert response.headers["etag"] == etag
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["vary"] == vary


class TestPageWithTheWidget:
    async def test_a_navigation_gets_the_widget_right_after_the_doctype(self, client):
        response = await client.get("/aurora/feedback/", headers=NAVIGATION)
        assert response.status_code == 200
        tag = _tag_in(response.content, DOC, DOCTYPE_END)
        match = TAG_RE.fullmatch(tag)
        assert match is not None
        assert match.group(1) == BUNDLE_HASH.encode()

    async def test_its_headers_are_the_files_but_for_what_the_token_changes(self, client):
        """Never stored, no ETag, no Range, no Content-Encoding, and the
        exact length of what goes out."""
        response = await client.get("/aurora/feedback/", headers=NAVIGATION)
        assert dict(_header_list(response)) == {
            "content-type": "text/html; charset=utf-8",
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
            "content-security-policy": SANDBOX_CSP,
            "referrer-policy": "strict-origin-when-cross-origin",
            "vary": "Service-Worker, Sec-Fetch-Dest",
            "content-length": str(len(response.content)),
        }

    async def test_the_config(self, client):
        config = await _config_of(client)
        assert config.keys() == CONFIG_KEYS
        assert {name: value for name, value in config.items() if name != "token"} == {
            "v": 1,
            "api": "/-/feedback/v1",
            "identity": "name",
            "account": False,
            "accountUrl": "/-/feedback/account?returnTo=%2Faurora%2Ffeedback%2F",
            "hideUrl": "/-/feedback/hide",
            "showUrl": "/-/feedback/show?returnTo=%2Faurora%2Ffeedback%2F",
            "privacyUrl": "https://beheer.toon.example/-/privacy",
            "screenshotMaxBytes": 2 * 1024 * 1024,
        }

    async def test_the_editor_frame_when_toon_has_an_editor(self, client, environment, tmp_path):
        script = tmp_path / "feedback-editor.js"
        script.write_bytes(b"/*! Toon feedback editor. */\n")
        editor = load_feedback_editor(script, content_base_url=BASE_URL)
        environment.app.state.feedback_editor = editor
        config = await _config_of(client)
        assert config.keys() == {*CONFIG_KEYS, "editorUrl"}
        assert config["editorUrl"] == frame_url(editor)
        assert re.fullmatch(r"/-/feedback/editor\.[0-9a-f]{16}\.html", config["editorUrl"])

    async def test_no_editor_frame_without_an_editor(self, client, environment):
        environment.app.state.feedback_editor = None
        assert "editorUrl" not in await _config_of(client)
        del environment.app.state.feedback_editor
        assert "editorUrl" not in await _config_of(client)

    async def test_return_to_leaves_out_every_key_and_keeps_the_rest(self, client):
        config = await _config_of(client, "/aurora/feedback/?x=1&key=foo&y=%C3%A9&key=bar")
        return_to = "returnTo=%2Faurora%2Ffeedback%2F%3Fx%3D1%26y%3D%25C3%25A9"
        assert config["accountUrl"] == f"/-/feedback/account?{return_to}"
        assert config["showUrl"] == f"/-/feedback/show?{return_to}"

    async def test_the_token_reads_back_as_this_page_view(self, client, environment):
        before = int(time.time())
        token = _token(await _config_of(client))
        world = environment.world
        assert token == PageToken(
            site_id=world.site_ids["feedback"],
            kind="live",
            ref=None,
            version_id=world.live_ids["feedback"],
            session_id=None,
            access_session_id=None,
            key_id=None,
            expires_at=token.expires_at,
        )
        assert before + TOKEN_LIFETIME_S <= token.expires_at <= int(time.time()) + TOKEN_LIFETIME_S

    async def test_a_preview_gets_it_with_a_token_for_the_preview(self, client, environment):
        response = await client.get("/aurora/feedback/_preview/pr-1/", headers=NAVIGATION)
        assert response.status_code == 200
        assert response.headers["x-robots-tag"] == "noindex, nofollow"
        assert response.headers["cache-control"] == "no-store"
        assert set(response.headers) == {*INJECTED_HEADERS, "x-robots-tag"}
        config = _config(_tag_in(response.content, PREVIEW_DOC, PREVIEW_DOCTYPE_END))
        assert config["accountUrl"] == "/-/feedback/account?returnTo=%2Faurora%2Ffeedback%2F_preview%2Fpr-1%2F"
        token = _token(config)
        assert (token.kind, token.ref, token.version_id) == (
            "preview",
            "pr-1",
            environment.world.preview_ids["feedback"],
        )

    async def test_a_page_without_a_doctype_gets_it_in_front(self, client):
        response = await client.get("/aurora/feedback/zonder-doctype.html", headers=NAVIGATION)
        assert response.status_code == 200
        _tag_in(response.content, NO_DOCTYPE, 0)

    @pytest.mark.parametrize("limit", [1, 1024 * 1024, 16 * 1024 * 1024])
    async def test_the_screenshot_limit_is_the_setting(self, client, environment, limit):
        """What the widget API takes, so the widget encodes under it rather
        than send what the API refuses, and a higher limit reaches it too."""
        settings = environment.app.state.settings
        environment.app.state.settings = settings.model_copy(update={"feedback_screenshot_max_bytes": limit})
        assert (await _config_of(client))["screenshotMaxBytes"] == limit

    @pytest.mark.parametrize("dest", [" Document ", "DOCUMENT"])
    async def test_sec_fetch_dest_is_read_as_the_login_reads_it(self, client, dest):
        response = await client.get("/aurora/feedback/", headers={"Sec-Fetch-Dest": dest})
        _tag_in(response.content, DOC, DOCTYPE_END)


class TestPageKeptCompressed:
    """Decoded from its gzip copy whatever the request accepts, sent as it
    was published plus the tag, without compressing it again."""

    VARIES = "Service-Worker, Accept-Encoding, Sec-Fetch-Dest"

    @pytest.mark.parametrize("accept_encoding", ["br", "gzip", "br, gzip", None, "identity"])
    async def test_a_navigation_gets_it_decoded(self, client, environment, accept_encoding):
        _store_compressed(environment, environment.world.live_storage("feedback"), "index.html")
        response, body = await _on_the_wire(client, "/aurora/feedback/", accept_encoding, headers=NAVIGATION)
        assert response.status_code == 200
        _tag_in(body, DOC, DOCTYPE_END)
        assert response.headers["content-length"] == str(len(body))
        assert response.headers["vary"] == self.VARIES
        assert set(response.headers) == INJECTED_HEADERS

    async def test_from_the_gzip_copy_when_there_is_no_brotli_copy(self, client, environment):
        _store_compressed(environment, environment.world.live_storage("feedback"), "index.html", ("gzip",))
        response, body = await _on_the_wire(client, "/aurora/feedback/", "br", headers=NAVIGATION)
        _tag_in(body, DOC, DOCTYPE_END)
        assert "content-encoding" not in response.headers

    async def test_any_other_request_gets_the_copy_it_accepts(self, client, environment):
        copies = _store_compressed(environment, environment.world.live_storage("feedback"), "index.html")
        response, body = await _on_the_wire(client, "/aurora/feedback/", "br")
        assert response.headers["content-encoding"] == "br"
        assert body == copies["br"]
        assert response.headers["etag"] == f'"{environment.world.live_ids["feedback"]}-br"'
        assert response.headers["cache-control"] == "no-cache, must-revalidate"
        assert response.headers["vary"] == self.VARIES

    async def test_above_the_limit_a_navigation_gets_the_copy_it_accepts(self, client, environment, monkeypatch):
        copies = _store_compressed(environment, environment.world.live_storage("feedback"), "index.html")
        _set_limit(monkeypatch, len(DOC) - 1)
        response, body = await _on_the_wire(client, "/aurora/feedback/", "br", headers=NAVIGATION)
        assert response.status_code == 200
        assert response.headers["content-encoding"] == "br"
        assert body == copies["br"]
        assert response.headers["etag"] == f'"{environment.world.live_ids["feedback"]}-br"'
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["vary"] == self.VARIES


def _way_back(config: dict) -> str:
    """The `returnTo` that the consent page and the show link take back to
    the page, the same on both."""
    (account,) = parse_qs(urlsplit(config["accountUrl"]).query)["returnTo"]
    (show,) = parse_qs(urlsplit(config["showUrl"]).query)["returnTo"]
    assert account == show
    return account


class TestTheWayBack:
    """The consent page, the hide form and the show link send the visitor
    back to the page at the way back in the config, and refuse one that
    `return_target` does not take: the page's path and query, else its path
    alone, else no way back at all. The widget goes in every time."""

    START = "/aurora/feedback/?q="

    async def _hide_and_show(self, browser: httpx.AsyncClient, config: dict, way_back: str) -> None:
        hidden = await browser.post(
            PATH_FEEDBACK_HIDE, data={"token": config["token"], "returnTo": way_back}, headers=FORM
        )
        assert (hidden.status_code, hidden.headers["location"]) == (303, way_back)
        shown = await browser.get(config["showUrl"], headers=NAVIGATION)
        assert (shown.status_code, shown.headers["location"]) == (303, way_back)
        consent = await browser.get(config["accountUrl"], headers=NAVIGATION)
        assert consent.status_code == 302
        login = urlsplit(consent.headers["location"])
        assert login.path == PATH_CONTENT_LOGIN
        (here,) = parse_qs(login.query)["returnTo"]
        assert parse_qs(urlsplit(here).query)["returnTo"] == [way_back]

    async def test_the_path_and_query_when_the_feedback_pages_take_them(self, environment):
        longest = self.START + "a" * (MAX_RETURN_TO_LENGTH - 1 - len(self.START))
        async with _client(_whole_host(environment)) as browser:
            config = await _config_of(browser, longest)
            assert config.keys() == CONFIG_KEYS
            assert _way_back(config) == longest
            await self._hide_and_show(browser, config, longest)

    async def test_the_path_alone_when_the_query_makes_it_too_long(self, environment):
        too_long = self.START + "a" * (MAX_RETURN_TO_LENGTH - len(self.START))
        assert feedback_consent.return_target([too_long]) is None
        async with _client(_whole_host(environment)) as browser:
            config = await _config_of(browser, too_long)
            assert config.keys() == CONFIG_KEYS
            assert _way_back(config) == "/aurora/feedback/"
            await self._hide_and_show(browser, config, "/aurora/feedback/")

    async def test_the_path_alone_when_the_query_makes_the_config_too_long(self, client):
        """A way back the feedback pages take, whose every `+` the config's
        two addresses each carry as `%2B`."""
        return_to = self.START + "+" * (MAX_RETURN_TO_LENGTH - 1 - len(self.START))
        assert feedback_consent.return_target([return_to]) is not None
        assert script_tag(
            WidgetBundle(content=BUNDLE, hash=BUNDLE_HASH),
            page_config(
                token="",
                identity=FeedbackIdentity.NAME,
                account=False,
                return_to=return_to,
                admin_origin=ADMIN_ORIGIN,
                screenshot_max_bytes=1,
            ),
        ) is None
        config = await _config_of(client, return_to)
        assert config.keys() == CONFIG_KEYS
        assert _way_back(config) == "/aurora/feedback/"

    async def test_the_path_alone_when_the_query_is_one_the_login_would_change(self, client, monkeypatch):
        """No query the router builds has a control character, but
        `valid_return_to` decides, not the router."""
        monkeypatch.setattr(serving_router_module, "_path_without_key", lambda request: "/aurora/feedback/?q=a\tb")
        config = await _config_of(client)
        assert config.keys() == CONFIG_KEYS
        assert _way_back(config) == "/aurora/feedback/"

    async def test_no_way_back_when_the_path_alone_is_too_long(self, environment):
        """No account and no hiding in the widget, which still takes feedback
        with a name. The hide form refuses the way back it would have had."""
        path = f"/aurora/feedback/{quote(LONG_PATH)}"
        assert len(path) == MAX_RETURN_TO_LENGTH
        async with _client(_whole_host(environment)) as browser:
            response = await browser.get(f"{path}?x=1", headers=NAVIGATION)
            assert response.status_code == 200
            config = _config(_tag_in(response.content, DOC, DOCTYPE_END))
            assert config.keys() == CONFIG_KEYS - {"accountUrl", "showUrl"}
            assert config["identity"] == "name"
            form = {"token": config["token"], "returnTo": path}
            hidden = await browser.post(PATH_FEEDBACK_HIDE, data=form, headers=FORM)
            assert is_neutral_404(hidden)
            sent = await browser.post(
                ITEMS, files=parts(_sent(path, {"kind": "name", "name": "Kijker"})), headers=auth(config["token"])
            )
            assert sent.status_code == 201, sent.text


class TestNavigationWithoutTheWidget:
    """Each reason the widget stays out: the file as published, never
    stored and never a 304, varying with Sec-Fetch-Dest like any answer for
    the page."""

    VARY = "Service-Worker, Sec-Fetch-Dest"

    @staticmethod
    def _etag(environment: Environment) -> str:
        return f'"{environment.world.live_ids["feedback"]}"'

    async def test_the_button_hidden_on_this_site(self, client, environment):
        _hide(client, environment, "feedback")
        response = await client.get("/aurora/feedback/", headers=NAVIGATION)
        _assert_as_published(response, DOC, self._etag(environment), self.VARY)
        assert response.headers["accept-ranges"] == "bytes"
        preview = await client.get("/aurora/feedback/_preview/pr-1/", headers=NAVIGATION)
        assert preview.content == PREVIEW_DOC

    async def test_a_button_hidden_on_another_site_stays_there(self, client, environment):
        """Also with that site's mark at `/`, where it rides along to every
        site's pages: the mark names the site it hides the button on."""
        _hide(client, environment, "uit")
        response = await client.get("/aurora/feedback/", headers=NAVIGATION)
        _tag_in(response.content, DOC, DOCTYPE_END)
        _hide(client, environment, "alleen-preview", path="/")
        response = await client.get("/aurora/feedback/", headers=NAVIGATION)
        _tag_in(response.content, DOC, DOCTYPE_END)

    @pytest.mark.parametrize(
        "value_for",
        [
            pytest.param(lambda environment: "1", id="any-value"),
            pytest.param(lambda environment: "", id="empty"),
            pytest.param(lambda environment: _mark(environment, "feedback", secret=OTHER_SECRET), id="another-secret"),
            pytest.param(
                lambda environment: _mark(environment, "feedback", now=int(time.time()) - HIDE_LIFETIME_S),
                id="twelve-hours-old",
            ),
            pytest.param(
                lambda environment: mint_page_token(
                    SECRET,
                    site_id=environment.world.site_ids["feedback"],
                    kind="live",
                    ref=None,
                    version_id=None,
                    session_id=None,
                    key_id=None,
                    now=int(time.time()),
                ),
                id="a-page-token-of-the-site",
            ),
        ],
    )
    async def test_a_value_that_is_no_mark_of_now_hides_nothing(self, client, environment, value_for):
        """What a page's own script, or a sibling host, can write under the
        name, at `/` so it reaches every site, and for as long as it likes:
        the widget stays."""
        cookie = f"{FEEDBACK_HIDE_COOKIE}={value_for(environment)}"
        response = await client.get("/aurora/feedback/", headers={**NAVIGATION, "Cookie": cookie})
        _tag_in(response.content, DOC, DOCTYPE_END)

    async def test_one_mark_of_the_site_among_other_values_hides_it(self, client, environment):
        """Every value under the name counts, in one Cookie header or in
        several: the browser sends no path, and Starlette keeps one value of
        a name it sees twice, here never the mark."""
        mark, foreign = _mark(environment, "feedback"), _mark(environment, "uit")
        name = FEEDBACK_HIDE_COOKIE
        for headers in (
            [("cookie", f"{name}={mark}; {name}=1")],
            [("cookie", f"{name}=1; {name}={mark}; {name}={foreign}")],
            [("cookie", f"{name}={mark}"), ("cookie", f"{name}={foreign}")],
        ):
            response = await client.get("/aurora/feedback/", headers=[("sec-fetch-dest", "document"), *headers])
            _assert_as_published(response, DOC, self._etag(environment), self.VARY)

    @pytest.mark.parametrize("compressed", [False, True], ids=["plain", "kept-compressed"])
    async def test_a_page_above_ten_mib(self, client, environment, compressed):
        """Measured decoded, for a page kept compressed by its gzip trailer:
        exactly 10 MiB gets the widget, one byte more does not."""
        assert FEEDBACK_INJECT_MAX_BYTES == 10 * 1024 * 1024
        storage = environment.world.live_storage("feedback")
        directory = environment.store.file_path(storage, "index.html").parent
        for name, extra in (("precies.html", 0), ("te-groot.html", 1)):
            page = DOC + b" " * (FEEDBACK_INJECT_MAX_BYTES + extra - len(DOC))
            if compressed:
                _store_compressed(environment, storage, name, ("gzip",), content=page)
            else:
                (directory / name).write_bytes(page)
            response = await client.get(f"/aurora/feedback/{name}", headers=NAVIGATION)
            assert response.status_code == 200
            if extra:
                assert response.content == page
            else:
                _tag_in(response.content, page, DOCTYPE_END)

    @pytest.mark.parametrize(
        ("path", "content"),
        [("utf16.html", UTF16), ("lang-commentaar.html", LONG_COMMENT)],
        ids=["utf16", "no-place-in-the-first-64-kib"],
    )
    async def test_a_page_the_widget_cannot_go_into(self, client, environment, path, content):
        response = await client.get(f"/aurora/feedback/{path}", headers=NAVIGATION)
        _assert_as_published(response, content, self._etag(environment), self.VARY)

    async def test_no_bundle(self, client, environment):
        environment.app.state.feedback_widget = None
        response = await client.get("/aurora/feedback/", headers=NAVIGATION)
        _assert_as_published(response, DOC, self._etag(environment), self.VARY)
        del environment.app.state.feedback_widget
        response = await client.get("/aurora/feedback/", headers=NAVIGATION)
        _assert_as_published(response, DOC, self._etag(environment), self.VARY)

    async def test_no_admin_origin(self, client, environment):
        """Without TOON_BASE_URL the privacy link would be a path, which the
        widget refuses."""
        environment.app.state.settings = environment.app.state.settings.model_copy(update={"base_url": None})
        response = await client.get("/aurora/feedback/", headers=NAVIGATION)
        _assert_as_published(response, DOC, self._etag(environment), self.VARY)

    async def test_a_config_longer_than_the_widget_reads_even_without_a_way_back(self, client, environment):
        settings = environment.app.state.settings
        long_origin = f"{ADMIN_ORIGIN}/{'x' * MAX_CONFIG_LENGTH}"
        environment.app.state.settings = settings.model_copy(update={"base_url": long_origin})
        response = await client.get("/aurora/feedback/", headers=NAVIGATION)
        _assert_as_published(response, DOC, self._etag(environment), self.VARY)

    async def test_no_token(self, client, environment, monkeypatch):
        def refuse(*args, **kwargs):
            raise ValueError("these fields make no page token")

        monkeypatch.setattr(feedback_inject, "mint_page_token", refuse)
        response = await client.get("/aurora/feedback/", headers=NAVIGATION)
        _assert_as_published(response, DOC, self._etag(environment), self.VARY)

    async def test_a_range_is_served_as_for_any_file(self, client, environment):
        _hide(client, environment, "feedback")
        response = await client.get("/aurora/feedback/", headers={**NAVIGATION, "Range": "bytes=0-3"})
        assert response.status_code == 206
        assert response.content == DOC[:4]


class TestNotAFeedbackPage:
    """Nothing changes where the widget never goes: a navigation gets what
    any other request gets, byte for byte, and a 304 where it fits."""

    @pytest.mark.parametrize(
        "path",
        [
            "/aurora/uit/",
            "/aurora/uit/_preview/pr-3/",
            "/aurora/alleen-preview/",
            "/aurora/feedback/stijl.css",
            "/aurora/feedback/404.html",
        ],
    )
    async def test_a_navigation_gets_what_any_request_gets(self, client, path):
        other = await client.get(path)
        navigation = await client.get(path, headers=NAVIGATION)
        assert navigation.status_code == other.status_code == 200
        assert navigation.content == other.content
        assert _header_list(navigation) == _header_list(other)
        assert navigation.headers["vary"] == "Service-Worker"
        revalidated = await client.get(path, headers={**NAVIGATION, "If-None-Match": other.headers["etag"]})
        assert revalidated.status_code == 304

    async def test_a_compressed_page_of_a_site_without_feedback_answers_as_before(self, client, environment):
        copies = _store_compressed(environment, environment.world.live_storage("uit"), "index.html")
        response, body = await _on_the_wire(client, "/aurora/uit/", "br", headers=NAVIGATION)
        assert body == copies["br"]
        assert response.headers["vary"] == "Service-Worker, Accept-Encoding"
        assert response.headers["cache-control"] == "no-cache, must-revalidate"

    async def test_previews_only_leaves_the_live_site_alone(self, client):
        live = await client.get("/aurora/alleen-preview/", headers=NAVIGATION)
        assert live.content == DOC
        preview = await client.get("/aurora/alleen-preview/_preview/pr-2/", headers=NAVIGATION)
        config = _config(_tag_in(preview.content, PREVIEW_DOC, PREVIEW_DOCTYPE_END))
        assert config["identity"] == "anonymous"

    async def test_a_version_view_gets_no_widget(self, client, environment):
        set_content_session_cookie(client, environment.app, sub="lid-actief", sites=("/aurora/feedback/",))
        path = f"/aurora/feedback/_version/{environment.world.live_ids['feedback']}/"
        other = await client.get(path)
        navigation = await client.get(path, headers=NAVIGATION)
        assert navigation.status_code == 200
        assert navigation.content == other.content == DOC
        assert _header_list(navigation) == _header_list(other)
        assert navigation.headers["vary"] == "Service-Worker"

    async def test_the_sites_404_page_gets_no_widget(self, client):
        response = await client.get("/aurora/feedback/bestaat-niet.html", headers=NAVIGATION)
        assert response.status_code == 404
        assert response.content == PAGE_404
        assert response.headers["vary"] == "Service-Worker"
        assert response.headers["cache-control"] == "no-cache, must-revalidate"


class TestCaching:
    async def test_a_navigation_never_gets_a_304(self, client, environment):
        etag = f'"{environment.world.live_ids["feedback"]}"'
        for condition in ("*", etag, f"W/{etag}"):
            response = await client.get("/aurora/feedback/", headers={**NAVIGATION, "If-None-Match": condition})
            assert response.status_code == 200, condition
            _tag_in(response.content, DOC, DOCTYPE_END)

    async def test_not_even_one_that_gets_no_widget(self, client, environment):
        etag = f'"{environment.world.live_ids["feedback"]}"'
        _hide(client, environment, "feedback")
        response = await client.get("/aurora/feedback/", headers={**NAVIGATION, "If-None-Match": etag})
        _assert_as_published(response, DOC, etag, "Service-Worker, Sec-Fetch-Dest")

    @pytest.mark.parametrize("dest", [None, "empty", "iframe", "script"])
    async def test_every_other_request_varies_with_sec_fetch_dest(self, client, environment, dest):
        """A cache that stored this answer must not hand it to a navigation.
        The 304 repeats the 200's headers, Vary included."""
        headers = {} if dest is None else {"Sec-Fetch-Dest": dest}
        etag = f'"{environment.world.live_ids["feedback"]}"'
        full = await client.get("/aurora/feedback/", headers=headers)
        assert full.status_code == 200
        assert full.content == DOC
        assert full.headers["etag"] == etag
        assert full.headers["cache-control"] == "no-cache, must-revalidate"
        assert full.headers["vary"] == "Service-Worker, Sec-Fetch-Dest"
        revalidated = await client.get("/aurora/feedback/", headers={**headers, "If-None-Match": etag})
        assert revalidated.status_code == 304
        unrepeated = {"content-length", "content-type", "accept-ranges", "last-modified"}
        assert {k: v for k, v in revalidated.headers.items() if k != "content-length"} == {
            k: v for k, v in full.headers.items() if k not in unrepeated
        }

    @pytest.mark.parametrize("if_range", [None, "etag", "date"])
    async def test_range_and_if_range_are_ignored(self, client, environment, if_range):
        headers = {**NAVIGATION, "Range": "bytes=0-3"}
        if if_range == "etag":
            headers["If-Range"] = f'"{environment.world.live_ids["feedback"]}"'
        elif if_range == "date":
            headers["If-Range"] = "Wed, 21 Oct 2099 07:28:00 GMT"
        response = await client.get("/aurora/feedback/", headers=headers)
        assert response.status_code == 200
        assert "content-range" not in response.headers
        _tag_in(response.content, DOC, DOCTYPE_END)


class TestHead:
    @pytest.mark.parametrize("path", ["/aurora/feedback/", "/aurora/feedback/_preview/pr-1/"])
    async def test_a_head_answers_with_the_headers_of_the_get(self, client, path):
        get = await client.get(path, headers=NAVIGATION)
        head = await client.head(path, headers=NAVIGATION)
        assert head.status_code == get.status_code == 200
        assert _header_list(head) == _header_list(get)

    async def test_a_head_of_a_page_kept_compressed(self, client, environment):
        _store_compressed(environment, environment.world.live_storage("feedback"), "index.html")
        get, _ = await _on_the_wire(client, "/aurora/feedback/", "br", headers=NAVIGATION)
        head, body = await _on_the_wire(client, "/aurora/feedback/", "br", method="HEAD", headers=NAVIGATION)
        assert body == b""
        assert _header_list(head) == _header_list(get)

    async def test_a_head_sends_no_body(self, client, environment):
        get = await client.get("/aurora/feedback/", headers=NAVIGATION)
        status, headers, body = await _raw_request(
            environment.app, "/aurora/feedback/", method="HEAD", extra_headers=((b"sec-fetch-dest", b"document"),)
        )
        assert status == 200
        assert body == b""
        assert dict(headers)["content-length"] == str(len(get.content))

    async def test_a_head_without_the_widget_and_a_head_of_another_request(self, client, environment):
        _hide(client, environment, "feedback")
        for headers in (NAVIGATION, {}):
            get = await client.get("/aurora/feedback/", headers=headers)
            assert get.content == DOC, headers
            head = await client.head("/aurora/feedback/", headers=headers)
            assert _header_list(head) == _header_list(get), headers


def _viewer(app: FastAPI, *, name: str | None = None, email: str = "kijker@example.org"):
    return app.state.session_store.create_session(
        sub="kijker-sub", email=email, email_verified=True, acr="urn:acr:hoog", name=name, kind=SessionKind.CONTENT
    )


def _set_site_cookie(client: httpx.AsyncClient, app: FastAPI, session_id: str, site: str) -> None:
    token = sign(app.state.settings.session_secret, session_id)
    client.cookies.set(CONTENT_SESSION_COOKIE, token, domain="toon.example", path=f"/aurora/{site}/")


class TestWhoTheTokenNames:
    @pytest.mark.parametrize(
        ("site", "consented_on", "identity", "roles"),
        [
            pytest.param("feedback", None, "name", "none", id="public-without-consent"),
            pytest.param("feedback", "uit", "name", "none", id="public-consent-for-another-site"),
            pytest.param("feedback", "feedback", "name", "own", id="public-consent-for-this-site"),
            pytest.param("besloten", "besloten", "name", "own", id="sign-in-consent"),
            pytest.param("intern", None, "account", "gate", id="account-only-without-consent"),
            pytest.param("intern", "intern", "account", "own", id="account-only-consent"),
        ],
    )
    async def test_the_role_a_session_rides_in(self, client, environment, site, consented_on, identity, roles):
        """Consent names the session in its own role alone. Without it, the
        session rides only where it let the visitor in, for the gate: a
        public page lets everyone in, so there the token carries nothing that
        sets a signed-in visitor apart."""
        session = _viewer(environment.app)
        if consented_on is not None:
            store = environment.app.state.session_store
            store.consent_feedback(session.id, str(environment.world.site_ids[consented_on]))
        _set_site_cookie(client, environment.app, session.id, site)
        config = await _config_of(client, f"/aurora/{site}/")
        assert (config["identity"], config["account"]) == (identity, roles == "own")
        token = _token(config)
        expected = {"none": (None, None), "gate": (None, session.id), "own": (session.id, None)}[roles]
        assert (token.session_id, token.access_session_id) == expected

    @pytest.mark.parametrize("path", ["/aurora/besloten/", "/aurora/besloten/_preview/pr-4/"], ids=["live", "preview"])
    async def test_where_the_session_let_the_visitor_in_it_rides_for_the_gate_alone(self, client, environment, path):
        """The API asks the gate again, and on a page that needs a sign-in
        an anonymous visitor would be refused: so the session goes along in
        the role that serves the gate, and the config still says no account."""
        session = _viewer(environment.app)
        _set_site_cookie(client, environment.app, session.id, "besloten")
        response = await client.get(path, headers=NAVIGATION)
        assert response.status_code == 200
        original, point = (DOC, DOCTYPE_END) if "_preview" not in path else (PREVIEW_DOC, PREVIEW_DOCTYPE_END)
        config = _config(_tag_in(response.content, original, point))
        assert (config["identity"], config["account"]) == ("name", False)
        token = _token(config)
        assert (token.session_id, token.access_session_id, token.key_id) == (None, session.id, None)

    @pytest.mark.parametrize(("site", "consented"), [("feedback", True), ("besloten", False)])
    async def test_a_site_cookie_sent_twice_names_nobody(self, environment, site, consented):
        """A cookie of the same name from elsewhere can shadow the visitor's.
        The last one sent is the one read, and here that is the visitor's own
        session, consented on the public site and not on the one that needs a
        sign-in: it counts in neither role."""
        session = _viewer(environment.app)
        if consented:
            environment.app.state.session_store.consent_feedback(session.id, str(environment.world.site_ids[site]))
        signed = sign(environment.app.state.settings.session_secret, session.id)
        path = f"/aurora/{site}/"
        async with _client(environment.app) as client:
            alone = _token(await _config_of(client, path, Cookie=f"{CONTENT_SESSION_COOKIE}={signed}"))
            assert session.id in (alone.session_id, alone.access_session_id)
            for shadow in ("schaduw", signed):
                config = await _config_of(
                    client, path, Cookie=f"{CONTENT_SESSION_COOKIE}={shadow}; {CONTENT_SESSION_COOKIE}={signed}"
                )
                assert config["account"] is False, shadow
                token = _token(config)
                assert (token.session_id, token.access_session_id) == (None, None), shadow

    async def test_the_secret_link_the_gate_let_the_visitor_in_with(self, client, environment):
        """In the token, and on the way to the consent page as a ticket of
        its own that ends with the token: the key cookie never reaches that
        page. The token itself goes into no address."""
        world = environment.world
        cookie = sign_key_cookie(environment.app.state.settings.session_secret, str(world.key_id))
        client.cookies.set(KEY_COOKIE, cookie, domain="toon.example", path="/aurora/geheim/")
        response = await client.get("/aurora/geheim/", headers=NAVIGATION)
        assert response.headers["referrer-policy"] == "same-origin"
        config = _config(_tag_in(response.content, DOC, DOCTYPE_END))
        token = _token(config)
        assert (token.site_id, token.version_id, token.key_id) == (
            world.site_ids["geheim"],
            world.live_ids["geheim"],
            str(world.key_id),
        )
        assert _link_of(config) == LinkTicket(
            key_id=str(world.key_id), site_id=world.site_ids["geheim"], expires_at=token.expires_at
        )
        assert config["showUrl"] == "/-/feedback/show?returnTo=%2Faurora%2Fgeheim%2F"
        for address in (config["accountUrl"], config["showUrl"], config["hideUrl"], config["privacyUrl"]):
            assert config["token"] not in address

    async def test_a_consented_session_beside_the_secret_link_gets_no_ticket(self, client, environment):
        """With consent the widget offers the way to stop instead, which it
        builds from the same address: it carries nothing it does not need."""
        world = environment.world
        session = _viewer(environment.app)
        environment.app.state.session_store.consent_feedback(session.id, str(world.site_ids["geheim"]))
        _set_site_cookie(client, environment.app, session.id, "geheim")
        cookie = sign_key_cookie(environment.app.state.settings.session_secret, str(world.key_id))
        client.cookies.set(KEY_COOKIE, cookie, domain="toon.example", path="/aurora/geheim/")
        config = await _config_of(client, "/aurora/geheim/")
        assert config["account"] is True
        token = _token(config)
        assert (token.session_id, token.key_id) == (session.id, str(world.key_id))
        assert config["accountUrl"] == "/-/feedback/account?returnTo=%2Faurora%2Fgeheim%2F"

    async def test_a_session_beside_the_secret_link_that_let_the_visitor_in_stays_out(self, client, environment):
        """The gate tries the link first, and the link is what the API asks it
        about again: the session is not what let the visitor in."""
        world = environment.world
        session = _viewer(environment.app)
        _set_site_cookie(client, environment.app, session.id, "geheim")
        cookie = sign_key_cookie(environment.app.state.settings.session_secret, str(world.key_id))
        client.cookies.set(KEY_COOKIE, cookie, domain="toon.example", path="/aurora/geheim/")
        token = _token(await _config_of(client, "/aurora/geheim/"))
        assert (token.session_id, token.access_session_id, token.key_id) == (None, None, str(world.key_id))

    async def test_a_key_cookie_the_gate_did_not_need_is_not_carried(self, client, environment):
        """A public site lets everyone in before any link is looked at: no
        link in the token, and no ticket on the way to the consent page."""
        cookie = sign_key_cookie(environment.app.state.settings.session_secret, str(environment.world.key_id))
        client.cookies.set(KEY_COOKIE, cookie, domain="toon.example", path="/aurora/feedback/")
        config = await _config_of(client)
        assert _token(config).key_id is None
        assert config["accountUrl"] == "/-/feedback/account?returnTo=%2Faurora%2Ffeedback%2F"

    async def test_the_page_names_nobody(self, client, environment):
        """No name, email address, subject or session reference in the
        config or anywhere else in the page: the site's own scripts read it."""
        session = _viewer(environment.app, name="Jan Jansen", email="jan.jansen@example.org")
        environment.app.state.session_store.consent_feedback(session.id, str(environment.world.site_ids["feedback"]))
        _set_site_cookie(client, environment.app, session.id, "feedback")
        response = await client.get("/aurora/feedback/", headers=NAVIGATION)
        tag = _tag_in(response.content, DOC, DOCTYPE_END)
        config = _config(tag)
        assert config["account"] is True
        text = json.dumps(config)
        # Each with a character base64url lacks, or too long to turn up in
        # random token bytes by chance.
        for secret in ("Jan Jansen", "jan.jansen@example.org", "kijker-sub", session.id):
            assert secret not in text, secret
            assert secret.encode() not in response.content, secret


async def _unshield(environment: Environment, site: str) -> None:
    async with environment.factory() as db:
        await db.execute(update(Site).where(Site.id == environment.world.site_ids[site]).values(sandbox=False))
        await db.commit()


class TestWithoutTheShielding:
    """The shielding plays no part in what the page offers: without it the
    token carries the consented session and the config the way to the consent
    page, as on a shielded page. The risk of that is accepted in
    docs/security.md."""

    @pytest.mark.parametrize(
        ("site", "consented", "roles"),
        [
            pytest.param("feedback", False, "none", id="public-without-consent"),
            pytest.param("feedback", True, "own", id="public-with-consent"),
            pytest.param("besloten", False, "gate", id="sign-in-without-consent"),
            pytest.param("besloten", True, "own", id="sign-in-with-consent"),
            pytest.param("intern", False, "gate", id="account-only-without-consent"),
            pytest.param("intern", True, "own", id="account-only-with-consent"),
        ],
    )
    async def test_the_account_as_on_a_shielded_page(self, client, environment, site, consented, roles):
        await _unshield(environment, site)
        session = _viewer(environment.app)
        if consented:
            environment.app.state.session_store.consent_feedback(session.id, str(environment.world.site_ids[site]))
        _set_site_cookie(client, environment.app, session.id, site)
        response = await client.get(f"/aurora/{site}/", headers=NAVIGATION)
        assert response.headers["content-security-policy"] == FULL_CSP
        config = _config(_tag_in(response.content, DOC, DOCTYPE_END))
        assert config.keys() == CONFIG_KEYS
        assert config["account"] is consented
        assert config["accountUrl"] == f"/-/feedback/account?returnTo=%2Faurora%2F{site}%2F"
        token = _token(config)
        expected = {"none": (None, None), "gate": (None, session.id), "own": (session.id, None)}[roles]
        assert (token.session_id, token.access_session_id) == expected

    async def test_beside_a_secret_link_the_ticket(self, client, environment):
        world = environment.world
        await _unshield(environment, "geheim")
        cookie = sign_key_cookie(environment.app.state.settings.session_secret, str(world.key_id))
        client.cookies.set(KEY_COOKIE, cookie, domain="toon.example", path="/aurora/geheim/")
        config = await _config_of(client, "/aurora/geheim/")
        token = _token(config)
        assert token.key_id == str(world.key_id)
        assert _link_of(config) == LinkTicket(
            key_id=str(world.key_id), site_id=world.site_ids["geheim"], expires_at=token.expires_at
        )


def _filled(unit: bytes) -> bytes:
    return (unit * (PREFIX_BYTES // len(unit) + 1))[:PREFIX_BYTES]


def _cpu_ms(prefix: bytes) -> float:
    """The CPU time this thread spends on `insertion_point(prefix)`, the best
    of three: what other work on the machine costs stays out of it."""
    spent = []
    for _ in range(3):
        start = time.thread_time()
        insertion_point(prefix)
        spent.append(time.thread_time() - start)
    return min(spent) * 1000


class TestCost:
    @pytest.mark.parametrize("unit", [b"<!---->", b"<!-->", b"<!--->", b"<!--x--!>"])
    def test_a_prefix_packed_with_comments_is_examined_in_one_pass(self, unit: bytes):
        """Every navigation to such a page examines its first 64 KiB again,
        under the GIL. A search per comment that runs on to the end of the
        prefix makes that quadratic: a third of a second of CPU per
        navigation instead of a few milliseconds. Measured against a prefix
        of bogus comments of the same size, so the bound holds on a slow
        machine and under coverage alike."""
        packed = _filled(unit)
        assert insertion_point(packed) is None
        spent, reference = _cpu_ms(packed), _cpu_ms(_filled(b"<?>"))
        assert spent < 2 * reference, f"{spent:.1f} ms against {reference:.1f} ms"

    async def test_a_page_view_with_the_widget_costs_one_query(self, client, environment):
        """The decision brings the site's feedback settings along, the token
        comes from the session store: nothing more is asked of the database."""
        engine = environment.factory.kw["bind"].sync_engine
        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            response = await client.get("/aurora/feedback/", headers=NAVIGATION)
        finally:
            event.remove(engine, "before_cursor_execute", record)

        _tag_in(response.content, DOC, DOCTYPE_END)
        queries = [statement for statement in statements if "SAVEPOINT" not in statement]
        assert len(queries) == 1, statements


# --- The whole content host --------------------------------------------------


def _whole_host(environment: Environment, idp: MockIdP | None = None) -> FastAPI:
    """The content host as main.py mounts it, on this environment's state:
    the feedback routes with their API, the login, then serving. The OIDC
    client is a mock, which the login reads only when it has to go to the
    IdP: pass `idp` to sign in through it."""
    source = environment.app.state
    app = make_feedback_app(source.settings, environment.factory)
    app.state.session_store = source.session_store
    app.state.content_store = source.content_store
    app.state.audit_log = source.audit_log
    app.state.feedback_widget = source.feedback_widget
    oidc_settings = source.settings.model_copy(update={"oidc_client_private_jwk": make_client_jwk()})
    app.state.oidc_client = make_oidc_client(oidc_settings, MockIdP() if idp is None else idp)
    app.include_router(pages.router)
    app.include_router(serving_router)
    app.add_middleware(ContentHeadMiddleware, content_host="toon.example")
    return app


def _nonce_of(page: httpx.Response) -> str:
    marker = 'name="nonce" value="'
    start = page.text.index(marker) + len(marker)
    return page.text[start : page.text.index('"', start)]


async def _stored(environment: Environment) -> list[FeedbackItem]:
    async with environment.factory() as db:
        return list((await db.scalars(select(FeedbackItem))).all())


def _sent(path: str, author: dict[str, object]) -> dict[str, object]:
    return item(page={"path": path, "title": None}, author=author)


def _recording_client(app: FastAPI, addresses: list[str]) -> httpx.AsyncClient:
    """A client that notes the address of every request it sends and every
    Location it gets back."""

    async def request(sent: httpx.Request) -> None:
        addresses.append(str(sent.url))

    async def response(answer: httpx.Response) -> None:
        addresses.extend(answer.headers.get_list("location"))

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=BASE_URL,
        follow_redirects=False,
        event_hooks={"request": [request], "response": [response]},
    )


SHARED = {"/aurora/gedeeld/": (DOC, DOCTYPE_END), "/aurora/gedeeld/_preview/pr-5/": (PREVIEW_DOC, PREVIEW_DOCTYPE_END)}


class TestTheWholeContentHost:
    async def test_from_the_consent_page_to_feedback_with_the_account(self, environment):
        """The form posts from an opaque origin without a cookie, the answer
        sets none, and the login after it hands out the site cookie from the
        anchor: back on the page in account state."""
        app = _whole_host(environment)
        session = _viewer(app, name="Kijker Jansen")
        async with _client(app) as browser, _client(app) as opaque_form:
            browser.cookies.set(
                CONTENT_ANCHOR_COOKIE, sign(SECRET, session.id), domain="toon.example", path=CONTENT_ANCHOR_PATH
            )
            _set_site_cookie(browser, app, session.id, "besloten")
            before = await _config_of(browser, "/aurora/besloten/")
            assert before["account"] is False
            page = await browser.get(before["accountUrl"], headers=NAVIGATION)
            assert page.status_code == 200
            posted = await opaque_form.post(
                PATH_FEEDBACK_ACCOUNT, data={"nonce": _nonce_of(page), "agree": "1"}, headers=FORM
            )
            assert posted.status_code == 303
            assert "set-cookie" not in posted.headers
            back = await browser.get(posted.headers["location"], headers=NAVIGATION)
            assert (back.status_code, back.headers["location"]) == (303, "/aurora/besloten/")
            after = await _config_of(browser, "/aurora/besloten/")
            assert after["account"] is True
            token = _token(after)
            assert (token.session_id, token.access_session_id) == (session.id, None)
            created = await browser.post(
                ITEMS, files=parts(_sent("/aurora/besloten/", {"kind": "account"})), headers=auth(after["token"])
            )
            assert created.status_code == 201, created.text
        (stored,) = await _stored(environment)
        assert (stored.author_kind, stored.author_sub, stored.author_name) == (
            FeedbackIdentity.ACCOUNT,
            "kijker-sub",
            "Kijker Jansen",
        )

    @pytest.mark.parametrize("path", list(SHARED), ids=["live", "preview"])
    async def test_from_a_secret_link_through_the_sign_in_to_feedback_with_the_account(self, environment, path):
        """A site shared by secret link alone that takes feedback with an
        account only. The key cookie stays on the page's path, so the way
        to the consent page carries the link as a ticket: through the
        sign-in, onto the consent page and into its nonce, and the gate lets
        the signed-in visitor in with it at each step. Back on the page in
        account state, an item goes in under the visitor's name, and the
        page token was in no address on the way."""
        idp = MockIdP()
        idp.token_claim_overrides = {"name": "Kijker Jansen"}
        app = _whole_host(environment, idp)
        world = environment.world
        original, point = SHARED[path]
        addresses: list[str] = []
        async with _recording_client(app, addresses) as browser, _client(app) as opaque_form:
            redeemed = await browser.get(path, params={"key": world.shared_key}, headers=NAVIGATION)
            assert (redeemed.status_code, redeemed.headers["location"]) == (302, path)
            before = _config(_tag_in((await browser.get(path, headers=NAVIGATION)).content, original, point))
            assert (before["identity"], before["account"]) == ("account", False)
            token = _token(before)
            assert (token.session_id, token.access_session_id) == (None, None)
            assert _link_of(before) == LinkTicket(
                key_id=str(world.shared_key_id), site_id=world.site_ids["gedeeld"], expires_at=token.expires_at
            )

            to_login = await browser.get(before["accountUrl"], headers=NAVIGATION)
            assert to_login.status_code == 302
            (way_back,) = parse_qs(urlsplit(to_login.headers["location"]).query)["returnTo"]
            assert way_back == before["accountUrl"]
            signed_in = await complete_login(browser, idp, return_to=way_back, headers=NAVIGATION)
            assert (signed_in.status_code, signed_in.headers["location"]) == (303, before["accountUrl"])

            page = await browser.get(signed_in.headers["location"], headers=NAVIGATION)
            assert page.status_code == 200
            assert "<dt>Naam</dt>\n<dd>Kijker Jansen</dd>" in page.text
            posted = await opaque_form.post(
                PATH_FEEDBACK_ACCOUNT, data={"nonce": _nonce_of(page), "agree": "1"}, headers=FORM
            )
            assert posted.status_code == 303
            assert "set-cookie" not in posted.headers
            back = await browser.get(posted.headers["location"], headers=NAVIGATION)
            assert (back.status_code, back.headers["location"]) == (303, path)

            after = _config(_tag_in((await browser.get(path, headers=NAVIGATION)).content, original, point))
            assert after["account"] is True
            consented = _token(after)
            assert consented.key_id == str(world.shared_key_id)
            assert consented.session_id is not None
            assert after["accountUrl"] == f"/-/feedback/account?returnTo={path.replace('/', '%2F')}"
            created = await browser.post(
                ITEMS, files=parts(_sent(path, {"kind": "account"})), headers=auth(after["token"])
            )
            assert created.status_code == 201, created.text
        (stored,) = await _stored(environment)
        assert (stored.author_kind, stored.author_sub, stored.author_name) == (
            FeedbackIdentity.ACCOUNT,
            "gebruiker-1",
            "Kijker Jansen",
        )
        assert any("link=" in address for address in addresses)
        for address in addresses:
            assert before["token"] not in address
            assert after["token"] not in address

    async def test_without_the_ticket_the_sign_in_leads_nowhere(self, environment):
        """What the ticket is there for: the same way without it ends on the
        neutral 404 once the visitor has signed in, because only the link
        lets them see the page."""
        idp = MockIdP()
        app = _whole_host(environment, idp)
        async with _client(app) as browser:
            await browser.get("/aurora/gedeeld/", params={"key": environment.world.shared_key}, headers=NAVIGATION)
            config = await _config_of(browser, "/aurora/gedeeld/")
            bare = config["accountUrl"].split("&link=", 1)[0]
            signed_in = await complete_login(browser, idp, return_to=bare, headers=NAVIGATION)
            assert signed_in.headers["location"] == bare
            assert is_neutral_404(await browser.get(bare, headers=NAVIGATION))

    async def test_somebody_elses_nonce_leaves_the_visitor_their_own_session(self, environment):
        """A page elsewhere posts another person's nonce in this visitor's
        browser. What follows hands this browser its own session again, which
        never consented: the widget offers no account, and feedback sent from
        the page is nobody's but this visitor's."""
        app = _whole_host(environment)
        other = _viewer(app, name="Ander Persoon")
        own = app.state.session_store.create_session(
            sub="eigen-sub",
            email="eigen@example.org",
            email_verified=True,
            acr="urn:acr:hoog",
            kind=SessionKind.CONTENT,
        )
        nonce = mint_consent_nonce(
            SECRET,
            session_id=other.id,
            site_id=environment.world.site_ids["feedback"],
            return_to="/aurora/feedback/",
            now=int(time.time()),
        )
        async with _client(app) as browser, _client(app) as cross_site:
            browser.cookies.set(
                CONTENT_ANCHOR_COOKIE, sign(SECRET, own.id), domain="toon.example", path=CONTENT_ANCHOR_PATH
            )
            _set_site_cookie(browser, app, own.id, "feedback")
            posted = await cross_site.post(
                PATH_FEEDBACK_ACCOUNT,
                data={"nonce": nonce, "agree": "1"},
                headers={**FORM, "Sec-Fetch-Site": "cross-site", "Origin": "https://kwaad.example"},
            )
            assert posted.status_code == 303
            assert "set-cookie" not in posted.headers
            back = await browser.get(posted.headers["location"], headers=NAVIGATION)
            assert back.status_code == 303
            config = await _config_of(browser, back.headers["location"])
            assert config["account"] is False
            token = _token(config)
            assert (token.session_id, token.access_session_id) == (None, None)
            as_account = await browser.post(
                ITEMS, files=parts(_sent("/aurora/feedback/", {"kind": "account"})), headers=auth(config["token"])
            )
            assert_problem(as_account, 403, "FEEDBACK_ACCOUNT_REQUIRED")
        assert await _stored(environment) == []

    async def test_an_anchor_set_from_elsewhere_gets_no_site_cookie_out_of_the_login(self, environment):
        """Another person consented on the site, and the script of an
        unshielded page plants that person's anchor at `/`, beside the
        visitor's own, and sends the tab to the login. The visitor already
        holds a site cookie of their own there. The login hands out nothing
        while the anchor arrives twice, so that cookie stays, and the page
        token names nobody: the widget offers no account, and nothing goes in
        under the other person's name."""
        app = _whole_host(environment)
        store = app.state.session_store
        planter = _viewer(app, name="Ander Persoon")
        store.consent_feedback(planter.id, str(environment.world.site_ids["feedback"]))
        own = store.create_session(
            sub="eigen-sub",
            email="eigen@example.org",
            email_verified=True,
            acr="urn:acr:hoog",
            kind=SessionKind.CONTENT,
        )
        async with _client(app) as browser:
            browser.cookies.set(
                CONTENT_ANCHOR_COOKIE, sign(SECRET, own.id), domain="toon.example", path=CONTENT_ANCHOR_PATH
            )
            _set_site_cookie(browser, app, own.id, "feedback")
            browser.cookies.set(CONTENT_ANCHOR_COOKIE, sign(SECRET, planter.id), domain="toon.example", path="/")
            login = await browser.get("/-/login", params={"returnTo": "/aurora/feedback/"}, headers=NAVIGATION)
            assert login.status_code == 302
            assert login.headers["location"].startswith("https://idp.example/authorize?")
            assert not any(
                header.startswith(f"{CONTENT_SESSION_COOKIE}=") for header in login.headers.get_list("set-cookie")
            )
            config = await _config_of(browser)
            assert config["account"] is False
            assert (_token(config).session_id, _token(config).access_session_id) == (None, None)
            as_account = await browser.post(
                ITEMS, files=parts(_sent("/aurora/feedback/", {"kind": "account"})), headers=auth(config["token"])
            )
            assert_problem(as_account, 403, "FEEDBACK_ACCOUNT_REQUIRED")
        assert store.get_session(planter.id).content_sites == frozenset()
        assert await _stored(environment) == []

    @pytest.mark.parametrize("path", ["/aurora/besloten/", "/aurora/besloten/_preview/pr-4/"], ids=["live", "preview"])
    async def test_without_consent_a_signed_in_visitor_sends_with_a_name(self, environment, path):
        """On a page that needs a sign-in. Nothing of the session goes with
        the item, the visitor's own items and feedback with the account stay
        behind consent, and a logout ends what the token can do."""
        app = _whole_host(environment)
        session = _viewer(app, name="Kijker Jansen", email="kijker@example.org")
        named = parts(_sent(path, {"kind": "name", "name": "Jan"}))
        async with _client(app) as browser:
            _set_site_cookie(browser, app, session.id, "besloten")
            response = await browser.get(path, headers=NAVIGATION)
            original, point = (DOC, DOCTYPE_END) if "_preview" not in path else (PREVIEW_DOC, PREVIEW_DOCTYPE_END)
            config = _config(_tag_in(response.content, original, point))
            assert (config["identity"], config["account"]) == ("name", False)
            token = config["token"]
            created = await browser.post(ITEMS, files=named, headers=auth(token))
            assert created.status_code == 201, created.text
            assert_problem(await browser.get(ITEMS, headers=auth(token)), 403, "FEEDBACK_ACCOUNT_REQUIRED")
            as_account = await browser.post(
                ITEMS, files=parts(_sent(path, {"kind": "account"})), headers=auth(token)
            )
            assert_problem(as_account, 403, "FEEDBACK_ACCOUNT_REQUIRED")
            app.state.session_store.delete_session(session.id)
            assert_problem(await browser.post(ITEMS, files=named, headers=auth(token)), 404, "FEEDBACK_UNAVAILABLE")
        (stored,) = await _stored(environment)
        assert (stored.author_kind, stored.author_sub, stored.author_name, stored.author_email) == (
            FeedbackIdentity.NAME,
            None,
            "Jan",
            None,
        )

    async def test_hiding_and_showing_the_button(self, environment):
        """Hidden on the site, live and previews alike, and on no other site.
        A copy of the mark at `/`, as a page's own script could plant it, goes
        with the show link too."""
        app = _whole_host(environment)
        async with _client(app) as browser:
            config = await _config_of(browser)
            hidden = await browser.post(
                PATH_FEEDBACK_HIDE, data={"token": config["token"], "returnTo": "/aurora/feedback/"}, headers=FORM
            )
            assert (hidden.status_code, hidden.headers["location"]) == (303, "/aurora/feedback/")
            assert (await browser.get("/aurora/feedback/", headers=NAVIGATION)).content == DOC
            assert (await browser.get("/aurora/feedback/_preview/pr-1/", headers=NAVIGATION)).content == PREVIEW_DOC
            other = await browser.get("/aurora/alleen-preview/_preview/pr-2/", headers=NAVIGATION)
            _tag_in(other.content, PREVIEW_DOC, PREVIEW_DOCTYPE_END)

            copy = hidden.cookies[FEEDBACK_HIDE_COOKIE]
            browser.cookies.set(FEEDBACK_HIDE_COOKIE, copy, domain="toon.example", path="/")
            shown = await browser.get(config["showUrl"], headers=NAVIGATION)
            assert (shown.status_code, shown.headers["location"]) == (303, "/aurora/feedback/")
            assert [cookie for cookie in browser.cookies.jar if cookie.name == FEEDBACK_HIDE_COOKIE] == []
            await _config_of(browser)

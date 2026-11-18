"""Tests for serving/feedback_editor.py and its route: reading the editor at
startup and the three ways it can be unavailable, the frame document and its
policy, and the two addresses on the content host with every ground for a
neutral 404 on its own. No database needed."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from test_feedback_consent import is_neutral_404
from test_serving import BASE_URL, make_settings

from toon.constants import PATH_CONTENT_FEEDBACK_PREFIX
from toon.host_separation import belongs_to_content
from toon.serving.feedback_editor import (
    FeedbackEditor,
    editor_csp,
    frame_url,
    load_feedback_editor,
    script_url,
)
from toon.serving.feedback_routes import router as feedback_router
from toon.serving.feedback_widget import WidgetBundle

LOGGER = "toon.serving.feedback_editor"
SCRIPT = b'/*! Toon feedback editor. */(()=>{"use strict";})();\n'
SCRIPT_HASH = hashlib.sha256(SCRIPT).hexdigest()[:16]
ADMIN_URL = "https://beheer.toon.example"


def _frame(script_hash: str) -> bytes:
    return (
        b"<!doctype html>\n"
        b"<html>\n"
        b"<head>\n"
        b'<meta charset="utf-8">\n'
        b'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        b"</head>\n"
        b"<body>\n"
        b'<script src="/-/feedback/editor.' + script_hash.encode() + b'.js"></script>\n'
        b"</body>\n"
        b"</html>\n"
    )


FRAME = _frame(SCRIPT_HASH)
FRAME_HASH = hashlib.sha256(FRAME).hexdigest()[:16]
SCRIPT_URL = f"/-/feedback/editor.{SCRIPT_HASH}.js"
FRAME_URL = f"/-/feedback/editor.{FRAME_HASH}.html"


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == LOGGER and record.levelno == logging.WARNING]


def _load(tmp_path: Path, content: bytes = SCRIPT, content_base_url: str = BASE_URL) -> FeedbackEditor | None:
    path = tmp_path / "feedback-editor.js"
    path.write_bytes(content)
    return load_feedback_editor(path, content_base_url=content_base_url)


class TestUnavailable:
    """Read as the widget bundle is, every way it can be unavailable tested
    there (test_feedback_widget_bundle.py)."""

    @pytest.mark.parametrize("content", [None, b""], ids=["missing", "empty"])
    def test_no_editor_and_one_warning_that_says_what_the_widget_does_instead(
        self, tmp_path: Path, caplog, content: bytes | None
    ):
        path = tmp_path / "feedback-editor.js"
        if content is not None:
            path.write_bytes(content)
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert load_feedback_editor(path, content_base_url=BASE_URL) is None
        [warning] = _warnings(caplog)
        assert str(path) in warning.getMessage()
        assert "plain text field" in warning.getMessage()


class TestEditor:
    def test_the_script_the_frame_and_their_addresses(self, tmp_path: Path, caplog):
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            editor = _load(tmp_path)
        assert editor == FeedbackEditor(
            script=WidgetBundle(content=SCRIPT, hash=SCRIPT_HASH),
            frame=FRAME,
            frame_hash=FRAME_HASH,
            csp=editor_csp(BASE_URL, SCRIPT_URL),
        )
        assert _warnings(caplog) == []
        assert (script_url(editor.script), frame_url(editor)) == (SCRIPT_URL, FRAME_URL)
        for url in (SCRIPT_URL, FRAME_URL):
            assert url.startswith(PATH_CONTENT_FEEDBACK_PREFIX)
            assert belongs_to_content(url)

    def test_another_build_moves_both_addresses(self, tmp_path: Path):
        """The frame names the script, so a cached frame never loads a script
        of another build."""
        first, second = tmp_path / "a", tmp_path / "b"
        first.mkdir()
        second.mkdir()
        one, other = _load(first), _load(second, SCRIPT + b"//")
        assert script_url(one.script) != script_url(other.script)
        assert frame_url(one) != frame_url(other)
        assert other.frame == _frame(other.script.hash)


class TestPolicy:
    def test_the_policy(self):
        """No frame-ancestors and no style-src: see editor_csp."""
        assert editor_csp("https://example.test:8445", "/-/feedback/editor.0123456789abcdef.js") == (
            "default-src 'none'; "
            "script-src https://example.test:8445/-/feedback/editor.0123456789abcdef.js; "
            "base-uri 'none'; "
            "form-action 'none'; "
            "sandbox allow-scripts"
        )

    @pytest.mark.parametrize(
        ("content_base_url", "origin"),
        [
            ("https://toon.example", "https://toon.example"),
            ("https://toon.example/", "https://toon.example"),
            ("https://Toon.Example:8445/pad", "https://toon.example:8445"),
            ("http://localhost:8889", "http://localhost:8889"),
        ],
    )
    def test_the_script_source_is_the_exact_url_on_the_content_origin(self, content_base_url: str, origin: str):
        """The port counts and nothing past the origin does: the preview
        runs on a port of its own."""
        csp = editor_csp(content_base_url, SCRIPT_URL)
        assert f"script-src {origin}{SCRIPT_URL};" in csp


def _app(tmp_path: Path, editor: FeedbackEditor | None) -> FastAPI:
    """The feedback routes as main.py mounts them; no middleware, so an
    answer is exactly what the route built."""
    app = FastAPI()
    app.state.settings = make_settings(tmp_path / "content").model_copy(update={"base_url": ADMIN_URL})
    app.state.feedback_editor = editor
    app.include_router(feedback_router)
    return app


def _client(app: FastAPI, base_url: str = BASE_URL) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url, follow_redirects=False)


@pytest_asyncio.fixture
async def app(tmp_path: Path) -> AsyncIterator[FastAPI]:
    yield _app(tmp_path, _load(tmp_path))


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with _client(app) as c:
        yield c


def _headers(response: httpx.Response) -> dict[str, str]:
    return dict(sorted((k.lower(), v) for k, v in response.headers.multi_items()))


class TestServed:
    async def test_the_script(self, client):
        response = await client.get(SCRIPT_URL)
        assert response.status_code == 200
        assert response.content == SCRIPT
        assert _headers(response) == {
            "cache-control": "public, max-age=31536000, immutable",
            "content-length": str(len(SCRIPT)),
            "content-type": "text/javascript; charset=utf-8",
            "x-content-type-options": "nosniff",
        }

    async def test_the_frame(self, client):
        response = await client.get(FRAME_URL)
        assert response.status_code == 200
        assert response.content == FRAME
        assert _headers(response) == {
            "cache-control": "public, max-age=31536000, immutable",
            "content-length": str(len(FRAME)),
            "content-security-policy": editor_csp(BASE_URL, SCRIPT_URL),
            "content-type": "text/html; charset=utf-8",
            "referrer-policy": "no-referrer",
            "x-content-type-options": "nosniff",
        }

    async def test_a_query_changes_nothing(self, client):
        assert (await client.get(f"{FRAME_URL}?x=1")).content == FRAME


class TestRefused:
    """Each ground on its own, every refusal the neutral 404 byte for byte."""

    @pytest.mark.parametrize("path", [SCRIPT_URL, FRAME_URL])
    async def test_on_the_admin_host(self, app, path: str):
        async with _client(app, ADMIN_URL) as admin:
            assert is_neutral_404(await admin.get(path))

    @pytest.mark.parametrize("path", [SCRIPT_URL, FRAME_URL])
    async def test_without_an_editor(self, tmp_path: Path, path: str):
        app = _app(tmp_path, None)
        async with _client(app) as client:
            assert is_neutral_404(await client.get(path))
            del app.state.feedback_editor
            assert is_neutral_404(await client.get(path))

    @pytest.mark.parametrize(
        "path",
        [
            "/-/feedback/editor.0000000000000000.js",
            "/-/feedback/editor.0000000000000000.html",
            f"/-/feedback/editor.{SCRIPT_HASH}.html",
            f"/-/feedback/editor.{FRAME_HASH}.js",
            f"/-/feedback/editor.{SCRIPT_HASH}",
            f"/-/feedback/editor.{FRAME_HASH}",
            f"/-/feedback/editor.{SCRIPT_HASH.upper()}.js",
            f"/-/feedback/editor.{FRAME_HASH.upper()}.html",
            f"/-/feedback/editor.{FRAME_HASH}.html/",
            f"/-/feedback/editor.{FRAME_HASH}.htm",
            f"/-/feedback/widget.{SCRIPT_HASH}.js",
            "/-/feedback/editor.",
        ],
    )
    async def test_any_other_name(self, client, path: str):
        assert is_neutral_404(await client.get(path))

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
    @pytest.mark.parametrize("path", [SCRIPT_URL, FRAME_URL])
    async def test_any_other_method(self, client, method: str, path: str):
        """HEAD included, as for the widget bundle: under /-/ a HEAD stays a
        HEAD, which no route here takes."""
        response = await client.request(method, path)
        refused = await client.get("/-/feedback/editor.0000000000000000.js")
        assert is_neutral_404(refused)
        assert response.status_code == 404
        assert _headers(response) == _headers(refused)
        assert response.content == (b"" if method == "HEAD" else refused.content)

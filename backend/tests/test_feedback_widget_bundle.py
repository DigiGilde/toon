"""Tests for serving/feedback_widget.py: reading the widget bundle at startup,
its content address, and the three ways it can be unavailable, each of which
leaves Toon running with one warning. The same for the place script, which
also has to be fit to go inline. No database needed."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import pytest

from toon.constants import PATH_CONTENT_FEEDBACK_PREFIX
from toon.host_separation import belongs_to_content
from toon.serving.feedback_widget import WidgetBundle, load_place_script, load_widget_bundle, widget_url

LOGGER = "toon.serving.feedback_widget"
BUNDLE = b'/*! Toon feedback widget. */(()=>{"use strict";})();\n'


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == LOGGER and record.levelno == logging.WARNING]


def _directory(path: Path) -> None:
    path.mkdir()


def _empty(path: Path) -> None:
    """A broken build rather than a widget: adding a script that does
    nothing to every page would only cost them their caching."""
    path.write_bytes(b"")


def _unreadable(path: Path) -> None:
    if os.name != "posix" or os.geteuid() == 0:
        pytest.skip("root reads a file whatever its mode")
    path.write_bytes(BUNDLE)
    path.chmod(0)


@pytest.mark.parametrize(
    "spoil",
    [lambda path: None, _directory, _unreadable, _empty],
    ids=["missing", "a-directory", "no-read-permission", "empty"],
)
def test_an_unavailable_bundle_is_none_with_one_warning(tmp_path: Path, caplog, spoil):
    path = tmp_path / "feedback-widget.js"
    spoil(path)
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert load_widget_bundle(path) is None
    finally:
        if path.is_file():
            path.chmod(0o600)
    [warning] = _warnings(caplog)
    assert str(path) in warning.getMessage()


def test_the_bundle_is_the_file_and_its_address_the_start_of_its_hash(tmp_path: Path, caplog):
    path = tmp_path / "feedback-widget.js"
    path.write_bytes(BUNDLE)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        bundle = load_widget_bundle(path)
    expected = hashlib.sha256(BUNDLE).hexdigest()[:16]
    assert bundle == WidgetBundle(content=BUNDLE, hash=expected)
    assert _warnings(caplog) == []
    url = widget_url(bundle)
    assert url == f"/-/feedback/widget.{expected}.js"
    assert url.startswith(PATH_CONTENT_FEEDBACK_PREFIX)
    assert belongs_to_content(url)


PLACE = b'(function(){var s=document.currentScript;if(s&&location.hash<"#")return;})();\n'


@pytest.mark.parametrize(
    "spoil",
    [lambda path: None, _directory, _unreadable, _empty],
    ids=["missing", "a-directory", "no-read-permission", "empty"],
)
def test_an_unavailable_place_script_is_none_with_one_warning(tmp_path: Path, caplog, spoil):
    path = tmp_path / "feedback-place.js"
    spoil(path)
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert load_place_script(path) is None
    finally:
        if path.is_file():
            path.chmod(0o600)
    [warning] = _warnings(caplog)
    assert str(path) in warning.getMessage()


def test_the_place_script_is_the_file_without_its_last_newline(tmp_path: Path, caplog):
    path = tmp_path / "feedback-place.js"
    path.write_bytes(PLACE)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert load_place_script(path) == PLACE.strip()
    assert _warnings(caplog) == []


@pytest.mark.parametrize(
    "content",
    [
        b'(function(){var a="</script>";})();',
        b'(function(){var a="</SCRIPT ";})();',
        b'(function(){var a="<script>";})();',
        b'(function(){var a="<!--";})();',
        "(function(){var a='\u00e9';})();".encode(),
    ],
    ids=["ends-the-element", "ends-it-in-capitals", "opens-a-script", "opens-a-comment", "not-ascii"],
)
def test_a_place_script_that_cannot_stand_inline_is_none_with_one_warning(tmp_path: Path, caplog, content):
    path = tmp_path / "feedback-place.js"
    path.write_bytes(content)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert load_place_script(path) is None
    [warning] = _warnings(caplog)
    assert "cannot go inline" in warning.getMessage()

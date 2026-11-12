"""Tests for serving/feedback_widget.py: reading the widget bundle at startup,
its content address, and the three ways it can be unavailable, each of which
leaves Toon running with one warning. No database needed."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

import pytest

from toon.constants import PATH_CONTENT_FEEDBACK_PREFIX
from toon.host_separation import belongs_to_content
from toon.serving.feedback_widget import WidgetBundle, load_widget_bundle, widget_url

LOGGER = "toon.serving.feedback_widget"
BUNDLE = b'/*! Toon feedback widget. */(()=>{"use strict";})();\n'


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == LOGGER and record.levelno == logging.WARNING]


class TestUnavailable:
    def test_a_missing_file(self, tmp_path: Path, caplog):
        path = tmp_path / "feedback-widget.js"
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert load_widget_bundle(path) is None
        [warning] = _warnings(caplog)
        assert str(path) in warning.getMessage()

    def test_a_directory_in_its_place(self, tmp_path: Path, caplog):
        path = tmp_path / "feedback-widget.js"
        path.mkdir()
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert load_widget_bundle(path) is None
        [warning] = _warnings(caplog)
        assert str(path) in warning.getMessage()

    @pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="root reads a file whatever its mode")
    def test_a_file_without_read_permission(self, tmp_path: Path, caplog):
        path = tmp_path / "feedback-widget.js"
        path.write_bytes(BUNDLE)
        path.chmod(0)
        try:
            with caplog.at_level(logging.WARNING, logger=LOGGER):
                assert load_widget_bundle(path) is None
        finally:
            path.chmod(0o600)
        [warning] = _warnings(caplog)
        assert str(path) in warning.getMessage()

    def test_an_empty_file(self, tmp_path: Path, caplog):
        """A broken build rather than a widget: adding a script that does
        nothing to every page would only cost them their caching."""
        path = tmp_path / "feedback-widget.js"
        path.write_bytes(b"")
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert load_widget_bundle(path) is None
        [warning] = _warnings(caplog)
        assert str(path) in warning.getMessage()


class TestBundle:
    def test_the_bundle_is_the_file_and_the_start_of_its_hash(self, tmp_path: Path, caplog):
        path = tmp_path / "feedback-widget.js"
        path.write_bytes(BUNDLE)
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            bundle = load_widget_bundle(path)
        assert bundle == WidgetBundle(content=BUNDLE, hash=hashlib.sha256(BUNDLE).hexdigest()[:16])
        assert _warnings(caplog) == []

    def test_another_build_gets_another_address(self, tmp_path: Path):
        first, second = tmp_path / "a.js", tmp_path / "b.js"
        first.write_bytes(BUNDLE)
        second.write_bytes(BUNDLE + b"//")
        assert widget_url(load_widget_bundle(first)) != widget_url(load_widget_bundle(second))

    def test_the_same_build_keeps_its_address(self, tmp_path: Path):
        first, second = tmp_path / "a.js", tmp_path / "b.js"
        first.write_bytes(BUNDLE)
        second.write_bytes(BUNDLE)
        assert widget_url(load_widget_bundle(first)) == widget_url(load_widget_bundle(second))

    def test_the_address_lies_under_the_feedback_prefix_of_the_content_host(self, tmp_path: Path):
        path = tmp_path / "feedback-widget.js"
        path.write_bytes(BUNDLE)
        url = widget_url(load_widget_bundle(path))
        expected = hashlib.sha256(BUNDLE).hexdigest()[:16]
        assert url == f"/-/feedback/widget.{expected}.js"
        assert re.fullmatch(r"/-/feedback/widget\.[0-9a-f]{16}\.js", url)
        assert url.startswith(PATH_CONTENT_FEEDBACK_PREFIX)
        assert belongs_to_content(url)

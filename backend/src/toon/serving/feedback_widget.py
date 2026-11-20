"""The feedback widget bundle: the built script Toon adds to the pages of
sites with feedback on, read once at startup.

Its address carries the start of its SHA-256, so a page always loads the
bundle it was served with and the bundle can be cached for good. A bundle that
is missing, unreadable or empty does not stop Toon: pages are served without
the widget, and one warning says why.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from toon.constants import PATH_FEEDBACK_WIDGET_PREFIX

_logger = logging.getLogger(__name__)

_HASH_LENGTH = 16


@dataclass(frozen=True)
class WidgetBundle:
    content: bytes
    hash: str


def content_hash(content: bytes) -> str:
    """The first 16 hex characters of the SHA-256 of `content`, the part of a
    content address that changes with every build."""
    return hashlib.sha256(content).hexdigest()[:_HASH_LENGTH]


def read_build(path: Path, *, logger: logging.Logger, name: str, without: str) -> WidgetBundle | None:
    """The built file at `path` with its content hash, or None with one
    warning when it is missing, unreadable or empty."""
    try:
        content = path.read_bytes()
    except OSError as error:
        logger.warning("%s not readable (%s): %s until Toon restarts with it", name, error, without)
        return None
    if not content:
        logger.warning("%s %s is empty: %s until Toon restarts with a built one", name, path, without)
        return None
    return WidgetBundle(content=content, hash=content_hash(content))


def load_widget_bundle(path: Path) -> WidgetBundle | None:
    return read_build(path, logger=_logger, name="Feedback widget", without="pages get no feedback widget")


# What would end or bend an inline script element (WHATWG HTML, 4.12.1.3):
# the place script goes into pages as the text of one.
_NOT_INLINE = re.compile(rb"</script|<script|<!--", re.IGNORECASE)


def load_place_script(path: Path) -> bytes | None:
    """The place script (frontend/src/feedback-widget/place.ts), which goes
    inline into every page that gets the widget, or None with one warning
    when it is missing, unreadable, empty, not ASCII or not fit to stand
    inside a script element. Pages then get the widget without it."""
    bundle = read_build(path, logger=_logger, name="Feedback place script", without="pages get the widget without it")
    if bundle is None:
        return None
    if not bundle.content.isascii() or _NOT_INLINE.search(bundle.content):
        _logger.warning("Feedback place script %s cannot go inline: pages get the widget without it", path)
        return None
    return bundle.content.strip()


def widget_url(bundle: WidgetBundle) -> str:
    """The bundle's address on the content host, which changes with every
    build."""
    return f"{PATH_FEEDBACK_WIDGET_PREFIX}{bundle.hash}.js"


__all__ = [
    "WidgetBundle",
    "content_hash",
    "load_place_script",
    "load_widget_bundle",
    "read_build",
    "widget_url",
]

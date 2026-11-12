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
from dataclasses import dataclass
from pathlib import Path

from toon.constants import PATH_FEEDBACK_WIDGET_PREFIX

_logger = logging.getLogger(__name__)

_HASH_LENGTH = 16


@dataclass(frozen=True)
class WidgetBundle:
    content: bytes
    # The first 16 hex characters of the SHA-256 of `content`.
    hash: str


def load_widget_bundle(path: Path) -> WidgetBundle | None:
    try:
        content = path.read_bytes()
    except OSError as error:
        _logger.warning(
            "Feedback widget not readable (%s): pages get no feedback widget until Toon restarts with it",
            error,
        )
        return None
    if not content:
        _logger.warning(
            "Feedback widget %s is empty: pages get no feedback widget until Toon restarts with a built one",
            path,
        )
        return None
    return WidgetBundle(content=content, hash=hashlib.sha256(content).hexdigest()[:_HASH_LENGTH])


def widget_url(bundle: WidgetBundle) -> str:
    """The bundle's address on the content host, which changes with every
    build."""
    return f"{PATH_FEEDBACK_WIDGET_PREFIX}{bundle.hash}.js"


__all__ = [
    "WidgetBundle",
    "load_widget_bundle",
    "widget_url",
]

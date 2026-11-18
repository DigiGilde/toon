"""The feedback editor: the markdown editor the feedback widget opens in a
sandboxed frame on the content host, read once at startup.

Two addresses, both content-addressed: the built script under the start of
its SHA-256, and the frame document that loads it under the start of the
SHA-256 of the document itself, which names the script. Either can be cached
for good. An editor that is missing, unreadable or empty does not stop Toon:
the widget gets no editor address and offers a plain text field, and one
warning says why.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from toon.constants import PATH_FEEDBACK_EDITOR_PREFIX
from toon.serving.feedback_widget import WidgetBundle, content_hash, read_build

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedbackEditor:
    script: WidgetBundle
    frame: bytes
    frame_hash: str
    csp: str


def script_url(script: WidgetBundle) -> str:
    return f"{PATH_FEEDBACK_EDITOR_PREFIX}{script.hash}.js"


def frame_url(editor: FeedbackEditor) -> str:
    return f"{PATH_FEEDBACK_EDITOR_PREFIX}{editor.frame_hash}.html"


def frame_document(script_address: str) -> bytes:
    """The frame: the script and nothing else, no inline code and nothing a
    visitor or a site wrote."""
    return (
        "<!doctype html>\n"
        "<html>\n"
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "</head>\n"
        "<body>\n"
        f'<script src="{script_address}"></script>\n'
        "</body>\n"
        "</html>\n"
    ).encode("ascii")


def editor_csp(content_base_url: str, script_address: str) -> str:
    """The frame's policy: its one script by its full URL, since the content
    host also serves every published site's own scripts.

    No style-src, so no style element and no style attribute from markup:
    the script styles the frame with constructed stylesheets, which CSP does
    not govern, and sets inline styles through the CSSOM alone.

    No frame-ancestors: a sandboxed page has an opaque origin, which no
    source matches in Chrome and WebKit ('self', the host, `*` or a scheme
    alike; Firefox lets `*` through), so any value would keep the editor out
    of shielded sites."""
    parts = urlsplit(content_base_url)
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    return (
        "default-src 'none'; "
        f"script-src {origin}{script_address}; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "sandbox allow-scripts"
    )


def load_feedback_editor(path: Path, *, content_base_url: str) -> FeedbackEditor | None:
    script = read_build(
        path, logger=_logger, name="Feedback editor", without="the feedback widget offers a plain text field"
    )
    if script is None:
        return None
    frame = frame_document(script_url(script))
    return FeedbackEditor(
        script=script,
        frame=frame,
        frame_hash=content_hash(frame),
        csp=editor_csp(content_base_url, script_url(script)),
    )


__all__ = [
    "FeedbackEditor",
    "editor_csp",
    "frame_document",
    "frame_url",
    "load_feedback_editor",
    "script_url",
]

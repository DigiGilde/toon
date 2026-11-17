"""What Toon's own pages on the content host share: the code page of a secret
link and the consent page of site feedback.

The content host carries no design system, and a stylesheet of its own would
need a route under `/-/`, so each page inlines `PAGE_CSS` followed by its own
rules. Sizes in rem and em, so everything grows with the text at 200 percent,
and nothing has a fixed width, so 320 CSS px needs no horizontal scrolling.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import partial
from html import escape

from fastapi import Request
from starlette.datastructures import FormData
from starlette.formparsers import FormParser
from starlette.requests import ClientDisconnect

from toon import i18n

_FORM_TYPE = "application/x-www-form-urlencoded"

PAGE_CSS = """
:root {
  color-scheme: light;
  --toon-ground: #fff;
  --toon-text: #1c2022;
  --toon-brand: #154273;
  --toon-brand-contrast: #fff;
  --toon-line: #767676;
  --toon-critical: #a90000;
}

@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --toon-ground: #1c2022;
    --toon-text: #e7eaec;
    /* Rijksblauw reaches only 3:1 on this ground; this lighter tint 6.5:1. */
    --toon-brand: #8fb8e0;
    --toon-brand-contrast: #1c2022;
    --toon-line: #9aa4a9;
    --toon-critical: #ff8a80;
  }
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: var(--toon-ground);
  color: var(--toon-text);
  font-family: "RO Sans", "Segoe UI", system-ui, sans-serif;
  line-height: 1.6;
  overflow-wrap: break-word;
}

.page {
  max-width: 32rem;
  margin: 0 auto;
  padding: 1.5rem 1rem 2rem;
}

h1 {
  margin: 0 0 0.5rem;
  color: var(--toon-brand);
  font-size: 1.75rem;
  line-height: 1.2;
}

button {
  margin-top: 1rem;
  padding: 0.6em 1.2em;
  border: 0;
  border-radius: 0.25em;
  background: var(--toon-brand);
  color: var(--toon-brand-contrast);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

button:hover {
  text-decoration: underline;
}

:focus-visible {
  outline: 3px solid var(--toon-text);
  outline-offset: 2px;
}

.site-footer {
  margin-top: 3rem;
  border-top: 1px solid var(--toon-line);
  padding-top: 1rem;
}

.site-footer ul {
  margin: 0;
  padding: 0;
  list-style: none;
}
"""


def footer_html(locale: str, admin_origin: str) -> str:
    """The footer that links the accessibility statement at `admin_origin`
    (TOON_BASE_URL, never the Host header). Without one there is no footer."""
    if not admin_origin:
        return ""
    say = partial(i18n.t, locale)
    return (
        f'<footer class="site-footer">\n<nav aria-label="{say("footer.label")}">\n<ul>\n'
        f'<li><a href="{escape(admin_origin, quote=True)}/-/accessibility">{say("footer.accessibility")}</a></li>\n'
        "</ul>\n</nav>\n</footer>\n"
    )


class _FormTooLargeError(Exception):
    """Raised mid-stream once a form is longer than its bound, for a chunked
    request that carries no Content-Length to check upfront."""


async def bounded_form(request: Request, max_bytes: int) -> FormData | None:
    """The urlencoded form of a request, or None for anything else: another
    content type, a body longer than `max_bytes`, or one the client gave up
    on, whose ClientDisconnect would otherwise end in a 500 with a logged
    traceback while nobody reads the answer."""
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != _FORM_TYPE:
        return None
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        return None
    total = 0

    async def bounded() -> AsyncGenerator[bytes]:
        nonlocal total
        async for chunk in request.stream():
            total += len(chunk)
            if total > max_bytes:
                raise _FormTooLargeError
            yield chunk

    try:
        return await FormParser(request.headers, bounded()).parse()
    except (_FormTooLargeError, ClientDisconnect):
        return None


__all__ = ["PAGE_CSS", "bounded_form", "footer_html"]

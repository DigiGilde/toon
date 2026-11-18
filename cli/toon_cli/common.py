"""What every command shares: the version, the two errors and the slug and
site checks."""

from __future__ import annotations

import importlib.metadata
import re

VERSION = importlib.metadata.version("toon")

# Slug for group, site and preview ref: identical to the server contract
# regex, so a ref or site part can never leak into the URL as an extra path
# segment.
SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# The fixed id of a site, as the Deploy tab shows it. Toon takes only the
# lowercase form in an audience, so the CLI lowers what it is given.
SITE_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


class IncompatibleServer(Exception):
    """The server speaks a newer API major than this CLI supports."""


class UsageError(Exception):
    """An error in the call itself: a wrong argument, a missing path, an invalid file type."""


def _valid_slug(value: str, what: str) -> str:
    if not SLUG_RE.fullmatch(value):
        raise UsageError(
            f"{what} must be a valid slug (a-z, 0-9, hyphen), got: {value!r}"
        )
    return value


def _split_site(site: str, label: str = "--site") -> tuple[str, str]:
    parts = site.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise UsageError(
            f"{label} must have the form 'group/site', got: {site!r}"
        )
    return _valid_slug(parts[0], "group"), _valid_slug(parts[1], "site")

"""The path checks every archive entry goes through, and the base path the caller sends."""

from __future__ import annotations

import re
from collections.abc import Sequence

from toon.constants import RESERVED_SEGMENTS
from toon.ingest.unpacker.errors import BundleError, _name_bytes
from toon.ingest.unpacker.limits import Limits

# The longest path an entry may have, in bytes of the name as it lands on disk:
# its UTF-8, where a byte of a tar name that is not UTF-8 counts as the one
# byte it is. PATH_MAX on macOS, where most bundles are made, and a quarter of
# Linux's, which leaves the content volume room for its own prefix. It also
# bounds every path a refusal names.
MAX_PATH_BYTES = 1024
# How much of a path over that limit its refusal names: enough to find it.
PATH_ECHO_CHARS = 100

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


# Metadata the Finder bakes into a zip. Without this the most common route of
# all strands: directory in Finder, right-click, "Compress". The archive then
# holds `mijn-site/` beside `__MACOSX/`, two things in the root, so the
# enclosure is not peeled off and there is no index.html in the root. These
# entries are ignored: they do not count towards the root, they are not
# proposed as an index and they are not published.
_JUNK_DIR = "__MACOSX"
_JUNK_FILES = frozenset({".DS_Store"})
# AppleDouble: the Finder puts the resource fork of `x.html` in `._x.html`.
_JUNK_PREFIX = "._"


def is_archive_junk(segments: Sequence[str]) -> bool:
    """Whether this path is OS metadata that belongs nowhere in the site.

    Only to be called after the structural path check has run: a path pointing
    outside the destination has to be refused, not silently skipped.
    """
    if any(segment == _JUNK_DIR for segment in segments):
        return True
    if not segments:
        return False
    name = segments[-1]
    return name in _JUNK_FILES or name.startswith(_JUNK_PREFIX)


def _check_length(raw_value: str) -> None:
    """Refuses a path over MAX_PATH_BYTES, naming only its start.

    Comes before every other check that names the path, so no refusal
    repeats more than this limit allows."""
    if len(_name_bytes(raw_value)) > MAX_PATH_BYTES:
        raise BundleError(
            "PATH_TOO_LONG",
            params={"max_bytes": MAX_PATH_BYTES, "path": raw_value[:PATH_ECHO_CHARS] + "..."},
        )


def _safe_segments(raw_value: str) -> list[str]:
    """Structural path check: whatever gets through here cannot point outside
    the destination. Applies to every entry, including one that ends up
    outside the chosen root and is therefore not published."""
    _check_length(raw_value)
    if "\x00" in raw_value:
        raise BundleError("NULL_BYTE", params={"path": raw_value})
    path = raw_value.replace("\\", "/")
    if path.startswith("/") or _WINDOWS_DRIVE_RE.match(path):
        raise BundleError("ABSOLUTE_PATH", params={"path": raw_value})

    segments: list[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            raise BundleError("PATH_TRAVERSAL", params={"path": raw_value})
        segments.append(segment)

    if not segments:
        raise BundleError("EMPTY_PATH", params={"path": raw_value})
    return segments


# Files you never want on a website and that come along by themselves with a
# zipped site directory. Refuse, do not skip: whoever sends these along
# packed something other than what they thought, and that is worth saying out
# loud. The CLI already skips `.git` while packing; this is about the browser
# upload, where the archive is already made.
SECRETS_DIR = ".git"
SECRETS_FILE_PREFIX = ".env"


def _secrets_segment(segments: Sequence[str]) -> str | None:
    """The first segment that must not be published, or None.

    No blanket ban on dotfiles: `.well-known/` is precisely a path a site
    ought to be able to serve.
    """
    for segment in segments:
        if segment == SECRETS_DIR:
            return segment
    name = segments[-1] if segments else ""
    # `.env`, `.env.local`, `.env.production`; `.env.example` too, because the
    # price of that false alarm is an error message and the price of the other
    # way round is a leaked token.
    if name == SECRETS_FILE_PREFIX or name.startswith(SECRETS_FILE_PREFIX + "."):
        return name
    return None


def _check_policy(segments: list[str], raw_value: str, limits: Limits) -> None:
    """Limits that are about the published tree, on the path as it sits in the
    archive (so an enclosing directory counts towards the depth)."""
    if len(segments) > limits.max_depth:
        raise BundleError(
            "TOO_DEEP", params={"max_depth": limits.max_depth, "path": raw_value}
        )
    secret = _secrets_segment(segments)
    if secret is not None:
        raise BundleError("SECRET_FILE", params={"path": raw_value, "secret": secret})


def _scannable_segments(raw_value: str) -> list[str] | None:
    """Segments for determining the root, or None as soon as the path is going
    to be refused anyway. Refusing does not happen here: the unpacking does
    that, with the precise reason and on the entry where it goes wrong."""
    try:
        return _safe_segments(raw_value)
    except BundleError:
        return None


def _validate_base_path(base_path: str | None, limits: Limits) -> tuple[str, ...]:
    """The same path validation as for an archive entry; an invalid base path is
    a refusal, never a silent fallback to the archive root."""
    if base_path is None:
        return ()
    try:
        segments = _safe_segments(base_path)
        _check_policy(segments, base_path, limits)
        if segments[0] in RESERVED_SEGMENTS:
            # Not because something reserved would be published (the
            # contents of that directory land in the root), but because a
            # platform name as input ought to be refused fail-closed.
            raise BundleError("RESERVED_SEGMENT.base_path", params={"segment": segments[0]})
    except BundleError as error:
        raise BundleError("BASE_PATH_INVALID", params={"cause": error.message}) from error
    return tuple(segments)

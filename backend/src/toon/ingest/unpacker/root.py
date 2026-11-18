"""The root of the site: the scan over the archive names, and where each entry lands."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from toon.constants import INDEX_FILE, RESERVED_SEGMENTS
from toon.ingest.unpacker.errors import BundleError
from toon.ingest.unpacker.limits import Limits
from toon.ingest.unpacker.paths import _check_policy, _safe_segments, _scannable_segments, is_archive_junk
from toon.messages import Msg

# Upper bound on the proposal in the error response: enough to recognise the
# right directory, short enough to fit in an error message and in constant
# memory.
MAX_INDEX_SUGGESTIONS = 5


def _common(links: Sequence[str], right: Sequence[str]) -> int:
    count = 0
    for one, two in zip(links, right, strict=False):
        if one != two:
            break
        count += 1
    return count


@dataclass(frozen=True)
class _Root:
    """The chosen root of the site, split by origin: what the peeling took away
    and what the caller chose as base path.

    The split exists for the error messages: the user recognises their own
    base path, not the prefix the peeling put in front of it.
    """

    peeled: tuple[str, ...] = ()
    base: tuple[str, ...] = ()

    @property
    def segments(self) -> tuple[str, ...]:
        return self.peeled + self.base


class _Scan:
    """Reads only the names from the archive index, never the contents.

    Delivers three things: the chain of enclosing directories, the depths at
    which the base path occurs in the archive, and (in case the root turns out
    to have no index.html) the shortest index.html paths as a proposal. Memory
    is constant: a single segment list for the chain, at most
    MAX_INDEX_SUGGESTIONS proposals, a single path for the capitalisation
    variant and a set of depths that never grows beyond the deepest path.

    The chain is the longest prefix every entry agrees on, cut short by two
    things: a file at level n stops the peeling at level n-1 (deeper would
    swallow the file itself), and two entries that diverge at level j mean two
    things sit beside each other there. A directory entry above the chain is
    merely an intermediate directory and constrains nothing; `dist/` being in
    the archive does not say that something sits beside `dist/sub/`.
    """

    def __init__(self, base: Sequence[str] = ()) -> None:
        self._chain: list[str] | None = None
        self._bound_at: int | None = None
        self._invalid = False
        self._candidates: list[list[str]] = []
        self._variant: list[str] | None = None
        self._base = list(base)
        self._base_lower = [segment.lower() for segment in base]
        self._base_depths: set[int] = set()
        self._base_variant: list[str] | None = None

    def see(self, raw_path_value: str, is_map: bool) -> None:
        segments = _scannable_segments(raw_path_value)
        if segments is None:
            self._invalid = True
            return
        if is_archive_junk(segments):
            return
        self._note_base(segments)
        if not is_map:
            self._note_index(segments)
            self._bound(len(segments) - 1)
        if self._chain is None:
            self._chain = segments
            return
        common = _common(self._chain, segments)
        if common < min(len(self._chain), len(segments)):
            self._bound(common)
        elif len(segments) > len(self._chain):
            self._chain = segments

    def _bound(self, level: int) -> None:
        self._bound_at = level if self._bound_at is None else min(self._bound_at, level)

    def _note_base(self, segments: list[str]) -> None:
        """Records at which depths the base path occurs in this archive path.

        That way the scan settles which reading of the base path really exists:
        the documented one (relative to the root after the peeling) or a
        shallower one, in which the user typed the peeled prefix along
        themselves or the peeling already took their directory away.
        """
        if not self._base:
            return
        for i in range(len(segments) - len(self._base) + 1):
            part = segments[i : i + len(self._base)]
            if part == self._base:
                self._base_depths.add(i)
            elif self._base_variant is None and [s.lower() for s in part] == self._base_lower:
                self._base_variant = part

    def _note_index(self, segments: list[str]) -> None:
        if segments[-1] == INDEX_FILE:
            self._candidates.append(segments)
            self._candidates.sort(key=lambda path: (len(path), path))
            del self._candidates[MAX_INDEX_SUGGESTIONS:]
        elif segments[-1].lower() == INDEX_FILE and self._variant is None:
            self._variant = segments

    @property
    def root(self) -> list[str]:
        """The chain to peel off. Empty as soon as a path is going to be refused
        anyway: shifting the root on an archive we do not trust makes no
        sense."""
        if self._invalid or self._chain is None:
            return []
        if self._bound_at is None:
            # Only directory entries seen: the whole archive is enclosure.
            return list(self._chain)
        return self._chain[: self._bound_at]

    def pick_root(self) -> _Root:
        """The root of the site: the peeled prefix, and behind it the base path at
        the deepest place where it really occurs in the archive.

        That deepest place is the documented reading as soon as it exists; if
        the base path occurs only shallower, that is the only reading the user
        can have meant. If it occurs nowhere, the documented reading stands and
        the unpacking refuses with BASE_PATH_UNKNOWN."""
        peeled = self.root
        if not self._base:
            return _Root(peeled=tuple(peeled))
        depths = [i for i in self._base_depths if i <= len(peeled)]
        depth = max(depths) if depths else len(peeled)
        return _Root(peeled=tuple(peeled[:depth]), base=tuple(self._base))

    def base_case_variant(self) -> str | None:
        """The path that looks like the base path but is spelled differently, or
        None as soon as the base path itself occurs exactly somewhere."""
        if self._base_depths or self._base_variant is None:
            return None
        return "/".join(self._base_variant)

    def suggestions(self, root: Sequence[str]) -> tuple[str, ...]:
        return tuple("/".join(path[len(root) :]) for path in self._candidates)

    def case_variant(self, root: Sequence[str]) -> str | None:
        if self._variant is None:
            return None
        return "/".join(self._variant[len(root) :])


@dataclass(frozen=True)
class _Placement:
    """Where an archive entry lands in the site.

    `inside` says whether the entry falls under the chosen root (or is that
    directory itself), `path` is the path within the site, or None when there
    is nothing to write.
    """

    inside: bool
    path: str | None


def _reserved_error(segment: str, root: _Root) -> BundleError:
    """`_preview`, `_version` and `_encoded` are platform names and must not sit
    in the root of the site. The message says where that root comes from, so
    the user is not served a prefix they never typed."""
    if root.base:
        where = Msg("where.base_path", {"base": "/".join(root.base)})
    elif root.peeled:
        where = Msg("where.peeled", {"peeled": "/".join(root.peeled)})
    else:
        where = Msg("where.bundle_root")
    return BundleError("RESERVED_SEGMENT", params={"segment": segment, "where": where})


def _place(raw_value: str, root: _Root, limits: Limits) -> _Placement:
    """Determines where an entry is placed and refuses an unsafe path.

    The structural check applies to every entry; the policy limits (reserved
    segment in the root, directory depth) apply only to what gets published,
    so a `base_path` does not trip over a deep node_modules that the user
    deliberately kept out of the publication.
    """
    segments = _safe_segments(raw_value)
    if is_archive_junk(segments):
        return _Placement(inside=False, path=None)
    root_segments = list(root.segments)
    if len(segments) < len(root_segments) or segments[: len(root_segments)] != root_segments:
        return _Placement(inside=False, path=None)
    rest = segments[len(root_segments) :]
    if not rest:
        # The root itself: proves the base path exists, writes nothing.
        return _Placement(inside=True, path=None)
    _check_policy(segments, raw_value, limits)
    if rest[0] in RESERVED_SEGMENTS:
        raise _reserved_error(rest[0], root)
    return _Placement(inside=True, path="/".join(rest))

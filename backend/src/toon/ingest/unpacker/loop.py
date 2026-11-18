"""The bookkeeping over one pass through an archive, and what it establishes."""

from __future__ import annotations

from dataclasses import dataclass

from toon.ingest.unpacker.limits import Limits
from toon.ingest.unpacker.root import _Placement, _Root, _Scan
from toon.ingest.unpacker.writing import _Tree


@dataclass(frozen=True)
class _Outcome:
    """What the unpacking established about the bundle as a whole."""

    count: int
    root_seen: bool
    index_seen: bool
    suggestions: tuple[str, ...] = ()
    case_variant: str | None = None
    # The root does exist, but is a file: only possible with a base path, and
    # the difference between "does not exist" and "is not a directory".
    root_is_file: bool = False
    # What the peeling took away, and the path that looks like the base path
    # but is spelled differently: both only for the error message.
    peeled: str = ""
    base_variant: str | None = None


@dataclass
class _Loop:
    """Bookkeeping over one pass through an archive."""

    tree: _Tree
    root: _Root
    root_seen: bool
    index_seen: bool = False
    root_is_file: bool = False
    suggestions: tuple[str, ...] = ()
    case_variant: str | None = None
    peeled: str = ""
    base_variant: str | None = None

    def see_entry(self, placement: _Placement, is_map: bool) -> None:
        """Tracks whether the root has been seen and counts what lands in the
        site. What falls outside the root does not count towards that; the
        number of entries walked was bounded in the pre-pass already."""
        if not placement.inside:
            return
        self.root_seen = True
        if placement.path is None:
            self.root_is_file = self.root_is_file or not is_map
        if is_map or placement.path is None:
            self.tree.count_entry()

    def outcome(self) -> _Outcome:
        return _Outcome(
            count=self.tree.count,
            root_seen=self.root_seen,
            index_seen=self.index_seen,
            suggestions=self.suggestions,
            case_variant=self.case_variant,
            root_is_file=self.root_is_file,
            peeled=self.peeled,
            base_variant=self.base_variant,
        )


def _start_loop(scan: _Scan, limits: Limits) -> _Loop:
    peeled = scan.root
    root = scan.pick_root()
    suggestions = scan.suggestions(peeled)
    return _Loop(
        tree=_Tree(limits, suggestions),
        root=root,
        # Without a base path the chosen root is by construction the root of
        # the archive; only a supplied base path can turn out to be unfindable.
        root_seen=not root.base,
        # The proposals are relative to the root after the peeling: exactly
        # the form in which they can be sent back as a base path.
        suggestions=suggestions,
        case_variant=scan.case_variant(peeled),
        peeled="/".join(peeled),
        base_variant=scan.base_case_variant(),
    )

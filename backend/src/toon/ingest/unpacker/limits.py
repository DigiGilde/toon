"""The limits on a bundle, and the bound on the archive entries one pass walks."""

from __future__ import annotations

from dataclasses import dataclass

from toon.config import Settings
from toon.ingest.unpacker.errors import BundleError

# Entries outside the chosen root do not end up in the site, but walking past
# them still costs time: every header is read and checked, in both passes of a
# tar. They therefore get their own, wider limit than the site itself: a
# node_modules beside your dist must not hold up a deploy, an archive with
# hundreds of thousands of entries must.
ARCHIVE_ENTRY_FACTOR = 50


@dataclass(frozen=True)
class Limits:
    max_file: int
    max_total: int
    max_files: int
    max_depth: int

    @classmethod
    def from_settings(cls, settings: Settings) -> Limits:
        return cls(
            max_file=settings.ingest_max_file,
            max_total=settings.ingest_max_total,
            max_files=settings.ingest_max_files,
            max_depth=settings.ingest_max_depth,
        )

    @property
    def max_archive_entries(self) -> int:
        """Limit on the number of archive entries walked, published or not.
        Derived from `max_files`, so an instance that allows bigger sites
        allows roomier bundles as a matter of course."""
        return self.max_files * ARCHIVE_ENTRY_FACTOR


class _EntryCounter:
    """Bounds how many entries one pass over the archive touches.

    Separate from the tree, because this also counts what falls outside the
    root and therefore writes nothing; a counter of its own per pass, so the
    pre-pass and the unpacking do not add up against each other.
    """

    def __init__(self, limits: Limits) -> None:
        self._limit = limits.max_archive_entries
        self._entries = 0

    def count(self) -> None:
        self._entries += 1
        if self._entries > self._limit:
            raise BundleError(
                "TOO_MANY_FILES.archive_entries", params={"limit": self._limit}
            )

"""Writing an entry: the bounded copy, and the bookkeeping over the paths written."""

from __future__ import annotations

from typing import IO, Protocol

from toon.ingest.precompress import Writable
from toon.ingest.unpacker.errors import BundleError
from toon.ingest.unpacker.limits import Limits

READ_CHUNK = 64 * 1024


class Destination(Protocol):
    """Where unpacked files go (the store's VersionWriter)."""

    def open_file(self, rel_path: str) -> Writable: ...


class _Tree:
    """Tracks the paths only (no contents): collisions and counts."""

    def __init__(self, limits: Limits, suggestions: tuple[str, ...] = ()) -> None:
        self._limits = limits
        # Only to pass along at the entry limit: whoever zips their whole
        # site directory hits that first, and then the directory holding
        # the index.html is exactly the answer they need.
        self._suggestions = suggestions
        self._files: set[str] = set()
        self._dirs: set[str] = set()
        self._entries = 0
        self.total = 0

    @property
    def count(self) -> int:
        return len(self._files)

    def count_entry(self) -> None:
        """Counts one archive entry, a directory entry included: it writes
        nothing, but both tarfile and our own bookkeeping hold memory per
        entry walked, so counting files alone does not bound that."""
        self._entries += 1
        if self._entries > self._limits.max_files:
            raise BundleError(
                "TOO_MANY_FILES",
                params={"limit": self._limits.max_files},
                index_candidates=self._suggestions,
            )

    def check_upfront(self, path: str, claimed: int) -> None:
        """Every check that can be done before the target file is opened."""
        self.count_entry()
        if path in self._files or path in self._dirs:
            raise BundleError("DUPLICATE_PATH", params={"path": path})
        parts = path.split("/")
        for i in range(1, len(parts)):
            prefix = "/".join(parts[:i])
            if prefix in self._files:
                raise BundleError("DUPLICATE_PATH", params={"path": prefix})
        if claimed > self._limits.max_file:
            raise BundleError(
                "FILE_TOO_LARGE", params={"path": path, "max_file": self._limits.max_file}
            )
        if self.total + claimed > self._limits.max_total:
            raise BundleError(
                "TOTAL_TOO_LARGE", params={"max_total": self._limits.max_total}
            )

    def register(self, path: str, size: int) -> None:
        parts = path.split("/")
        for i in range(1, len(parts)):
            self._dirs.add("/".join(parts[:i]))
        self._files.add(path)
        self.total += size


def _copy_bounded(source: IO[bytes], target: Writable, path: str, tree: _Tree, limits: Limits) -> int:
    """Copies in chunks and counts the real unpacked bytes against the limits."""
    read_bytes_count = 0
    while True:
        part_item = source.read(READ_CHUNK)
        if not part_item:
            return read_bytes_count
        read_bytes_count += len(part_item)
        if read_bytes_count > limits.max_file:
            raise BundleError(
                "FILE_TOO_LARGE", params={"path": path, "max_file": limits.max_file}
            )
        if tree.total + read_bytes_count > limits.max_total:
            raise BundleError("TOTAL_TOO_LARGE", params={"max_total": limits.max_total})
        target.write(part_item)


def _write_entry(
    source: IO[bytes], path: str, claimed: int, tree: _Tree, destination: Destination, limits: Limits
) -> None:
    tree.check_upfront(path, claimed)
    with destination.open_file(path) as target:
        size = _copy_bounded(source, target, path, tree, limits)
    tree.register(path, size)

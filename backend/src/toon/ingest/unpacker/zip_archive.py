"""Unpacking a zip, with its central directory bounded before zipfile reads it."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path
from typing import IO

from toon.constants import INDEX_FILE
from toon.ingest.unpacker.errors import BundleError, _unreadable
from toon.ingest.unpacker.limits import Limits, _EntryCounter
from toon.ingest.unpacker.loop import _Outcome, _start_loop
from toon.ingest.unpacker.root import _place, _Scan
from toon.ingest.unpacker.writing import Destination, _write_entry

# The tail of a zip: the end-of-central-directory record, behind it a comment
# of at most 65535 bytes, and in a zip64 the locator that points at the record
# holding the real totals.
_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD_SIZE = 22
_MAX_ZIP_COMMENT = 65535
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP64_LOCATOR_SIZE = 20
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP64_EOCD_SIZE = 56
# One record in the central directory: the fixed fields, with the name, the
# extra field and the comment behind them.
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_CENTRAL_RECORD_SIZE = 46


def _eocd_start(tail: bytes, tail_offset: int, size: int) -> int | None:
    """Where the end-of-central-directory record begins in `tail`, or None.

    Scanned from the back, because a comment of up to 65535 bytes may follow
    the record and those bytes can hold the signature themselves. A candidate
    counts only when its comment length reaches exactly the end of the file.
    """
    end = len(tail)
    while True:
        start = tail.rfind(_EOCD_SIGNATURE, 0, end)
        if start < 0:
            return None
        if len(tail) - start >= _EOCD_SIZE:
            comment = int.from_bytes(tail[start + 20 : start + 22], "little")
            if tail_offset + start + _EOCD_SIZE + comment == size:
                return start
        end = start + len(_EOCD_SIGNATURE) - 1


def _zip64_directory_size(handle: IO[bytes], tail: bytes, start: int, size: int) -> int | None:
    """The size of the central directory out of the zip64 record, which the
    classic record refers to as soon as a total does not fit in its own
    fields. None as soon as that trail does not hold up."""
    locator = start - _ZIP64_LOCATOR_SIZE
    if locator < 0 or tail[locator : locator + 4] != _ZIP64_LOCATOR_SIGNATURE:
        return None
    offset = int.from_bytes(tail[locator + 8 : locator + 16], "little")
    if offset + _ZIP64_EOCD_SIZE > size:
        return None
    handle.seek(offset)
    record = handle.read(_ZIP64_EOCD_SIZE)
    if record[:4] != _ZIP64_EOCD_SIGNATURE:
        return None
    return int.from_bytes(record[40:48], "little")


def _count_central_records(handle: IO[bytes], start: int, limit: int) -> int:
    """The number of records in the central directory, counted to at most
    `limit` + 1: enough to decide, cheap for an archive that claims millions.

    Counting stops at the first record that does not parse. zipfile stumbles
    over that same record, so it never builds more objects than were counted
    here; the claimed number of entries is deliberately not trusted, because
    zipfile reads to the end of the directory and not to that number.
    """
    handle.seek(start)
    count = 0
    while count <= limit:
        record = handle.read(_CENTRAL_RECORD_SIZE)
        if len(record) < _CENTRAL_RECORD_SIZE or record[:4] != _CENTRAL_SIGNATURE:
            return count
        # The name, the extra field and the comment sit behind the record.
        behind = sum(int.from_bytes(record[at : at + 2], "little") for at in (28, 30, 32))
        handle.seek(behind, 1)
        count += 1
    return count


def _zip_entry_bound(handle: IO[bytes], limit: int) -> int | None:
    """How many entries the zip holds, counted to at most `limit` + 1, or None
    when the tail does not parse (zipfile then refuses the archive).

    zipfile parses the whole central directory into memory the moment the
    archive opens, hundreds of bytes per entry, so an entry counter over
    `infolist()` comes too late: an archive that ships millions of entries has
    then already cost gigabytes. Walking the directory itself costs nothing
    beyond the records walked, and stops at the limit.
    """
    handle.seek(0, 2)
    size = handle.tell()
    tail_size = min(size, _EOCD_SIZE + _MAX_ZIP_COMMENT)
    handle.seek(size - tail_size)
    tail = handle.read(tail_size)
    start = _eocd_start(tail, size - tail_size, size)
    if start is None:
        return None
    directory_size = int.from_bytes(tail[start + 12 : start + 16], "little")
    total = int.from_bytes(tail[start + 10 : start + 12], "little")
    zip64_size = 0
    if total == 0xFFFF or directory_size == 0xFFFFFFFF:
        found = _zip64_directory_size(handle, tail, start, size)
        if found is None:
            return None
        directory_size = found
        zip64_size = _ZIP64_EOCD_SIZE + _ZIP64_LOCATOR_SIZE
    # The directory ends where its own records end: right in front of the
    # zip64 records, if any, and the classic record. Reckoning back from there
    # rather than from the claimed offset keeps a zip with something in front
    # of it (a self-extracting header) working, exactly as zipfile does.
    directory_start = size - tail_size + start - zip64_size - directory_size
    if directory_start < 0:
        return None
    return _count_central_records(handle, directory_start, limit)


def _check_zip_entry_bound(source: Path, limits: Limits) -> None:
    try:
        with source.open("rb") as handle:
            found = _zip_entry_bound(handle, limits.max_archive_entries)
    except OSError as error:
        raise BundleError("INVALID_ARCHIVE.zip", params={"error": _unreadable(error)}) from error
    if found is not None and found > limits.max_archive_entries:
        raise BundleError(
            "TOO_MANY_FILES.archive_entries", params={"limit": limits.max_archive_entries}
        )


def _unpack_zip(source: Path, destination: Destination, limits: Limits, base: tuple[str, ...]) -> _Outcome:
    # zipfile reads the whole central directory into memory as soon as the
    # archive opens, so the entry counter below does not bound that first
    # peak: the tail of the archive says beforehand how many entries that
    # directory holds at most.
    _check_zip_entry_bound(source, limits)
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as error:
        raise BundleError("INVALID_ARCHIVE.zip", params={"error": _unreadable(error)}) from error

    with archive:
        # The central directory is in memory already; this extra pass over
        # the names costs no archive access. The counter cuts it off just as
        # hard as the unpacking itself, so an archive with a million entries
        # is not scanned in full first.
        scan = _Scan(base)
        counter = _EntryCounter(limits)
        for info in archive.infolist():
            counter.count()
            # zipfile truncates a name at the first NUL byte, so an entry
            # declared `index.html\0.bak` arrives as `index.html` and the
            # archive listing disagrees with what lands on disk.
            # `orig_filename` is the only place where that byte is still
            # visible.
            if "\x00" in info.orig_filename:
                raise BundleError("NULL_BYTE", params={"path": info.orig_filename})
            scan.see(info.filename, info.is_dir())
        loop = _start_loop(scan, limits)

        for info in archive.infolist():
            mode_ = info.external_attr >> 16
            if stat.S_ISLNK(mode_):
                raise BundleError("SYMLINK_REFUSED", params={"name": info.filename})
            is_map = info.is_dir()
            # Only the file type bits count: many zippers set permission
            # bits only (type 0), which is a regular file.
            if not is_map and stat.S_IFMT(mode_) not in (0, stat.S_IFREG):
                raise BundleError("SPECIAL_FILE", params={"name": info.filename})
            placement = _place(info.filename, loop.root, limits)
            loop.see_entry(placement, is_map)
            if is_map or placement.path is None:
                continue
            try:
                with archive.open(info) as stream:
                    _write_entry(stream, placement.path, info.file_size, loop.tree, destination, limits)
            except BundleError:
                raise
            except (zipfile.BadZipFile, OSError, RuntimeError) as error:
                raise BundleError(
                    "INVALID_ARCHIVE.zip_entry", params={"error": _unreadable(error)}
                ) from error
            loop.index_seen = loop.index_seen or placement.path == INDEX_FILE
    return loop.outcome()

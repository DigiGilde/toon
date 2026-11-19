"""Unpacking a .tar.gz: a pre-pass over the headers, then the members in order."""

from __future__ import annotations

import gzip
import re
import tarfile
import zlib
from collections.abc import Iterator
from pathlib import Path

from toon.constants import INDEX_FILE
from toon.ingest.unpacker.errors import BundleError, _unreadable
from toon.ingest.unpacker.limits import Limits, _EntryCounter
from toon.ingest.unpacker.loop import _Outcome, _start_loop
from toon.ingest.unpacker.paths import MAX_PATH_BYTES, _check_length
from toon.ingest.unpacker.root import _place, _Scan
from toon.ingest.unpacker.writing import READ_CHUNK, Destination, _write_entry

# What tarfile is handed for one member before the member reaches a check
# here: its header blocks, a GNU long name or long link, its pax records and a
# sparse map. Members are let go of one by one, so this bounds what is held at
# a time, not a sum over the archive. A path at MAX_PATH_BYTES with the pax
# records tar tools write takes a few KiB; the rest is room for a record such
# as a comment.
MAX_TAR_HEADER_BYTES = 64 * 1024
# How many pax records the headers of one member may hold, the ones passed over
# included: each costs a parse of its own, however short it is. bsdtar writes
# a few, plus two for every extended attribute.
MAX_TAR_HEADER_RECORDS = 64
# How many extension headers (pax, pax global, GNU long name or long link)
# may stand in front of one member. Each costs a parse and an application of
# the global records, even an empty one; tar tools write at most three.
MAX_TAR_EXTENSION_HEADERS = 8
# What the pax global records may hold together, in characters, and at most
# MAX_TAR_HEADER_RECORDS of them: tarfile applies them to every member after
# them. `git archive` writes one, holding the commit.
MAX_TAR_GLOBAL_BYTES = 8 * 1024
# What the archive as a whole may hand tarfile to parse, for every entry
# `max_archive_entries` allows: a header block, a pax header and its records.
# With the default limits that is 50,000 times 2 KiB, so the time spent parsing
# headers is bound by the entry limit, not by the size limit. What is passed
# over unread (extended attributes, padding) may come to `max_total` on top:
# it only has to be decompressed.
TAR_HEADER_ROOM_PER_ENTRY = 2 * 1024

# Pax records holding an extended attribute. bsdtar on macOS writes each one
# twice (base64 and raw), and a download URL or the resource fork of a custom
# icon easily takes more than MAX_TAR_HEADER_BYTES. Nothing here sets them on
# the files it writes, so they are passed over unread: framed by their length
# alone, they count towards the header data of the archive, not towards what
# is held for one member.
_PASSED_OVER = (b"LIBARCHIVE.xattr.", b"SCHILY.xattr.")
_RECORD_LENGTH = re.compile(rb"([0-9]{1,20}) ")
# Enough of a record to tell whether it is passed over.
_RECORD_HEAD = 21 + max(len(prefix) for prefix in _PASSED_OVER)
_PAX_TYPES = (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE)
_EXTENSION_TYPES = (*_PAX_TYPES, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)
# The bytes a long name or long link is read for: one past the path limit, so
# a longer one still reaches `_check_length` as too long.
_LONG_NAME_KEPT = MAX_PATH_BYTES + 1
# Header types for which tarfile does not read the ustar prefix field.
_GNU_TYPES = (tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK, tarfile.GNUTYPE_SPARSE)


def _in_blocks(size: int) -> int:
    return size + -size % tarfile.BLOCKSIZE


def _size_of(header: bytes) -> int:
    """The size field of a header tarfile has parsed already: octal, or
    base-256 behind a 0x80 byte. A negative one never gets here, because
    tarfile refuses it before reading the payload."""
    field = header[124:136]
    if field[0] & 0x80:
        return int.from_bytes(field[1:], "big")
    # Decoded and stripped as text, as tarfile does: str.strip() takes more
    # than bytes.strip() would.
    return int(field.split(b"\0", 1)[0].decode("ascii").strip() or "0", 8)


class _TarFile(tarfile.TarFile):
    # Missing from the type stubs: the list tarfile appends every member it
    # reads to.
    members: list[tarfile.TarInfo]


def _source(path: Path, limits: Limits) -> _TarSource:
    return _TarSource(path, limits.max_archive_entries * TAR_HEADER_ROOM_PER_ENTRY, limits.max_total)


def _header_error(limit: int = MAX_TAR_HEADER_BYTES) -> BundleError:
    return BundleError("INVALID_ARCHIVE.tar_header", params={"limit": limit})


def _tar_scan(source: Path, limits: Limits, base: tuple[str, ...]) -> _Scan:
    """Pre-pass over the tar headers only, to determine the root.

    A tar has no central directory the way a zip does, so the names come from
    a pass of our own over the spool file. That pass holds nothing more than
    the chain, at most five proposals and the member at hand, and the entry
    counter bounds the number of headers walked here just as hard as during
    the unpacking.

    Reaching the next header is not a free jump: in a gzip stream everything
    in between has to be decompressed. The claimed sizes from the headers are
    therefore held against the limits here already, so this pass never
    decompresses more than a bundle is allowed to be under those same limits.
    Otherwise an archive that ought to be refused on its first header would
    have to be read through in full first. What tarfile skips for a member
    that is not a file counts along, and the header data has a bound of its
    own (`_TarSource`).
    """
    scan = _Scan(base)
    counter = _EntryCounter(limits)
    claimed_total = 0
    with _source(source, limits) as stream_source, _open(stream_source) as archive:
        for member in _members(archive, stream_source):
            counter.count()
            _admit(archive, member, stream_source)
            if member.isfile():
                if member.size > limits.max_file:
                    raise BundleError(
                        "FILE_TOO_LARGE",
                        params={"path": member.name, "max_file": limits.max_file},
                    )
                claimed_total += member.size
            else:
                claimed_total += archive.offset - member.offset_data
            if claimed_total > limits.max_total:
                raise BundleError("TOTAL_TOO_LARGE.archive", params={"max_total": limits.max_total})
            scan.see(member.name, member.isdir())
    return scan


class _TarSource(gzip.GzipFile):
    """The gzip stream under the tar, with the raw header within reach and
    what tarfile reads for a header bounded.

    This leans on how CPython's tarfile reads a header, pinned by
    `TestTarfileReadPattern`: one read of BLOCKSIZE per header block, the
    payload of an extension header (a long name, a long link, pax records)
    read right after its header, and a payload over 1 MiB read in pieces of
    1 MiB until one comes back short or empty.

    tarfile decodes a member name only up to the first NUL byte, so an entry
    declared `index.html\\0.bak` arrives as `index.html` and the archive
    listing disagrees with what lands on disk. The raw header, and the raw
    long name in front of it, are the only places where that byte is still
    visible (`declared_names`). Recording is off while a member's content is
    being read.

    tarfile reads the payload of an extension header in full, in the size its
    header claims, before any check here sees the member. So that read is
    answered here: a long name with its first `_LONG_NAME_KEPT` bytes, pax
    records without the extended attributes, the rest of the payload passed
    over unread. What is kept for one member is bounded by
    MAX_TAR_HEADER_BYTES, MAX_TAR_HEADER_RECORDS and
    MAX_TAR_EXTENSION_HEADERS: pax records over it are cut off and the member
    is refused by name once handed over (`oversized`); any other read over it
    is refused before it is issued. What is handed to tarfile counts towards
    `max_kept` for the archive as a whole, what is passed over towards
    `max_passed_over`.
    """

    def __init__(self, path: Path, max_kept: int, max_passed_over: int) -> None:
        super().__init__(filename=str(path), mode="rb")
        self._max_kept = max_kept
        self._max_passed_over = max_passed_over
        self._kept_total = 0
        self._passed_over_total = 0
        self._kept = 0
        self._records = 0
        self._extensions = 0
        self._at_header = True
        self._extension: bytes | None = None
        self._swallow = False
        self._header = b""
        self._long_name: bytes | None = None
        self.oversized = False
        self.globals_read = False
        self.recording = True

    def read(self, size: int | None = -1) -> bytes:
        if not self.recording:
            return super().read(size)
        if size is None or size < 0:
            raise _header_error()
        if self._swallow:
            # tarfile reads a payload over 1 MiB in pieces until a piece
            # comes back empty; this one has been answered in full already.
            self._swallow = False
            return b""
        extension, self._extension = self._extension, None
        if extension is None:
            return self._block_read(size)
        if self.oversized:
            raise _header_error()
        self._at_header = True
        return self._payload(extension, size)

    def _block_read(self, size: int) -> bytes:
        """A header block, or a block of a sparse map."""
        at_header, self._at_header = self._at_header, False
        if self.oversized:
            # Only the header of the member that was cut off may follow, so
            # the refusal can name it.
            if not at_header:
                raise _header_error()
        elif self._kept + size > MAX_TAR_HEADER_BYTES:
            raise _header_error()
        self._keep(size)
        data = super().read(size)
        if not at_header:
            return data
        kind = data[156:157]
        if kind not in _EXTENSION_TYPES:
            self._header = data
            return data
        self._extensions += 1
        if self._extensions > MAX_TAR_EXTENSION_HEADERS:
            raise BundleError("INVALID_ARCHIVE.tar_extensions", params={"limit": MAX_TAR_EXTENSION_HEADERS})
        self._extension = data
        self.globals_read = self.globals_read or kind == tarfile.XGLTYPE
        return data

    def _payload(self, header: bytes, size: int) -> bytes:
        """The payload of an extension header, as tarfile gets to see it."""
        length = _in_blocks(_size_of(header))
        kind = header[156:157]
        if kind in _PAX_TYPES:
            data = self._pax_records(length)
        else:
            if self._kept + _LONG_NAME_KEPT > MAX_TAR_HEADER_BYTES:
                raise _header_error()
            kept = min(length, _LONG_NAME_KEPT)
            self._keep(kept)
            data = super().read(kept)
            self._skip(length - kept)
            if kind == tarfile.GNUTYPE_LONGNAME:
                self._long_name = data
        self._swallow = length > size and bool(data)
        return data

    def _pax_records(self, length: int) -> bytes:
        """Reads `length` bytes of pax records and keeps those tarfile needs.

        A record that does not frame (a length that is not a number, below
        the five bytes of the shortest record, or running past the payload) is
        refused, and so is a passed-over one without an `=` or a closing
        newline: tarfile would end the archive on it. A kept record is checked
        further by tarfile, as before. Records end at a NUL byte, as tarfile
        reads them. What is read here counts as passed over, and a record
        kept counts as kept as well.
        """
        kept = bytearray()
        left = length
        buffer = b""
        at = 0
        while True:
            if len(buffer) - at < _RECORD_HEAD and left:
                more = self._exact(min(left, READ_CHUNK))
                left -= len(more)
                buffer, at = buffer[at:] + more, 0
            if at == len(buffer) or buffer[at] == 0:
                break
            match = _RECORD_LENGTH.match(buffer, at)
            size = int(match[1]) if match else 0
            if match is None or size < 5 or at + size > len(buffer) + left:
                raise tarfile.ReadError("invalid pax record")
            passed_over = buffer.startswith(_PASSED_OVER, match.end())
            self._records += 1
            if self._records > MAX_TAR_HEADER_RECORDS or (
                not passed_over and self._kept + len(kept) + size > MAX_TAR_HEADER_BYTES
            ):
                self.oversized = True
                break
            end = at + size
            if passed_over:
                body = buffer[match.end() : end]
                if end <= len(buffer):
                    equals, last = b"=" in body, body[-1] if body else -1
                    at = end
                else:
                    missing = end - len(buffer)
                    left -= missing
                    equals, last = self._skip(missing)
                    equals = equals or b"=" in body
                    buffer, at = b"", 0
                if not equals or last != 0x0A:
                    raise tarfile.ReadError("invalid pax record")
                continue
            if end > len(buffer):
                missing = end - len(buffer)
                left -= missing
                buffer += self._exact(missing)
            kept += buffer[at:end]
            at = end
        self._skip(left)
        self._keep(len(kept))
        return bytes(kept)

    def _keep(self, size: int) -> None:
        """Counts bytes handed to tarfile, for this member and the archive."""
        self._kept += size
        self._kept_total += size
        if self._kept_total > self._max_kept:
            raise BundleError("TOTAL_TOO_LARGE.archive_headers", params={"limit": self._max_kept})

    def _pass_over(self, size: int) -> None:
        self._passed_over_total += size
        if self._passed_over_total > self._max_passed_over:
            raise BundleError("TOTAL_TOO_LARGE.archive_headers", params={"limit": self._max_passed_over})

    def _exact(self, size: int) -> bytes:
        self._pass_over(size)
        data = super().read(size)
        if len(data) < size:
            raise tarfile.ReadError("unexpected end of data")
        return data

    def _skip(self, size: int) -> tuple[bool, int]:
        """Reads `size` bytes and lets them go. Says whether an `=` went by,
        and what the last byte was (-1 for none)."""
        self._pass_over(size)
        equals = False
        last = -1
        while size:
            data = super().read(min(size, READ_CHUNK))
            if not data:
                raise tarfile.ReadError("unexpected end of data")
            equals = equals or b"=" in data
            last = data[-1]
            size -= len(data)
        return equals, last

    def next_member(self, offset: int) -> None:
        """Lets go of the member just handled and moves to the next header.

        The move is what tarfile would make itself, including a read of the
        byte in front of the header to tell a cut-off archive; made here, that
        byte does not count as header data of the next member.
        """
        self._kept = 0
        self._records = 0
        self._extensions = 0
        self._at_header = True
        self._extension = None
        self._header = b""
        self._long_name = None
        self.oversized = False
        self.globals_read = False
        if offset != self.tell():
            self.seek(offset - 1)
            if not super().read(1):
                raise tarfile.ReadError("unexpected end of data")

    def declared_names(self) -> list[bytes]:
        """The names the member just handed over was declared under, NUL
        padding removed: its header's name field, behind the ustar prefix
        field where tarfile reads that, and the GNU long name in front of it
        (at most `_LONG_NAME_KEPT` bytes of it). A pax `path` record is
        handed over NUL and all, so `_safe_segments` refuses that one on the
        name itself.
        """
        name = self._header[0:100].rstrip(b"\0")
        prefix = self._header[345:500]
        if prefix.split(b"\0", 1)[0] and self._header[156:157] not in _GNU_TYPES:
            name = prefix.rstrip(b"\0") + b"/" + name
        names = [name]
        if self._long_name is not None:
            names.append(self._long_name.rstrip(b"\0"))
        return names


# What tarfile lets out of a malformed sparse map or sparse size, or of an
# old GNU sparse header cut off in its extension blocks.
_TARFILE_ERRORS = (ValueError, IndexError)


def _unreadable_tar(error: Exception) -> BundleError:
    return BundleError("INVALID_ARCHIVE.tar", params={"error": _unreadable(error)})


def _open(source: _TarSource) -> _TarFile:
    try:
        return _TarFile.open(fileobj=source, mode="r:")
    except _TARFILE_ERRORS as error:
        raise _unreadable_tar(error) from error


def _members(archive: _TarFile, source: _TarSource) -> Iterator[tarfile.TarInfo]:
    """The members in order, each let go of before the next is read.

    Iterating a TarFile keeps every member it walks in `archive.members`,
    each with its decoded names, pax records and sparse map: memory that
    grows with the number of members, whatever the bound on one.
    """
    while True:
        try:
            member = archive.next()
        except _TARFILE_ERRORS as error:
            raise _unreadable_tar(error) from error
        archive.members.clear()
        if member is None:
            return
        yield member
        source.next_member(archive.offset)


def _admit(archive: tarfile.TarFile, member: tarfile.TarInfo, source: _TarSource) -> None:
    """The checks on a member's header, the same in both passes.

    The lengths come first, so no refusal of this member names more than
    MAX_PATH_BYTES. A sparse file is refused outright: a website has no use
    for one, and tarfile holds its map as a list. The size has to match the
    data tarfile skips for the member: a negative one, or a pax global
    record that overrides it, would otherwise let the claimed total go down
    while the data still has to be decompressed. Nor may the next header lie
    behind this one's data, as it does when a pax `size` record is applied
    to an offset tarfile took from a header further up the chain: the next
    read would then seek back, and in a gzip stream that means decompressing
    from the start again.
    """
    _check_length(member.name)
    _check_length(member.linkname)
    if source.oversized:
        raise BundleError(
            "INVALID_ARCHIVE.tar_entry_header",
            params={"name": member.name, "limit": MAX_TAR_HEADER_BYTES, "records": MAX_TAR_HEADER_RECORDS},
        )
    if member.issparse() or any(key.startswith("GNU.sparse.") for key in member.pax_headers):
        raise BundleError("SPECIAL_FILE.sparse", params={"name": member.name})
    span = archive.offset - member.offset_data
    if member.size < 0 or span < 0 or (member.isfile() and span != _in_blocks(member.size)):
        raise BundleError("INVALID_ARCHIVE.tar_size", params={"name": member.name})
    if source.globals_read:
        records = archive.pax_headers
        if (
            len(records) > MAX_TAR_HEADER_RECORDS
            or sum(len(key) + len(value) for key, value in records.items()) > MAX_TAR_GLOBAL_BYTES
        ):
            raise _header_error(MAX_TAR_GLOBAL_BYTES)


def _unpack_tar(source: Path, destination: Destination, limits: Limits, base: tuple[str, ...]) -> _Outcome:
    try:
        loop = _start_loop(_tar_scan(source, limits, base), limits)
        # Deliberately a GzipFile and not the stream mode r|gz: GzipFile
        # decompresses per read with max_length (READ_CHUNK), the stream mode per
        # whole gzip block, which for highly compressible data can be tens of
        # MB per block. Members are read in order, so GzipFile never has to
        # seek back.
        with _source(source, limits) as stream_source, _open(stream_source) as archive:
            for member in _members(archive, stream_source):
                _admit(archive, member, stream_source)
                for declared in stream_source.declared_names():
                    raw_name = declared.decode(archive.encoding, "surrogateescape")
                    _check_length(raw_name)
                    if "\0" in raw_name:
                        raise BundleError("NULL_BYTE", params={"path": raw_name})
                if member.issym():
                    raise BundleError("SYMLINK_REFUSED", params={"name": member.name})
                if member.islnk():
                    raise BundleError("HARDLINK_REFUSED", params={"name": member.name})
                if not member.isdir() and not member.isfile():
                    raise BundleError("SPECIAL_FILE", params={"name": member.name})
                placement = _place(member.name, loop.root, limits)
                loop.see_entry(placement, member.isdir())
                if member.isdir() or placement.path is None:
                    continue
                stream = archive.extractfile(member)
                if stream is None:  # pragma: no cover - extractfile returns None
                    # only for a directory, a special file or a link, and those
                    # three are caught or skipped above already. Kept because
                    # tarfile gives no guarantee and None would turn into an
                    # AttributeError here.
                    raise BundleError("INVALID_ARCHIVE.tar_entry", params={"name": member.name})
                stream_source.recording = False
                try:
                    with stream:
                        _write_entry(stream, placement.path, member.size, loop.tree, destination, limits)
                finally:
                    stream_source.recording = True
                loop.index_seen = loop.index_seen or placement.path == INDEX_FILE
    except BundleError:
        raise
    except (tarfile.TarError, OSError, EOFError, zlib.error) as error:
        # zlib.error: a deflate stream that breaks off in data skipped
        # outside tarfile's own reads.
        raise BundleError("INVALID_ARCHIVE.tar", params={"error": _unreadable(error)}) from error
    return loop.outcome()

"""Unpacking a .tar.gz: a pre-pass over the headers, then the members in order."""

from __future__ import annotations

import gzip
import tarfile
from pathlib import Path

from toon.constants import INDEX_FILE
from toon.ingest.unpacker.errors import BundleError, _unreadable
from toon.ingest.unpacker.limits import Limits, _EntryCounter
from toon.ingest.unpacker.loop import _Outcome, _start_loop
from toon.ingest.unpacker.root import _place, _Scan
from toon.ingest.unpacker.writing import Destination, _write_entry


def _tar_scan(source: Path, limits: Limits, base: tuple[str, ...]) -> _Scan:
    """Pre-pass over the tar headers only, to determine the root.

    A tar has no central directory the way a zip does, so the names come from
    a pass of our own over the spool file. That pass holds nothing more than
    the chain and at most five proposals, and the entry counter bounds the
    number of headers walked here just as hard as during the unpacking.

    Reaching the next header is not a free jump: in a gzip stream everything
    in between has to be decompressed. The claimed sizes from the headers are
    therefore held against the limits here already, so this pass never
    decompresses more than a bundle is allowed to be under those same limits.
    Otherwise an archive that ought to be refused on its first header would
    have to be read through in full first.
    """
    scan = _Scan(base)
    counter = _EntryCounter(limits)
    claimed_total = 0
    with tarfile.open(source, mode="r:gz") as archive:
        for member in archive:
            counter.count()
            if member.isfile():
                if member.size > limits.max_file:
                    raise BundleError(
                        "FILE_TOO_LARGE",
                        params={"path": member.name, "max_file": limits.max_file},
                    )
                claimed_total += member.size
                if claimed_total > limits.max_total:
                    raise BundleError(
                        "TOTAL_TOO_LARGE.archive", params={"max_total": limits.max_total}
                    )
            scan.see(member.name, member.isdir())
    return scan


class _TarSource(gzip.GzipFile):
    """The gzip stream under the tar, with the raw header blocks within reach.

    tarfile decodes a member name only up to the first NUL byte, so an entry
    declared `index.html\\0.bak` arrives as `index.html` and the archive
    listing disagrees with what lands on disk. The raw header is the only
    place where that byte is still visible.

    Blocks read since the previous member was handed over are kept: that is
    the header of the member being handed over now, and in front of it the
    long-name blocks that belong to it. Recording is off while a member's
    content is being read: a GNU long-name payload past 512 bytes comes back
    from one `read()` call sized to the name (not to BLOCKSIZE), so blocks
    cannot be told apart from content reads by their size; only by when they
    happen, before `raw_name_of` for the next member, not during it.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(filename=str(path), mode="rb")
        self._blocks: dict[int, bytes] = {}
        self.recording = True

    def read(self, size: int | None = -1) -> bytes:
        start = self.tell()
        data = super().read(size)
        if self.recording and data:
            self._blocks[start] = data
        return data

    def raw_name_of(self, member: tarfile.TarInfo) -> bytes:
        """The name bytes this member was declared under, NUL padding removed.

        A name too long for the header sits in a block of its own: the GNU
        variant puts it behind its own header, the pax variant in a record
        tarfile reads NUL and all, so `_safe_segments` refuses that one on the
        name itself. The bookkeeping is dropped along the way: a member is
        handed over once.
        """
        header = self._blocks.get(member.offset, b"")
        if header[156:157] == tarfile.GNUTYPE_LONGNAME:
            raw_name = self._blocks.get(member.offset + tarfile.BLOCKSIZE, b"")
        else:
            raw_name = header[0:100]
        self._blocks.clear()
        return raw_name.rstrip(b"\0")


def _unpack_tar(source: Path, destination: Destination, limits: Limits, base: tuple[str, ...]) -> _Outcome:
    try:
        loop = _start_loop(_tar_scan(source, limits, base), limits)
        # Deliberately a GzipFile and not the stream mode r|gz: GzipFile
        # decompresses per read with max_length (READ_CHUNK), the stream mode per
        # whole gzip block, which for highly compressible data can be tens of
        # MB per block. Members are read in order, so GzipFile never has to
        # seek back.
        with _TarSource(source) as stream_source, tarfile.open(fileobj=stream_source, mode="r:") as archive:
            for member in archive:
                raw_name = stream_source.raw_name_of(member)
                if b"\0" in raw_name:
                    raise BundleError(
                        "NULL_BYTE",
                        params={"path": raw_name.decode(archive.encoding, "surrogateescape")},
                    )
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
    except (tarfile.TarError, OSError, EOFError) as error:
        raise BundleError("INVALID_ARCHIVE.tar", params={"error": _unreadable(error)}) from error
    return loop.outcome()

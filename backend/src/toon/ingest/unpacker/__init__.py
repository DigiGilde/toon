"""Unpacker: validates and unpacks an uploaded bundle.

Fail-closed: anything that is not explicitly a regular file with a safe
relative path is refused. The source is a file on disk (the spooled upload)
and every entry streams in 64 KiB chunks straight to the destination; nothing is
held in memory as a whole.

The root of the site is settled in three steps:

1. A chain of enclosing directories is peeled off: as long as the root holds
   exactly one entry and that entry is a directory, that directory becomes the
   new root. That never loses a file, because by definition nothing sits
   beside it, and it makes an archive holding only
   `mijnsite/dist/index.html` work too.
2. A `base_path` from the caller then wins: that directory becomes the root;
   whatever sits beside it is not published. That is the user confirming the
   proposal from step 3. The path is relative to the root after the peeling,
   but the spelling including the peeled prefix counts just as well, and so
   does a base path that the peeling already took away: a fixed
   `base-path: dist` in a workflow must not depend on whether a LEESMIJ.md
   happened to sit beside `dist/` that time.
3. If that does not yield an `index.html` in the root, the bundle is refused,
   with the index.html paths found as a proposal.

Deliberately not: automatically picking the shortest or deepest index.html as
the root when something sits beside that directory in the root. That would
silently throw away files the user thought they were publishing (think of a
zipped site directory holding both `dist/` and `docs/`). The machine
proposes (step 3), the human confirms (step 2).

Capitalisation counts: only `index.html` is an index. `Index.html` does work
on the builder's case-insensitive Mac and not on the server, because both the
serving layer and the filesystem there look for exactly `index.html`. Such a
bundle is therefore refused, with a message that names the capitals instead
of a site that only works for the builder.

Reserved segments (`_preview`, `_version`) are about the root of the published
tree, not about the path as it sits in the archive: an enclosing directory by
that name is simply peeled off, and whatever ends up in the root after the
peeling and the base path is refused.

Decompression is checked incrementally against the limits, so a zip/tar bomb
never gets past them: the sizes from the archive headers are deliberately not
trusted (a header that already claims more than the limit is an early
refusal, though). That also holds for the pre-pass a tar needs to determine
its root: reaching the next header means decompressing everything in between
in a gzip stream, so that pass refuses on those same claimed sizes and adds
them up against `max_total`.

Entries that are walked cost memory even when they write nothing, and are
therefore bounded separately: `max_files` applies to what lands in the
site (directory entries included), `max_archive_entries` to everything the
archive contains, so also to what falls outside the chosen root.
"""

from toon.ingest.unpacker.bundle import unpack
from toon.ingest.unpacker.errors import BundleError
from toon.ingest.unpacker.limits import ARCHIVE_ENTRY_FACTOR, Limits
from toon.ingest.unpacker.paths import (
    MAX_PATH_BYTES,
    PATH_ECHO_CHARS,
    SECRETS_DIR,
    SECRETS_FILE_PREFIX,
    is_archive_junk,
)
from toon.ingest.unpacker.root import MAX_INDEX_SUGGESTIONS
from toon.ingest.unpacker.tar_archive import MAX_TAR_HEADER_BYTES
from toon.ingest.unpacker.writing import READ_CHUNK, Destination

__all__ = [
    "ARCHIVE_ENTRY_FACTOR",
    "MAX_INDEX_SUGGESTIONS",
    "MAX_PATH_BYTES",
    "MAX_TAR_HEADER_BYTES",
    "PATH_ECHO_CHARS",
    "READ_CHUNK",
    "SECRETS_DIR",
    "SECRETS_FILE_PREFIX",
    "BundleError",
    "Destination",
    "Limits",
    "is_archive_junk",
    "unpack",
]

"""The entry point: dispatch on the file name, and the refusals about the bundle as a whole."""

from __future__ import annotations

from pathlib import Path

from toon.constants import INDEX_FILE
from toon.ingest.unpacker.errors import BundleError, _unreadable
from toon.ingest.unpacker.limits import Limits
from toon.ingest.unpacker.loop import _Outcome
from toon.ingest.unpacker.paths import _JUNK_DIR, _JUNK_PREFIX, _validate_base_path
from toon.ingest.unpacker.tar_archive import _unpack_tar
from toon.ingest.unpacker.writing import Destination, _Tree, _write_entry
from toon.ingest.unpacker.zip_archive import _unpack_zip
from toon.messages import Msg


def _unpack_html(source: Path, destination: Destination, limits: Limits) -> _Outcome:
    tree = _Tree(limits)
    try:
        with source.open("rb") as stream:
            _write_entry(stream, INDEX_FILE, source.stat().st_size, tree, destination, limits)
    except OSError as error:
        raise BundleError("INVALID_ARCHIVE.html", params={"error": _unreadable(error)}) from error
    return _Outcome(count=tree.count, root_seen=True, index_seen=True)


def _base_path_unknown_error(base_path: str | None, outcome: _Outcome) -> BundleError:
    """The base path occurs nowhere in the archive. The message names the two
    things the user cannot see in their own bundle: a directory that differs
    in capitalisation only, and the prefix the peeling took away."""
    params: dict[str, object] = {"base_path": base_path}
    if outcome.base_variant is not None:
        key = "BASE_PATH_UNKNOWN.case_variant"
        params["variant"] = outcome.base_variant
    elif outcome.peeled:
        key = "BASE_PATH_UNKNOWN.peeled"
        params["peeled"] = outcome.peeled
    else:
        key = "BASE_PATH_UNKNOWN"
    return BundleError(key, params=params, index_candidates=outcome.suggestions)


def _suggestion_sentence(shortest: str) -> Msg:
    """What can be done with the nearest index.html. If it already sits in the
    root there is no directory to point at, and the answer is to leave the
    base path out."""
    if "/" not in shortest:
        return Msg("suggestion.in_root", {"index": INDEX_FILE})
    return Msg("suggestion.nearest", {"path": shortest})


def _no_index_error(base_path: str | None, outcome: _Outcome) -> BundleError:
    if base_path is not None:
        # A base path with no files in it (an empty directory) is the same
        # problem as a base path without an index: the directory is no good as
        # a root, and the candidates point the way to one that is.
        what = "empty" if outcome.count == 0 else "no_index"
        params: dict[str, object] = {"base_path": base_path, "index": INDEX_FILE}
        if outcome.suggestions:
            key = f"BASE_PATH_WITHOUT_INDEX.{what}_with_suggestion"
            params["suggestion"] = _suggestion_sentence(outcome.suggestions[0])
        else:
            key = f"BASE_PATH_WITHOUT_INDEX.{what}"
        return BundleError(key, params=params, index_candidates=outcome.suggestions)

    if not outcome.suggestions:
        if outcome.case_variant is not None:
            return BundleError(
                "NO_INDEX.case_variant",
                params={"index": INDEX_FILE, "variant": outcome.case_variant},
            )
        return BundleError("NO_INDEX", params={"index": INDEX_FILE})

    # The shortest candidate always sits in a subdirectory: were it in the
    # root, there would be an index and we would not get here.
    shortest = outcome.suggestions[0]
    dir_name = shortest.rsplit("/", 1)[0]
    return BundleError(
        "NO_INDEX.nearest",
        params={"index": INDEX_FILE, "shortest": shortest, "directory": dir_name},
        index_candidates=outcome.suggestions,
    )


def unpack(
    filename: str,
    source: Path,
    destination: Destination,
    limits: Limits,
    base_path: str | None = None,
) -> int:
    """Unpacks the upload in `source` into `destination`; returns the number of
    files written and raises BundleError on a refusal. The caller cleans up the
    destination on an error (the store does that in write_version).

    The refusal over a missing index deliberately comes after the unpacking:
    an unsafe or oversized path keeps its own, more precise reason that way,
    and nothing is left behind regardless because the work directory
    disappears on every error.
    """
    base = _validate_base_path(base_path, limits)
    name = filename.lower()
    if name.endswith(".html"):
        if base:
            raise BundleError("BASE_PATH_UNKNOWN.html_file", params={"base_path": base_path})
        outcome = _unpack_html(source, destination, limits)
    elif name.endswith(".zip"):
        outcome = _unpack_zip(source, destination, limits, base)
    elif name.endswith((".tar.gz", ".tgz")):
        outcome = _unpack_tar(source, destination, limits, base)
    else:
        raise BundleError("UNKNOWN_FORMAT")

    if not outcome.root_seen:
        raise _base_path_unknown_error(base_path, outcome)
    if outcome.root_is_file:
        raise BundleError(
            "BASE_PATH_UNKNOWN.file",
            params={"base_path": base_path},
            index_candidates=outcome.suggestions,
        )
    if outcome.count == 0:
        # With a base path, "empty" is a statement about that one directory,
        # not about the bundle: that one is usually full of files.
        if base_path is not None:
            raise _no_index_error(base_path, outcome)
        raise BundleError(
            "EMPTY_ARCHIVE", params={"junk_dir": _JUNK_DIR, "junk_prefix": _JUNK_PREFIX}
        )
    if not outcome.index_seen:
        raise _no_index_error(base_path, outcome)
    return outcome.count

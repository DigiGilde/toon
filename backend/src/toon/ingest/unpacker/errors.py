"""The refusal of a bundle, and what the client may be told about an unreadable archive."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from toon import i18n, messages
from toon.messages import Msg

# Named after the package, not this module: the unpacker logs as one.
_logger = logging.getLogger(__package__)


class BundleError(Exception):
    """Refused or invalid bundle, with a machine-readable reason code.

    It names a message key from toon/messages.py rather than a text, so the
    same refusal reaches the client in the language it asked for; `reason` is
    the code that key carries.

    Every text among the params, and every index candidate, is made
    printable here (see `_printable`): a name from an archive reaches the
    JSON answer only through this.

    On a refusal about the root, `index_candidates` carries the index.html
    paths found, shortest first, relative to the root after the peeling:
    exactly the values that can be sent back as `basePath`.
    """

    def __init__(
        self,
        key: str,
        *,
        params: Mapping[str, object] | None = None,
        index_candidates: tuple[str, ...] = (),
    ) -> None:
        self.message = Msg(key, _printable_params(params or {}))
        self.reason = messages.code_of(key)
        self.index_candidates = tuple(_printable(candidate) for candidate in index_candidates)
        super().__init__(messages.render(i18n.API_DEFAULT, self.message))


def _name_bytes(value: str) -> bytes:
    """The bytes a name was declared under.

    tarfile decodes a byte that is not UTF-8 to a lone surrogate
    (surrogateescape), which turns back into that one byte here. Another
    lone surrogate, which only a decoded request can hold, is taken as its
    three-byte form.
    """
    try:
        return value.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return value.encode("utf-8", "surrogatepass")


def _printable(value: str) -> str:
    """A name as a refusal repeats it: valid UTF-8, so the JSON answer can
    carry it, with a byte that is not UTF-8 and a character that does not
    print (a control character, a null byte) written as its escape."""
    text = _name_bytes(value).decode("utf-8", "backslashreplace")
    return "".join(char if char.isprintable() else ascii(char)[1:-1] for char in text)


def _printable_params(params: Mapping[str, object]) -> dict[str, object]:
    return {
        name: _printable(value)
        if isinstance(value, str)
        else Msg(value.key, _printable_params(value.params))
        if isinstance(value, Msg)
        else value
        for name, value in params.items()
    }


def _unreadable(error: Exception) -> str:
    """Logs the unreadable archive and returns what the client may be told.

    Never `str(error)`: an OSError names the absolute path of the spool file
    on the server, and a problem+json `detail` carries no internal details.
    """
    _logger.warning("Archive unreadable", exc_info=error)
    return type(error).__name__

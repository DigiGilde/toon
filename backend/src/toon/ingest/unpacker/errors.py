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
        self.message = Msg(key, dict(params or {}))
        self.reason = messages.code_of(key)
        self.index_candidates = index_candidates
        super().__init__(messages.render(i18n.API_DEFAULT, self.message))


def _unreadable(error: Exception) -> str:
    """Logs the unreadable archive and returns what the client may be told.

    Never `str(error)`: an OSError names the absolute path of the spool file
    on the server, and a problem+json `detail` carries no internal details.
    """
    _logger.warning("Archive unreadable", exc_info=error)
    return type(error).__name__

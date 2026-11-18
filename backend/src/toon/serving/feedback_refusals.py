"""The page tokens the widget API and the hide route refuse, counted for the
operator (BIO2, ISO 27002:2022 8.16 on monitoring).

Every refusal answers the same, so nobody learns the reason from it. This
process counts them per reason, in windows of REFUSED_TOKENS_WINDOW_S
seconds. A window that ends with more than REFUSED_TOKENS_THRESHOLD of them
is one warning line, with the total and the count per reason. A request
that presents no token at all counts for nothing. The line holds nothing
about a visitor: no address, no sub, no site, no token. A window ends at the
first refusal after it, or at the sweep that runs for the lifespan of
main.py, whichever comes first.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from enum import StrEnum

_logger = logging.getLogger(__name__)

REFUSED_TOKENS_WINDOW_S = 600
REFUSED_TOKENS_THRESHOLD = 100
# How often the sweep ends a window nobody refused a token after: a window's
# line comes at most this late.
SWEEP_INTERVAL_S = 60


class RefusedToken(StrEnum):
    """Why a page token was refused, in the order the line lists them."""

    # Not a page token Toon sealed, or one with a field minting never writes.
    UNOPENED = "unopened"
    EXPIRED = "expired"
    # The session it names is gone, is no content session, or the IdP no
    # longer stands behind it.
    SESSION = "session"
    CONSENT = "consent"
    GATE = "gate"
    # Feedback is off for the target, or the live site of a site that takes
    # it on previews only.
    FEEDBACK_OFF = "feedback_off"
    # The token's site is gone, or another one answers at its address.
    SITE = "site"


class RefusalTally:
    """The refusals of the window that runs, in the memory of this process."""

    def __init__(self) -> None:
        self._start: float | None = None
        self._counts = dict.fromkeys(RefusedToken, 0)

    def note(self, reason: RefusedToken, now: float) -> None:
        self.close(now)
        if self._start is None:
            self._start = now
        self._counts[reason] += 1

    def close(self, now: float) -> None:
        """Ends the window that runs once its time is up, with the warning
        when it holds more refusals than the threshold."""
        if self._start is None or now - self._start < REFUSED_TOKENS_WINDOW_S:
            return
        total = sum(self._counts.values())
        if total > REFUSED_TOKENS_THRESHOLD:
            _logger.warning(
                "Refused feedback page tokens: %d in a window of %d seconds, more than %d. Per reason: %s.",
                total,
                REFUSED_TOKENS_WINDOW_S,
                REFUSED_TOKENS_THRESHOLD,
                ", ".join(f"{reason.value}={count}" for reason, count in self._counts.items()),
            )
        self._start = None
        self._counts = dict.fromkeys(RefusedToken, 0)


# A clock of its own, so a test can move it without moving asyncio's.
_clock = time.monotonic
_tally = RefusalTally()


def refused(reason: RefusedToken) -> None:
    """Counts one refused page token."""
    _tally.note(reason, _clock())


async def _close_every(interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        _tally.close(_clock())


@asynccontextmanager
async def refused_token_sweep() -> AsyncIterator[asyncio.Task]:
    """Background task that ends a window whose time is up even when no
    refusal follows it, for the lifespan of main.py."""
    task = asyncio.create_task(_close_every(SWEEP_INTERVAL_S))
    try:
        yield task
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


__all__ = [
    "REFUSED_TOKENS_THRESHOLD",
    "REFUSED_TOKENS_WINDOW_S",
    "SWEEP_INTERVAL_S",
    "RefusalTally",
    "RefusedToken",
    "refused",
    "refused_token_sweep",
]

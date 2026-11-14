"""Audit retention job: nightly at 04:00 UTC, publishes the chain checkpoint and
then purges what has outlived its retention.

ZAD has no scheduler, so the app does what `python -m toon.audit.checkpoint` and
`python -m toon.audit.retention` do by hand, on the same TOON_DB_URL.

`run_once` is the core and can be called on its own by tests; main.py starts the
background loop through `audit_retention_job()`, an async context manager,
inside its lifespan.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, time

from toon.audit import checkpoint, retention
from toon.previews.cleanup_job import _seconds_until

TIMESTAMP_DEFAULT = time(4, 0)

_logger = logging.getLogger(__name__)


async def run_once(dsn: str, *, purge_paused: bool) -> None:
    """Publishes the checkpoint, then purges. The checkpoint goes first so the
    line names the oldest row of every chain, with its deadline, before the
    purge takes it. A failing checkpoint does not hold the purge back: the
    retention maximum does not depend on it. `purge_paused` skips the purge
    and says so every night, so nobody forgets it is on."""
    try:
        published = await checkpoint.collect(dsn)
    except Exception:
        _logger.exception("Publishing the audit chain checkpoint failed, purging anyway")
    else:
        # WARNING, because nothing shows INFO for this logger under uvicorn, and
        # the line is worth only what leaves the machine.
        _logger.warning("%s %s", checkpoint.MARKER, published.as_json())
    if purge_paused:
        _logger.warning(
            "Audit purge paused (TOON_AUDIT_PURGE_PAUSED): rows past their retention stay until it is turned off"
        )
        return
    try:
        await retention.purge(dsn)
    except Exception:
        _logger.exception("Purging the audit log failed, retrying on the next run")


async def _run_daily(dsn: str, *, purge_paused: bool, occurred_at: time, stop: asyncio.Event) -> None:
    while not stop.is_set():
        wait_time = _seconds_until(occurred_at, datetime.now(tz=UTC))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=wait_time)
        if stop.is_set():
            break
        await run_once(dsn, purge_paused=purge_paused)


@asynccontextmanager
async def audit_retention_job(
    dsn: str, *, purge_paused: bool, occurred_at: time = TIMESTAMP_DEFAULT
) -> AsyncIterator[asyncio.Task]:
    """Background task that runs `run_once` daily at `occurred_at` (UTC).

    Meant to be opened inside the lifespan of main.py:
    `async with audit_retention_job(settings.db_url, purge_paused=...): yield`.
    """
    stop = asyncio.Event()
    task = asyncio.create_task(_run_daily(dsn, purge_paused=purge_paused, occurred_at=occurred_at, stop=stop))
    try:
        yield task
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

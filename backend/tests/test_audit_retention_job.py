"""Tests for audit/retention_job.py: the nightly run publishes the chain
checkpoint before it purges, a failure in either step is logged without
stopping the other or the loop, and shutdown ends a run in progress.

The DB tests commit against the test container database, like the purge tests
in test_audit.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import timedelta
from pathlib import Path

import asyncpg
from helpers_audit import HOLD_REASON, insert_aged_audit_row, open_purge_hold
from helpers_oidc import MockIdP, make_settings

from toon.audit import checkpoint, retention, vocabulary
from toon.audit.retention_job import TIMESTAMP_DEFAULT, _run_daily, audit_retention_job, run_once
from toon.main import create_app

LOGGER = "toon.audit.retention_job"


async def _aged_row(dsn: str, action: str, result: str, age: timedelta) -> tuple[uuid.UUID, int, int]:
    connection = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        entry_id = await insert_aged_audit_row(connection, action, result, age)
        row = await connection.fetchrow(
            "SELECT chain_shard, chain_seq FROM audit_log_entries WHERE id = $1", entry_id
        )
        return entry_id, row["chain_shard"], row["chain_seq"]
    finally:
        await connection.close()


async def _audit_ids(connection: asyncpg.Connection) -> set[uuid.UUID]:
    return {row["id"] for row in await connection.fetch("SELECT id FROM audit_log_entries")}


async def _audit_ids_of(dsn: str) -> set[uuid.UUID]:
    connection = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        return await _audit_ids(connection)
    finally:
        await connection.close()


def _published_lines(caplog) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == LOGGER and record.getMessage().startswith(checkpoint.MARKER)
    ]


def _errors(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER and record.levelno == logging.ERROR
    ]


class TestRunOnce:
    async def test_the_line_names_the_front_the_purge_then_takes(
        self, migrated_dsn: str, db_connection: asyncpg.Connection, caplog
    ):
        expired, shard, seq = await _aged_row(migrated_dsn, "content_access", "allowed", timedelta(days=181))
        recent, _, _ = await _aged_row(migrated_dsn, "content_access", "allowed", timedelta(days=7))

        with caplog.at_level(logging.INFO):
            await run_once(migrated_dsn)

        [line] = _published_lines(caplog)
        assert line.levelno == logging.WARNING
        published = {head.shard: head for head in checkpoint.parse(line.getMessage()).heads}
        assert published[shard].first_seq == seq
        remaining = await _audit_ids(db_connection)
        assert expired not in remaining
        assert recent in remaining
        purge_row = await db_connection.fetchrow(
            "SELECT refs FROM audit_log_entries WHERE action = $1", vocabulary.AUDIT_PURGE
        )
        assert json.loads(purge_row["refs"]) == {"audit_log_entries": 1, "content_viewers": 0}
        assert _errors(caplog) == []

    async def test_a_failing_checkpoint_is_logged_and_the_purge_still_runs(
        self, migrated_dsn: str, db_connection: asyncpg.Connection, monkeypatch, caplog
    ):
        expired, _, _ = await _aged_row(migrated_dsn, "logout", "allowed", timedelta(days=181))

        async def broken_collect(dsn: str):
            raise RuntimeError("database unreachable")

        monkeypatch.setattr(checkpoint, "collect", broken_collect)
        with caplog.at_level(logging.INFO):
            await run_once(migrated_dsn)

        assert _published_lines(caplog) == []
        assert any("checkpoint failed" in message for message in _errors(caplog))
        assert expired not in await _audit_ids(db_connection)

    async def test_a_failing_purge_is_logged_after_the_line_went_out(
        self, migrated_dsn: str, db_connection: asyncpg.Connection, monkeypatch, caplog
    ):
        expired, _, _ = await _aged_row(migrated_dsn, "logout", "allowed", timedelta(days=181))

        async def broken_purge(dsn: str, **kwargs):
            raise RuntimeError("database unreachable")

        monkeypatch.setattr(retention, "purge", broken_purge)
        with caplog.at_level(logging.INFO):
            await run_once(migrated_dsn)

        assert len(_published_lines(caplog)) == 1
        assert any("Purging the audit log failed" in message for message in _errors(caplog))
        assert expired in await _audit_ids(db_connection)

    async def test_a_held_purge_still_publishes_and_says_since_when(
        self, migrated_dsn: str, db_connection: asyncpg.Connection, caplog
    ):
        expired, shard, seq = await _aged_row(migrated_dsn, "content_access", "allowed", timedelta(days=181))
        started_at = await open_purge_hold(migrated_dsn)

        with caplog.at_level(logging.INFO):
            await run_once(migrated_dsn)

        [line] = _published_lines(caplog)
        assert {head.shard: head for head in checkpoint.parse(line.getMessage()).heads}[shard].first_seq == seq
        held = [record for record in caplog.records if record.name == LOGGER and "on hold" in record.getMessage()]
        assert [record.levelno for record in held] == [logging.WARNING]
        assert held[0].getMessage() == (
            f"Audit purge on hold since {started_at.date().isoformat()}: "
            "rows past their retention stay until the hold ends"
        )
        assert HOLD_REASON not in caplog.text
        assert expired in await _audit_ids(db_connection)
        assert await db_connection.fetchval(
            "SELECT count(*) FROM audit_log_entries WHERE action = $1", vocabulary.AUDIT_PURGE
        ) == 0
        assert _errors(caplog) == []

    async def test_an_ended_hold_lets_the_purge_run_again(
        self, migrated_dsn: str, db_connection: asyncpg.Connection, caplog
    ):
        expired, _, _ = await _aged_row(migrated_dsn, "content_access", "allowed", timedelta(days=181))
        await open_purge_hold(migrated_dsn, ended=True)

        with caplog.at_level(logging.INFO):
            await run_once(migrated_dsn)

        assert not [record for record in caplog.records if "on hold" in record.getMessage()]
        assert expired not in await _audit_ids(db_connection)

    async def test_an_unreachable_database_fails_both_steps_without_raising(self, caplog):
        with caplog.at_level(logging.INFO):
            await run_once("postgresql+asyncpg://nobody:secret@127.0.0.1:1/none")

        errors = _errors(caplog)
        assert len(errors) == 2
        assert _published_lines(caplog) == []
        assert "secret" not in caplog.text


async def _no_hold(dsn: str) -> None:
    return None


def _fake_steps(monkeypatch, *, collect, purge) -> None:
    monkeypatch.setattr(checkpoint, "collect", collect)
    monkeypatch.setattr(retention, "hold_started_at", _no_hold)
    monkeypatch.setattr(retention, "purge", purge)


class TestRunDaily:
    """The loop must outlive a run that fails, and its stop event must end it
    without starting another run."""

    async def test_a_failing_run_is_logged_and_the_next_tick_still_runs(self, monkeypatch, caplog):
        purges: list[str] = []
        second_run = asyncio.Event()

        async def collect(dsn: str):
            raise RuntimeError("database unreachable")

        async def purge(dsn: str, **kwargs):
            purges.append(dsn)
            if len(purges) == 1:
                raise RuntimeError("database unreachable")
            second_run.set()
            return 0

        _fake_steps(monkeypatch, collect=collect, purge=purge)
        monkeypatch.setattr("toon.audit.retention_job._seconds_until", lambda occurred_at, reference: 0.0)

        stop = asyncio.Event()
        with caplog.at_level(logging.ERROR):
            task = asyncio.create_task(_run_daily("dsn", occurred_at=TIMESTAMP_DEFAULT, stop=stop))
            await asyncio.wait_for(second_run.wait(), timeout=5)
            stop.set()
            await asyncio.wait_for(task, timeout=5)

        assert len(purges) >= 2
        assert any("Purging the audit log failed" in message for message in _errors(caplog))

    async def test_stopping_while_waiting_runs_nothing_and_ends_the_task(self, monkeypatch):
        calls: list[str] = []

        async def collect(dsn: str):
            calls.append("collect")

        async def purge(dsn: str, **kwargs):
            calls.append("purge")

        _fake_steps(monkeypatch, collect=collect, purge=purge)
        monkeypatch.setattr("toon.audit.retention_job._seconds_until", lambda occurred_at, reference: 3600.0)

        stop = asyncio.Event()
        task = asyncio.create_task(_run_daily("dsn", occurred_at=TIMESTAMP_DEFAULT, stop=stop))
        await asyncio.sleep(0)
        stop.set()
        await asyncio.wait_for(task, timeout=5)

        assert calls == []

    async def test_stop_already_set_before_the_first_tick_runs_nothing(self, monkeypatch):
        calls: list[str] = []

        async def purge(dsn: str, **kwargs):
            calls.append("purge")

        _fake_steps(monkeypatch, collect=purge, purge=purge)

        stop = asyncio.Event()
        stop.set()
        await asyncio.wait_for(
            _run_daily("dsn", occurred_at=TIMESTAMP_DEFAULT, stop=stop), timeout=5
        )

        assert calls == []


class TestAuditRetentionJob:
    async def test_the_dsn_reaches_both_steps_checkpoint_first(self, monkeypatch, caplog):
        steps: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

        class Published:
            def as_json(self) -> str:
                return "{}"

        async def collect(dsn: str):
            steps.put_nowait(("collect", dsn))
            return Published()

        async def purge(dsn: str, **kwargs):
            steps.put_nowait(("purge", dsn))
            return 0

        _fake_steps(monkeypatch, collect=collect, purge=purge)
        monkeypatch.setattr("toon.audit.retention_job._seconds_until", lambda occurred_at, reference: 0.0)

        async with audit_retention_job("postgresql+asyncpg://app/toon"):
            first = await asyncio.wait_for(steps.get(), timeout=5)
            second = await asyncio.wait_for(steps.get(), timeout=5)

        assert (first, second) == (("collect", "postgresql+asyncpg://app/toon"), ("purge", "postgresql+asyncpg://app/toon"))

    async def test_shutdown_during_a_purge_cancels_it_and_logs_no_failure(self, monkeypatch, caplog):
        started = asyncio.Event()
        outcome: list[str] = []

        async def collect(dsn: str):
            raise RuntimeError("database unreachable")

        async def purge(dsn: str, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                outcome.append("cancelled")
                raise

        _fake_steps(monkeypatch, collect=collect, purge=purge)
        monkeypatch.setattr("toon.audit.retention_job._seconds_until", lambda occurred_at, reference: 0.0)

        with caplog.at_level(logging.ERROR):
            async with audit_retention_job("dsn") as task:
                await asyncio.wait_for(started.wait(), timeout=5)

        assert task.cancelled()
        assert outcome == ["cancelled"]
        assert not any("Purging the audit log failed" in message for message in _errors(caplog))

    async def test_the_lifespan_runs_and_stops_the_job(self, tmp_path: Path):
        app = create_app(make_settings(MockIdP(), content_root=tmp_path / "content"))
        async with app.router.lifespan_context(app):
            task = app.state.audit_retention_task
            assert not task.done()
        assert task.done()

class TestManualPurge:
    """`python -m toon.audit.retention` asks the database for an open hold and
    refuses while there is one, before it purges anything."""

    def test_an_open_hold_refuses_the_purge(self, migrated_dsn: str, monkeypatch, caplog):
        expired, _, _ = asyncio.run(_aged_row(migrated_dsn, "logout", "allowed", timedelta(days=181)))
        started_at = asyncio.run(open_purge_hold(migrated_dsn))
        monkeypatch.setenv(retention.DB_URL_VAR, migrated_dsn)

        with caplog.at_level(logging.INFO):
            assert retention.main() == 1

        assert [record.levelno for record in caplog.records] == [logging.ERROR]
        assert f"on hold since {started_at.date().isoformat()}" in caplog.text
        assert HOLD_REASON not in caplog.text
        assert asyncio.run(_audit_ids_of(migrated_dsn)) >= {expired}

    def test_an_ended_hold_lets_it_purge(self, migrated_dsn: str, monkeypatch, caplog):
        expired, _, _ = asyncio.run(_aged_row(migrated_dsn, "logout", "allowed", timedelta(days=181)))
        asyncio.run(open_purge_hold(migrated_dsn, ended=True))
        monkeypatch.setenv(retention.DB_URL_VAR, migrated_dsn)

        with caplog.at_level(logging.INFO):
            assert retention.main() == 0

        assert expired not in asyncio.run(_audit_ids_of(migrated_dsn))

    async def test_the_database_tells_whether_a_hold_is_open(self, migrated_dsn: str):
        assert await retention.hold_started_at(migrated_dsn) is None
        await open_purge_hold(migrated_dsn, ended=True)
        assert await retention.hold_started_at(migrated_dsn) is None
        started_at = await open_purge_hold(migrated_dsn)
        assert await retention.hold_started_at(migrated_dsn) == started_at

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
from helpers_audit import insert_aged_audit_row
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
            await run_once(migrated_dsn, purge_paused=False)

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
            await run_once(migrated_dsn, purge_paused=False)

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
            await run_once(migrated_dsn, purge_paused=False)

        assert len(_published_lines(caplog)) == 1
        assert any("Purging the audit log failed" in message for message in _errors(caplog))
        assert expired in await _audit_ids(db_connection)

    async def test_a_paused_purge_still_publishes_and_says_it_is_paused(
        self, migrated_dsn: str, db_connection: asyncpg.Connection, caplog
    ):
        expired, shard, seq = await _aged_row(migrated_dsn, "content_access", "allowed", timedelta(days=181))

        with caplog.at_level(logging.INFO):
            await run_once(migrated_dsn, purge_paused=True)

        [line] = _published_lines(caplog)
        assert {head.shard: head for head in checkpoint.parse(line.getMessage()).heads}[shard].first_seq == seq
        paused = [
            record
            for record in caplog.records
            if record.name == LOGGER and "TOON_AUDIT_PURGE_PAUSED" in record.getMessage()
        ]
        assert [record.levelno for record in paused] == [logging.WARNING]
        assert expired in await _audit_ids(db_connection)
        assert await db_connection.fetchval(
            "SELECT count(*) FROM audit_log_entries WHERE action = $1", vocabulary.AUDIT_PURGE
        ) == 0
        assert _errors(caplog) == []

    async def test_an_unreachable_database_fails_both_steps_without_raising(self, caplog):
        with caplog.at_level(logging.INFO):
            await run_once("postgresql+asyncpg://nobody:secret@127.0.0.1:1/none", purge_paused=False)

        errors = _errors(caplog)
        assert len(errors) == 2
        assert _published_lines(caplog) == []
        assert "secret" not in caplog.text


def _fake_steps(monkeypatch, *, collect, purge) -> None:
    monkeypatch.setattr(checkpoint, "collect", collect)
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
            task = asyncio.create_task(_run_daily("dsn", purge_paused=False, occurred_at=TIMESTAMP_DEFAULT, stop=stop))
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
        task = asyncio.create_task(_run_daily("dsn", purge_paused=False, occurred_at=TIMESTAMP_DEFAULT, stop=stop))
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
            _run_daily("dsn", purge_paused=False, occurred_at=TIMESTAMP_DEFAULT, stop=stop), timeout=5
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

        async with audit_retention_job("postgresql+asyncpg://app/toon", purge_paused=False):
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
            async with audit_retention_job("dsn", purge_paused=False) as task:
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

    async def test_the_lifespan_hands_the_pause_to_the_job(self, tmp_path: Path, monkeypatch, caplog):
        published = asyncio.Event()
        purges: list[str] = []

        class Published:
            def as_json(self) -> str:
                return "{}"

        async def collect(dsn: str):
            return Published()

        async def purge(dsn: str, **kwargs):
            purges.append(dsn)
            return 0

        _fake_steps(monkeypatch, collect=collect, purge=purge)
        monkeypatch.setattr("toon.audit.retention_job._seconds_until", lambda occurred_at, reference: 0.0)

        class PausedSeen(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if "TOON_AUDIT_PURGE_PAUSED" in record.getMessage():
                    published.set()

        handler = PausedSeen()
        logging.getLogger(LOGGER).addHandler(handler)
        try:
            app = create_app(
                make_settings(MockIdP(), content_root=tmp_path / "content", audit_purge_paused=True)
            )
            async with app.router.lifespan_context(app):
                await asyncio.wait_for(published.wait(), timeout=5)
        finally:
            logging.getLogger(LOGGER).removeHandler(handler)

        assert purges == []


class TestManualPurge:
    """`python -m toon.audit.retention` reads TOON_AUDIT_PURGE_PAUSED itself and
    refuses while it is on, before it touches the database."""

    def test_a_pause_refuses_the_purge(self, monkeypatch, caplog):
        purges: list[str] = []

        async def purge(dsn: str, **kwargs):
            purges.append(dsn)
            return 0

        monkeypatch.setattr(retention, "purge", purge)
        monkeypatch.setenv(retention.DB_URL_VAR, "postgresql+asyncpg://app/toon")
        for value in ("true", "True", "1", "yes", "on"):
            monkeypatch.setenv(retention.PAUSED_VAR, value)
            caplog.clear()
            with caplog.at_level(logging.INFO):
                assert retention.main() == 1
            assert [record.levelno for record in caplog.records] == [logging.ERROR]
            assert retention.PAUSED_VAR in caplog.text
        assert purges == []

    def test_a_value_settings_would_refuse_refuses_the_purge(self, monkeypatch, caplog):
        purges: list[str] = []

        async def purge(dsn: str, **kwargs):
            purges.append(dsn)
            return 0

        monkeypatch.setattr(retention, "purge", purge)
        monkeypatch.setenv(retention.DB_URL_VAR, "postgresql+asyncpg://app/toon")
        for value in ("", " true", "paused", "2"):
            monkeypatch.setenv(retention.PAUSED_VAR, value)
            caplog.clear()
            with caplog.at_level(logging.INFO):
                assert retention.main() == 1
            assert retention.PAUSED_VAR in caplog.text
        assert purges == []

    def test_false_or_unset_purges(self, monkeypatch, caplog):
        purges: list[str] = []

        async def purge(dsn: str, **kwargs):
            purges.append(dsn)
            return 0

        monkeypatch.setattr(retention, "purge", purge)
        monkeypatch.setenv(retention.DB_URL_VAR, "postgresql+asyncpg://app/toon")
        for value in ("false", "0", "no", "off", None):
            if value is None:
                monkeypatch.delenv(retention.PAUSED_VAR, raising=False)
            else:
                monkeypatch.setenv(retention.PAUSED_VAR, value)
            assert retention.main() == 0
        assert purges == ["postgresql+asyncpg://app/toon"] * 5

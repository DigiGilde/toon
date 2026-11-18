"""Tests for serving/feedback_refusals.py: the count of the page tokens the
widget API and the hide route refuse, and the one warning a window with too
many of them ends with. First the window on its own, then the sweep that ends
a window nobody refused a token after, then the reason each ground for
refusal counts under, against the real PostgreSQL test container with the
app of test_feedback_api.py, and last that the line holds nothing about a
visitor.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, update
from test_feedback_api import (
    ITEMS,
    ROOT,
    SECRET,
    Environment,
    assert_problem,
    auth,
    client_for,
    consented_session,
    create,
    open_environment,
    page_token,
    parts,
    rolled_back,
)
from test_feedback_consent import hide, is_neutral_404

from toon.access.decision import FeedbackPolicy, allow
from toon.constants import AccessBase, AccessPolicy, FeedbackIdentity
from toon.models.publication import Site
from toon.serving import feedback_access, feedback_refusals
from toon.serving.feedback_refusals import (
    REFUSED_TOKENS_THRESHOLD,
    REFUSED_TOKENS_WINDOW_S,
    RefusalTally,
    RefusedToken,
    refused_token_sweep,
)
from toon.serving.feedback_token import TOKEN_LIFETIME_S

LOGGER = "toon.serving.feedback_refusals"
WINDOW = REFUSED_TOKENS_WINDOW_S


def warnings_of(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [(record.levelno, record.getMessage()) for record in caplog.records if record.name == LOGGER]


def line(total: int, **counts: int) -> str:
    per_reason = ", ".join(f"{reason.value}={counts.get(reason.value, 0)}" for reason in RefusedToken)
    return (
        f"Refused feedback page tokens: {total} in a window of {WINDOW} seconds, "
        f"more than {REFUSED_TOKENS_THRESHOLD}. Per reason: {per_reason}."
    )


def note_many(tally: RefusalTally, start: float, **counts: int) -> None:
    moment = start
    for reason, count in counts.items():
        for _ in range(count):
            tally.note(RefusedToken(reason), moment)
            moment += 1


# --- The window ----------------------------------------------------------------------


class TestTheWindow:
    def test_up_to_the_threshold_is_no_line(self, caplog):
        tally = RefusalTally()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            note_many(tally, 0.0, unopened=REFUSED_TOKENS_THRESHOLD)
            tally.close(WINDOW)
        assert warnings_of(caplog) == []

    def test_over_the_threshold_is_one_line_when_the_window_ends(self, caplog):
        tally = RefusalTally()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            note_many(tally, 0.0, unopened=60, expired=30, gate=11)
            tally.close(WINDOW - 0.5)
            assert warnings_of(caplog) == []
            tally.close(WINDOW)
            tally.close(3 * WINDOW)
        assert warnings_of(caplog) == [(logging.WARNING, line(101, unopened=60, expired=30, gate=11))]

    def test_a_refusal_after_the_window_ends_it_and_starts_the_next(self, caplog):
        tally = RefusalTally()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            note_many(tally, 0.0, session=50, consent=51)
            note_many(tally, WINDOW, gate=40, feedback_off=40, site=21)
            # The window of the second batch began at its first refusal.
            tally.close(2 * WINDOW - 1)
            assert len(warnings_of(caplog)) == 1
            tally.close(2 * WINDOW)
            note_many(tally, 3 * WINDOW, expired=5)
            tally.close(4 * WINDOW)
        assert warnings_of(caplog) == [
            (logging.WARNING, line(101, session=50, consent=51)),
            (logging.WARNING, line(101, gate=40, feedback_off=40, site=21)),
        ]

    def test_an_empty_window_ends_without_a_line(self, caplog):
        tally = RefusalTally()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            tally.close(WINDOW)
        assert warnings_of(caplog) == []


class TestTheSweep:
    async def test_it_ends_a_window_nobody_refused_a_token_after(self, monkeypatch, caplog):
        now = [0.0]
        tally = RefusalTally()
        monkeypatch.setattr(feedback_refusals, "_clock", lambda: now[0])
        monkeypatch.setattr(feedback_refusals, "_tally", tally)
        monkeypatch.setattr(feedback_refusals, "SWEEP_INTERVAL_S", 0.01)
        closed, ended = asyncio.Event(), asyncio.Event()
        close = tally.close

        def watched(now_: float) -> None:
            close(now_)
            closed.set()
            if now_ >= WINDOW:
                ended.set()

        monkeypatch.setattr(tally, "close", watched)
        for _ in range(REFUSED_TOKENS_THRESHOLD + 1):
            feedback_refusals.refused(RefusedToken.UNOPENED)
        with caplog.at_level(logging.INFO, logger=LOGGER):
            async with refused_token_sweep() as task:
                await asyncio.wait_for(closed.wait(), 5)
                # Its window still runs.
                assert warnings_of(caplog) == []
                now[0] = float(WINDOW)
                await asyncio.wait_for(ended.wait(), 5)
            assert task.cancelled()
        assert warnings_of(caplog) == [(logging.WARNING, line(101, unopened=101))]


# --- What each refusal counts under -------------------------------------------------


@pytest_asyncio.fixture
async def environment(migrated_dsn: str, tmp_path: Path) -> AsyncIterator[Environment]:
    async with rolled_back(migrated_dsn) as factory:
        yield await open_environment(factory, tmp_path)


@pytest_asyncio.fixture
async def client(environment: Environment) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(environment.app) as c:
        yield c


@pytest.fixture
def tally(monkeypatch) -> RefusalTally:
    fresh = RefusalTally()
    monkeypatch.setattr(feedback_refusals, "_tally", fresh)
    return fresh


def counted(tally: RefusalTally) -> dict[str, int]:
    return {reason.value: count for reason, count in tally._counts.items() if count}


async def _change_site(environment: Environment, **values: object) -> None:
    async with environment.factory() as db:
        await db.execute(update(Site).where(Site.id == environment.world.site_id).values(**values))
        await db.commit()


async def _token_for(ground: str, environment: Environment, monkeypatch) -> str:
    """A page token the widget API refuses on `ground`, and whatever has to
    change around it for that."""
    app, world = environment.app, environment.world
    store = app.state.session_store
    if ground == "garbage":
        return "nep.token"
    if ground == "expired":
        return page_token(app, world.site_id, now=int(time.time()) - TOKEN_LIFETIME_S - 1)
    if ground == "logged-out":
        session = consented_session(app, world.site_id)
        store.delete_session(session.id)
        return page_token(app, world.site_id, session_id=session.id)
    if ground == "admin-session":
        session = store.create_session(sub="lid-actief", email="lid@example.org", email_verified=True, acr="x")
        return page_token(app, world.site_id, session_id=session.id)
    if ground in ("idp-ended", "consent-withdrawn-meanwhile"):
        session = consented_session(app, world.site_id)

        async def revalidated(request, found):
            if ground == "idp-ended":
                return None
            return store.withdraw_feedback(found.id, str(world.site_id))

        monkeypatch.setattr(feedback_access, "revalidate_session", revalidated)
        return page_token(app, world.site_id, session_id=session.id)
    if ground == "not-consented":
        return page_token(app, world.site_id, session_id=consented_session(app).id)
    if ground == "gate-session-gone":
        session = consented_session(app)
        store.delete_session(session.id)
        return page_token(app, world.site_id, access_session_id=session.id)
    if ground == "site-gone":
        async with environment.factory() as db:
            await db.execute(delete(Site).where(Site.id == world.site_id))
            await db.commit()
        return page_token(app, world.site_id)
    if ground == "another-site-at-the-slugs":

        async def other_site(db, group, site, ref, visitor):
            return allow(
                world.other_live_version_id,
                AccessPolicy(AccessBase.PUBLIC),
                site_id=world.other_site_id,
                feedback=FeedbackPolicy(enabled=True, identity=FeedbackIdentity.ANONYMOUS, live=True),
            )

        monkeypatch.setattr(feedback_access, "decide_page", other_site)
        return page_token(app, world.site_id)
    if ground == "access-narrowed":
        await _change_site(environment, access_base=AccessBase.SITE_TEAM)
        return page_token(app, world.site_id)
    assert ground == "feedback-off"
    await _change_site(environment, feedback_enabled=False)
    return page_token(app, world.site_id)


API_GROUNDS = [
    ("garbage", "unopened"),
    ("expired", "expired"),
    ("logged-out", "session"),
    ("admin-session", "session"),
    ("idp-ended", "session"),
    ("gate-session-gone", "session"),
    ("not-consented", "consent"),
    ("consent-withdrawn-meanwhile", "consent"),
    ("access-narrowed", "gate"),
    ("feedback-off", "feedback_off"),
    ("site-gone", "site"),
    ("another-site-at-the-slugs", "site"),
]


class TestTheWidgetApiCounts:
    @pytest.mark.parametrize(("ground", "reason"), API_GROUNDS, ids=[ground for ground, _ in API_GROUNDS])
    async def test_each_ground_under_its_reason(self, client, environment, monkeypatch, tally, ground, reason):
        token = await _token_for(ground, environment, monkeypatch)
        assert_problem(await create(client, token), 404, "FEEDBACK_UNAVAILABLE")
        assert counted(tally) == {reason: 1}

    async def test_a_request_without_a_token_counts_for_nothing(self, client, tally):
        assert_problem(await client.post(ITEMS, files=parts()), 404, "FEEDBACK_UNAVAILABLE")
        assert counted(tally) == {}

    async def test_an_accepted_token_counts_for_nothing(self, client, environment, tally):
        app, world = environment.app, environment.world
        session = consented_session(app, world.site_id)
        assert (await create(client, page_token(app, world.site_id))).status_code == 201
        mine = await client.get(ITEMS, headers=auth(page_token(app, world.site_id, session_id=session.id)))
        assert mine.status_code == 200
        assert counted(tally) == {}


class TestTheHideRouteCounts:
    async def test_a_token_that_does_not_open(self, client, tally):
        assert is_neutral_404(await hide(client, {"token": "nep", "returnTo": ROOT}))
        assert counted(tally) == {"unopened": 1}

    async def test_an_expired_token(self, client, environment, tally):
        expired = page_token(environment.app, environment.world.site_id, now=int(time.time()) - TOKEN_LIFETIME_S - 1)
        assert is_neutral_404(await hide(client, {"token": expired, "returnTo": ROOT}))
        assert counted(tally) == {"expired": 1}

    async def test_a_token_that_does_not_open_with_no_way_back(self, client, tally):
        assert is_neutral_404(await hide(client, {"token": "nep"}))
        assert counted(tally) == {"unopened": 1}

    async def test_a_token_of_another_site_or_of_one_that_is_gone(self, client, environment, tally):
        world = environment.world
        other = page_token(environment.app, world.other_site_id)
        assert is_neutral_404(await hide(client, {"token": other, "returnTo": ROOT}))
        gone = page_token(environment.app, world.site_id)
        async with environment.factory() as db:
            await db.execute(delete(Site).where(Site.id == world.site_id))
            await db.commit()
        assert is_neutral_404(await hide(client, {"token": gone, "returnTo": ROOT}))
        assert counted(tally) == {"site": 2}

    async def test_no_token_an_empty_one_two_tokens_or_a_good_one_count_for_nothing(self, client, environment, tally):
        token = page_token(environment.app, environment.world.site_id)
        assert is_neutral_404(await hide(client, {"returnTo": ROOT}))
        assert is_neutral_404(await hide(client, f"token=nep&token=nep&returnTo={ROOT}"))
        assert is_neutral_404(await hide(client, {"token": "", "returnTo": ROOT}))
        assert is_neutral_404(await hide(client, {"token": "  ", "returnTo": ROOT}))
        assert is_neutral_404(await hide(client, {"token": token}))
        assert (await hide(client, {"token": token, "returnTo": ROOT})).status_code == 303
        assert counted(tally) == {}


# --- What the line holds --------------------------------------------------------------


class TestTheLineHoldsNothingAboutAVisitor:
    async def test_counts_alone(self, environment, monkeypatch, caplog, tally):
        now = [5000.0]
        monkeypatch.setattr(feedback_refusals, "_clock", lambda: now[0])
        monkeypatch.setattr(feedback_refusals, "REFUSED_TOKENS_THRESHOLD", 2)
        app, world = environment.app, environment.world
        await _change_site(environment, feedback_enabled=False)
        session = consented_session(app, world.site_id, sub="kijker-7f3a", email="Jan.Jansen@Example.org")
        token = page_token(app, world.site_id, session_id=session.id)
        with caplog.at_level(logging.INFO, logger=LOGGER):
            async with client_for(app, address="192.0.2.7") as other:
                for _ in range(3):
                    assert_problem(await create(other, token), 404, "FEEDBACK_UNAVAILABLE")
                assert_problem(await create(other, "nep.token"), 404, "FEEDBACK_UNAVAILABLE")
                now[0] += WINDOW
                assert_problem(await create(other, token), 404, "FEEDBACK_UNAVAILABLE")
        logged = warnings_of(caplog)
        counts = {"unopened": 1, "feedback_off": 3}
        per_reason = ", ".join(f"{reason.value}={counts.get(reason.value, 0)}" for reason in RefusedToken)
        expected = (
            f"Refused feedback page tokens: 4 in a window of {WINDOW} seconds, more than 2. Per reason: {per_reason}."
        )
        assert logged == [(logging.WARNING, expected)]
        message = logged[0][1]
        for secret in (
            "192.0.2.7",
            "kijker-7f3a",
            "Jan.Jansen",
            "example.org",
            str(world.site_id),
            "aurora",
            session.id,
            token,
            SECRET,
        ):
            assert secret.lower() not in message.lower()
        assert counted(tally) == {"feedback_off": 1}

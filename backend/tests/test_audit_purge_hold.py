"""The hold on the audit purge: platform admins put it on and take it off
through the API, the database keeps one open at a time, and both are audited.
docs/audit-log.md, "During an incident".

Reuses the admin API fixtures.
"""

# Fixtures imported from test_admin_api are found by name, which ruff reads as
# a parameter shadowing the import.
# ruff: noqa: F811

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from test_admin_api import (  # noqa: F401 - fixtures are found by name
    BASE,
    PROBLEM,
    _count,
    app,
    client,
    content_root,
    data,
    factory,
    login,
)

from toon.api import admin
from toon.api.schema import iso_utc
from toon.audit import vocabulary
from toon.audit.pseudonymisation import pseudonymise
from toon.models.audit import AuditLogEntry, AuditPurgeHold
from toon.models.identity import Member, MemberStatus, PlatformRole

HOLD = f"{BASE}/platform/audit/purge-hold"
END = f"{BASE}/platform/audit/purge-hold/end"
LOOKUP = f"{BASE}/platform/audit/actor-pseudonym"
REASON = "incident 2026-091"
END_REASON = "incident 2026-091 closed"
NOT_HELD = {"onHold": False, "hold": None}
ROUTES = (("GET", HOLD), ("POST", HOLD), ("POST", END))


def _as_admin(client, app) -> dict[str, str]:
    return login(client, app, sub="admin-sub", email="admin@example.nl")


async def _holds(factory) -> list[AuditPurgeHold]:
    async with factory() as db:
        return list(await db.scalars(select(AuditPurgeHold).order_by(AuditPurgeHold.started_at)))


async def _audit_rows(factory, action: str) -> list[AuditLogEntry]:
    async with factory() as db:
        return list(await db.scalars(select(AuditLogEntry).where(AuditLogEntry.action == action)))


class TestStatus:
    async def test_without_a_hold_the_purge_is_not_on_hold(self, client, app, data):
        headers = _as_admin(client, app)

        response = await client.get(HOLD, headers=headers)

        assert response.status_code == 200
        assert response.json() == NOT_HELD

    async def test_the_open_hold_says_why_since_when_and_by_whom(self, client, app, factory, data):
        async with factory() as db:
            await db.execute(update(Member).where(Member.id == data.admin_member.id).values(name="Ada Beheer"))
            await db.commit()
        headers = _as_admin(client, app)
        await client.post(HOLD, json={"reason": REASON}, headers=headers)

        response = await client.get(HOLD, headers=headers)

        [hold] = await _holds(factory)
        assert response.json() == {
            "onHold": True,
            "hold": {
                "reason": REASON,
                "startedAt": iso_utc(hold.started_at),
                "startedByName": "Ada Beheer",
            },
        }

    async def test_without_a_name_the_starter_is_named_by_email(self, client, app, data):
        headers = _as_admin(client, app)

        response = await client.post(HOLD, json={"reason": REASON}, headers=headers)

        assert response.json()["hold"]["startedByName"] == "admin@example.nl"


class TestStarting:
    async def test_a_hold_is_started_and_audited_in_the_long_tier(self, client, app, factory, data):
        headers = _as_admin(client, app)

        response = await client.post(HOLD, json={"reason": f"  {REASON}  "}, headers=headers)

        assert response.status_code == 201
        assert response.json()["onHold"] is True
        assert response.json()["hold"]["reason"] == REASON
        [hold] = await _holds(factory)
        assert (hold.reason, hold.started_by, hold.ended_at) == (REASON, data.admin_member.id, None)
        [row] = await _audit_rows(factory, vocabulary.AUDIT_PURGE_HOLD_STARTED)
        assert row.result == vocabulary.ALLOWED
        assert row.actor_pseudonym == pseudonymise(app.state.settings.audit_pepper, "admin-sub")
        assert row.refs == {"hold": str(hold.id), "reason": REASON}
        async with factory() as db:
            long_tier = await db.scalar(
                text(
                    "SELECT audit_log_chain_retention(chain_shard) = audit_log_retention(1) "
                    "FROM audit_log_entries WHERE id = :id"
                ),
                {"id": row.id},
            )
        assert long_tier is True

    async def test_a_second_hold_is_refused_with_409(self, client, app, factory, data):
        headers = _as_admin(client, app)
        await client.post(HOLD, json={"reason": REASON}, headers=headers)

        response = await client.post(HOLD, json={"reason": "incident 2026-092"}, headers=headers)

        assert response.status_code == 409
        assert response.headers["content-type"] == PROBLEM
        assert response.json()["code"] == "AUDIT_PURGE_ALREADY_ON_HOLD"
        assert response.json()["detail"] == "The audit purge is already on hold."
        assert [hold.reason for hold in await _holds(factory)] == [REASON]
        assert len(await _audit_rows(factory, vocabulary.AUDIT_PURGE_HOLD_STARTED)) == 1

    async def test_the_refusal_speaks_dutch_when_asked(self, client, app, data):
        headers = _as_admin(client, app)
        await client.post(HOLD, json={"reason": REASON}, headers=headers)

        response = await client.post(
            HOLD, json={"reason": REASON}, headers={**headers, "Accept-Language": "nl"}
        )

        assert response.json()["detail"] == "Het opruimen van het auditlog staat al op pauze."

    async def test_two_admins_at_once_get_one_hold_and_one_409(self, client, app, factory, data):
        headers = _as_admin(client, app)

        responses = await asyncio.gather(
            client.post(HOLD, json={"reason": REASON}, headers=headers),
            client.post(HOLD, json={"reason": "incident 2026-092"}, headers=headers),
        )

        assert sorted(response.status_code for response in responses) == [201, 409]
        assert len(await _holds(factory)) == 1

    async def test_another_integrity_error_is_no_409(self, client, app, factory, data, monkeypatch):
        """Only the open-hold index means a hold is already open. Any other
        constraint is a fault, and goes on as it came."""
        headers = _as_admin(client, app)
        await client.post(HOLD, json={"reason": REASON}, headers=headers)
        monkeypatch.setattr(admin, "violated_constraint", lambda error: "some_other_constraint")

        with pytest.raises(IntegrityError):
            await client.post(HOLD, json={"reason": "incident 2026-092"}, headers=headers)

        assert len(await _holds(factory)) == 1

    async def test_a_new_hold_may_follow_an_ended_one(self, client, app, factory, data):
        headers = _as_admin(client, app)
        await client.post(HOLD, json={"reason": REASON}, headers=headers)
        await client.post(END, json={"reason": END_REASON}, headers=headers)

        response = await client.post(HOLD, json={"reason": "incident 2026-092"}, headers=headers)

        assert response.status_code == 201
        assert [hold.ended_at is None for hold in await _holds(factory)] == [False, True]


class TestEnding:
    async def test_the_hold_is_ended_and_audited(self, client, app, factory, data):
        headers = _as_admin(client, app)
        await client.post(HOLD, json={"reason": REASON}, headers=headers)

        response = await client.post(END, json={"reason": END_REASON}, headers=headers)

        assert response.status_code == 200
        assert response.json() == NOT_HELD
        assert (await client.get(HOLD, headers=headers)).json() == NOT_HELD
        [hold] = await _holds(factory)
        assert hold.ended_at is not None
        assert (hold.ended_by, hold.end_reason, hold.reason) == (data.admin_member.id, END_REASON, REASON)
        [row] = await _audit_rows(factory, vocabulary.AUDIT_PURGE_HOLD_ENDED)
        assert row.result == vocabulary.ALLOWED
        assert row.actor_pseudonym == pseudonymise(app.state.settings.audit_pepper, "admin-sub")
        assert row.refs == {"hold": str(hold.id), "reason": END_REASON}

    async def test_ending_without_a_hold_is_refused_with_409(self, client, app, factory, data):
        headers = _as_admin(client, app)

        response = await client.post(END, json={"reason": END_REASON}, headers=headers)

        assert response.status_code == 409
        assert response.headers["content-type"] == PROBLEM
        assert response.json()["code"] == "AUDIT_PURGE_NOT_ON_HOLD"
        assert response.json()["detail"] == "The audit purge is not on hold."
        assert await _audit_rows(factory, vocabulary.AUDIT_PURGE_HOLD_ENDED) == []

    async def test_an_ended_hold_is_not_ended_again(self, client, app, factory, data):
        headers = _as_admin(client, app)
        await client.post(HOLD, json={"reason": REASON}, headers=headers)
        await client.post(END, json={"reason": END_REASON}, headers=headers)

        response = await client.post(END, json={"reason": "incident 2026-091 closed twice"}, headers=headers)

        assert response.status_code == 409
        [hold] = await _holds(factory)
        assert hold.end_reason == END_REASON

    async def test_two_admins_at_once_end_it_once(self, client, app, factory, data):
        headers = _as_admin(client, app)
        await client.post(HOLD, json={"reason": REASON}, headers=headers)

        responses = await asyncio.gather(
            client.post(END, json={"reason": END_REASON}, headers=headers),
            client.post(END, json={"reason": "incident 2026-091 closed too"}, headers=headers),
        )

        assert sorted(response.status_code for response in responses) == [200, 409]
        assert len(await _audit_rows(factory, vocabulary.AUDIT_PURGE_HOLD_ENDED)) == 1


class TestReason:
    @pytest.mark.parametrize("url", [HOLD, END])
    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"reason": ""},
            {"reason": " \r\n\n  \n "},
            {"reason": "x" * 10_001},
            {"reason": "incident\t2026-091"},
            {"reason": "incident ‮2026-091"},
            {"reason": "ask admin@example.nl"},
            {"reason": "incident 2026-091\ncc piet.de.vries@gemeente.example"},
        ],
    )
    async def test_an_invalid_reason_is_422(self, client, app, factory, data, url, body):
        headers = _as_admin(client, app)

        response = await client.post(url, json=body, headers=headers)

        assert response.status_code == 422
        assert await _holds(factory) == []

    @pytest.mark.parametrize("reason", ["7", "x" * 10_000, "INC-1 @ops-team", "zie @piet", "a@b"])
    async def test_a_short_long_or_mentioning_reason_is_accepted(self, client, app, factory, data, reason):
        headers = _as_admin(client, app)

        response = await client.post(HOLD, json={"reason": reason}, headers=headers)

        assert response.status_code == 201
        assert [hold.reason for hold in await _holds(factory)] == [reason]

    @pytest.mark.parametrize(
        ("sent", "kept"),
        [
            ("incident 2026-091\nsuspected leak", "incident 2026-091\nsuspected leak"),
            ("\r\nincident 2026-091\r\nsuspected leak\r\n", "incident 2026-091\nsuspected leak"),
            ("incident 2026-091\rsuspected leak", "incident 2026-091\nsuspected leak"),
        ],
    )
    async def test_a_reason_keeps_its_line_breaks(self, client, app, factory, data, sent, kept):
        headers = _as_admin(client, app)

        response = await client.post(HOLD, json={"reason": sent}, headers=headers)

        assert response.status_code == 201
        assert response.json()["hold"]["reason"] == kept
        assert [hold.reason for hold in await _holds(factory)] == [kept]

    async def test_holding_does_not_count_towards_the_daily_lookup_limit(self, client, app, factory, data):
        app.state.settings.audit_lookup_daily_limit = 1
        headers = _as_admin(client, app)

        assert (await client.post(HOLD, json={"reason": REASON}, headers=headers)).status_code == 201
        assert (await client.post(END, json={"reason": END_REASON}, headers=headers)).status_code == 200
        lookup = {"identifier": "a@example.nl", "reason": "onderzoek naar melding 2026-091"}
        assert (await client.post(LOOKUP, json=lookup, headers=headers)).status_code == 200
        assert (await client.post(LOOKUP, json=lookup, headers=headers)).status_code == 429

        assert (await client.post(HOLD, json={"reason": REASON}, headers=headers)).status_code == 201


class TestAccess:
    """Every ground for refusal, on each of the three routes."""

    @staticmethod
    async def _call(client, method: str, url: str, headers: dict[str, str] | None = None, body=None):
        if method == "GET":
            return await client.get(url, headers=headers)
        return await client.post(url, json={"reason": REASON} if body is None else body, headers=headers)

    @pytest.mark.parametrize(("method", "url"), ROUTES)
    async def test_without_a_session_401(self, client, factory, data, method, url):
        response = await self._call(client, method, url)

        assert response.status_code == 401
        assert response.json()["code"] == "NO_SESSION"
        assert await _holds(factory) == []

    @pytest.mark.parametrize(("method", "url"), ROUTES)
    async def test_a_member_who_is_no_platform_admin_403(self, client, app, factory, data, method, url):
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await self._call(client, method, url, headers)

        assert response.status_code == 403
        assert response.json()["code"] == "NOT_ADMIN"
        assert await _holds(factory) == []
        [refused] = await _audit_rows(factory, vocabulary.ADMIN_ACCESS)
        assert (refused.result, refused.reason_code) == (vocabulary.REFUSED, "NOT_ADMIN")

    @pytest.mark.parametrize(("method", "url"), ROUTES)
    async def test_a_group_admin_is_no_platform_admin_either(self, client, app, factory, data, method, url):
        """lid-a is admin of group team, which grants nothing here."""
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await self._call(client, method, url, headers, body={})

        assert response.status_code == 403
        assert response.json()["code"] == "NOT_ADMIN"

    @pytest.mark.parametrize(("method", "url"), ROUTES)
    async def test_a_deactivated_platform_admin_403(self, client, app, factory, data, method, url):
        async with factory() as db:
            db.add(
                Member(
                    sso_subject="oud-beheerder",
                    email="oud-beheerder@example.nl",
                    platform_role=PlatformRole.ADMIN,
                    status=MemberStatus.DEACTIVATED,
                )
            )
            await db.commit()
        headers = login(client, app, sub="oud-beheerder", email="oud-beheerder@example.nl")

        response = await self._call(client, method, url, headers)

        assert response.status_code == 403
        assert response.json()["code"] == "MEMBER_DEACTIVATED"
        assert await _holds(factory) == []

    @pytest.mark.parametrize("url", [HOLD, END])
    async def test_a_write_without_csrf_403(self, client, app, factory, data, url):
        _as_admin(client, app)

        response = await client.post(url, json={"reason": REASON})

        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_INVALID"
        assert await _holds(factory) == []

    @pytest.mark.parametrize("url", [HOLD, END])
    async def test_a_write_with_a_wrong_csrf_token_403(self, client, app, factory, data, url):
        _as_admin(client, app)

        response = await client.post(url, json={"reason": REASON}, headers={"X-CSRF-Token": "not-the-token"})

        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_INVALID"

    async def test_a_refused_end_leaves_the_hold_open(self, client, app, factory, data):
        await client.post(HOLD, json={"reason": REASON}, headers=_as_admin(client, app))
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.post(END, json={"reason": END_REASON}, headers=headers)

        assert response.status_code == 403
        [hold] = await _holds(factory)
        assert hold.ended_at is None
        assert await _count(factory, AuditLogEntry, action=vocabulary.AUDIT_PURGE_HOLD_ENDED) == 0

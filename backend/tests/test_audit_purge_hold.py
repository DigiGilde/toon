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
import itertools
import re
import time

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

from toon.api.admin.platform import audit as audit_routes
from toon.api.admin.platform import schemas as platform_schemas
from toon.api.errors import ApiError
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


def _fail_audit_of(app, monkeypatch, action: str) -> None:
    """Fails the strict audit write of `action` only, so every other audit row
    (such as a generic refusal row) would still land."""
    write_strict = app.state.audit_log.write_strict

    async def _write_strict(written_action, *args, **kwargs):
        if written_action == action:
            raise RuntimeError("audit log write error")
        await write_strict(written_action, *args, **kwargs)

    monkeypatch.setattr(app.state.audit_log, "write_strict", _write_strict)


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
        monkeypatch.setattr(audit_routes, "violated_constraint", lambda error: "some_other_constraint")

        with pytest.raises(IntegrityError):
            await client.post(HOLD, json={"reason": "incident 2026-092"}, headers=headers)

        assert len(await _holds(factory)) == 1

    async def test_a_failed_audit_write_still_starts_the_hold(self, client, app, factory, data, monkeypatch):
        """A hold only protects: during an incident a broken audit log must not
        leave the next night free to purge. The hold row says who and why."""
        headers = _as_admin(client, app)
        _fail_audit_of(app, monkeypatch, vocabulary.AUDIT_PURGE_HOLD_STARTED)

        response = await client.post(HOLD, json={"reason": REASON}, headers=headers)

        assert response.status_code == 201
        assert response.json()["onHold"] is True
        [hold] = await _holds(factory)
        assert hold.reason == REASON
        assert await _audit_rows(factory, vocabulary.AUDIT_PURGE_HOLD_STARTED) == []

    async def test_without_an_audit_log_the_hold_still_starts(self, client, app, factory, data):
        headers = _as_admin(client, app)
        app.state.audit_log = None

        response = await client.post(HOLD, json={"reason": REASON}, headers=headers)

        assert response.status_code == 201
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

    async def test_a_failed_audit_write_leaves_the_hold_open(self, client, app, factory, data, monkeypatch):
        headers = _as_admin(client, app)
        await client.post(HOLD, json={"reason": REASON}, headers=headers)
        _fail_audit_of(app, monkeypatch, vocabulary.AUDIT_PURGE_HOLD_ENDED)

        response = await client.post(END, json={"reason": END_REASON}, headers=headers)

        assert response.status_code == 503
        assert response.headers["content-type"] == PROBLEM
        assert response.json()["code"] == "AUDIT_UNAVAILABLE"
        [hold] = await _holds(factory)
        assert (hold.ended_at, hold.ended_by, hold.end_reason) == (None, None, None)
        assert (await client.get(HOLD, headers=headers)).json()["onHold"] is True
        assert await _audit_rows(factory, vocabulary.AUDIT_PURGE_HOLD_ENDED) == []
        assert await _audit_rows(factory, vocabulary.ADMIN_ACCESS) == []

        monkeypatch.undo()
        assert (await client.post(END, json={"reason": END_REASON}, headers=headers)).status_code == 200

    async def test_two_admins_at_once_end_it_once(self, client, app, factory, data):
        headers = _as_admin(client, app)
        await client.post(HOLD, json={"reason": REASON}, headers=headers)

        responses = await asyncio.gather(
            client.post(END, json={"reason": END_REASON}, headers=headers),
            client.post(END, json={"reason": "incident 2026-091 closed too"}, headers=headers),
        )

        assert sorted(response.status_code for response in responses) == [200, 409]
        assert len(await _audit_rows(factory, vocabulary.AUDIT_PURGE_HOLD_ENDED)) == 1


EMPTY = (
    "HOLD_REASON_EMPTY",
    "Give a reason, such as a case or ticket number.",
    "Geef een reden, zoals een zaak- of ticketnummer.",
)
TOO_LONG = (
    "HOLD_REASON_TOO_LONG",
    "The reason is longer than 10000 characters. Shorten it, or refer to the case or ticket number.",
    "De reden is langer dan 10000 tekens. Maak hem korter, of verwijs naar het zaak- of ticketnummer.",
)
INVISIBLE = (
    "HOLD_REASON_INVISIBLE_CHARACTER",
    "The reason holds an invisible character, such as a zero-width space. Type the reason again or paste it as "
    "plain text.",
    "De reden bevat een onzichtbaar teken, zoals een spatie zonder breedte. Typ de reden opnieuw of plak hem als "
    "platte tekst.",
)
EMAIL = (
    "HOLD_REASON_EMAIL",
    "The reason holds an e-mail address. Every platform administrator can read the reason in the audit log. Give "
    "a case or ticket number instead.",
    "De reden bevat een e-mailadres. Elke platformbeheerder kan de reden lezen in het auditlog. Noem daarom een "
    "zaak- of ticketnummer.",
)


class TestReason:
    async def _refused(self, client, app, factory, url, reason, language):
        """Posts `reason` to `url` with a hold open for END and none for HOLD,
        and checks that the refusal changed nothing."""
        headers = _as_admin(client, app)
        if url == END:
            await client.post(HOLD, json={"reason": REASON}, headers=headers)

        response = await client.post(url, json={"reason": reason}, headers={**headers, "Accept-Language": language})

        assert response.status_code == 422
        assert response.headers["content-type"] == PROBLEM
        holds = await _holds(factory)
        if url == END:
            assert [(hold.reason, hold.ended_at) for hold in holds] == [(REASON, None)]
        else:
            assert holds == []
        assert await _count(factory, AuditLogEntry, action=vocabulary.AUDIT_PURGE_HOLD_ENDED) == 0
        return response.json()

    @pytest.mark.parametrize("url", [HOLD, END])
    @pytest.mark.parametrize("language", ["en", "nl"])
    @pytest.mark.parametrize(
        ("reason", "refusal"),
        [
            pytest.param("", EMPTY, id="empty"),
            pytest.param(" \r\n\t\n  \n ", EMPTY, id="only-whitespace"),
            pytest.param("x" * 10_001, TOO_LONG, id="too-long"),
            pytest.param("incident\u200b2026-091", INVISIBLE, id="zero-width-space"),
            pytest.param("incident \u202e2026-091", INVISIBLE, id="right-to-left-override"),
            pytest.param("incident\x002026-091", INVISIBLE, id="nul"),
            pytest.param("ask admin@example.nl", EMAIL, id="email"),
            pytest.param("incident 2026-091\ncc piet.de.vries@gemeente.example", EMAIL, id="email-on-a-later-line"),
        ],
    )
    async def test_each_invalid_reason_gets_its_own_code_and_says_what_to_change(
        self, client, app, factory, data, url, language, reason, refusal
    ):
        code, english, dutch = refusal

        body = await self._refused(client, app, factory, url, reason, language)

        assert body["code"] == code
        assert body["detail"] == (dutch if language == "nl" else english)
        assert body["title"] == ("Onverwerkbare invoer" if language == "nl" else "Unprocessable input")

    @pytest.mark.parametrize("url", [HOLD, END])
    async def test_a_missing_reason_is_a_plain_422(self, client, app, factory, data, url):
        headers = _as_admin(client, app)

        response = await client.post(url, json={}, headers=headers)

        assert response.status_code == 422
        assert "code" not in response.json()
        assert response.json()["detail"] == "Invalid input: reason"
        assert await _holds(factory) == []

    async def test_a_tab_becomes_a_space(self, client, app, factory, data):
        headers = _as_admin(client, app)

        response = await client.post(HOLD, json={"reason": "\tincident\t2026-091\t"}, headers=headers)

        assert response.status_code == 201
        assert response.json()["hold"]["reason"] == "incident 2026-091"
        [hold] = await _holds(factory)
        assert hold.reason == "incident 2026-091"
        [row] = await _audit_rows(factory, vocabulary.AUDIT_PURGE_HOLD_STARTED)
        assert row.refs["reason"] == "incident 2026-091"

    async def test_a_tab_in_the_end_reason_becomes_a_space(self, client, app, factory, data):
        headers = _as_admin(client, app)
        await client.post(HOLD, json={"reason": REASON}, headers=headers)

        response = await client.post(END, json={"reason": "incident\t2026-091 closed"}, headers=headers)

        assert response.status_code == 200
        [hold] = await _holds(factory)
        assert hold.end_reason == END_REASON
        [row] = await _audit_rows(factory, vocabulary.AUDIT_PURGE_HOLD_ENDED)
        assert row.refs["reason"] == END_REASON

    def test_the_length_is_checked_before_the_characters(self):
        """A huge text is refused without being scanned character by character."""
        with pytest.raises(ApiError) as refused:
            platform_schemas._validated_hold_reason("\u200b" * (platform_schemas.HOLD_REASON_MAX_LENGTH + 1))

        assert refused.value.code == "HOLD_REASON_TOO_LONG"

    @pytest.mark.parametrize("reason", ["7", "x" * 10_000, "INC-1 @ops-team", "zie @piet", "a@b"])
    async def test_a_short_long_or_mentioning_reason_is_accepted(self, client, app, factory, data, reason):
        headers = _as_admin(client, app)

        response = await client.post(HOLD, json={"reason": reason}, headers=headers)

        assert response.status_code == 201
        assert [hold.reason for hold in await _holds(factory)] == [reason]

    def test_the_longest_reason_without_an_address_is_checked_quickly(self):
        """A pattern like `[^\\s@]+@...` backtracks over the whole rest of the
        text from every start position: about half a second for 10,000
        characters, on the event loop. Twenty checks have to fit in a second."""
        reason = "a" * platform_schemas.HOLD_REASON_MAX_LENGTH
        start = time.perf_counter()

        for _ in range(20):
            assert platform_schemas._validated_hold_reason(reason) == reason

        assert time.perf_counter() - start < 1

    def test_the_address_check_finds_what_the_plain_pattern_finds(self):
        """The linear pattern is a rewrite of this readable one, and finds an
        address in exactly the same texts."""
        plain = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
        for length in range(7):
            for characters in itertools.product("a@. \n", repeat=length):
                text = "".join(characters)
                assert bool(platform_schemas._EMAIL_ADDRESS.search(text)) == bool(plain.search(text)), repr(text)

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

"""The access matrix over HTTP for the feedback widget API.

test_access_gate.py pins what the gate decides for every policy and visitor.
This grid sends feedback through the API on the same policies and visitors,
on the live site and on a preview (inherited and overridden), and holds the
answer to the gate: an item goes in exactly where the gate lets the visitor
see the page, and every other cell is the one FEEDBACK_UNAVAILABLE, byte for
byte. The visitor reaches the API only through what a page token can carry: a
consented content session, the content session of a signed-in visitor who
never consented (for the gate alone, sending with a name or without one), or
the id of the secret link the gate accepted.

Then the gap between a page view and the feedback sent from it: a revoked
key, an invitee removed, a member deactivated, a preview expired, feedback
settings changed, an IdP that ends a session for the gate alone, a rename of
the site or its group and a token used for another site are checked again on
the request itself. Last what a session for the gate alone does not open
(everything that needs feedback with an account) and the write keys it
spends.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from helpers_oidc import MockIdP, make_client_jwk, make_oidc_client
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_access_gate import (
    FEEDBACK_MATRIX,
    OVERRIDE_PAIRS,
    POLICIES,
    VISITOR_NAMES,
    WITH_SESSION,
    World,
    expected,
    make_world,
    policy_id,
    visitors,
)
from test_feedback_api import (
    account_item,
    assert_problem,
    auth,
    author_keys,
    client_for,
    consented_session,
    header_list,
    item,
    make_app,
    make_settings,
    page_token,
    parts,
    rolled_back,
)

from toon.access import keys
from toon.access.decision import DecisionKind
from toon.auth.sessions import Session
from toon.constants import AccessBase, AccessPolicy, FeedbackIdentity, FeedbackKind
from toon.models.feedback import FeedbackItem
from toon.models.identity import Group, Member, MemberStatus
from toon.models.publication import AccessKey, Invitee, Preview, Site, VersionTarget
from toon.serving import feedback_api
from toon.serving.feedback_token import mint_page_token

ITEMS = "/-/feedback/v1/items"
LIVE_ROOT = "/aurora/site/"
PREVIEW_ROOT = "/aurora/site/_preview/pr-42/"

# The visitors who hold a content session, in the order of the grid.
SESSION_NAMES = [name for name in VISITOR_NAMES if name in WITH_SESSION]
# What a signed-in visitor without consent can send: with a name, or without.
WITHOUT_ACCOUNT = [{"kind": "name", "name": "Jan Jansen"}, {"kind": "anonymous"}]


@pytest_asyncio.fixture
async def factory(migrated_dsn: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with rolled_back(migrated_dsn) as made:
        yield made


async def build(
    factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    policy: AccessPolicy,
    *,
    preview_override: AccessPolicy | None = None,
) -> tuple[World, FastAPI]:
    async with factory() as db:
        world = await make_world(db, policy, preview_override=preview_override, feedback=FEEDBACK_MATRIX)
        await db.commit()
    return world, make_app(make_settings(tmp_path), factory)


async def _key_id_of(factory: async_sessionmaker[AsyncSession], plain: str) -> str:
    async with factory() as db:
        return str(await db.scalar(select(AccessKey.id).where(AccessKey.selector == plain.split(".")[0])))


async def token_for(
    factory: async_sessionmaker[AsyncSession], app: FastAPI, world: World, name: str, *, preview: bool
) -> str:
    """The page token a page view would have handed this visitor: their
    content session (consented on this site) when they have one, and the
    secret link they hold as a key id. A link the gate never accepted (a
    wrong code) leaves no key id for a token to carry."""
    visitor = visitors(world)[name]
    session_id = None
    if name in WITH_SESSION:
        session_id = consented_session(
            app,
            world.site_id,
            sub=visitor.sub,
            email=visitor.email,
            email_verified=visitor.email_verified,
            name=None,
        ).id
    key_id = {
        "key_query": str(world.key_id),
        "key_cookie": str(world.key_id),
        "key_cookie_revoked": str(world.revoked_key_id),
        "key_query_expired": await _key_id_of(factory, world.expired_key_plain),
        "key_query_other_site": await _key_id_of(factory, world.other_site_key_plain),
    }.get(name)
    return page_token(
        app,
        world.site_id,
        ref=world.preview_ref if preview else None,
        version_id=world.preview_version_id if preview else world.live_version_id,
        session_id=session_id,
        key_id=key_id,
    )


def unconsented_session(app: FastAPI, world: World, name: str) -> Session:
    """The content session of a signed-in visitor of the grid who never
    consented to feedback with their account."""
    visitor = visitors(world)[name]
    return consented_session(
        app, sub=visitor.sub, email=visitor.email, email_verified=visitor.email_verified, name=None
    )


def gate_token(app: FastAPI, world: World, session_id: str, *, preview: bool, site_id: uuid.UUID | None = None) -> str:
    """The page token a page view hands that visitor where their session let
    them in: the session for the gate alone, nothing else."""
    return mint_page_token(
        app.state.settings.session_secret,
        site_id=world.site_id if site_id is None else site_id,
        kind="preview" if preview else "live",
        ref=world.preview_ref if preview else None,
        version_id=world.preview_version_id if preview else world.live_version_id,
        session_id=None,
        access_session_id=session_id,
        key_id=None,
        now=int(time.time()),
    )


async def send(
    client: httpx.AsyncClient,
    token: str,
    *,
    preview: bool,
    author: dict[str, object] | None = None,
    **headers: str,
) -> httpx.Response:
    value = item(page={"path": PREVIEW_ROOT if preview else LIVE_ROOT, "title": None})
    if author is not None:
        value["author"] = author
    return await client.post(ITEMS, files=parts(value), headers=auth(token, **headers))


async def assert_cell(
    client: httpx.AsyncClient, reference: httpx.Response, response: httpx.Response, allowed: bool, cell: object
) -> None:
    if allowed:
        assert response.status_code == 201, (cell, response.text)
    else:
        assert response.status_code == 404, (cell, response.text)
        assert response.content == reference.content, cell
        assert header_list(response) == header_list(reference), cell


@pytest.mark.parametrize("name", VISITOR_NAMES)
@pytest.mark.parametrize("policy", POLICIES, ids=policy_id)
async def test_matrix_live_and_preview(factory, tmp_path, policy, name):
    world, app = await build(factory, tmp_path, policy)
    async with client_for(app) as client:
        reference = await client.post(ITEMS, files=parts())
        for preview in (False, True):
            allowed = expected(policy, name, preview=preview)[0] is DecisionKind.ALLOW
            token = await token_for(factory, app, world, name, preview=preview)
            response = await send(client, token, preview=preview)
            await assert_cell(client, reference, response, allowed, ("preview" if preview else "live", name))


@pytest.mark.parametrize("name", VISITOR_NAMES)
@pytest.mark.parametrize(("site_policy", "override"), OVERRIDE_PAIRS, ids=repr)
async def test_matrix_preview_override(factory, tmp_path, site_policy, override, name):
    """The override decides who may look at a preview, and with that who may
    give feedback on it."""
    world, app = await build(factory, tmp_path, site_policy, preview_override=override)
    async with client_for(app) as client:
        reference = await client.post(ITEMS, files=parts())
        allowed = expected(override, name, preview=True)[0] is DecisionKind.ALLOW
        token = await token_for(factory, app, world, name, preview=True)
        await assert_cell(client, reference, await send(client, token, preview=True), allowed, name)


async def assert_nothing_of_a_session_stored(factory: async_sessionmaker[AsyncSession]) -> None:
    for row in await stored(factory):
        assert (row.author_sub, row.author_email) == (None, None), row.author_kind
        assert row.author_kind is not FeedbackIdentity.ACCOUNT


@pytest.mark.parametrize("name", SESSION_NAMES)
@pytest.mark.parametrize("policy", POLICIES, ids=policy_id)
async def test_matrix_without_consent_live_and_preview(factory, tmp_path, policy, name):
    """A signed-in visitor who never consented sends with a name and without
    one, on a token that carries their session for the gate alone. A page
    view hands that token out only where the session let them in. Here every
    policy gets it, so it shows that the session opens exactly what the gate
    opens to its visitor and nothing more."""
    world, app = await build(factory, tmp_path, policy)
    session = unconsented_session(app, world, name)
    async with client_for(app) as client:
        reference = await client.post(ITEMS, files=parts())
        for preview in (False, True):
            allowed = expected(policy, name, preview=preview)[0] is DecisionKind.ALLOW
            token = gate_token(app, world, session.id, preview=preview)
            for author in WITHOUT_ACCOUNT:
                response = await send(client, token, preview=preview, author=author)
                cell = ("preview" if preview else "live", name, author["kind"])
                await assert_cell(client, reference, response, allowed, cell)
    await assert_nothing_of_a_session_stored(factory)


@pytest.mark.parametrize("name", SESSION_NAMES)
@pytest.mark.parametrize(("site_policy", "override"), OVERRIDE_PAIRS, ids=repr)
async def test_matrix_without_consent_preview_override(factory, tmp_path, site_policy, override, name):
    world, app = await build(factory, tmp_path, site_policy, preview_override=override)
    session = unconsented_session(app, world, name)
    async with client_for(app) as client:
        reference = await client.post(ITEMS, files=parts())
        allowed = expected(override, name, preview=True)[0] is DecisionKind.ALLOW
        token = gate_token(app, world, session.id, preview=True)
        for author in WITHOUT_ACCOUNT:
            response = await send(client, token, preview=True, author=author)
            await assert_cell(client, reference, response, allowed, (name, author["kind"]))
    await assert_nothing_of_a_session_stored(factory)


# --- The gap between the page view and the feedback ----------------------------


async def stored(factory: async_sessionmaker[AsyncSession]) -> list[FeedbackItem]:
    async with factory() as db:
        return list((await db.scalars(select(FeedbackItem))).all())


async def _revoke_key(db: AsyncSession, world: World) -> None:
    await keys.revoke(db, world.key_id)


async def _remove_invitee(db: AsyncSession, world: World) -> None:
    await db.execute(delete(Invitee).where(Invitee.identifier == "genodigde-sub"))


async def _deactivate_member(db: AsyncSession, world: World) -> None:
    deactivated = update(Member).where(Member.sso_subject == "lid-actief").values(status=MemberStatus.DEACTIVATED)
    await db.execute(deactivated)


async def _expire_preview(db: AsyncSession, world: World) -> None:
    expired = datetime.now(UTC) - timedelta(minutes=1)
    await db.execute(update(Preview).where(Preview.site_id == world.site_id).values(expires_at=expired))


def _set_site(**values: object):
    async def change(db: AsyncSession, world: World) -> None:
        await db.execute(update(Site).where(Site.id == world.site_id).values(**values))

    return change


def _gap_token(app: FastAPI, world: World, holder: str | None, *, preview: bool) -> str:
    """A page token for `holder`: the secret link ("key"), a session for the
    gate alone ("gate:<sub>"), a consented session (a sub), or nobody."""
    ref = world.preview_ref if preview else None
    if holder == "key":
        return page_token(app, world.site_id, ref=ref, key_id=str(world.key_id))
    if holder is None:
        return page_token(app, world.site_id, ref=ref)
    sub = holder.removeprefix("gate:")
    email = None if sub == "genodigde-sub" else f"{sub}@example.org"
    if holder.startswith("gate:"):
        return gate_token(app, world, consented_session(app, sub=sub, email=email).id, preview=preview)
    session = consented_session(app, world.site_id, sub=sub, email=email)
    return page_token(app, world.site_id, ref=ref, session_id=session.id)


# (policy, holder, preview, change, refused afterwards)
GAP_CHANGES = [
    pytest.param(AccessPolicy(AccessBase.NOBODY, keys=True), "key", False, _revoke_key, True, id="key-revoked"),
    pytest.param(
        AccessPolicy(AccessBase.NOBODY, invitees=True),
        "genodigde-sub",
        False,
        _remove_invitee,
        True,
        id="invitee-removed",
    ),
    pytest.param(
        AccessPolicy(AccessBase.SITE_TEAM), "lid-actief", False, _deactivate_member, True, id="member-deactivated"
    ),
    pytest.param(
        AccessPolicy(AccessBase.SITE_TEAM),
        "gate:lid-actief",
        False,
        _deactivate_member,
        True,
        id="member-deactivated-for-the-gate-alone",
    ),
    pytest.param(AccessPolicy(AccessBase.PUBLIC), None, True, _expire_preview, True, id="preview-expired"),
    pytest.param(
        AccessPolicy(AccessBase.PUBLIC),
        None,
        True,
        _set_site(feedback_live=False),
        False,
        id="previews-only-keeps-the-preview",
    ),
    pytest.param(
        AccessPolicy(AccessBase.PUBLIC),
        None,
        True,
        _set_site(feedback_enabled=False),
        True,
        id="feedback-off-on-a-preview",
    ),
    pytest.param(
        AccessPolicy(AccessBase.PUBLIC),
        "kijker",
        False,
        _set_site(sandbox=False),
        False,
        id="unshielded-keeps-the-consent",
    ),
]


class TestTheGap:
    """Each case first proves the token works, then changes one thing, and
    sends the same token again."""

    async def _accepted_then(self, client: httpx.AsyncClient, token: str, *, preview: bool = False) -> None:
        assert (await send(client, token, preview=preview)).status_code == 201

    async def _refused(self, client: httpx.AsyncClient, token: str, *, preview: bool = False) -> None:
        assert_problem(await send(client, token, preview=preview), 404, "FEEDBACK_UNAVAILABLE")

    @pytest.mark.parametrize(("policy", "holder", "preview", "change", "refused"), GAP_CHANGES)
    async def test_a_change_after_the_page_view(self, factory, tmp_path, policy, holder, preview, change, refused):
        world, app = await build(factory, tmp_path, policy)
        token = _gap_token(app, world, holder, preview=preview)
        async with client_for(app) as client:
            await self._accepted_then(client, token, preview=preview)
            async with factory() as db:
                await change(db, world)
                await db.commit()
            if refused:
                await self._refused(client, token, preview=preview)
            else:
                await self._accepted_then(client, token, preview=preview)

    async def test_the_idp_ended_the_session_for_the_gate_alone(self, factory, tmp_path):
        """The token carries no cookie the middleware could see, so the API
        asks the IdP itself."""
        idp = MockIdP()
        world, _ = await build(factory, tmp_path, AccessPolicy(AccessBase.SSO))
        settings = make_settings(
            tmp_path, oidc_issuer=idp.issuer, oidc_client_id=idp.client_id, oidc_client_private_jwk=make_client_jwk()
        )
        app = make_app(settings, factory)
        app.state.oidc_client = make_oidc_client(settings, idp)
        session = consented_session(app, sub="kijker", refresh_token="ververstoken-1")
        token = gate_token(app, world, session.id, preview=False)
        async with client_for(app) as client:
            await self._accepted_then(client, token)
            idp.refresh_error = "invalid_grant"
            idp.refresh_status = 400
            store = app.state.session_store
            store._sessions[session.id] = replace(
                store.get_session(session.id), checked_at=datetime.now(UTC) - timedelta(hours=1)
            )
            await self._refused(client, token)
        assert store.get_session(session.id) is None
        assert len(idp.refresh_requests) == 1

    @pytest.mark.parametrize("renamed", ["site", "group"])
    @pytest.mark.parametrize("preview", [False, True], ids=["live", "preview"])
    async def test_a_rename(self, factory, tmp_path, renamed, preview):
        """The token names the site by its id, so it holds: the page still at
        the old address is told the site has moved, the same token sends from
        the address the site is at now, where the gate decides again, and a
        reply, which names no page, goes through as before."""
        world, app = await build(factory, tmp_path, AccessPolicy(AccessBase.PUBLIC))
        session = consented_session(app, world.site_id, sub="kijker")
        token = page_token(app, world.site_id, ref=world.preview_ref if preview else None, session_id=session.id)
        own = await account_item(factory, world.site_id, sub="kijker")
        old = PREVIEW_ROOT if preview else LIVE_ROOT
        new = old.replace(LIVE_ROOT, "/aurora/nieuw/" if renamed == "site" else "/borealis/site/")
        async with client_for(app) as client:
            await self._accepted_then(client, token, preview=preview)
            async with factory() as db:
                if renamed == "site":
                    (await db.scalar(select(Site).where(Site.id == world.site_id))).slug = "nieuw"
                else:
                    (await db.scalar(select(Group).where(Group.id == world.group_id))).slug = "borealis"
                await db.commit()
            for language in ("en", "nl"):
                moved = await send(client, token, preview=preview, **{"Accept-Language": language})
                assert_problem(moved, 422, "FEEDBACK_PAGE_MOVED", locale=language)
            value = item(page={"path": f"{new}docs/", "title": None})
            assert (await client.post(ITEMS, files=parts(value), headers=auth(token))).status_code == 201
            replied = await client.post(f"{ITEMS}/{own}/replies", json={"body": "Nog iets"}, headers=auth(token))
            assert replied.status_code == 201, replied.text
        assert sorted(row.page_path for row in await stored(factory)) == ["", "docs/", "docs/"]

    async def test_a_token_of_another_site(self, factory, tmp_path):
        """A token is bound to its site: it neither posts onto another site's
        pages nor reaches another site's items, not even its own giver's. A
        path outside its site is taken for a page of its own site at an old
        address."""
        world, app = await build(factory, tmp_path, AccessPolicy(AccessBase.PUBLIC))
        async with factory() as db:
            await db.execute(
                update(Site)
                .where(Site.id == world.other_site_id)
                .values(access_base=AccessBase.PUBLIC, feedback_enabled=True, feedback_live=True)
            )
            elsewhere = FeedbackItem(
                site_id=world.other_site_id,
                target=VersionTarget.LIVE,
                page_path="",
                kind=FeedbackKind.PAGE,
                comment="Op de andere site",
                author_kind=FeedbackIdentity.ACCOUNT,
                author_sub="kijker",
            )
            db.add(elsewhere)
            await db.commit()
        session = consented_session(app, world.site_id, world.other_site_id, sub="kijker")
        token = page_token(app, world.site_id, session_id=session.id)
        async with client_for(app) as client:
            value = item(page={"path": "/aurora/ander/", "title": None})
            moved = await client.post(ITEMS, files=parts(value), headers=auth(token))
            assert_problem(moved, 422, "FEEDBACK_PAGE_MOVED")
            replied = await client.post(f"{ITEMS}/{elsewhere.id}/replies", json={"body": "x"}, headers=auth(token))
            assert_problem(replied, 404, "FEEDBACK_UNAVAILABLE")
            deleted = await client.delete(f"{ITEMS}/{elsewhere.id}", headers=auth(token))
            assert_problem(deleted, 404, "FEEDBACK_UNAVAILABLE")
            mine = await client.get(ITEMS, headers=auth(token))
            assert mine.json() == {"items": [], "more": False}
        assert len(await stored(factory)) == 1


# (what each send carries, the status each gets, the write keys spent)
WRITE_KEYS = [
    pytest.param(
        ["public", "public", "gate:kijker", "gate:kijker"],
        [201, 429, 201, 429],
        {"site:{other}:ip:127.0.0.1", "site:{site}:gate:kijker"},
        id="by-their-sub-not-their-address",
    ),
    pytest.param(
        ["gate:collega-1", "gate:collega-2"],
        [201, 201],
        {"site:{site}:gate:collega-1", "site:{site}:gate:collega-2"},
        id="colleagues-behind-one-address-a-budget-each",
    ),
    pytest.param(["gate:kijker", "gate:kijker"], [201, 429], {"site:{site}:gate:kijker"}, id="signing-in-again"),
    pytest.param(
        ["gate:kijker", "session:kijker", "gate:kijker", "session:kijker"],
        [201, 201, 429, 429],
        {"site:{site}:gate:kijker", "site:{site}:session:kijker"},
        id="the-gate-and-the-consent-of-one-person-count-apart",
    ),
]


class TestTheSessionForTheGateAlone:
    """A session that only let the visitor in serves the gate and nothing
    after it: everything that needs feedback with an account stays behind
    consent. Its sub only keys the write counter in memory, on that site, so
    colleagues behind one address do not share a budget, and under a
    namespace of its own, apart from the count of the person's consented
    session: a site whose team sees the person's name through their consented
    items could otherwise read from a shared count how many items that person
    sent without it."""

    async def test_nothing_that_needs_an_account(self, factory, tmp_path):
        world, app = await build(factory, tmp_path, AccessPolicy(AccessBase.SSO))
        session = consented_session(app, sub="kijker")
        token = gate_token(app, world, session.id, preview=False)
        own = await account_item(factory, world.site_id, sub="kijker")
        async with client_for(app) as client:
            as_account = await send(client, token, preview=False, author={"kind": "account"})
            assert_problem(as_account, 403, "FEEDBACK_ACCOUNT_REQUIRED")
            assert_problem(await client.get(ITEMS, headers=auth(token)), 403, "FEEDBACK_ACCOUNT_REQUIRED")
            replied = await client.post(f"{ITEMS}/{own}/replies", json={"body": "Nog iets"}, headers=auth(token))
            assert_problem(replied, 403, "FEEDBACK_ACCOUNT_REQUIRED")
            deleted = await client.delete(f"{ITEMS}/{own}", headers=auth(token))
            assert_problem(deleted, 403, "FEEDBACK_ACCOUNT_REQUIRED")
        assert len(await stored(factory)) == 1

    @pytest.mark.parametrize(("sends", "statuses", "spent"), WRITE_KEYS)
    async def test_the_write_keys(self, factory, tmp_path, monkeypatch, sends, statuses, spent):
        """Every "gate:" and "session:" send opens a new session for its
        sub. Each "public" send goes without a session to another site, from
        the same address."""
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_AUTHOR", 1)
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_ADDRESS", 1)
        world, app = await build(factory, tmp_path, AccessPolicy(AccessBase.SSO))
        async with factory() as db:
            await db.execute(
                update(Site)
                .where(Site.id == world.other_site_id)
                .values(
                    access_base=AccessBase.PUBLIC,
                    feedback_enabled=True,
                    feedback_identity=FeedbackIdentity.ANONYMOUS,
                    feedback_live=True,
                )
            )
            await db.commit()
        elsewhere = item(page={"path": "/aurora/ander/", "title": None})
        got = []
        async with client_for(app) as client:
            for carried in sends:
                role, _, sub = carried.partition(":")
                if role == "public":
                    token, value = page_token(app, world.other_site_id), elsewhere
                elif role == "gate":
                    token, value = gate_token(app, world, consented_session(app, sub=sub).id, preview=False), item()
                else:
                    token = page_token(app, world.site_id, session_id=consented_session(app, world.site_id, sub=sub).id)
                    value = item()
                response = await client.post(ITEMS, files=parts(value), headers=auth(token))
                got.append(response.status_code)
                if response.status_code == 429:
                    assert_problem(response, 429, "TOO_MANY_REQUESTS.feedback")
        assert got == statuses
        assert author_keys(app) == {key.format(site=world.site_id, other=world.other_site_id) for key in spent}
        await assert_nothing_of_a_session_stored(factory)

    @pytest.mark.parametrize("ground", ["gone", "admin-session", "unknown"])
    async def test_a_session_that_does_not_hold(self, factory, tmp_path, ground):
        world, app = await build(factory, tmp_path, AccessPolicy(AccessBase.PUBLIC))
        store = app.state.session_store
        if ground == "admin-session":
            session_id = store.create_session(sub="kijker", email=None, email_verified=False, acr="urn:acr:hoog").id
        elif ground == "gone":
            session_id = consented_session(app, sub="kijker").id
            store.delete_session(session_id)
        else:
            session_id = "onbekende-sessie"
        token = gate_token(app, world, session_id, preview=False)
        async with client_for(app) as client:
            # Even on a public site: the token names a session, and none stands behind it.
            assert_problem(await send(client, token, preview=False), 404, "FEEDBACK_UNAVAILABLE")

"""The admin API for notifications of new feedback: a member's email choice at
three levels, and the Mattermost webhook of a site or a group.

Refusals come first: the role matrix per level, CSRF, no session, an
installation without email or without webhooks. Then what the routes do: the
resolution of the three levels, what is stored and audited, and the one rule
that holds everywhere for a webhook: its URL goes in and never comes out, not
in an answer, not in a refusal and not in the audit log.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import pytest
import pytest_asyncio
from fastapi import FastAPI
from helpers_oidc import APP_BASE_URL, CONTENT_BASE_URL, make_test_client, set_session_cookie
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from toon import messages
from toon.api.admin import make_admin_router
from toon.api.deploys import BearerOutsideDeploysMiddleware
from toon.api.errors import register_error_handlers
from toon.audit.log import AuditLog
from toon.auth.sessions import CSRF_COOKIE, CSRF_HEADER, SessionStore
from toon.config import Settings
from toon.constants import AccessBase, Role
from toon.db import make_session_factory
from toon.ingest.store import ContentStore
from toon.models.audit import AuditLogEntry
from toon.models.identity import Group, GroupMember, Member, MemberLanguage, MemberStatus, PlatformRole, SiteMember
from toon.models.notifications import FeedbackWebhook, GroupFeedbackEmail, SiteFeedbackEmail
from toon.models.publication import Site

BASE = "/-/api/v1"
PROBLEM = "application/problem+json"
PEPPER = "audit-pepper-van-minstens-32-bytes!!"
HOSTS = "mattermost.example.org,chat.example.org:8443"
HIDDEN_PATH = "verborgen-hookpad"
URL = f"https://mattermost.example.org/hooks/{HIDDEN_PATH}"
OTHER_URL = f"https://chat.example.org:8443/hooks/{HIDDEN_PATH}2"

EMAIL_ROUTES = {
    "profile": f"{BASE}/me/feedback-email",
    "group": f"{BASE}/groups/team/feedback-email",
    "site": f"{BASE}/sites/team/docs/feedback-email",
}
WEBHOOK_ROUTES = {
    "group": f"{BASE}/groups/team/feedback-webhook",
    "site": f"{BASE}/sites/team/docs/feedback-webhook",
}
WEBHOOK_AUDIT = {"group": "group_feedback_webhook", "site": "site_feedback_webhook"}
CALLERS = ("admin", "editor", "reader", "site_reader", "site_admin", "outsider", "platform_admin")


def _settings(content_root, *, email: bool, hosts: str) -> Settings:
    smtp = {"smtp_host": "smtp.example.org", "smtp_from": "toon@example.org"} if email else {}
    return Settings(
        db_url="postgresql+asyncpg://toon:toon@localhost:5432/toon",
        content_root=content_root,
        oidc_issuer="https://idp.example",
        oidc_client_id="toon-client",
        oidc_client_private_jwk="{}",
        oidc_required_acr="urn:acr:hoog",
        session_secret="sessie-geheim-van-minstens-32-bytes!",
        audit_pepper=PEPPER,
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        base_url=APP_BASE_URL,
        content_base_url=CONTENT_BASE_URL,
        environment="dev",
        feedback_webhook_hosts=hosts,
        **smtp,
    )


def _build_app(factory, tmp_path, *, email: bool, hosts: str) -> FastAPI:
    content_root = tmp_path / "content"
    content_root.mkdir(exist_ok=True)
    settings = _settings(content_root, email=email, hosts=hosts)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_store = SessionStore()
    app.state.session_factory = factory
    app.state.content_store = ContentStore(content_root)
    app.state.audit_log = AuditLog(factory, settings.audit_pepper, settings.audit_ip_key_bytes)
    register_error_handlers(app)
    app.add_middleware(BearerOutsideDeploysMiddleware)
    app.include_router(make_admin_router())
    return app


@pytest_asyncio.fixture
async def factory(migrated_dsn: str):
    engine = create_async_engine(migrated_dsn, poolclass=NullPool)
    try:
        yield make_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def app(factory, tmp_path) -> FastAPI:
    """An installation that sends email and allows webhooks."""
    return _build_app(factory, tmp_path, email=True, hosts=HOSTS)


@pytest.fixture
def bare_app(factory, tmp_path) -> FastAPI:
    """An installation without SMTP and without webhook hosts."""
    return _build_app(factory, tmp_path, email=False, hosts="")


@pytest_asyncio.fixture
async def client(app):
    async with make_test_client(app) as client:
        yield client


@pytest_asyncio.fixture
async def bare_client(bare_app):
    async with make_test_client(bare_app) as client:
        yield client


@dataclass(frozen=True)
class World:
    group: Group
    site: Site
    admin: Member
    editor: Member
    reader: Member
    site_reader: Member
    site_admin: Member
    outsider: Member
    platform_admin: Member


@pytest_asyncio.fixture
async def world(factory) -> World:
    """Group 'team' with site 'docs': a group admin, editor and reader, a
    reader and an admin of 'docs' alone, someone without a role and a
    platform admin without one."""
    async with factory() as db:
        members = {
            sub: Member(
                sso_subject=sub,
                email=f"{sub}@example.nl",
                status=MemberStatus.ACTIVE,
                platform_role=PlatformRole.ADMIN if sub == "platformbeheer" else PlatformRole.MEMBER,
            )
            for sub in (
                "beheerder",
                "redacteur",
                "lezer",
                "sitelezer",
                "sitebeheerder",
                "buitenstaander",
                "platformbeheer",
            )
        }
        group = Group(slug="team", name="Team", default_access_base=AccessBase.SITE_TEAM)
        db.add_all([group, *members.values()])
        await db.flush()
        site = Site(group_id=group.id, slug="docs", title="Docs", access_base=AccessBase.SITE_TEAM)
        db.add(site)
        await db.flush()
        for sub, role in (("beheerder", Role.ADMIN), ("redacteur", Role.EDITOR), ("lezer", Role.READER)):
            db.add(GroupMember(group_id=group.id, member_id=members[sub].id, role=role))
        db.add(SiteMember(site_id=site.id, member_id=members["sitelezer"].id, role=Role.READER))
        db.add(SiteMember(site_id=site.id, member_id=members["sitebeheerder"].id, role=Role.ADMIN))
        await db.commit()
    return World(
        group=group,
        site=site,
        admin=members["beheerder"],
        editor=members["redacteur"],
        reader=members["lezer"],
        site_reader=members["sitelezer"],
        site_admin=members["sitebeheerder"],
        outsider=members["buitenstaander"],
        platform_admin=members["platformbeheer"],
    )


def _login(client, app, member: Member) -> dict[str, str]:
    """An admin session for `member`, and the CSRF header for mutations."""
    session = set_session_cookie(client, app, sub=member.sso_subject, email=member.email)
    client.cookies.set(CSRF_COOKIE, session.csrf_token, domain="toon.example", path="/")
    return {CSRF_HEADER: session.csrf_token}


def _problem(response, status: int, code: str | None) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith(PROBLEM)
    body = response.json()
    assert body.get("code") == code
    return body


async def _rows(factory, action: str) -> list[AuditLogEntry]:
    async with factory() as db:
        return list(
            await db.scalars(
                select(AuditLogEntry).where(AuditLogEntry.action == action).order_by(AuditLogEntry.occurred_at)
            )
        )


async def _count(factory, model) -> int:
    async with factory() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def _hooks(factory) -> list[FeedbackWebhook]:
    async with factory() as db:
        return list(await db.scalars(select(FeedbackWebhook)))


async def _set_profile(factory, member: Member, value: bool | None) -> None:
    async with factory() as db:
        (await db.get(Member, member.id)).feedback_email = value
        await db.commit()


async def _set_language(factory, member: Member, language: MemberLanguage | None) -> None:
    async with factory() as db:
        (await db.get(Member, member.id)).language = language
        await db.commit()


async def _put_choice(factory, world: World, member: Member, *, site: bool | None, group: bool | None) -> None:
    async with factory() as db:
        if site is not None:
            db.add(SiteFeedbackEmail(site_id=world.site.id, member_id=member.id, enabled=site))
        if group is not None:
            db.add(GroupFeedbackEmail(group_id=world.group.id, member_id=member.id, enabled=group))
        await db.commit()


async def _put_hook(factory, world: World, *, level: str, url: str = URL, host: str = "mattermost.example.org") -> None:
    async with factory() as db:
        owner = {"group_id": world.group.id} if level == "group" else {"site_id": world.site.id}
        db.add(FeedbackWebhook(id=uuid.uuid4(), url=url, host=host, language=MemberLanguage.NL, **owner))
        await db.commit()


# -- Email: who may call -----------------------------------------------------


def _email_expectation(level: str, caller: str) -> tuple[int, str | None]:
    if level == "profile":
        return 200, None
    if level == "group":
        return (200, None) if caller in ("admin", "editor", "reader") else (403, "INSUFFICIENT_ROLE")
    if caller == "outsider":
        return 404, "UNKNOWN_SITE"
    if caller == "platform_admin":
        return 403, "INSUFFICIENT_ROLE"
    return 200, None


@pytest.mark.parametrize("level", ["profile", "group", "site"])
@pytest.mark.parametrize("caller", CALLERS)
async def test_the_email_role_matrix_on_a_read(client, app, world, level, caller):
    _login(client, app, getattr(world, caller))
    response = await client.get(EMAIL_ROUTES[level])
    status, code = _email_expectation(level, caller)
    if code:
        _problem(response, status, code)
    else:
        assert response.status_code == 200, response.text


@pytest.mark.parametrize("level", ["profile", "group", "site"])
@pytest.mark.parametrize("caller", CALLERS)
async def test_the_email_role_matrix_on_a_write(client, app, factory, world, level, caller):
    headers = _login(client, app, getattr(world, caller))
    response = await client.put(EMAIL_ROUTES[level], json={"enabled": True}, headers=headers)
    status, code = _email_expectation(level, caller)
    if code:
        _problem(response, status, code)
        assert await _rows(factory, "feedback_email") == []
    else:
        assert response.status_code == 200, response.text
        assert len(await _rows(factory, "feedback_email")) == 1


async def test_a_site_only_reader_sets_the_site_but_not_the_group(client, app, factory, world):
    headers = _login(client, app, world.site_reader)
    assert (await client.put(EMAIL_ROUTES["site"], json={"enabled": True}, headers=headers)).status_code == 200
    _problem(await client.put(EMAIL_ROUTES["group"], json={"enabled": True}, headers=headers), 403, "INSUFFICIENT_ROLE")
    assert await _count(factory, GroupFeedbackEmail) == 0
    assert await _count(factory, SiteFeedbackEmail) == 1


@pytest.mark.parametrize("level", ["profile", "group", "site"])
async def test_an_email_write_without_the_csrf_header_is_refused(client, app, factory, world, level):
    _login(client, app, world.admin)
    response = await client.put(EMAIL_ROUTES[level], json={"enabled": True})
    _problem(response, 403, "CSRF_INVALID")
    assert await _rows(factory, "feedback_email") == []
    assert await _count(factory, SiteFeedbackEmail) == 0


@pytest.mark.parametrize("level", ["profile", "group", "site"])
@pytest.mark.parametrize("method", ["get", "put"])
async def test_email_without_a_session_is_401(client, world, level, method):
    response = await getattr(client, method)(
        EMAIL_ROUTES[level], **({"json": {"enabled": True}} if method == "put" else {})
    )
    _problem(response, 401, "NO_SESSION")


@pytest.mark.parametrize("level", ["group", "site"])
async def test_an_email_choice_on_an_unknown_group_or_site_is_404(client, app, world, level):
    _login(client, app, world.admin)
    route = (
        EMAIL_ROUTES[level].replace("team", "bestaat-niet")
        if level == "group"
        else EMAIL_ROUTES[level].replace("docs", "bestaat-niet")
    )
    response = await client.get(route)
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("level", ["profile", "group", "site"])
async def test_an_installation_without_email_reads_but_refuses_a_write(bare_client, bare_app, factory, world, level):
    headers = _login(bare_client, bare_app, world.admin)
    read = await bare_client.get(EMAIL_ROUTES[level])
    assert read.status_code == 200
    assert read.json() == {"available": False, "enabled": None, "effective": False, "source": "default"}
    _problem(
        await bare_client.put(EMAIL_ROUTES[level], json={"enabled": True}, headers=headers),
        409,
        "FEEDBACK_EMAIL_UNAVAILABLE",
    )
    assert await _rows(factory, "feedback_email") == []
    assert await _count(factory, SiteFeedbackEmail) == 0
    assert await _count(factory, GroupFeedbackEmail) == 0


async def test_an_installation_without_email_does_not_touch_the_profile(bare_client, bare_app, factory, world):
    headers = _login(bare_client, bare_app, world.admin)
    await bare_client.put(EMAIL_ROUTES["profile"], json={"enabled": True}, headers=headers)
    async with factory() as db:
        assert (await db.get(Member, world.admin.id)).feedback_email is None


@pytest.mark.parametrize("level", ["profile", "group", "site"])
@pytest.mark.parametrize("body", [{"enabled": True, "extra": 1}, {}, {"enabled": "ja"}])
async def test_an_email_body_outside_the_contract_is_422(client, app, factory, world, level, body):
    headers = _login(client, app, world.admin)
    response = await client.put(EMAIL_ROUTES[level], json=body, headers=headers)
    assert response.status_code == 422, response.text
    assert await _rows(factory, "feedback_email") == []


# -- Email: what the levels decide -------------------------------------------


@pytest.mark.parametrize(
    ("site", "group", "profile", "effective", "source"),
    [
        (None, None, None, False, "default"),
        (None, None, True, True, "profile"),
        (None, None, False, False, "profile"),
        (None, True, None, True, "group"),
        (None, True, False, True, "group"),
        (None, False, True, False, "group"),
        (True, None, None, True, "site"),
        (True, False, False, True, "site"),
        (False, True, True, False, "site"),
        (False, None, True, False, "site"),
    ],
)
async def test_the_site_level_resolves_the_narrowest_choice(
    client, app, factory, world, site, group, profile, effective, source
):
    await _put_choice(factory, world, world.reader, site=site, group=group)
    await _set_profile(factory, world.reader, profile)
    _login(client, app, world.reader)
    response = await client.get(EMAIL_ROUTES["site"])
    assert response.json() == {"available": True, "enabled": site, "effective": effective, "source": source}


@pytest.mark.parametrize(
    ("group", "profile", "effective", "source"),
    [
        (None, None, False, "default"),
        (None, True, True, "profile"),
        (True, False, True, "group"),
        (False, True, False, "group"),
    ],
)
async def test_the_group_level_ignores_the_site_and_resolves_the_rest(
    client, app, factory, world, group, profile, effective, source
):
    await _put_choice(factory, world, world.reader, site=not effective, group=group)
    await _set_profile(factory, world.reader, profile)
    _login(client, app, world.reader)
    response = await client.get(EMAIL_ROUTES["group"])
    assert response.json() == {"available": True, "enabled": group, "effective": effective, "source": source}


@pytest.mark.parametrize(
    ("profile", "effective", "source"), [(None, False, "default"), (True, True, "profile"), (False, False, "profile")]
)
async def test_the_profile_level_resolves_itself(client, app, factory, world, profile, effective, source):
    await _put_choice(factory, world, world.reader, site=True, group=True)
    await _set_profile(factory, world.reader, profile)
    _login(client, app, world.reader)
    response = await client.get(EMAIL_ROUTES["profile"])
    assert response.json() == {"available": True, "enabled": profile, "effective": effective, "source": source}


async def test_another_members_choice_does_not_count(client, app, factory, world):
    await _put_choice(factory, world, world.editor, site=True, group=True)
    _login(client, app, world.reader)
    response = await client.get(EMAIL_ROUTES["site"])
    assert response.json()["source"] == "default"


async def test_a_site_off_beats_a_group_on_when_written_through_the_api(client, app, factory, world):
    headers = _login(client, app, world.reader)
    await client.put(EMAIL_ROUTES["group"], json={"enabled": True}, headers=headers)
    response = await client.put(EMAIL_ROUTES["site"], json={"enabled": False}, headers=headers)
    assert response.json() == {"available": True, "enabled": False, "effective": False, "source": "site"}
    group = await client.get(EMAIL_ROUTES["group"])
    assert group.json() == {"available": True, "enabled": True, "effective": True, "source": "group"}


async def test_a_group_on_with_the_site_unset_applies_to_the_site(client, app, world):
    headers = _login(client, app, world.reader)
    response = await client.put(EMAIL_ROUTES["group"], json={"enabled": True}, headers=headers)
    assert response.json()["source"] == "group"
    site = await client.get(EMAIL_ROUTES["site"])
    assert site.json() == {"available": True, "enabled": None, "effective": True, "source": "group"}


async def test_the_profile_choice_is_stored_on_the_member(client, app, factory, world):
    headers = _login(client, app, world.reader)
    response = await client.put(EMAIL_ROUTES["profile"], json={"enabled": True}, headers=headers)
    assert response.json() == {"available": True, "enabled": True, "effective": True, "source": "profile"}
    async with factory() as db:
        assert (await db.get(Member, world.reader.id)).feedback_email is True
    await client.put(EMAIL_ROUTES["profile"], json={"enabled": None}, headers=headers)
    async with factory() as db:
        assert (await db.get(Member, world.reader.id)).feedback_email is None


@pytest.mark.parametrize(("level", "model"), [("group", GroupFeedbackEmail), ("site", SiteFeedbackEmail)])
async def test_a_choice_is_set_replaced_and_cleared_with_null(client, app, factory, world, level, model):
    headers = _login(client, app, world.reader)
    route = EMAIL_ROUTES[level]
    assert (await client.put(route, json={"enabled": True}, headers=headers)).json()["enabled"] is True
    assert await _count(factory, model) == 1
    assert (await client.put(route, json={"enabled": False}, headers=headers)).json()["enabled"] is False
    assert await _count(factory, model) == 1
    async with factory() as db:
        assert await db.scalar(select(model.enabled)) is False
    cleared = await client.put(route, json={"enabled": None}, headers=headers)
    assert cleared.json() == {"available": True, "enabled": None, "effective": False, "source": "default"}
    assert await _count(factory, model) == 0
    assert (await client.put(route, json={"enabled": None}, headers=headers)).status_code == 200


# -- Email: the audit log ----------------------------------------------------


async def test_an_email_choice_is_audited_with_its_level_and_refs(client, app, factory, world):
    headers = _login(client, app, world.reader)
    await client.put(EMAIL_ROUTES["profile"], json={"enabled": True}, headers=headers)
    await client.put(EMAIL_ROUTES["group"], json={"enabled": False}, headers=headers)
    await client.put(EMAIL_ROUTES["site"], json={"enabled": None}, headers=headers)
    refs = [row.refs for row in await _rows(factory, "feedback_email")]
    assert refs == [
        {"level": "profile", "enabled": True},
        {"level": "group", "group": "team", "enabled": False},
        {"level": "site", "group": "team", "site": "docs", "enabled": None},
    ]


# -- Webhooks: who may call --------------------------------------------------


def _webhook_expectation(level: str, caller: str) -> tuple[int, str | None]:
    allowed = {"admin", "site_admin"} if level == "site" else {"admin"}
    if caller in allowed:
        return 200, None
    if level == "site" and caller == "outsider":
        return 404, "UNKNOWN_SITE"
    return 403, "INSUFFICIENT_ROLE"


@pytest.mark.parametrize("level", ["group", "site"])
@pytest.mark.parametrize("caller", CALLERS)
async def test_the_webhook_role_matrix_on_a_read(client, app, world, level, caller):
    _login(client, app, getattr(world, caller))
    response = await client.get(WEBHOOK_ROUTES[level])
    status, code = _webhook_expectation(level, caller)
    if code:
        _problem(response, status, code)
    else:
        assert response.status_code == 200, response.text


@pytest.mark.parametrize("level", ["group", "site"])
@pytest.mark.parametrize("caller", CALLERS)
async def test_the_webhook_role_matrix_on_a_write(client, app, factory, world, level, caller):
    headers = _login(client, app, getattr(world, caller))
    response = await client.put(WEBHOOK_ROUTES[level], json={"url": URL}, headers=headers)
    status, code = _webhook_expectation(level, caller)
    if code:
        _problem(response, status, code)
        assert await _hooks(factory) == []
        assert await _rows(factory, WEBHOOK_AUDIT[level]) == []
        assert HIDDEN_PATH not in response.text
    else:
        assert response.status_code == 200, response.text
        assert len(await _hooks(factory)) == 1


@pytest.mark.parametrize("level", ["group", "site"])
@pytest.mark.parametrize("caller", CALLERS)
async def test_the_webhook_role_matrix_on_a_delete(client, app, factory, world, level, caller):
    await _put_hook(factory, world, level=level)
    headers = _login(client, app, getattr(world, caller))
    response = await client.delete(WEBHOOK_ROUTES[level], headers=headers)
    status, code = _webhook_expectation(level, caller)
    if code:
        _problem(response, status, code)
        assert len(await _hooks(factory)) == 1
        assert await _rows(factory, WEBHOOK_AUDIT[level]) == []
    else:
        assert response.status_code == 204
        assert await _hooks(factory) == []


async def test_a_group_admin_sets_the_webhook_of_the_group_and_of_a_site(client, app, factory, world):
    headers = _login(client, app, world.admin)
    assert (await client.put(WEBHOOK_ROUTES["group"], json={"url": URL}, headers=headers)).status_code == 200
    assert (await client.put(WEBHOOK_ROUTES["site"], json={"url": OTHER_URL}, headers=headers)).status_code == 200
    assert {hook.host for hook in await _hooks(factory)} == {"mattermost.example.org", "chat.example.org"}


async def test_a_site_only_admin_sets_the_site_webhook_but_not_the_groups(client, app, factory, world):
    headers = _login(client, app, world.site_admin)
    assert (await client.put(WEBHOOK_ROUTES["site"], json={"url": URL}, headers=headers)).status_code == 200
    _problem(await client.put(WEBHOOK_ROUTES["group"], json={"url": URL}, headers=headers), 403, "INSUFFICIENT_ROLE")
    hooks = await _hooks(factory)
    assert [(hook.site_id, hook.group_id) for hook in hooks] == [(world.site.id, None)]


@pytest.mark.parametrize("level", ["group", "site"])
async def test_a_webhook_write_without_the_csrf_header_is_refused(client, app, factory, world, level):
    _login(client, app, world.admin)
    response = await client.put(WEBHOOK_ROUTES[level], json={"url": URL})
    _problem(response, 403, "CSRF_INVALID")
    assert HIDDEN_PATH not in response.text
    assert await _hooks(factory) == []


@pytest.mark.parametrize("level", ["group", "site"])
async def test_a_webhook_delete_without_the_csrf_header_is_refused(client, app, factory, world, level):
    await _put_hook(factory, world, level=level)
    _login(client, app, world.admin)
    _problem(await client.delete(WEBHOOK_ROUTES[level]), 403, "CSRF_INVALID")
    assert len(await _hooks(factory)) == 1


@pytest.mark.parametrize("level", ["group", "site"])
@pytest.mark.parametrize("method", ["get", "put", "delete"])
async def test_a_webhook_without_a_session_is_401(client, world, level, method):
    kwargs = {"json": {"url": URL}} if method == "put" else {}
    response = await getattr(client, method)(WEBHOOK_ROUTES[level], **kwargs)
    _problem(response, 401, "NO_SESSION")
    assert HIDDEN_PATH not in response.text


@pytest.mark.parametrize("level", ["group", "site"])
async def test_a_webhook_on_an_unknown_group_or_site_is_404(client, app, world, level):
    _login(client, app, world.admin)
    route = WEBHOOK_ROUTES[level].replace("team", "bestaat-niet")
    assert (await client.get(route)).status_code == 404
    route = WEBHOOK_ROUTES["site"].replace("docs", "bestaat-niet")
    _problem(await client.get(route), 404, "UNKNOWN_SITE")


# -- Webhooks: an installation that has none ---------------------------------


@pytest.mark.parametrize("level", ["group", "site"])
async def test_an_installation_without_webhook_hosts_says_so_and_refuses_a_write(
    bare_client, bare_app, factory, world, level
):
    headers = _login(bare_client, bare_app, world.admin)
    read = await bare_client.get(WEBHOOK_ROUTES[level])
    assert read.status_code == 200
    assert read.json()["available"] is False
    assert read.json()["configured"] is False
    response = await bare_client.put(WEBHOOK_ROUTES[level], json={"url": URL}, headers=headers)
    _problem(response, 409, "WEBHOOKS_UNAVAILABLE")
    assert HIDDEN_PATH not in response.text
    assert await _hooks(factory) == []
    assert await _rows(factory, WEBHOOK_AUDIT[level]) == []


@pytest.mark.parametrize("level", ["group", "site"])
async def test_an_existing_webhook_can_still_be_removed_when_the_hosts_are_gone(
    bare_client, bare_app, factory, world, level
):
    await _put_hook(factory, world, level=level)
    headers = _login(bare_client, bare_app, world.admin)
    read = await bare_client.get(WEBHOOK_ROUTES[level])
    assert (read.json()["available"], read.json()["configured"]) == (False, True)
    assert (await bare_client.delete(WEBHOOK_ROUTES[level], headers=headers)).status_code == 204
    assert await _hooks(factory) == []


# -- Webhooks: the URL checks ------------------------------------------------

_P = f"/hooks/{HIDDEN_PATH}"
REFUSED_URLS = [
    pytest.param(f"http://mattermost.example.org{_P}", "WEBHOOK_URL_INVALID.scheme", id="http"),
    pytest.param(f"ftp://mattermost.example.org{_P}", "WEBHOOK_URL_INVALID.scheme", id="other-scheme"),
    pytest.param(HIDDEN_PATH, "WEBHOOK_URL_INVALID.scheme", id="no-scheme"),
    pytest.param(
        f"https://gebruiker9:wachtwoord9@mattermost.example.org{_P}",
        "WEBHOOK_URL_INVALID.credentials",
        id="user-and-password",
    ),
    pytest.param(f"https://gebruiker9@mattermost.example.org{_P}", "WEBHOOK_URL_INVALID.credentials", id="user-only"),
    pytest.param(f"https://127.0.0.1{_P}", "WEBHOOK_URL_INVALID.ip_literal", id="ipv4"),
    pytest.param(f"https://[::1]{_P}", "WEBHOOK_URL_INVALID.ip_literal", id="ipv6"),
    pytest.param(f"https://[::1]:8443{_P}", "WEBHOOK_URL_INVALID.ip_literal", id="ipv6-with-port"),
    pytest.param(f"https://2130706433{_P}", None, id="numeric-host"),
    pytest.param(f"https://0x7f000001{_P}", None, id="hex-host"),
    pytest.param(
        f"https://mattermost.example.org:8443{_P}", "WEBHOOK_URL_INVALID.port", id="other-port-on-an-allowed-host"
    ),
    pytest.param(
        f"https://chat.example.org{_P}", "WEBHOOK_URL_INVALID.port", id="default-port-where-another-is-listed"
    ),
    pytest.param(
        f"https://chat.example.org:8444{_P}", "WEBHOOK_URL_INVALID.port", id="wrong-port-on-a-listed-port-host"
    ),
    pytest.param(f"https://elders.example.org{_P}", "WEBHOOK_HOST_NOT_ALLOWED", id="host-not-listed"),
    pytest.param(f"https://mattermost.example.org.evil.test{_P}", "WEBHOOK_HOST_NOT_ALLOWED", id="look-alike-suffix"),
    pytest.param(f"https://evil-mattermost.example.org{_P}", "WEBHOOK_HOST_NOT_ALLOWED", id="look-alike-prefix"),
    pytest.param(f"https://mattermost.example.org.{_P}", "WEBHOOK_HOST_NOT_ALLOWED", id="trailing-dot"),
    pytest.param(
        f"https://mattermost.example.org\\@evil.test{_P}",
        "WEBHOOK_URL_INVALID.credentials",
        id="backslash-hiding-an-at-sign",
    ),
    pytest.param(f"https://mattermost.example.org\\{HIDDEN_PATH}", "WEBHOOK_URL_INVALID.form", id="backslash"),
    pytest.param(
        "https://mattermost.example.org/hooks/" + HIDDEN_PATH + "x" * 2100, "WEBHOOK_URL_INVALID.length", id="too-long"
    ),
    pytest.param(f"https://mattermost.example.org/hooks/{HIDDEN_PATH} extra", "WEBHOOK_URL_INVALID.form", id="space"),
    pytest.param(f"https://mattermost.example.org/hooks/{HIDDEN_PATH}\ntail", "WEBHOOK_URL_INVALID.form", id="newline"),
    pytest.param(f"https://mattermost.example.org/hooks/{HIDDEN_PATH}\x00", "WEBHOOK_URL_INVALID.form", id="control"),
    pytest.param(f"https://mattermost.example.org/hooks/{HIDDEN_PATH}é", "WEBHOOK_URL_INVALID.form", id="non-ascii"),
    pytest.param(f"https://mattermost.example.org{_P}#fragment", "WEBHOOK_URL_INVALID.form", id="fragment"),
    pytest.param(f"https://mattermost.example.org:99999{_P}", "WEBHOOK_URL_INVALID.form", id="port-out-of-range"),
    pytest.param(f"https://mattermost.example.org:abc{_P}", "WEBHOOK_URL_INVALID.form", id="port-not-a-number"),
    pytest.param(f"https:///hooks/{HIDDEN_PATH}", "WEBHOOK_URL_INVALID.form", id="no-host"),
    pytest.param("", "WEBHOOK_URL_INVALID.scheme", id="empty"),
]


@pytest.mark.parametrize("level", ["group", "site"])
@pytest.mark.parametrize(("url", "code"), REFUSED_URLS)
async def test_a_refused_url_is_422_and_neither_stored_audited_nor_repeated(
    client, app, factory, world, level, url, code
):
    headers = _login(client, app, world.admin)
    response = await client.put(WEBHOOK_ROUTES[level], json={"url": url}, headers=headers)
    body = response.json()
    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith(PROBLEM)
    if code is not None:
        assert body["code"] == code.split(".")[0]
        assert body["detail"] == messages.EN[code]
    else:
        assert body["code"].startswith("WEBHOOK_")
    assert HIDDEN_PATH not in response.text
    assert "wachtwoord9" not in response.text
    assert "gebruiker9" not in response.text
    if url:
        assert url not in response.text
        assert json.dumps(url)[1:-1] not in response.text
    assert await _hooks(factory) == []
    assert await _rows(factory, WEBHOOK_AUDIT[level]) == []


async def test_a_refused_replacement_keeps_the_old_webhook(client, app, factory, world):
    await _put_hook(factory, world, level="site")
    headers = _login(client, app, world.admin)
    response = await client.put(
        WEBHOOK_ROUTES["site"], json={"url": f"http://mattermost.example.org{_P}"}, headers=headers
    )
    assert response.status_code == 422
    hooks = await _hooks(factory)
    assert [hook.url for hook in hooks] == [URL]
    assert await _rows(factory, "site_feedback_webhook") == []


@pytest.mark.parametrize("body", [{}, {"url": 12}, {"url": URL, "extra": 1}, {"url": URL, "language": "fr"}])
async def test_a_webhook_body_outside_the_contract_is_422_without_the_url(client, app, factory, world, body):
    headers = _login(client, app, world.admin)
    response = await client.put(WEBHOOK_ROUTES["site"], json=body, headers=headers)
    assert response.status_code == 422, response.text
    assert HIDDEN_PATH not in response.text
    assert await _hooks(factory) == []


@pytest.mark.parametrize(
    ("url", "host"),
    [
        (URL, "mattermost.example.org"),
        (f"https://chat.example.org:8443/hooks/{HIDDEN_PATH}", "chat.example.org"),
        (f"https://MATTERMOST.Example.org/hooks/{HIDDEN_PATH}", "mattermost.example.org"),
        (f"https://mattermost.example.org:443/hooks/{HIDDEN_PATH}", "mattermost.example.org"),
    ],
)
async def test_an_allowed_url_is_stored_with_its_lowercase_host(client, app, factory, world, url, host):
    headers = _login(client, app, world.admin)
    response = await client.put(WEBHOOK_ROUTES["site"], json={"url": url}, headers=headers)
    assert response.status_code == 200, response.text
    [hook] = await _hooks(factory)
    assert (hook.url, hook.host, hook.site_id, hook.set_by) == (url, host, world.site.id, world.admin.id)


# -- Webhooks: what goes in never comes out ----------------------------------


@pytest.mark.parametrize("level", ["group", "site"])
async def test_setting_a_webhook_stores_it_and_returns_no_url(client, app, factory, world, level):
    headers = _login(client, app, world.admin)
    response = await client.put(WEBHOOK_ROUTES[level], json={"url": URL, "language": "nl"}, headers=headers)
    assert response.status_code == 200, response.text
    assert HIDDEN_PATH not in response.text
    assert "hooks" not in response.text
    body = response.json()
    assert body["available"] is True
    assert body["configured"] is True
    assert body["host"] == "mattermost.example.org"
    assert body["language"] == "nl"
    assert body["setAt"].endswith("Z")
    assert body["groupHost"] is None
    [hook] = await _hooks(factory)
    assert hook.url == URL
    read = await client.get(WEBHOOK_ROUTES[level])
    assert HIDDEN_PATH not in read.text
    assert read.json() == body


@pytest.mark.parametrize("level", ["group", "site"])
async def test_a_webhook_is_audited_with_the_host_and_never_the_url(client, app, factory, world, level):
    headers = _login(client, app, world.admin)
    await client.put(WEBHOOK_ROUTES[level], json={"url": URL, "language": "en"}, headers=headers)
    [row] = await _rows(factory, WEBHOOK_AUDIT[level])
    expected = {"group": "team", "host": "mattermost.example.org", "language": "en", "set": True}
    if level == "site":
        expected["site"] = "docs"
    assert row.refs == expected
    serialized = json.dumps(row.refs)
    assert HIDDEN_PATH not in serialized
    assert "hooks" not in serialized
    assert "https" not in serialized
    assert row.result == "allowed"


@pytest.mark.parametrize("level", ["group", "site"])
async def test_replacing_a_webhook_keeps_one_row_with_the_new_host(client, app, factory, world, level):
    headers = _login(client, app, world.admin)
    first = await client.put(WEBHOOK_ROUTES[level], json={"url": URL}, headers=headers)
    second = await client.put(WEBHOOK_ROUTES[level], json={"url": OTHER_URL}, headers=headers)
    [hook] = await _hooks(factory)
    assert (hook.url, hook.host) == (OTHER_URL, "chat.example.org")
    assert second.json()["host"] == "chat.example.org"
    assert second.json()["setAt"] >= first.json()["setAt"]
    assert len(await _rows(factory, WEBHOOK_AUDIT[level])) == 2


async def test_a_site_shows_the_host_of_the_webhook_of_its_group(client, app, factory, world):
    headers = _login(client, app, world.admin)
    await client.put(WEBHOOK_ROUTES["group"], json={"url": OTHER_URL}, headers=headers)
    site = await client.get(WEBHOOK_ROUTES["site"])
    assert site.json()["groupHost"] == "chat.example.org"
    assert site.json()["configured"] is False
    assert site.json()["host"] is None
    assert site.json()["language"] is None
    assert site.json()["setAt"] is None
    assert HIDDEN_PATH not in site.text
    own = await client.put(WEBHOOK_ROUTES["site"], json={"url": URL}, headers=headers)
    assert (own.json()["host"], own.json()["groupHost"]) == ("mattermost.example.org", "chat.example.org")
    group = await client.get(WEBHOOK_ROUTES["group"])
    assert group.json()["groupHost"] is None


@pytest.mark.parametrize("level", ["group", "site"])
async def test_a_group_or_site_without_a_webhook_reads_as_not_configured(client, app, world, level):
    _login(client, app, world.admin)
    response = await client.get(WEBHOOK_ROUTES[level])
    assert response.json() == {
        "available": True,
        "configured": False,
        "host": None,
        "language": None,
        "setAt": None,
        "groupHost": None,
    }


# -- Webhooks: the language of the posts -------------------------------------


@pytest.mark.parametrize(
    ("member_language", "accept_language", "body_language", "expected"),
    [
        (MemberLanguage.NL, None, None, "nl"),
        (MemberLanguage.EN, None, None, "en"),
        (MemberLanguage.NL, "en", None, "nl"),
        (MemberLanguage.EN, "nl", None, "en"),
        (None, "nl", None, "nl"),
        (None, "en", None, "en"),
        (None, None, None, "en"),
        (None, "fr", None, "en"),
        (MemberLanguage.NL, "nl", "en", "en"),
        (MemberLanguage.EN, "en", "nl", "nl"),
        (None, None, "nl", "nl"),
    ],
)
async def test_the_language_of_a_webhook_defaults_to_the_member_then_the_request(
    client, app, factory, world, member_language, accept_language, body_language, expected
):
    await _set_language(factory, world.admin, member_language)
    headers = _login(client, app, world.admin)
    if accept_language:
        headers["Accept-Language"] = accept_language
    body = {"url": URL, **({"language": body_language} if body_language else {})}
    response = await client.put(WEBHOOK_ROUTES["group"], json=body, headers=headers)
    assert response.json()["language"] == expected
    [hook] = await _hooks(factory)
    assert hook.language == MemberLanguage(expected)
    [row] = await _rows(factory, "group_feedback_webhook")
    assert row.refs["language"] == expected


async def test_an_explicit_language_null_falls_back_to_the_default(client, app, factory, world):
    await _set_language(factory, world.admin, MemberLanguage.NL)
    headers = _login(client, app, world.admin)
    response = await client.put(WEBHOOK_ROUTES["site"], json={"url": URL, "language": None}, headers=headers)
    assert response.json()["language"] == "nl"


# -- Webhooks: removing ------------------------------------------------------


@pytest.mark.parametrize("level", ["group", "site"])
async def test_deleting_a_webhook_removes_it_and_audits_once(client, app, factory, world, level):
    headers = _login(client, app, world.admin)
    await client.put(WEBHOOK_ROUTES[level], json={"url": URL}, headers=headers)
    response = await client.delete(WEBHOOK_ROUTES[level], headers=headers)
    assert response.status_code == 204
    assert response.content == b""
    assert await _hooks(factory) == []
    rows = await _rows(factory, WEBHOOK_AUDIT[level])
    assert [row.refs.get("set") for row in rows] == [True, False]
    expected = {"group": "team", "set": False} | ({"site": "docs"} if level == "site" else {})
    assert rows[-1].refs == expected
    assert HIDDEN_PATH not in json.dumps(rows[-1].refs)


@pytest.mark.parametrize("level", ["group", "site"])
async def test_deleting_a_webhook_that_is_not_there_is_204_without_an_audit_row(client, app, factory, world, level):
    headers = _login(client, app, world.admin)
    assert (await client.delete(WEBHOOK_ROUTES[level], headers=headers)).status_code == 204
    assert await _rows(factory, WEBHOOK_AUDIT[level]) == []


@pytest.mark.parametrize("level", ["group", "site"])
async def test_deleting_a_second_time_is_204_without_a_second_audit_row(client, app, factory, world, level):
    await _put_hook(factory, world, level=level)
    headers = _login(client, app, world.admin)
    assert (await client.delete(WEBHOOK_ROUTES[level], headers=headers)).status_code == 204
    assert (await client.delete(WEBHOOK_ROUTES[level], headers=headers)).status_code == 204
    assert len(await _rows(factory, WEBHOOK_AUDIT[level])) == 1


async def test_deleting_the_site_webhook_leaves_the_group_webhook(client, app, factory, world):
    await _put_hook(factory, world, level="group")
    await _put_hook(factory, world, level="site", url=OTHER_URL, host="chat.example.org")
    headers = _login(client, app, world.admin)
    await client.delete(WEBHOOK_ROUTES["site"], headers=headers)
    [hook] = await _hooks(factory)
    assert hook.group_id == world.group.id

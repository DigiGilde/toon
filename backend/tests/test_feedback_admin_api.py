"""The admin API for site feedback (spec §16.11): the seven routes under
`/sites/{group}/{site}/feedback`, the feedback fields of SiteOut, and the CLI
token on the five routes the CLI uses.

The app wires the admin router behind BearerOutsideDeploysMiddleware the way
main.py does, so a bearer on a route outside the allowlist meets the 401 it
meets in production. Refusals come first: the role matrix, CSRF, the tokens,
then what each route does.
"""

from __future__ import annotations

import asyncio
import base64
import json
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import FastAPI
from helpers_oidc import APP_BASE_URL, CONTENT_BASE_URL, make_test_client, set_session_cookie
from sqlalchemy import JSON, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from test_feedback_api import JOINED_EMOJI, PERSIAN, REFUSED_FORMAT, UNASSIGNED, SlowCommitSession

from toon.api.admin import make_admin_router
from toon.api.deploys import BearerOutsideDeploysMiddleware
from toon.api.errors import register_error_handlers
from toon.api.openapi_file import schema as english_schema
from toon.api.openapi_i18n import localise
from toon.audit.log import AuditLog
from toon.audit.pseudonymisation import pseudonymise
from toon.auth.sessions import CSRF_COOKIE, CSRF_HEADER, SessionStore
from toon.cli import service as cli
from toon.config import Settings
from toon.constants import (
    FEEDBACK_MAX_GIVER_REPLIES,
    FEEDBACK_MAX_REPLIES,
    AccessBase,
    FeedbackAuthor,
    FeedbackIdentity,
    FeedbackKind,
    FeedbackReplyAuthor,
    FeedbackStatus,
    Role,
)
from toon.db import make_session_factory
from toon.ingest.store import ContentStore
from toon.models.audit import AuditLogEntry
from toon.models.cli import CliSession
from toon.models.feedback import FeedbackItem, FeedbackReply, FeedbackScreenshot
from toon.models.identity import Group, GroupMember, Member, MemberStatus, PlatformRole, SiteMember
from toon.models.publication import Site, Version, VersionTarget

BASE = "/-/api/v1"
FEEDBACK = f"{BASE}/sites/team/docs/feedback"
PROBLEM = "application/problem+json"
PEPPER = "audit-pepper-van-minstens-32-bytes!!"
WWW_AUTHENTICATE = 'Bearer realm="toon", error="invalid_token"'
JWT_SHAPED = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
POINT_ANCHOR = {
    "selectors": [{"type": "CssSelector", "value": "main > p:nth-of-type(3)"}],
    "point": {"x": 0.5, "y": 0.25, "pageX": 640, "pageY": 1210},
}
TEXT_ANCHOR = {
    "selectors": [
        {"type": "TextQuoteSelector", "exact": "2,4 miljoen", "prefix": "begroting van ", "suffix": " euro voor"},
        {"type": "TextPositionSelector", "start": 1200, "end": 1211},
        {"type": "CssSelector", "value": "main > p:nth-of-type(3)"},
    ],
    "point": None,
}


def _settings(content_root) -> Settings:
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
    )


@pytest_asyncio.fixture
async def factory(migrated_dsn: str):
    engine = create_async_engine(migrated_dsn, poolclass=NullPool)
    try:
        yield make_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def app(factory, tmp_path) -> FastAPI:
    content_root = tmp_path / "content"
    content_root.mkdir()
    settings = _settings(content_root)
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
async def client(app):
    async with make_test_client(app) as client:
        yield client


@dataclass(frozen=True)
class World:
    group: Group
    site: Site
    other: Site
    admin: Member
    editor: Member
    reader: Member
    site_reader: Member
    outsider: Member
    platform_admin: Member


@pytest_asyncio.fixture
async def world(factory) -> World:
    """Group 'team' with sites 'docs' and 'ander'; a group admin, editor and
    reader, a reader of 'docs' alone, someone without a role and a platform
    admin without one."""
    async with factory() as db:
        members = {
            sub: Member(
                sso_subject=sub,
                email=f"{sub}@example.nl",
                name=name,
                status=MemberStatus.ACTIVE,
                platform_role=PlatformRole.ADMIN if sub == "platformbeheer" else PlatformRole.MEMBER,
            )
            for sub, name in (
                ("beheerder", None),
                ("redacteur", "Sanne Jansen"),
                ("lezer", "Lex Lezer"),
                ("sitelezer", None),
                ("buitenstaander", None),
                ("platformbeheer", None),
            )
        }
        group = Group(slug="team", name="Team", default_access_base=AccessBase.SITE_TEAM)
        db.add_all([group, *members.values()])
        await db.flush()
        site = Site(group_id=group.id, slug="docs", title="Docs", access_base=AccessBase.SITE_TEAM)
        other = Site(group_id=group.id, slug="ander", title="Ander", access_base=AccessBase.SITE_TEAM)
        db.add_all([site, other])
        await db.flush()
        for sub, role in (("beheerder", Role.ADMIN), ("redacteur", Role.EDITOR), ("lezer", Role.READER)):
            db.add(GroupMember(group_id=group.id, member_id=members[sub].id, role=role))
        db.add(SiteMember(site_id=site.id, member_id=members["sitelezer"].id, role=Role.READER))
        await db.commit()
    return World(
        group=group,
        site=site,
        other=other,
        admin=members["beheerder"],
        editor=members["redacteur"],
        reader=members["lezer"],
        site_reader=members["sitelezer"],
        outsider=members["buitenstaander"],
        platform_admin=members["platformbeheer"],
    )


def _login(client, app, member: Member) -> dict[str, str]:
    """An admin session for `member`; returns the CSRF header for mutations."""
    session = set_session_cookie(client, app, sub=member.sso_subject, email=member.email)
    client.cookies.set(CSRF_COOKIE, session.csrf_token, domain="toon.example", path="/")
    return {CSRF_HEADER: session.csrf_token}


async def _token(factory, member: Member) -> str:
    """A CLI access token for this member, through the real device flow."""
    async with factory() as db:
        created = await cli.create_device_authorization(db, client_name="toon-cli", ip_truncated=None)
    async with factory() as db:
        await cli.decide(db, created.user_code, await db.get(Member, member.id), approve=True)
    async with factory() as db:
        return (await cli.exchange_device_code(db, created.device_code)).access_token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _item(factory, site: Site, **values) -> FeedbackItem:
    """A feedback item as the widget API would store it."""
    fields = {
        "site_id": site.id,
        "target": VersionTarget.LIVE,
        "page_path": "docs/",
        "kind": FeedbackKind.PAGE,
        "comment": "Klopt dit bedrag?",
        "author_kind": FeedbackAuthor.ANONYMOUS,
        **values,
    }
    async with factory() as db:
        item = FeedbackItem(**fields)
        db.add(item)
        await db.commit()
        return item


async def _account_item(factory, site: Site, **values) -> FeedbackItem:
    return await _item(
        factory,
        site,
        author_kind=FeedbackAuthor.ACCOUNT,
        author_sub="gever-sub",
        author_name="Jan Jansen",
        author_email="jan.jansen@example.nl",
        **values,
    )


async def _screenshot(factory, item: FeedbackItem, *, data: bytes = PNG, media_type: str = "image/png") -> None:
    async with factory() as db:
        db.add(
            FeedbackScreenshot(
                item_id=item.id, media_type=media_type, data=data, size_bytes=len(data), width=10, height=10
            )
        )
        await db.commit()


async def _reply(factory, item: FeedbackItem, **values) -> None:
    async with factory() as db:
        db.add(FeedbackReply(item_id=item.id, **values))
        await db.commit()


async def _stored(factory, item: FeedbackItem) -> FeedbackItem | None:
    async with factory() as db:
        return await db.get(FeedbackItem, item.id)


async def _rows(factory, action: str) -> list[AuditLogEntry]:
    async with factory() as db:
        return list(
            await db.scalars(
                select(AuditLogEntry).where(AuditLogEntry.action == action).order_by(AuditLogEntry.occurred_at)
            )
        )


async def _count(factory, model, **filters) -> int:
    async with factory() as db:
        statement = select(func.count()).select_from(model)
        for name, value in filters.items():
            statement = statement.where(getattr(model, name) == value)
        return await db.scalar(statement)


def _problem(response, status: int, code: str | None) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith(PROBLEM)
    body = response.json()
    assert body.get("code") == code
    return body


def _fragment(url: str) -> dict:
    """The JSON of the highlight fragment at the end of `url`."""
    _, _, fragment = url.partition("#")
    assert fragment.startswith("toon-feedback=")
    value = fragment.removeprefix("toon-feedback=")
    assert "=" not in value
    return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))


# -- Who may call what: the role matrix -------------------------------------

# Per route: method, path after .../feedback, body, the status of success and
# the narrowest effective site role that gets it.
ROUTES = {
    "list": ("GET", "", None, 200, Role.READER),
    "detail": ("GET", "/{item}", None, 200, Role.READER),
    "screenshot": ("GET", "/{item}/screenshot", None, 200, Role.READER),
    "status": ("PUT", "/{item}/status", {"status": "seen"}, 200, Role.EDITOR),
    "reply": ("POST", "/{item}/replies", {"body": "Dank je, dit is aangepast."}, 201, Role.EDITOR),
    "delete": ("DELETE", "/{item}", None, 204, Role.ADMIN),
    "settings": ("PUT", "-settings", {"enabled": True, "identity": "name", "live": False}, 200, Role.ADMIN),
}
RANK = {Role.READER: 1, Role.EDITOR: 2, Role.ADMIN: 3}
# Caller, and the effective role it has on 'docs'.
CALLERS = {
    "reader": Role.READER,
    "site_reader": Role.READER,
    "editor": Role.EDITOR,
    "admin": Role.ADMIN,
    "outsider": None,
    "platform_admin": None,
}
CLI_ROUTES = ("list", "detail", "screenshot", "status", "reply")


async def _call(client, route: str, item: FeedbackItem, headers: dict[str, str]):
    method_, suffix, body, _, _ = ROUTES[route]
    return await client.request(
        method_, FEEDBACK + suffix.format(item=item.id), json=body, headers=headers
    )


def _expect(response, route: str, caller: str) -> None:
    _, _, _, success, minimum = ROUTES[route]
    role = CALLERS[caller]
    if caller == "outsider":
        _problem(response, 404, "UNKNOWN_SITE")
    elif role is None or RANK[role] < RANK[minimum]:
        _problem(response, 403, "INSUFFICIENT_ROLE")
    else:
        assert response.status_code == success, response.text


@pytest.mark.parametrize("caller", list(CALLERS))
@pytest.mark.parametrize("route", list(ROUTES))
async def test_the_session_role_matrix(client, app, factory, world, route, caller):
    """No role is the 404 of a site that is not there, a role too narrow the
    403, and a platform admin without a role is refused like anyone else. A
    read sends no CSRF header: none of them needs one."""
    item = await _item(factory, world.site)
    await _screenshot(factory, item)
    headers = _login(client, app, getattr(world, caller))
    if ROUTES[route][0] == "GET":
        headers = {}

    response = await _call(client, route, item, headers)

    _expect(response, route, caller)


@pytest.mark.parametrize("caller", list(CALLERS))
@pytest.mark.parametrize("route", CLI_ROUTES)
async def test_the_cli_token_role_matrix(client, factory, world, route, caller):
    """The CLI token is the member: the same roles decide, with no CSRF."""
    item = await _item(factory, world.site)
    await _screenshot(factory, item)
    token = await _token(factory, getattr(world, caller))

    response = await _call(client, route, item, _bearer(token))

    _expect(response, route, caller)


# -- Sessions, CSRF and tokens ------------------------------------------------


@pytest.mark.parametrize("route", list(ROUTES))
async def test_without_a_session_or_a_token_every_route_is_401(client, factory, world, route):
    item = await _item(factory, world.site)

    response = await _call(client, route, item, {})

    _problem(response, 401, "NO_SESSION")


@pytest.mark.parametrize("route", ["status", "reply", "delete", "settings"])
async def test_a_session_mutation_without_the_csrf_header_is_refused(client, app, factory, world, route):
    item = await _item(factory, world.site)
    _login(client, app, world.admin)

    response = await _call(client, route, item, {})

    _problem(response, 403, "CSRF_INVALID")
    stored = await _stored(factory, item)
    assert stored is not None
    assert stored.status == FeedbackStatus.NEW
    assert await _count(factory, FeedbackReply) == 0
    async with factory() as db:
        assert (await db.get(Site, world.site.id)).feedback_enabled is False


@pytest.mark.parametrize("route", CLI_ROUTES)
async def test_a_ci_id_token_is_401_and_names_the_cli_token(client, factory, world, route):
    item = await _item(factory, world.site)

    response = await _call(client, route, item, _bearer(JWT_SHAPED))

    body = _problem(response, 401, "TOKEN_INVALID")
    assert "toon login" in body["detail"]
    assert response.headers["WWW-Authenticate"] == WWW_AUTHENTICATE
    assert (await _stored(factory, item)).status == FeedbackStatus.NEW


@pytest.mark.parametrize("route", ["delete", "settings"])
async def test_a_valid_cli_token_of_an_admin_cannot_delete_or_change_the_settings(client, factory, world, route):
    item = await _item(factory, world.site)
    token = await _token(factory, world.admin)

    response = await _call(client, route, item, _bearer(token))

    _problem(response, 401, "BEARER_NOT_ACCEPTED")
    assert response.headers["WWW-Authenticate"] == WWW_AUTHENTICATE
    assert await _stored(factory, item) is not None
    async with factory() as db:
        assert (await db.get(Site, world.site.id)).feedback_enabled is False


async def test_an_explicit_none_anchor_and_viewport_are_stored_as_sql_null(factory, world):
    """JSON null would break the anchor and viewport CHECK constraints."""
    item = await _item(factory, world.site, anchor=None, viewport=None)

    async with factory() as db:
        row = (
            await db.execute(
                select(FeedbackItem.anchor.is_(None), FeedbackItem.viewport.is_(None)).where(
                    FeedbackItem.id == item.id
                )
            )
        ).one()
    assert tuple(row) == (True, True)


async def test_an_unknown_cli_token_is_401(client, factory, world):
    response = await client.get(FEEDBACK, headers=_bearer("tooncli_abc_def"))

    _problem(response, 401, "TOKEN_INVALID")
    assert response.headers["WWW-Authenticate"] == WWW_AUTHENTICATE


async def test_the_token_of_a_deactivated_member_is_403(client, factory, world):
    token = await _token(factory, world.editor)
    async with factory() as db:
        await db.execute(update(Member).where(Member.id == world.editor.id).values(status=MemberStatus.DEACTIVATED))
        await db.commit()

    response = await client.get(FEEDBACK, headers=_bearer(token))

    _problem(response, 403, "MEMBER_NOT_ACTIVE")


async def test_a_valid_session_does_not_rescue_a_bad_token_on_a_read(client, app, factory, world):
    """With a bearer header the bearer decides, on a read as on a mutation."""
    _login(client, app, world.editor)

    response = await client.get(FEEDBACK, headers=_bearer("tooncli_abc_def"))

    _problem(response, 401, "TOKEN_INVALID")


async def test_a_refusal_with_the_cli_token_is_audited_as_the_cli_member(client, factory, world):
    item = await _item(factory, world.site)
    token = await _token(factory, world.reader)

    response = await client.put(f"{FEEDBACK}/{item.id}/status", json={"status": "seen"}, headers=_bearer(token))

    _problem(response, 403, "INSUFFICIENT_ROLE")
    [row] = await _rows(factory, "admin_access")
    assert (row.result, row.reason_code) == ("refused", "INSUFFICIENT_ROLE")
    assert row.actor_pseudonym == pseudonymise(PEPPER, "lezer")
    async with factory() as db:
        session_id = str(await db.scalar(select(CliSession.id).where(CliSession.member_id == world.reader.id)))
    assert row.refs == {
        "method": "PUT",
        "route": f"{BASE}/sites/{{group_slug}}/{{site_slug}}/feedback/{{item_id}}/status",
        "status": 403,
        "via": "cli",
        "cli_session": session_id,
    }


# -- The list -------------------------------------------------------------------


async def _listed(client, query: str = "", **params: object) -> dict:
    # httpx replaces the query of the URL with `params`, even an empty one.
    response = await client.get(f"{FEEDBACK}{query}", params=params or None)
    assert response.status_code == 200, response.text
    return response.json()


def _ids(page: dict) -> list[str]:
    return [item["id"] for item in page["items"]]


async def test_without_filters_the_list_holds_the_open_items_newest_first(client, app, factory, world):
    moment = datetime(2026, 11, 1, 9, tzinfo=UTC)
    old = await _item(factory, world.site, created_at=moment)
    seen = await _item(factory, world.site, status=FeedbackStatus.SEEN, created_at=moment + timedelta(hours=1))
    await _item(
        factory,
        world.site,
        status=FeedbackStatus.RESOLVED,
        resolved_at=moment,
        created_at=moment + timedelta(hours=2),
    )
    newest = await _item(factory, world.site, created_at=moment + timedelta(hours=3))
    await _item(factory, world.other, created_at=moment + timedelta(hours=4))
    _login(client, app, world.reader)

    page = await _listed(client)

    assert _ids(page) == [str(newest.id), str(seen.id), str(old.id)]
    assert page["nextCursor"] is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [("open", ["seen", "new"]), ("new", ["new"]), ("seen", ["seen"]), ("resolved", ["resolved"]),
     ("all", ["resolved", "seen", "new"])],
)
async def test_each_status_filter(client, app, factory, world, status, expected):
    moment = datetime(2026, 11, 1, 9, tzinfo=UTC)
    await _item(factory, world.site, created_at=moment)
    await _item(factory, world.site, status=FeedbackStatus.SEEN, created_at=moment + timedelta(hours=1))
    await _item(
        factory, world.site, status=FeedbackStatus.RESOLVED, resolved_at=moment, created_at=moment + timedelta(hours=2)
    )
    _login(client, app, world.reader)

    page = await _listed(client, f"?status={status}")

    assert [item["status"] for item in page["items"]] == expected


async def test_the_target_and_ref_filters(client, app, factory, world):
    moment = datetime(2026, 11, 1, 9, tzinfo=UTC)
    live = await _item(factory, world.site, created_at=moment)
    first = await _item(
        factory, world.site, target=VersionTarget.PREVIEW, preview_ref="pr-1", created_at=moment + timedelta(hours=1)
    )
    second = await _item(
        factory, world.site, target=VersionTarget.PREVIEW, preview_ref="pr-2", created_at=moment + timedelta(hours=2)
    )
    _login(client, app, world.reader)

    assert _ids(await _listed(client)) == [str(second.id), str(first.id), str(live.id)]
    assert _ids(await _listed(client, "?target=all")) == [str(second.id), str(first.id), str(live.id)]
    assert _ids(await _listed(client, "?target=live")) == [str(live.id)]
    assert _ids(await _listed(client, "?target=preview")) == [str(second.id), str(first.id)]
    assert _ids(await _listed(client, "?target=preview&ref=pr-1")) == [str(first.id)]
    assert _ids(await _listed(client, "?target=preview&ref=pr-9")) == []


@pytest.mark.parametrize("query", ["?ref=pr-1", "?target=live&ref=pr-1", "?target=all&ref=pr-1"])
async def test_a_ref_without_target_preview_is_422(client, app, world, query):
    _login(client, app, world.reader)

    response = await client.get(f"{FEEDBACK}{query}")

    body = _problem(response, 422, "FEEDBACK_REF_WITHOUT_PREVIEW")
    assert body["detail"] == "The filter ref only works together with target=preview."
    dutch = await client.get(f"{FEEDBACK}{query}", headers={"Accept-Language": "nl"})
    assert dutch.json()["detail"] == "Het filter ref werkt alleen samen met target=preview."


@pytest.mark.parametrize(
    "ref", ["a\x00b", "PR-1", "pr-1\n", "-pr", "x" * 64], ids=["nul", "capitals", "newline", "leading-dash", "too-long"]
)
async def test_a_ref_that_is_no_slug_is_422(client, app, world, ref):
    """A stored preview ref is always a slug, so nothing else can match one.
    A NUL would otherwise reach PostgreSQL, which refuses it: a 500."""
    _login(client, app, world.reader)

    response = await client.get(FEEDBACK, params={"target": "preview", "ref": ref})

    body = _problem(response, 422, "PREVIEW_REF_INVALID")
    assert body["detail"] == "The preview ref is not a valid slug."


@pytest.mark.parametrize("query", ["?status=open,new", "?status=gezien", "?target=beide", "?limit=0", "?limit=101"])
async def test_a_filter_outside_its_values_is_422(client, app, world, query):
    _login(client, app, world.reader)

    response = await client.get(f"{FEEDBACK}{query}")

    _problem(response, 422, None)


@pytest.mark.parametrize("cursor", ["niet-base64!", base64.urlsafe_b64encode(b"geen|cursor").decode()])
async def test_an_unreadable_cursor_is_422(client, app, world, cursor):
    _login(client, app, world.reader)

    response = await client.get(FEEDBACK, params={"cursor": cursor})

    _problem(response, 422, "CURSOR_INVALID")


async def test_the_cursor_pages_through_without_skipping_or_repeating(client, app, factory, world):
    """Items sent in the same instant are told apart by their id, so a cursor
    that lands among them neither skips one nor shows one twice."""
    moment = datetime(2026, 11, 1, 9, tzinfo=UTC)
    items = [await _item(factory, world.site, created_at=moment) for _ in range(3)]
    items += [await _item(factory, world.site, created_at=moment + timedelta(minutes=1)) for _ in range(2)]
    expected = [
        str(item.id) for item in sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)
    ]
    _login(client, app, world.reader)

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        page = await _listed(client, limit=2, **({"cursor": cursor} if cursor else {}))
        seen += _ids(page)
        pages += 1
        cursor = page["nextCursor"]
        if cursor is None:
            break

    assert seen == expected
    assert pages == 3


async def test_a_full_last_page_has_no_cursor(client, app, factory, world):
    for _ in range(2):
        await _item(factory, world.site)
    _login(client, app, world.reader)

    page = await _listed(client, "?limit=2")

    assert len(page["items"]) == 2
    assert page["nextCursor"] is None


async def test_the_counts_cover_the_whole_site_whatever_the_filter(client, app, factory, world):
    moment = datetime(2026, 11, 1, 9, tzinfo=UTC)
    await _item(factory, world.site)
    await _item(factory, world.site, target=VersionTarget.PREVIEW, preview_ref="pr-1")
    await _item(factory, world.site, status=FeedbackStatus.SEEN)
    for _ in range(3):
        await _item(factory, world.site, status=FeedbackStatus.RESOLVED, resolved_at=moment)
    await _item(factory, world.other)
    _login(client, app, world.reader)

    page = await _listed(client, "?status=resolved&target=live&limit=1")

    assert page["counts"] == {"new": 2, "seen": 1, "resolved": 3}
    assert len(page["items"]) == 1


async def test_a_site_without_feedback_lists_nothing_and_counts_zero(client, app, world):
    _login(client, app, world.reader)

    assert await _listed(client) == {"items": [], "nextCursor": None, "counts": {"new": 0, "seen": 0, "resolved": 0}}


async def test_an_item_carries_its_fields(client, app, factory, world):
    async with factory() as db:
        version = Version(site_id=world.site.id, target=VersionTarget.LIVE, member_id=world.editor.id)
        db.add(version)
        await db.commit()
    item = await _account_item(
        factory,
        world.site,
        kind=FeedbackKind.TEXT,
        quote="2,4 miljoen",
        anchor=TEXT_ANCHOR,
        page_path="begroting/",
        page_title="Begroting",
        version_id=version.id,
        created_at=datetime(2026, 11, 2, 10, 30, tzinfo=UTC),
    )
    await _screenshot(factory, item)
    await _reply(factory, item, author_kind=FeedbackReplyAuthor.GIVER, body="Nog een vraag.")
    await _reply(
        factory,
        item,
        author_kind=FeedbackReplyAuthor.MEMBER,
        member_id=world.editor.id,
        author_name="Sanne Jansen",
        body="Opgelost.",
    )
    _login(client, app, world.editor)

    [listed] = (await _listed(client))["items"]

    assert listed == {
        "id": str(item.id),
        "kind": "text",
        "status": "new",
        "comment": "Klopt dit bedrag?",
        "quote": "2,4 miljoen",
        "page": {
            "path": "begroting/",
            "title": "Begroting",
            "url": f"{CONTENT_BASE_URL}/team/docs/begroting/",
        },
        "target": "live",
        "previewRef": None,
        "versionId": str(version.id),
        "author": {"kind": "account", "name": "Jan Jansen", "email": "jan.jansen@example.nl"},
        "hasScreenshot": True,
        "replyCount": 2,
        "createdAt": "2026-11-02T10:30:00Z",
        "statusChangedAt": None,
        "statusChangedBy": None,
    }


async def test_an_item_without_screenshot_replies_or_version_says_so(client, app, factory, world):
    await _item(factory, world.site, page_title=None)
    _login(client, app, world.reader)

    [listed] = (await _listed(client))["items"]

    assert (listed["hasScreenshot"], listed["replyCount"], listed["versionId"]) == (False, 0, None)
    assert listed["page"]["title"] is None
    assert listed["author"] == {"kind": "anonymous", "name": None, "email": None}


async def test_a_name_item_shows_the_typed_name_and_no_email(client, app, factory, world):
    await _item(factory, world.site, author_kind=FeedbackAuthor.NAME, author_name="Piet")
    _login(client, app, world.admin)

    [listed] = (await _listed(client))["items"]

    assert listed["author"] == {"kind": "name", "name": "Piet", "email": None}


@pytest.mark.parametrize(("caller", "email"), [
    ("reader", None),
    ("site_reader", None),
    ("editor", "jan.jansen@example.nl"),
    ("admin", "jan.jansen@example.nl"),
])
async def test_the_email_of_a_submitter_is_for_editors_and_admins(client, app, factory, world, caller, email):
    item = await _account_item(factory, world.site)
    _login(client, app, getattr(world, caller))

    [listed] = (await _listed(client))["items"]
    detail = (await client.get(f"{FEEDBACK}/{item.id}")).json()

    assert listed["author"] == {"kind": "account", "name": "Jan Jansen", "email": email}
    assert detail["author"] == listed["author"]


async def test_a_site_role_wider_than_the_group_role_shows_the_email(client, app, factory, world):
    """The effective role is the widest of the two, as everywhere else."""
    async with factory() as db:
        db.add(SiteMember(site_id=world.site.id, member_id=world.reader.id, role=Role.EDITOR))
        await db.commit()
    await _account_item(factory, world.site)
    _login(client, app, world.reader)

    [listed] = (await _listed(client))["items"]

    assert listed["author"]["email"] == "jan.jansen@example.nl"


async def test_the_page_address_is_the_current_address_of_the_site(client, app, factory, world):
    """The item keeps its path relative to the root; the address follows the
    site to a new slug, and a preview item points into its preview."""
    moment = datetime(2026, 11, 1, 9, tzinfo=UTC)
    await _item(factory, world.site, page_path="", created_at=moment)
    await _item(
        factory,
        world.site,
        target=VersionTarget.PREVIEW,
        preview_ref="pr-42",
        page_path="docs/aan-de-slag/",
        created_at=moment + timedelta(hours=1),
    )
    headers = _login(client, app, world.admin)
    moved = await client.put(f"{BASE}/sites/team/docs/slug", json={"slug": "handboek"}, headers=headers)
    assert moved.status_code == 200, moved.text

    response = await client.get(f"{BASE}/sites/team/handboek/feedback")

    preview, live = response.json()["items"]
    assert preview["page"] == {
        "path": "docs/aan-de-slag/",
        "title": None,
        "url": f"{CONTENT_BASE_URL}/team/handboek/_preview/pr-42/docs/aan-de-slag/",
    }
    assert (preview["target"], preview["previewRef"]) == ("preview", "pr-42")
    assert live["page"]["url"] == f"{CONTENT_BASE_URL}/team/handboek/"


async def test_a_stored_path_is_encoded_where_it_would_end_the_path(client, app, factory, world):
    """A path is stored percent-encoded; what it holds that would end or
    break the path in a URL is encoded, what is encoded already stays."""
    await _item(factory, world.site, page_path="a b/über?x#y/50%25/")
    _login(client, app, world.reader)

    [listed] = (await _listed(client))["items"]

    assert listed["page"]["url"] == f"{CONTENT_BASE_URL}/team/docs/a%20b/%C3%BCber%3Fx%23y/50%25/"
    assert listed["page"]["path"] == "a b/über?x#y/50%25/"


async def test_who_changed_the_status_is_named_or_else_their_email(client, app, factory, world):
    moment = datetime(2026, 11, 1, 9, tzinfo=UTC)
    await _item(
        factory,
        world.site,
        status=FeedbackStatus.SEEN,
        status_changed_at=moment,
        status_changed_by=world.editor.id,
        created_at=moment,
    )
    await _item(
        factory,
        world.site,
        status=FeedbackStatus.SEEN,
        status_changed_at=moment,
        status_changed_by=world.admin.id,
        created_at=moment + timedelta(hours=1),
    )
    _login(client, app, world.reader)

    by_admin, by_editor = (await _listed(client))["items"]

    assert (by_editor["statusChangedBy"], by_editor["statusChangedAt"]) == ("Sanne Jansen", "2026-11-01T09:00:00Z")
    assert by_admin["statusChangedBy"] == "beheerder@example.nl"


# -- One item ---------------------------------------------------------------------


async def test_the_detail_carries_anchor_viewport_and_replies_oldest_first(client, app, factory, world):
    item = await _account_item(
        factory,
        world.site,
        kind=FeedbackKind.POINT,
        anchor=POINT_ANCHOR,
        viewport={"width": 1280, "height": 800, "dpr": 2},
    )
    moment = datetime(2026, 11, 2, 9, tzinfo=UTC)
    await _reply(
        factory,
        item,
        author_kind=FeedbackReplyAuthor.MEMBER,
        member_id=world.editor.id,
        author_name="Sanne Jansen",
        body="Welke alinea?",
        created_at=moment,
    )
    await _reply(
        factory, item, author_kind=FeedbackReplyAuthor.GIVER, body="De derde.\nMet\tinsprong.",
        created_at=moment + timedelta(minutes=5),
    )
    await _reply(
        factory, item, author_kind=FeedbackReplyAuthor.MEMBER, body="Aangepast.", created_at=moment + timedelta(hours=1)
    )
    _login(client, app, world.reader)

    response = await client.get(f"{FEEDBACK}/{item.id}")

    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["anchor"] == POINT_ANCHOR
    assert detail["viewport"] == {"width": 1280, "height": 800, "dpr": 2}
    assert [(reply["author"], reply["body"], reply["createdAt"]) for reply in detail["replies"]] == [
        ({"kind": "member", "name": "Sanne Jansen"}, "Welke alinea?", "2026-11-02T09:00:00Z"),
        ({"kind": "giver", "name": None}, "De derde.\nMet\tinsprong.", "2026-11-02T09:05:00Z"),
        ({"kind": "member", "name": None}, "Aangepast.", "2026-11-02T10:00:00Z"),
    ]
    assert detail["replyCount"] == 3
    assert detail["author"]["email"] is None


def test_the_contract_names_the_selector_a_text_item_is_anchored_by():
    """A text item's TextPositionSelector counts UTF-16 code units over the
    raw body text, which the W3C model does not allow: an integration that
    takes it for the standard one marks the wrong characters once an emoji
    comes before the selection. The contract says so, in both languages."""
    english = english_schema()
    dutch = localise(english, "nl")

    def anchor(schema: dict) -> str:
        return schema["components"]["schemas"]["FeedbackDetail"]["properties"]["anchor"]["description"]

    assert "For a `text` item, anchor by the `TextQuoteSelector`." in anchor(english)
    assert "it counts UTF-16 code units, not code points" in anchor(english)
    assert "Veranker een `text`-item op zijn `TextQuoteSelector`." in anchor(dutch)
    assert "hij telt UTF-16-code-units in plaats van codepoints" in anchor(dutch)


def test_the_contract_says_a_reopened_item_falls_under_the_term_of_open_items():
    """Reopening ends the term of a resolved item, but the term of an open one
    counts from when it came in, so an old item reopened goes at the next
    nightly cleanup. The status route says so, in both languages, where it
    used to say that reopening stops the deletion."""
    english = english_schema()
    dutch = localise(english, "nl")

    def status(schema: dict) -> str:
        return schema["paths"]["/-/api/v1/sites/{group_slug}/{site_slug}/feedback/{item_id}/status"]["put"][
            "description"
        ]

    assert (
        "an open item is deleted once the retention period for open items, counted from the moment it came in, "
        "has passed: a reopened item older than that is deleted at the next nightly cleanup."
    ) in status(english)
    assert "stops that" not in status(english)
    assert (
        "een open item wordt verwijderd zodra de bewaartermijn voor open items, gerekend vanaf het moment dat het "
        "binnenkwam, voorbij is: een heropend item dat ouder is, wordt bij de eerstvolgende nachtelijke opruiming "
        "verwijderd."
    ) in status(dutch)
    assert "stopt dat" not in status(dutch)


async def test_an_unknown_item_is_404(client, app, world):
    _login(client, app, world.reader)

    response = await client.get(f"{FEEDBACK}/{uuid.uuid4()}")

    _problem(response, 404, "FEEDBACK_NOT_FOUND")


async def test_an_item_of_another_site_is_404_at_this_address(client, app, factory, world):
    """The admin of the group has a role on both sites: the item is still only
    reachable at the address of its own site."""
    item = await _item(factory, world.other)
    _login(client, app, world.admin)

    assert (await client.get(f"{BASE}/sites/team/ander/feedback/{item.id}")).status_code == 200
    _problem(await client.get(f"{FEEDBACK}/{item.id}"), 404, "FEEDBACK_NOT_FOUND")


async def test_an_id_that_is_no_uuid_is_422(client, app, world):
    _login(client, app, world.reader)

    response = await client.get(f"{FEEDBACK}/geen-uuid")

    _problem(response, 422, None)


# -- The highlight fragment -------------------------------------------------------


async def _highlight(client, app, factory, world, **values) -> str | None:
    item = await _item(factory, world.site, **values)
    _login(client, app, world.reader)
    response = await client.get(f"{FEEDBACK}/{item.id}")
    assert response.status_code == 200, response.text
    return response.json()["highlightUrl"]


async def test_a_point_is_highlighted_by_selector_and_place(client, app, factory, world):
    url = await _highlight(client, app, factory, world, kind=FeedbackKind.POINT, anchor=POINT_ANCHOR)

    assert url.startswith(f"{CONTENT_BASE_URL}/team/docs/docs/#toon-feedback=")
    assert _fragment(url) == {"k": "point", "s": "main > p:nth-of-type(3)", "x": 0.5, "y": 0.25, "px": 640, "py": 1210}


async def test_a_text_is_highlighted_by_selector_and_quote(client, app, factory, world):
    url = await _highlight(client, app, factory, world, kind=FeedbackKind.TEXT, quote="2,4 miljoen", anchor=TEXT_ANCHOR)

    assert _fragment(url) == {"k": "text", "s": "main > p:nth-of-type(3)", "q": "2,4 miljoen"}


async def test_a_page_item_has_no_highlight(client, app, factory, world):
    assert await _highlight(client, app, factory, world) is None


@pytest.mark.parametrize(
    ("kind", "anchor"),
    [
        (FeedbackKind.POINT, {"selectors": "main", "point": {"x": "0.5", "y": True}}),
        (
            FeedbackKind.TEXT,
            {"selectors": [1, {"type": "CssSelector", "value": 5}, {"type": "TextQuoteSelector"}], "point": None},
        ),
        (FeedbackKind.POINT, JSON.NULL),
    ],
    ids=["no-list-and-no-numbers", "no-usable-selector", "json-null"],
)
async def test_an_anchor_without_anything_usable_has_no_highlight(client, app, factory, world, kind, anchor):
    """The widget stores the anchor; whatever shape it has, the detail answers
    and only leaves out what it cannot read."""
    quote = "x" if kind == FeedbackKind.TEXT else None
    item = await _item(factory, world.site, kind=kind, anchor=anchor, quote=quote)
    _login(client, app, world.reader)

    detail = (await client.get(f"{FEEDBACK}/{item.id}")).json()

    assert detail["highlightUrl"] is None
    assert detail["anchor"] == (None if anchor is JSON.NULL else anchor)


async def test_a_long_quote_is_cut_until_the_fragment_fits(client, app, factory, world):
    exact = "€" * 1000
    anchor = {"selectors": [{"type": "TextQuoteSelector", "exact": exact}, {"type": "CssSelector", "value": "p"}]}

    url = await _highlight(client, app, factory, world, kind=FeedbackKind.TEXT, quote="euro", anchor=anchor)

    assert len(url.partition("#")[2]) <= 2048
    fragment = _fragment(url)
    assert fragment["s"] == "p"
    assert fragment["q"]
    assert exact.startswith(fragment["q"])
    assert len(fragment["q"]) < len(exact)


async def test_a_selector_too_long_for_the_fragment_is_left_out_whole(client, app, factory, world):
    """Cut short, a selector could name another element; the place on the
    page still finds the spot."""
    anchor = {"selectors": [{"type": "CssSelector", "value": "ä" * 1000}], "point": POINT_ANCHOR["point"]}

    url = await _highlight(client, app, factory, world, kind=FeedbackKind.POINT, anchor=anchor)

    assert len(url.partition("#")[2]) <= 2048
    assert _fragment(url) == {"k": "point", "x": 0.5, "y": 0.25, "px": 640, "py": 1210}


# -- The screenshot -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("media_type", "extension"), [("image/png", "png"), ("image/jpeg", "jpg"), ("image/webp", "webp")]
)
async def test_the_screenshot_is_the_stored_image_with_strict_headers(
    client, app, factory, world, media_type, extension
):
    item = await _item(factory, world.site)
    data = b"beeld-" + extension.encode() + bytes(range(256))
    await _screenshot(factory, item, data=data, media_type=media_type)
    _login(client, app, world.reader)

    response = await client.get(f"{FEEDBACK}/{item.id}/screenshot")

    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == media_type
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert response.headers["content-disposition"] == f'inline; filename="feedback-{item.id}.{extension}"'


async def test_an_item_without_a_screenshot_is_404(client, app, factory, world):
    item = await _item(factory, world.site)
    _login(client, app, world.reader)

    _problem(await client.get(f"{FEEDBACK}/{item.id}/screenshot"), 404, "FEEDBACK_NOT_FOUND")
    _problem(await client.get(f"{FEEDBACK}/{uuid.uuid4()}/screenshot"), 404, "FEEDBACK_NOT_FOUND")


async def test_the_screenshot_of_another_sites_item_is_404_here(client, app, factory, world):
    item = await _item(factory, world.other)
    await _screenshot(factory, item)
    _login(client, app, world.admin)

    _problem(await client.get(f"{FEEDBACK}/{item.id}/screenshot"), 404, "FEEDBACK_NOT_FOUND")


@pytest.mark.parametrize("route", ["list", "detail", "status", "reply"])
async def test_no_browser_keeps_an_answer_that_carries_feedback(client, app, factory, world, route):
    """The comment, a typed name, the verified address of the submitter and
    the replies would otherwise stay in a team member's HTTP cache after the
    submitter deletes the item or the cleanup purges it. The screenshot says
    `private, no-store` of its own (above)."""
    item = await _account_item(factory, world.site)
    headers = _login(client, app, world.editor)

    response = await _call(client, route, item, headers)

    assert response.status_code == ROUTES[route][3], response.text
    assert response.headers["cache-control"] == "no-store"


# -- The status -------------------------------------------------------------------


async def _set_status(client, item: FeedbackItem, status: str, headers: dict[str, str]):
    return await client.put(f"{FEEDBACK}/{item.id}/status", json={"status": status}, headers=headers)


async def test_marking_seen_records_who_and_when_and_answers_the_detail(client, app, factory, world):
    item = await _account_item(factory, world.site)
    headers = _login(client, app, world.editor)
    before = datetime.now(UTC)

    response = await _set_status(client, item, "seen", headers)

    assert response.status_code == 200, response.text
    detail = response.json()
    assert (detail["status"], detail["statusChangedBy"]) == ("seen", "Sanne Jansen")
    assert detail["author"]["email"] == "jan.jansen@example.nl"
    assert "replies" in detail
    stored = await _stored(factory, item)
    assert stored.status == FeedbackStatus.SEEN
    assert (stored.status_changed_by, stored.resolved_at) == (world.editor.id, None)
    assert stored.status_changed_at >= before
    [row] = await _rows(factory, "feedback_status")
    assert row.result == "allowed"
    assert row.actor_pseudonym == pseudonymise(PEPPER, "redacteur")
    assert row.refs == {"group": "team", "site": "docs", "item": str(item.id), "from": "new", "to": "seen"}


async def test_resolving_sets_the_resolved_moment_and_reopening_clears_it(client, app, factory, world):
    item = await _item(factory, world.site, status=FeedbackStatus.SEEN)
    headers = _login(client, app, world.editor)

    resolved = await _set_status(client, item, "resolved", headers)

    assert resolved.json()["status"] == "resolved"
    stored = await _stored(factory, item)
    assert stored.resolved_at is not None
    assert stored.resolved_at == stored.status_changed_at

    reopened = await _set_status(client, item, "new", headers)

    assert reopened.json()["status"] == "new"
    stored = await _stored(factory, item)
    assert (stored.status, stored.resolved_at) == (FeedbackStatus.NEW, None)
    assert [(row.refs["from"], row.refs["to"]) for row in await _rows(factory, "feedback_status")] == [
        ("seen", "resolved"),
        ("resolved", "new"),
    ]


async def test_the_same_status_changes_nothing_and_writes_no_audit_row(client, app, factory, world):
    moment = datetime(2026, 11, 1, 9, tzinfo=UTC)
    item = await _item(
        factory, world.site, status=FeedbackStatus.SEEN, status_changed_at=moment, status_changed_by=world.admin.id
    )
    headers = _login(client, app, world.editor)

    response = await _set_status(client, item, "seen", headers)

    assert response.status_code == 200
    assert (response.json()["statusChangedBy"], response.json()["statusChangedAt"]) == (
        "beheerder@example.nl",
        "2026-11-01T09:00:00Z",
    )
    stored = await _stored(factory, item)
    assert (stored.status_changed_by, stored.status_changed_at) == (world.admin.id, moment)
    assert await _rows(factory, "feedback_status") == []


async def test_a_status_for_an_unknown_item_is_404_and_audited_as_refused(client, app, factory, world):
    headers = _login(client, app, world.editor)

    response = await client.put(f"{FEEDBACK}/{uuid.uuid4()}/status", json={"status": "seen"}, headers=headers)

    _problem(response, 404, "FEEDBACK_NOT_FOUND")
    [row] = await _rows(factory, "admin_access")
    assert (row.result, row.reason_code) == ("refused", "FEEDBACK_NOT_FOUND")


async def test_the_status_of_another_sites_item_is_404_and_stays(client, app, factory, world):
    item = await _item(factory, world.other)
    headers = _login(client, app, world.admin)

    _problem(await _set_status(client, item, "resolved", headers), 404, "FEEDBACK_NOT_FOUND")
    assert (await _stored(factory, item)).status == FeedbackStatus.NEW


@pytest.mark.parametrize("body", [{"status": "open"}, {"status": "afgehandeld"}, {}])
async def test_a_status_that_is_none_of_the_three_is_422(client, app, factory, world, body):
    item = await _item(factory, world.site)
    headers = _login(client, app, world.editor)

    response = await client.put(f"{FEEDBACK}/{item.id}/status", json=body, headers=headers)

    _problem(response, 422, None)
    assert (await _stored(factory, item)).status == FeedbackStatus.NEW


async def test_the_cli_marks_and_the_audit_row_says_cli(client, factory, world):
    item = await _item(factory, world.site)
    token = await _token(factory, world.editor)

    response = await _set_status(client, item, "resolved", _bearer(token))

    assert response.status_code == 200, response.text
    [row] = await _rows(factory, "feedback_status")
    async with factory() as db:
        session_id = str(await db.scalar(select(CliSession.id).where(CliSession.member_id == world.editor.id)))
    assert row.refs == {
        "group": "team",
        "site": "docs",
        "item": str(item.id),
        "from": "new",
        "to": "resolved",
        "via": "cli",
        "cli_session": session_id,
    }


# -- Replies ------------------------------------------------------------------------


async def _post_reply(client, item: FeedbackItem, body: object, headers: dict[str, str]):
    return await client.post(f"{FEEDBACK}/{item.id}/replies", json={"body": body}, headers=headers)


async def test_a_reply_is_stored_with_the_members_name_and_answered_with_201(client, app, factory, world):
    item = await _item(factory, world.site)
    headers = _login(client, app, world.editor)

    response = await _post_reply(client, item, "  Dank je, dit is aangepast.\n\nGroet,\tSanne  ", headers)

    assert response.status_code == 201, response.text
    reply = response.json()
    assert reply["author"] == {"kind": "member", "name": "Sanne Jansen"}
    assert reply["body"] == "Dank je, dit is aangepast.\n\nGroet,\tSanne"
    assert reply["createdAt"].endswith("Z")
    async with factory() as db:
        stored = await db.get(FeedbackReply, uuid.UUID(reply["id"]))
    assert (stored.item_id, stored.author_kind, stored.member_id, stored.author_name) == (
        item.id,
        FeedbackReplyAuthor.MEMBER,
        world.editor.id,
        "Sanne Jansen",
    )
    assert (await _stored(factory, item)).status == FeedbackStatus.NEW
    [row] = await _rows(factory, "feedback_reply")
    assert row.refs == {"group": "team", "site": "docs", "item": str(item.id)}


async def test_a_reply_by_a_member_without_a_name_carries_none(client, app, factory, world):
    item = await _item(factory, world.site)
    headers = _login(client, app, world.admin)

    response = await _post_reply(client, item, "Opgepakt.", headers)

    assert response.json()["author"] == {"kind": "member", "name": None}


async def test_a_long_member_name_is_cut_to_what_the_reply_holds(client, app, factory, world):
    async with factory() as db:
        await db.execute(update(Member).where(Member.id == world.editor.id).values(name="N" * 250))
        await db.commit()
    item = await _item(factory, world.site)
    headers = _login(client, app, world.editor)

    response = await _post_reply(client, item, "Opgepakt.", headers)

    assert response.json()["author"]["name"] == "N" * 200


async def test_the_reply_shows_in_the_count_and_the_thread(client, app, factory, world):
    item = await _item(factory, world.site)
    headers = _login(client, app, world.editor)
    await _post_reply(client, item, "Eerste.", headers)
    await _post_reply(client, item, "Tweede.", headers)

    [listed] = (await _listed(client))["items"]
    detail = (await client.get(f"{FEEDBACK}/{item.id}")).json()

    assert listed["replyCount"] == 2
    assert sorted(reply["body"] for reply in detail["replies"]) == ["Eerste.", "Tweede."]


async def test_a_reply_of_exactly_5000_characters_after_trimming_is_taken(client, app, factory, world):
    item = await _item(factory, world.site)
    headers = _login(client, app, world.editor)

    response = await _post_reply(client, item, "  " + "a" * 5000 + "\n", headers)

    assert response.status_code == 201
    assert len(response.json()["body"]) == 5000


async def test_a_reply_with_a_code_point_the_servers_tables_do_not_know_is_taken(client, app, factory, world):
    """An emoji newer than the server's Unicode tables is unassigned to it."""
    assert unicodedata.category(UNASSIGNED) == "Cn"
    item = await _item(factory, world.site)
    headers = _login(client, app, world.editor)

    response = await _post_reply(client, item, f"Opgelost {UNASSIGNED}", headers)

    assert response.status_code == 201, response.text
    async with factory() as db:
        stored = await db.get(FeedbackReply, uuid.UUID(response.json()["id"]))
    assert stored.body == f"Opgelost {UNASSIGNED}"


async def test_a_reply_keeps_the_two_joiners_writing_needs(client, app, factory, world):
    """An emoji sequence from the picker is held together by the zero-width
    joiner, and Persian keeps letters apart with the zero-width non-joiner."""
    item = await _item(factory, world.site)
    headers = _login(client, app, world.editor)
    text = f"Opgelost, dank je {JOINED_EMOJI} {PERSIAN}"

    response = await _post_reply(client, item, text, headers)

    assert response.status_code == 201, response.text
    assert response.json()["body"] == text
    async with factory() as db:
        stored = await db.get(FeedbackReply, uuid.UUID(response.json()["id"]))
    assert stored.body == text


@pytest.mark.parametrize("char", list(REFUSED_FORMAT.values()), ids=list(REFUSED_FORMAT))
async def test_a_reply_with_any_other_format_character_is_422(client, app, factory, world, char):
    item = await _item(factory, world.site)
    headers = _login(client, app, world.editor)

    response = await _post_reply(client, item, f"Opgelost{char}, dank je", headers)

    _problem(response, 422, "FIELD_CONTROL_CHARACTERS")
    assert await _count(factory, FeedbackReply) == 0


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("", "FIELD_EMPTY"),
        (" \n\t ", "FIELD_EMPTY"),
        ("a" * 5001, "FIELD_TOO_LONG"),
        ("nul\x00teken", "FIELD_CONTROL_CHARACTERS"),
        ("wagen\rterug", "FIELD_CONTROL_CHARACTERS"),
        ("bel\x07teken", "FIELD_CONTROL_CHARACTERS"),
        ("c1\x85teken", "FIELD_CONTROL_CHARACTERS"),
        ("om‮draaien", "FIELD_CONTROL_CHARACTERS"),
        ("zero​width", "FIELD_CONTROL_CHARACTERS"),
        ("eigengebruik", "FIELD_CONTROL_CHARACTERS"),
    ],
    ids=[
        "empty",
        "blank",
        "too-long",
        "nul",
        "carriage-return",
        "bell",
        "c1",
        "rtl-override",
        "zero-width",
        "private-use",
    ],
)
async def test_a_reply_body_outside_the_rule_is_422_and_stores_nothing(client, app, factory, world, body, code):
    item = await _item(factory, world.site)
    headers = _login(client, app, world.editor)

    response = await _post_reply(client, item, body, headers)

    problem = _problem(response, 422, code)
    assert "'body'" in problem["detail"]
    assert await _count(factory, FeedbackReply) == 0
    assert await _rows(factory, "feedback_reply") == []


async def test_a_lone_surrogate_is_422_not_a_500(client, app, factory, world):
    """JSON can carry one as an escape; the database could not store it."""
    item = await _item(factory, world.site)
    headers = {**_login(client, app, world.editor), "Content-Type": "application/json"}

    response = await client.post(
        f"{FEEDBACK}/{item.id}/replies", content=b'{"body": "half \\ud800 teken"}', headers=headers
    )

    _problem(response, 422, "FIELD_CONTROL_CHARACTERS")
    assert await _count(factory, FeedbackReply) == 0


async def test_a_reply_body_that_is_no_text_is_422(client, app, factory, world):
    item = await _item(factory, world.site)
    headers = _login(client, app, world.editor)

    _problem(await _post_reply(client, item, ["lijst"], headers), 422, None)


async def test_a_reply_to_an_unknown_or_foreign_item_is_404(client, app, factory, world):
    foreign = await _item(factory, world.other)
    headers = _login(client, app, world.admin)

    _problem(
        await client.post(f"{FEEDBACK}/{uuid.uuid4()}/replies", json={"body": "Hallo."}, headers=headers),
        404,
        "FEEDBACK_NOT_FOUND",
    )
    _problem(await _post_reply(client, foreign, "Hallo.", headers), 404, "FEEDBACK_NOT_FOUND")
    assert await _count(factory, FeedbackReply) == 0


async def _replies(factory, item: FeedbackItem, author: FeedbackReplyAuthor, count: int) -> None:
    async with factory() as db:
        db.add_all(FeedbackReply(item_id=item.id, author_kind=author, body="Eerder.") for _ in range(count))
        await db.commit()


async def test_a_reply_past_the_cap_in_all_is_409_and_stores_nothing(client, app, factory, world):
    item = await _account_item(factory, world.site)
    await _replies(factory, item, FeedbackReplyAuthor.GIVER, 10)
    await _replies(factory, item, FeedbackReplyAuthor.MEMBER, FEEDBACK_MAX_REPLIES - 11)
    headers = _login(client, app, world.editor)

    assert (await _post_reply(client, item, "De laatste.", headers)).status_code == 201
    full = await _post_reply(client, item, "Eén te veel.", {**headers, "Accept-Language": "nl"})

    problem = _problem(full, 409, "FEEDBACK_THREAD_FULL")
    assert problem["detail"] == "Bij deze feedback kunnen geen reacties meer bij."
    assert await _count(factory, FeedbackReply, item_id=item.id) == FEEDBACK_MAX_REPLIES
    assert len(await _rows(factory, "feedback_reply")) == 1


async def test_the_team_answers_a_submitter_who_used_up_their_own_replies(client, app, factory, world):
    """The cap on the submitter's replies is theirs alone."""
    item = await _account_item(factory, world.site)
    await _replies(factory, item, FeedbackReplyAuthor.GIVER, FEEDBACK_MAX_GIVER_REPLIES)
    headers = _login(client, app, world.editor)

    assert (await _post_reply(client, item, "Dank voor al je reacties.", headers)).status_code == 201


async def test_a_burst_of_replies_stops_exactly_at_the_cap(client, app, factory, world, migrated_dsn):
    """Under the item's lock, which the widget's replies take too, the count
    and the insert of one reply cannot interleave with another's. A commit
    that waits makes the race certain rather than likely."""
    item = await _item(factory, world.site)
    await _replies(factory, item, FeedbackReplyAuthor.MEMBER, FEEDBACK_MAX_REPLIES - 5)
    headers = _login(client, app, world.editor)
    engine = create_async_engine(migrated_dsn, poolclass=NullPool)
    app.state.session_factory = async_sessionmaker(engine, class_=SlowCommitSession, expire_on_commit=False)
    try:
        responses = await asyncio.gather(
            *(_post_reply(client, item, f"Reactie {number}.", headers) for number in range(12))
        )
    finally:
        await engine.dispose()

    assert sorted(response.status_code for response in responses) == [201] * 5 + [409] * 7
    assert await _count(factory, FeedbackReply, item_id=item.id) == FEEDBACK_MAX_REPLIES


async def test_the_cli_replies_and_the_audit_row_says_cli_without_the_text(client, factory, world):
    item = await _item(factory, world.site)
    token = await _token(factory, world.editor)

    response = await client.post(
        f"{FEEDBACK}/{item.id}/replies", json={"body": "Vertrouwelijk antwoord."}, headers=_bearer(token)
    )

    assert response.status_code == 201, response.text
    [row] = await _rows(factory, "feedback_reply")
    assert row.refs["via"] == "cli"
    assert "Vertrouwelijk" not in json.dumps(row.refs)


# -- Deleting ---------------------------------------------------------------------


async def test_deleting_takes_the_replies_and_the_screenshot_along(client, app, factory, world):
    item = await _item(factory, world.site)
    await _screenshot(factory, item)
    await _reply(factory, item, author_kind=FeedbackReplyAuthor.GIVER, body="Nog iets.")
    kept = await _item(factory, world.site)
    headers = _login(client, app, world.admin)

    response = await client.delete(f"{FEEDBACK}/{item.id}", headers=headers)

    assert response.status_code == 204
    assert response.content == b""
    assert await _stored(factory, item) is None
    assert await _count(factory, FeedbackReply, item_id=item.id) == 0
    assert await _count(factory, FeedbackScreenshot, item_id=item.id) == 0
    assert await _stored(factory, kept) is not None
    [row] = await _rows(factory, "feedback_delete")
    assert row.refs == {"group": "team", "site": "docs", "item": str(item.id)}


async def test_deleting_an_unknown_or_foreign_item_is_404(client, app, factory, world):
    foreign = await _item(factory, world.other)
    headers = _login(client, app, world.admin)

    _problem(await client.delete(f"{FEEDBACK}/{uuid.uuid4()}", headers=headers), 404, "FEEDBACK_NOT_FOUND")
    _problem(await client.delete(f"{FEEDBACK}/{foreign.id}", headers=headers), 404, "FEEDBACK_NOT_FOUND")
    assert await _stored(factory, foreign) is not None
    assert await _rows(factory, "feedback_delete") == []


async def test_feedback_goes_with_its_site(client, app, factory, world):
    item = await _item(factory, world.site)
    await _screenshot(factory, item)
    await _reply(factory, item, author_kind=FeedbackReplyAuthor.GIVER, body="Nog iets.")
    headers = _login(client, app, world.admin)

    response = await client.delete(f"{BASE}/sites/team/docs", headers=headers)

    assert response.status_code == 204
    assert await _count(factory, FeedbackItem) == 0
    assert await _count(factory, FeedbackReply) == 0
    assert await _count(factory, FeedbackScreenshot) == 0


# -- The settings and SiteOut -----------------------------------------------------


SETTINGS = f"{BASE}/sites/team/docs/feedback-settings"


async def test_the_settings_are_stored_answered_and_audited(client, app, factory, world):
    headers = _login(client, app, world.admin)

    response = await client.put(
        SETTINGS, json={"enabled": True, "identity": "anonymous", "live": True}, headers=headers
    )

    assert response.status_code == 200, response.text
    site = response.json()
    assert (site["slug"], site["feedbackEnabled"], site["feedbackIdentity"], site["feedbackLive"]) == (
        "docs",
        True,
        "anonymous",
        True,
    )
    async with factory() as db:
        stored = await db.get(Site, world.site.id)
    assert (stored.feedback_enabled, stored.feedback_identity, stored.feedback_live) == (
        True,
        FeedbackIdentity.ANONYMOUS,
        True,
    )
    [row] = await _rows(factory, "site_feedback")
    assert row.result == "allowed"
    assert row.refs == {"group": "team", "site": "docs", "enabled": True, "identity": "anonymous", "live": True}


async def test_switching_off_keeps_the_choices_and_the_feedback(client, app, factory, world):
    await _item(factory, world.site)
    headers = _login(client, app, world.admin)
    await client.put(SETTINGS, json={"enabled": True, "identity": "name", "live": True}, headers=headers)

    response = await client.put(SETTINGS, json={"enabled": False, "identity": "name", "live": True}, headers=headers)

    assert (response.json()["feedbackEnabled"], response.json()["feedbackIdentity"]) == (False, "name")
    assert response.json()["openFeedbackCount"] == 1
    assert len(await _rows(factory, "site_feedback")) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"enabled": True, "identity": "everyone", "live": False},
        {"enabled": True, "live": False},
        {"identity": "account", "live": False},
        {"enabled": True, "identity": "account"},
        {"enabled": "ja", "identity": "account", "live": False},
    ],
)
async def test_settings_outside_the_schema_are_422_and_change_nothing(client, app, factory, world, body):
    headers = _login(client, app, world.admin)

    _problem(await client.put(SETTINGS, json=body, headers=headers), 422, None)
    async with factory() as db:
        stored = await db.get(Site, world.site.id)
    assert (stored.feedback_enabled, stored.feedback_identity, stored.feedback_live) == (
        False,
        FeedbackIdentity.ACCOUNT,
        False,
    )


def _site_fields(site: dict) -> tuple:
    return site["feedbackEnabled"], site["feedbackIdentity"], site["feedbackLive"], site["openFeedbackCount"]


async def test_overview_and_group_detail_carry_the_feedback_fields_per_site(client, app, factory, world):
    """One batched count serves every site: new and seen count, resolved does
    not, and no site borrows the count of another."""
    async with factory() as db:
        await db.execute(
            update(Site)
            .where(Site.id == world.site.id)
            .values(feedback_enabled=True, feedback_identity=FeedbackIdentity.NAME, feedback_live=True)
        )
        await db.commit()
    await _item(factory, world.site)
    await _item(factory, world.site, status=FeedbackStatus.SEEN)
    await _item(factory, world.site, status=FeedbackStatus.RESOLVED, resolved_at=datetime.now(UTC))
    await _item(factory, world.other)
    _login(client, app, world.reader)

    overview = (await client.get(f"{BASE}/overview")).json()
    detail = (await client.get(f"{BASE}/groups/team")).json()

    for sites in (overview["groups"][0]["sites"], detail["sites"]):
        by_slug = {site["slug"]: _site_fields(site) for site in sites}
        assert by_slug == {"ander": (False, "account", False, 1), "docs": (True, "name", True, 2)}


async def test_a_new_site_starts_with_feedback_off(client, app, world):
    headers = _login(client, app, world.editor)

    response = await client.post(f"{BASE}/groups/team/sites", json={"title": "Nieuw", "slug": "nieuw"}, headers=headers)

    assert response.status_code == 201, response.text
    assert _site_fields(response.json()) == (False, "account", False, 0)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("title", {"title": "Documentatie"}),
        ("access", {"base": "sso"}),
        ("external-sources", {"externalSources": False}),
        ("sandbox", {"sandbox": False}),
        ("live-versions-kept", {"liveVersionsKept": 3}),
    ],
)
async def test_every_site_setting_answers_with_the_feedback_fields(client, app, factory, world, path, body):
    await _item(factory, world.site)
    headers = _login(client, app, world.admin)

    response = await client.put(f"{BASE}/sites/team/docs/{path}", json=body, headers=headers)

    assert response.status_code == 200, response.text
    assert _site_fields(response.json()) == (False, "account", False, 1)

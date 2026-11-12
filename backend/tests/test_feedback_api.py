"""Tests for the feedback widget API on the content host
(serving/feedback_api.py, its routes in serving/feedback_routes.py and the
token checks in serving/feedback_access.py), against the real PostgreSQL test
container.

Refusals first: every ground for FEEDBACK_UNAVAILABLE answers the same bytes
per language, every other code answers its exact status and body, every
validation refusal is a 422 and never a 500, and the widget header set rides
on every answer of `/-/feedback/v1/`. Then what a crafted body costs, what an
accepted item stores, the quotas under concurrency, the write counters, the
giver's own items, replies and deletes, the bundle and the catch-all. The HTTP-level access matrix lives
in test_feedback_matrix.py and the consent pages in test_feedback_consent.py;
both build on the helpers here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import tracemalloc
import unicodedata
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from helpers_audit import install_audit_recorder
from helpers_oidc import MockIdP, make_client_jwk, make_oidc_client
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request
from test_feedback_images import JPEG_SIPS, PNG_ANIMATED, PNG_FILE, WEBP_LOSSY, png_header

from toon import messages
from toon.access.decision import FeedbackPolicy, allow
from toon.auth.sessions import CONTENT_ANCHOR_COOKIE, CONTENT_SESSION_COOKIE, Session, SessionKind, SessionStore, sign
from toon.config import FEEDBACK_BODY_ALLOWANCE, Settings
from toon.constants import (
    FEEDBACK_MAX_GIVER_REPLIES,
    FEEDBACK_MAX_REPLIES,
    AccessBase,
    AccessPolicy,
    FeedbackAuthor,
    FeedbackIdentity,
    FeedbackKind,
    FeedbackReplyAuthor,
    FeedbackStatus,
)
from toon.messages import Msg
from toon.models.feedback import FeedbackItem, FeedbackReply, FeedbackScreenshot
from toon.models.identity import Group, Member, MemberStatus
from toon.models.publication import Preview, Site, Version, VersionTarget
from toon.ratelimit import FEEDBACK_API_HEADERS, InMemoryCounter
from toon.serving import feedback_access, feedback_api
from toon.serving.feedback_api import PREFLIGHT_HEADERS, BodyBudget, WriteCounter, write_counter_sweep
from toon.serving.feedback_routes import router as feedback_router
from toon.serving.feedback_token import TOKEN_LIFETIME_S, mint_consent_nonce, mint_page_token
from toon.serving.feedback_widget import WidgetBundle
from toon.serving.response import NEUTRAL_404_BODY

BASE_URL = "https://toon.example"
ADMIN_URL = "https://beheer.toon.example"
ITEMS = "/-/feedback/v1/items"
# A fixed id: every xdist worker has to collect the same test ids.
AN_ITEM = f"{ITEMS}/0b9f4c3e-1d2a-4e5f-8a9b-0c1d2e3f4a5b"
ROOT = "/aurora/site/"
PREVIEW_ROOT = "/aurora/site/_preview/pr-1/"

SECRET = "sessie-geheim-van-minstens-32-bytes!"


def make_settings(content_root: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "db_url": "postgresql+asyncpg://ongebruikt:ongebruikt@localhost:5432/ongebruikt",
        "content_root": content_root,
        "oidc_issuer": "https://idp.example",
        "oidc_client_id": "toon-client",
        "oidc_client_private_jwk": "{}",
        "oidc_required_acr": "urn:acr:hoog",
        "session_secret": SECRET,
        "audit_pepper": "audit-pepper-van-minstens-32-bytes!!",
        "audit_ip_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        "base_url": ADMIN_URL,
        "content_base_url": BASE_URL,
        "environment": "dev",
    }
    values.update(overrides)
    return Settings(**values)


def make_app(settings: Settings, factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    """The feedback routes as main.py mounts them, with what they read from
    app.state; no middleware, so an answer is exactly what a route built."""
    app = FastAPI()
    app.state.settings = settings
    app.state.session_store = SessionStore()
    app.state.session_factory = factory
    app.state.feedback_author_writes = WriteCounter()
    app.state.feedback_site_writes = WriteCounter()
    app.state.feedback_bodies = BodyBudget()
    app.include_router(feedback_router)
    return app


def client_for(app: FastAPI, base_url: str = BASE_URL, *, address: str = "127.0.0.1") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(address, 123))
    return httpx.AsyncClient(transport=transport, base_url=base_url, follow_redirects=False)


@dataclass
class World:
    group_id: uuid.UUID
    site_id: uuid.UUID
    live_version_id: uuid.UUID
    preview_version_id: uuid.UUID
    other_site_id: uuid.UUID
    other_live_version_id: uuid.UUID
    member_id: uuid.UUID


async def seed(factory: async_sessionmaker[AsyncSession]) -> World:
    """Group aurora with two public sites that take feedback on live and
    previews from anyone: `site` with preview pr-1, and `ander`."""
    async with factory() as db:
        group = Group(slug="aurora", name="Aurora", default_access_base=AccessBase.PUBLIC)
        member = Member(
            sso_subject="lid-actief", email="lid@example.org", name="Lid Actief", status=MemberStatus.ACTIVE
        )
        db.add_all([group, member])
        await db.flush()
        feedback = {"feedback_enabled": True, "feedback_identity": FeedbackIdentity.ANONYMOUS, "feedback_live": True}
        site = Site(group_id=group.id, slug="site", title="Site", access_base=AccessBase.PUBLIC, **feedback)
        other = Site(group_id=group.id, slug="ander", title="Ander", access_base=AccessBase.PUBLIC, **feedback)
        db.add_all([site, other])
        await db.flush()
        live = Version(site_id=site.id, target=VersionTarget.LIVE, member_id=member.id)
        preview = Version(site_id=site.id, target=VersionTarget.PREVIEW, member_id=member.id)
        other_live = Version(site_id=other.id, target=VersionTarget.LIVE, member_id=member.id)
        db.add_all([live, preview, other_live])
        await db.flush()
        site.live_version_id = live.id
        other.live_version_id = other_live.id
        db.add(Preview(site_id=site.id, ref="pr-1", version_id=preview.id))
        await db.commit()
        return World(group.id, site.id, live.id, preview.id, other.id, other_live.id, member.id)


@dataclass
class Environment:
    factory: async_sessionmaker[AsyncSession]
    app: FastAPI
    world: World
    tmp_path: Path
    clients: list[httpx.AsyncClient] = field(default_factory=list)

    def app_with(self, **overrides: object) -> FastAPI:
        """Another app on the same data, with other settings."""
        return make_app(make_settings(self.tmp_path, **overrides), self.factory)

    @property
    def store(self) -> SessionStore:
        return self.app.state.session_store


@asynccontextmanager
async def rolled_back(dsn: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Every session on one connection inside a transaction that is rolled
    back: a commit releases a savepoint, and nothing leaks between tests."""
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                yield async_sessionmaker(
                    bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def open_environment(factory: async_sessionmaker[AsyncSession], tmp_path: Path) -> Environment:
    world = await seed(factory)
    return Environment(factory, make_app(make_settings(tmp_path), factory), world, tmp_path)


@pytest_asyncio.fixture
async def savepoints(migrated_dsn: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with rolled_back(migrated_dsn) as factory:
        yield factory


class SlowCommitSession(AsyncSession):
    """Waits before every commit. Under the site's lock that only makes each
    create take longer; without it, every create of a burst would have
    counted before the first one committed, which is the race the lock is
    there to close, made certain instead of likely."""

    async def commit(self) -> None:
        await asyncio.sleep(0.05)
        await super().commit()


@pytest_asyncio.fixture
async def committed(migrated_dsn: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Real connections and real commits, for requests that run at the same
    time; conftest empties the tables before the next database test."""
    engine = create_async_engine(migrated_dsn, pool_size=20, max_overflow=0)
    try:
        yield async_sessionmaker(engine, class_=SlowCommitSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def environment(savepoints, tmp_path: Path) -> Environment:
    return await open_environment(savepoints, tmp_path)


@pytest_asyncio.fixture
async def client(environment: Environment) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(environment.app) as c:
        yield c


# --- Helpers -----------------------------------------------------------------------


def consented_session(
    app: FastAPI,
    *site_ids: uuid.UUID,
    sub: str = "kijker",
    email: str | None = "Kijker@Example.org",
    email_verified: bool = True,
    name: str | None = "Kijker Jansen",
    refresh_token: str | None = None,
) -> Session:
    """A content session whose visitor consented to account feedback on
    these sites."""
    store: SessionStore = app.state.session_store
    session = store.create_session(
        sub=sub,
        email=email,
        email_verified=email_verified,
        acr="urn:acr:hoog",
        name=name,
        kind=SessionKind.CONTENT,
        refresh_token=refresh_token,
    )
    for site_id in site_ids:
        store.consent_feedback(session.id, str(site_id))
    found = store.get_session(session.id)
    assert found is not None
    return found


def page_token(
    app: FastAPI,
    site_id: uuid.UUID,
    *,
    ref: str | None = None,
    version_id: uuid.UUID | None = None,
    session_id: str | None = None,
    key_id: str | None = None,
    now: int | None = None,
    access_session_id: str | None = None,
) -> str:
    return mint_page_token(
        app.state.settings.session_secret,
        site_id=site_id,
        kind="live" if ref is None else "preview",
        ref=ref,
        version_id=version_id,
        session_id=session_id,
        key_id=key_id,
        now=int(time.time()) if now is None else now,
        access_session_id=access_session_id,
    )


def auth(token: str, **headers: str) -> dict[str, str]:
    return {"Authorization": f"Toon-Feedback {token}", **headers}


def item(**changes: object) -> dict[str, object]:
    """A page item as the widget sends it, anonymous, on the live site."""
    base: dict[str, object] = {
        "kind": "page",
        "comment": "Klopt dit bedrag?",
        "page": {"path": f"{ROOT}docs/", "title": "Documentatie"},
        "anchor": None,
        "viewport": {"width": 1280, "height": 800, "dpr": 2},
        "author": {"kind": "anonymous"},
        "screenshot": {"region": None},
    }
    base.update(changes)
    return base


POINT_ANCHOR = {
    "selectors": [{"type": "CssSelector", "value": "main > p:nth-of-type(3)"}],
    "point": {"x": 0.25, "y": 0.5, "pageX": 340, "pageY": 1200.5},
}

TEXT_ANCHOR = {
    "selectors": [
        {"type": "TextPositionSelector", "start": 1200, "end": 1211},
        {"type": "CssSelector", "value": "main > p:nth-of-type(3)"},
        {"type": "TextQuoteSelector", "exact": "2,4 miljoen", "prefix": "begroting van ", "suffix": " euro voor"},
    ],
    "point": None,
}

# Planes 4 to 13 hold no character yet: this stands in for an emoji newer
# than the server's Unicode tables. Each test that uses it checks that it is
# still unassigned here.
UNASSIGNED = chr(0x50000)

# The two format characters writing needs: the zero-width joiner holds an
# emoji sequence together (woman technologist), and the zero-width
# non-joiner keeps two Persian letters apart ("mi-khaham", I want).
JOINED_EMOJI = "\U0001f469\u200d\U0001f4bb"
PERSIAN = "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645"

# Every other format character stays refused: the bidi controls, which
# reorder what follows them, the tag characters a language model reads and a
# screen does not show, and the invisible ones that only hide a difference.
REFUSED_FORMAT = {
    "left-to-right-mark": "\u200e",
    "right-to-left-mark": "\u200f",
    "arabic-letter-mark": "\u061c",
    "left-to-right-embedding": "\u202a",
    "right-to-left-embedding": "\u202b",
    "pop-directional-formatting": "\u202c",
    "left-to-right-override": "\u202d",
    "right-to-left-override": "\u202e",
    "left-to-right-isolate": "\u2066",
    "right-to-left-isolate": "\u2067",
    "first-strong-isolate": "\u2068",
    "pop-directional-isolate": "\u2069",
    "language-tag": "\U000e0001",
    "tag-latin-capital-a": "\U000e0041",
    "cancel-tag": "\U000e007f",
    "zero-width-space": "\u200b",
    "word-joiner": "\u2060",
    "byte-order-mark": "\ufeff",
    "soft-hyphen": "\u00ad",
}


def parts(
    value: object = None,
    *,
    screenshot: bytes | None = None,
    screenshot_type: str = "image/jpeg",
    raw: bytes | str | None = None,
) -> list[tuple[str, tuple[str | None, bytes | str, str]]]:
    files: list[tuple[str, tuple[str | None, bytes | str, str]]] = [
        ("item", (None, raw if raw is not None else json.dumps(item() if value is None else value), "application/json"))
    ]
    if screenshot is not None:
        files.append(("screenshot", ("screenshot.jpg", screenshot, screenshot_type)))
    return files


async def create(
    client: httpx.AsyncClient,
    token: str,
    value: object = None,
    *,
    screenshot: bytes | None = None,
    screenshot_type: str = "image/jpeg",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return await client.post(
        ITEMS,
        files=parts(value, screenshot=screenshot, screenshot_type=screenshot_type),
        headers={**auth(token), **(headers or {})},
    )


def header_list(response: httpx.Response) -> list[tuple[str, str]]:
    return sorted((name.lower(), value) for name, value in response.headers.multi_items())


def problem_body(code: str, status: int, *, locale: str = "en", **params: object) -> dict[str, object]:
    key = code
    return {
        "type": "about:blank",
        "title": messages.title(locale, status),
        "status": status,
        "detail": messages.render(locale, Msg(key, params)),
        "code": code,
    }


def assert_problem(response: httpx.Response, status: int, code: str, *, locale: str = "en", **params: object) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == problem_body(code, status, locale=locale, **params)
    assert response.headers["content-language"] == locale
    assert response.headers["vary"] == "Accept-Language"
    for name, value in FEEDBACK_API_HEADERS.items():
        assert response.headers[name] == value, name


def assert_invalid(response: httpx.Response, field_name: str) -> None:
    assert_problem(response, 422, "FEEDBACK_INVALID", field=field_name)


def is_neutral_404(response: httpx.Response) -> bool:
    return response.status_code == 404 and response.content == NEUTRAL_404_BODY


async def stored_items(factory: async_sessionmaker[AsyncSession]) -> list[FeedbackItem]:
    async with factory() as db:
        return list((await db.scalars(select(FeedbackItem).order_by(FeedbackItem.created_at))).all())


async def add_item(factory: async_sessionmaker[AsyncSession], site_id: uuid.UUID, **values: object) -> uuid.UUID:
    """An item written straight into the database."""
    item_id = uuid.uuid4()
    row: dict[str, object] = {
        "site_id": site_id,
        "target": VersionTarget.LIVE,
        "page_path": "docs/",
        "kind": FeedbackKind.PAGE,
        "comment": "Eerder gestuurd",
        "author_kind": FeedbackAuthor.ANONYMOUS,
    }
    row.update(values)
    async with factory() as db:
        db.add(FeedbackItem(id=item_id, **row))
        await db.commit()
    return item_id


async def account_item(
    factory: async_sessionmaker[AsyncSession], site_id: uuid.UUID, sub: str = "kijker", **values: object
) -> uuid.UUID:
    """An item sent with the account of `sub`, straight into the database."""
    return await add_item(factory, site_id, author_kind=FeedbackAuthor.ACCOUNT, author_sub=sub, **values)


async def add_replies(
    factory: async_sessionmaker[AsyncSession], item_id: uuid.UUID, *, giver: int = 0, member: int = 0
) -> None:
    """Replies straight into the database, from the giver and from the team."""
    async with factory() as db:
        for author, count in ((FeedbackReplyAuthor.GIVER, giver), (FeedbackReplyAuthor.MEMBER, member)):
            db.add_all(FeedbackReply(item_id=item_id, author_kind=author, body="x") for _ in range(count))
        await db.commit()


async def replies_on(factory: async_sessionmaker[AsyncSession], item_id: uuid.UUID) -> int:
    async with factory() as db:
        return await db.scalar(select(func.count()).select_from(FeedbackReply).where(FeedbackReply.item_id == item_id))


def writes_spent(app: FastAPI, site_id: uuid.UUID, author: str = "ip:127.0.0.1") -> tuple[int, int]:
    """What the author's write counter and the site's hold now. The author is
    named as on the site (`session:<sub>`, `gate:<sub>`), and is the test
    client's address unless it is named."""
    now_ = feedback_api._clock()
    state = app.state
    return (
        state.feedback_author_writes.spent(f"site:{site_id}:{author}", now_).count,
        state.feedback_site_writes.spent(f"site:{site_id}", now_).count,
    )


MULTIPART = "multipart/form-data; boundary=grens"


ITEM_HEAD = b'--grens\r\nContent-Disposition: form-data; name="item"\r\n\r\n'
ITEM_TAIL = json.dumps(item()).encode() + b"\r\n--grens--\r\n"


@dataclass
class HeldBody:
    """A create that sends its `head` and then nothing until `release` is
    set, when the `tail` follows: until then it is one of the client's
    bodies in flight, holding the room of its head."""

    release: asyncio.Event = field(default_factory=asyncio.Event)
    # Toon asked for the body at all, and Toon has read the head and waits
    # for more.
    asked: asyncio.Event = field(default_factory=asyncio.Event)
    holding: asyncio.Event = field(default_factory=asyncio.Event)
    head: bytes = ITEM_HEAD
    tail: bytes = ITEM_TAIL

    @property
    def length(self) -> int:
        return len(self.head) + len(self.tail)

    @property
    def pulled(self) -> bool:
        return self.asked.is_set()

    async def chunks(self) -> AsyncIterator[bytes]:
        self.asked.set()
        if self.head:
            yield self.head
        self.holding.set()
        await self.release.wait()
        if self.tail:
            yield self.tail


def whole_body(size: int) -> HeldBody:
    """A held create of exactly `size` bytes whose head is all of it: the
    item padded with the spaces JSON allows before a value. Released, it
    ends without another byte."""
    head = ITEM_HEAD + b" " * (size - len(ITEM_HEAD) - len(ITEM_TAIL)) + ITEM_TAIL
    assert len(head) == size
    return HeldBody(head=head, tail=b"")


async def held_post(
    client: httpx.AsyncClient, path: str, body: HeldBody, headers: dict[str, str | bytes]
) -> asyncio.Task[httpx.Response]:
    """Starts the POST and comes back once Toon has read the body's head
    and waits for more, so once that head holds its room."""
    sent = asyncio.create_task(client.post(path, content=body.chunks(), headers=headers))
    holding = asyncio.create_task(body.holding.wait())
    await asyncio.wait([sent, holding], return_when=asyncio.FIRST_COMPLETED)
    holding.cancel()
    assert body.holding.is_set(), (await sent).text
    return sent


async def held_create(
    client: httpx.AsyncClient, token: str, body: HeldBody, *, declared: int | None = None
) -> asyncio.Task[httpx.Response]:
    """A held create. With `declared` it declares that Content-Length, else
    its body is chunked."""
    headers: dict[str, str | bytes] = {**auth(token, **{"Content-Type": MULTIPART})}
    if declared is not None:
        headers["Content-Length"] = str(declared)
    return await held_post(client, ITEMS, body, headers)


# --- The widget header set ----------------------------------------------------------


class TestTheWidgetHeaderSet:
    async def test_a_success_carries_it_and_nothing_that_opens_more(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        response = await create(client, token, headers={"Origin": "https://kwaad.example"})
        assert response.status_code == 201
        assert response.headers["content-type"] == "application/json"
        for name, value in FEEDBACK_API_HEADERS.items():
            assert response.headers[name] == value, name
        # Never a reflected origin, never credentials.
        assert response.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in response.headers
        # API-Version is the admin API's: it says nothing about this one.
        assert response.headers["access-control-expose-headers"] == "Retry-After, Content-Language"

    async def test_a_problem_carries_it_with_its_language(self, client):
        response = await client.post(ITEMS, files=parts(), headers={"Origin": "null", "Accept-Language": "nl"})
        assert_problem(response, 404, "FEEDBACK_UNAVAILABLE", locale="nl")
        assert response.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in response.headers

    @pytest.mark.parametrize("path", [ITEMS, AN_ITEM, f"{AN_ITEM}/replies"])
    async def test_a_preflight_names_exactly_what_the_widget_sends(self, client, path):
        response = await client.options(
            path,
            headers={
                "Origin": "https://kwaad.example",
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": "authorization, content-type, x-ander",
            },
        )
        assert response.status_code == 204
        assert response.content == b""
        assert response.headers["access-control-allow-methods"] == "GET, POST, DELETE"
        assert response.headers["access-control-allow-headers"] == "Authorization, Content-Type, Accept-Language"
        assert response.headers["access-control-max-age"] == "600"
        assert response.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in response.headers
        for name, value in PREFLIGHT_HEADERS.items():
            assert response.headers[name] == value, name

    async def test_an_unforeseen_failure_still_answers_with_it(self, client, environment, monkeypatch, caplog):
        """A 5xx without the header set would reach a shielded page as a
        network error; with it, the widget reads the status and says to try
        again later."""

        async def broken(request, raw):
            raise RuntimeError("database weg")

        monkeypatch.setattr(feedback_api, "verify_page_token", broken)
        with caplog.at_level(logging.ERROR, logger="toon.serving.feedback_api"):
            response = await create(client, "x", headers={"Accept-Language": "nl"})
        assert response.status_code == 500
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json() == {"type": "about:blank", "title": messages.title("nl", 500), "status": 500}
        assert response.headers["content-language"] == "nl"
        for name, value in FEEDBACK_API_HEADERS.items():
            assert response.headers[name] == value, name
        assert "The feedback widget API failed on POST /-/feedback/v1/items" in caplog.text


# --- FEEDBACK_UNAVAILABLE: one answer for every ground ----------------------------


class TestUnavailable:
    """Every ground for refusing the token, or an item, answers the same
    bytes per language: nothing tells one reason from another."""

    async def _reference(self, client: httpx.AsyncClient, language: str) -> httpx.Response:
        return await client.post(ITEMS, files=parts(), headers={"Accept-Language": language})

    async def _assert_unavailable(self, client, response_for) -> None:
        for language in ("en", "nl"):
            reference = await self._reference(client, language)
            assert_problem(reference, 404, "FEEDBACK_UNAVAILABLE", locale=language)
            response = await response_for({"Accept-Language": language})
            assert response.status_code == 404
            assert response.content == reference.content
            assert header_list(response) == header_list(reference)

    @pytest.mark.parametrize(
        "authorization",
        [
            "",
            "Toon-Feedback",
            "Toon-Feedback ",
            "Bearer x",
            "Basic eDp5",
            "Toon-Feedback nep.token",
            "Toon-Feedbackx y",
        ],
        ids=["empty", "scheme-only", "scheme-and-space", "bearer", "basic", "garbage", "lookalike-scheme"],
    )
    async def test_a_missing_or_malformed_token(self, client, authorization):
        async def respond(headers):
            return await client.post(ITEMS, files=parts(), headers={**headers, "Authorization": authorization})

        await self._assert_unavailable(client, respond)

    async def test_the_scheme_is_case_insensitive_and_the_token_still_checked(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        response = await client.post(ITEMS, files=parts(), headers={"Authorization": f"tOON-fEEDBACK  {token}  "})
        assert response.status_code == 201

    async def test_a_token_sealed_with_another_secret(self, client, environment):
        token = mint_page_token(
            "een-ander-geheim-van-minstens-32-bytes!",
            site_id=environment.world.site_id,
            kind="live",
            ref=None,
            version_id=None,
            session_id=None,
            key_id=None,
            now=int(time.time()),
        )

        async def respond(headers):
            return await create(client, token, headers=headers)

        await self._assert_unavailable(client, respond)

    async def test_a_consent_nonce_is_no_page_token(self, client, environment):
        session = consented_session(environment.app)
        nonce = mint_consent_nonce(
            SECRET, session_id=session.id, site_id=environment.world.site_id, return_to=ROOT, now=int(time.time())
        )

        async def respond(headers):
            return await create(client, nonce, headers=headers)

        await self._assert_unavailable(client, respond)

    async def test_an_expired_token(self, client, environment):
        token = page_token(environment.app, environment.world.site_id, now=int(time.time()) - TOKEN_LIFETIME_S - 1)

        async def respond(headers):
            return await create(client, token, headers=headers)

        await self._assert_unavailable(client, respond)

    @pytest.mark.parametrize("ground", ["logged-out", "not-consented", "consent-for-another-site", "admin-session"])
    async def test_a_session_that_does_not_hold(self, client, environment, ground):
        app, world = environment.app, environment.world
        if ground == "admin-session":
            session = app.state.session_store.create_session(
                sub="lid-actief", email="lid@example.org", email_verified=True, acr="urn:acr:hoog"
            )
        elif ground == "not-consented":
            session = consented_session(app)
        elif ground == "consent-for-another-site":
            session = consented_session(app, world.other_site_id)
        else:
            session = consented_session(app, world.site_id)
            app.state.session_store.delete_session(session.id)
        token = page_token(app, world.site_id, session_id=session.id)

        async def respond(headers):
            return await create(client, token, headers=headers)

        await self._assert_unavailable(client, respond)

    async def test_consent_withdrawn_while_the_idp_is_asked(self, client, environment, monkeypatch):
        """The consent is checked again after the revalidation, which awaits
        the IdP and so lets a withdrawal in between."""
        app, world = environment.app, environment.world
        session = consented_session(app, world.site_id)

        async def withdrawn_meanwhile(request, found):
            return app.state.session_store.withdraw_feedback(found.id, str(world.site_id))

        monkeypatch.setattr(feedback_access, "revalidate_session", withdrawn_meanwhile)
        token = page_token(app, world.site_id, session_id=session.id)

        async def respond(headers):
            return await create(client, token, headers=headers)

        await self._assert_unavailable(client, respond)

    async def test_a_session_the_idp_ended(self, environment):
        """The page token carries no cookie, so the API runs the IdP check
        itself; a session the IdP ended is gone, and the token with it."""
        idp = MockIdP()
        idp.refresh_error = "invalid_grant"
        idp.refresh_status = 400
        app = environment.app_with(
            oidc_issuer=idp.issuer, oidc_client_id=idp.client_id, oidc_client_private_jwk=make_client_jwk()
        )
        app.state.oidc_client = make_oidc_client(app.state.settings, idp)
        session = consented_session(app, environment.world.site_id, refresh_token="ververstoken-1")
        store: SessionStore = app.state.session_store
        store._sessions[session.id] = replace(session, checked_at=datetime.now(UTC) - timedelta(hours=1))
        token = page_token(app, environment.world.site_id, session_id=session.id)
        async with client_for(app) as other:
            response = await create(other, token)
        assert_problem(response, 404, "FEEDBACK_UNAVAILABLE")
        assert store.get_session(session.id) is None
        assert len(idp.refresh_requests) == 1

    async def test_a_site_that_is_gone(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        async with environment.factory() as db:
            await db.execute(delete(Site).where(Site.id == environment.world.site_id))
            await db.commit()

        async def respond(headers):
            return await create(client, token, headers=headers)

        await self._assert_unavailable(client, respond)

    @pytest.mark.parametrize(
        "change",
        [
            {"feedback_enabled": False},
            {"feedback_live": False},
            {"access_base": AccessBase.SITE_TEAM},
            {"live_version_id": None},
        ],
        ids=["feedback-off", "previews-only", "access-narrowed", "no-live-version"],
    )
    async def test_a_live_page_that_no_longer_takes_feedback(self, client, environment, change):
        token = page_token(environment.app, environment.world.site_id)
        async with environment.factory() as db:
            await db.execute(update(Site).where(Site.id == environment.world.site_id).values(**change))
            await db.commit()

        async def respond(headers):
            return await create(client, token, headers=headers)

        await self._assert_unavailable(client, respond)

    async def test_a_preview_that_is_gone(self, client, environment):
        token = page_token(environment.app, environment.world.site_id, ref="pr-1")
        async with environment.factory() as db:
            await db.execute(delete(Preview).where(Preview.ref == "pr-1"))
            await db.commit()

        async def respond(headers):
            value = item(page={"path": PREVIEW_ROOT, "title": None})
            return await create(client, token, value, headers=headers)

        await self._assert_unavailable(client, respond)

    async def test_another_site_at_the_slugs_is_a_refusal(self, client, environment, monkeypatch):
        """The site is found by id and the gate decides by slug; should the
        slugs change hands in between, the gate's site is not the token's."""
        world = environment.world

        async def other_site(db, group, site, ref, visitor):
            return allow(
                world.other_live_version_id,
                AccessPolicy(AccessBase.PUBLIC),
                site_id=world.other_site_id,
                feedback=FeedbackPolicy(enabled=True, identity=FeedbackIdentity.ANONYMOUS, live=True),
            )

        monkeypatch.setattr(feedback_access, "decide_page", other_site)
        token = page_token(environment.app, world.site_id)

        async def respond(headers):
            return await create(client, token, headers=headers)

        await self._assert_unavailable(client, respond)

    async def test_an_allow_without_feedback_settings_is_feedback_off(self, client, environment, monkeypatch):
        world = environment.world

        async def bare_allow(db, group, site, ref, visitor):
            return allow(world.live_version_id, AccessPolicy(AccessBase.PUBLIC), site_id=world.site_id)

        monkeypatch.setattr(feedback_access, "decide_page", bare_allow)
        token = page_token(environment.app, world.site_id)

        async def respond(headers):
            return await create(client, token, headers=headers)

        await self._assert_unavailable(client, respond)

    async def test_a_site_deleted_after_the_check_is_unavailable_not_a_500(self, client, environment, monkeypatch):
        """The site's row is locked inside the create transaction; one that
        went away between the token check and that lock is no site."""
        verify = feedback_api.verify_page_token

        async def then_deleted(request, raw):
            context = await verify(request, raw)
            async with environment.factory() as db:
                await db.execute(delete(Site).where(Site.id == environment.world.site_id))
                await db.commit()
            return context

        monkeypatch.setattr(feedback_api, "verify_page_token", then_deleted)
        response = await create(client, page_token(environment.app, environment.world.site_id))
        assert_problem(response, 404, "FEEDBACK_UNAVAILABLE")
        assert await stored_items(environment.factory) == []

    async def test_cookies_count_for_nothing(self, client, environment):
        """The API reads no cookie: neither the anchor nor a site cookie,
        duplicated or not, turns a token without a session into one with."""
        app, world = environment.app, environment.world
        session = consented_session(app, world.site_id)
        signed = sign(SECRET, session.id)
        token = page_token(app, world.site_id)
        site_cookie = f"{CONTENT_SESSION_COOKIE}={signed}"
        cookie = f"{site_cookie}; {site_cookie}; {CONTENT_ANCHOR_COOKIE}={signed}"
        response = await create(client, token, item(author={"kind": "account"}), headers={"Cookie": cookie})
        assert_problem(response, 403, "FEEDBACK_ACCOUNT_REQUIRED")
        anonymous = await create(client, token, headers={"Cookie": cookie})
        assert anonymous.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert (stored.author_sub, stored.author_name, stored.author_email) == (None, None, None)


# --- Validation: 422, never 500 ---------------------------------------------------


def _long(length: int, char: str = "a") -> str:
    return char * length


INVALID_ITEMS: list[tuple[str, object, str]] = [
    # (id, item, the field the detail names)
    ("not-an-object", [1, 2], "item"),
    ("unknown-key", item(email="iemand@example.org"), "item"),
    ("no-comment", {k: v for k, v in item().items() if k != "comment"}, "item"),
    ("no-author", {k: v for k, v in item().items() if k != "author"}, "item"),
    ("kind-unknown", item(kind="video"), "kind"),
    ("kind-not-a-string", item(kind=["page"]), "kind"),
    ("kind-an-object", item(kind={"page": 1}), "kind"),
    ("comment-empty", item(comment=""), "comment"),
    ("comment-blank", item(comment=" \n\t "), "comment"),
    ("comment-too-long", item(comment=_long(5001)), "comment"),
    ("comment-not-a-string", item(comment=5), "comment"),
    ("comment-nul", item(comment="a\x00b"), "comment"),
    ("comment-carriage-return", item(comment="a\r\nb"), "comment"),
    ("comment-right-to-left-override", item(comment="Typo in de kop.‮"), "comment"),
    ("comment-tag-characters", item(comment="Typo\U000e0041\U000e0042"), "comment"),
    ("comment-zero-width-space", item(comment="a​b"), "comment"),
    ("comment-lone-surrogate", item(comment="a\ud800b"), "comment"),
    ("comment-private-use", item(comment="a"), "comment"),
    ("comment-c1-control", item(comment="a\x85b"), "comment"),
    ("page-not-an-object", item(page="/aurora/site/"), "page"),
    ("page-unknown-key", item(page={"path": ROOT, "url": "https://kwaad.example/"}), "page"),
    ("page-without-path", item(page={"title": "x"}), "page"),
    ("path-not-a-string", item(page={"path": 7}), "page.path"),
    ("path-empty", item(page={"path": ""}), "page.path"),
    ("path-query", item(page={"path": f"{ROOT}docs/?x=1"}), "page.path"),
    ("path-fragment", item(page={"path": f"{ROOT}docs/#x"}), "page.path"),
    ("path-backslash", item(page={"path": f"{ROOT}docs\\x"}), "page.path"),
    ("path-nul", item(page={"path": f"{ROOT}a\x00"}), "page.path"),
    ("path-space", item(page={"path": f"{ROOT}a b"}), "page.path"),
    ("path-not-ascii", item(page={"path": f"{ROOT}café/"}), "page.path"),
    ("path-dot-dot", item(page={"path": f"{ROOT}../ander/"}), "page.path"),
    ("path-dot", item(page={"path": f"{ROOT}./docs/"}), "page.path"),
    ("path-encoded-dot-dot", item(page={"path": f"{ROOT}%2E%2e/ander/"}), "page.path"),
    ("path-half-encoded-dot-dot", item(page={"path": f"{ROOT}.%2E/"}), "page.path"),
    ("path-empty-segment", item(page={"path": f"{ROOT}/docs/"}), "page.path"),
    ("path-empty-segment-inside", item(page={"path": f"{ROOT}docs//x"}), "page.path"),
    ("path-a-preview-on-a-live-token", item(page={"path": PREVIEW_ROOT}), "page.path"),
    ("path-version", item(page={"path": f"{ROOT}_version/x/"}), "page.path"),
    ("path-encoded-copies", item(page={"path": f"{ROOT}_encoded/gzip/index.html"}), "page.path"),
    ("path-too-long", item(page={"path": ROOT + _long(2049 - len(ROOT))}), "page.path"),
    ("title-not-a-string", item(page={"path": ROOT, "title": ["x"]}), "page.title"),
    ("anchor-on-a-page", item(anchor=POINT_ANCHOR), "anchor"),
    ("point-without-anchor", item(kind="point", anchor=None), "anchor"),
    ("anchor-not-an-object", item(kind="point", anchor="main"), "anchor"),
    ("anchor-unknown-key", item(kind="point", anchor={**POINT_ANCHOR, "html": "<b>"}), "anchor"),
    ("anchor-without-selectors", item(kind="point", anchor={"point": POINT_ANCHOR["point"]}), "anchor"),
    ("selectors-not-a-list", item(kind="point", anchor={**POINT_ANCHOR, "selectors": {}}), "anchor.selectors"),
    ("selectors-empty", item(kind="point", anchor={**POINT_ANCHOR, "selectors": []}), "anchor.selectors"),
    (
        "selectors-too-many",
        item(kind="text", anchor={**TEXT_ANCHOR, "selectors": [*TEXT_ANCHOR["selectors"], {"type": "CssSelector"}]}),
        "anchor.selectors",
    ),
    (
        "selector-unknown-type",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": [{"type": "XPathSelector", "value": "/html"}]}),
        "anchor.selectors",
    ),
    (
        "selector-type-not-a-string",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": [{"type": ["CssSelector"], "value": "p"}]}),
        "anchor.selectors",
    ),
    (
        "selector-not-an-object",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": ["CssSelector"]}),
        "anchor.selectors",
    ),
    (
        "selector-unknown-key",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": [{"type": "CssSelector", "value": "p", "x": 1}]}),
        "anchor.selectors",
    ),
    (
        "selector-twice",
        item(
            kind=FeedbackKind.TEXT,
            anchor={**TEXT_ANCHOR, "selectors": [TEXT_ANCHOR["selectors"][2], TEXT_ANCHOR["selectors"][2]]},
        ),
        "anchor.selectors",
    ),
    (
        "point-with-a-quote",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": [TEXT_ANCHOR["selectors"][2]]}),
        "anchor.selectors",
    ),
    (
        "point-with-two-selectors",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": TEXT_ANCHOR["selectors"][1:]}),
        "anchor.selectors",
    ),
    ("point-without-point", item(kind="point", anchor={**POINT_ANCHOR, "point": None}), "anchor.point"),
    ("point-missing", item(kind="point", anchor={"selectors": POINT_ANCHOR["selectors"]}), "anchor.point"),
    (
        "point-unknown-key",
        item(kind="point", anchor={**POINT_ANCHOR, "point": {**POINT_ANCHOR["point"], "z": 1}}),
        "anchor.point",
    ),
    (
        "point-fraction-above-one",
        item(kind="point", anchor={**POINT_ANCHOR, "point": {**POINT_ANCHOR["point"], "x": 1.01}}),
        "anchor.point",
    ),
    (
        "point-negative",
        item(kind="point", anchor={**POINT_ANCHOR, "point": {**POINT_ANCHOR["point"], "pageX": -1}}),
        "anchor.point",
    ),
    (
        "point-too-far",
        item(kind="point", anchor={**POINT_ANCHOR, "point": {**POINT_ANCHOR["point"], "pageY": 1_000_001}}),
        "anchor.point",
    ),
    (
        "point-a-boolean",
        item(kind="point", anchor={**POINT_ANCHOR, "point": {**POINT_ANCHOR["point"], "y": True}}),
        "anchor.point",
    ),
    (
        "point-a-string",
        item(kind="point", anchor={**POINT_ANCHOR, "point": {**POINT_ANCHOR["point"], "y": "0.5"}}),
        "anchor.point",
    ),
    (
        "text-without-quote",
        item(kind="text", anchor={**TEXT_ANCHOR, "selectors": TEXT_ANCHOR["selectors"][:2]}),
        "anchor.selectors",
    ),
    ("text-with-a-point", item(kind="text", anchor={**TEXT_ANCHOR, "point": POINT_ANCHOR["point"]}), "anchor.point"),
    (
        "quote-empty",
        item(kind="text", anchor={"selectors": [{"type": "TextQuoteSelector", "exact": ""}]}),
        "anchor.selectors",
    ),
    (
        "quote-too-long",
        item(kind="text", anchor={"selectors": [{"type": "TextQuoteSelector", "exact": _long(1001)}]}),
        "anchor.selectors",
    ),
    (
        "quote-newline",
        item(kind="text", anchor={"selectors": [{"type": "TextQuoteSelector", "exact": "a\nb"}]}),
        "anchor.selectors",
    ),
    (
        "quote-format-character",
        item(kind="text", anchor={"selectors": [{"type": "TextQuoteSelector", "exact": "a⁦b"}]}),
        "anchor.selectors",
    ),
    (
        "prefix-too-long",
        item(kind="text", anchor={"selectors": [{"type": "TextQuoteSelector", "exact": "a", "prefix": _long(33)}]}),
        "anchor.selectors",
    ),
    (
        "suffix-not-a-string",
        item(kind="text", anchor={"selectors": [{"type": "TextQuoteSelector", "exact": "a", "suffix": 3}]}),
        "anchor.selectors",
    ),
    (
        "position-backwards",
        item(
            kind=FeedbackKind.TEXT,
            anchor={"selectors": [TEXT_ANCHOR["selectors"][2], {"type": "TextPositionSelector", "start": 9, "end": 9}]},
        ),
        "anchor.selectors",
    ),
    (
        "position-a-fraction",
        item(
            kind=FeedbackKind.TEXT,
            anchor={
                "selectors": [TEXT_ANCHOR["selectors"][2], {"type": "TextPositionSelector", "start": 1.5, "end": 9}]
            },
        ),
        "anchor.selectors",
    ),
    (
        "position-too-far",
        item(
            kind=FeedbackKind.TEXT,
            anchor={
                "selectors": [TEXT_ANCHOR["selectors"][2], {"type": "TextPositionSelector", "start": 1, "end": 10**7}]
            },
        ),
        "anchor.selectors",
    ),
    (
        "position-without-end",
        item(kind="text", anchor={"selectors": [TEXT_ANCHOR["selectors"][2], {"type": "TextPositionSelector"}]}),
        "anchor.selectors",
    ),
    (
        "css-empty",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": [{"type": "CssSelector", "value": ""}]}),
        "anchor.selectors",
    ),
    (
        "css-too-long",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": [{"type": "CssSelector", "value": _long(1001)}]}),
        "anchor.selectors",
    ),
    ("viewport-not-an-object", item(viewport=[1280, 800]), "viewport"),
    ("viewport-unknown-key", item(viewport={"width": 1, "height": 1, "dpr": 1, "zoom": 1}), "viewport"),
    ("viewport-without-dpr", item(viewport={"width": 1, "height": 1}), "viewport"),
    ("viewport-dpr-zero", item(viewport={"width": 1, "height": 1, "dpr": 0}), "viewport"),
    ("viewport-dpr-above-ten", item(viewport={"width": 1, "height": 1, "dpr": 10.5}), "viewport"),
    ("viewport-negative", item(viewport={"width": -1, "height": 1, "dpr": 1}), "viewport"),
    ("viewport-too-wide", item(viewport={"width": 1_000_001, "height": 1, "dpr": 1}), "viewport"),
    ("author-not-an-object", item(author="anonymous"), "author"),
    ("author-unknown-key", item(author={"kind": "account", "email": "baas@example.org"}), "author"),
    ("author-without-kind", item(author={"name": "Jan"}), "author"),
    ("author-kind-unknown", item(author={"kind": "member"}), "author.kind"),
    ("name-missing", item(author={"kind": "name"}), "author.name"),
    ("name-blank", item(author={"kind": "name", "name": "  "}), "author.name"),
    ("name-too-long", item(author={"kind": "name", "name": _long(101)}), "author.name"),
    ("name-newline", item(author={"kind": "name", "name": "Jan\nJansen"}), "author.name"),
    ("name-right-to-left-override", item(author={"kind": "name", "name": "‮Jan"}), "author.name"),
    ("name-beside-anonymous", item(author={"kind": "anonymous", "name": "Jan"}), "author.name"),
    ("name-beside-account", item(author={"kind": "account", "name": "De Baas"}), "author.name"),
    ("name-a-number", item(author={"kind": "anonymous", "name": 3}), "author.name"),
    ("screenshot-not-an-object", item(screenshot=True), "screenshot"),
    ("screenshot-unknown-key", item(screenshot={"region": None, "data": "x"}), "screenshot"),
    ("region-unknown-key", item(screenshot={"region": {"x": 0, "y": 0, "w": 1, "h": 1}}), "screenshot"),
    ("region-negative", item(screenshot={"region": {"x": -5, "y": 0, "width": 1, "height": 1}}), "screenshot"),
]


class TestValidation:
    @pytest.mark.parametrize(
        ("value", "field_name"), [(v, f) for _, v, f in INVALID_ITEMS], ids=[i for i, _, _ in INVALID_ITEMS]
    )
    async def test_an_item_that_does_not_hold_is_refused_by_field(self, client, environment, value, field_name):
        token = page_token(environment.app, environment.world.site_id)
        response = await create(client, token, value)
        assert_invalid(response, field_name)
        assert await stored_items(environment.factory) == []

    @pytest.mark.parametrize("char", list(REFUSED_FORMAT.values()), ids=list(REFUSED_FORMAT))
    async def test_every_other_format_character_is_refused(self, client, environment, char):
        """Of the format characters only the two joiners pass, in a comment
        and in a name alike."""
        assert unicodedata.category(char) == "Cf"
        token = page_token(environment.app, environment.world.site_id)
        assert_invalid(await create(client, token, item(comment=f"Typo{char} in de kop.")), "comment")
        assert_invalid(await create(client, token, item(author={"kind": "name", "name": f"Jan{char}"})), "author.name")
        assert await stored_items(environment.factory) == []

    @pytest.mark.parametrize(
        ("ref", "path"),
        [
            (None, "/aurora/ander/"),
            (None, "/aurora/site"),
            (None, "/aurora/sites/docs/"),
            (None, "/borealis/site/docs/"),
            (None, "/"),
            ("pr-1", "/aurora/ander/_preview/pr-1/docs/"),
            ("pr-1", "/borealis/site/_preview/pr-1/"),
        ],
        ids=[
            "another-site",
            "the-site-without-slash",
            "a-longer-slug",
            "another-group",
            "the-root",
            "a-preview-of-another-site",
            "a-preview-in-another-group",
        ],
    )
    async def test_a_path_outside_the_sites_root_has_moved(self, client, environment, ref, path):
        """What a page served before a rename of its site or group sends: the
        token names the site by its id and holds, and only a reload gives the
        widget the address the site is at now. Nothing is stored or spent."""
        world = environment.world
        token = page_token(environment.app, world.site_id, ref=ref)
        for language in ("en", "nl"):
            response = await create(
                client, token, item(page={"path": path, "title": None}), headers={"Accept-Language": language}
            )
            assert_problem(response, 422, "FEEDBACK_PAGE_MOVED", locale=language)
        assert await stored_items(environment.factory) == []
        assert writes_spent(environment.app, world.site_id) == (0, 0)

    @pytest.mark.parametrize(
        "raw",
        [
            '{"kind": "page", "comment": NaN}',
            '{"kind": "page", "comment": "a", "viewport": {"width": Infinity, "height": 1, "dpr": 1}}',
            '{"kind": "page", "comment": "a", "viewport": {"width": -Infinity, "height": 1, "dpr": 1}}',
            '{"kind": "page", "kind": "point"}',
            "not json",
            "",
            '{"kind": "page"',
            "﻿{}",
            "[" * 16000 + "]" * 16000,
        ],
        ids=["nan", "infinity", "minus-infinity", "duplicate-key", "garbage", "empty", "cut-short", "bom", "deep"],
    )
    async def test_json_that_is_not_strict_json_is_refused(self, client, environment, raw):
        token = page_token(environment.app, environment.world.site_id)
        response = await client.post(ITEMS, files=parts(raw=raw), headers=auth(token))
        assert_invalid(response, "item")

    async def test_a_number_beyond_a_float_is_refused_not_infinite(self, client, environment):
        """1e400 parses as infinity without any constant JSON names."""
        token = page_token(environment.app, environment.world.site_id)
        raw = json.dumps(item()).replace('"width": 1280', '"width": 1e400')
        response = await client.post(ITEMS, files=parts(raw=raw), headers=auth(token))
        assert_invalid(response, "viewport")

    async def test_an_item_that_is_not_utf8_is_refused(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        response = await client.post(ITEMS, files=parts(raw=b'{"comment": "\xff"}'), headers=auth(token))
        assert_invalid(response, "item")

    async def test_an_item_over_32_kib_is_refused(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        raw = json.dumps(item(comment="a", page={"path": ROOT, "title": _long(33 * 1024)}))
        response = await client.post(ITEMS, files=parts(raw=raw), headers=auth(token))
        assert_invalid(response, "item")

    @pytest.mark.parametrize(
        ("content_type", "body"),
        [
            ("application/json", json.dumps(item()).encode()),
            ("multipart/form-data", b"--x\r\n"),
            ("text/plain", b"item=x"),
        ],
        ids=["json", "multipart-without-boundary", "text"],
    )
    async def test_a_body_that_is_no_multipart_form_is_refused(self, client, environment, content_type, body):
        token = page_token(environment.app, environment.world.site_id)
        response = await client.post(ITEMS, content=body, headers=auth(token, **{"Content-Type": content_type}))
        assert_invalid(response, "item")

    @pytest.mark.parametrize(
        "body",
        [
            b"--grens\r\nContent-Disposition: form-data; name=\"item\"\r\n\r\n{}",
            b"--grens\r\nContent-Disposition: form-data; name=\"screenshot\"\r\n\r\nx\r\n--grens--\r\n",
            b"--grens\r\nContent-Disposition: form-data; name=\"anders\"\r\n\r\nx\r\n--grens--\r\n",
            b"--grens\r\nContent-Disposition: attachment; name=\"item\"\r\n\r\n{}\r\n--grens--\r\n",
            b"--grens\r\nContent-Type: text/plain\r\n\r\n{}\r\n--grens--\r\n",
            b"--grens\r\nContent-Disposition: form-data; name=\"item\"\r\n\r\n{}\r\n"
            b"--grens\r\nContent-Disposition: form-data; name=\"item\"\r\n\r\n{}\r\n--grens--\r\n",
            b"geen multipart",
            b"--grens\r\nzonder dubbele punt\r\n\r\n",
        ],
        ids=[
            "cut-short",
            "no-item",
            "unknown-part",
            "not-form-data",
            "no-disposition",
            "item-twice",
            "garbage",
            "broken-header",
        ],
    )
    async def test_a_multipart_body_that_does_not_hold_is_refused(self, client, environment, body):
        token = page_token(environment.app, environment.world.site_id)
        headers = auth(token, **{"Content-Type": "multipart/form-data; boundary=grens"})
        response = await client.post(ITEMS, content=body, headers=headers)
        assert_invalid(response, "item")

    async def test_a_boundary_longer_than_the_parser_takes_is_refused(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        boundary = "g" * 300
        body = f'--{boundary}\r\nContent-Disposition: form-data; name="item"\r\n\r\n{{}}\r\n--{boundary}--\r\n'
        headers = auth(token, **{"Content-Type": f"multipart/form-data; boundary={boundary}"})
        response = await client.post(ITEMS, content=body.encode(), headers=headers)
        assert_invalid(response, "item")

    async def test_a_second_screenshot_is_refused(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        screenshot = ("screenshot", ("a.png", PNG_FILE, "image/png"))
        files = [*parts(), screenshot, screenshot]
        response = await client.post(ITEMS, files=files, headers=auth(token))
        assert_invalid(response, "item")

    @pytest.mark.parametrize("piece", [1, 4, 8, None], ids=["by-the-byte", "by-four", "by-eight", "whole"])
    @pytest.mark.parametrize(
        ("opening", "inside", "accepted"),
        [(b"\r\n", 0, True), (b"", 1, True), (b"\r\n", 1, False), (b"", 2, False)],
        ids=["three-with-a-line-break-first", "three-with-one-in-the-data", "four", "four-in-the-data"],
    )
    async def test_a_body_with_more_delimiters_than_a_create_holds_is_refused(
        self, client, environment, piece, opening, inside, accepted
    ):
        """A create holds three delimiters at most: one after the item, one
        that closes the body, and one before the first boundary when the body
        opens with a line break. A fourth is refused wherever the chunks cut
        the delimiters, also when the parser would read it as screenshot data
        because no boundary follows it."""
        token = page_token(environment.app, environment.world.site_id)
        body = delimited_body(opening=opening, inside=inside)
        response = await client.post(
            ITEMS, content=in_pieces(body, piece), headers=auth(token, **{"Content-Type": MULTIPART})
        )
        if accepted:
            assert response.status_code == 201, response.text
        else:
            assert_invalid(response, "item")
            assert await stored_items(environment.factory) == []


def delimited_body(
    *, opening: bytes = b"", inside: int = 0, boundary: bytes = b"grens", data: bytes = PNG_FILE
) -> bytes:
    """A create with an item and a screenshot: `opening` before the first
    boundary, and `inside` delimiters in the screenshot's data, each followed
    by a byte that makes no boundary of it."""
    return (
        opening
        + b"--" + boundary + b'\r\nContent-Disposition: form-data; name="item"\r\n\r\n'
        + json.dumps(item()).encode()
        + b"\r\n--" + boundary
        + b'\r\nContent-Disposition: form-data; name="screenshot"; filename="s.png"\r\nContent-Type: image/png\r\n\r\n'
        + data
        + (b"\r\n--" + boundary + b"X") * inside
        + b"\r\n--" + boundary + b"--\r\n"
    )


async def in_pieces(body: bytes, piece: int | None) -> AsyncIterator[bytes]:
    """The body in chunks of `piece` bytes, or whole."""
    step = piece or len(body)
    for start in range(0, len(body), step):
        yield body[start : start + step]


class TestCost:
    """What a create costs the event loop of the one process. Measured
    against an ordinary body of the same size, so the bound holds on a slow
    machine and under coverage alike."""

    async def test_a_body_packed_with_delimiters_is_refused_before_it_is_parsed(self, client, environment):
        """The parser steps through every delimiter it finds byte by byte in
        Python. A screenshot of 2 MiB packed with them under a boundary of
        one letter cost 220 ms of CPU against 1 to 2 ms for an ordinary one,
        and its 422 gave the write back, so only the rate limiter stopped a
        client that kept sending them. Counted before the parser sees a
        chunk, such a body is refused at its first chunk."""
        token = page_token(environment.app, environment.world.site_id)
        size = environment.app.state.settings.feedback_screenshot_max_bytes
        unit = b"\r\n--b-"
        packed = delimited_body(boundary=b"b", data=(unit * (size // len(unit) + 1))[:size])
        ordinary = delimited_body(boundary=b"b", data=bytes(size))
        assert len(packed) == len(ordinary)
        headers = auth(token, **{"Content-Type": "multipart/form-data; boundary=b"})

        async def cpu_ms(body: bytes) -> tuple[float, httpx.Response]:
            """The CPU time this thread spends on the create, sent in the
            pieces of 64 KiB uvicorn hands over, the best of three."""
            spent = []
            for _ in range(3):
                start = time.thread_time()
                response = await client.post(ITEMS, content=in_pieces(body, 64 * 1024), headers=headers)
                spent.append(time.thread_time() - start)
            return min(spent) * 1000, response

        spent, refused = await cpu_ms(packed)
        reference, parsed = await cpu_ms(ordinary)
        assert_invalid(refused, "item")
        assert_problem(parsed, 422, "FEEDBACK_SCREENSHOT_INVALID")
        assert spent < 2 * reference, f"{spent:.1f} ms against {reference:.1f} ms"


# --- What an accepted item stores ------------------------------------------------


class TestWhatIsStored:
    async def test_a_page_item_stores_its_fields_and_nothing_about_the_person(self, client, environment):
        world = environment.world
        token = page_token(environment.app, world.site_id, version_id=world.live_version_id)
        response = await create(client, token, item(comment="  Klopt dit bedrag?\n\tEcht?  "))
        assert response.status_code == 201
        body = response.json()
        assert set(body) == {"id", "status", "createdAt"}
        assert body["status"] == "new"
        assert body["createdAt"].endswith("Z")
        (stored,) = await stored_items(environment.factory)
        assert str(stored.id) == body["id"]
        assert stored.site_id == world.site_id
        assert stored.target is VersionTarget.LIVE
        assert stored.preview_ref is None
        assert stored.version_id == world.live_version_id
        assert stored.page_path == "docs/"
        assert stored.page_title == "Documentatie"
        assert stored.kind.value == "page"
        assert stored.comment == "Klopt dit bedrag?\n\tEcht?"
        assert stored.quote is None
        assert stored.viewport == {"width": 1280, "height": 800, "dpr": 2}
        assert stored.author_kind.value == "anonymous"
        assert (stored.author_sub, stored.author_name, stored.author_email) == (None, None, None)
        assert stored.status is FeedbackStatus.NEW
        async with environment.factory() as db:
            # SQL NULL, not the JSON null a bare None would have made.
            assert (await db.execute(text("SELECT anchor IS NULL FROM feedback_items"))).scalar_one() is True

    async def test_a_point_item_stores_the_anchor_it_was_rebuilt_to(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        response = await create(client, token, item(kind="point", anchor=POINT_ANCHOR, viewport=None))
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.anchor == POINT_ANCHOR
        assert stored.quote is None
        async with environment.factory() as db:
            assert (await db.execute(text("SELECT viewport IS NULL FROM feedback_items"))).scalar_one() is True

    async def test_a_text_item_stores_its_selectors_in_order_and_its_quote(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        response = await create(client, token, item(kind="text", anchor=TEXT_ANCHOR))
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.anchor == {
            "selectors": [TEXT_ANCHOR["selectors"][2], TEXT_ANCHOR["selectors"][0], TEXT_ANCHOR["selectors"][1]],
            "point": None,
        }
        assert stored.quote == "2,4 miljoen"

    async def test_a_quote_alone_gets_empty_affixes_and_no_point(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        anchor = {"selectors": [{"type": "TextQuoteSelector", "exact": "kop", "prefix": None}]}
        response = await create(client, token, item(kind="text", anchor=anchor))
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.anchor == {
            "selectors": [{"type": "TextQuoteSelector", "exact": "kop", "prefix": "", "suffix": ""}],
            "point": None,
        }

    async def test_a_code_point_the_servers_tables_do_not_know_is_taken_everywhere(self, client, environment):
        """An emoji newer than the server's Unicode tables, in the comment,
        the name, the quote with its context, the selector and the title."""
        assert unicodedata.category(UNASSIGNED) == "Cn"
        token = page_token(environment.app, environment.world.site_id)
        remark = f"Mooi {UNASSIGNED}"
        quote = {"type": "TextQuoteSelector", "exact": remark, "prefix": UNASSIGNED, "suffix": f"{UNASSIGNED}!"}
        css = {"type": "CssSelector", "value": f"#kop{UNASSIGNED}"}
        value = item(
            kind="text",
            comment=remark,
            page={"path": ROOT, "title": remark},
            anchor={"selectors": [css, quote], "point": None},
            author={"kind": "name", "name": remark},
        )
        response = await create(client, token, value)
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert (stored.comment, stored.author_name, stored.quote, stored.page_title) == (remark, remark, remark, remark)
        assert stored.anchor == {"selectors": [quote, css], "point": None}

    async def test_the_two_joiners_writing_needs_are_taken_everywhere(self, client, environment):
        """Both are format characters, and both stay as they came: in the
        comment, the name, the quote with its context, the selector, and the
        title, which is cleaned of every other format character."""
        assert {unicodedata.category(char) for char in "\u200c\u200d"} == {"Cf"}
        token = page_token(environment.app, environment.world.site_id)
        remark = f"Dank je {JOINED_EMOJI} {PERSIAN}"
        quote = {"type": "TextQuoteSelector", "exact": remark, "prefix": PERSIAN, "suffix": JOINED_EMOJI}
        css = {"type": "CssSelector", "value": f"#{PERSIAN}"}
        value = item(
            kind="text",
            comment=remark,
            page={"path": ROOT, "title": f"\u202e{remark}\u200b"},
            anchor={"selectors": [css, quote], "point": None},
            author={"kind": "name", "name": PERSIAN},
        )
        response = await create(client, token, value)
        assert response.status_code == 201, response.text
        (stored,) = await stored_items(environment.factory)
        assert (stored.comment, stored.quote, stored.page_title) == (remark, remark, remark)
        assert stored.author_name == PERSIAN
        assert stored.anchor == {"selectors": [quote, css], "point": None}

    async def test_the_title_is_cleaned_and_cut_rather_than_refused(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        title = "‮ Begroting\x00 " + _long(400, "b")
        response = await create(client, token, item(page={"path": ROOT, "title": title}))
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.page_title == ("Begroting " + _long(400, "b"))[:300]
        assert stored.page_path == ""

    @pytest.mark.parametrize("title", [None, " ​‮ ", ""], ids=["none", "only-format-characters", "empty"])
    async def test_a_title_with_nothing_left_is_none(self, client, environment, title):
        token = page_token(environment.app, environment.world.site_id)
        page = {"path": ROOT} if title is None else {"path": ROOT, "title": title}
        assert (await create(client, token, item(page=page))).status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.page_title is None

    async def test_a_path_as_location_pathname_gives_it_is_kept_as_it_came(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        path = f"{ROOT}caf%C3%A9/[1]/~a;b=c,d'e(f)*g+h!$&@|^/index.html"
        response = await create(client, token, item(page={"path": path, "title": None}))
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.page_path == path.removeprefix(ROOT)

    @pytest.mark.parametrize(
        "note",
        [None, {"region": {"x": 10, "y": 20.5, "width": 300, "height": 200}}, {}, "absent"],
        ids=["null", "a-region", "no-region", "absent"],
    )
    async def test_what_the_item_says_about_its_screenshot_is_checked_not_kept(self, client, environment, note):
        token = page_token(environment.app, environment.world.site_id)
        value = item() if note == "absent" else item(screenshot=note)
        if note == "absent":
            del value["screenshot"]
        assert (await create(client, token, value)).status_code == 201

    async def test_a_preview_item_stores_its_ref_and_preview_version(self, client, environment):
        world = environment.world
        token = page_token(environment.app, world.site_id, ref="pr-1", version_id=world.preview_version_id)
        response = await create(client, token, item(page={"path": f"{PREVIEW_ROOT}docs/", "title": None}))
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.target is VersionTarget.PREVIEW
        assert stored.preview_ref == "pr-1"
        assert stored.version_id == world.preview_version_id
        assert stored.page_path == "docs/"

    async def test_a_live_path_is_refused_on_a_preview_token(self, client, environment):
        token = page_token(environment.app, environment.world.site_id, ref="pr-1")
        assert_invalid(await create(client, token, item(page={"path": f"{ROOT}docs/"})), "page.path")

    async def test_a_name_item_stores_the_typed_name_only(self, client, environment):
        app, world = environment.app, environment.world
        session = consented_session(app, world.site_id)
        token = page_token(app, world.site_id, session_id=session.id)
        response = await create(client, token, item(author={"kind": "name", "name": "  Jan de Vries  "}))
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.author_kind.value == "name"
        assert (stored.author_sub, stored.author_name, stored.author_email) == (None, "Jan de Vries", None)

    async def test_anonymous_stores_nothing_even_with_a_consented_session(self, client, environment):
        app, world = environment.app, environment.world
        session = consented_session(app, world.site_id)
        token = page_token(app, world.site_id, session_id=session.id)
        response = await create(client, token, item(author={"kind": "anonymous", "name": "   "}))
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert (stored.author_sub, stored.author_name, stored.author_email) == (None, None, None)


class TestTheVersionItWasGivenOn:
    """Written through `(SELECT id FROM versions WHERE ...)`, so a version
    gone since the page view gives NULL instead of a foreign key error."""

    async def test_a_preview_redeployed_after_the_page_view_gives_null(self, client, environment):
        world = environment.world
        token = page_token(environment.app, world.site_id, ref="pr-1", version_id=world.preview_version_id)
        async with environment.factory() as db:
            newer = Version(site_id=world.site_id, target=VersionTarget.PREVIEW, member_id=world.member_id)
            db.add(newer)
            await db.flush()
            await db.execute(update(Preview).where(Preview.ref == "pr-1").values(version_id=newer.id))
            await db.execute(delete(Version).where(Version.id == world.preview_version_id))
            await db.commit()
        response = await create(client, token, item(page={"path": PREVIEW_ROOT, "title": None}))
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.version_id is None

    async def test_a_version_of_another_site_gives_null(self, client, environment):
        world = environment.world
        token = page_token(environment.app, world.site_id, version_id=world.other_live_version_id)
        assert (await create(client, token)).status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.version_id is None

    async def test_a_token_without_a_version_gives_null(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        assert (await create(client, token)).status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.version_id is None


# --- Who may give feedback, and as whom ------------------------------------------


class TestAuthors:
    @pytest.mark.parametrize(
        ("identity", "author", "with_session", "outcome"),
        [
            (FeedbackIdentity.ACCOUNT, "account", True, 201),
            (FeedbackIdentity.ACCOUNT, "account", False, 403),
            (FeedbackIdentity.ACCOUNT, "name", True, 422),
            (FeedbackIdentity.ACCOUNT, "anonymous", True, 422),
            (FeedbackIdentity.NAME, "account", True, 201),
            (FeedbackIdentity.NAME, "name", False, 201),
            (FeedbackIdentity.NAME, "anonymous", False, 422),
            (FeedbackIdentity.ANONYMOUS, "account", True, 201),
            (FeedbackIdentity.ANONYMOUS, "account", False, 403),
            (FeedbackIdentity.ANONYMOUS, "name", False, 201),
            (FeedbackIdentity.ANONYMOUS, "anonymous", False, 201),
        ],
    )
    async def test_each_author_kind_against_each_setting(
        self, client, environment, identity, author, with_session, outcome
    ):
        app, world = environment.app, environment.world
        async with environment.factory() as db:
            await db.execute(update(Site).where(Site.id == world.site_id).values(feedback_identity=identity))
            await db.commit()
        session_id = consented_session(app, world.site_id).id if with_session else None
        token = page_token(app, world.site_id, session_id=session_id)
        value = item(author={"kind": author, "name": "Jan"} if author == "name" else {"kind": author})
        response = await create(client, token, value)
        if outcome == 201:
            assert response.status_code == 201
        elif outcome == 403:
            assert_problem(response, 403, "FEEDBACK_ACCOUNT_REQUIRED")
        else:
            assert_problem(response, 422, "FEEDBACK_AUTHOR_NOT_ALLOWED")

    async def test_an_account_item_takes_its_author_from_the_session(self, client, environment):
        app, world = environment.app, environment.world
        session = consented_session(app, world.site_id, name="K" * 250, email="Kijker@Example.ORG")
        token = page_token(app, world.site_id, session_id=session.id)
        response = await create(client, token, item(author={"kind": "account", "name": None}))
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.author_kind.value == "account"
        assert stored.author_sub == "kijker"
        assert stored.author_name == "K" * 200
        assert stored.author_email == "kijker@example.org"

    async def test_an_unverified_email_or_a_missing_name_is_not_stored(self, client, environment):
        app, world = environment.app, environment.world
        session = consented_session(app, world.site_id, name=None, email="kijker@example.org", email_verified=False)
        token = page_token(app, world.site_id, session_id=session.id)
        assert (await create(client, token, item(author={"kind": "account"}))).status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert (stored.author_sub, stored.author_name, stored.author_email) == ("kijker", None, None)


# --- Screenshots -------------------------------------------------------------------


class TestScreenshots:
    @pytest.mark.parametrize(
        ("data", "media_type", "size"),
        [(PNG_FILE, "image/png", (5, 3)), (JPEG_SIPS, "image/jpeg", (5, 3)), (WEBP_LOSSY, "image/webp", (5, 3))],
        ids=["png", "jpeg", "webp"],
    )
    async def test_each_format_is_stored_by_what_its_bytes_are(self, client, environment, data, media_type, size):
        token = page_token(environment.app, environment.world.site_id)
        # The declared type says something else, and counts for nothing.
        response = await create(client, token, screenshot=data, screenshot_type="application/x-msdownload")
        assert response.status_code == 201
        async with environment.factory() as db:
            (shot,) = (await db.scalars(select(FeedbackScreenshot))).all()
        assert str(shot.item_id) == response.json()["id"]
        assert shot.media_type == media_type
        assert shot.data == data
        assert shot.size_bytes == len(data)
        assert (shot.width, shot.height) == size

    @pytest.mark.parametrize(
        "data",
        [b"GIF89a" + b"\x00" * 20, b"", PNG_ANIMATED, png_header(5000, 10), png_header(4096, 4000)],
        ids=["wrong-magic", "empty", "animated", "too-wide", "too-many-pixels"],
    )
    async def test_anything_else_is_refused_and_nothing_stored(self, client, environment, data):
        token = page_token(environment.app, environment.world.site_id)
        response = await create(client, token, screenshot=data)
        assert_problem(response, 422, "FEEDBACK_SCREENSHOT_INVALID")
        assert await stored_items(environment.factory) == []

    async def test_a_screenshot_over_the_limit_is_refused_while_it_streams_in(self, environment):
        below = environment.app_with(feedback_screenshot_max_bytes=len(PNG_FILE) - 1)
        async with client_for(below) as other:
            response = await create(other, page_token(below, environment.world.site_id), screenshot=PNG_FILE)
        assert_problem(response, 413, "FEEDBACK_SCREENSHOT_TOO_LARGE")
        assert await stored_items(environment.factory) == []
        at = environment.app_with(feedback_screenshot_max_bytes=len(PNG_FILE))
        async with client_for(at) as other:
            response = await create(other, page_token(at, environment.world.site_id), screenshot=PNG_FILE)
        assert response.status_code == 201

    async def test_an_item_is_not_held_to_the_screenshot_limit(self, environment):
        app = environment.app_with(feedback_screenshot_max_bytes=10)
        async with client_for(app) as other:
            response = await create(other, page_token(app, environment.world.site_id))
        assert response.status_code == 201

    async def test_a_declared_body_over_the_limit_is_refused_unread(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        limit = environment.app.state.settings.feedback_screenshot_max_bytes + FEEDBACK_BODY_ALLOWANCE
        headers = auth(
            token, **{"Content-Type": "multipart/form-data; boundary=grens", "Content-Length": str(limit + 1)}
        )

        async def body() -> AsyncIterator[bytes]:
            yield b"--grens--\r\n"

        response = await client.post(ITEMS, content=body(), headers=headers)
        assert_problem(response, 413, "FEEDBACK_SCREENSHOT_TOO_LARGE")

    async def test_a_chunked_body_over_the_limit_is_refused_while_it_streams_in(self, environment):
        """No Content-Length and no part over its own bound: what runs over
        is counted all the same, here after the closing boundary."""
        app = environment.app_with(feedback_screenshot_max_bytes=100)
        token = page_token(app, environment.world.site_id)
        headers = auth(token, **{"Content-Type": "multipart/form-data; boundary=grens"})
        payload = json.dumps(item()).encode()

        async def body() -> AsyncIterator[bytes]:
            yield b'--grens\r\nContent-Disposition: form-data; name="item"\r\n\r\n' + payload + b"\r\n--grens--\r\n"
            for _ in range(70):
                yield b" " * 1024

        async with client_for(app) as other:
            response = await other.post(ITEMS, content=body(), headers=headers)
        assert_problem(response, 413, "FEEDBACK_SCREENSHOT_TOO_LARGE")

    async def test_a_small_chunked_body_is_read_in_full(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        payload = json.dumps(item()).encode()

        async def body() -> AsyncIterator[bytes]:
            yield b'--grens\r\nContent-Disposition: form-data; name="item"\r\n\r\n'
            yield payload[:10]
            yield payload[10:] + b"\r\n--grens--\r\n"

        headers = auth(token, **{"Content-Type": "multipart/form-data; boundary=grens"})
        response = await client.post(ITEMS, content=body(), headers=headers)
        assert response.status_code == 201

    async def test_the_quota_of_the_site_counts_its_stored_screenshots(self, environment):
        world = environment.world
        size = len(PNG_FILE)
        app = environment.app_with(feedback_site_screenshot_max_bytes=2 * size)
        async with client_for(app) as other:
            for _ in range(2):
                assert (await create(other, page_token(app, world.site_id), screenshot=PNG_FILE)).status_code == 201
            refused = await create(other, page_token(app, world.site_id), screenshot=PNG_FILE)
            assert_problem(refused, 409, "FEEDBACK_SCREENSHOT_QUOTA")
            # Without a screenshot the item still goes in, and another site
            # has a quota of its own.
            assert (await create(other, page_token(app, world.site_id))).status_code == 201
            ander = item(page={"path": "/aurora/ander/", "title": None})
            on_ander = await create(other, page_token(app, world.other_site_id), ander, screenshot=PNG_FILE)
            assert on_ander.status_code == 201
        assert len(await stored_items(environment.factory)) == 4

    async def test_the_platform_quota_counts_every_site(self, environment):
        world = environment.world
        size = len(PNG_FILE)
        app = environment.app_with(feedback_total_screenshot_max_bytes=2 * size)
        ander = item(page={"path": "/aurora/ander/", "title": None})
        async with client_for(app) as other:
            assert (await create(other, page_token(app, world.site_id), screenshot=PNG_FILE)).status_code == 201
            on_ander = await create(other, page_token(app, world.other_site_id), ander, screenshot=PNG_FILE)
            assert on_ander.status_code == 201
            refused = await create(other, page_token(app, world.site_id), screenshot=PNG_FILE)
        assert_problem(refused, 409, "FEEDBACK_SCREENSHOT_QUOTA")


def feedback_log(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "toon.serving.feedback_api"]


class TestTheOperatorHearsOfAFullPlatform:
    """Once the platform-wide cap is reached, every site refuses a screenshot
    that does not fit, the sites that never sent one included: the operator
    hears of it. A site's own cap is the business of its team and leaves the
    log alone."""

    @pytest.mark.parametrize("site_full_too", [False, True], ids=["platform", "platform-and-site"])
    async def test_a_refusal_by_the_platform_cap_is_a_warning_once_per_interval(
        self, environment, monkeypatch, caplog, site_full_too
    ):
        world = environment.world
        size = len(PNG_FILE)
        now = [5000.0]
        monkeypatch.setattr(feedback_api, "_clock", lambda: now[0])
        monkeypatch.setattr(feedback_api, "_quota_warnings", InMemoryCounter())
        caps = {"feedback_total_screenshot_max_bytes": size}
        if site_full_too:
            caps["feedback_site_screenshot_max_bytes"] = size
        app = environment.app_with(**caps)
        remark = "Het bedrag van Jan Jansen klopt niet"
        interval = feedback_api.QUOTA_WARNING_INTERVAL_S
        with caplog.at_level(logging.INFO, logger="toon.serving.feedback_api"):
            async with client_for(app, address="192.0.2.7") as other:
                assert (await create(other, page_token(app, world.site_id), screenshot=PNG_FILE)).status_code == 201
                assert feedback_log(caplog) == []
                for moment in (5000.0, 5001.0, 5000.0 + interval - 1, 5000.0 + interval):
                    now[0] = moment
                    token = page_token(app, world.site_id)
                    refused = await create(other, token, item(comment=remark), screenshot=PNG_FILE)
                    assert_problem(refused, 409, "FEEDBACK_SCREENSHOT_QUOTA")
        # Byte counts only: nothing of the person, their address, the site or
        # what was sent.
        line = (
            f"The platform-wide cap on feedback screenshots is reached: {size} of {size} bytes are stored, and a "
            f"screenshot of {size} bytes was refused. Every site refuses a screenshot that does not fit until "
            "space is freed or TOON_FEEDBACK_TOTAL_SCREENSHOT_MAX_BYTES is raised. Logged at most once per "
            f"{interval} seconds."
        )
        logged = feedback_log(caplog)
        assert [(record.levelno, record.getMessage()) for record in logged] == [(logging.WARNING, line)] * 2

    async def test_a_refusal_by_the_cap_of_a_site_is_not_logged(self, environment, monkeypatch, caplog):
        world = environment.world
        monkeypatch.setattr(feedback_api, "_quota_warnings", InMemoryCounter())
        app = environment.app_with(feedback_site_screenshot_max_bytes=len(PNG_FILE))
        with caplog.at_level(logging.INFO, logger="toon.serving.feedback_api"):
            async with client_for(app) as other:
                assert (await create(other, page_token(app, world.site_id), screenshot=PNG_FILE)).status_code == 201
                refused = await create(other, page_token(app, world.site_id), screenshot=PNG_FILE)
        assert_problem(refused, 409, "FEEDBACK_SCREENSHOT_QUOTA")
        assert feedback_log(caplog) == []


class TestTheOpenItemCap:
    async def test_the_cap_counts_new_and_seen_items_of_this_site_only(self, environment):
        world = environment.world
        await add_item(environment.factory, world.site_id, status=FeedbackStatus.NEW)
        await add_item(environment.factory, world.site_id, status=FeedbackStatus.SEEN)
        await add_item(
            environment.factory,
            world.site_id,
            status=FeedbackStatus.RESOLVED,
            resolved_at=func.now(),
        )
        await add_item(environment.factory, world.other_site_id)
        app = environment.app_with(feedback_site_max_open=3)
        async with client_for(app) as other:
            assert (await create(other, page_token(app, world.site_id))).status_code == 201
            refused = await create(other, page_token(app, world.site_id), headers={"Accept-Language": "nl"})
        assert_problem(refused, 409, "FEEDBACK_SITE_FULL", locale="nl")


# Every request of a burst comes from one client, which may otherwise have
# only a few bodies in flight at once.
ONE_CLIENT_BURST: dict[str, object] = {"feedback_bodies_per_client": 100}


class TestConcurrentCreates:
    """Under the advisory lock the checks and the insert of one create cannot
    interleave with another's: however many arrive at once, the cap and the
    quotas hold exactly."""

    async def _burst(self, app: FastAPI, requests: list[tuple[str, object, bytes | None]]) -> list[httpx.Response]:
        async with client_for(app) as other:
            return await asyncio.gather(
                *(create(other, token, value, screenshot=shot) for token, value, shot in requests)
            )

    async def test_the_open_item_cap(self, committed, tmp_path):
        world = await seed(committed)
        app = make_app(make_settings(tmp_path, feedback_site_max_open=3, **ONE_CLIENT_BURST), committed)
        responses = await self._burst(app, [(page_token(app, world.site_id), item(), None)] * 12)
        assert sorted(response.status_code for response in responses) == [201] * 3 + [409] * 9
        assert len(await stored_items(committed)) == 3

    async def test_the_screenshot_quota_of_a_site(self, committed, tmp_path):
        world = await seed(committed)
        settings = make_settings(tmp_path, feedback_site_screenshot_max_bytes=3 * len(PNG_FILE), **ONE_CLIENT_BURST)
        app = make_app(settings, committed)
        responses = await self._burst(app, [(page_token(app, world.site_id), item(), PNG_FILE)] * 12)
        assert sorted(response.status_code for response in responses) == [201] * 3 + [409] * 9

    async def test_the_platform_quota_across_two_sites(self, committed, tmp_path):
        world = await seed(committed)
        settings = make_settings(tmp_path, feedback_total_screenshot_max_bytes=3 * len(PNG_FILE), **ONE_CLIENT_BURST)
        app = make_app(settings, committed)
        ander = item(page={"path": "/aurora/ander/", "title": None})
        requests = [(page_token(app, world.site_id), item(), PNG_FILE)] * 6 + [
            (page_token(app, world.other_site_id), ander, PNG_FILE)
        ] * 6
        responses = await self._burst(app, requests)
        assert sorted(response.status_code for response in responses) == [201] * 3 + [409] * 9
        async with committed() as db:
            assert await db.scalar(select(func.sum(FeedbackScreenshot.size_bytes))) == 3 * len(PNG_FILE)


# --- The bodies in flight -----------------------------------------------------------

# The smallest budget there may be: one whole create at a screenshot limit of
# 1 KiB.
BUDGET = 1024 + FEEDBACK_BODY_ALLOWANCE
ONE_CREATE = {"feedback_screenshot_max_bytes": 1024, "feedback_body_budget_bytes": BUDGET}


async def fill_budget(
    client: httpx.AsyncClient, token: str, *, free: int = 0
) -> tuple[list[HeldBody], list[asyncio.Task[httpx.Response]]]:
    """Three whole creates of one client, held before their end, that leave
    `free` bytes of ONE_CREATE's budget."""
    sizes = [(BUDGET - free) // 3] * 2
    sizes.append(BUDGET - free - sum(sizes))
    bodies = [whole_body(size) for size in sizes]
    return bodies, [await held_create(client, token, body) for body in bodies]


async def finish(held: list[HeldBody], sent: list[asyncio.Task[httpx.Response]]) -> list[int]:
    """Releases the held creates one by one, as they share the test's one
    database connection, and gives their statuses."""
    statuses = []
    for body, each in zip(held, sent, strict=True):
        body.release.set()
        statuses.append((await each).status_code)
    return statuses


async def two_chunks() -> AsyncIterator[bytes]:
    yield ITEM_HEAD
    yield ITEM_TAIL


class TestTheBodyBudget:
    """However many uploads stall, they hold no more than the budget, and no
    client more than its share. A body holds room for the bytes that have
    arrived, never for the length it declares, and a refusal for lack of room
    reads nothing more and spends nothing."""

    async def test_a_create_beyond_the_budget_is_busy_until_the_room_is_given_back(self, environment):
        world = environment.world
        app = environment.app_with(**ONE_CREATE)
        token = page_token(app, world.site_id)
        async with client_for(app) as client, client_for(app, address="192.0.2.7") as other:
            held, sent = await fill_budget(client, token)
            assert app.state.feedback_bodies.in_flight == BUDGET
            for language in ("en", "nl"):
                busy = await create(other, token, headers={"Accept-Language": language})
                assert_problem(busy, 503, "FEEDBACK_BUSY", locale=language)
                assert busy.headers["retry-after"] == "60"
            # A write is charged once its body is in: the held creates have
            # spent none yet.
            assert writes_spent(app, world.site_id, author="ip:192.0.2.7") == (0, 0)
            assert await finish(held, sent) == [201] * 3
            assert (await create(other, token)).status_code == 201
        assert app.state.feedback_bodies.in_flight == 0
        assert app.state.feedback_bodies._per_client == {}
        assert len(await stored_items(environment.factory)) == 4

    async def test_a_length_declared_and_not_sent_holds_no_room(self, environment):
        """The route's whole limit is ONE_CREATE's whole budget: a body that
        declares it and sends nothing still leaves the budget to the rest."""
        app = environment.app_with(**ONE_CREATE)
        token = page_token(app, environment.world.site_id)
        silent = HeldBody(head=b"", tail=ITEM_HEAD + ITEM_TAIL)
        async with client_for(app) as client:
            first = await held_create(client, token, silent, declared=BUDGET)
            assert app.state.feedback_bodies.in_flight == 0
            assert app.state.feedback_bodies._per_client == {"ip:127.0.0.1": 1}
            assert (await create(client, token)).status_code == 201
            silent.release.set()
            assert (await first).status_code == 201
        assert app.state.feedback_bodies.in_flight == 0
        assert app.state.feedback_bodies._per_client == {}

    @pytest.mark.parametrize("declared", [False, True], ids=["chunked", "declared"])
    async def test_a_body_holds_room_for_the_bytes_that_arrived(self, environment, declared):
        """Not the route's whole limit, and not the length it declares."""
        app = environment.app_with(**ONE_CREATE)
        token = page_token(app, environment.world.site_id)
        held = HeldBody()
        async with client_for(app) as client:
            first = await held_create(client, token, held, declared=held.length if declared else None)
            assert app.state.feedback_bodies.in_flight == len(held.head)
            held.release.set()
            assert (await first).status_code == 201
        assert app.state.feedback_bodies.in_flight == 0

    async def test_a_chunk_that_does_not_fit_is_busy_and_its_body_gives_back_what_it_took(self, environment):
        world = environment.world
        app = environment.app_with(**ONE_CREATE)
        token = page_token(app, world.site_id)
        bodies = app.state.feedback_bodies
        assert len(ITEM_HEAD) < 100 < len(ITEM_HEAD) + len(ITEM_TAIL)
        async with client_for(app) as client, client_for(app, address="192.0.2.7") as other:
            held, sent = await fill_budget(client, token, free=100)
            headers = auth(token, **{"Content-Type": MULTIPART})
            busy = await other.post(ITEMS, content=two_chunks(), headers=headers)
            assert_problem(busy, 503, "FEEDBACK_BUSY")
            assert busy.headers["retry-after"] == "60"
            assert bodies.in_flight == BUDGET - 100
            assert bodies._per_client == {"ip:127.0.0.1": 3}
            assert writes_spent(app, world.site_id, author="ip:192.0.2.7") == (0, 0)
            assert await finish(held, sent) == [201] * 3
        assert bodies.in_flight == 0
        assert len(await stored_items(environment.factory)) == 3

    async def test_a_body_beyond_the_limit_is_too_large_not_busy(self, environment):
        """The bound comes before the room: a body that can never be taken
        is 413, which the widget does not ask to try again, even where
        ONE_CREATE's budget, the size of the limit, cannot hold its chunk."""
        app = environment.app_with(**ONE_CREATE)
        token = page_token(app, environment.world.site_id)

        async def one_chunk() -> AsyncIterator[bytes]:
            yield ITEM_HEAD + b" " * BUDGET

        async with client_for(app) as client:
            response = await client.post(
                ITEMS, content=one_chunk(), headers=auth(token, **{"Content-Type": MULTIPART})
            )
        assert_problem(response, 413, "FEEDBACK_SCREENSHOT_TOO_LARGE")
        assert app.state.feedback_bodies.in_flight == 0

    @pytest.mark.parametrize("ground", ["full-budget", "client-share"])
    async def test_the_retry_after_is_the_deadline(self, environment, ground):
        """Every body in flight is read, or refused, within the deadline."""
        app = environment.app_with(
            **ONE_CREATE, feedback_bodies_per_client=3, feedback_body_deadline_seconds=5
        )
        token = page_token(app, environment.world.site_id)
        address = "192.0.2.7" if ground == "full-budget" else "127.0.0.1"
        async with client_for(app) as client, client_for(app, address=address) as asking:
            held, sent = await fill_budget(client, token)
            busy = await create(asking, token)
            await finish(held, sent)
        assert_problem(busy, 503, "FEEDBACK_BUSY")
        assert busy.headers["retry-after"] == "5"

    async def test_a_client_has_no_more_bodies_in_flight_than_its_share(self, environment):
        world = environment.world
        app = environment.app_with(feedback_bodies_per_client=1)
        token = page_token(app, world.site_id)
        held = HeldBody()
        async with client_for(app) as here, client_for(app, address="192.0.2.7") as elsewhere:
            first = await held_create(here, token, held)
            assert_problem(await create(here, token), 503, "FEEDBACK_BUSY")
            assert (await create(elsewhere, token)).status_code == 201
            held.release.set()
            assert (await first).status_code == 201
            assert (await create(here, token)).status_code == 201
        assert app.state.feedback_bodies._per_client == {}

    async def test_an_ipv6_client_counts_by_its_64(self, environment):
        """As the rate limiter counts: one subscriber holds a whole /64."""
        app = environment.app_with(feedback_bodies_per_client=1)
        token = page_token(app, environment.world.site_id)
        held = HeldBody()
        async with (
            client_for(app, address="2001:db8:1:2::1") as one,
            client_for(app, address="2001:db8:1:2:ffff::9") as same_64,
            client_for(app, address="2001:db8:1:3::1") as next_64,
        ):
            first = await held_create(one, token, held)
            assert_problem(await create(same_64, token), 503, "FEEDBACK_BUSY")
            assert (await create(next_64, token)).status_code == 201
            held.release.set()
            assert (await first).status_code == 201

    async def test_a_reply_takes_its_room_too(self, environment):
        world = environment.world
        app = environment.app_with(feedback_bodies_per_client=1)
        session = consented_session(app, world.site_id)
        own = await account_item(environment.factory, world.site_id)
        held = HeldBody()
        async with client_for(app) as client:
            first = await held_create(client, page_token(app, world.site_id), held)
            refused = await reply(client, page_token(app, world.site_id, session_id=session.id), own)
            held.release.set()
            await first
        assert_problem(refused, 503, "FEEDBACK_BUSY")
        assert writes_spent(app, world.site_id, author="session:kijker") == (0, 1)
        assert await replies_on(environment.factory, own) == 0

    async def test_a_reply_holds_room_for_the_bytes_that_arrived(self, environment):
        world = environment.world
        app = environment.app
        session = consented_session(app, world.site_id)
        own = await account_item(environment.factory, world.site_id)
        held = HeldBody(head=b'{"body": "Ik bedoel', tail=b' de tweede alinea."}')
        headers = auth(page_token(app, world.site_id, session_id=session.id), **{"Content-Type": "application/json"})
        async with client_for(app) as client:
            sent = await held_post(client, f"{ITEMS}/{own}/replies", held, {**headers})
            assert app.state.feedback_bodies.in_flight == len(held.head)
            held.release.set()
            assert (await sent).status_code == 201
        assert app.state.feedback_bodies.in_flight == 0

    @pytest.mark.parametrize("declared", ["9" * 21, "1" + "0" * 5000], ids=["21-digits", "beyond-int"])
    async def test_a_length_longer_than_any_body_is_refused_unread(self, client, environment, declared):
        """Refused before int() reads it, which would fail on 5000 digits."""
        token = page_token(environment.app, environment.world.site_id)
        body = HeldBody()
        headers = auth(token, **{"Content-Type": MULTIPART, "Content-Length": declared})
        response = await client.post(ITEMS, content=body.chunks(), headers=headers)
        assert_problem(response, 413, "FEEDBACK_SCREENSHOT_TOO_LARGE")
        assert body.pulled is False
        assert writes_spent(environment.app, environment.world.site_id) == (0, 0)

    @pytest.mark.parametrize(
        "declared", ["²", "1e3", "-5", " 12"], ids=["superscript-digit", "float", "negative", "space"]
    )
    async def test_a_length_that_does_not_read_is_no_length(self, environment, declared):
        """`isdigit` alone would take a superscript two for a digit, and
        `int` would then fail on it. The body is read to the route's limit,
        with room for what arrives."""
        app = environment.app_with(**ONE_CREATE)
        token = page_token(app, environment.world.site_id)
        headers = {**auth(token, **{"Content-Type": MULTIPART}), "Content-Length": declared.encode("latin-1")}
        held = HeldBody()
        async with client_for(app) as client:
            sent = await held_post(client, ITEMS, held, headers)
            assert app.state.feedback_bodies.in_flight == len(held.head)
            held.release.set()
            response = await sent
        assert response.status_code == 201, response.text

    @pytest.mark.parametrize("route", ["create", "reply"])
    async def test_a_body_still_coming_at_the_deadline_is_408(self, environment, route, caplog):
        world = environment.world
        app = environment.app_with(feedback_body_deadline_seconds=1)
        session = consented_session(app, world.site_id)
        token = page_token(app, world.site_id, session_id=session.id)
        own = await account_item(environment.factory, world.site_id)
        never = asyncio.Event()

        async def stalled() -> AsyncIterator[bytes]:
            yield b'{"body": "Ik bedoel' if route == "reply" else ITEM_HEAD
            await never.wait()

        if route == "reply":
            path, content_type = f"{ITEMS}/{own}/replies", "application/json"
        else:
            path, content_type = ITEMS, MULTIPART
        headers = auth(token, **{"Content-Type": content_type, "Accept-Language": "nl"})
        started = time.monotonic()
        with caplog.at_level(logging.INFO, logger="toon.serving.feedback_api"):
            async with client_for(app) as client:
                response = await client.post(path, content=stalled(), headers=headers)
        assert_problem(response, 408, "FEEDBACK_TIMEOUT", locale="nl")
        assert 1 <= time.monotonic() - started < 10
        assert app.state.feedback_bodies.in_flight == 0
        assert writes_spent(app, world.site_id, author="session:kijker") == (0, 0)
        assert await replies_on(environment.factory, own) == 0
        assert len(await stored_items(environment.factory)) == 1
        assert [record for record in caplog.records if record.levelno >= logging.WARNING] == []

    async def test_an_aborted_upload_is_one_line_in_the_log_not_a_traceback(self, environment, caplog):
        """The client is gone, so nobody reads the answer; what matters is
        that the room is given back, the write refunded and the log not
        flooded with a traceback for every upload someone gives up on."""
        world = environment.world
        app = environment.app
        token = page_token(app, world.site_id)
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": ITEMS,
            "raw_path": ITEMS.encode(),
            "query_string": b"",
            "root_path": "",
            "server": ("toon.example", 443),
            "client": ("127.0.0.1", 123),
            "headers": [
                (b"host", b"toon.example"),
                (b"authorization", f"Toon-Feedback {token}".encode()),
                (b"content-type", MULTIPART.encode()),
            ],
        }
        incoming = iter([{"type": "http.request", "body": ITEM_HEAD, "more_body": True}, {"type": "http.disconnect"}])
        sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return next(incoming)

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        with caplog.at_level(logging.INFO, logger="toon.serving.feedback_api"):
            await app(scope, receive, send)
        assert sent[0]["status"] == 400
        ours = [record for record in caplog.records if record.name == "toon.serving.feedback_api"]
        assert [(record.levelno, record.getMessage(), record.exc_info) for record in ours] == [
            (logging.INFO, f"The client went away during POST {ITEMS}", None)
        ]
        assert [record for record in caplog.records if record.levelno >= logging.WARNING] == []
        assert app.state.feedback_bodies.in_flight == 0
        assert app.state.feedback_bodies._per_client == {}
        assert writes_spent(app, world.site_id) == (0, 0)
        assert await stored_items(environment.factory) == []

    @pytest.mark.parametrize(
        "author", [{"kind": "account"}, {"kind": "name", "name": "Jan"}, {"kind": "anonymous"}], ids=lambda a: a["kind"]
    )
    async def test_a_site_for_accounts_only_refuses_a_create_without_a_session_unread(
        self, client, environment, monkeypatch, author
    ):
        """Every such create is refused anyway: before its body is read, it
        takes no place among the bodies in flight and spends no write,
        whatever author it names."""
        world = environment.world
        async with environment.factory() as db:
            await db.execute(
                update(Site).where(Site.id == world.site_id).values(feedback_identity=FeedbackIdentity.ACCOUNT)
            )
            await db.commit()

        def no_room_asked(*args: object, **kwargs: object) -> bool:
            raise AssertionError("an early refusal asked for room in the budget")

        monkeypatch.setattr(environment.app.state.feedback_bodies, "admit", no_room_asked)
        monkeypatch.setattr(environment.app.state.feedback_bodies, "take", no_room_asked)
        body = HeldBody(tail=json.dumps(item(author=author)).encode())
        token = page_token(environment.app, world.site_id)
        response = await client.post(ITEMS, content=body.chunks(), headers=auth(token, **{"Content-Type": MULTIPART}))
        assert_problem(response, 403, "FEEDBACK_ACCOUNT_REQUIRED")
        assert body.pulled is False
        assert writes_spent(environment.app, world.site_id) == (0, 0)


class TestBodyBudgetBookkeeping:
    def test_a_chunk_that_fits_exactly_takes_the_last_room(self):
        bodies = BodyBudget()
        assert bodies.take(60, budget=100)
        assert bodies.take(40, budget=100)
        assert not bodies.take(1, budget=100)
        assert bodies.in_flight == 100
        # An empty chunk still fits, with no room left.
        assert bodies.take(0, budget=100)

    def test_a_client_has_no_more_bodies_than_its_share(self):
        bodies = BodyBudget()
        assert bodies.admit("ip:a", per_client=2)
        assert bodies.admit("ip:a", per_client=2)
        assert not bodies.admit("ip:a", per_client=2)
        assert bodies.admit("ip:b", per_client=2)
        assert (bodies.in_flight, bodies._per_client) == (0, {"ip:a": 2, "ip:b": 1})

    def test_giving_one_body_back_keeps_the_clients_others(self):
        bodies = BodyBudget()
        assert bodies.admit("ip:a", per_client=2)
        assert bodies.take(10, budget=100)
        assert bodies.admit("ip:a", per_client=2)
        assert bodies.take(20, budget=100)
        bodies.give_back("ip:a", 10)
        assert (bodies.in_flight, bodies._per_client) == (20, {"ip:a": 1})
        assert bodies.admit("ip:a", per_client=2)
        bodies.give_back("ip:a", 20)
        bodies.give_back("ip:a", 0)
        assert (bodies.in_flight, bodies._per_client) == (0, {})


class TestTheMemoryOfACreate:
    async def test_the_screenshot_is_held_once_as_the_budget_counts_it(self, tmp_path):
        """The budget counts each byte of a body once, as its chunk arrives,
        and a create holds it once: in the parts the parser fills. A copy of
        the screenshot on its way to the database would hold it a second
        time, outside the budget, and a create would take twice its room."""
        size = 2 * 1024 * 1024
        screenshot = b"\xff\xd8\xff" + bytes(size - 3)
        body = delimited_body(data=screenshot)
        settings = make_settings(tmp_path)
        app = FastAPI()
        app.state.settings = settings
        # Made before the tracing starts, as the server has them before it
        # reads them: what is measured is what the reading holds.
        chunks = [body[start : start + 64 * 1024] for start in range(0, len(body), 64 * 1024)]
        incoming = iter(
            [{"type": "http.request", "body": chunk, "more_body": True} for chunk in chunks]
            + [{"type": "http.request", "body": b"", "more_body": False}]
        )

        async def receive() -> dict[str, object]:
            return next(incoming)

        scope = {"type": "http", "method": "POST", "headers": [(b"content-type", MULTIPART.encode())], "app": app}
        room = feedback_api._Room(BodyBudget(), settings, len(body))
        tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            before = tracemalloc.get_traced_memory()[0]
            item_part, screenshot_part = await feedback_api._create_parts(Request(scope, receive), room, size)
            held = tracemalloc.get_traced_memory()[1] - before
        finally:
            tracemalloc.stop()
        assert json.loads(item_part) == item()
        assert screenshot_part == screenshot
        assert room.taken == len(body)
        # A part reserves some room as it grows. A copy would add all of it.
        assert held < 1.5 * room.taken, f"{held} bytes held for {room.taken} counted"


# --- The write counters -------------------------------------------------------------


class TestWriteCounters:
    async def test_only_accepted_writes_count_per_author(self, environment, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(feedback_api, "_clock", lambda: now[0])
        app = environment.app_with(
            feedback_writes_per_address=2, feedback_writes_per_author=1, feedback_writes_window_s=600
        )
        token = page_token(app, environment.world.site_id)
        async with client_for(app) as other:
            for _ in range(5):
                assert (await create(other, token, item(comment=""))).status_code == 422
            # A refused attempt keeps no address in memory either.
            assert len(app.state.feedback_author_writes) == 0
            # Without a session the address is the author, with the budget of
            # a network that many people can share, not that of one person.
            for _ in range(2):
                assert (await create(other, token)).status_code == 201
            now[0] += 100
            refused = await create(other, token)
            assert_problem(refused, 429, "FEEDBACK_RATE_LIMITED")
            assert refused.headers["retry-after"] == "500"
            # A consented session is an author of its own, apart from the
            # address, with the budget of one person.
            session = consented_session(app, environment.world.site_id)
            as_session = page_token(app, environment.world.site_id, session_id=session.id)
            assert (await create(other, as_session)).status_code == 201
            assert_problem(await create(other, as_session), 429, "FEEDBACK_RATE_LIMITED")
            now[0] += 500
            assert (await create(other, token)).status_code == 201

    async def test_an_address_has_a_budget_on_each_site(self, environment, monkeypatch):
        """Keyed on the address and the site: people behind one network who
        give feedback on one site leave the budget on another alone."""
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        app = environment.app_with(feedback_writes_per_address=1)
        world = environment.world
        async with client_for(app) as other:
            assert (await create(other, page_token(app, world.site_id))).status_code == 201
            assert_problem(await create(other, page_token(app, world.site_id)), 429, "FEEDBACK_RATE_LIMITED")
            ander = item(page={"path": "/aurora/ander/", "title": None})
            assert (await create(other, page_token(app, world.other_site_id), ander)).status_code == 201
        assert set(app.state.feedback_author_writes._buckets) == {
            f"site:{world.site_id}:ip:127.0.0.1",
            f"site:{world.other_site_id}:ip:127.0.0.1",
        }

    @pytest.mark.parametrize("role", ["session", "gate"])
    async def test_a_person_has_a_budget_on_each_site(self, environment, monkeypatch, role):
        """A site's own scripts hold its page token, the person's session in
        it, and can spend all of the budget that token counts. Keyed on the
        person and the site, that leaves the person's budget on every other
        site alone, for a consented session and for one that let the person
        in alike."""
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        app = environment.app_with(feedback_writes_per_author=1)
        world = environment.world
        session = consented_session(app, world.site_id, world.other_site_id)

        def token(site_id: uuid.UUID) -> str:
            if role == "session":
                return page_token(app, site_id, session_id=session.id)
            return page_token(app, site_id, access_session_id=session.id)

        ander = item(page={"path": "/aurora/ander/", "title": None})
        async with client_for(app) as other:
            assert (await create(other, token(world.site_id))).status_code == 201
            assert_problem(await create(other, token(world.site_id)), 429, "FEEDBACK_RATE_LIMITED")
            assert (await create(other, token(world.other_site_id), ander)).status_code == 201
            assert_problem(await create(other, token(world.other_site_id), ander), 429, "FEEDBACK_RATE_LIMITED")
        assert set(app.state.feedback_author_writes._buckets) == {
            f"site:{world.site_id}:{role}:kijker",
            f"site:{world.other_site_id}:{role}:kijker",
        }

    async def test_the_site_counts_every_author_together(self, environment, monkeypatch):
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        app = environment.app_with(feedback_writes_per_site=2)
        world = environment.world
        first = consented_session(app, world.site_id, sub="een")
        second = consented_session(app, world.site_id, sub="twee")
        async with client_for(app) as other:
            assert (await create(other, page_token(app, world.site_id, session_id=first.id))).status_code == 201
            assert (await create(other, page_token(app, world.site_id))).status_code == 201
            refused = await create(other, page_token(app, world.site_id, session_id=second.id))
            assert_problem(refused, 429, "FEEDBACK_RATE_LIMITED")
            assert refused.headers["retry-after"] == "600"
            # Another site has a budget of its own.
            ander = item(page={"path": "/aurora/ander/", "title": None})
            assert (await create(other, page_token(app, world.other_site_id), ander)).status_code == 201

    async def test_replies_and_deletes_are_writes_too(self, environment, monkeypatch):
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        app = environment.app_with(feedback_writes_per_author=2)
        world = environment.world
        session = consented_session(app, world.site_id)
        token = page_token(app, world.site_id, session_id=session.id)
        first = await account_item(environment.factory, world.site_id)
        async with client_for(app) as other:
            assert (await reply(other, token, first)).status_code == 201
            assert (await other.delete(f"{ITEMS}/{first}", headers=auth(token))).status_code == 204
            assert_problem(await reply(other, token, first), 429, "FEEDBACK_RATE_LIMITED")
            assert_problem(await other.delete(f"{ITEMS}/{first}", headers=auth(token)), 429, "FEEDBACK_RATE_LIMITED")
            # Reading costs nothing.
            assert (await other.get(ITEMS, headers=auth(token))).status_code == 200

    async def test_a_create_whose_body_has_not_arrived_holds_no_write(self, environment, monkeypatch):
        """Charged once its body is in, not while it streams: a create that
        declares its length and sends nothing leaves the site's last write
        to another create, and is refused itself when its body arrives after
        that one."""
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        world = environment.world
        app = environment.app_with(feedback_writes_per_site=1)
        token = page_token(app, world.site_id)
        silent = HeldBody(head=b"", tail=ITEM_HEAD + ITEM_TAIL)
        async with client_for(app) as client, client_for(app, address="192.0.2.7") as other:
            held = await held_create(client, token, silent, declared=silent.length)
            assert writes_spent(app, world.site_id) == (0, 0)
            assert (await create(other, token)).status_code == 201
            silent.release.set()
            assert_problem(await held, 429, "FEEDBACK_RATE_LIMITED")
        assert writes_spent(app, world.site_id) == (0, 1)
        assert len(await stored_items(environment.factory)) == 1

    async def test_a_reply_whose_body_has_not_arrived_holds_no_write(self, environment, monkeypatch):
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        world = environment.world
        app = environment.app_with(feedback_writes_per_author=1)
        session = consented_session(app, world.site_id)
        token = page_token(app, world.site_id, session_id=session.id)
        own = await account_item(environment.factory, world.site_id)
        content = b'{"body": "Ik bedoel de tweede alinea."}'
        silent = HeldBody(head=b"", tail=content)
        headers = auth(token, **{"Content-Type": "application/json", "Content-Length": str(len(content))})
        async with client_for(app) as client:
            held = await held_post(client, f"{ITEMS}/{own}/replies", silent, {**headers})
            assert writes_spent(app, world.site_id, author="session:kijker") == (0, 0)
            assert (await reply(client, token, own)).status_code == 201
            silent.release.set()
            assert_problem(await held, 429, "FEEDBACK_RATE_LIMITED")
        assert writes_spent(app, world.site_id, author="session:kijker") == (1, 1)
        assert await replies_on(environment.factory, own) == 1

    @pytest.mark.parametrize("route", ["create", "reply"])
    async def test_a_spent_budget_is_refused_before_the_body_is_read(self, environment, monkeypatch, route):
        """The check before the body charges nothing and still reads no byte
        of a body that cannot be taken, nor gives it a place among the bodies
        in flight."""
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        world = environment.world
        app = environment.app_with(feedback_writes_per_author=1)
        session = consented_session(app, world.site_id)
        token = page_token(app, world.site_id, session_id=session.id)
        own = await account_item(environment.factory, world.site_id)

        def no_room_asked(*args: object, **kwargs: object) -> bool:
            raise AssertionError("a spent budget asked for room in the budget")

        if route == "create":
            path, content_type, body = ITEMS, MULTIPART, HeldBody()
        else:
            path, content_type = f"{ITEMS}/{own}/replies", "application/json"
            body = HeldBody(head=b'{"body": "x', tail=b'"}')
        headers = auth(token, **{"Content-Type": content_type})
        async with client_for(app) as client:
            assert (await reply(client, token, own)).status_code == 201
            monkeypatch.setattr(app.state.feedback_bodies, "admit", no_room_asked)
            response = await client.post(path, content=body.chunks(), headers=headers)
        assert_problem(response, 429, "FEEDBACK_RATE_LIMITED")
        assert body.pulled is False
        assert writes_spent(app, world.site_id, author="session:kijker") == (1, 1)

    def test_a_key_never_written_has_spent_nothing(self):
        counter = WriteCounter()
        assert counter.spent("ip:192.0.2.1", 10.0).count == 0

    def test_a_refund_takes_back_a_charge_of_its_own_window_only(self):
        counter = WriteCounter()
        first = counter.charge("ip:192.0.2.1", 10, 0.0)
        assert counter.charge("ip:192.0.2.1", 10, 1.0) == first
        counter.refund("ip:192.0.2.1", first)
        assert counter.spent("ip:192.0.2.1", 2.0).count == 1
        # A window that started since never owed the old one's charge.
        assert counter.charge("ip:192.0.2.1", 10, 20.0) == 20.0
        counter.refund("ip:192.0.2.1", first)
        assert counter.spent("ip:192.0.2.1", 20.0).count == 1
        # Nor does a key the counter no longer holds.
        counter.refund("ip:192.0.2.9", first)
        assert counter.spent("ip:192.0.2.9", 20.0).count == 0

    def test_a_refund_back_to_nothing_leaves_no_key(self):
        """A refused attempt keeps no address or sub in memory."""
        counter = WriteCounter()
        first = counter.charge("ip:192.0.2.1", 10, 0.0)
        second = counter.charge("ip:192.0.2.1", 10, 1.0)
        counter.refund("ip:192.0.2.1", first)
        assert counter.spent("ip:192.0.2.1", 2.0).count == 1
        counter.refund("ip:192.0.2.1", second)
        assert len(counter) == 0

    def test_a_key_goes_once_its_window_has_ended(self):
        """Whichever key a check or a charge is for, it sweeps every window
        that has ended: the address of a giver who never comes back goes
        too, and on the end of its window, not a moment before."""
        counter = WriteCounter()
        counter.charge("ip:192.0.2.1", 10, 0.0)
        counter.charge("ip:192.0.2.2", 10, 5.0)
        assert counter.spent("ip:192.0.2.9", 9.9).count == 0
        assert list(counter._buckets) == ["ip:192.0.2.1", "ip:192.0.2.2"]
        assert counter.spent("ip:192.0.2.9", 10.0).count == 0
        assert list(counter._buckets) == ["ip:192.0.2.2"]
        counter.charge("ip:192.0.2.9", 10, 15.0)
        assert list(counter._buckets) == ["ip:192.0.2.9"]

    def test_a_sweep_drops_the_ended_windows_and_keeps_the_running_ones(self):
        counter = WriteCounter()
        for start, key in enumerate(["ip:a", "ip:b", "ip:c"]):
            counter.charge(key, 10, float(start))
        # A window that starts again moves its key behind the others.
        counter.charge("ip:a", 10, 10.0)
        counter.sweep(11.5)
        assert list(counter._buckets) == ["ip:c", "ip:a"]
        counter.sweep(20.0)
        assert len(counter) == 0
        counter.sweep(30.0)
        assert len(counter) == 0

    async def test_the_sweep_job_drops_the_ended_windows_without_a_write(self, monkeypatch):
        """With no write at all, the last giver's key still goes: the job
        sweeps both counters every interval."""
        now = [0.0]
        monkeypatch.setattr(feedback_api, "_clock", lambda: now[0])
        counters = (WriteCounter(), WriteCounter())
        for counter in counters:
            counter.charge("ip:192.0.2.1", 10, 0.0)
        # The job sweeps the counters in order: once the last one is swept,
        # so is every one before it.
        swept, ended = asyncio.Event(), asyncio.Event()
        sweep = counters[-1].sweep

        def watched(now_: float) -> None:
            sweep(now_)
            swept.set()
            if now_ >= 10.0:
                ended.set()

        monkeypatch.setattr(counters[-1], "sweep", watched)
        async with write_counter_sweep(counters, 0.01) as task:
            await asyncio.wait_for(swept.wait(), 5)
            # Its window still runs.
            assert [len(counter) for counter in counters] == [1, 1]
            now[0] = 10.0
            await asyncio.wait_for(ended.wait(), 5)
            assert [len(counter) for counter in counters] == [0, 0]
        assert task.cancelled()

    async def test_a_burst_cannot_pass_the_check_together(self, committed, tmp_path):
        """Charged before the write with nothing awaited in between: of a
        burst from one author exactly the budget passes, where checking
        first and counting after the commit let every one of them through."""
        world = await seed(committed)
        app = make_app(make_settings(tmp_path, feedback_writes_per_address=3, **ONE_CLIENT_BURST), committed)
        token = page_token(app, world.site_id)
        async with client_for(app) as client:
            responses = await asyncio.gather(*(create(client, token) for _ in range(12)))
        assert sorted(response.status_code for response in responses) == [201] * 3 + [429] * 9
        assert len(await stored_items(committed)) == 3
        assert writes_spent(app, world.site_id) == (3, 3)

    async def test_a_burst_that_the_database_refuses_in_part_keeps_only_the_accepted(self, committed, tmp_path):
        """Whatever was charged for a create the open-item cap turned away is
        given back, however the burst interleaves."""
        world = await seed(committed)
        settings = make_settings(tmp_path, feedback_site_max_open=2, feedback_writes_per_address=6, **ONE_CLIENT_BURST)
        app = make_app(settings, committed)
        token = page_token(app, world.site_id)
        async with client_for(app) as client:
            responses = await asyncio.gather(*(create(client, token) for _ in range(12)))
        statuses = sorted(response.status_code for response in responses)
        assert statuses[:2] == [201, 201]
        assert set(statuses[2:]) <= {409, 429}
        assert writes_spent(app, world.site_id) == (2, 2)

    async def test_a_create_the_database_refuses_spends_nothing(self, environment):
        world = environment.world
        await add_item(environment.factory, world.site_id)
        app = environment.app_with(feedback_site_max_open=1)
        async with client_for(app) as client:
            assert_problem(await create(client, page_token(app, world.site_id)), 409, "FEEDBACK_SITE_FULL")
            assert_problem(
                await create(client, page_token(app, world.site_id), screenshot=b"geen beeld"),
                422,
                "FEEDBACK_SCREENSHOT_INVALID",
            )
        assert writes_spent(app, world.site_id) == (0, 0)

    async def test_a_reply_or_delete_on_no_own_item_spends_nothing(self, client, environment):
        world = environment.world
        session = consented_session(environment.app, world.site_id)
        token = page_token(environment.app, world.site_id, session_id=session.id)
        foreign = await account_item(environment.factory, world.site_id, "iemand-anders")
        assert_problem(await reply(client, token, foreign), 404, "FEEDBACK_UNAVAILABLE")
        assert_problem(await client.delete(f"{ITEMS}/{foreign}", headers=auth(token)), 404, "FEEDBACK_UNAVAILABLE")
        assert writes_spent(environment.app, world.site_id, author="session:kijker") == (0, 0)


# --- The giver's own items --------------------------------------------------------


async def reply(client: httpx.AsyncClient, token: str, item_id: object, body: object = None) -> httpx.Response:
    return await client.post(
        f"{ITEMS}/{item_id}/replies",
        json={"body": "Dank, ik kijk ernaar."} if body is None else body,
        headers=auth(token),
    )


# The most bytes a character takes in UTF-8, and in the JSON of an answer.
FOUR_BYTES = "\U0001f600"


async def context_item(factory: async_sessionmaker[AsyncSession], world: World, minutes: int) -> uuid.UUID:
    """A text item of kijker's, `minutes` old, whose stored anchor holds the
    most the entry leaves out (the context of the quote at its limit, in
    four-byte characters, and the position) and which holds little else."""
    anchor = {
        "selectors": [
            {"type": "TextQuoteSelector", "exact": "q", "prefix": FOUR_BYTES * 32, "suffix": FOUR_BYTES * 32},
            {"type": "TextPositionSelector", "start": 0, "end": 1},
            {"type": "CssSelector", "value": "p"},
        ],
        "point": None,
    }
    return await account_item(
        factory,
        world.site_id,
        kind=FeedbackKind.TEXT,
        anchor=anchor,
        quote="q",
        comment="x",
        page_path="",
        created_at=func.now() - text(f"interval '{minutes} minutes'"),
    )


async def maximal_item(factory: async_sessionmaker[AsyncSession], world: World, minutes: int) -> uuid.UUID:
    """An item of kijker's, `minutes` old, as large as Toon lets one grow,
    in four-byte characters: every field at the limit of the database or,
    in the anchor, of the widget API, and ten replies of the team at theirs."""
    quote = FOUR_BYTES * 1000
    anchor = {
        "selectors": [
            {"type": "TextQuoteSelector", "exact": quote, "prefix": FOUR_BYTES * 32, "suffix": FOUR_BYTES * 32},
            {"type": "TextPositionSelector", "start": 0, "end": 1000},
            {"type": "CssSelector", "value": FOUR_BYTES * 1000},
        ],
        "point": None,
    }
    item_id = await account_item(
        factory,
        world.site_id,
        kind=FeedbackKind.TEXT,
        anchor=anchor,
        quote=quote,
        comment=FOUR_BYTES * 5000,
        page_path="a" * 2048,
        page_title=FOUR_BYTES * 300,
        target=VersionTarget.PREVIEW,
        preview_ref="pr-1",
        created_at=func.now() - text(f"interval '{minutes} minutes'"),
    )
    async with factory() as db:
        db.add_all(
            FeedbackReply(
                item_id=item_id,
                author_kind=FeedbackReplyAuthor.MEMBER,
                member_id=world.member_id,
                author_name=FOUR_BYTES * 200,
                body=FOUR_BYTES * 5000,
            )
            for _ in range(10)
        )
        await db.commit()
    return item_id


class TestMyItems:
    async def test_a_token_without_a_consented_session_needs_an_account(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        response = await client.get(ITEMS, headers=auth(token, **{"Accept-Language": "nl"}))
        assert_problem(response, 403, "FEEDBACK_ACCOUNT_REQUIRED", locale="nl")

    async def test_only_the_sessions_own_account_items_on_this_site_newest_first(self, client, environment):
        app, world = environment.app, environment.world
        factory = environment.factory
        older = await add_item(
            factory,
            world.site_id,
            author_kind=FeedbackAuthor.ACCOUNT,
            author_sub="kijker",
            kind=FeedbackKind.POINT,
            anchor=POINT_ANCHOR,
            created_at=func.now() - text("interval '1 hour'"),
        )
        newer = await add_item(
            factory,
            world.site_id,
            author_kind=FeedbackAuthor.ACCOUNT,
            author_sub="kijker",
            kind=FeedbackKind.TEXT,
            anchor={"selectors": [TEXT_ANCHOR["selectors"][2]], "point": None},
            quote="2,4 miljoen",
            target=VersionTarget.PREVIEW,
            preview_ref="pr-1",
            page_path="",
            page_title="Voorbeeld",
            status=FeedbackStatus.RESOLVED,
            resolved_at=func.now(),
        )
        # Not the visitor's: another sub, a name, no name, another site.
        await add_item(factory, world.site_id, author_kind=FeedbackAuthor.ACCOUNT, author_sub="iemand-anders")
        await add_item(factory, world.site_id, author_kind=FeedbackAuthor.NAME, author_name="kijker")
        await add_item(factory, world.site_id)
        await add_item(factory, world.other_site_id, author_kind=FeedbackAuthor.ACCOUNT, author_sub="kijker")
        async with factory() as db:
            db.add_all(
                [
                    FeedbackReply(
                        item_id=older,
                        author_kind=FeedbackReplyAuthor.MEMBER,
                        member_id=world.member_id,
                        author_name="Lid Actief",
                        body="We passen het aan.",
                        created_at=func.now() - text("interval '2 minutes'"),
                    ),
                    FeedbackReply(item_id=older, author_kind=FeedbackReplyAuthor.GIVER, body="Dank je."),
                    FeedbackReply(
                        item_id=newer,
                        author_kind=FeedbackReplyAuthor.MEMBER,
                        body="Opgelost.",
                    ),
                ]
            )
            await db.commit()
        session = consented_session(app, world.site_id)
        response = await client.get(ITEMS, headers=auth(page_token(app, world.site_id, session_id=session.id)))
        assert response.status_code == 200
        for name, value in FEEDBACK_API_HEADERS.items():
            assert response.headers[name] == value, name
        items = response.json()["items"]
        assert [entry["id"] for entry in items] == [str(newer), str(older)]
        first, second = items
        assert first["kind"] == "text"
        assert first["status"] == "resolved"
        assert first["quote"] == "2,4 miljoen"
        assert first["anchor"] == {"k": "text", "q": "2,4 miljoen"}
        assert first["page"] == {"path": "", "title": "Voorbeeld", "url": PREVIEW_ROOT}
        assert (first["target"], first["previewRef"]) == ("preview", "pr-1")
        assert first["createdAt"].endswith("Z")
        assert [(r["author"], r["name"], r["body"]) for r in first["replies"]] == [("team", None, "Opgelost.")]
        assert first["replyCount"] == 1
        assert second["anchor"] == {
            "k": "point", "s": "main > p:nth-of-type(3)", "x": 0.25, "y": 0.5, "px": 340, "py": 1200.5
        }
        assert second["page"] == {"path": "docs/", "title": None, "url": f"{ROOT}docs/"}
        assert (second["target"], second["previewRef"], second["quote"]) == ("live", None, None)
        assert [(r["author"], r["name"], r["body"]) for r in second["replies"]] == [
            ("team", "Lid Actief", "We passen het aan."),
            ("you", None, "Dank je."),
        ]
        assert second["replyCount"] == 2
        assert set(second["replies"][0]) == {"id", "author", "name", "body", "createdAt"}
        assert set(second) == {
            "id",
            "kind",
            "status",
            "comment",
            "quote",
            "anchor",
            "page",
            "target",
            "previewRef",
            "createdAt",
            "replies",
            "replyCount",
        }

    async def test_a_page_item_has_no_anchor_and_a_text_item_keeps_its_ancestor(self, client, environment):
        app, world = environment.app, environment.world
        await account_item(environment.factory, world.site_id)
        await add_item(
            environment.factory,
            world.site_id,
            author_kind=FeedbackAuthor.ACCOUNT,
            author_sub="kijker",
            kind=FeedbackKind.TEXT,
            anchor={"selectors": [TEXT_ANCHOR["selectors"][2], TEXT_ANCHOR["selectors"][1]], "point": None},
            quote="2,4 miljoen",
            created_at=func.now() + text("interval '1 minute'"),
        )
        session = consented_session(app, world.site_id)
        response = await client.get(ITEMS, headers=auth(page_token(app, world.site_id, session_id=session.id)))
        text_item, page_item = response.json()["items"]
        assert text_item["anchor"] == {"k": "text", "s": "main > p:nth-of-type(3)", "q": "2,4 miljoen"}
        assert page_item["anchor"] is None

    @pytest.mark.parametrize(("stored", "more"), [(51, True), (50, False)])
    async def test_at_most_the_fifty_newest(self, client, environment, stored, more):
        """`more` says whether older items were left out."""
        app, world = environment.app, environment.world
        async with environment.factory() as db:
            db.add_all(
                FeedbackItem(
                    site_id=world.site_id,
                    target=VersionTarget.LIVE,
                    page_path="",
                    kind=FeedbackKind.PAGE,
                    comment=f"{minutes} minuten oud",
                    author_kind=FeedbackAuthor.ACCOUNT,
                    author_sub="kijker",
                    created_at=func.now() - text(f"interval '{minutes} minutes'"),
                )
                for minutes in range(stored)
            )
            await db.commit()
        session = consented_session(app, world.site_id)
        response = await client.get(ITEMS, headers=auth(page_token(app, world.site_id, session_id=session.id)))
        assert [entry["comment"] for entry in response.json()["items"]] == [
            f"{minutes} minuten oud" for minutes in range(50)
        ]
        assert response.json()["more"] is more

    async def test_the_answer_stays_within_256_kib(self, client, environment):
        """Bounded by count alone, fifty items of four-byte characters with
        their replies made an answer of megabytes. Now the newest that fit
        come in, here only one, since a maximal item is close to the bound
        on its own, and `more` says older ones were left out."""
        app, world = environment.app, environment.world
        newest, *_ = [await maximal_item(environment.factory, world, minutes) for minutes in range(3)]
        session = consented_session(app, world.site_id)
        response = await client.get(ITEMS, headers=auth(page_token(app, world.site_id, session_id=session.id)))
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        assert 200 * 1024 < len(response.content) <= 256 * 1024
        body = response.json()
        assert body["more"] is True
        (entry,) = body["items"]
        assert entry["id"] == str(newest)
        assert entry["comment"] == FOUR_BYTES * 5000
        assert [reply["body"] for reply in entry["replies"]] == [FOUR_BYTES * 5000] * 10
        assert entry["replyCount"] == 10

    async def test_the_newest_item_comes_in_even_over_the_bound_alone(self, client, environment, monkeypatch):
        """The list is never empty while the visitor has an item."""
        monkeypatch.setattr(feedback_api, "MAX_MINE_BYTES", 10)
        app, world = environment.app, environment.world
        newest = await account_item(environment.factory, world.site_id)
        await account_item(environment.factory, world.site_id, created_at=func.now() - text("interval '1 minute'"))
        session = consented_session(app, world.site_id)
        response = await client.get(ITEMS, headers=auth(page_token(app, world.site_id, session_id=session.id)))
        body = response.json()
        assert [entry["id"] for entry in body["items"]] == [str(newest)]
        assert body["more"] is True

    async def test_the_bound_counts_every_byte_of_the_answer(self, client, environment, monkeypatch):
        """Commas, brackets and `more` included: an answer of exactly the
        bound keeps every item, one byte less leaves the oldest out."""
        app, world = environment.app, environment.world
        newest = await account_item(environment.factory, world.site_id)
        await account_item(environment.factory, world.site_id, created_at=func.now() - text("interval '1 minute'"))
        session = consented_session(app, world.site_id)
        headers = auth(page_token(app, world.site_id, session_id=session.id))
        whole = await client.get(ITEMS, headers=headers)
        assert len(whole.json()["items"]) == 2
        monkeypatch.setattr(feedback_api, "MAX_MINE_BYTES", len(whole.content))
        assert (await client.get(ITEMS, headers=headers)).content == whole.content
        monkeypatch.setattr(feedback_api, "MAX_MINE_BYTES", len(whole.content) - 1)
        cut = await client.get(ITEMS, headers=headers)
        assert len(cut.content) < len(whole.content)
        assert [entry["id"] for entry in cut.json()["items"]] == [str(newest)]
        assert cut.json()["more"] is True

    async def test_only_what_can_fit_is_loaded(self, client, environment):
        """What fits is worked out from the sizes of the texts before any text
        is loaded. Of three maximal items only the newest fits, so only it and
        its ten replies are loaded, where loading all three and cutting while
        encoding put some 13 MiB on the event loop to send 240 KB."""
        app, world = environment.app, environment.world
        newest, *_ = [await maximal_item(environment.factory, world, minutes) for minutes in range(3)]
        headers = auth(page_token(app, world.site_id, session_id=consented_session(app, world.site_id).id))
        loaded: list[FeedbackItem | FeedbackReply] = []

        def record(row: FeedbackItem | FeedbackReply, context: object) -> None:
            loaded.append(row)

        for model in (FeedbackItem, FeedbackReply):
            event.listen(model, "load", record)
        try:
            response = await client.get(ITEMS, headers=headers)
        finally:
            for model in (FeedbackItem, FeedbackReply):
                event.remove(model, "load", record)
        body = response.json()
        assert ([entry["id"] for entry in body["items"]], body["more"]) == ([str(newest)], True)
        assert [row.id for row in loaded if isinstance(row, FeedbackItem)] == [newest]
        assert [row.item_id for row in loaded if isinstance(row, FeedbackReply)] == [newest] * 10

    @pytest.mark.parametrize("made", [maximal_item, context_item], ids=["maximal", "context-and-little-else"])
    async def test_the_sizes_leave_out_only_what_would_not_fit(self, client, environment, monkeypatch, made):
        """Each size counts at most what the item's entry holds, its reply
        texts, its quote twice and its selector values included, and nothing
        of the stored anchor the entry leaves out: an answer of exactly the
        bound keeps both items, and one byte less leaves the older out."""
        app, world = environment.app, environment.world
        newest = await made(environment.factory, world, 0)
        await made(environment.factory, world, 1)
        headers = auth(page_token(app, world.site_id, session_id=consented_session(app, world.site_id).id))
        monkeypatch.setattr(feedback_api, "MAX_MINE_BYTES", 1024 * 1024)
        whole = await client.get(ITEMS, headers=headers)
        assert len(whole.json()["items"]) == 2
        monkeypatch.setattr(feedback_api, "MAX_MINE_BYTES", len(whole.content))
        assert (await client.get(ITEMS, headers=headers)).content == whole.content
        monkeypatch.setattr(feedback_api, "MAX_MINE_BYTES", len(whole.content) - 1)
        cut = (await client.get(ITEMS, headers=headers)).json()
        assert ([entry["id"] for entry in cut["items"]], cut["more"]) == ([str(newest)], True)

    @pytest.mark.parametrize("gone", ["deleted", "expired"])
    async def test_an_item_from_a_preview_that_no_longer_opens_has_no_address(self, client, environment, gone):
        """The gate answers a preview that is gone or past its expiry with the
        neutral 404, so the page of an item given there comes without its
        address, and the widget shows it without a link. A preview that
        still opens keeps it, one that expires later too, and so does every
        live item. A preview of that name on another site opens nothing."""
        app, world, factory = environment.app, environment.world, environment.factory
        now = datetime.now(UTC)
        async with factory() as db:
            later = Preview(site_id=world.site_id, ref="pr-2", version_id=world.preview_version_id)
            later.expires_at = now + timedelta(days=1)
            if gone == "expired":
                past = Preview(site_id=world.site_id, ref="pr-3", version_id=world.preview_version_id)
                past.expires_at = now - timedelta(minutes=1)
            else:
                past = Preview(site_id=world.other_site_id, ref="pr-3", version_id=world.other_live_version_id)
            db.add_all([later, past])
            await db.commit()
        for minutes, ref in enumerate([None, "pr-1", "pr-2", "pr-3"]):
            await account_item(
                factory,
                world.site_id,
                target=VersionTarget.LIVE if ref is None else VersionTarget.PREVIEW,
                preview_ref=ref,
                created_at=func.now() - text(f"interval '{minutes} minutes'"),
            )
        headers = auth(page_token(app, world.site_id, session_id=consented_session(app, world.site_id).id))
        response = await client.get(ITEMS, headers=headers)
        assert {entry["previewRef"]: entry["page"]["url"] for entry in response.json()["items"]} == {
            None: f"{ROOT}docs/",
            "pr-1": f"{PREVIEW_ROOT}docs/",
            "pr-2": f"{ROOT}_preview/pr-2/docs/",
            "pr-3": None,
        }

    async def test_each_item_carries_its_ten_newest_replies_oldest_first_and_how_many(self, client, environment):
        """Each item has a window of its own: the replies of a long thread do
        not crowd out those of another item."""
        app, world = environment.app, environment.world
        factory = environment.factory
        long_thread = await account_item(factory, world.site_id)
        short_thread = await account_item(factory, world.site_id, created_at=func.now() - text("interval '1 hour'"))
        silent = await account_item(factory, world.site_id, created_at=func.now() - text("interval '2 hours'"))
        async with environment.factory() as db:
            db.add_all(
                FeedbackReply(
                    item_id=long_thread,
                    author_kind=FeedbackReplyAuthor.GIVER if minutes % 2 else FeedbackReplyAuthor.MEMBER,
                    body=f"{minutes} minuten oud",
                    created_at=func.now() - text(f"interval '{minutes} minutes'"),
                )
                for minutes in range(12)
            )
            db.add_all(
                FeedbackReply(
                    item_id=short_thread,
                    author_kind=FeedbackReplyAuthor.GIVER,
                    body=f"kort {minutes}",
                    created_at=func.now() - text(f"interval '{minutes} minutes'"),
                )
                for minutes in range(3)
            )
            await db.commit()
        session = consented_session(app, world.site_id)
        response = await client.get(ITEMS, headers=auth(page_token(app, world.site_id, session_id=session.id)))
        long_entry, short_entry, silent_entry = response.json()["items"]
        assert [long_entry["id"], short_entry["id"], silent_entry["id"]] == [
            str(long_thread),
            str(short_thread),
            str(silent),
        ]
        newest_ten = [f"{minutes} minuten oud" for minutes in range(9, -1, -1)]
        assert [reply["body"] for reply in long_entry["replies"]] == newest_ten
        assert long_entry["replyCount"] == 12
        assert [reply["body"] for reply in short_entry["replies"]] == ["kort 2", "kort 1", "kort 0"]
        assert short_entry["replyCount"] == 3
        assert (silent_entry["replies"], silent_entry["replyCount"]) == ([], 0)

    async def test_nothing_yet_is_an_empty_list(self, client, environment):
        app, world = environment.app, environment.world
        session = consented_session(app, world.site_id)
        response = await client.get(ITEMS, headers=auth(page_token(app, world.site_id, session_id=session.id)))
        assert response.json() == {"items": [], "more": False}


class TestReplies:
    async def _setup(self, environment: Environment) -> tuple[str, uuid.UUID]:
        world = environment.world
        session = consented_session(environment.app, world.site_id)
        own = await account_item(environment.factory, world.site_id)
        return page_token(environment.app, world.site_id, session_id=session.id), own

    async def test_a_token_without_a_session_needs_an_account(self, client, environment):
        _, own = await self._setup(environment)
        token = page_token(environment.app, environment.world.site_id)
        assert_problem(await reply(client, token, own), 403, "FEEDBACK_ACCOUNT_REQUIRED")

    async def test_a_reply_on_the_givers_own_item(self, client, environment):
        token, own = await self._setup(environment)
        response = await reply(client, token, own, {"body": "  Ik bedoel de tweede alinea.\n  "})
        assert response.status_code == 201
        for name, value in FEEDBACK_API_HEADERS.items():
            assert response.headers[name] == value, name
        body = response.json()
        assert body["author"] == "you"
        assert body["name"] is None
        assert body["body"] == "Ik bedoel de tweede alinea."
        assert body["createdAt"].endswith("Z")
        async with environment.factory() as db:
            (stored,) = (await db.scalars(select(FeedbackReply))).all()
        assert str(stored.id) == body["id"]
        assert stored.item_id == own
        assert stored.author_kind is FeedbackReplyAuthor.GIVER
        assert (stored.member_id, stored.author_name) == (None, None)

    async def test_a_reply_takes_a_code_point_the_servers_tables_do_not_know(self, client, environment):
        assert unicodedata.category(UNASSIGNED) == "Cn"
        token, own = await self._setup(environment)
        response = await reply(client, token, own, {"body": f"Dank {UNASSIGNED}"})
        assert response.status_code == 201
        assert response.json()["body"] == f"Dank {UNASSIGNED}"
        async with environment.factory() as db:
            assert (await db.scalars(select(FeedbackReply.body))).all() == [f"Dank {UNASSIGNED}"]

    async def test_a_reply_keeps_the_two_joiners_writing_needs(self, client, environment):
        token, own = await self._setup(environment)
        text = f"Dank je {JOINED_EMOJI} {PERSIAN}"
        response = await reply(client, token, own, {"body": text})
        assert response.status_code == 201, response.text
        assert response.json()["body"] == text
        async with environment.factory() as db:
            assert (await db.scalars(select(FeedbackReply.body))).all() == [text]

    @pytest.mark.parametrize("char", list(REFUSED_FORMAT.values()), ids=list(REFUSED_FORMAT))
    async def test_a_reply_with_any_other_format_character_is_refused(self, client, environment, char):
        token, own = await self._setup(environment)
        assert_invalid(await reply(client, token, own, {"body": f"Dank{char} je"}), "body")
        assert await replies_on(environment.factory, own) == 0

    async def test_an_item_that_is_not_the_givers_own_is_unavailable(self, client, environment):
        token, own = await self._setup(environment)
        world = environment.world
        reference = await reply(client, "nep", own)
        for other in (
            "geen-uuid",
            uuid.uuid4(),
            await account_item(environment.factory, world.site_id, "iemand-anders"),
            await add_item(environment.factory, world.site_id, author_kind=FeedbackAuthor.NAME, author_name="kijker"),
            await account_item(environment.factory, world.other_site_id),
        ):
            response = await reply(client, token, other)
            assert response.content == reference.content, other
            assert header_list(response) == header_list(reference)

    @pytest.mark.parametrize(
        ("content_type", "body"),
        [
            ("text/plain", b'{"body": "x"}'),
            ("application/json", b"{"),
            ("application/json", b'{"body": NaN}'),
            ("application/json", b"[]"),
            ("application/json", b'{"tekst": "x"}'),
            ("application/json", b'{"body": "x", "author": "team"}'),
            ("application/json", b'{"body": "   "}'),
            ("application/json", json.dumps({"body": "a" * 5001}).encode()),
            ("application/json", json.dumps({"body": "a‮b"}).encode()),
            ("application/json", json.dumps({"body": "a\x07b"}).encode()),
            ("application/json", b'{"body": "\xff"}'),
            ("application/json", json.dumps({"body": "a" * 70_000}).encode()),
        ],
        ids=[
            "not-json",
            "broken-json",
            "nan",
            "not-an-object",
            "unknown-key",
            "extra-key",
            "blank",
            "too-long",
            "format-character",
            "control-character",
            "not-utf8",
            "body-too-large",
        ],
    )
    async def test_a_reply_that_does_not_hold_is_refused(self, client, environment, content_type, body):
        token, own = await self._setup(environment)
        response = await client.post(
            f"{ITEMS}/{own}/replies", content=body, headers=auth(token, **{"Content-Type": content_type})
        )
        assert_invalid(response, "body")


class TestReplyCaps:
    """At most FEEDBACK_MAX_GIVER_REPLIES from the giver and
    FEEDBACK_MAX_REPLIES in all on one item, so neither a thread nor Mijn
    feedback grows without end."""

    async def _setup(self, environment: Environment) -> tuple[str, uuid.UUID]:
        world = environment.world
        session = consented_session(environment.app, world.site_id)
        own = await account_item(environment.factory, world.site_id)
        return page_token(environment.app, world.site_id, session_id=session.id), own

    async def test_the_givers_own_replies_stop_at_their_cap(self, client, environment):
        token, own = await self._setup(environment)
        await add_replies(environment.factory, own, giver=FEEDBACK_MAX_GIVER_REPLIES - 1, member=5)
        assert (await reply(client, token, own)).status_code == 201
        full = await client.post(
            f"{ITEMS}/{own}/replies", json={"body": "Nog één."}, headers=auth(token, **{"Accept-Language": "nl"})
        )
        assert_problem(full, 409, "FEEDBACK_THREAD_FULL", locale="nl")
        assert await replies_on(environment.factory, own) == FEEDBACK_MAX_GIVER_REPLIES + 5
        # The refused reply spent nothing.
        assert writes_spent(environment.app, environment.world.site_id, author="session:kijker") == (1, 1)
        # The cap is per item: another item of theirs still takes replies.
        other = await account_item(environment.factory, environment.world.site_id)
        assert (await reply(client, token, other)).status_code == 201

    async def test_every_reply_counts_toward_the_cap_in_all(self, client, environment):
        token, own = await self._setup(environment)
        await add_replies(environment.factory, own, member=FEEDBACK_MAX_REPLIES - 1)
        assert (await reply(client, token, own)).status_code == 201
        assert_problem(await reply(client, token, own), 409, "FEEDBACK_THREAD_FULL")
        assert await replies_on(environment.factory, own) == FEEDBACK_MAX_REPLIES


class TestConcurrentReplies:
    """Under the item's lock the count and the insert of one reply cannot
    interleave with another's: however many arrive at once, the caps hold
    exactly, and every refused reply gets its charge back."""

    async def _burst(self, committed, tmp_path: Path, **prefilled: int) -> tuple[list[int], FastAPI, World, uuid.UUID]:
        world = await seed(committed)
        app = make_app(make_settings(tmp_path, **ONE_CLIENT_BURST), committed)
        own = await account_item(committed, world.site_id)
        await add_replies(committed, own, **prefilled)
        token = page_token(app, world.site_id, session_id=consented_session(app, world.site_id).id)
        async with client_for(app) as client:
            responses = await asyncio.gather(*(reply(client, token, own) for _ in range(12)))
        return sorted(response.status_code for response in responses), app, world, own

    async def test_the_givers_cap(self, committed, tmp_path):
        statuses, app, world, own = await self._burst(committed, tmp_path, giver=FEEDBACK_MAX_GIVER_REPLIES - 2)
        assert statuses == [201] * 2 + [409] * 10
        assert await replies_on(committed, own) == FEEDBACK_MAX_GIVER_REPLIES
        assert writes_spent(app, world.site_id, author="session:kijker") == (2, 2)

    async def test_the_cap_in_all(self, committed, tmp_path):
        statuses, app, world, own = await self._burst(committed, tmp_path, member=FEEDBACK_MAX_REPLIES - 5)
        assert statuses == [201] * 5 + [409] * 7
        assert await replies_on(committed, own) == FEEDBACK_MAX_REPLIES
        assert writes_spent(app, world.site_id, author="session:kijker") == (5, 5)


class TestDeletes:
    async def test_a_token_without_a_session_needs_an_account(self, client, environment):
        own = await account_item(environment.factory, environment.world.site_id)
        token = page_token(environment.app, environment.world.site_id)
        assert_problem(await client.delete(f"{ITEMS}/{own}", headers=auth(token)), 403, "FEEDBACK_ACCOUNT_REQUIRED")

    async def test_the_giver_deletes_their_own_item_with_everything_on_it(self, client, environment):
        world = environment.world
        own = await account_item(environment.factory, world.site_id)
        kept = await account_item(environment.factory, world.site_id, "iemand-anders")
        async with environment.factory() as db:
            db.add(FeedbackReply(item_id=own, author_kind=FeedbackReplyAuthor.GIVER, body="x"))
            db.add(
                FeedbackScreenshot(
                    item_id=own, media_type="image/png", data=PNG_FILE, size_bytes=len(PNG_FILE), width=5, height=3
                )
            )
            await db.commit()
        session = consented_session(environment.app, world.site_id)
        token = page_token(environment.app, world.site_id, session_id=session.id)
        response = await client.delete(f"{ITEMS}/{own}", headers=auth(token))
        assert response.status_code == 204
        assert response.content == b""
        for name, value in FEEDBACK_API_HEADERS.items():
            assert response.headers[name] == value, name
        assert [stored.id for stored in await stored_items(environment.factory)] == [kept]
        async with environment.factory() as db:
            assert (await db.scalars(select(FeedbackReply))).all() == []
            assert (await db.scalars(select(FeedbackScreenshot))).all() == []
        again = await client.delete(f"{ITEMS}/{own}", headers=auth(token))
        assert_problem(again, 404, "FEEDBACK_UNAVAILABLE")

    async def test_an_item_that_is_not_the_givers_own_stays(self, client, environment):
        world = environment.world
        session = consented_session(environment.app, world.site_id)
        token = page_token(environment.app, world.site_id, session_id=session.id)
        others = [
            await account_item(environment.factory, world.site_id, "iemand-anders"),
            await add_item(environment.factory, world.site_id),
            await account_item(environment.factory, world.other_site_id),
        ]
        for other in [*others, "geen-uuid", uuid.uuid4()]:
            assert_problem(await client.delete(f"{ITEMS}/{other}", headers=auth(token)), 404, "FEEDBACK_UNAVAILABLE")
        assert len(await stored_items(environment.factory)) == 3


class TestNoAuditTrail:
    async def test_giving_answering_and_deleting_write_no_audit_row(self, client, environment):
        """Like looking at public content: what a giver does through the
        widget leaves no row in the audit log, accepted or refused."""
        recorder = install_audit_recorder(environment.app)
        world = environment.world
        session = consented_session(environment.app, world.site_id)
        token = page_token(environment.app, world.site_id, session_id=session.id)
        created = await create(client, token, item(author={"kind": "account"}))
        assert created.status_code == 201
        own = created.json()["id"]
        assert (await client.get(ITEMS, headers=auth(token))).status_code == 200
        assert (await reply(client, token, own)).status_code == 201
        assert (await client.delete(f"{ITEMS}/{own}", headers=auth(token))).status_code == 204
        assert (await create(client, "nep")).status_code == 404
        assert recorder.records == []


# --- The bundle, the catch-all and the admin host -----------------------------


class TestTheBundle:
    BUNDLE = WidgetBundle(content=b"(()=>{})();\n", hash="0123456789abcdef")

    async def test_the_current_bundle_under_its_hash(self, client, environment):
        environment.app.state.feedback_widget = self.BUNDLE
        response = await client.get("/-/feedback/widget.0123456789abcdef.js")
        assert response.status_code == 200
        assert response.content == self.BUNDLE.content
        assert response.headers["content-type"] == "text/javascript; charset=utf-8"
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert response.headers["x-content-type-options"] == "nosniff"

    @pytest.mark.parametrize(
        "path",
        [
            "/-/feedback/widget.fedcba9876543210.js",
            "/-/feedback/widget.js",
            "/-/feedback/widget.0123456789abcdef.js.map",
            "/-/feedback/widget.0123456789ABCDEF.js",
            "/-/feedback/widget.0123456789abcdef.mjs",
        ],
    )
    async def test_any_other_name_is_the_neutral_404(self, client, environment, path):
        environment.app.state.feedback_widget = self.BUNDLE
        assert is_neutral_404(await client.get(path))

    async def test_without_a_bundle_there_is_none(self, client):
        assert is_neutral_404(await client.get("/-/feedback/widget.0123456789abcdef.js"))


KNOWN_PATHS = [
    "/-/feedback/widget.0123456789abcdef.js",
    "/-/feedback/hide",
    "/-/feedback/show",
    "/-/feedback/account",
    "/-/feedback/account/stop",
    ITEMS,
    f"{ITEMS}/0b9f4c3e-1d2a-4e5f-8a9b-0c1d2e3f4a5b",
    f"{ITEMS}/0b9f4c3e-1d2a-4e5f-8a9b-0c1d2e3f4a5b/replies",
]
UNKNOWN_PATHS = [
    "/-/feedback/",
    "/-/feedback/onbekend",
    "/-/feedback/v1/",
    "/-/feedback/v1/onbekend",
    f"{ITEMS}/",
    f"{ITEMS}/x/replies/y",
    "/-/feedback/v2/items",
    "/-/feedback/account/stop/x",
]
# What each known path answers itself; everything else is the catch-all.
ROUTED = {
    ("GET", "/-/feedback/widget.0123456789abcdef.js"),
    ("POST", "/-/feedback/hide"),
    ("GET", "/-/feedback/show"),
    ("GET", "/-/feedback/account"),
    ("POST", "/-/feedback/account"),
    ("GET", "/-/feedback/account/stop"),
    ("OPTIONS", ITEMS),
    ("GET", ITEMS),
    ("POST", ITEMS),
    ("OPTIONS", f"{ITEMS}/0b9f4c3e-1d2a-4e5f-8a9b-0c1d2e3f4a5b"),
    ("DELETE", f"{ITEMS}/0b9f4c3e-1d2a-4e5f-8a9b-0c1d2e3f4a5b"),
    ("OPTIONS", f"{ITEMS}/0b9f4c3e-1d2a-4e5f-8a9b-0c1d2e3f4a5b/replies"),
    ("POST", f"{ITEMS}/0b9f4c3e-1d2a-4e5f-8a9b-0c1d2e3f4a5b/replies"),
}
# No route names the last four. uvicorn's parser refuses a token it does not
# know, such as FOO, with a 400 before Toon sees it. This transport does not.
METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "PROPFIND", "QUERY", "FOO"]


def _neutral_app(tmp_path: Path) -> FastAPI:
    """The routes without a database: nothing these requests reach may open
    one, and the session factory here says so if one does."""
    app = make_app(make_settings(tmp_path), None)

    def no_database() -> None:
        raise AssertionError("a catch-all answer touched the database")

    app.state.session_factory = no_database
    return app


class TestEverythingElseIsTheNeutral404:
    @pytest.mark.parametrize("method", METHODS)
    @pytest.mark.parametrize("path", KNOWN_PATHS + UNKNOWN_PATHS)
    async def test_every_method_on_every_path_no_route_takes(self, tmp_path, method, path):
        """Never FastAPI's 405 with an Allow header, never its 422."""
        if (method, path) in ROUTED:
            pytest.skip("answered by its own route")
        async with client_for(_neutral_app(tmp_path)) as client:
            response = await client.request(method, path)
        assert response.status_code == 404
        assert "allow" not in response.headers
        if method != "HEAD":
            assert response.content == NEUTRAL_404_BODY


class TestTheAdminHost:
    @pytest.mark.parametrize("method", ["TRACE", "PROPFIND", "QUERY", "FOO"])
    async def test_a_method_no_route_names_is_the_neutral_404_there_too(self, tmp_path, method):
        async with client_for(_neutral_app(tmp_path), ADMIN_URL) as client:
            response = await client.request(method, ITEMS)
        assert is_neutral_404(response)
        assert "allow" not in response.headers

    @pytest.mark.parametrize(("method", "path"), sorted(ROUTED))
    async def test_every_feedback_route_is_the_neutral_404_there(self, tmp_path, method, path):
        app = _neutral_app(tmp_path)
        app.state.feedback_widget = TestTheBundle.BUNDLE
        async with client_for(app, ADMIN_URL) as client:
            response = await client.request(
                method,
                path,
                headers={"Sec-Fetch-Dest": "document", "Authorization": "Toon-Feedback x"},
                params={"returnTo": ROOT} if method == "GET" else None,
            )
        assert is_neutral_404(response)

"""Tests for the feedback widget API on the content host
(serving/feedback_api.py, its routes in serving/feedback_routes.py and the
token checks in serving/feedback_access.py), against the real PostgreSQL test
container.

Refusals first: every ground for FEEDBACK_UNAVAILABLE answers the same bytes
per language, every other code answers its exact status and body, and the
widget header set rides on every answer of `/-/feedback/v1/`. Then what an
accepted item stores, the quotas under concurrency, the write counters, the
giver's own items, replies and deletes, and the catch-all. The validation of
an item lives in test_feedback_input.py, the bodies and their budget in
test_feedback_body.py, the HTTP-level access matrix in test_feedback_matrix.py
and the consent pages in test_feedback_consent.py. All build on the helpers
here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
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
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_feedback_images import JPEG_SIPS, PNG_ANIMATED, PNG_FILE, WEBP_LOSSY, png_header

from toon import messages
from toon.access.decision import FeedbackPolicy, allow
from toon.auth.sessions import CONTENT_ANCHOR_COOKIE, CONTENT_SESSION_COOKIE, Session, SessionKind, SessionStore, sign
from toon.config import Settings
from toon.constants import (
    FEEDBACK_BODY_ALLOWANCE,
    FEEDBACK_MAX_GIVER_REPLIES,
    FEEDBACK_MAX_REPLIES,
    AccessBase,
    AccessPolicy,
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
from toon.serving import feedback_access, feedback_api, feedback_body
from toon.serving.feedback_api import PREFLIGHT_HEADERS, write_counter_sweep
from toon.serving.feedback_body import BodyBudget
from toon.serving.feedback_routes import router as feedback_router
from toon.serving.feedback_token import TOKEN_LIFETIME_S, mint_consent_nonce, mint_page_token
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
    app.state. There is no middleware, so an answer is exactly what a route
    built."""
    app = FastAPI()
    app.state.settings = settings
    app.state.session_store = SessionStore()
    app.state.session_factory = factory
    app.state.feedback_author_writes = InMemoryCounter()
    app.state.feedback_site_writes = InMemoryCounter()
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
    create take longer. Without it, every create of a burst would have
    counted before the first one committed, which is the race the lock is
    there to close, made certain instead of likely."""

    async def commit(self) -> None:
        await asyncio.sleep(0.05)
        await super().commit()


@pytest_asyncio.fixture
async def committed(migrated_dsn: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Real connections and real commits, for requests that run at the same
    time. The conftest empties the tables before the next database test."""
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
    }
    base.update(changes)
    return base


def long_text(length: int, char: str = "a") -> str:
    return char * length


POINT_ANCHOR = {
    "selectors": [{"type": "CssSelector", "value": "main > p:nth-of-type(3)"}],
    "point": {"x": 0.25, "y": 0.5, "pageX": 340, "pageY": 1200.5},
    "area": None,
}

AREA_ANCHOR = {
    "selectors": [{"type": "CssSelector", "value": "main > p:nth-of-type(3)"}],
    "point": None,
    "area": {
        "x": 0.1,
        "y": 0.2,
        "width": 0.5,
        "height": 0.25,
        "pageX": 64,
        "pageY": 1200.5,
        "pageWidth": 320,
        "pageHeight": 90,
    },
}

TEXT_ANCHOR = {
    "selectors": [
        {"type": "TextPositionSelector", "start": 1200, "end": 1211},
        {"type": "CssSelector", "value": "main > p:nth-of-type(3)"},
        {"type": "TextQuoteSelector", "exact": "2,4 miljoen", "prefix": "begroting van ", "suffix": " euro voor"},
    ],
    "point": None,
    "area": None,
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


def problem_body(key: str, status: int, *, locale: str = "en", **params: object) -> dict[str, object]:
    return {
        "type": "about:blank",
        "title": messages.title(locale, status),
        "status": status,
        "detail": messages.render(locale, Msg(key, params)),
        "code": messages.code_of(key),
    }


def assert_problem(response: httpx.Response, status: int, key: str, *, locale: str = "en", **params: object) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == problem_body(key, status, locale=locale, **params)
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
        "author_kind": FeedbackIdentity.ANONYMOUS,
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
    return await add_item(factory, site_id, author_kind=FeedbackIdentity.ACCOUNT, author_sub=sub, **values)


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
    """What the write counters hold now for the author and for the site. The
    author is named as on the site (`session:<sub>`, `gate:<sub>`), and is the
    test client's address unless it is named."""
    author_bucket = app.state.feedback_author_writes._buckets.get(f"site:{site_id}:{author}")
    site_bucket = app.state.feedback_site_writes._buckets.get(f"site:{site_id}")
    return (author_bucket.count if author_bucket else 0, site_bucket.count if site_bucket else 0)


def author_keys(app: FastAPI) -> set[str]:
    return set(app.state.feedback_author_writes._buckets)


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
        network error. With it, the widget reads the status and says to try
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

    async def _assert_refused(self, client, token: str, value: object = None) -> None:
        await self._assert_unavailable(client, lambda headers: create(client, token, value, headers=headers))

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

    @pytest.mark.parametrize("ground", ["another-secret", "a-consent-nonce", "expired"])
    async def test_a_token_that_does_not_hold(self, client, environment, ground):
        app, site_id, now = environment.app, environment.world.site_id, int(time.time())
        if ground == "another-secret":
            other = "een-ander-geheim-van-minstens-32-bytes!"
            fields = {"ref": None, "version_id": None, "session_id": None, "key_id": None}
            token = mint_page_token(other, site_id=site_id, kind="live", now=now, **fields)
        elif ground == "a-consent-nonce":
            session_id = consented_session(app).id
            token = mint_consent_nonce(SECRET, session_id=session_id, site_id=site_id, return_to=ROOT, now=now)
        else:
            token = page_token(app, site_id, now=now - TOKEN_LIFETIME_S - 1)
        await self._assert_refused(client, token)

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
        await self._assert_refused(client, token)

    async def test_consent_withdrawn_while_the_idp_is_asked(self, client, environment, monkeypatch):
        """The consent is checked again after the revalidation, which awaits
        the IdP and so lets a withdrawal in between."""
        app, world = environment.app, environment.world
        session = consented_session(app, world.site_id)

        async def withdrawn_meanwhile(request, found):
            return app.state.session_store.withdraw_feedback(found.id, str(world.site_id))

        monkeypatch.setattr(feedback_access, "revalidate_session", withdrawn_meanwhile)
        token = page_token(app, world.site_id, session_id=session.id)
        await self._assert_refused(client, token)

    async def test_a_session_the_idp_ended(self, environment):
        """The page token carries no cookie, so the API runs the IdP check
        itself. A session the IdP ended is gone, and the token with it."""
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
        await self._assert_refused(client, token)

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
        await self._assert_refused(client, token)

    async def test_a_preview_that_is_gone(self, client, environment):
        token = page_token(environment.app, environment.world.site_id, ref="pr-1")
        async with environment.factory() as db:
            await db.execute(delete(Preview).where(Preview.ref == "pr-1"))
            await db.commit()
        await self._assert_refused(client, token, item(page={"path": PREVIEW_ROOT, "title": None}))

    async def test_another_site_at_the_slugs_is_a_refusal(self, client, environment, monkeypatch):
        """The site is found by id and the gate decides by slug. Should the
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
        await self._assert_refused(client, token)

    async def test_an_allow_without_feedback_settings_is_feedback_off(self, client, environment, monkeypatch):
        world = environment.world

        async def bare_allow(db, group, site, ref, visitor):
            return allow(world.live_version_id, AccessPolicy(AccessBase.PUBLIC), site_id=world.site_id)

        monkeypatch.setattr(feedback_access, "decide_page", bare_allow)
        token = page_token(environment.app, world.site_id)
        await self._assert_refused(client, token)

    async def test_a_site_deleted_after_the_check_is_unavailable_not_a_500(self, client, environment, monkeypatch):
        """The site's row is locked inside the create transaction. One that
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
            # SQL NULL, not JSON null: none_as_null on the column maps None.
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

    @pytest.mark.parametrize(
        "wire",
        [
            {**AREA_ANCHOR, "area": {**AREA_ANCHOR["area"]}},
            {"selectors": AREA_ANCHOR["selectors"], "area": AREA_ANCHOR["area"]},
        ],
        ids=["whole", "without-a-point-key"],
    )
    async def test_an_area_item_stores_the_anchor_it_was_rebuilt_to(self, client, environment, wire):
        token = page_token(environment.app, environment.world.site_id)
        response = await create(client, token, item(kind="area", anchor=wire))
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.kind is FeedbackKind.AREA
        assert stored.anchor == AREA_ANCHOR
        assert stored.quote is None

    async def test_a_text_item_stores_its_selectors_in_order_and_its_quote(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        response = await create(client, token, item(kind="text", anchor=TEXT_ANCHOR))
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.anchor == {
            "selectors": [TEXT_ANCHOR["selectors"][2], TEXT_ANCHOR["selectors"][0], TEXT_ANCHOR["selectors"][1]],
            "point": None,
            "area": None,
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
            "area": None,
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
        assert stored.anchor == {"selectors": [quote, css], "point": None, "area": None}

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
        assert stored.anchor == {"selectors": [quote, css], "point": None, "area": None}

    async def test_the_title_is_cleaned_and_cut_rather_than_refused(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        title = "\u202e Begroting\x00 " + long_text(400, "b")
        response = await create(client, token, item(page={"path": ROOT, "title": title}))
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.page_title == ("Begroting " + long_text(400, "b"))[:300]
        assert stored.page_path == ""

    @pytest.mark.parametrize("title", [None, " \u200b\u202e ", ""], ids=["none", "only-format-characters", "empty"])
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

    @pytest.mark.parametrize(
        ("author", "kind", "name"),
        [
            ({"kind": "name", "name": "  Jan de Vries  "}, "name", "Jan de Vries"),
            ({"kind": "anonymous", "name": "   "}, "anonymous", None),
        ],
        ids=["name-stores-the-typed-name-only", "anonymous-stores-nothing"],
    )
    async def test_a_consented_session_adds_nothing_to_a_typed_author(
        self, client, environment, author, kind, name
    ):
        app, world = environment.app, environment.world
        session = consented_session(app, world.site_id)
        token = page_token(app, world.site_id, session_id=session.id)
        response = await create(client, token, item(author=author))
        assert response.status_code == 201
        (stored,) = await stored_items(environment.factory)
        assert stored.author_kind.value == kind
        assert (stored.author_sub, stored.author_name, stored.author_email) == (None, name, None)


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

    async def test_the_quota_of_the_site_counts_its_stored_screenshots(self, environment, monkeypatch):
        world = environment.world
        size = len(PNG_FILE)
        monkeypatch.setattr(feedback_api, "FEEDBACK_SITE_SCREENSHOT_MAX_BYTES", 2 * size)
        app = environment.app
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

    async def test_the_platform_quota_counts_every_site(self, environment, monkeypatch):
        world = environment.world
        size = len(PNG_FILE)
        monkeypatch.setattr(feedback_api, "FEEDBACK_TOTAL_SCREENSHOT_MAX_BYTES", 2 * size)
        app = environment.app
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
        monkeypatch.setattr(feedback_api, "FEEDBACK_TOTAL_SCREENSHOT_MAX_BYTES", size)
        if site_full_too:
            monkeypatch.setattr(feedback_api, "FEEDBACK_SITE_SCREENSHOT_MAX_BYTES", size)
        app = environment.app
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
            f"space is freed. Logged at most once per {interval} seconds."
        )
        logged = feedback_log(caplog)
        assert [(record.levelno, record.getMessage()) for record in logged] == [(logging.WARNING, line)] * 2

    async def test_a_refusal_by_the_cap_of_a_site_is_not_logged(self, environment, monkeypatch, caplog):
        world = environment.world
        monkeypatch.setattr(feedback_api, "_quota_warnings", InMemoryCounter())
        monkeypatch.setattr(feedback_api, "FEEDBACK_SITE_SCREENSHOT_MAX_BYTES", len(PNG_FILE))
        app = environment.app
        with caplog.at_level(logging.INFO, logger="toon.serving.feedback_api"):
            async with client_for(app) as other:
                assert (await create(other, page_token(app, world.site_id), screenshot=PNG_FILE)).status_code == 201
                refused = await create(other, page_token(app, world.site_id), screenshot=PNG_FILE)
        assert_problem(refused, 409, "FEEDBACK_SCREENSHOT_QUOTA")
        assert feedback_log(caplog) == []


class TestTheOpenItemCap:
    async def test_the_cap_counts_new_and_seen_items_of_this_site_only(self, environment, monkeypatch):
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
        monkeypatch.setattr(feedback_api, "FEEDBACK_SITE_MAX_OPEN", 3)
        app = environment.app
        async with client_for(app) as other:
            assert (await create(other, page_token(app, world.site_id))).status_code == 201
            refused = await create(other, page_token(app, world.site_id), headers={"Accept-Language": "nl"})
        assert_problem(refused, 409, "FEEDBACK_SITE_FULL", locale="nl")


def one_client_burst(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every request of a burst comes from one client, which may otherwise
    have only a few bodies in flight at once."""
    monkeypatch.setattr(feedback_body, "FEEDBACK_BODIES_PER_CLIENT", 100)


class TestConcurrentCreates:
    """Under the advisory lock the checks and the insert of one create cannot
    interleave with another's: however many arrive at once, the cap and the
    quotas hold exactly."""

    async def _burst(self, app: FastAPI, requests: list[tuple[str, object, bytes | None]]) -> list[httpx.Response]:
        async with client_for(app) as other:
            return await asyncio.gather(
                *(create(other, token, value, screenshot=shot) for token, value, shot in requests)
            )

    async def test_the_open_item_cap(self, committed, tmp_path, monkeypatch):
        one_client_burst(monkeypatch)
        monkeypatch.setattr(feedback_api, "FEEDBACK_SITE_MAX_OPEN", 3)
        world = await seed(committed)
        app = make_app(make_settings(tmp_path), committed)
        responses = await self._burst(app, [(page_token(app, world.site_id), item(), None)] * 12)
        assert sorted(response.status_code for response in responses) == [201] * 3 + [409] * 9
        assert len(await stored_items(committed)) == 3

    async def test_the_screenshot_quota_of_a_site(self, committed, tmp_path, monkeypatch):
        one_client_burst(monkeypatch)
        monkeypatch.setattr(feedback_api, "FEEDBACK_SITE_SCREENSHOT_MAX_BYTES", 3 * len(PNG_FILE))
        world = await seed(committed)
        app = make_app(make_settings(tmp_path), committed)
        responses = await self._burst(app, [(page_token(app, world.site_id), item(), PNG_FILE)] * 12)
        assert sorted(response.status_code for response in responses) == [201] * 3 + [409] * 9

    async def test_the_platform_quota_across_two_sites(self, committed, tmp_path, monkeypatch):
        one_client_burst(monkeypatch)
        monkeypatch.setattr(feedback_api, "FEEDBACK_TOTAL_SCREENSHOT_MAX_BYTES", 3 * len(PNG_FILE))
        world = await seed(committed)
        app = make_app(make_settings(tmp_path), committed)
        ander = item(page={"path": "/aurora/ander/", "title": None})
        requests = [(page_token(app, world.site_id), item(), PNG_FILE)] * 6 + [
            (page_token(app, world.other_site_id), ander, PNG_FILE)
        ] * 6
        responses = await self._burst(app, requests)
        assert sorted(response.status_code for response in responses) == [201] * 3 + [409] * 9
        async with committed() as db:
            assert await db.scalar(select(func.sum(FeedbackScreenshot.size_bytes))) == 3 * len(PNG_FILE)


# --- The write counter --------------------------------------------------------------


class TestTheWriteCounter:
    async def test_every_attempt_counts_per_author(self, environment, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(feedback_api, "_clock", lambda: now[0])
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_ADDRESS", 3)
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_AUTHOR", 1)
        app = environment.app
        token = page_token(app, environment.world.site_id)
        async with client_for(app) as other:
            # A refused attempt counts too.
            assert (await create(other, token, item(comment=""))).status_code == 422
            # Without a session the address is the author, with the budget of
            # a network that many people can share, not that of one person.
            for _ in range(2):
                assert (await create(other, token)).status_code == 201
            now[0] += 100
            refused = await create(other, token)
            assert_problem(refused, 429, "TOO_MANY_REQUESTS.feedback")
            assert refused.headers["retry-after"] == "500"
            # A consented session is an author of its own, apart from the
            # address, with the budget of one person.
            session = consented_session(app, environment.world.site_id)
            as_session = page_token(app, environment.world.site_id, session_id=session.id)
            assert (await create(other, as_session)).status_code == 201
            assert_problem(await create(other, as_session), 429, "TOO_MANY_REQUESTS.feedback")
            now[0] += 500
            assert (await create(other, token)).status_code == 201

    async def test_an_address_has_a_budget_on_each_site(self, environment, monkeypatch):
        """Keyed on the address and the site: people behind one network who
        give feedback on one site leave the budget on another alone."""
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_ADDRESS", 1)
        app = environment.app
        world = environment.world
        async with client_for(app) as other:
            assert (await create(other, page_token(app, world.site_id))).status_code == 201
            assert_problem(await create(other, page_token(app, world.site_id)), 429, "TOO_MANY_REQUESTS.feedback")
            ander = item(page={"path": "/aurora/ander/", "title": None})
            assert (await create(other, page_token(app, world.other_site_id), ander)).status_code == 201
        assert author_keys(app) == {
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
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_AUTHOR", 1)
        app = environment.app
        world = environment.world
        session = consented_session(app, world.site_id, world.other_site_id)

        def token(site_id: uuid.UUID) -> str:
            if role == "session":
                return page_token(app, site_id, session_id=session.id)
            return page_token(app, site_id, access_session_id=session.id)

        ander = item(page={"path": "/aurora/ander/", "title": None})
        async with client_for(app) as other:
            assert (await create(other, token(world.site_id))).status_code == 201
            assert_problem(await create(other, token(world.site_id)), 429, "TOO_MANY_REQUESTS.feedback")
            assert (await create(other, token(world.other_site_id), ander)).status_code == 201
            assert_problem(await create(other, token(world.other_site_id), ander), 429, "TOO_MANY_REQUESTS.feedback")
        assert author_keys(app) == {
            f"site:{world.site_id}:{role}:kijker",
            f"site:{world.other_site_id}:{role}:kijker",
        }

    async def test_the_site_counts_every_author_together(self, environment, monkeypatch):
        now = [5000.0]
        monkeypatch.setattr(feedback_api, "_clock", lambda: now[0])
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_SITE", 2)
        app = environment.app
        world = environment.world
        first = consented_session(app, world.site_id, sub="een")
        second = consented_session(app, world.site_id, sub="twee")
        async with client_for(app) as other:
            assert (await create(other, page_token(app, world.site_id, session_id=first.id))).status_code == 201
            assert (await create(other, page_token(app, world.site_id))).status_code == 201
            now[0] += 100
            refused = await create(other, page_token(app, world.site_id, session_id=second.id))
            assert_problem(refused, 429, "TOO_MANY_REQUESTS.feedback")
            assert refused.headers["retry-after"] == "500"
            # Another site has a budget of its own.
            ander = item(page={"path": "/aurora/ander/", "title": None})
            assert (await create(other, page_token(app, world.other_site_id), ander)).status_code == 201

    async def test_replies_and_deletes_are_writes_too(self, environment, monkeypatch):
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_AUTHOR", 2)
        app = environment.app
        world = environment.world
        session = consented_session(app, world.site_id)
        token = page_token(app, world.site_id, session_id=session.id)
        first = await account_item(environment.factory, world.site_id)
        async with client_for(app) as other:
            assert (await reply(other, token, first)).status_code == 201
            assert (await other.delete(f"{ITEMS}/{first}", headers=auth(token))).status_code == 204
            assert_problem(await reply(other, token, first), 429, "TOO_MANY_REQUESTS.feedback")
            spent = await other.delete(f"{ITEMS}/{first}", headers=auth(token))
            assert_problem(spent, 429, "TOO_MANY_REQUESTS.feedback")
            # Reading costs nothing.
            assert (await other.get(ITEMS, headers=auth(token))).status_code == 200

    async def test_a_create_reserves_the_sites_write_before_its_body_arrives(self, environment, monkeypatch):
        """A create that declares its length and sends nothing has spent its
        author's attempt and holds the site's write before its body arrives.
        With the site's last write held, a create beside it is refused, and
        the write stays the held create's once it is stored."""
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_SITE", 1)
        world = environment.world
        app = environment.app
        token = page_token(app, world.site_id)
        silent = HeldBody(head=b"", tail=ITEM_HEAD + ITEM_TAIL)
        async with client_for(app) as client, client_for(app, address="192.0.2.7") as other:
            held = await held_create(client, token, silent, declared=silent.length)
            assert writes_spent(app, world.site_id) == (1, 1)
            assert_problem(await create(other, token), 429, "TOO_MANY_REQUESTS.feedback")
            assert writes_spent(app, world.site_id, author="ip:192.0.2.7") == (1, 1)
            silent.release.set()
            assert (await held).status_code == 201
        assert writes_spent(app, world.site_id) == (1, 1)
        assert len(await stored_items(environment.factory)) == 1

    async def test_concurrent_writes_cannot_take_the_site_past_its_budget(self, committed, tmp_path, monkeypatch):
        """Each write reserves the site's before anything is awaited: of a
        burst from several authors exactly the site's budget is stored."""
        one_client_burst(monkeypatch)
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_SITE", 3)
        world = await seed(committed)
        app = make_app(make_settings(tmp_path), committed)
        token = page_token(app, world.site_id)
        clients = [client_for(app, address=f"192.0.2.{host}") for host in range(1, 13)]
        responses = await asyncio.gather(*(create(client, token) for client in clients))
        for client in clients:
            await client.aclose()
        assert sorted(response.status_code for response in responses) == [201] * 3 + [429] * 9
        assert len(await stored_items(committed)) == 3
        assert writes_spent(app, world.site_id, author="ip:192.0.2.1") == (1, 3)
        assert set(app.state.feedback_site_writes._buckets) == {f"site:{world.site_id}"}
        assert len(author_keys(app)) == 12

    @pytest.mark.parametrize("ending", ["failure", "thread-full", "bad-body", "too-large"])
    async def test_every_other_end_after_admission_gives_the_sites_write_back(self, environment, monkeypatch, ending):
        """Whatever ends a write after its reservation without storing it
        gives the site its write back, a failure nobody foresaw included:
        the site's last write is still there for the next create."""
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_SITE", 1)
        world = environment.world
        app = environment.app
        session = consented_session(app, world.site_id)
        token = page_token(app, world.site_id, session_id=session.id)
        own = await account_item(environment.factory, world.site_id)
        async with client_for(app) as client:
            if ending == "failure":
                store = feedback_api._store

                async def broken(*args: object) -> None:
                    raise RuntimeError("database weg")

                monkeypatch.setattr(feedback_api, "_store", broken)
                assert (await create(client, token)).status_code == 500
                monkeypatch.setattr(feedback_api, "_store", store)
            elif ending == "thread-full":
                await add_replies(environment.factory, own, giver=FEEDBACK_MAX_GIVER_REPLIES)
                assert_problem(await reply(client, token, own), 409, "FEEDBACK_THREAD_FULL")
            elif ending == "bad-body":
                assert_problem(await reply(client, token, own, {"body": ""}), 422, "FEEDBACK_INVALID", field="body")
            else:
                monkeypatch.setattr(app.state.settings, "feedback_screenshot_max_bytes", 1)
                refused = await create(client, token, screenshot=PNG_FILE, screenshot_type="image/png")
                assert_problem(refused, 413, "FEEDBACK_SCREENSHOT_TOO_LARGE")
            assert writes_spent(app, world.site_id, author="session:kijker") == (1, 0)
            assert (await create(client, token)).status_code == 201
        assert writes_spent(app, world.site_id, author="session:kijker") == (2, 1)

    @pytest.mark.parametrize("budget", ["FEEDBACK_WRITES_PER_AUTHOR", "FEEDBACK_WRITES_PER_SITE"])
    @pytest.mark.parametrize("route", ["create", "reply"])
    async def test_a_spent_budget_is_refused_before_the_body_is_read(self, environment, monkeypatch, route, budget):
        """No byte of a body that cannot be taken is read, nor does it get a
        place among the bodies in flight, whether the author's budget or the
        site's is spent."""
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        monkeypatch.setattr(feedback_api, budget, 1)
        world = environment.world
        app = environment.app
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
        assert_problem(response, 429, "TOO_MANY_REQUESTS.feedback")
        assert body.pulled is False
        assert writes_spent(app, world.site_id, author="session:kijker") == (2, 1)

    async def test_the_cleanup_job_drops_the_ended_windows_without_a_write(self, monkeypatch):
        """With no write at all, the last giver's key still goes: the job
        cleans the counter once per window."""
        now = [0.0]
        monkeypatch.setattr(feedback_api, "_clock", lambda: now[0])
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_WINDOW_S", 0.01)
        counter = InMemoryCounter()
        counter.add("ip:192.0.2.1", 10, 0.0)
        cleaned, ended = asyncio.Event(), asyncio.Event()
        cleanup = counter.cleanup

        def watched(now_: float) -> int:
            removed = cleanup(now_)
            cleaned.set()
            if now_ >= 10.0:
                ended.set()
            return removed

        monkeypatch.setattr(counter, "cleanup", watched)
        async with write_counter_sweep(counter) as task:
            await asyncio.wait_for(cleaned.wait(), 5)
            # Its window still runs.
            assert len(counter) == 1
            now[0] = 10.0
            await asyncio.wait_for(ended.wait(), 5)
            assert len(counter) == 0
        assert task.cancelled()

    async def test_a_burst_cannot_pass_the_check_together(self, committed, tmp_path, monkeypatch):
        """Counted with nothing awaited between the count and the check: of a
        burst from one author exactly the budget passes."""
        one_client_burst(monkeypatch)
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_ADDRESS", 3)
        world = await seed(committed)
        app = make_app(make_settings(tmp_path), committed)
        token = page_token(app, world.site_id)
        async with client_for(app) as client:
            responses = await asyncio.gather(*(create(client, token) for _ in range(12)))
        assert sorted(response.status_code for response in responses) == [201] * 3 + [429] * 9
        assert len(await stored_items(committed)) == 3
        assert writes_spent(app, world.site_id) == (12, 3)

    async def test_a_refused_write_counts_for_its_author_not_for_the_site(self, environment, monkeypatch):
        monkeypatch.setattr(feedback_api, "FEEDBACK_SITE_MAX_OPEN", 1)
        world = environment.world
        await add_item(environment.factory, world.site_id)
        app = environment.app
        session = consented_session(app, world.site_id)
        as_session = page_token(app, world.site_id, session_id=session.id)
        foreign = await account_item(environment.factory, world.site_id, "iemand-anders")
        async with client_for(app) as client:
            assert_problem(await create(client, page_token(app, world.site_id)), 409, "FEEDBACK_SITE_FULL")
            assert_problem(
                await create(client, page_token(app, world.site_id), screenshot=b"geen beeld"),
                422,
                "FEEDBACK_SCREENSHOT_INVALID",
            )
            assert_problem(await reply(client, as_session, foreign), 404, "FEEDBACK_UNAVAILABLE")
            assert_problem(
                await client.delete(f"{ITEMS}/{foreign}", headers=auth(as_session)), 404, "FEEDBACK_UNAVAILABLE"
            )
        assert writes_spent(app, world.site_id) == (2, 0)
        assert writes_spent(app, world.site_id, author="session:kijker") == (2, 0)

    async def test_a_refusal_of_the_account_or_the_item_id_counts_nowhere(self, environment):
        world = environment.world
        app = environment.app
        own = await account_item(environment.factory, world.site_id)
        without_session = page_token(app, world.site_id)
        as_session = page_token(app, world.site_id, session_id=consented_session(app, world.site_id).id)
        async with client_for(app) as client:
            for token, target, refusal in (
                (without_session, own, (403, "FEEDBACK_ACCOUNT_REQUIRED")),
                (as_session, "geen-uuid", (404, "FEEDBACK_UNAVAILABLE")),
            ):
                assert_problem(await reply(client, token, target), *refusal)
                assert_problem(await client.delete(f"{ITEMS}/{target}", headers=auth(token)), *refusal)
        assert writes_spent(app, world.site_id) == (0, 0)
        assert writes_spent(app, world.site_id, author="session:kijker") == (0, 0)

    async def test_refused_attempts_cannot_lock_the_site_out(self, environment, monkeypatch):
        """One holder of the page token who keeps sending what is refused,
        by validation or by their own budget, leaves the site's budget to
        everyone else."""
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_ADDRESS", 2)
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_SITE", 1)
        world = environment.world
        app = environment.app
        token = page_token(app, world.site_id)
        async with client_for(app) as flood, client_for(app, address="192.0.2.7") as other:
            refusals = [(await create(flood, token, item(comment=""))).status_code for _ in range(5)]
            assert refusals == [422, 422, 429, 429, 429]
            assert writes_spent(app, world.site_id) == (5, 0)
            assert (await create(other, token)).status_code == 201
            assert_problem(await create(other, token), 429, "TOO_MANY_REQUESTS.feedback")

    async def test_a_flood_of_author_keys_cannot_evict_a_sites_key(self, environment, monkeypatch):
        monkeypatch.setattr(feedback_api, "_clock", lambda: 5000.0)
        monkeypatch.setattr(feedback_api, "FEEDBACK_WRITES_PER_SITE", 1)
        world = environment.world
        app = environment.app
        app.state.feedback_author_writes = InMemoryCounter(max_keys=2)
        token = page_token(app, world.site_id)
        async with client_for(app) as client:
            assert (await create(client, token)).status_code == 201
        for host in range(1, 6):
            async with client_for(app, address=f"192.0.2.{host}") as churn:
                assert_problem(await create(churn, token), 429, "TOO_MANY_REQUESTS.feedback")
        assert len(app.state.feedback_author_writes) == 2
        assert writes_spent(app, world.site_id) == (0, 1)


# --- The giver's own items --------------------------------------------------------


async def reply(client: httpx.AsyncClient, token: str, item_id: object, body: object = None) -> httpx.Response:
    return await client.post(
        f"{ITEMS}/{item_id}/replies",
        json={"body": "Dank, ik kijk ernaar."} if body is None else body,
        headers=auth(token),
    )


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
            author_kind=FeedbackIdentity.ACCOUNT,
            author_sub="kijker",
            kind=FeedbackKind.POINT,
            anchor=POINT_ANCHOR,
            created_at=func.now() - text("interval '1 hour'"),
        )
        newer = await add_item(
            factory,
            world.site_id,
            author_kind=FeedbackIdentity.ACCOUNT,
            author_sub="kijker",
            kind=FeedbackKind.TEXT,
            anchor={"selectors": [TEXT_ANCHOR["selectors"][2]], "point": None, "area": None},
            quote="2,4 miljoen",
            target=VersionTarget.PREVIEW,
            preview_ref="pr-1",
            page_path="",
            page_title="Voorbeeld",
            status=FeedbackStatus.RESOLVED,
            resolved_at=func.now(),
        )
        # Not the visitor's: another sub, a name, no name, another site.
        await add_item(factory, world.site_id, author_kind=FeedbackIdentity.ACCOUNT, author_sub="iemand-anders")
        await add_item(factory, world.site_id, author_kind=FeedbackIdentity.NAME, author_name="kijker")
        await add_item(factory, world.site_id)
        await add_item(factory, world.other_site_id, author_kind=FeedbackIdentity.ACCOUNT, author_sub="kijker")
        async with factory() as db:
            db.add_all(
                [
                    FeedbackReply(
                        item_id=older,
                        author_kind=FeedbackReplyAuthor.MEMBER,
                        member_id=world.member_id,
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

    async def test_a_team_reply_shows_the_name_its_member_has_now(self, client, environment):
        """The name is read from the member with the list: a rename shows, and
        a member without a name or one deleted since shows none."""
        app, world, factory = environment.app, environment.world, environment.factory
        own = await account_item(factory, world.site_id)
        async with factory() as db:
            nameless = Member(sso_subject="lid-zonder-naam", email="naamloos@example.org", status=MemberStatus.ACTIVE)
            gone = Member(sso_subject="lid-weg", email="weg@example.org", name="Lid Weg", status=MemberStatus.ACTIVE)
            db.add_all([nameless, gone])
            await db.flush()
            db.add_all(
                FeedbackReply(
                    item_id=own,
                    author_kind=FeedbackReplyAuthor.MEMBER,
                    member_id=member_id,
                    body=body,
                    created_at=func.now() - text(f"interval '{minutes} minutes'"),
                )
                for minutes, (member_id, body) in enumerate(
                    [(world.member_id, "hernoemd"), (nameless.id, "naamloos"), (gone.id, "verwijderd")]
                )
            )
            await db.commit()
        async with factory() as db:
            await db.execute(update(Member).where(Member.id == world.member_id).values(name="Lid Hernoemd"))
            await db.execute(delete(Member).where(Member.id == gone.id))
            await db.commit()
        session = consented_session(app, world.site_id)
        response = await client.get(ITEMS, headers=auth(page_token(app, world.site_id, session_id=session.id)))
        (entry,) = response.json()["items"]
        assert [(r["author"], r["name"], r["body"]) for r in entry["replies"]] == [
            ("team", None, "verwijderd"),
            ("team", None, "naamloos"),
            ("team", "Lid Hernoemd", "hernoemd"),
        ]

    async def test_a_page_item_has_no_anchor_and_a_text_item_keeps_its_ancestor(self, client, environment):
        app, world = environment.app, environment.world
        await account_item(environment.factory, world.site_id)
        await add_item(
            environment.factory,
            world.site_id,
            author_kind=FeedbackIdentity.ACCOUNT,
            author_sub="kijker",
            kind=FeedbackKind.TEXT,
            anchor={
                "selectors": [TEXT_ANCHOR["selectors"][2], TEXT_ANCHOR["selectors"][1]],
                "point": None,
                "area": None,
            },
            quote="2,4 miljoen",
            created_at=func.now() + text("interval '1 minute'"),
        )
        session = consented_session(app, world.site_id)
        response = await client.get(ITEMS, headers=auth(page_token(app, world.site_id, session_id=session.id)))
        text_item, page_item = response.json()["items"]
        assert text_item["anchor"] == {"k": "text", "s": "main > p:nth-of-type(3)", "q": "2,4 miljoen"}
        assert page_item["anchor"] is None

    async def test_an_area_item_carries_its_rectangle_in_the_reduced_anchor(self, client, environment):
        app, world = environment.app, environment.world
        await account_item(environment.factory, world.site_id, kind=FeedbackKind.AREA, anchor=AREA_ANCHOR)
        session = consented_session(app, world.site_id)
        response = await client.get(ITEMS, headers=auth(page_token(app, world.site_id, session_id=session.id)))
        (entry,) = response.json()["items"]
        assert entry["kind"] == "area"
        assert entry["anchor"] == {
            "k": "area",
            "s": "main > p:nth-of-type(3)",
            "x": 0.1,
            "y": 0.2,
            "w": 0.5,
            "h": 0.25,
            "px": 64,
            "py": 1200.5,
            "pw": 320,
            "ph": 90,
        }

    @pytest.mark.parametrize(("stored", "more"), [(21, True), (20, False)])
    async def test_at_most_the_twenty_newest(self, client, environment, stored, more):
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
                    author_kind=FeedbackIdentity.ACCOUNT,
                    author_sub="kijker",
                    created_at=func.now() - text(f"interval '{minutes} minutes'"),
                )
                for minutes in range(stored)
            )
            await db.commit()
        session = consented_session(app, world.site_id)
        response = await client.get(ITEMS, headers=auth(page_token(app, world.site_id, session_id=session.id)))
        assert [entry["comment"] for entry in response.json()["items"]] == [
            f"{minutes} minuten oud" for minutes in range(20)
        ]
        assert response.json()["more"] is more

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


async def giver_with_item(environment: Environment) -> tuple[str, uuid.UUID]:
    world = environment.world
    session = consented_session(environment.app, world.site_id)
    own = await account_item(environment.factory, world.site_id)
    return page_token(environment.app, world.site_id, session_id=session.id), own


class TestReplies:
    async def test_a_token_without_a_session_needs_an_account(self, client, environment):
        _, own = await giver_with_item(environment)
        token = page_token(environment.app, environment.world.site_id)
        assert_problem(await reply(client, token, own), 403, "FEEDBACK_ACCOUNT_REQUIRED")

    async def test_a_reply_on_the_givers_own_item(self, client, environment):
        token, own = await giver_with_item(environment)
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
        assert stored.member_id is None

    async def test_a_reply_takes_a_code_point_the_servers_tables_do_not_know(self, client, environment):
        assert unicodedata.category(UNASSIGNED) == "Cn"
        token, own = await giver_with_item(environment)
        response = await reply(client, token, own, {"body": f"Dank {UNASSIGNED}"})
        assert response.status_code == 201
        assert response.json()["body"] == f"Dank {UNASSIGNED}"
        async with environment.factory() as db:
            assert (await db.scalars(select(FeedbackReply.body))).all() == [f"Dank {UNASSIGNED}"]

    async def test_a_reply_keeps_the_two_joiners_writing_needs(self, client, environment):
        token, own = await giver_with_item(environment)
        text = f"Dank je {JOINED_EMOJI} {PERSIAN}"
        response = await reply(client, token, own, {"body": text})
        assert response.status_code == 201, response.text
        assert response.json()["body"] == text
        async with environment.factory() as db:
            assert (await db.scalars(select(FeedbackReply.body))).all() == [text]

    @pytest.mark.parametrize("char", list(REFUSED_FORMAT.values()), ids=list(REFUSED_FORMAT))
    async def test_a_reply_with_any_other_format_character_is_refused(self, client, environment, char):
        token, own = await giver_with_item(environment)
        assert_invalid(await reply(client, token, own, {"body": f"Dank{char} je"}), "body")
        assert await replies_on(environment.factory, own) == 0

    async def test_an_item_that_is_not_the_givers_own_is_unavailable(self, client, environment):
        token, own = await giver_with_item(environment)
        world = environment.world
        reference = await reply(client, "nep", own)
        for other in (
            "geen-uuid",
            uuid.uuid4(),
            await account_item(environment.factory, world.site_id, "iemand-anders"),
            await add_item(environment.factory, world.site_id, author_kind=FeedbackIdentity.NAME, author_name="kijker"),
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
            ("application/json", json.dumps({"body": "a\u202eb"}).encode()),
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
        token, own = await giver_with_item(environment)
        response = await client.post(
            f"{ITEMS}/{own}/replies", content=body, headers=auth(token, **{"Content-Type": content_type})
        )
        assert_invalid(response, "body")


class TestReplyCaps:
    """At most FEEDBACK_MAX_GIVER_REPLIES from the giver and
    FEEDBACK_MAX_REPLIES in all on one item, so neither a thread nor Mijn
    feedback grows without end."""

    async def test_the_givers_own_replies_stop_at_their_cap(self, client, environment):
        token, own = await giver_with_item(environment)
        await add_replies(environment.factory, own, giver=FEEDBACK_MAX_GIVER_REPLIES - 1, member=5)
        assert (await reply(client, token, own)).status_code == 201
        full = await client.post(
            f"{ITEMS}/{own}/replies", json={"body": "Nog één."}, headers=auth(token, **{"Accept-Language": "nl"})
        )
        assert_problem(full, 409, "FEEDBACK_THREAD_FULL", locale="nl")
        assert await replies_on(environment.factory, own) == FEEDBACK_MAX_GIVER_REPLIES + 5
        # The refused reply counts for its author.
        assert writes_spent(environment.app, environment.world.site_id, author="session:kijker") == (2, 1)
        # The cap is per item: another item of theirs still takes replies.
        other = await account_item(environment.factory, environment.world.site_id)
        assert (await reply(client, token, other)).status_code == 201

    async def test_every_reply_counts_toward_the_cap_in_all(self, client, environment):
        token, own = await giver_with_item(environment)
        await add_replies(environment.factory, own, member=FEEDBACK_MAX_REPLIES - 1)
        assert (await reply(client, token, own)).status_code == 201
        assert_problem(await reply(client, token, own), 409, "FEEDBACK_THREAD_FULL")
        assert await replies_on(environment.factory, own) == FEEDBACK_MAX_REPLIES


class TestConcurrentReplies:
    """Under the item's lock the count and the insert of one reply cannot
    interleave with another's: however many arrive at once, the caps hold
    exactly."""

    async def _burst(self, committed, tmp_path: Path, monkeypatch, **prefilled: int) -> tuple[list[int], uuid.UUID]:
        one_client_burst(monkeypatch)
        world = await seed(committed)
        app = make_app(make_settings(tmp_path), committed)
        own = await account_item(committed, world.site_id)
        await add_replies(committed, own, **prefilled)
        token = page_token(app, world.site_id, session_id=consented_session(app, world.site_id).id)
        async with client_for(app) as client:
            responses = await asyncio.gather(*(reply(client, token, own) for _ in range(12)))
        return sorted(response.status_code for response in responses), own

    @pytest.mark.parametrize(
        ("prefilled", "room", "total"),
        [
            ({"giver": FEEDBACK_MAX_GIVER_REPLIES - 2}, 2, FEEDBACK_MAX_GIVER_REPLIES),
            ({"member": FEEDBACK_MAX_REPLIES - 5}, 5, FEEDBACK_MAX_REPLIES),
        ],
        ids=["the-givers-cap", "the-cap-in-all"],
    )
    async def test_the_caps_hold_exactly(self, committed, tmp_path, monkeypatch, prefilled, room, total):
        statuses, own = await self._burst(committed, tmp_path, monkeypatch, **prefilled)
        assert statuses == [201] * room + [409] * (12 - room)
        assert await replies_on(committed, own) == total


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


# --- The catch-all and the admin host ----------------------------------------


KNOWN_PATHS = [
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
# What each known path answers itself. Everything else is the catch-all.
ROUTED = {
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
        async with client_for(app, ADMIN_URL) as client:
            response = await client.request(
                method,
                path,
                headers={"Sec-Fetch-Dest": "document", "Authorization": "Toon-Feedback x"},
                params={"returnTo": ROOT} if method == "GET" else None,
            )
        assert is_neutral_404(response)

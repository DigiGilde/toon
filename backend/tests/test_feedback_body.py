"""Tests for the bodies of the feedback widget API
(serving/feedback_body.py): a create's body that is no multipart form, or
one that does not hold, is a 422 or a 413 and never a 500, and the budget for
the bodies in flight, the share per client and the deadline per body hold
however uploads stall. Against the real PostgreSQL test container, with the
app and helpers of test_feedback_api.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import tracemalloc
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import update
from starlette.requests import Request
from test_feedback_api import (
    ITEM_HEAD,
    ITEM_TAIL,
    ITEMS,
    MULTIPART,
    ROOT,
    Environment,
    HeldBody,
    account_item,
    assert_invalid,
    assert_problem,
    auth,
    client_for,
    consented_session,
    create,
    held_create,
    held_post,
    item,
    long_text,
    make_settings,
    open_environment,
    page_token,
    parts,
    replies_on,
    reply,
    rolled_back,
    stored_items,
    whole_body,
    writes_spent,
)
from test_feedback_images import PNG_FILE

from toon.constants import FEEDBACK_BODY_ALLOWANCE, FeedbackIdentity
from toon.models.publication import Site
from toon.serving import feedback_body
from toon.serving.feedback_body import BodyBudget


@pytest_asyncio.fixture
async def environment(migrated_dsn: str, tmp_path: Path) -> AsyncIterator[Environment]:
    async with rolled_back(migrated_dsn) as factory:
        yield await open_environment(factory, tmp_path)


@pytest_asyncio.fixture
async def client(environment: Environment) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(environment.app) as c:
        yield c


# --- The body of a create: 422 or 413, never 500 -------------------------------


class TestTheBodyOfACreate:
    async def test_an_item_over_32_kib_is_refused(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        raw = json.dumps(item(comment="a", page={"path": ROOT, "title": long_text(33 * 1024)}))
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


# --- The bodies in flight -----------------------------------------------------------

# The smallest budget there may be: one whole create at a screenshot limit of
# 1 KiB.
BUDGET = 1024 + FEEDBACK_BODY_ALLOWANCE


def one_create(environment: Environment, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """An app whose budget is BUDGET."""
    monkeypatch.setattr(feedback_body, "FEEDBACK_BODY_BUDGET_CREATES", 1)
    return environment.app_with(feedback_screenshot_max_bytes=1024)


async def fill_budget(
    client: httpx.AsyncClient, token: str, *, free: int = 0
) -> tuple[list[HeldBody], list[asyncio.Task[httpx.Response]]]:
    """Three whole creates of one client, held before their end, that leave
    `free` bytes of BUDGET."""
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

    async def test_a_create_beyond_the_budget_is_busy_until_the_room_is_given_back(self, environment, monkeypatch):
        world = environment.world
        app = one_create(environment, monkeypatch)
        token = page_token(app, world.site_id)
        async with client_for(app) as client, client_for(app, address="192.0.2.7") as other:
            held, sent = await fill_budget(client, token)
            assert app.state.feedback_bodies.in_flight == BUDGET
            for language in ("en", "nl"):
                busy = await create(other, token, headers={"Accept-Language": language})
                assert_problem(busy, 503, "FEEDBACK_BUSY", locale=language)
                assert busy.headers["retry-after"] == "60"
            # A busy attempt counts for its author and gives the site its
            # write back: the site's three are the held creates'.
            assert writes_spent(app, world.site_id, author="ip:192.0.2.7") == (2, 3)
            assert await finish(held, sent) == [201] * 3
            assert (await create(other, token)).status_code == 201
        assert app.state.feedback_bodies.in_flight == 0
        assert app.state.feedback_bodies._per_client == {}
        assert len(await stored_items(environment.factory)) == 4

    async def test_a_length_declared_and_not_sent_holds_no_room(self, environment, monkeypatch):
        """The route's whole limit is the whole BUDGET: a body that
        declares it and sends nothing still leaves the budget to the rest."""
        app = one_create(environment, monkeypatch)
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
    async def test_a_body_holds_room_for_the_bytes_that_arrived(self, environment, declared, monkeypatch):
        """Not the route's whole limit, and not the length it declares."""
        app = one_create(environment, monkeypatch)
        token = page_token(app, environment.world.site_id)
        held = HeldBody()
        async with client_for(app) as client:
            first = await held_create(client, token, held, declared=held.length if declared else None)
            assert app.state.feedback_bodies.in_flight == len(held.head)
            held.release.set()
            assert (await first).status_code == 201
        assert app.state.feedback_bodies.in_flight == 0

    async def test_a_chunk_that_does_not_fit_is_busy_and_its_body_gives_back_what_it_took(
        self, environment, monkeypatch
    ):
        world = environment.world
        app = one_create(environment, monkeypatch)
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
            assert writes_spent(app, world.site_id, author="ip:192.0.2.7") == (1, 3)
            assert await finish(held, sent) == [201] * 3
        assert bodies.in_flight == 0
        assert len(await stored_items(environment.factory)) == 3

    async def test_a_body_beyond_the_limit_is_too_large_not_busy(self, environment, monkeypatch):
        """The bound comes before the room: a body that can never be taken
        is 413, which the widget does not ask to try again, even where
        BUDGET, the size of the limit, cannot hold its chunk."""
        app = one_create(environment, monkeypatch)
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
    async def test_the_retry_after_is_the_deadline(self, environment, ground, monkeypatch):
        """Every body in flight is read, or refused, within the deadline."""
        monkeypatch.setattr(feedback_body, "FEEDBACK_BODIES_PER_CLIENT", 3)
        monkeypatch.setattr(feedback_body, "FEEDBACK_BODY_DEADLINE_S", 5)
        app = one_create(environment, monkeypatch)
        token = page_token(app, environment.world.site_id)
        address = "192.0.2.7" if ground == "full-budget" else "127.0.0.1"
        async with client_for(app) as client, client_for(app, address=address) as asking:
            held, sent = await fill_budget(client, token)
            busy = await create(asking, token)
            await finish(held, sent)
        assert_problem(busy, 503, "FEEDBACK_BUSY")
        assert busy.headers["retry-after"] == "5"

    async def test_a_client_has_no_more_bodies_in_flight_than_its_share(self, environment, monkeypatch):
        world = environment.world
        monkeypatch.setattr(feedback_body, "FEEDBACK_BODIES_PER_CLIENT", 1)
        app = environment.app
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
        # The busy create gave the site its write back.
        assert writes_spent(app, world.site_id) == (3, 3)

    async def test_an_ipv6_client_counts_by_its_64(self, environment, monkeypatch):
        """As the rate limiter counts: one subscriber holds a whole /64."""
        monkeypatch.setattr(feedback_body, "FEEDBACK_BODIES_PER_CLIENT", 1)
        app = environment.app
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

    async def test_a_reply_takes_its_room_too(self, environment, monkeypatch):
        world = environment.world
        monkeypatch.setattr(feedback_body, "FEEDBACK_BODIES_PER_CLIENT", 1)
        app = environment.app
        session = consented_session(app, world.site_id)
        own = await account_item(environment.factory, world.site_id)
        held = HeldBody()
        async with client_for(app) as client:
            first = await held_create(client, page_token(app, world.site_id), held)
            refused = await reply(client, page_token(app, world.site_id, session_id=session.id), own)
            held.release.set()
            await first
        assert_problem(refused, 503, "FEEDBACK_BUSY")
        assert writes_spent(app, world.site_id, author="session:kijker") == (1, 1)
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
        assert writes_spent(environment.app, environment.world.site_id) == (1, 0)

    @pytest.mark.parametrize(
        "declared", ["²", "1e3", "-5", " 12"], ids=["superscript-digit", "float", "negative", "space"]
    )
    async def test_a_length_that_does_not_read_is_no_length(self, environment, declared, monkeypatch):
        """`isdigit` alone would take a superscript two for a digit, and
        `int` would then fail on it. The body is read to the route's limit,
        with room for what arrives."""
        app = one_create(environment, monkeypatch)
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
    async def test_a_body_still_coming_at_the_deadline_is_408(self, environment, route, caplog, monkeypatch):
        world = environment.world
        monkeypatch.setattr(feedback_body, "FEEDBACK_BODY_DEADLINE_S", 1)
        app = environment.app
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
        assert writes_spent(app, world.site_id, author="session:kijker") == (1, 0)
        assert await replies_on(environment.factory, own) == 0
        assert len(await stored_items(environment.factory)) == 1
        assert [record for record in caplog.records if record.levelno >= logging.WARNING] == []

    async def test_an_aborted_upload_is_one_line_in_the_log_not_a_traceback(self, environment, caplog):
        """The client is gone, so nobody reads the answer. What matters is
        that the room is given back and the log not
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
        assert writes_spent(app, world.site_id) == (1, 0)
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
        room = feedback_body._Room(BodyBudget(), len(body), len(body))
        tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            before = tracemalloc.get_traced_memory()[0]
            item_part, screenshot_part = await feedback_body.create_parts(Request(scope, receive), room, size)
            held = tracemalloc.get_traced_memory()[1] - before
        finally:
            tracemalloc.stop()
        assert json.loads(item_part) == item()
        assert screenshot_part == screenshot
        assert room.taken == len(body)
        # A part reserves some room as it grows. A copy would add all of it.
        assert held < 1.5 * room.taken, f"{held} bytes held for {room.taken} counted"

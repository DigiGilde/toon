"""The bodies of the feedback widget API: the budget they share and how
they are read.

The body is read from the stream with a bound on every part, so no input ends
in a 500. Every chunk of a body takes its room in a budget for the whole
process before it is read, and the parts keep the bytes as they arrived,
without a copy. A client has at most a few bodies in flight, and each is read
within a deadline, so stalled uploads cannot add up to more and a body that
sends nothing holds nothing.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import aclosing, contextmanager
from dataclasses import dataclass

from fastapi import Request
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header

from toon import net
from toon.config import Settings
from toon.constants import (
    FEEDBACK_BODIES_PER_CLIENT,
    FEEDBACK_BODY_ALLOWANCE,
    FEEDBACK_BODY_BUDGET_CREATES,
    FEEDBACK_BODY_DEADLINE_S,
)
from toon.messages import Msg
from toon.serving.feedback_images import ImageInfo
from toon.serving.feedback_input import ProblemError, bad_field

# The JSON of one item, with room to spare.
MAX_ITEM_BYTES = 32 * 1024


def too_large() -> ProblemError:
    return ProblemError(413, Msg("FEEDBACK_SCREENSHOT_TOO_LARGE"))


def bad_body() -> ProblemError:
    return bad_field("body")


def client_key(request: Request) -> str:
    """The client as the rate limiter counts one that sends no cookie: its
    address, IPv6 by the /64."""
    settings: Settings = request.app.state.settings
    return f"ip:{net.rate_limit_key(net.client_ip(request, bool(settings.behind_proxy)))}"


# --- The bodies in flight -----------------------------------------------------


class BodyBudget:
    """The widget API bodies this process holds right now: the bytes that
    arrived of all of them together, and how many each client has in flight.
    A body takes room for each chunk before it is read and gives all of it
    back when its request is done, so however many uploads stall, they hold
    no more than the budget, and a declared length that never arrives holds
    nothing."""

    def __init__(self) -> None:
        self.in_flight = 0
        self._per_client: dict[str, int] = {}

    def admit(self, client: str, *, per_client: int) -> bool:
        """One more body from `client`, or False when it has as many in
        flight as it may have."""
        held = self._per_client.get(client, 0)
        if held >= per_client:
            return False
        self._per_client[client] = held + 1
        return True

    def take(self, size: int, *, budget: int) -> bool:
        """Room for `size` more bytes, or False when the budget cannot hold
        them. Nothing awaits in here, so two chunks cannot both take the
        last room."""
        if self.in_flight + size > budget:
            return False
        self.in_flight += size
        return True

    def give_back(self, client: str, size: int) -> None:
        """A body is done: the `size` bytes it took go back, and so does its
        place among the client's bodies."""
        self.in_flight -= size
        held = self._per_client.pop(client) - 1
        if held:
            self._per_client[client] = held


def _busy() -> ProblemError:
    # Every body in flight now is read, or refused, within the deadline.
    return ProblemError(503, Msg("FEEDBACK_BUSY"), retry_after=FEEDBACK_BODY_DEADLINE_S)


class _Room:
    """One body's room in the budget: the bound to read it to, and the bytes
    it has taken so far."""

    def __init__(self, bodies: BodyBudget, budget: int, bound: int) -> None:
        self.bound = bound
        self.taken = 0
        self._bodies = bodies
        self._budget = budget

    def take(self, size: int) -> None:
        """Room for a chunk of `size` bytes before it is read, or 503
        FEEDBACK_BUSY when the budget is full."""
        if not self._bodies.take(size, budget=self._budget):
            raise _busy()
        self.taken += size


# 2**64 has 20 digits: a Content-Length with more is beyond any body, and is
# refused before int() is asked to read it.
_MAX_LENGTH_DIGITS = 20


@contextmanager
def body_room(request: Request, limit: int, refusal: Callable[[], ProblemError]) -> Iterator[_Room]:
    """This request's body among the bodies in flight until the block ends,
    with room for each chunk as it arrives, read to its declared length, or
    to `limit` when it declares none. A declared length above `limit` is
    refused unread, and a client with as many bodies in flight as it may
    have is 503 FEEDBACK_BUSY."""
    settings: Settings = request.app.state.settings
    declared = request.headers.get("content-length", "")
    bound = limit
    if declared.isascii() and declared.isdigit():
        if len(declared) > _MAX_LENGTH_DIGITS or int(declared) > limit:
            raise refusal()
        bound = int(declared)
    client = client_key(request)
    bodies: BodyBudget = request.app.state.feedback_bodies
    if not bodies.admit(client, per_client=FEEDBACK_BODIES_PER_CLIENT):
        raise _busy()
    budget = FEEDBACK_BODY_BUDGET_CREATES * (settings.feedback_screenshot_max_bytes + FEEDBACK_BODY_ALLOWANCE)
    room = _Room(bodies, budget, bound)
    try:
        yield room
    finally:
        bodies.give_back(client, room.taken)


# --- The body of a create -----------------------------------------------------


@dataclass(frozen=True)
class Screenshot:
    data: bytearray
    info: ImageInfo


class _Parts:
    """The two parts of a create, collected as the parser finds them, each
    held to its own bound while it streams in."""

    def __init__(self, screenshot_limit: int) -> None:
        self.found: dict[str, bytearray] = {}
        self.ended = False
        self._screenshot_limit = screenshot_limit
        self._name = ""
        self._header_name = bytearray()
        self._header_value = bytearray()
        self._disposition = b""

    def callbacks(self) -> dict[str, Callable[..., None]]:
        return {
            "on_part_begin": self._part_begin,
            "on_header_field": self._header_field,
            "on_header_value": self._header_value_data,
            "on_header_end": self._header_end,
            "on_headers_finished": self._headers_finished,
            "on_part_data": self._part_data,
            "on_end": self._end,
        }

    def _part_begin(self) -> None:
        self._disposition = b""

    def _header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_name += data[start:end]

    def _header_value_data(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def _header_end(self) -> None:
        if self._header_name.lower() == b"content-disposition":
            self._disposition = bytes(self._header_value)
        self._header_name = bytearray()
        self._header_value = bytearray()

    def _headers_finished(self) -> None:
        disposition, options = parse_options_header(self._disposition)
        name = options.get(b"name", b"").decode("latin-1")
        if disposition != b"form-data" or name not in ("item", "screenshot") or name in self.found:
            raise bad_field("item")
        self._name = name
        self.found[name] = bytearray()

    def _part_data(self, data: bytes, start: int, end: int) -> None:
        part = self.found[self._name]
        part += data[start:end]
        if self._name == "item":
            if len(part) > MAX_ITEM_BYTES:
                raise bad_field("item")
        elif len(part) > self._screenshot_limit:
            raise too_large()

    def _end(self) -> None:
        self.ended = True


# The delimiters a create holds at most: one after the item, one that closes
# the body, and one more when the body opens with a line break before its
# first boundary. RFC 2046 keeps the delimiter out of every part.
_MAX_DELIMITERS = 3


class _Delimited:
    """The parser of a create, handed each chunk once the delimiters in it are
    counted at C speed, those split over two chunks included. The parser
    steps through every delimiter it finds byte by byte in Python, so a body
    packed with them is refused before it is parsed."""

    def __init__(self, parser: MultipartParser, boundary: bytes) -> None:
        self._parser = parser
        self._delimiter = b"\r\n--" + boundary
        self._tail = b""
        self._count = 0

    def write(self, chunk: bytes) -> None:
        keep = len(self._delimiter) - 1
        # One split over two chunks starts in the tail, which is shorter than
        # a delimiter, and ends in the first `keep` bytes of the chunk.
        self._count += (self._tail + chunk[:keep]).count(self._delimiter) + chunk.count(self._delimiter)
        if self._count > _MAX_DELIMITERS:
            raise bad_field("item")
        self._tail = (self._tail + chunk[-keep:])[-keep:]
        self._parser.write(chunk)


async def _bounded(request: Request, room: _Room, refusal: Callable[[], ProblemError]) -> AsyncIterator[bytes]:
    """The body as it streams in, each chunk with its room taken before it
    is read, and refused once the body is longer than the room's bound."""
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > room.bound:
            raise refusal()
        room.take(len(chunk))
        yield chunk


async def _read(
    request: Request, room: _Room, refusal: Callable[[], ProblemError], take: Callable[[bytes], object]
) -> None:
    """Hands the body to `take` as it streams in, at most the room's bound,
    and all of it within the deadline, or 408 FEEDBACK_TIMEOUT."""
    try:
        # Around the loop rather than inside `_bounded`: a timeout scope must
        # not span the yield of an async generator.
        async with asyncio.timeout(FEEDBACK_BODY_DEADLINE_S), aclosing(_bounded(request, room, refusal)) as body:
            async for chunk in body:
                take(chunk)
    except TimeoutError:
        raise ProblemError(408, Msg("FEEDBACK_TIMEOUT")) from None


async def create_parts(request: Request, room: _Room, screenshot_limit: int) -> tuple[bytearray, bytearray | None]:
    media_type, options = parse_options_header(request.headers.get("content-type"))
    boundary = options.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary:
        raise bad_field("item")
    parts = _Parts(screenshot_limit)
    try:
        parser = _Delimited(MultipartParser(boundary, parts.callbacks()), boundary)
        await _read(request, room, too_large, parser.write)
    except FormParserError:
        raise bad_field("item") from None
    item = parts.found.get("item")
    if not parts.ended or item is None:
        raise bad_field("item")
    # The parts as the parser filled them: a copy would hold the screenshot a
    # second time, outside the budget.
    return item, parts.found.get("screenshot")


async def reply_bytes(request: Request, room: _Room) -> bytes:
    if parse_options_header(request.headers.get("content-type"))[0] != b"application/json":
        raise bad_field("body")
    chunks: list[bytes] = []
    await _read(request, room, bad_body, chunks.append)
    return b"".join(chunks)


__all__ = [
    "BodyBudget",
    "Screenshot",
    "bad_body",
    "body_room",
    "client_key",
    "create_parts",
    "reply_bytes",
    "too_large",
]

"""The one way out to the server: one client per run, the User-Agent, the
API version check and the retries."""

from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx

from toon_cli.common import VERSION, IncompatibleServer

# The major of the server API contract (the `API-Version` response header)
# this CLI understands. A server with a higher major is refused.
SUPPORTED_API_MAJOR = 1
API_VERSION_RE = re.compile(r"^(\d+)\.\d+\.\d+$")


def _check_api_version(response: httpx.Response) -> None:
    match = API_VERSION_RE.match(response.headers.get("API-Version", ""))
    if match and int(match.group(1)) > SUPPORTED_API_MAJOR:
        raise IncompatibleServer(
            f"this server speaks API {match.group(1)}.x, this Toon CLI "
            f"({VERSION}) supports API {SUPPORTED_API_MAJOR}.x. Install a "
            "newer CLI: uv tool install --force "
            '"git+https://github.com/DigiGilde/toon@<tag>#subdirectory=cli"'
        )


_client: Any = None

# What a retry may follow, for calls that pass `retry_on`. A request that
# never left (connect errors) is safe to repeat whatever it does; any
# transport error, a read timeout included, only for a call that is
# idempotent.
RETRY_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout)
RETRY_ANY_TRANSPORT = (httpx.TransportError,)
RETRY_STATUSES = frozenset({502, 503, 504})
RETRY_DELAY_S = 1.0


def _http(
    method: str,
    url: str,
    retry_on: tuple[type[Exception], ...] = (),
    retry_headers: Callable[[], dict[str, str]] | None = None,
    stream: bool = False,
    **kwargs: Any,
) -> httpx.Response:
    """The one way out to the server: sets the User-Agent and refuses a server
    with a newer API major before any caller reads the response.

    One client for the whole run, so a login poll or a refresh followed by a
    request reuses the connection instead of a new TLS handshake each time.
    A call that passes `retry_on` is tried once more after RETRY_DELAY_S
    when it fails with one of those errors or the server answers 502, 503
    or 504; nothing else is ever retried. `retry_headers` replaces headers
    for that second try. With `stream` the body is left unread, for the
    caller to read and close."""
    global _client
    if _client is None:
        _client = httpx.Client()
    client = _client
    headers = {**kwargs.pop("headers", {}), "User-Agent": f"toon-cli/{VERSION}"}

    def send(extra: dict[str, str] | None = None) -> httpx.Response:
        merged = {**headers, **(extra or {})}
        if stream:
            return client.send(client.build_request(method, url, headers=merged, **kwargs), stream=True)
        return client.request(method, url, headers=merged, **kwargs)

    def send_again() -> httpx.Response:
        time.sleep(RETRY_DELAY_S)
        return send(retry_headers() if retry_headers else None)

    try:
        response = send()
    except retry_on:
        response = send_again()
    else:
        if retry_on and response.status_code in RETRY_STATUSES:
            response.close()
            response = send_again()
    _check_api_version(response)
    return response


def _request(host: str, method: str, url: str, **kwargs: Any) -> httpx.Response | None:
    """`_http`, except that a connection failure is printed and answered with
    None instead of raised."""
    try:
        return _http(method, url, **kwargs)
    except httpx.HTTPError as error:
        print(f"Error: could not connect to {host}: {error}", file=sys.stderr)
        return None


def _problem_data(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}

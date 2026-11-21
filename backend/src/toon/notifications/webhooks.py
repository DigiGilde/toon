"""A Mattermost incoming webhook: which URLs Toon accepts, and the one way it
posts to one.

The URL is a secret: whoever has it posts in the channel. It is checked when
an admin saves it and again before every post, since the operator can take a
host off the list in between. The server posts only to a host the operator
listed in TOON_FEEDBACK_WEBHOOK_HOSTS, matched exactly with its port, over
https, without following a redirect, without a proxy from the environment,
with short timeouts, and without reading the answer.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from toon.config import HTTPS_PORT, is_ip_literal
from toon.models.notifications import WEBHOOK_URL_MAX

TIMEOUT = httpx.Timeout(5.0, connect=3.0)

# The problem+json keys of a refused URL (toon/messages.py).
KEY_LENGTH = "WEBHOOK_URL_INVALID.length"
KEY_FORM = "WEBHOOK_URL_INVALID.form"
KEY_SCHEME = "WEBHOOK_URL_INVALID.scheme"
KEY_CREDENTIALS = "WEBHOOK_URL_INVALID.credentials"
KEY_IP_LITERAL = "WEBHOOK_URL_INVALID.ip_literal"
KEY_PORT = "WEBHOOK_URL_INVALID.port"
KEY_HOST_NOT_ALLOWED = "WEBHOOK_HOST_NOT_ALLOWED"


class WebhookUrlError(ValueError):
    """A URL Toon will not post to. `key` is the problem+json message key."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


class WebhookDeliveryError(Exception):
    """A post that did not get a 2xx. Its text never holds the URL."""


@dataclass(frozen=True)
class WebhookTarget:
    host: str
    port: int


def check_url(url: str, allowed: frozenset[tuple[str, int]]) -> WebhookTarget:
    """The host and port of `url`, or a WebhookUrlError naming why not.

    In order: the form (length, whitespace, control or non-ASCII characters,
    something urlsplit cannot read), https, no credentials, no IP address,
    then the list. A host on the list with another port is a refusal of the
    port, so an admin learns what to change."""
    if len(url) > WEBHOOK_URL_MAX:
        raise WebhookUrlError(KEY_LENGTH)
    if not url.isascii() or any(char.isspace() or unicodedata.category(char)[0] == "C" for char in url):
        raise WebhookUrlError(KEY_FORM)
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise WebhookUrlError(KEY_FORM) from None
    if parts.scheme.lower() != "https":
        raise WebhookUrlError(KEY_SCHEME)
    if "@" in parts.netloc:
        raise WebhookUrlError(KEY_CREDENTIALS)
    host = (parts.hostname or "").lower()
    if not host or parts.fragment or "\\" in url:
        raise WebhookUrlError(KEY_FORM)
    if is_ip_literal(host) or parts.netloc.startswith("["):
        raise WebhookUrlError(KEY_IP_LITERAL)
    target = WebhookTarget(host=host, port=port if port is not None else HTTPS_PORT)
    if (target.host, target.port) in allowed:
        return target
    if any(allowed_host == target.host for allowed_host, _port in allowed):
        raise WebhookUrlError(KEY_PORT)
    raise WebhookUrlError(KEY_HOST_NOT_ALLOWED)


def make_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """The client every post goes through. `transport` is for tests only."""
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        timeout=TIMEOUT,
        headers={"User-Agent": "Toon"},
        transport=transport,
    )


async def post(client: httpx.AsyncClient, url: str, text: str) -> None:
    """Posts `text` to the webhook. Anything but a 2xx, a redirect included,
    is a WebhookDeliveryError; the body of the answer is never read."""
    try:
        async with client.stream("POST", url, json={"text": text}) as response:
            status = response.status_code
    except httpx.HTTPError as error:
        # The class only: an httpx message can hold the URL.
        raise WebhookDeliveryError(type(error).__name__) from None
    if not 200 <= status < 300:
        raise WebhookDeliveryError(f"HTTP {status}")

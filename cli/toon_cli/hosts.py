"""Which host a command talks to: --host, TOON_HOST, the last login or the
default, checked before anything is sent to it."""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse

import httpx

from toon_cli.common import UsageError
from toon_cli.config import _stored_default_host

DEFAULT_HOST = "https://beheer.toon.rijks.app"
HOST_HELP = (
    "Admin origin of Toon; defaults to TOON_HOST, then the host you last "
    f"logged in to, then {DEFAULT_HOST}"
)

# The DigiGilde instance before it was renamed: a workflow that only changed
# its `uses:` line still names these. Nothing else may translate, and the new
# host comes from this table only, never from what was given.
MOVED_HOSTS = {
    "https://beheer.plak.rijks.app": "https://beheer.toon.rijks.app",
    "https://plak.rijks.app": "https://toon.rijks.app",
}


# Loopback per RFC 6761 §6.3: `localhost` and everything under `.localhost`
# always resolve to the loopback, so `beheer.toon.localhost` of the dev stack
# does too. Beyond that the whole 127.0.0.0/8 block and the IPv6 loopback
# address.
_LOOPBACK_RE = re.compile(
    r"^http://(?:(?:[a-z0-9-]+\.)*localhost|127(?:\.\d{1,3}){3}|\[::1\])(?::\d+)?(?:/|$)",
    re.IGNORECASE,
)


# Where the host came from, and the way out of a refusal. None of them names
# the host: it can carry a password.
INVALID_HOST = {
    "--host": "The host in --host is not valid: pass an address like https://beheer.toon.example.org",
    "TOON_HOST": "The host in TOON_HOST is not valid: set it to an address like https://beheer.toon.example.org, or unset it",
    "login": "The host from your last login is not valid: log in again with 'toon login --host <host>'",
}


def _require_valid_url(host: str, source: str) -> None:
    # httpx builds every request from the host, urllib reads it in the checks
    # below and in _same_origin, and each refuses strings the other takes. Some
    # of that only shows on use: urllib parses the port when it is read, httpx
    # decodes an xn-- label when .host is read, and socket and ssl encode the
    # name httpx hands them (.raw_host) like .encode("idna") does, where an
    # empty label or one over 63 characters is a UnicodeError that httpx lets
    # through.
    not_valid = UsageError(INVALID_HOST[source])
    try:
        url = httpx.URL(host)
        _ = url.host
        url.raw_host.decode("ascii").encode("idna")
        parts = urllib.parse.urlsplit(host)
        _ = parts.port
    except (httpx.InvalidURL, ValueError):
        # Not a parser's message: it can quote the host, and what httpx reads
        # as a port can be a password (https://user:secret#@host).
        raise not_valid from None
    # A #, / or ? in a password ends the authority before the @, so neither
    # parser sees a password any more, and digits before it pass for the port
    # (https://user:12345#@host). An @ in the userinfo itself is left to
    # _require_https, which says what is wrong without naming the host.
    if "?" in host or "#" in host or ("@" in host and parts.username is None):
        raise not_valid


def _require_https(host: str) -> None:
    # Credentials in the host itself: httpx sends them, and every message that
    # names the host afterwards puts them in a terminal or a CI log. The check
    # comes first, so an https:// URL carrying them is refused too.
    if urllib.parse.urlsplit(host).username is not None:
        raise UsageError("Do not put a username or password in --host")
    if host.startswith("https://"):
        return
    if _LOOPBACK_RE.match(host):
        return
    raise UsageError(f"Use https:// for {host} (http only for loopback)")


def _resolve_host(args: argparse.Namespace) -> tuple[str, str]:
    """Host from --host, TOON_HOST in the environment, the host you last
    logged in to, or DEFAULT_HOST, with the key in INVALID_HOST of where it
    came from. An old host of the DigiGilde instance
    becomes its new one here, before anything is built from it: the requests,
    the CI audience and the key a session is stored under all name the new
    one, so a session stored under the old host never goes along."""
    if getattr(args, "host", None):
        host, source = args.host, "--host"
    elif os.environ.get("TOON_HOST"):
        host, source = os.environ["TOON_HOST"], "TOON_HOST"
    else:
        # DEFAULT_HOST always passes the checks, so only a stored host is refused.
        host, source = _stored_default_host() or DEFAULT_HOST, "login"
    host = host.rstrip("/")
    # A hostname has no case, but str.lower() also turns the Kelvin sign into a
    # k, and a host spelled with that only looks like an old one.
    moved = MOVED_HOSTS.get(host.lower()) if host.isascii() else None
    if moved is None:
        return host, source
    print(
        f"Warning: {host} has moved to {moved}. Using the new host. Update --host, "
        "host: in the workflow or TOON_HOST, or log in again if it came from your last login.",
        file=sys.stderr,
    )
    return moved, source


def _same_origin(url: str, host: str) -> bool:
    """Whether url and host share the same scheme, hostname and port. Used to
    check a sign-in URL from the server before it is shown or opened: a
    server returning a URL to another domain (or file://) is ignored."""
    try:
        url_parts = urllib.parse.urlsplit(url)
        host_parts = urllib.parse.urlsplit(host)
        # urllib only reads the port when asked, and raises then.
        url_port, host_port = url_parts.port, host_parts.port
    except ValueError:
        return False
    if url_parts.scheme not in ("http", "https") or url_parts.scheme != host_parts.scheme:
        return False
    if not url_parts.hostname or url_parts.hostname != host_parts.hostname:
        return False
    default_port = 443 if url_parts.scheme == "https" else 80
    return (url_port or default_port) == (host_port or default_port)


def _setup(args: argparse.Namespace) -> str:
    """The host a command talks to, checked for a valid URL and for https. A
    UsageError ends in `main` as an error line and exit code 2."""
    host, source = _resolve_host(args)
    _require_valid_url(host, source)
    _require_https(host)
    return host

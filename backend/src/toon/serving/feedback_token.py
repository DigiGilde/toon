"""The page token, the link ticket, the consent nonce and the hide mark of
site feedback, sealed by serving/feedback_seal.py.

The page token rides in the widget's config on every page Toon adds the
widget to. It names the site and the target the page belongs to, the version
served, the content session and the secret link the gate accepted, and it
expires after twelve hours. The session is there in one of two roles, never
both: `ses` once its visitor consented to feedback with their account on this
site, or else `acc` when the gate let the visitor in through that session and
nothing else, which serves the gate alone. A widget API call is checked again
against the live session, key and gate. The token only says what to check.

The link ticket takes the secret link of a page token to Toon's consent page,
in the widget's way there: the key cookie stays on the site's path and never
reaches `/-/`. It names the key and the site and ends with the page token
minted beside it. It is no credential of its own: the consent page asks the
gate with its link for the signed-in visitor, so a revoked or expired link
lets nobody in. Unlike the page token it goes into an address, so it never
opens as one.

The consent nonce is the credential of Toon's consent form: a POST from that
sandboxed page carries no Lax cookie, so the session it consents for travels
sealed in the form, for ten minutes, with the secret link of the ticket the
page was opened with.

The hide mark is the value of the cookie that hides the button: the site and
an expiry twelve hours on. Sealed, so a value a page writes itself, or one
meant for another site, hides nothing.

Parsing is as strict as minting: a payload with a field minting would never
have written is no token at all. Minting runs the same check, so a token that
parsing refuses never leaves the server.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from toon.auth.sessions import parse_site_path, valid_return_to
from toon.constants import SLUG_RE
from toon.serving.feedback_seal import PURPOSE_CONSENT, PURPOSE_HIDE, PURPOSE_PAGE, seal, unseal

TOKEN_VERSION = 1
TOKEN_LIFETIME_S = 12 * 3600
# No longer than the page token it is minted beside.
LINK_LIFETIME_S = TOKEN_LIFETIME_S
CONSENT_LIFETIME_S = 600
HIDE_LIFETIME_S = 12 * 3600
MAX_RETURN_TO_LENGTH = 2048

# Not among serving/feedback_seal.py's three: a purpose of its own all the
# same, so a ticket opens as nothing else and nothing else as a ticket.
PURPOSE_LINK = b"toon-feedback-link-v1"

# How far an `exp` may lie past a fresh token's lifetime, for a clock that
# stepped back between minting and checking. Any further and Toon did not
# mint it.
_CLOCK_SLACK_S = 60
# A session id is 43 characters and a key id 36. This only bounds what
# parsing takes.
_MAX_HANDLE_LENGTH = 128

_PAGE_FIELDS = frozenset({"v", "site", "kind", "ref", "ver", "ses", "acc", "key", "exp"})
_LINK_FIELDS = frozenset({"key", "site", "exp"})
_CONSENT_FIELDS = frozenset({"ses", "site", "key", "returnTo", "exp"})
_HIDE_FIELDS = frozenset({"site", "exp"})


@dataclass(frozen=True)
class PageToken:
    site_id: uuid.UUID
    kind: Literal["live", "preview"]
    # The preview's ref, None on live.
    ref: str | None
    version_id: uuid.UUID | None
    # Session.id of a content session that consented for this site, never
    # Session.sid.
    session_id: str | None
    # Without that consent, Session.id of the content session the gate let
    # the visitor in with: only to ask the gate again, never an author.
    access_session_id: str | None
    # The secret link the gate let the visitor in with.
    key_id: str | None
    expires_at: int


@dataclass(frozen=True)
class LinkTicket:
    # The secret link the gate let the visitor in with, as the key cookie
    # names it.
    key_id: str
    site_id: uuid.UUID
    expires_at: int


@dataclass(frozen=True)
class ConsentNonce:
    session_id: str
    site_id: uuid.UUID
    # The secret link of the ticket the consent page was opened with, None
    # without one.
    key_id: str | None
    return_to: str
    expires_at: int


@dataclass(frozen=True)
class HideMark:
    site_id: uuid.UUID
    expires_at: int


def mint_page_token(
    secret: str,
    *,
    site_id: uuid.UUID,
    kind: Literal["live", "preview"],
    ref: str | None,
    version_id: uuid.UUID | None,
    session_id: str | None,
    key_id: str | None,
    now: int,
    access_session_id: str | None = None,
) -> str:
    """The sealed page token for a page served at `now` (Unix seconds).
    Minting stores nothing. Raises ValueError for fields `parse_page_token`
    would refuse, a session in both roles among them."""
    payload: dict[str, object] = {
        "v": TOKEN_VERSION,
        "site": str(site_id),
        "kind": kind,
        "ref": ref,
        "ver": None if version_id is None else str(version_id),
        "ses": session_id,
        "acc": access_session_id,
        "key": key_id,
        "exp": now + TOKEN_LIFETIME_S,
    }
    if _page_token(payload, now=now) is None:
        raise ValueError("these fields make no page token")
    return seal(secret, PURPOSE_PAGE, payload)


def parse_page_token(secret: str, token: object, *, now: int) -> PageToken | None:
    """The page token, or None for anything that is not one Toon minted and
    that is still valid at `now`."""
    payload = unseal(secret, PURPOSE_PAGE, token)
    return None if payload is None else _page_token(payload, now=now)


def mint_link_ticket(secret: str, *, key_id: str, site_id: uuid.UUID, now: int) -> str:
    """The sealed link ticket for a page served at `now`, which ends with a
    page token minted at that moment. Raises ValueError for fields
    `parse_link_ticket` would refuse."""
    payload: dict[str, object] = {"key": key_id, "site": str(site_id), "exp": now + LINK_LIFETIME_S}
    if _link_ticket(payload, now=now) is None:
        raise ValueError("these fields make no link ticket")
    return seal(secret, PURPOSE_LINK, payload)


def parse_link_ticket(secret: str, token: object, *, now: int) -> LinkTicket | None:
    """The link ticket, or None for anything that is not one Toon minted and
    that is still valid at `now`."""
    payload = unseal(secret, PURPOSE_LINK, token)
    return None if payload is None else _link_ticket(payload, now=now)


def mint_consent_nonce(
    secret: str, *, session_id: str, site_id: uuid.UUID, return_to: str, now: int, key_id: str | None = None
) -> str:
    """The sealed consent nonce for a consent page shown at `now`, with the
    secret link of its ticket in `key_id`. Raises ValueError for fields
    `parse_consent_nonce` would refuse, which includes a `return_to` too
    long to fit a sealed nonce."""
    payload: dict[str, object] = {
        "ses": session_id,
        "site": str(site_id),
        "key": key_id,
        "returnTo": return_to,
        "exp": now + CONSENT_LIFETIME_S,
    }
    if _consent_nonce(payload, now=now) is None:
        raise ValueError("these fields make no consent nonce")
    return seal(secret, PURPOSE_CONSENT, payload)


def parse_consent_nonce(secret: str, token: object, *, now: int) -> ConsentNonce | None:
    """The consent nonce, or None for anything that is not one Toon minted and
    that is still valid at `now`."""
    payload = unseal(secret, PURPOSE_CONSENT, token)
    return None if payload is None else _consent_nonce(payload, now=now)


def mint_hide_mark(secret: str, *, site_id: uuid.UUID, now: int) -> str:
    """The sealed hide mark for the button hidden on a site at `now`. Raises
    ValueError for fields `parse_hide_mark` would refuse."""
    payload: dict[str, object] = {"site": str(site_id), "exp": now + HIDE_LIFETIME_S}
    if _hide_mark(payload, now=now) is None:
        raise ValueError("these fields make no hide mark")
    return seal(secret, PURPOSE_HIDE, payload)


def parse_hide_mark(secret: str, value: object, *, now: int) -> HideMark | None:
    """The hide mark, or None for anything that is not one Toon minted and
    that is still valid at `now`."""
    payload = unseal(secret, PURPOSE_HIDE, value)
    return None if payload is None else _hide_mark(payload, now=now)


class _RefusedError(Exception):
    """A field that does not hold up, which refuses the whole payload."""


def _page_token(payload: dict[str, object], *, now: int) -> PageToken | None:
    try:
        _expect_fields(payload, _PAGE_FIELDS)
        if _exact_int(payload["v"]) != TOKEN_VERSION:
            raise _RefusedError
        kind = _kind(payload["kind"])
        session_id = None if payload["ses"] is None else _handle(payload["ses"])
        access_session_id = None if payload["acc"] is None else _handle(payload["acc"])
        if session_id is not None and access_session_id is not None:
            raise _RefusedError
        return PageToken(
            site_id=_uuid(payload["site"]),
            kind=kind,
            ref=_slug(payload["ref"]) if kind == "preview" else _null(payload["ref"]),
            version_id=None if payload["ver"] is None else _uuid(payload["ver"]),
            session_id=session_id,
            access_session_id=access_session_id,
            key_id=None if payload["key"] is None else _handle(payload["key"]),
            expires_at=_expiry(payload["exp"], now=now, lifetime=TOKEN_LIFETIME_S),
        )
    except _RefusedError:
        return None


def _link_ticket(payload: dict[str, object], *, now: int) -> LinkTicket | None:
    try:
        _expect_fields(payload, _LINK_FIELDS)
        return LinkTicket(
            key_id=_handle(payload["key"]),
            site_id=_uuid(payload["site"]),
            expires_at=_expiry(payload["exp"], now=now, lifetime=LINK_LIFETIME_S),
        )
    except _RefusedError:
        return None


def _consent_nonce(payload: dict[str, object], *, now: int) -> ConsentNonce | None:
    try:
        _expect_fields(payload, _CONSENT_FIELDS)
        return ConsentNonce(
            session_id=_handle(payload["ses"]),
            site_id=_uuid(payload["site"]),
            key_id=None if payload["key"] is None else _handle(payload["key"]),
            return_to=_return_to(payload["returnTo"]),
            expires_at=_expiry(payload["exp"], now=now, lifetime=CONSENT_LIFETIME_S),
        )
    except _RefusedError:
        return None


def _hide_mark(payload: dict[str, object], *, now: int) -> HideMark | None:
    try:
        _expect_fields(payload, _HIDE_FIELDS)
        return HideMark(
            site_id=_uuid(payload["site"]),
            expires_at=_expiry(payload["exp"], now=now, lifetime=HIDE_LIFETIME_S),
        )
    except _RefusedError:
        return None


def _expect_fields(payload: dict[str, object], fields: frozenset[str]) -> None:
    if payload.keys() != fields:
        raise _RefusedError


def _exact_int(value: object) -> int:
    # bool is an int too, and JSON true must not pass for 1.
    if type(value) is not int:
        raise _RefusedError
    return value


def _expiry(value: object, *, now: int, lifetime: int) -> int:
    expires_at = _exact_int(value)
    if not now < expires_at <= now + lifetime + _CLOCK_SLACK_S:
        raise _RefusedError
    return expires_at


def _uuid(value: object) -> uuid.UUID:
    if not isinstance(value, str):
        raise _RefusedError
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise _RefusedError from None
    # Only the spelling str() writes: uuid.UUID also takes braces, a urn:
    # prefix, capitals and the hex without dashes.
    if str(parsed) != value:
        raise _RefusedError
    return parsed


def _kind(value: object) -> Literal["live", "preview"]:
    if value == "live":
        return "live"
    if value == "preview":
        return "preview"
    raise _RefusedError


def _slug(value: object) -> str:
    if not isinstance(value, str) or not SLUG_RE.fullmatch(value):
        raise _RefusedError
    return value


def _null(value: object) -> None:
    if value is not None:
        raise _RefusedError


def _handle(value: object) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= _MAX_HANDLE_LENGTH:
        raise _RefusedError
    return value


def _return_to(value: object) -> str:
    """A path the login would accept as its returnTo, and a site's path at
    that: whatever leaves through the 303 after consent stays on this host."""
    if (
        not isinstance(value, str)
        or len(value) >= MAX_RETURN_TO_LENGTH
        or valid_return_to(value, default="") != value
        or parse_site_path(value) is None
    ):
        raise _RefusedError
    return value


__all__ = [
    "CONSENT_LIFETIME_S",
    "HIDE_LIFETIME_S",
    "LINK_LIFETIME_S",
    "MAX_RETURN_TO_LENGTH",
    "PURPOSE_LINK",
    "TOKEN_LIFETIME_S",
    "TOKEN_VERSION",
    "ConsentNonce",
    "HideMark",
    "LinkTicket",
    "PageToken",
    "mint_consent_nonce",
    "mint_hide_mark",
    "mint_link_ticket",
    "mint_page_token",
    "parse_consent_nonce",
    "parse_hide_mark",
    "parse_link_ticket",
    "parse_page_token",
]

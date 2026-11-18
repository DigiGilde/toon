"""Message keys, text and slug rules and schema hints that the areas of the admin API share."""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from typing import Any, Literal, overload

from sqlalchemy.exc import IntegrityError

from toon.api.errors import ApiError
from toon.constants import RESERVED_SLUGS, SLUG_RE, FeedbackStatus
from toon.db import violated_constraint
from toon.serving.feedback_input import giver_text

# Message keys in toon/messages.py; the code the SPA branches on is what
# stands before the dot.
KEY_NO_SESSION = "NO_SESSION"
KEY_CSRF_INVALID = "CSRF_INVALID"
KEY_ADMIN_ONLY = "NOT_ADMIN"
KEY_NOT_GROUP_MEMBER = "NOT_GROUP_MEMBER.you"


# Unicode categories no text a member types may contain: Cc (control, e.g.
# NUL, tab, newline), Cf (format, e.g. U+202E right-to-left override, which
# reverses the reading order of everything after it) and Cs (a lone surrogate,
# which asyncpg cannot encode: a 500) - a plain ASCII space is category Zs,
# not Cc/Cf, so ordinary spacing is unaffected.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})

# Longest group name or site title a rename accepts. Not applied on create: a
# new `maxLength` on an existing request field is a breaking change for clients.
TEXT_MAX_LENGTH = 200


def _has_forbidden_characters(value: str) -> bool:
    return any(unicodedata.category(char) in _FORBIDDEN_CATEGORIES for char in value)

ACCESS_EXTRAS_HINT = (
    "Besides the base there are two exceptions that can be added independently of each other: `keys` "
    "lets in anyone with a valid secret link, even without signing in, and `invitees` lets in "
    "signed-in addresses on the invitee list. They widen the base and never narrow it, so with "
    "base `public` they change nothing."
)

ROLE_HINT = (
    "`reader` (views), `editor` (publishes) or `admin` (determines "
    "access, invitees, secret links and who is in the group). A higher role can do everything a "
    "lower role can."
)

SITE_ROLE_HINT = (
    "`reader` (views), `editor` (publishes) or `admin` (determines "
    "access, invitees, secret links and who has a role on the site). A higher role can do everything a "
    "lower role can."
)

MEMBER_IDENTIFIER_HINT = (
    "E-mail address, SSO subject, or the full name as known in the admin interface (case and "
    "leading or trailing spaces ignored). A name only works if it matches exactly "
    "one active platform member; if it matches more than one, the response is 409; use the "
    "e-mail address instead. Case in an e-mail address is ignored."
)


def _timestamp_schema() -> dict[str, Any]:
    """OpenAPI exceptions for a timestamp field: the API writes RFC 3339 in UTC with a Z."""
    return {"format": "date-time", "examples": ["2026-09-12T09:30:00Z"]}


@overload
def _require_aware(value: datetime) -> datetime: ...
@overload
def _require_aware(value: datetime | None) -> datetime | None: ...
def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _validate_slug(slug: str, *, reserved_refused: bool = False) -> str:
    normalised = slug.strip()
    if not SLUG_RE.fullmatch(normalised) or (
        reserved_refused and normalised in RESERVED_SLUGS
    ):
        raise ApiError(422, "SLUG_INVALID", params={"slug": normalised})
    return normalised


# Per kind, what a slug that is taken runs into (migration 0005): the current
# slug of another group or site, or one that another gave up and still holds.
_SLUG_CONSTRAINTS: dict[str, tuple[str, str]] = {
    "group": ("uq_groups_slug", "pk_group_slugs"),
    "site": ("uq_sites_group_slug", "pk_site_slugs"),
}


def _slug_conflict(error: IntegrityError, kind: Literal["group", "site"], slug: str) -> ApiError:
    """The 409 for a slug that is taken, told by the constraint it ran into.
    Any other integrity error is a fault rather than a collision, and goes on
    as it came."""
    current, previous = _SLUG_CONSTRAINTS[kind]
    constraint = violated_constraint(error)
    if constraint == current:
        return ApiError(409, f"SLUG_EXISTS.{kind}", params={"slug": slug})
    if constraint == previous:
        return ApiError(409, f"SLUG_EXISTS.{kind}_previous", params={"slug": slug})
    raise error


def _validate_text(value: str, field: str, *, max_length: int | None = None, prose: bool = False) -> str:
    normalised = value.strip()
    if not normalised:
        raise ApiError(422, "FIELD_EMPTY", params={"field": field})
    # Before the scan below: a huge text is refused without being read again.
    if max_length is not None and len(normalised) > max_length:
        raise ApiError(422, "FIELD_TOO_LONG", params={"field": field, "max": max_length})
    # Prose takes the rule for what a feedback giver writes: line breaks,
    # tabs and the two joiners pass there.
    if giver_text(normalised, multiline=True) is None if prose else _has_forbidden_characters(normalised):
        raise ApiError(422, "FIELD_CONTROL_CHARACTERS", params={"field": field})
    return normalised


_FEEDBACK_OPEN = (FeedbackStatus.NEW, FeedbackStatus.SEEN)

"""The problem+json errors the routes of the admin API declare in the schema."""

from __future__ import annotations

from typing import Any

from toon.api.admin.dependencies import CREATION_MAX_PER_WINDOW, CREATION_WINDOW_S
from toon.api.errors import error_responses

# Every route in this router carries these three; the routes extend them with
# whatever they can refuse themselves.
_ERROR_SESSION = {
    401: "There is no valid admin session: the cookie is missing, invalid or expired (`NO_SESSION`).",
    403: (
        "The request comes from an origin other than the admin host, or the member is not (or no "
        "longer) active (`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`)."
    ),
    429: "The rate limit budget for this session is used up; try again later.",
}
_ERROR_CSRF = {403: "The `X-CSRF-Token` header is missing or does not match the CSRF cookie (`CSRF_INVALID`)."}
_ERROR_ADMIN = {403: "Only a platform administrator is allowed to do this (`NOT_ADMIN`)."}
_ERROR_GROUP_ROLE = {403: "The member's role in this group is insufficient for this action (`INSUFFICIENT_ROLE`)."}
_ERROR_SITE_ROLE = {403: "The member's role on this site is insufficient for this action (`INSUFFICIENT_ROLE`)."}
_ERROR_GROUP = {404: "Unknown group (`UNKNOWN_GROUP`)."}
_ERROR_SITE = {404: "Unknown group (`UNKNOWN_GROUP`) or unknown site (`UNKNOWN_SITE`)."}


_ERROR_SEARCH = {422: "The search term is shorter than two characters (`SEARCH_TOO_SHORT`)."}


_ERROR_IDENTIFIER_AMBIGUOUS = {
    409: (
        "The e-mail address or name belongs to more than one member (`IDENTIFIER_AMBIGUOUS`); if the name is "
        "ambiguous, use the e-mail address; if the e-mail address is ambiguous, use the SSO subject."
    )
}


def _errors(*additions: dict[int, str]) -> dict[int | str, dict[str, Any]]:
    """The fixed session errors plus the route's own, as OpenAPI `responses`."""
    descriptions = dict(_ERROR_SESSION)
    for addition in additions:
        for status, text in addition.items():
            existing = descriptions.get(status)
            descriptions[status] = f"{existing} {text}" if existing else text
    return error_responses(descriptions)


def _deleted(what: str) -> dict[int | str, dict[str, Any]]:
    return {204: {"description": f"{what} No content is returned."}}


_ERROR_CLI_TOKEN = {
    401: (
        "With a Bearer header: it is not a CLI token from `toon login`, or it is invalid, revoked or "
        "expired (`TOKEN_INVALID`); the response then carries `WWW-Authenticate: Bearer`."
    ),
    403: "With a CLI token: the member is not (or no longer) active (`MEMBER_NOT_ACTIVE`).",
}
_ERROR_CREATIONS = {
    429: (
        f"This member has already made {CREATION_MAX_PER_WINDOW} new groups, sites and addresses combined in "
        "the past hour (`TOO_MANY_CREATIONS`); the `Retry-After` header says after how many seconds it is "
        "allowed again."
    )
}
_CREATION_RULE = (
    "Besides the admin session with a valid CSRF header, this is also allowed with a CLI token from "
    "`toon login` (`Authorization: Bearer tooncli_...`), with exactly the same role check; the CSRF header "
    "is then not needed, because a token is not sent along automatically the way a cookie is. A CI ID "
    "token is not allowed. "
    f"Each member has a limit of {CREATION_MAX_PER_WINDOW} new groups, sites and addresses combined per "
    f"{CREATION_WINDOW_S // 60} minutes, across the admin interface and the CLI together."
)

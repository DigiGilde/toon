"""The request and response models of the platform routes."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator

from toon.api.admin.common import _has_forbidden_characters, _timestamp_schema
from toon.api.errors import ApiError
from toon.api.schema import ApiModel, SessionBody
from toon.models.audit import ActorKind
from toon.models.identity import MemberStatus, PlatformRole


class PlatformRoleUpdate(SessionBody):
    """The new platform role of a member."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"platformRole": "admin"}]})

    platform_role: PlatformRole = Field(
        description="`admin` makes the member platform administrator, `member` removes that role again.",
        examples=["admin"],
    )


class VolumeOut(ApiModel):
    """How full the content volume is."""

    total_bytes: int = Field(description="Size of the content volume in bytes.", examples=[1073741824])
    used_bytes: int = Field(description="Bytes in use on the volume.", examples=[536870912])
    free_bytes: int = Field(description="Bytes still free on the volume.", examples=[536870912])
    reserve_bytes: int = Field(
        description=(
            "Free space the volume must keep (`TOON_STORAGE_MIN_FREE_BYTES`); below that a "
            "deploy is refused. `0` means that check is off."
        ),
        examples=[104857600],
    )
    max_deploy_bytes: int = Field(
        description=(
            "Largest unpacked size of one deploy (`TOON_INGEST_MAX_TOTAL`). If free space on the volume is less "
            "than `reserveBytes` plus this number, a deploy of maximum size is no longer possible."
        ),
        examples=[209715200],
    )


AUDIT_PAGE_DEFAULT = 50
AUDIT_PAGE_MAX = 200
AUDIT_PSEUDONYM_LENGTH = 64
REASON_MIN_LENGTH = 10
REASON_MAX_LENGTH = 500
HOLD_REASON_MAX_LENGTH = 10_000
_EMAIL_ADDRESS = re.compile(r"[^\s@]@[^\s@]+\.[^\s@]")


class AuditFilters(ApiModel):
    """Filters on the audit log. Everything is optional and everything combines with AND."""

    limit: int = Field(
        default=AUDIT_PAGE_DEFAULT,
        ge=1,
        le=AUDIT_PAGE_MAX,
        description=f"Number of rows per page, 1 to {AUDIT_PAGE_MAX}.",
        examples=[50],
    )
    cursor: str | None = Field(
        default=None,
        description=(
            "The `nextCursor` from the previous response, copied unchanged. Omit it for the "
            "first page."
        ),
    )
    since: datetime | None = Field(
        default=None,
        description="Only rows from this time onwards (RFC 3339, inclusive). Without a time zone, UTC applies.",
        examples=["2026-09-01T00:00:00Z"],
    )
    until: datetime | None = Field(
        default=None,
        description="Only rows from before this time (RFC 3339, exclusive). Without a time zone, UTC applies.",
        examples=["2026-09-19T00:00:00Z"],
    )
    action: str | None = Field(
        default=None,
        description="Exact action, for example `content_access`, `deploy` or `site_create`.",
        examples=["content_access"],
    )
    result: str | None = Field(
        default=None,
        description="Exact outcome: `allowed`, `refused` or `login_redirect`.",
        examples=["refused"],
    )
    reason_code: str | None = Field(
        default=None,
        description="Exact reason code behind the outcome, for example `UNKNOWN_SITE`.",
        examples=["UNKNOWN_SITE"],
    )
    group: str | None = Field(
        default=None,
        description=(
            "Slug of the group the row is about. Also matches the `previous_group` of a change of address, so "
            "that change is found from the old slug as well as the new one."
        ),
        examples=["aurora"],
    )
    site: str | None = Field(
        default=None,
        description=(
            "Slug of the site the row is about. Also matches the `previous_site` of a change of address, and the "
            "`sites` of a row about a whole group: the sites that moved with its new address or went with it."
        ),
        examples=["docs"],
    )
    actor_pseudonym: str | None = Field(
        default=None,
        description=(
            "Pseudonym of one actor, 64 hexadecimal characters. Fetch it with "
            "`POST /platform/audit/actor-pseudonym`. An e-mail address does not belong here: a query "
            "parameter ends up in proxy logs."
        ),
    )


class AuditEntryOut(ApiModel):
    """One row of the audit log: an action, who did it and how it went."""

    id: uuid.UUID = Field(description="ID of the audit row; also the second sort key after `occurredAt`.")
    occurred_at: str | None = Field(
        default=None,
        description="Time of the action.",
        json_schema_extra=_timestamp_schema(),
    )
    actor_kind: ActorKind = Field(
        description=(
            "Kind of actor: `member` (a signed-in member, including via the CLI), `ci` (a CI workflow with an ID "
            "token), "
            "`system` (the application itself) "
            "or `anonymous` (a visitor without a session)."
        )
    )
    actor_pseudonym: str | None = Field(
        default=None,
        description=(
            "Pseudonym of the actor: HMAC-SHA256 of their identifier under the audit pepper. Equal "
            "pseudonyms mean the same actor, as long as the pepper has not been changed. `null` for "
            "`system` and `anonymous`."
        ),
    )
    action: str = Field(
        description="What happened, for example `content_access` or `site_create`.",
        examples=["content_access"],
    )
    result: str = Field(
        description="How it went: `allowed`, `refused` or `login_redirect`.", examples=["refused"]
    )
    reason_code: str | None = Field(
        default=None,
        description="Reason behind the outcome, for example `UNKNOWN_SITE`.",
        examples=["UNKNOWN_SITE"],
    )
    refs: dict[str, Any] | None = Field(
        default=None,
        description="What the action was about: keys such as `group`, `site`, `preview` and `path`.",
        examples=[{"group": "aurora", "site": "docs"}],
    )
    ip_truncated: str | None = Field(
        default=None,
        description="Network of the visitor, truncated to /24 (IPv4) or /48 (IPv6); never the whole address.",
        examples=["203.0.113.0/24"],
    )


class AuditPage(ApiModel):
    """One page of the audit log, newest row first."""

    entries: list[AuditEntryOut] = Field(description="The rows of this page, newest first.")
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Opaque reference to the next page; pass it back unchanged as `cursor`. "
            "`null` means this was the last page."
        ),
    )


def _validated_reason(value: str) -> str:
    normalised = value.strip()
    if not (REASON_MIN_LENGTH <= len(normalised) <= REASON_MAX_LENGTH):
        raise ValueError(
            f"reason must be {REASON_MIN_LENGTH} to {REASON_MAX_LENGTH} characters after stripping whitespace"
        )
    if _has_forbidden_characters(normalised):
        raise ValueError("reason must not contain control or formatting characters")
    if _EMAIL_ADDRESS.search(normalised):
        raise ValueError("give a case or ticket number in reason, not an email address")
    return normalised


def _validated_hold_reason(value: str) -> str:
    normalised = value.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ").strip()
    if not normalised:
        raise ApiError(422, "HOLD_REASON_EMPTY")
    if len(normalised) > HOLD_REASON_MAX_LENGTH:
        raise ApiError(422, "HOLD_REASON_TOO_LONG", params={"max": HOLD_REASON_MAX_LENGTH})
    if _has_forbidden_characters(normalised.replace("\n", " ")):
        raise ApiError(422, "HOLD_REASON_INVISIBLE_CHARACTER")
    if _EMAIL_ADDRESS.search(normalised):
        raise ApiError(422, "HOLD_REASON_EMAIL")
    return normalised


class ReasonField(SessionBody):
    """Mandatory justification when re-identifying a pseudonym or IP address (spec §12): ends up unchanged
    in the audit row of that re-identification (visible to every platform administrator who reads the
    audit log), and counts towards the daily limit on such re-identifications."""

    reason: str = Field(
        description=(
            f"Why this re-identification is needed, {REASON_MIN_LENGTH} to {REASON_MAX_LENGTH} characters. Give "
            "a case or ticket number, not an e-mail address or other personal data: this field ends up "
            "readable in the audit log, for every platform administrator."
        ),
        examples=["onderzoek naar melding 2026-091"],
    )

    @field_validator("reason")
    @classmethod
    def _reason_length(cls, value: str) -> str:
        return _validated_reason(value)


class ActorLookup(ReasonField):
    """The identifier of an actor, to look up their audit pseudonym with."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"identifier": "lid@example.nl", "reason": "onderzoek naar melding 2026-091"}]
        }
    )

    identifier: str = Field(
        description=(
            "E-mail address or SSO subject of a member or content viewer, or a linked repository as "
            "`owner/repo`, `github.com/owner/repo` or `https://code.overheid.nl/owner/repo`. "
            "An e-mail address is first translated to the SSO subject of that member or viewer, because "
            "that is what is audited."
        ),
        examples=["lid@example.nl"],
    )


class ActorPseudonymOut(ApiModel):
    """The audit pseudonym that belongs to an identifier."""

    actor_pseudonym: str = Field(
        description="Value to use as `actorPseudonym` filter on `GET /platform/audit`."
    )
    previous_pseudonyms: list[str] = Field(
        description=(
            "Pseudonyms of the same identifier under the previous audit peppers, newest first, while a pepper "
            "rotation is in progress. Rows written before the rotation carry one of these: use each as "
            "`actorPseudonym` filter on `GET /platform/audit`. Empty when no previous pepper is configured."
        )
    )
    resolved_as: Literal["member", "content_viewer", "ci", "unknown"] = Field(
        description=(
            "What the identifier belonged to: `member` (translated to their SSO subject), `content_viewer` "
            "(an SSO viewer of protected content, seen in the last 180 days), `ci` "
            "(a repository linked to a site) or `unknown` (pseudonymised as given, only for an "
            "identifier that is not an e-mail address: an e-mail address that belongs to nothing gives a 404)."
        )
    )


class ActorIdentityLookup(ReasonField):
    """An audit pseudonym, to look up the actor behind it."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"actorPseudonym": "a" * AUDIT_PSEUDONYM_LENGTH, "reason": "onderzoek naar melding 2026-091"}
            ]
        }
    )

    actor_pseudonym: str = Field(
        description=f"The pseudonym from the audit log: {AUDIT_PSEUDONYM_LENGTH} hexadecimal characters."
    )


class ActorIdentityOut(ApiModel):
    """The actor behind an audit pseudonym."""

    kind: Literal["member", "content_viewer", "ci"] = Field(
        description="Whether the pseudonym belongs to a member, a content viewer or a CI repository."
    )
    member_id: uuid.UUID | None = Field(default=None, description="Internal ID of the member, for kind `member`.")
    email: str | None = Field(
        default=None,
        description="E-mail address, for kind `member` or `content_viewer`; `null` if none is known.",
    )
    email_verified: bool | None = Field(
        default=None,
        description=(
            "Whether `email` was a verified claim of the identity provider, for kind `content_viewer`. "
            "The forward lookup (`actor-pseudonym`) only matches on a verified e-mail address."
        ),
    )
    name: str | None = Field(
        default=None,
        description="Display name of the member, for kind `member`; empty if the identity provider does not send it.",
    )
    member_status: MemberStatus | None = Field(default=None, description="Status of the member, for kind `member`.")
    last_seen_at: str | None = Field(
        default=None,
        description="Last sign-in on the content host, for kind `content_viewer`.",
        json_schema_extra=_timestamp_schema(),
    )
    provider: Literal["github", "forgejo"] | None = Field(
        default=None, description="CI provider of the repository, for kind `ci`."
    )
    host: str | None = Field(
        default=None,
        description="Base URL of the provider, for kind `ci`.",
        examples=["https://github.com"],
    )
    repository: str | None = Field(
        default=None, description="The repository as `owner/repo`, for kind `ci`.", examples=["minbzk/website"]
    )
    sites: list[str] | None = Field(
        default=None,
        description=(
            "The sites to which this repository is currently linked, as `group/site`, for kind `ci`. An "
            "unlinked repository can no longer be traced."
        ),
        examples=[["aurora/docs"]],
    )


class IpRevealBody(ReasonField):
    """Justification for decrypting the full IP address of one audit row."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"reason": "onderzoek naar melding 2026-091"}]}
    )


class IpRevealOut(ApiModel):
    """The full IP address behind one audit row."""

    ip: str = Field(description="The full IP address as stored with this audit row.")


class PurgeHoldReason(SessionBody):
    """Why the audit purge is put on hold, or why the hold ends."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"reason": "incident 2026-091"}]})

    reason: str = Field(
        description=(
            "Why, at least one character. Line breaks are allowed, and a tab becomes a space. Give a case or "
            "ticket number, not an e-mail address or other personal data: this field ends up readable in the "
            "audit log, for every platform administrator."
        ),
        examples=["incident 2026-091"],
    )


class PurgeHoldOut(ApiModel):
    """The open hold on the audit purge."""

    reason: str = Field(description="Why the hold was started.")
    started_at: str = Field(description="When the hold was started.", json_schema_extra=_timestamp_schema())
    started_by_name: str = Field(
        description="Who started the hold: their name, or their e-mail address if no name is known."
    )


class PurgeHoldStatus(ApiModel):
    """Whether the nightly audit purge is on hold, and since when."""

    on_hold: bool = Field(
        description="`true` while a hold is open: nothing is purged, not even rows past their retention."
    )
    hold: PurgeHoldOut | None = Field(default=None, description="The open hold, or `null` when there is none.")

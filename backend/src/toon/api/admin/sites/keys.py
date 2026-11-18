"""Secret links to a site."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import ConfigDict, Field, field_validator
from sqlalchemy import select, update
from starlette.responses import Response

from toon.access import keys as access_keys
from toon.api.admin.common import _has_forbidden_characters, _require_aware, _timestamp_schema
from toon.api.admin.contract import _ERROR_CSRF, _ERROR_SITE, _ERROR_SITE_ROLE, _deleted, _errors
from toon.api.admin.dependencies import ActiveMember, Csrf, Db
from toon.api.admin.lookup import _audit, _site_with_role, _write_one_or_404
from toon.api.docs import TAG_KEYS
from toon.api.errors import ApiError
from toon.api.schema import ApiModel, SessionBody, iso_utc
from toon.constants import Role
from toon.expiry import MAX_VALIDITY, ExpiryError
from toon.models.publication import AccessKey, KeyStatus


class KeyCreate(SessionBody):
    """A new secret link."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"label": "reviewers", "expiresAt": "2026-12-31T23:59:59Z"},
                {"label": None, "expiresAt": None},
            ]
        }
    )

    label: str | None = Field(
        default=None,
        description=(
            "What this link is for; only for the admin's own use, it does not appear in the URL. Omit "
            "or leave empty for a name with today's date."
        ),
        examples=["reviewers"],
    )
    expires_at: datetime | None = Field(
        default=None,
        description=(
            "Time after which the link stops working (RFC 3339). Omit or `null` for the "
            f"default validity of 90 days; at most {MAX_VALIDITY.days} days ahead."
        ),
        json_schema_extra=_timestamp_schema(),
    )

    @field_validator("label")
    @classmethod
    def _plain_label(cls, value: str | None) -> str | None:
        if value is not None and _has_forbidden_characters(value):
            raise ValueError("label must not contain control or formatting characters")
        return value

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value)


class KeyOut(ApiModel):
    """A secret link, without the secret itself."""

    site_slug: str = Field(description="Slug of the site.", examples=["docs"])
    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    label: str = Field(description="What this link is for; only for the admin.", examples=["reviewers"])
    selector: str = Field(
        description="First half of the key value: the non-secret half, with which you look up the key.",
        examples=["a1b2c3d4"],
    )
    status: KeyStatus = Field(description="`active` (works) or `revoked` (no longer works).")
    created_at: str | None = Field(
        default=None, description="Time of creation.", json_schema_extra=_timestamp_schema()
    )
    expires_at: str | None = Field(
        default=None,
        description="Time after which the link stops working.",
        json_schema_extra=_timestamp_schema(),
    )


class KeyCreated(ApiModel):
    """The fresh key plus its value. This is the only moment the value can be seen."""

    key: KeyOut = Field(description="The created key.")
    value: str = Field(
        description=(
            "The full key value `<selector>.<secret>` for use in the link. Toon only stores a hash, "
            "so this value cannot be retrieved anywhere afterwards."
        ),
        examples=["a1b2c3d4.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"],
    )


def _key_json(key: AccessKey, group_slug: str, site_slug: str) -> KeyOut:
    return KeyOut(
        site_slug=site_slug,
        group_slug=group_slug,
        label=key.label,
        selector=key.selector,
        status=key.status,
        created_at=iso_utc(key.created_at),
        expires_at=iso_utc(key.expires_at),
    )


def add_routes(router: APIRouter) -> None:
    # -- Keys (secret links) --

    @router.get(
        "/sites/{group_slug}/{site_slug}/keys",
        tags=[TAG_KEYS],
        summary="Secret links of a site",
        response_description="The secret links, newest first.",
        description=(
            "The secret links of this site, newest first, including the revoked links. The secret "
            "value is not included: it can only be seen when the link is created.\n\n"
            "**Who can call this:** effective site role `editor` or higher."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def keys(group_slug: str, site_slug: str, member: ActiveMember, db: Db) -> list[KeyOut]:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.EDITOR)
        rows = await db.scalars(
            select(AccessKey)
            .where(AccessKey.site_id == site.id)
            .order_by(AccessKey.created_at.desc())
        )
        return [_key_json(key, group_slug, site_slug) for key in rows]

    @router.post(
        "/sites/{group_slug}/{site_slug}/keys",
        status_code=201,
        tags=[TAG_KEYS],
        summary="Create a secret link",
        response_description="The created key, with its full value shown once.",
        description=(
            "Creates a secret link with which the site can be seen without signing in, while the `keys` exception is "
            "on.\n\n"
            "The response contains `value` once: the full key `<selector>.<secret>`. Toon only stores "
            "a hash, so if you lose the value, create a new key.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {
                422: (
                    "`expiresAt` is not a valid time, lies in the past (`EXPIRY_IN_PAST`) or "
                    "further ahead than allowed (`EXPIRY_TOO_FAR`)."
                )
            },
        ),
    )
    async def create_key(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: KeyCreate,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> KeyCreated:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        try:
            key, value = await access_keys.create_key(db, site.id, body.label, body.expires_at)
        except ExpiryError as error:
            raise ApiError(422, error.message.key, params=error.message.params) from None
        await db.commit()
        await _audit(
            request,
            member,
            "key_create",
            {"group": group_slug, "site": site_slug, "selector": key.selector},
        )
        return KeyCreated(key=_key_json(key, group_slug, site_slug), value=value)

    @router.delete(
        "/sites/{group_slug}/{site_slug}/keys/{selector}",
        status_code=204,
        tags=[TAG_KEYS],
        summary="Revoke a secret link",
        description=(
            "Sets the key to `revoked`; the link no longer works afterwards. The key stays in the list, "
            "so there is a record that it existed. The path uses the `selector`, not the full "
            "key value.\n\n"
            "**Who can call this:** effective site role `editor` or higher, with a valid CSRF header."
        ),
        responses=_deleted("The key has been revoked.")
        | _errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {404: "This site has no key with this selector (`UNKNOWN_KEY`)."},
        ),
    )
    async def revoke_key(
        request: Request,
        group_slug: str,
        site_slug: str,
        selector: str,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> Response:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.EDITOR)
        await _write_one_or_404(
            db,
            update(AccessKey)
            .where(AccessKey.site_id == site.id, AccessKey.selector == selector)
            .values(status=KeyStatus.REVOKED)
            .returning(AccessKey.id),
            "UNKNOWN_KEY",
        )
        await _audit(
            request,
            member,
            "key_revoke",
            {"group": group_slug, "site": site_slug, "selector": selector},
        )
        return Response(status_code=204)

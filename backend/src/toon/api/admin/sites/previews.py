"""The previews of a site and who may see them."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from pydantic import ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from toon.api.admin.common import _FEEDBACK_OPEN, _timestamp_schema
from toon.api.admin.contract import _ERROR_CSRF, _ERROR_SITE, _ERROR_SITE_ROLE, _errors
from toon.api.admin.dependencies import ActiveMember, Csrf, Db
from toon.api.admin.lookup import _audit, _site_with_role
from toon.api.admin.schemas import AccessBody
from toon.api.docs import TAG_PREVIEWS
from toon.api.errors import ApiError
from toon.api.schema import AccessOut, ApiModel, SessionBody, iso_utc
from toon.constants import Role
from toon.models.feedback import FeedbackItem as FeedbackItemRow
from toon.models.publication import Preview, Site, VersionTarget


class PreviewAccessBody(SessionBody):
    """Separate access settings for a preview, or `null` to turn it off."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"access": {"base": "sso", "keys": False, "invitees": False}}, {"access": None}]
        }
    )

    access: AccessBody | None = Field(
        description=(
            "Access that applies only to this preview: base plus exceptions as a whole. "
            "`null` removes the override, after which the preview follows the site again."
        )
    )


class PreviewOut(ApiModel):
    """A preview: a temporary copy of a site alongside the live version."""

    site_slug: str = Field(description="Slug of the site.", examples=["docs"])
    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    ref: str = Field(
        description="Name of the preview, usually the pull request number or the branch name as a slug.",
        examples=["pr-42"],
    )
    version_id: uuid.UUID = Field(description="Version that is on this preview.")
    access_override: AccessOut | None = Field(
        default=None,
        description=(
            "Access that applies only to this preview; `null` means: follows the site."
        ),
    )
    last_updated_at: str | None = Field(
        default=None,
        description="Time of the last deploy to this preview.",
        json_schema_extra=_timestamp_schema(),
    )
    expires_at: str | None = Field(
        default=None,
        description=(
            "Time at which the cleanup job discards this preview. Every new deploy to the same ref pushes "
            "it forward; `null` means it does not expire automatically."
        ),
        json_schema_extra=_timestamp_schema(),
    )
    url: str = Field(
        description="Path of the preview on the content origin from `contentBaseUrl`.",
        examples=["/aurora/docs/_preview/pr-42/"],
    )
    open_feedback_count: int = Field(
        description="Number of feedback items given on this preview with status `new` or `seen`.", examples=[3]
    )


def _preview_json(preview: Preview, group_slug: str, site_slug: str, open_feedback_count: int) -> PreviewOut:
    return PreviewOut(
        site_slug=site_slug,
        group_slug=group_slug,
        ref=preview.ref,
        version_id=preview.version_id,
        access_override=(
            None
            if preview.access_base_override is None
            else AccessOut(
                base=preview.access_base_override,
                keys=bool(preview.access_keys_override),
                invitees=bool(preview.access_invitees_override),
            )
        ),
        last_updated_at=iso_utc(preview.last_updated_at),
        expires_at=iso_utc(preview.expires_at),
        url=f"/{group_slug}/{site_slug}/_preview/{preview.ref}/",
        open_feedback_count=open_feedback_count,
    )


async def _open_feedback_per_preview(db: AsyncSession, site: Site) -> dict[str, int]:
    """Per preview ref, how much feedback given on it is open, in one count
    for every preview of the site."""
    return dict(
        (
            await db.execute(
                select(FeedbackItemRow.preview_ref, func.count())
                .where(
                    FeedbackItemRow.site_id == site.id,
                    FeedbackItemRow.target == VersionTarget.PREVIEW,
                    FeedbackItemRow.status.in_(_FEEDBACK_OPEN),
                )
                .group_by(FeedbackItemRow.preview_ref)
            )
        ).all()
    )


def add_routes(router: APIRouter) -> None:
    # -- Previews --

    @router.get(
        "/sites/{group_slug}/{site_slug}/previews",
        tags=[TAG_PREVIEWS],
        summary="Previews of a site",
        response_description="The previews of the site, sorted by ref.",
        description=(
            "The previews that currently exist for this site, sorted by ref, with their URL on the content "
            "origin, the time at which the cleanup job discards them and how much feedback given on each is "
            "open.\n\n"
            "**Who can call this:** effective site role `reader` or higher."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def previews(group_slug: str, site_slug: str, member: ActiveMember, db: Db) -> list[PreviewOut]:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.READER)
        rows = await db.scalars(
            select(Preview).where(Preview.site_id == site.id).order_by(Preview.ref)
        )
        open_feedback = await _open_feedback_per_preview(db, site)
        return [
            _preview_json(preview, group_slug, site_slug, open_feedback.get(preview.ref, 0)) for preview in rows
        ]

    @router.put(
        "/sites/{group_slug}/{site_slug}/previews/{ref}/access",
        tags=[TAG_PREVIEWS],
        summary="Set the access to a preview",
        response_description="The preview with its new access.",
        description=(
            "Gives this preview its own access settings, independent of the site: so a preview can be "
            "broader or stricter than the live site. It is base plus exceptions as a whole, "
            "never a base of the preview with exceptions of the site. "
            "`access: null` removes the override, after which the preview follows the site again.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            {
                404: (
                    "Unknown group (`UNKNOWN_GROUP`), unknown site (`UNKNOWN_SITE`), or this "
                    "site has no preview with this ref (`UNKNOWN_PREVIEW`)."
                )
            },
        ),
    )
    async def set_preview_access(
        request: Request,
        group_slug: str,
        site_slug: str,
        ref: str,
        body: PreviewAccessBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> PreviewOut:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        preview = await db.scalar(
            select(Preview).where(Preview.site_id == site.id, Preview.ref == ref)
        )
        if preview is None:
            raise ApiError(404, "UNKNOWN_PREVIEW")
        preview.access_base_override = body.access.base if body.access else None
        preview.access_keys_override = body.access.keys if body.access else None
        preview.access_invitees_override = body.access.invitees if body.access else None
        await db.commit()
        await db.refresh(preview)
        await _audit(
            request,
            member,
            "preview_visibility",
            {
                "group": group_slug,
                "site": site_slug,
                "preview": ref,
                "base": str(body.access.base) if body.access else None,
                "keys": body.access.keys if body.access else None,
                "invitees": body.access.invitees if body.access else None,
            },
        )
        open_feedback = await _open_feedback_per_preview(db, site)
        return _preview_json(preview, group_slug, site_slug, open_feedback.get(ref, 0))

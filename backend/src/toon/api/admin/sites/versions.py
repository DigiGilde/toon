"""The versions of a site, rolling back to one, and the storage they take."""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import select

from toon.api.admin.common import _timestamp_schema
from toon.api.admin.contract import _ERROR_CSRF, _ERROR_SITE, _ERROR_SITE_ROLE, _errors
from toon.api.admin.dependencies import ActiveMember, Csrf, Db
from toon.api.admin.lookup import _audit, _site_or_404, _site_with_role
from toon.api.admin.schemas import SiteOut
from toon.api.admin.serialization import _one_site_json
from toon.api.docs import TAG_VERSIONS
from toon.api.errors import ApiError
from toon.api.schema import ApiModel, iso_utc
from toon.constants import Role
from toon.ingest.service import IngestError, IngestService
from toon.ingest.store import version_ref
from toon.models.identity import Member
from toon.models.publication import Site, Version, VersionTarget


class VersionOut(ApiModel):
    """A deployed version: an unpacked bundle that can be live or attached to a preview."""

    id: uuid.UUID = Field(description="Internal ID of the version; you use it to roll back.")
    site_slug: str = Field(description="Slug of the site.", examples=["docs"])
    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    target: VersionTarget = Field(description="`live` for the public URL, `preview` for a preview ref.")
    storage_ref: str = Field(
        description="Internal reference to the unpacked file tree on disk. Deprecated, and gone in API 2.",
        deprecated=True,
    )
    origin: Literal["upload", "action"] = Field(
        description=(
            "`upload` if a member published this version themselves (in the admin interface or with the CLI), "
            "`action` if CI published it from the linked repository."
        )
    )
    created_by_member: uuid.UUID | None = Field(
        default=None, description="Member who deployed, or `null` for a deploy from CI."
    )
    created_by_name: str | None = Field(
        default=None,
        description=(
            "Name of the member who deployed, or their e-mail address if the identity provider sent no "
            "name. `null` for a deploy from CI. Without this field a list only has the "
            "internal ID to show, and that tells a reader nothing."
        ),
        examples=["Sanne Jansen"],
    )
    created_by_repository: str | None = Field(
        default=None,
        description=(
            "Repository from which CI published this version, as host plus `owner/repo`; `null` for a "
            "deploy by a member."
        ),
        examples=["github.com/minbzk/website"],
    )
    created_at: str | None = Field(
        default=None, description="Time of the deploy.", json_schema_extra=_timestamp_schema()
    )
    is_live: bool = Field(description="Whether this version is currently the live version of the site.")


class SiteStorageOut(ApiModel):
    """What a site takes up on the content volume, and how many live versions are kept."""

    used_bytes: int = Field(
        description=(
            "What all versions of this site together take up on the content volume, live and preview, in bytes."
        ),
        examples=[77594624],
    )
    max_bytes: int = Field(
        description=(
            "How much all versions of a site together may take up, in bytes. A deploy that would go "
            "over that gets 413 (`SITE_QUOTA_EXCEEDED`). `0` means no limit."
        ),
        examples=[524288000],
    )
    live_versions_kept: int = Field(
        description=(
            "How many previous live versions of this site are kept in addition to the current one: the site's own "
            "setting, or else the platform default. Older live versions are removed by "
            "the nightly cleanup, row and files. `0` means all versions are kept."
        ),
        examples=[5],
    )
    live_versions_kept_is_default: bool = Field(
        description=(
            "`true` if the site follows the platform default, `false` if a site admin "
            "set a custom number."
        ),
        examples=[True],
    )
    default_live_versions_kept: int = Field(
        description=(
            "The platform default: how many previous live versions a site without a custom number "
            "keeps. `0` means such sites keep all versions."
        ),
        examples=[5],
    )


def _version_json(
    version: Version, group_slug: str, site: Site, *, deployer: Member | None = None
) -> VersionOut:
    return VersionOut(
        id=version.id,
        site_slug=site.slug,
        group_slug=group_slug,
        target=version.target,
        storage_ref=version_ref(version.site_id, version.id),
        origin="upload" if version.member_id is not None else "action",
        created_by_member=version.member_id,
        created_by_name=(deployer.name or deployer.email) if deployer is not None else None,
        created_by_repository=version.ci_repository,
        created_at=iso_utc(version.created_at),
        is_live=version.id == site.live_version_id,
    )


def _ingest_service(request: Request) -> IngestService:
    return IngestService(request.app.state.content_store, request.app.state.settings)


def add_routes(router: APIRouter) -> None:
    # -- Versions and rollback --

    @router.get(
        "/sites/{group_slug}/{site_slug}/versions",
        tags=[TAG_VERSIONS],
        summary="Deploy history of a site",
        response_description="The versions of the site, newest first.",
        description=(
            "All versions of this site, newest first, with their target (`live` or `preview`) and origin "
            "(`upload` by a member or `action` by CI). Exactly one version has `isLive`, unless "
            "nothing is live yet.\n\n"
            "**Who can call this:** effective site role `reader` or higher."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def versions(group_slug: str, site_slug: str, member: ActiveMember, db: Db) -> list[VersionOut]:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.READER)
        # The deployer comes along in the same query: a list that only carried
        # the member id would leave the interface with nothing to show but a
        # UUID.
        rows = await db.execute(
            select(Version, Member)
            .outerjoin(Member, Member.id == Version.member_id)
            .where(Version.site_id == site.id)
            .order_by(Version.created_at.desc(), Version.id)
        )
        return [
            _version_json(version, group_slug, site, deployer=deployer) for version, deployer in rows
        ]

    @router.get(
        "/sites/{group_slug}/{site_slug}/storage",
        tags=[TAG_VERSIONS],
        summary="Storage and retention rule of a site",
        response_description="The usage, the limit and the number of live versions kept.",
        description=(
            "How much space the versions of this site currently take up, how much they may take up together, "
            "and how many previous live versions the nightly cleanup leaves in place. The current live version "
            "always stays, even after rolling back to an older version. The limit applies to the whole "
            "platform; the number of versions kept is the platform default, unless a "
            "site admin set a custom number for this site.\n\n"
            "**Who can call this:** effective site role `reader` or higher."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def site_storage(
        request: Request, group_slug: str, site_slug: str, member: ActiveMember, db: Db
    ) -> SiteStorageOut:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.READER)
        settings = request.app.state.settings
        used = await asyncio.to_thread(request.app.state.content_store.site_bytes, site.id)
        own = site.live_versions_kept
        return SiteStorageOut(
            used_bytes=used,
            max_bytes=settings.site_max_bytes,
            live_versions_kept=settings.live_versions_kept if own is None else own,
            live_versions_kept_is_default=own is None,
            default_live_versions_kept=settings.live_versions_kept,
        )

    @router.post(
        "/sites/{group_slug}/{site_slug}/versions/{version_id}/_set-live",
        tags=[TAG_VERSIONS],
        summary="Roll back to an earlier version",
        response_description="The site with the new live version.",
        description=(
            "Puts an existing version (back) on the public URL. Nothing is uploaded again: the "
            "unpacked files of that version are already there. The switch applies immediately.\n\n"
            "Only versions with target `live` can be live; a preview version returns 422.\n\n"
            "**Who can call this:** effective site role `editor` or higher, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            {
                404: (
                    "Unknown group (`UNKNOWN_GROUP`), unknown site (`UNKNOWN_SITE`), or the version "
                    "does not exist or belongs to another site (`UNKNOWN_VERSION`, `VERSION_OTHER_SITE`)."
                )
            },
            {422: "This version cannot go live, for example because it is a preview version."},
        ),
    )
    async def set_version_live(
        request: Request,
        group_slug: str,
        site_slug: str,
        version_id: uuid.UUID,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.EDITOR)
        # Close the read transaction: rollback_to opens `db.begin()` itself.
        await db.commit()
        try:
            await _ingest_service(request).rollback_to(db, site, version_id)
        except IngestError as error:
            if error.reason in ("UNKNOWN_VERSION", "VERSION_OTHER_SITE"):
                raise ApiError(404, "UNKNOWN_VERSION.site") from None
            raise ApiError(422, error.message.key, params=error.message.params) from None
        refreshed = await _site_or_404(db, group, site_slug)
        await _audit(
            request,
            member,
            "version_set_live",
            {"group": group_slug, "site": site_slug, "version_id": str(version_id)},
        )
        return await _one_site_json(db, group, refreshed)

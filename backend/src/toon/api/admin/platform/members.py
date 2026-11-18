"""The platform members, their status and platform role, and the content volume."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from toon.api.admin.contract import _ERROR_ADMIN, _ERROR_CSRF, _errors
from toon.api.admin.dependencies import Csrf, Db, PlatformAdmin
from toon.api.admin.lookup import _audit, _member_or_404
from toon.api.admin.platform.queries import _refuse_taking_platform_rights
from toon.api.admin.platform.schemas import PlatformRoleUpdate, VolumeOut
from toon.api.admin.schemas import MemberOut
from toon.api.admin.serialization import _member_json, _member_out
from toon.api.docs import TAG_PLATFORM
from toon.api.errors import ApiError
from toon.cli import service as cli
from toon.models.identity import Member, MemberStatus, PlatformRole


def add_routes(router: APIRouter) -> None:
    # -- Platform members --

    @router.get(
        "/platform/members",
        tags=[TAG_PLATFORM],
        summary="All platform members",
        response_description="All platform members, sorted by e-mail address.",
        description=(
            "Everyone who has ever signed in to the admin interface, sorted by e-mail address, with their "
            "role and status. This is also where you see who has been denied access.\n\n"
            "**Who can call this:** only a platform administrator."
        ),
        responses=_errors(_ERROR_ADMIN),
    )
    async def platform_members(request: Request, member: PlatformAdmin, db: Db) -> list[MemberOut]:
        bootstrap_sub = request.app.state.settings.bootstrap_admin_sub
        members = list(await db.scalars(select(Member).order_by(Member.email)))
        used_at = await cli.last_use_per_member(db, [row.id for row in members])
        return [_member_json(row, bootstrap_sub=bootstrap_sub, cli_used_at=used_at.get(row.id)) for row in members]

    # -- Content volume --

    @router.get(
        "/platform/storage",
        tags=[TAG_PLATFORM],
        summary="Disk usage of the content volume",
        response_description="Total, used and free space on the content volume, with the reserve.",
        description=(
            "The whole content volume at a glance: size, used, free and the reserve below which a deploy "
            "is refused. It lists no site or group: the platform administrator manages people and groups "
            "and does not look into the sites.\n\n"
            "**Who can call this:** only a platform administrator."
        ),
        responses=_errors(
            _ERROR_ADMIN,
            {
                503: (
                    "The volume cannot be measured, for example because the content root is missing "
                    "(`VOLUME_UNMEASURABLE`)."
                )
            },
        ),
    )
    async def platform_storage(request: Request, member: PlatformAdmin) -> VolumeOut:
        settings = request.app.state.settings
        try:
            usage = await asyncio.to_thread(shutil.disk_usage, request.app.state.content_store.root)
        except OSError as error:
            raise ApiError(503, "VOLUME_UNMEASURABLE") from error
        return VolumeOut(
            total_bytes=usage.total,
            used_bytes=usage.used,
            free_bytes=usage.free,
            reserve_bytes=settings.storage_min_free_bytes,
            max_deploy_bytes=settings.ingest_max_total,
        )

    async def _set_member_status(
        request: Request, member_id: uuid.UUID, status: MemberStatus, action: str, member: Member, db: AsyncSession
    ) -> MemberOut:
        target = await _member_or_404(db, member_id)
        if status != MemberStatus.ACTIVE:
            await _refuse_taking_platform_rights(request, db, target, member, "deactivate")
        target.status = status
        refs: dict[str, Any] = {"member_id": str(member_id)}
        if status == MemberStatus.DEACTIVATED:
            # Gone, not suspended: reactivating the member must not bring a
            # CLI login back that was made before the deactivation.
            refs["cli_sessions_revoked"] = await cli.revoke_all_of(db, target.id)
        await db.commit()
        await _audit(request, member, action, refs)
        return await _member_out(db, target, request.app.state.settings.bootstrap_admin_sub)

    @router.post(
        "/platform/members/{member_id}/_activate",
        tags=[TAG_PLATFORM],
        summary="Reactivate a platform member",
        response_description="The member with their new status.",
        description=(
            "Sets the status of a member to `active`, so they may use the admin API again. For a "
            "member whose access has been revoked (status `deactivated`).\n\n"
            "**Who can call this:** only a platform administrator, with a valid CSRF header."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_ADMIN, {404: "Unknown member (`UNKNOWN_MEMBER`)."}),
    )
    async def activate_platform_member(
        request: Request, member_id: uuid.UUID, _csrf: Csrf, member: PlatformAdmin, db: Db
    ) -> MemberOut:
        return await _set_member_status(request, member_id, MemberStatus.ACTIVE, "member_activate", member, db)

    @router.post(
        "/platform/members/{member_id}/_deactivate",
        tags=[TAG_PLATFORM],
        summary="Deactivate a platform member",
        response_description="The member with their new status.",
        description=(
            "Sets the status of a member to `deactivated`. Every subsequent API request from that member gets "
            "403, even with a session that is still valid. Group memberships remain, so activating brings "
            "the member back as they were. All CLI sessions (`toon login`) of the member are revoked, "
            "though: after reactivation they have to run `toon login` again.\n\n"
            "**Who can call this:** only a platform administrator, with a valid CSRF header."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_ADMIN, {404: "Unknown member (`UNKNOWN_MEMBER`)."}),
    )
    async def deactivate_platform_member(
        request: Request, member_id: uuid.UUID, _csrf: Csrf, member: PlatformAdmin, db: Db
    ) -> MemberOut:
        return await _set_member_status(
            request, member_id, MemberStatus.DEACTIVATED, "member_deactivate", member, db
        )

    @router.put(
        "/platform/members/{member_id}/platform-role",
        tags=[TAG_PLATFORM],
        summary="Change the platform role of a member",
        response_description="The member with their new platform role.",
        description=(
            "Makes a member platform administrator or removes that role. A platform administrator manages "
            "people and groups: activating and deactivating members, appointing administrators, and reading "
            "the member list. They do not manage sites; for that they give themselves a group role.\n\n"
            "Three things are not possible, all because they would make the platform unmanageable: taking "
            "away your own role, demoting the last active administrator, and changing the bootstrap "
            "account.\n\n"
            "**Who can call this:** only a platform administrator, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_ADMIN,
            {404: "Unknown member (`UNKNOWN_MEMBER`)."},
            {
                409: (
                    "Taking away your own role (`SELF_NOT_ALLOWED`), the last active administrator "
                    "(`LAST_PLATFORM_ADMIN`), or the bootstrap account (`BOOTSTRAP_MEMBER`)."
                )
            },
        ),
    )
    async def set_platform_role(
        request: Request,
        member_id: uuid.UUID,
        body: PlatformRoleUpdate,
        _csrf: Csrf,
        member: PlatformAdmin,
        db: Db,
    ) -> MemberOut:
        target = await _member_or_404(db, member_id)
        if body.platform_role != PlatformRole.ADMIN:
            await _refuse_taking_platform_rights(request, db, target, member, "demote")
        target.platform_role = body.platform_role
        await db.commit()
        await _audit(
            request, member, "member_platform_role", {"member_id": str(member_id), "role": body.platform_role.value}
        )
        return await _member_out(db, target, request.app.state.settings.bootstrap_admin_sub)

"""Who is in a group, and with which role."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from toon.api.admin.common import MEMBER_IDENTIFIER_HINT, ROLE_HINT, _validate_text
from toon.api.admin.contract import (
    _ERROR_CSRF,
    _ERROR_GROUP,
    _ERROR_GROUP_ROLE,
    _ERROR_IDENTIFIER_AMBIGUOUS,
    _ERROR_SEARCH,
    _deleted,
    _errors,
)
from toon.api.admin.dependencies import ActiveMember, Csrf, Db, SearchTerm
from toon.api.admin.lookup import (
    _audit,
    _commit_or_409,
    _find_member_or_409,
    _group_with_role,
    _member_or_404,
    _search_members,
)
from toon.api.admin.schemas import GroupMemberOut, IdentifierBody, MemberSearchOut
from toon.api.admin.serialization import _group_member_json, _group_members_json, _group_site_roles
from toon.api.docs import TAG_GROUP_MEMBERS
from toon.api.errors import ApiError
from toon.api.schema import SessionBody
from toon.constants import Role
from toon.models.identity import Group, GroupMember, Member, SiteMember
from toon.models.publication import Site

# PostgreSQL code for a PL/pgSQL `RAISE EXCEPTION` without a code of its own.
_SQLSTATE_RAISE_EXCEPTION = "P0001"


SiteRolesOnRemoval = Annotated[
    Literal["keep", "remove"],
    Query(
        alias="siteRoles",
        description=(
            "What happens to the direct site roles that this member has on sites in this group. "
            "`keep` leaves them in place, so they keep access to those sites; `remove` removes them in "
            "the same action. Omitted means `keep`: removing more than was asked is a "
            "deliberate choice.\n\n"
            "Only sites in this group. A site role in another group stays out of view and "
            "untouched."
        ),
        examples=["remove"],
    ),
]


class GroupMemberAdd(IdentifierBody):
    """A person who is added to the group, with the role they get there."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"identifier": "lid@example.nl"},
                {"identifier": "lid@example.nl", "role": "editor"},
            ]
        }
    )

    identifier: str = Field(description=MEMBER_IDENTIFIER_HINT, examples=["lid@example.nl"])
    role: Role = Field(
        default=Role.READER,
        description=f"Role this member gets in the group: {ROLE_HINT} Omitted means `reader`.",
        examples=["reader"],
    )


class GroupRoleUpdate(SessionBody):
    """The new role of a member within a group."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"role": "editor"}]})

    role: Role = Field(description=f"Role this member gets in the group: {ROLE_HINT}", examples=["editor"])


async def _is_last_group_member(db: AsyncSession, group_id: uuid.UUID, member_id: uuid.UUID) -> bool:
    """True when this member is the only one in the group. A group that empties
    out is unmanageable: adding someone requires membership yourself."""
    members = list(await db.scalars(select(GroupMember.member_id).where(GroupMember.group_id == group_id)))
    return members == [member_id]


@asynccontextmanager
async def _group_keeps_an_admin(db: AsyncSession) -> AsyncGenerator[None]:
    """Turns the trigger ck_groups_keep_one_admin into a 409 instead of a 500.

    The trigger (migration 0001) raises a bare PL/pgSQL exception, so the
    sqlstate is all there is to recognise it by; nothing else these routes
    touch can raise that code. It is a constraint trigger INITIALLY IMMEDIATE
    and therefore fires at the end of the statement, not at commit, so both
    have to happen inside this block.
    """
    try:
        yield
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != _SQLSTATE_RAISE_EXCEPTION:
            raise
        await db.rollback()
        raise ApiError(409, "LAST_GROUP_ADMIN") from None


async def _remove_site_roles_in_group(db: AsyncSession, group: Group, target: Member) -> list[str]:
    """Take away every site role this member holds on a site in this group, and
    report the slugs of the sites it really came off.

    Part of the caller's transaction and committed by the caller, so the group
    role and the site roles go together or not at all.

    `Site.group_id == group.id` is the boundary: a site role in another group is
    neither read nor written here.
    """
    in_group = dict(
        (
            await db.execute(
                select(SiteMember.site_id, Site.slug)
                .join(Site, Site.id == SiteMember.site_id)
                .where(Site.group_id == group.id, SiteMember.member_id == target.id)
            )
        ).all()
    )
    if not in_group:
        return []
    gone = await db.execute(
        delete(SiteMember)
        .where(SiteMember.member_id == target.id, SiteMember.site_id.in_(in_group))
        .returning(SiteMember.site_id)
    )
    # RETURNING rather than the list read above: a role somebody else took away
    # in the meantime is not ours to write an audit row about.
    return sorted(in_group[site_id] for site_id in gone.scalars())


async def _one_group_member(
    db: AsyncSession, group: Group, target: Member, role: Role
) -> GroupMemberOut:
    """The row for one member, built the same way the listing builds it, so an
    answer after a change can never disagree with the list."""
    site_roles = await _group_site_roles(db, group, target.id)
    return _group_member_json(group.slug, target, role, site_roles.get(target.id, []))


def add_routes(router: APIRouter) -> None:
    # -- Group members --

    @router.get(
        "/groups/{group_slug}/members",
        tags=[TAG_GROUP_MEMBERS],
        summary="Members of a group",
        response_description="The members of the group, sorted by e-mail address.",
        description=(
            "Who may manage the sites of this group, sorted by e-mail address, each with their role.\n\n"
            "**Who can call this:** group role `reader` or higher, or a platform administrator."
        ),
        responses=_errors(_ERROR_GROUP_ROLE, _ERROR_GROUP),
    )
    async def group_members(group_slug: str, member: ActiveMember, db: Db) -> list[GroupMemberOut]:
        group = await _group_with_role(db, member, group_slug, Role.READER, platform_admin=True)
        return await _group_members_json(db, group)

    @router.get(
        "/groups/{group_slug}/members/search",
        tags=[TAG_GROUP_MEMBERS],
        summary="Find someone to add to the group",
        response_description=(
            "At most ten active platform members, sorted by name and then by e-mail address."
        ),
        description=(
            "Searches the platform members by name or e-mail address, so you can add someone without "
            "knowing their address by heart. The `identifier` of a hit is exactly what "
            "`POST /groups/{group_slug}/members` expects.\n\n"
            "Only active members are returned: you do not add someone who has been "
            "deactivated. The answer is a shortlist of at most ten names, not a dump of the "
            "whole organisation; someone who is already in the group is included, with `alreadyMember` set "
            "to `true`.\n\n"
            "Only those who may add members may search, and Toon does not create an account here "
            "either: someone only appears once they have signed in to the admin interface themselves.\n\n"
            "**Who can call this:** group role `admin`, or a platform administrator."
        ),
        responses=_errors(_ERROR_GROUP_ROLE, _ERROR_GROUP, _ERROR_SEARCH),
    )
    async def search_group_members(
        group_slug: str, q: SearchTerm, member: ActiveMember, db: Db
    ) -> list[MemberSearchOut]:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN, platform_admin=True)
        already = set(
            await db.scalars(select(GroupMember.member_id).where(GroupMember.group_id == group.id))
        )
        return await _search_members(db, q, already)

    @router.post(
        "/groups/{group_slug}/members",
        status_code=201,
        tags=[TAG_GROUP_MEMBERS],
        summary="Add a member to a group",
        response_description="The added group member.",
        description=(
            "Adds an existing platform member to this group, looked up by e-mail address or SSO subject. The "
            "member must already have signed in to the admin interface once themselves; Toon does not "
            "create an account here.\n\n"
            "Without `role` the member becomes `reader`: new members start with read-only access, and you "
            "give the role they need deliberately.\n\n"
            "**Who can call this:** group role `admin`, or a platform administrator, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_GROUP_ROLE,
            _ERROR_GROUP,
            _ERROR_IDENTIFIER_AMBIGUOUS,
            {404: "There is no member with this identifier; they must sign in themselves first (`UNKNOWN_MEMBER`)."},
            {409: "This member is already in the group (`ALREADY_GROUP_MEMBER`)."},
            {422: "The identifier is empty (`FIELD_EMPTY`), or `role` is not an existing role."},
        ),
    )
    async def add_group_member(
        request: Request, group_slug: str, body: GroupMemberAdd, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> GroupMemberOut:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN, platform_admin=True)
        target = await _find_member_or_409(db, _validate_text(body.identifier, "identifier"))
        if target is None:
            raise ApiError(404, "UNKNOWN_MEMBER.must_sign_in")
        db.add(GroupMember(group_id=group.id, member_id=target.id, role=body.role))
        await _commit_or_409(db, "ALREADY_GROUP_MEMBER")
        await _audit(
            request,
            member,
            "group_member_add",
            {"group": group_slug, "member_id": str(target.id), "role": body.role.value},
        )
        return await _one_group_member(db, group, target, body.role)

    @router.delete(
        "/groups/{group_slug}/members/{member_id}",
        status_code=204,
        tags=[TAG_GROUP_MEMBERS],
        summary="Remove a member from a group",
        description=(
            "Removes someone from the group. The member's platform account remains, as do any other group "
            "memberships. The path holds `memberId` from the member list, not the e-mail address: "
            "an address in a URL ends up in the log lines of every proxy in between.\n\n"
            "A direct site role on an individual site is independent of the group and by default "
            "stays in place. With `siteRoles=remove` you remove those roles in the same action, but only on "
            "sites in this group; whatever this member has elsewhere is left untouched. Everything happens in "
            "one transaction, so it is all gone or nothing changes. Each removed site role produces the same "
            "audit row as removing it from the site screen (`site_member_remove`).\n\n"
            "The last member of a group cannot be removed: a group without members can no longer be managed, "
            "because only someone who is in it may add someone. For the same reason the last "
            "`admin` cannot be removed.\n\n"
            "**Who can call this:** group role `admin`, or a platform administrator, with a valid CSRF header. "
            "Whoever may manage the group may already remove any site role in it at the site itself."
        ),
        responses=_deleted("The member is no longer in the group.")
        | _errors(
            _ERROR_CSRF,
            _ERROR_GROUP_ROLE,
            _ERROR_GROUP,
            {
                409: (
                    "This is the last member of the group (`LAST_GROUP_MEMBER`), or its last admin "
                    "(`LAST_GROUP_ADMIN`)."
                ),
                404: (
                    "There is no member with this ID (`UNKNOWN_MEMBER`), or that member is not in this group "
                    "(`NOT_GROUP_MEMBER`)."
                )
            },
        ),
    )
    async def remove_group_member(
        request: Request,
        group_slug: str,
        member_id: uuid.UUID,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
        site_roles: SiteRolesOnRemoval = "keep",
    ) -> Response:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN, platform_admin=True)
        target = await _member_or_404(db, member_id)
        # Read before anything is deleted: the two deletes share one
        # transaction, so a rowcount afterwards can no longer be the thing that
        # decides whether this member was in the group at all.
        membership = await db.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group.id, GroupMember.member_id == target.id
            )
        )
        if membership is None:
            raise ApiError(404, "NOT_GROUP_MEMBER")
        # A group without members can no longer be managed: there is no route to
        # add someone who is not a member themselves. The database trigger
        # ck_groups_keep_one_admin guards the neighbouring case, a group left
        # without an admin.
        if await _is_last_group_member(db, group.id, target.id):
            raise ApiError(409, "LAST_GROUP_MEMBER")
        removed_sites: list[str] = []
        async with _group_keeps_an_admin(db):
            if site_roles == "remove":
                removed_sites = await _remove_site_roles_in_group(db, group, target)
            await db.execute(
                delete(GroupMember).where(GroupMember.group_id == group.id, GroupMember.member_id == target.id)
            )
            await db.commit()
        await _audit(
            request, member, "group_member_remove", {"group": group_slug, "member_id": str(target.id)}
        )
        for site_slug in removed_sites:
            await _audit(
                request,
                member,
                "site_member_remove",
                {"group": group_slug, "site": site_slug, "member_id": str(target.id)},
            )
        return Response(status_code=204)

    @router.put(
        "/groups/{group_slug}/members/{member_id}/role",
        tags=[TAG_GROUP_MEMBERS],
        summary="Change the group role of a member",
        response_description="The group member with their new role.",
        description=(
            "Gives a member of this group a different role. The role determines what they may do in the whole "
            f"group: {ROLE_HINT}\n\n"
            "This never changes a site role: you set that per site, and it only widens what someone may "
            "do on that one site.\n\n"
            "The last `admin` of a group cannot be demoted; a group without an admin can no longer be "
            "managed. Demoting yourself is allowed: as long as there is another admin, they can restore "
            "you.\n\n"
            "The path holds `memberId` from the member list, not the e-mail address: an address in a URL "
            "ends up in the log lines of every proxy in between.\n\n"
            "**Who can call this:** group role `admin`, or a platform administrator, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_GROUP_ROLE,
            _ERROR_GROUP,
            {
                404: (
                    "There is no member with this ID (`UNKNOWN_MEMBER`), or that member is not in this group "
                    "(`NOT_GROUP_MEMBER`)."
                )
            },
            {409: "This is the last admin of the group (`LAST_GROUP_ADMIN`)."},
        ),
    )
    async def set_group_member_role(
        request: Request,
        group_slug: str,
        member_id: uuid.UUID,
        body: GroupRoleUpdate,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> GroupMemberOut:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN, platform_admin=True)
        target = await _member_or_404(db, member_id)
        membership = await db.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group.id, GroupMember.member_id == target.id
            )
        )
        if membership is None:
            raise ApiError(404, "NOT_GROUP_MEMBER")
        membership.role = body.role
        async with _group_keeps_an_admin(db):
            await db.commit()
        await _audit(
            request,
            member,
            "group_member_role",
            {"group": group_slug, "member_id": str(target.id), "role": body.role.value},
        )
        return await _one_group_member(db, group, target, body.role)

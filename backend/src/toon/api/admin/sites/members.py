"""Direct roles on a site, besides the group roles."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from pydantic import ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from toon.access import roles
from toon.api.admin.common import MEMBER_IDENTIFIER_HINT, ROLE_HINT, SITE_ROLE_HINT, _validate_text
from toon.api.admin.contract import (
    _ERROR_CSRF,
    _ERROR_IDENTIFIER_AMBIGUOUS,
    _ERROR_SEARCH,
    _ERROR_SITE,
    _ERROR_SITE_ROLE,
    _deleted,
    _errors,
)
from toon.api.admin.dependencies import ActiveMember, Csrf, Db, SearchTerm
from toon.api.admin.lookup import (
    _audit,
    _commit_or_409,
    _find_member_or_409,
    _member_or_404,
    _search_members,
    _site_with_role,
    _write_one_or_404,
)
from toon.api.admin.schemas import IdentifierBody, MemberSearchOut
from toon.api.docs import TAG_SITE_MEMBERS
from toon.api.errors import ApiError
from toon.api.schema import ApiModel, SessionBody
from toon.constants import ROLE_RANK, Role
from toon.models.identity import Group, GroupMember, Member, SiteMember
from toon.models.publication import Site


class SiteMemberAdd(IdentifierBody):
    """A person who gets a role on this one site, in addition to what a group role already gives."""

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
        description=(
            f"Role this member gets on this site: {SITE_ROLE_HINT} Omitted means `reader`. The role "
            "only widens; someone with a higher group role keeps that."
        ),
        examples=["reader"],
    )


class SiteRoleUpdate(SessionBody):
    """The new site role of a member."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"role": "editor"}]})

    role: Role = Field(
        description=(
            f"Role this member gets on this site: {SITE_ROLE_HINT} A role lower than the group role "
            "changes nothing: the higher of the two applies."
        ),
        examples=["editor"],
    )


class SiteMemberOut(ApiModel):
    """A row of the member list of a site: everyone who can reach this site."""

    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    site_slug: str = Field(description="Slug of the site.", examples=["docs"])
    member_id: str = Field(
        description="ID of the platform member; you use it to remove their site role.",
        examples=["3f2a1c6e-9b4d-4f2a-8c1e-7d5b2a9f4c31"],
    )
    identifier: str = Field(
        description="What you use to set the site role of this member: the e-mail address.",
        examples=["lid@example.nl"],
    )
    name: str = Field(description="Display name from the SSO profile; empty if missing.")
    email: str = Field(description="E-mail address from the SSO profile.", examples=["lid@example.nl"])
    group_role: Role | None = Field(
        default=None,
        description=(
            f"Role in the group of this site, or `null` if this member is not a group member: {ROLE_HINT} "
            "You change this role on the group, not here."
        ),
        examples=["reader"],
    )
    site_role: Role | None = Field(
        default=None,
        description=(
            "Role that applies only on this site, or `null` if this member has none. This is the only "
            "field that the site routes change."
        ),
        examples=["editor"],
    )
    effective_role: Role = Field(
        description="What this member is actually allowed to do here: the higher of `groupRole` and `siteRole`.",
        examples=["editor"],
    )


def _site_member_json(
    group_slug: str,
    site_slug: str,
    member: Member,
    group_role: Role | None,
    site_role: Role | None,
) -> SiteMemberOut:
    """One row of a site member list.

    `group_role` and `site_role` are never both None: a row exists only for
    someone who reaches this site through at least one of the two.
    """
    return SiteMemberOut(
        group_slug=group_slug,
        site_slug=site_slug,
        member_id=str(member.id),
        identifier=member.email,
        name=member.name or "",
        email=member.email,
        group_role=group_role,
        site_role=site_role,
        effective_role=roles.widest(group_role, site_role),
    )


async def _one_site_member(
    db: AsyncSession, group: Group, site: Site, target: Member
) -> SiteMemberOut:
    """The row for one member, built the same way the listing builds it, so an
    answer after a change can never disagree with the list."""
    return _site_member_json(
        group.slug,
        site.slug,
        target,
        await roles.group_role(db, group.id, target.id),
        await roles.site_role(db, site.id, target.id),
    )


async def _site_members_json(db: AsyncSession, group: Group, site: Site) -> list[SiteMemberOut]:
    """Everyone who can reach this site, not just the rows in `site_members`.

    A list that showed only the site roles would leave out the group members
    who reach this site through the group, and read as if far fewer people had
    access than really do.

    The order is widest effective role first, alphabetically by e-mail within
    a role: the list gets opened to see who decides here, and inside a band a
    name stays findable.
    """
    group_roles = dict(
        (
            await db.execute(
                select(GroupMember.member_id, GroupMember.role).where(GroupMember.group_id == group.id)
            )
        ).all()
    )
    site_roles = dict(
        (
            await db.execute(
                select(SiteMember.member_id, SiteMember.role).where(SiteMember.site_id == site.id)
            )
        ).all()
    )
    identifiers = set(group_roles) | set(site_roles)
    # The listing route only reaches here through _site_with_role, which
    # already demands the caller be one of these identifiers.
    if not identifiers:  # pragma: no cover - unreachable, see above
        return []
    members = await db.scalars(select(Member).where(Member.id.in_(identifiers)))
    rows = [
        _site_member_json(
            group.slug, site.slug, row, group_roles.get(row.id), site_roles.get(row.id)
        )
        for row in members
    ]
    rows.sort(key=lambda row: (-ROLE_RANK[row.effective_role], row.email))
    return rows


def add_routes(router: APIRouter) -> None:
    # -- Site members ---------------------------------------------------------

    @router.get(
        "/sites/{group_slug}/{site_slug}/members",
        tags=[TAG_SITE_MEMBERS],
        summary="Members of a site",
        response_description="Everyone who can reach this site, each with the role that grants access.",
        description=(
            "Everyone who can reach this site, not only those with a direct role here. Each row shows "
            "the group role, the site role and the resulting role: the higher of the two "
            "wins, because a site role only widens and never takes anything away.\n\n"
            "A list with only the site roles would leave out the group members and read as if far fewer "
            "people can reach the site than is actually the case.\n\n"
            "**Who can call this:** effective site role `reader`."
        ),
        responses=_errors(_ERROR_SITE),
    )
    async def site_members(
        group_slug: str, site_slug: str, member: ActiveMember, db: Db
    ) -> list[SiteMemberOut]:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.READER)
        return await _site_members_json(db, group, site)

    @router.get(
        "/sites/{group_slug}/{site_slug}/members/search",
        tags=[TAG_SITE_MEMBERS],
        summary="Find someone to give a role on this site",
        response_description=(
            "At most ten active platform members, sorted by name and then by e-mail address."
        ),
        description=(
            "Searches the platform members by name or e-mail address, so you can give someone a role on this "
            "site without knowing their address by heart. The `identifier` of a hit is "
            "exactly what `POST /sites/{group_slug}/{site_slug}/members` expects.\n\n"
            "Only active members are returned: you do not add someone who has been "
            "deactivated. The answer is a shortlist of at most ten names, not a dump of the "
            "whole organisation; someone who already has a direct site role here is included, with "
            "`alreadyMember` set to `true`.\n\n"
            "You can simply pick group members here: a site role widens what they may already do "
            "through the group. What that is appears in `groupRole`, so you can see whether a site role "
            "adds anything.\n\n"
            "Only those who may add members may search, and Toon does not create an account here "
            "either: someone only appears once they have signed in to the admin interface themselves.\n\n"
            "**Who can call this:** effective site role `admin`."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE, _ERROR_SEARCH),
    )
    async def search_site_members(
        group_slug: str, site_slug: str, q: SearchTerm, member: ActiveMember, db: Db
    ) -> list[MemberSearchOut]:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        already = set(
            await db.scalars(select(SiteMember.member_id).where(SiteMember.site_id == site.id))
        )
        group_rows = await db.execute(
            select(GroupMember.member_id, GroupMember.role).where(GroupMember.group_id == group.id)
        )
        group_roles = dict(group_rows.all())
        return await _search_members(db, q, already, group_roles)

    @router.post(
        "/sites/{group_slug}/{site_slug}/members",
        status_code=201,
        tags=[TAG_SITE_MEMBERS],
        summary="Give a member a role on this site",
        response_description="The member with their new site role.",
        description=(
            "Gives an existing platform member a role on this one site. That can be a higher role than "
            "their group role; lower has no effect, because the higher of the two applies. Someone "
            "does not need to be a group member: this is how you give an outsider access to exactly this "
            "site.\n\n"
            "A group role is changed on the group, not here.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE,
            _ERROR_IDENTIFIER_AMBIGUOUS,
            {404: "There is no member with this identifier; they must sign in themselves first (`UNKNOWN_MEMBER`)."},
            {409: "This member already has a role on this site (`ALREADY_SITE_MEMBER`)."},
            {422: "The identifier is empty (`FIELD_EMPTY`)."},
        ),
    )
    async def add_site_member(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: SiteMemberAdd,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteMemberOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        target = await _find_member_or_409(db, _validate_text(body.identifier, "identifier"))
        if target is None:
            raise ApiError(404, "UNKNOWN_MEMBER.must_sign_in")
        db.add(SiteMember(site_id=site.id, member_id=target.id, role=body.role))
        await _commit_or_409(db, "ALREADY_SITE_MEMBER")
        await _audit(
            request,
            member,
            "site_member_add",
            {"group": group_slug, "site": site_slug, "member_id": str(target.id), "role": body.role.value},
        )
        return await _one_site_member(db, group, site, target)

    @router.put(
        "/sites/{group_slug}/{site_slug}/members/{member_id}/role",
        tags=[TAG_SITE_MEMBERS],
        summary="Change the site role of a member",
        response_description="The member with their new site role.",
        description=(
            "Gives a member a different role on this one site. Only applies to a direct site role; for someone listed "
            "through their group membership, change the role on the group.\n\n"
            "The path holds `memberId` from the member list, not the e-mail address: an address in a URL "
            "ends up in the log lines of every proxy in between.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE,
            {
                404: (
                    "There is no member with this ID (`UNKNOWN_MEMBER`), or that member has no direct "
                    "site role on this site (`NOT_SITE_MEMBER`)."
                )
            },
        ),
    )
    async def set_site_role(
        request: Request,
        group_slug: str,
        site_slug: str,
        member_id: uuid.UUID,
        body: SiteRoleUpdate,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteMemberOut:
        # The site check first, so a refusal tells an outsider nothing about
        # what exists here.
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        target = await _member_or_404(db, member_id)
        row = await db.scalar(
            select(SiteMember).where(SiteMember.site_id == site.id, SiteMember.member_id == target.id)
        )
        if row is None:
            raise ApiError(404, "NOT_SITE_MEMBER")
        row.role = body.role
        await db.commit()
        await _audit(
            request,
            member,
            "site_member_role",
            {"group": group_slug, "site": site_slug, "member_id": str(target.id), "role": body.role.value},
        )
        return await _one_site_member(db, group, site, target)

    @router.delete(
        "/sites/{group_slug}/{site_slug}/members/{member_id}",
        status_code=204,
        tags=[TAG_SITE_MEMBERS],
        summary="Remove the site role of a member",
        description=(
            "Removes the role that applied only to this site. A group role remains, so whoever can reach "
            "this site through the group still can afterwards.\n\n"
            "The path holds `memberId` from the member list, not the e-mail address: an address in a URL "
            "ends up in the log lines of every proxy in between.\n\n"
            "There is no last-admin protection here as there is for a group: the admins of the "
            "group can always reach this site, so a site without an admin of its own is not unmanageable."
            "\n\n**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_deleted("The member no longer has a direct site role on this site.")
        | _errors(
            _ERROR_CSRF,
            _ERROR_SITE,
            {
                404: (
                    "There is no member with this ID (`UNKNOWN_MEMBER`), or that member has no direct "
                    "site role on this site (`NOT_SITE_MEMBER`)."
                )
            },
        ),
    )
    async def remove_site_member(
        request: Request,
        group_slug: str,
        site_slug: str,
        member_id: uuid.UUID,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> Response:
        # The site check first, so a refusal tells an outsider nothing about
        # what exists here.
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        target = await _member_or_404(db, member_id)
        await _write_one_or_404(
            db,
            delete(SiteMember)
            .where(SiteMember.site_id == site.id, SiteMember.member_id == target.id)
            .returning(SiteMember.member_id),
            "NOT_SITE_MEMBER",
        )
        await _audit(
            request,
            member,
            "site_member_remove",
            {"group": group_slug, "site": site_slug, "member_id": str(target.id)},
        )
        return Response(status_code=204)

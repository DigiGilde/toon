"""A group: creating it, reading it, renaming it, its address, its default access and deleting it."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from toon.api.admin.common import TEXT_MAX_LENGTH, _slug_conflict, _validate_slug, _validate_text
from toon.api.admin.contract import (
    _CREATION_RULE,
    _ERROR_CLI_TOKEN,
    _ERROR_CREATIONS,
    _ERROR_CSRF,
    _ERROR_GROUP,
    _ERROR_GROUP_ROLE,
    _deleted,
    _errors,
)
from toon.api.admin.dependencies import (
    CREATION_MAX_PER_WINDOW,
    CREATION_WINDOW_S,
    ActiveMember,
    Creator,
    Csrf,
    Db,
    _require_creation_budget,
    require_creator,
)
from toon.api.admin.lookup import (
    _access_refs,
    _audit,
    _chosen_access,
    _group_with_role,
    _previous_slugs,
    _require_room_for_an_old_slug,
    _reread_locked,
)
from toon.api.admin.schemas import AccessBody, AccessChoice, GroupMemberOut, GroupOut, SiteOut
from toon.api.admin.serialization import _group_json, _group_members_json, _groups_json, _sites_json
from toon.api.docs import TAG_GROUPS
from toon.api.schema import ApiModel, SessionBody
from toon.constants import MAX_PREVIOUS_SLUGS, RESERVED_SLUGS, SLUG_REDIRECT_DAYS, AccessBase, Role
from toon.models.identity import Group, GroupMember
from toon.models.publication import Site
from toon.models.slugs import GroupSlug

# Taken by every change of a group's slug before it locks the group: two
# groups trading slugs share no row to wait on, and would each wait for the
# other's unique index entry, a deadlock.
_GROUP_SLUG_LOCK = select(func.pg_advisory_xact_lock(func.hashtext("toon-group-slug")))


class GroupCreate(ApiModel):
    """A new group."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"name": "Team Aurora", "slug": "aurora"},
                {"name": "Team Aurora", "slug": "aurora", "defaultAccess": {"base": "sso"}},
            ]
        }
    )

    name: str = Field(description="Display name of the group.", examples=["Team Aurora"])
    slug: str = Field(
        description=(
            "Slug of the group: lowercase letters, digits and hyphens, at most 63 characters, and not "
            "reserved (`robots.txt`, `favicon.ico`, `.well-known`). This becomes the first "
            "path segment of every site URL of the group."
        ),
        examples=["aurora"],
    )
    default_access: AccessChoice | None = Field(
        default=None,
        description=(
            "Optional: the default access the group starts with. Every omitted field, or the whole "
            "object omitted, gets the default: base `site_team`, no secret links, no "
            "invitees."
        ),
    )


class GroupNameBody(SessionBody):
    """The new display name of a group."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"name": "Team Aurora"}]})

    name: str = Field(
        description=(
            f"New display name, 1 to {TEXT_MAX_LENGTH} characters after trimming spaces; no control or "
            "formatting characters."
        ),
        json_schema_extra={"maxLength": TEXT_MAX_LENGTH},
    )


class GroupSlugBody(SessionBody):
    """The new slug of a group."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"slug": "aurora"}]})

    slug: str = Field(
        description=(
            "New slug of the group: lowercase letters, digits and hyphens, at most 63 characters, and not one of "
            "the names the platform reserves ("
            + ", ".join(f"`{name}`" for name in sorted(RESERVED_SLUGS))
            + "). Not the current slug of another group either, nor one that another group gave up and that is "
            "not free again yet."
        ),
        examples=["aurora"],
    )


class GroupDetail(ApiModel):
    """Everything the group page of the SPA needs in one go."""

    group: GroupOut = Field(description="The group itself.")
    sites: list[SiteOut] = Field(description="Sites of the group, sorted by slug.")
    members: list[GroupMemberOut] = Field(description="Members of the group, sorted by e-mail address.")


async def _group_sites(db: AsyncSession, group: Group) -> list[Site]:
    return list(
        await db.scalars(
            select(Site).where(Site.group_id == group.id).order_by(Site.slug)
        )
    )


def add_routes(router: APIRouter) -> None:
    @router.post(
        "/groups",
        status_code=201,
        tags=[TAG_GROUPS],
        summary="Create a group",
        response_description="The created group.",
        description=(
            "Creates a group and makes the creator group admin (`admin`) right away, so they can put sites "
            "in it. The new group starts with base `site_team` and no exceptions, unless "
            "`defaultAccess` asks for something else; that can be changed afterwards. Because the creator "
            "becomes group admin, choosing the default access right away is no more than they may do "
            "afterwards anyway.\n\n"
            "**Who can call this:** any active member, with a valid CSRF header. Creating a group is not a "
            "reserved action: anyone who wants to publish something must be able to make a place for it "
            "themselves. "
            + _CREATION_RULE
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_CLI_TOKEN,
            _ERROR_CREATIONS,
            {
                409: (
                    "Another group has this slug, now or as an old slug that is not free again yet "
                    "(`SLUG_EXISTS`)."
                )
            },
            {
                422: (
                    "The slug is invalid or reserved (`SLUG_INVALID`), or the name is empty "
                    "(`FIELD_EMPTY`)."
                )
            },
        ),
    )
    async def create_group(
        request: Request, body: GroupCreate, creator: Annotated[Creator, Depends(require_creator)], db: Db
    ) -> GroupOut:
        member = creator.member
        slug = _validate_slug(body.slug, reserved_refused=True)
        name = _validate_text(body.name, "name")
        base, keys, invitees = _chosen_access(body.default_access, AccessBase.SITE_TEAM, False, False)
        await _require_creation_budget(request, member)
        group = Group(
            slug=slug,
            name=name,
            default_access_base=base,
            default_access_keys=keys,
            default_access_invitees=invitees,
        )
        db.add(group)
        try:
            await db.flush()
        except IntegrityError as error:
            await db.rollback()
            raise _slug_conflict(error, "group", slug) from None
        # The creator becomes group admin in the same transaction, otherwise
        # they cannot create a site in their own fresh group (403).
        db.add(GroupMember(group_id=group.id, member_id=member.id, role=Role.ADMIN))
        await db.commit()
        await _audit(
            request,
            member,
            "group_create",
            {"group": slug, **_access_refs(base, keys, invitees), **creator.audit_refs()},
        )
        return _group_json(group, [])

    @router.get(
        "/groups/{group_slug}",
        tags=[TAG_GROUPS],
        summary="Group with sites and members",
        response_description="The group with its sites and members.",
        description=(
            "Everything the group page of the SPA needs in one go.\n\n"
            "**Who can call this:** group role `reader` or higher, or a platform administrator."
        ),
        responses=_errors(_ERROR_GROUP_ROLE, _ERROR_GROUP),
    )
    async def group_detail(group_slug: str, member: ActiveMember, db: Db) -> GroupDetail:
        group = await _group_with_role(db, member, group_slug, Role.READER, platform_admin=True)
        sites = await _group_sites(db, group)
        return GroupDetail(
            group=(await _groups_json(db, [group]))[0],
            sites=await _sites_json(db, group, sites),
            members=await _group_members_json(db, group),
        )

    @router.delete(
        "/groups/{group_slug}",
        status_code=204,
        tags=[TAG_GROUPS],
        summary="Delete a group",
        description=(
            "Deletes the group with all its sites, and per site everything `DELETE /sites/{group}/{site}` "
            "also removes: versions, previews, invitees, secret links, the linked repository and the "
            "unpacked files on disk. Irreversible; every URL of the group returns 404 afterwards.\n\n"
            "**Who can call this:** group role `admin`, with a valid CSRF header. A platform administrator without "
            "a group role is not allowed."
        ),
        responses=_deleted("The group and all its sites have been deleted.")
        | _errors(_ERROR_CSRF, _ERROR_GROUP_ROLE, _ERROR_GROUP),
    )
    async def delete_group(
        request: Request, group_slug: str, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN)
        sites = (
            await db.execute(select(Site.id, Site.slug).where(Site.group_id == group.id).order_by(Site.slug))
        ).all()
        await db.delete(group)
        await db.commit()
        store = request.app.state.content_store
        for site in sites:
            await asyncio.to_thread(store.delete_site, site.id)
        site_slugs = [site.slug for site in sites]
        await _audit(request, member, "group_delete", {"group": group_slug, "sites": site_slugs})
        return Response(status_code=204)

    @router.put(
        "/groups/{group_slug}/name",
        tags=[TAG_GROUPS],
        summary="Change the name of a group",
        response_description="The group with its new name.",
        description=(
            "Sets the display name of the group. The slug, and with it every URL of the group, does not "
            "change. The audit log keeps the previous name, in full. Sending the name the group already has "
            "(after trimming spaces) changes nothing and writes no audit row.\n\n"
            "**Who can call this:** group role `admin`, with a valid CSRF header. A platform administrator "
            "without a group role is not allowed."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_GROUP_ROLE,
            _ERROR_GROUP,
            {
                422: (
                    "The name is empty (`FIELD_EMPTY`), contains control or formatting characters "
                    f"(`FIELD_CONTROL_CHARACTERS`) or is longer than {TEXT_MAX_LENGTH} characters "
                    "(`FIELD_TOO_LONG`)."
                )
            },
        ),
    )
    async def set_group_name(
        request: Request, group_slug: str, body: GroupNameBody, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> GroupOut:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN)
        name = _validate_text(body.name, "name", max_length=TEXT_MAX_LENGTH)
        # Locked and read again: the audit row must name the value this change
        # replaces, not the one read before a rename that committed meanwhile.
        await _reread_locked(db, group)
        previous = group.name
        if name != previous:
            group.name = name
            await db.commit()
            await _audit(
                request,
                member,
                "group_name",
                {"group": group_slug, "group_id": str(group.id), "previous_name": previous},
            )
        return (await _groups_json(db, [group]))[0]

    @router.put(
        "/groups/{group_slug}/slug",
        tags=[TAG_GROUPS],
        summary="Change the address of a group",
        response_description="The group at its new address.",
        description=(
            "Sets the slug of the group, the first path segment of every URL of its sites: every site, preview "
            "and secret link of the group gets a new address at once. The old slug stays reserved for this group, "
            f"and its old addresses redirect to the new ones through day {SLUG_REDIRECT_DAYS} after the change "
            "(until midnight, Amsterdam time), for visitors who may see the site. After that they answer 404, and "
            "the old slug is free for another group once the nightly cleanup has run. Until then this group can "
            "change back to it. `previousSlugs` lists the old slugs that still redirect.\n\n"
            "Publishing under the old address stops at once: the API knows only the current slug, and from this "
            "change on the repository link of every site in the group requires the site id. Sending the slug the "
            "group already has changes nothing and writes no audit row. If the response gets lost, the old path "
            "answers 404: read the group at the new slug, or send the request again with the new slug in the path, "
            "which then changes nothing.\n\n"
            "**Who can call this:** group role `admin`, with a valid CSRF header. A platform administrator without "
            f"a group role is not allowed. Each change counts towards the limit of {CREATION_MAX_PER_WINDOW} new "
            f"groups, sites and addresses combined per {CREATION_WINDOW_S // 60} minutes, and at most "
            f"{MAX_PREVIOUS_SLUGS} old slugs of a group redirect at the same time."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_GROUP_ROLE,
            _ERROR_GROUP,
            _ERROR_CREATIONS,
            {
                409: (
                    "Another group has this slug, now or as an old slug that is not free again yet "
                    f"(`SLUG_EXISTS`); or this group already has {MAX_PREVIOUS_SLUGS} old slugs that still "
                    "redirect, and the new slug is none of them (`TOO_MANY_PREVIOUS_SLUGS`)."
                )
            },
            {422: "The slug is invalid or reserved (`SLUG_INVALID`)."},
        ),
    )
    async def set_group_slug(
        request: Request, group_slug: str, body: GroupSlugBody, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> GroupOut:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN)
        slug = _validate_slug(body.slug, reserved_refused=True)
        await db.execute(_GROUP_SLUG_LOCK)
        # Locked and read again before anything is decided: the old slugs are
        # counted, and the audit row names what this change replaces, as they
        # stand once a rename that ran meanwhile has committed.
        await _reread_locked(db, group, slug_change=True)
        previous = group.slug
        if slug == previous:
            return (await _groups_json(db, [group]))[0]
        await _require_creation_budget(request, member)
        previous_slugs = await _previous_slugs(db, GroupSlug.group_id, [group.id])
        _require_room_for_an_old_slug(previous_slugs.get(group.id, []), slug)
        group.slug = slug
        try:
            await db.flush()
        except IntegrityError as error:
            await db.rollback()
            raise _slug_conflict(error, "group", slug) from None
        sites = list(await db.scalars(select(Site.slug).where(Site.group_id == group.id).order_by(Site.slug)))
        await db.commit()
        await _audit(
            request,
            member,
            "group_slug",
            {"group": slug, "previous_group": previous, "group_id": str(group.id), "sites": sites},
        )
        return (await _groups_json(db, [group]))[0]

    @router.put(
        "/groups/{group_slug}/default-access",
        tags=[TAG_GROUPS],
        summary="Set the default access of a group",
        response_description="The group with its new default access.",
        description=(
            "Sets the access that new sites in this group start with: the "
            "base and the two exceptions in one go. Existing sites are not affected; "
            "you set those per site.\n\n"
            "**Who can call this:** group role `admin`, with a valid CSRF header. This is policy about content, so "
            "a platform administrator without a group role is not allowed."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_GROUP_ROLE, _ERROR_GROUP),
    )
    async def set_default_access(
        request: Request, group_slug: str, body: AccessBody, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> GroupOut:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN)
        group.default_access_base = body.base
        group.default_access_keys = body.keys
        group.default_access_invitees = body.invitees
        await db.commit()
        await _audit(
            request,
            member,
            "group_default_visibility",
            {
                "group": group_slug,
                "base": str(body.base),
                "keys": body.keys,
                "invitees": body.invitees,
            },
        )
        return (await _groups_json(db, [group]))[0]

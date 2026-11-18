"""The overview: the groups and sites the member can see."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from toon.api.admin.contract import _errors
from toon.api.admin.dependencies import ActiveMember, Db
from toon.api.admin.schemas import GroupOut, SiteOut
from toon.api.admin.serialization import _groups_json, _site_activity, _site_json
from toon.api.docs import TAG_OVERVIEW
from toon.api.schema import ApiModel
from toon.models.identity import Group, GroupMember, Member, SiteMember
from toon.models.publication import Site


class GroupRow(ApiModel):
    """A group with its sites, as the overview shows it."""

    group: GroupOut = Field(description="The group itself.")
    sites: list[SiteOut] = Field(
        description=(
            "The sites of the group that this member may see, sorted by slug. That is all of them, unless "
            "the member only reaches the group through a site role: then only those sites are listed."
        )
    )


class Overview(ApiModel):
    """The home screen of the admin SPA."""

    groups: list[GroupRow] = Field(
        description=(
            "Groups this member may see, sorted by slug: where they have a group role, and where they have "
            "a role on a site. Empty if the member has no role anywhere."
        )
    )


async def _groups_for_member(
    db: AsyncSession, member: Member
) -> tuple[list[Group], dict[uuid.UUID, list[Site]]]:
    """The groups this member sees in the overview, with the sites each one shows.

    A group role shows the whole group. A site role alone shows that group with
    only the sites the member has a role on: a site role grants nothing at
    group level, so the rest of the group stays hidden.
    """
    group_ids = set(
        await db.scalars(select(GroupMember.group_id).where(GroupMember.member_id == member.id))
    )
    own_sites = await db.scalars(
        select(Site)
        .join(SiteMember, SiteMember.site_id == Site.id)
        .where(SiteMember.member_id == member.id)
        .order_by(Site.slug)
    )
    sites_per_group: dict[uuid.UUID, list[Site]] = {}
    for site in own_sites:
        if site.group_id not in group_ids:
            sites_per_group.setdefault(site.group_id, []).append(site)
    groups = list(
        await db.scalars(
            select(Group).where(Group.id.in_(group_ids | set(sites_per_group))).order_by(Group.slug)
        )
    )
    if group_ids:
        for group_id in group_ids:
            sites_per_group[group_id] = []
        whole_groups = await db.scalars(select(Site).where(Site.group_id.in_(group_ids)).order_by(Site.slug))
        for site in whole_groups:
            sites_per_group[site.group_id].append(site)
    return groups, sites_per_group


def add_routes(router: APIRouter) -> None:
    # -- Overview and groups --

    @router.get(
        "/overview",
        tags=[TAG_OVERVIEW],
        summary="All visible groups with their sites",
        response_description="The groups this member is allowed to see, each with its sites.",
        description=(
            "The home screen of the admin SPA: per group the sites with their access, whether anything "
            "is live, when something was last deployed and how many previews are open.\n\n"
            "**Who can call this:** any active member. The member sees the groups in which they have a group role, "
            "each with all its sites, plus the groups "
            "in which they only have a site role: of those only the sites in question appear, because a "
            "site role grants nothing at group level. A platform administrator likewise only sees their own "
            "groups. Anyone without a role anywhere gets an empty list, not a 403."
        ),
        responses=_errors(),
    )
    async def overview(member: ActiveMember, db: Db) -> Overview:
        groups, sites_per_group = await _groups_for_member(db, member)
        groups_json = await _groups_json(db, groups)
        last, counts, open_feedback, previous = await _site_activity(
            db, [site for sites in sites_per_group.values() for site in sites]
        )
        rows = [
            GroupRow(
                group=group_json,
                sites=[
                    _site_json(
                        site,
                        group.slug,
                        last.get(site.id),
                        counts.get(site.id, 0),
                        open_feedback.get(site.id, 0),
                        previous.get(site.id, []),
                    )
                    for site in sites_per_group[group.id]
                ],
            )
            for group, group_json in zip(groups, groups_json, strict=True)
        ]
        return Overview(groups=rows)

"""How rows become the answers that more than one area of the admin API returns."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from toon.api.admin.common import _FEEDBACK_OPEN, _require_aware
from toon.api.admin.lookup import _previous_slugs
from toon.api.admin.schemas import GroupMemberOut, GroupOut, GroupSiteRole, MemberOut, PreviousSlugOut, SiteOut
from toon.api.errors import ApiError
from toon.api.schema import AccessOut, iso_utc
from toon.cli import service as cli
from toon.constants import Role
from toon.models.feedback import FeedbackItem as FeedbackItemRow
from toon.models.identity import Group, GroupMember, Member, SiteMember
from toon.models.publication import Preview, Site, Version
from toon.models.slugs import GroupSlug, SiteSlug


def _member_json(member: Member, *, bootstrap_sub: str = "", cli_used_at: datetime | None = None) -> MemberOut:
    moments = [moment for moment in (member.last_login_at, cli_used_at) if moment is not None]
    return MemberOut(
        id=member.id,
        sso_subject=member.sso_subject,
        email=member.email,
        name=member.name or "",
        platform_role=member.platform_role,
        status=member.status,
        is_bootstrap=bool(bootstrap_sub) and member.sso_subject == bootstrap_sub,
        created_at=iso_utc(member.created_at) or "",
        last_login_at=iso_utc(member.last_login_at),
        last_active_at=iso_utc(max(moments, default=None)),
    )


async def _member_out(db: AsyncSession, member: Member, bootstrap_sub: str = "") -> MemberOut:
    used_at = (await cli.last_use_per_member(db, [member.id])).get(member.id)
    return _member_json(member, bootstrap_sub=bootstrap_sub, cli_used_at=used_at)


def _group_json(group: Group, previous_slugs: list[PreviousSlugOut]) -> GroupOut:
    return GroupOut(
        slug=group.slug,
        name=group.name,
        default_access=AccessOut(
            base=group.default_access_base,
            keys=group.default_access_keys,
            invitees=group.default_access_invitees,
        ),
        previous_slugs=previous_slugs,
    )


def _site_json(
    site: Site,
    group_slug: str,
    last_published_at: datetime | None,
    preview_count: int,
    open_feedback_count: int,
    previous_slugs: list[PreviousSlugOut],
) -> SiteOut:
    return SiteOut(
        id=site.id,
        group_slug=group_slug,
        slug=site.slug,
        previous_slugs=previous_slugs,
        title=site.title,
        access=AccessOut(base=site.access_base, keys=site.access_keys, invitees=site.access_invitees),
        external_sources=site.external_sources,
        sandbox=site.sandbox,
        live_versions_kept=site.live_versions_kept,
        live_version_id=site.live_version_id,
        created_by=str(site.created_by) if site.created_by else "",
        has_live_version=site.live_version_id is not None,
        last_published_at=iso_utc(last_published_at),
        preview_count=preview_count,
        feedback_enabled=site.feedback_enabled,
        feedback_identity=site.feedback_identity,
        feedback_live=site.feedback_live,
        open_feedback_count=open_feedback_count,
    )


def _group_member_json(
    group_slug: str, member: Member, role: Role, site_roles: list[GroupSiteRole]
) -> GroupMemberOut:
    return GroupMemberOut(
        group_slug=group_slug,
        member_id=str(member.id),
        identifier=member.email,
        name=member.name or "",
        email=member.email,
        role=role,
        site_roles=site_roles,
    )


async def _groups_json(db: AsyncSession, groups: list[Group]) -> list[GroupOut]:
    previous = await _previous_slugs(db, GroupSlug.group_id, [group.id for group in groups])
    return [_group_json(group, previous.get(group.id, [])) for group in groups]


async def _site_activity(
    db: AsyncSession, sites: list[Site]
) -> tuple[
    dict[uuid.UUID, datetime], dict[uuid.UUID, int], dict[uuid.UUID, int], dict[uuid.UUID, list[PreviousSlugOut]]
]:
    """Per site, when a version was last deployed, how many previews are open,
    how much feedback is open and which old addresses still redirect."""
    ids = [site.id for site in sites]
    last: dict[uuid.UUID, datetime] = {}
    counts: dict[uuid.UUID, int] = {}
    open_feedback: dict[uuid.UUID, int] = {}
    previous: dict[uuid.UUID, list[PreviousSlugOut]] = {}
    if ids:
        previous = await _previous_slugs(db, SiteSlug.site_id, ids)
        last = dict(
            (
                await db.execute(
                    select(Version.site_id, func.max(Version.created_at))
                    .where(Version.site_id.in_(ids))
                    .group_by(Version.site_id)
                )
            ).all()
        )
        counts = dict(
            (
                await db.execute(
                    select(Preview.site_id, func.count())
                    .where(Preview.site_id.in_(ids))
                    .group_by(Preview.site_id)
                )
            ).all()
        )
        open_feedback = dict(
            (
                await db.execute(
                    select(FeedbackItemRow.site_id, func.count())
                    .where(FeedbackItemRow.site_id.in_(ids), FeedbackItemRow.status.in_(_FEEDBACK_OPEN))
                    .group_by(FeedbackItemRow.site_id)
                )
            ).all()
        )
    return last, counts, open_feedback, previous


async def _sites_json(db: AsyncSession, group: Group, sites: list[Site]) -> list[SiteOut]:
    last, counts, open_feedback, previous = await _site_activity(db, sites)
    return [
        _site_json(
            site,
            group.slug,
            last.get(site.id),
            counts.get(site.id, 0),
            open_feedback.get(site.id, 0),
            previous.get(site.id, []),
        )
        for site in sites
    ]


async def _one_site_json(db: AsyncSession, group: Group, site: Site) -> SiteOut:
    return (await _sites_json(db, group, [site]))[0]


async def _group_site_roles(
    db: AsyncSession, group: Group, member_id: uuid.UUID | None = None
) -> dict[uuid.UUID, list[GroupSiteRole]]:
    """Per member, the sites in this group they hold a role of their own on.

    `Site.group_id == group.id` is the whole point of this query and not an
    optimisation: a site role in another group would say where else in the
    organisation this person works, and an admin of this group has no business
    reading that here.
    """
    query = (
        select(SiteMember.member_id, Site.slug, Site.title, SiteMember.role)
        .join(Site, Site.id == SiteMember.site_id)
        .where(Site.group_id == group.id)
        .order_by(Site.slug)
    )
    if member_id is not None:
        query = query.where(SiteMember.member_id == member_id)
    per_member: dict[uuid.UUID, list[GroupSiteRole]] = {}
    for row_member_id, site_slug, site_title, role in await db.execute(query):
        per_member.setdefault(row_member_id, []).append(
            GroupSiteRole(site_slug=site_slug, site_title=site_title, role=role)
        )
    return per_member


async def _group_members_json(db: AsyncSession, group: Group) -> list[GroupMemberOut]:
    rows = await db.execute(
        select(Member, GroupMember.role)
        .join(GroupMember, GroupMember.member_id == Member.id)
        .where(GroupMember.group_id == group.id)
        .order_by(Member.email)
    )
    site_roles = await _group_site_roles(db, group)
    return [
        _group_member_json(group.slug, row.Member, row.role, site_roles.get(row.Member.id, []))
        for row in rows
    ]


def _page_cursor(moment: datetime, identifier: uuid.UUID) -> str:
    raw = f"{iso_utc(moment)}|{identifier}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _audit_cursor_read(cursor: str) -> tuple[datetime, uuid.UUID]:
    # binascii.Error and UnicodeDecodeError are both ValueError, as are
    # fromisoformat and UUID(), so one except catches every unreadable cursor.
    try:
        raw = base64.urlsafe_b64decode(cursor).decode("utf-8")
        timestamp, _, identifier = raw.partition("|")
        moment = _require_aware(datetime.fromisoformat(timestamp))
        return moment, uuid.UUID(identifier)
    except ValueError as error:
        raise ApiError(422, "CURSOR_INVALID") from error


def _content_base(request: Request) -> str:
    """Origin the SPA builds content URLs on (public URL, secret and preview
    links). TOON_CONTENT_BASE_URL is required everywhere, so there is one host
    to name here and no fallback to guess at."""
    return request.app.state.settings.content_base_url.rstrip("/")

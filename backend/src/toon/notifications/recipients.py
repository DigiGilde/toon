"""Who gets an email about new feedback on a site, decided when it is sent.

A member gets one when, at that moment, all of this holds:

- they are active and have an address the IdP verified (`members.email` is
  only ever filled from a verified claim, auth/members.py);
- they may read the site's feedback: an effective role on the site, which is
  a role in its group or on the site itself (docs/design.md §3.6);
- their own choice says yes. The site level decides where it is set, then
  the group level, then the profile. Nothing set anywhere is no.

An item a member sent with their own account is left out of their email.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import ColumnElement, and_, exists, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from toon.config import normalise_operator_email
from toon.models.identity import GroupMember, Member, MemberStatus, SiteMember
from toon.models.notifications import GroupFeedbackEmail, SiteFeedbackEmail
from toon.models.publication import Site


class Source(StrEnum):
    """The level whose choice applies."""

    SITE = "site"
    GROUP = "group"
    PROFILE = "profile"
    DEFAULT = "default"


@dataclass(frozen=True)
class Resolution:
    enabled: bool
    source: Source


def resolve(site: bool | None, group: bool | None, profile: bool | None) -> Resolution:
    """The narrowest level that is set decides; nothing set is off."""
    for choice, source in ((site, Source.SITE), (group, Source.GROUP), (profile, Source.PROFILE)):
        if choice is not None:
            return Resolution(enabled=choice, source=source)
    return Resolution(enabled=False, source=Source.DEFAULT)


@dataclass(frozen=True)
class Recipient:
    member_id: uuid.UUID
    email: str
    sso_subject: str
    language: str | None


def _reads_site(site: Site) -> ColumnElement[bool]:
    in_group = exists().where(GroupMember.group_id == site.group_id, GroupMember.member_id == Member.id)
    on_site = exists().where(SiteMember.site_id == site.id, SiteMember.member_id == Member.id)
    return in_group | on_site


def _recipients_query(site: Site):
    site_choice = SiteFeedbackEmail.enabled
    group_choice = GroupFeedbackEmail.enabled
    return (
        select(Member.id, Member.email, Member.sso_subject, Member.language)
        .outerjoin(
            SiteFeedbackEmail, and_(SiteFeedbackEmail.site_id == site.id, SiteFeedbackEmail.member_id == Member.id)
        )
        .outerjoin(
            GroupFeedbackEmail,
            and_(GroupFeedbackEmail.group_id == site.group_id, GroupFeedbackEmail.member_id == Member.id),
        )
        .where(
            Member.status == MemberStatus.ACTIVE,
            Member.email != "",
            _reads_site(site),
            func.coalesce(site_choice, group_choice, Member.feedback_email, false()),
        )
        .order_by(Member.id)
    )


def _plain(email: str) -> bool:
    """An address that can go in a To header as it is."""
    try:
        return normalise_operator_email(email) == email
    except ValueError:
        return False


async def recipients(db: AsyncSession, site: Site) -> list[Recipient]:
    rows = await db.execute(_recipients_query(site))
    return [
        Recipient(member_id=member_id, email=email, sso_subject=sub, language=language)
        for member_id, email, sub, language in rows
        if _plain(email)
    ]


async def recipient(db: AsyncSession, site: Site, member_id: uuid.UUID) -> Recipient | None:
    """`member_id` as a recipient on `site` right now, or None."""
    row = (await db.execute(_recipients_query(site).where(Member.id == member_id))).first()
    if row is None or not _plain(row.email):
        return None
    return Recipient(member_id=row.id, email=row.email, sso_subject=row.sso_subject, language=row.language)

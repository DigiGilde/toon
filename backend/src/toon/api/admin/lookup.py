"""Finding rows and checking roles: the database helpers more than one area of
the admin API uses, the audit write among them.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from toon.access import roles
from toon.api.admin.schemas import AccessChoice, MemberSearchOut, PreviousSlugOut
from toon.api.authorization import require_at_least, require_group_role
from toon.api.errors import ApiError
from toon.api.schema import iso_utc
from toon.audit import request as audit_request
from toon.audit import vocabulary
from toon.constants import MAX_PREVIOUS_SLUGS, AccessBase, Role
from toon.models.identity import Group, Member, MemberStatus, PlatformRole
from toon.models.publication import Site
from toon.slug_window import redirect_ends_at, still_redirects


async def _group_or_404(db: AsyncSession, slug: str) -> Group:
    group = await db.scalar(select(Group).where(Group.slug == slug))
    if group is None:
        raise ApiError(404, "UNKNOWN_GROUP")
    return group


async def _site_or_404(db: AsyncSession, group: Group, slug: str) -> Site:
    site = await db.scalar(
        select(Site).where(Site.group_id == group.id, Site.slug == slug)
    )
    if site is None:
        raise ApiError(404, "UNKNOWN_SITE")
    return site


async def _group_with_role(
    db: AsyncSession,
    member: Member,
    slug: str,
    minimum: Role,
    *,
    platform_admin: bool = False,
) -> Group:
    """Looks up the group and demands a group role of at least `minimum`.

    `platform_admin` lets a platform admin through without a group role; it
    is set only on the actions about people and groups, never about content.
    """
    group = await _group_or_404(db, slug)
    if platform_admin and member.platform_role == PlatformRole.ADMIN:
        return group
    await require_group_role(db, member, group.id, minimum)
    return group


async def _site_with_role(
    db: AsyncSession, member: Member, group_slug: str, site_slug: str, minimum: Role
) -> tuple[Group, Site]:
    """Looks up group and site and demands an effective site role of at least
    `minimum`: the widest of group role and site role.

    The role cannot be computed without the site, so the lookup comes first.
    That would tell anyone with a session which sites exist in a group they
    have nothing to do with, so someone with no role at all gets the same 404
    as for a site that is not there.

    Two people are spared that: someone who does have a role and only a
    narrower one than this action needs, and a platform admin. Both can
    already see that the site exists, the first from within the group and the
    second from the detail of any group, so hiding it from them protects
    nothing and only sends them looking for a mistake
    they did not make.
    """
    group = await _group_or_404(db, group_slug)
    site = await _site_or_404(db, group, site_slug)
    role = await roles.effective_site_role(db, site, member.id)
    if role is None and member.platform_role != PlatformRole.ADMIN:
        raise ApiError(404, "UNKNOWN_SITE")
    require_at_least(role, minimum)
    return group, site


async def _reread_locked(db: AsyncSession, row: Group | Site, *, slug_change: bool = False) -> None:
    """Reads `row` again under FOR NO KEY UPDATE: another change of the row
    waits, a row that points at it does not. `slug_change` takes FOR UPDATE,
    which changing a unique column takes anyway, so that a row pointing at it
    waits too. A row deleted since the role check is the 404 of one that was
    never there (`db.refresh` would raise)."""
    model = type(row)
    found = await db.scalar(
        select(model)
        .where(model.id == row.id)
        .with_for_update(key_share=not slug_change)
        .execution_options(populate_existing=True)
    )
    if found is None:
        raise ApiError(404, "UNKNOWN_GROUP" if model is Group else "UNKNOWN_SITE")


async def _member_or_404(db: AsyncSession, member_id: uuid.UUID) -> Member:
    target = await db.get(Member, member_id)
    if target is None:
        raise ApiError(404, "UNKNOWN_MEMBER")
    return target


async def _commit_or_409(db: AsyncSession, key: str, *, params: Mapping[str, object] | None = None) -> None:
    """Commits; a unique violation rolls back and becomes the 409 `key`."""
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ApiError(409, key, params=params) from None


async def _write_one_or_404(db: AsyncSession, statement: Any, key: str) -> None:
    """Runs a DELETE or UPDATE ... RETURNING aimed at one row and commits it;
    when it finds no row, rolls back and raises the 404 `key` instead."""
    if (await db.execute(statement)).first() is None:
        await db.rollback()
        raise ApiError(404, key)
    await db.commit()


async def _previous_slugs(
    db: AsyncSession, owner: InstrumentedAttribute[uuid.UUID], owner_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[PreviousSlugOut]]:
    """Per group or site (`owner` is `GroupSlug.group_id` or
    `SiteSlug.site_id`), the slugs it gave up that still redirect, newest
    first. One that stopped redirecting is left out before the nightly
    cleanup has removed it."""
    namespace = owner.class_
    now = datetime.now(UTC)
    rows = await db.execute(
        select(owner, namespace.slug, namespace.retired_at)
        .where(owner.in_(owner_ids), namespace.retired_at.is_not(None))
        .order_by(namespace.retired_at.desc(), namespace.slug)
    )
    found: dict[uuid.UUID, list[PreviousSlugOut]] = {}
    for owner_id, slug, retired_at in rows:
        if still_redirects(retired_at, now):
            found.setdefault(owner_id, []).append(
                PreviousSlugOut(slug=slug, redirects_until=iso_utc(redirect_ends_at(retired_at)))
            )
    return found


def _require_room_for_an_old_slug(previous_slugs: list[PreviousSlugOut], slug: str) -> None:
    """Going back to one of the old slugs keeps their number as it is; any
    other new slug adds the current one to them."""
    if len(previous_slugs) >= MAX_PREVIOUS_SLUGS and slug not in {old.slug for old in previous_slugs}:
        raise ApiError(409, "TOO_MANY_PREVIOUS_SLUGS", params={"max": MAX_PREVIOUS_SLUGS})


async def _audit(request: Request, member: Member, action: str, refs: dict) -> None:
    await audit_request.write(request, action, audit_request.member_actor(member), vocabulary.ALLOWED, refs=refs)


class IdentifierAmbiguousError(Exception):
    """Raised when an identifier matches more than one distinct subject.

    `sso_subject` carries a unique constraint, so an exact match on it is
    never ambiguous; neither `email` nor `name` does (on `members`, nor on
    `content_viewers` for email), so a lookup that falls back to either can
    genuinely hit more than one person. `via` says which step it was, so the
    409 can point at a way out that actually resolves it: the SSO-subject for
    an ambiguous email, the email for an ambiguous name.
    """

    def __init__(self, matches: int, *, via: Literal["email", "name"] = "email") -> None:
        self.matches = matches
        self.via = via
        super().__init__(f"{matches} different subjects for this identifier")


async def _find_member_by_identifier(db: AsyncSession, identifier: str) -> Member | None:
    """Exact sso_subject match wins; only when there is none does an email
    match count; only when there is neither does an exact, case-insensitive
    name match on exactly one ACTIVE member count. Only the email or the name
    step can be ambiguous (`IdentifierAmbiguousError`).

    Not used for the audit de-anonymisation lookup (`_resolve_lookup_identifier`):
    a name is not an identifier you should be able to guess your way through an
    actor pseudonym with, unlike a group or site membership where the person
    doing the adding already has to know who they mean.
    """
    normalised = identifier.strip()
    exact = await db.scalar(select(Member).where(Member.sso_subject == normalised))
    if exact is not None:
        return exact
    matches = list(await db.scalars(select(Member).where(Member.email == normalised.lower())))
    if len(matches) > 1:
        raise IdentifierAmbiguousError(len(matches), via="email")
    if matches:
        return matches[0]
    name_matches = list(
        await db.scalars(
            select(Member).where(
                Member.status == MemberStatus.ACTIVE,
                Member.name.isnot(None),
                func.lower(Member.name) == normalised.lower(),
            )
        )
    )
    if len(name_matches) > 1:
        raise IdentifierAmbiguousError(len(name_matches), via="name")
    return name_matches[0] if name_matches else None


async def _find_member_or_409(db: AsyncSession, identifier: str) -> Member | None:
    """Same resolution as `_find_member_by_identifier`, turning an ambiguous
    email or name match into a 409 so callers do not each have to handle it."""
    try:
        return await _find_member_by_identifier(db, identifier)
    except IdentifierAmbiguousError as error:
        key = (
            "IDENTIFIER_AMBIGUOUS.member_email"
            if error.via == "email"
            else "IDENTIFIER_AMBIGUOUS.member_name"
        )
        raise ApiError(409, key) from error


SEARCH_MINIMUM = 2
SEARCH_LIMIT = 10


async def _search_members(
    db: AsyncSession,
    term: str,
    already: set[uuid.UUID],
    group_roles: Mapping[uuid.UUID, Role] | None = None,
) -> list[MemberSearchOut]:
    """Active members whose name or e-mail contains `term`, as a shortlist.

    The floor under the term keeps an empty query from handing out the whole
    directory, and the cap keeps the answer a shortlist instead of an export.
    Whoever already has a role here stays in the answer: saying so beats
    leaving the row out, which reads as if the search were broken.

    `already` holds whoever has a role at this very level, and is the only
    thing that rules someone out. `group_roles` is what the site search adds
    on top: the role a member already reaches this site with through the
    group, which narrows or widens nothing but tells the admin whether a site
    role would add anything.
    """
    normalised = term.strip()
    if len(normalised) < SEARCH_MINIMUM:
        raise ApiError(422, "SEARCH_TOO_SHORT", params={"minimum": SEARCH_MINIMUM})
    # name is nullable and goes over the wire as "", so the sort uses the same
    # substitution; otherwise a nameless member lands after every named one.
    rows = await db.scalars(
        select(Member)
        .where(
            Member.status == MemberStatus.ACTIVE,
            Member.name.icontains(normalised, autoescape=True)
            | Member.email.icontains(normalised, autoescape=True),
        )
        .order_by(func.coalesce(Member.name, ""), Member.email)
        .limit(SEARCH_LIMIT)
    )
    return [
        MemberSearchOut(
            identifier=row.email,
            name=row.name or "",
            email=row.email,
            already_member=row.id in already,
            group_role=(group_roles or {}).get(row.id),
        )
        for row in rows
    ]


def _chosen_access(
    choice: AccessChoice | None, base: AccessBase, keys: bool, invitees: bool
) -> tuple[AccessBase, bool, bool]:
    """The access asked for, with every field left out taken from the default."""
    if choice is None:
        return base, keys, invitees
    return (
        choice.base if choice.base is not None else base,
        choice.keys if choice.keys is not None else keys,
        choice.invitees if choice.invitees is not None else invitees,
    )


def _access_refs(base: AccessBase, keys: bool, invitees: bool) -> dict:
    return {"base": str(base), "keys": keys, "invitees": invitees}

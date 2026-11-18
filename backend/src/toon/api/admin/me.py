"""The member's own account: profile and language."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from toon.access import roles
from toon.api.admin.common import ROLE_HINT, SITE_ROLE_HINT
from toon.api.admin.contract import _ERROR_CSRF, _deleted, _errors
from toon.api.admin.dependencies import ActiveMember, Csrf, Db
from toon.api.admin.lookup import _audit
from toon.api.admin.schemas import MemberOut
from toon.api.admin.serialization import _content_base, _member_out
from toon.api.docs import TAG_SESSION
from toon.api.schema import ApiModel, SessionBody
from toon.audit import vocabulary
from toon.constants import SLUG_REDIRECT_DAYS, Role
from toon.models.identity import Group, GroupMember, Member, MemberLanguage, SiteMember
from toon.models.publication import Site


class LanguageUpdate(SessionBody):
    """The language choice of the signed-in member."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"language": "en"}, {"language": None}]}
    )

    language: MemberLanguage | None = Field(
        description=(
            "`nl` or `en`, or `null` to let the browser decide again "
            "(`Accept-Language`, with English if that does not settle it). The choice is tied to the "
            "account, so it applies on every device."
        ),
        examples=["en"],
    )


class MyGroupRole(ApiModel):
    """A group in which the signed-in member has a role."""

    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    role: Role = Field(description=f"Role of the member in this group: {ROLE_HINT}", examples=["editor"])


class MySiteRole(ApiModel):
    """A site on which the signed-in member has a direct site role."""

    group_slug: str = Field(description="Slug of the group this site is in.", examples=["aurora"])
    site_slug: str = Field(description="Slug of the site.", examples=["docs"])
    role: Role = Field(
        description=f"The site role itself, independent of the group role: {SITE_ROLE_HINT}", examples=["editor"]
    )
    effective_role: Role = Field(
        description=(
            "What the member is actually allowed to do on this site: the higher of their group role "
            "and this site role."
        ),
        examples=["editor"],
    )


class MyProfile(MemberOut):
    """The signed-in member, extended with what the SPA needs to build links."""

    content_base_url: str = Field(
        description=(
            "Origin on which the published content lives. The SPA builds public URLs, preview links "
            "and secret links on it. Always a different host than the admin interface: content and admin "
            "never share an origin."
        ),
        examples=["https://sites.toon.example"],
    )
    group_roles: list[MyGroupRole] = Field(
        default_factory=list,
        description=(
            "Groups in which this member has a role, sorted by slug. Empty if the member is not a group "
            "member anywhere."
        ),
    )
    site_roles: list[MySiteRole] = Field(
        default_factory=list,
        description=(
            "Sites on which this member has a direct site role, sorted by group and site. Only the sites "
            "with a direct site role are listed: on every other site of a group the group role "
            "from `groupRoles` simply applies."
        ),
    )
    ci_forgejo_hosts: list[str] = Field(
        default_factory=list,
        description=(
            "The Forgejo instances whose CI ID tokens Toon accepts (`TOON_CI_FORGEJO_HOSTS`), as "
            "base URL. GitHub is always accepted and is not listed here."
        ),
        examples=[["https://code.overheid.nl"]],
    )
    ci_audience: str = Field(
        default="",
        description=(
            "The audience a CI workflow must request for its ID token: exactly `TOON_BASE_URL`. That "
            "is also the value for the `host` input of the Toon action."
        ),
        examples=["https://beheer.toon.example"],
    )
    language: MemberLanguage | None = Field(
        default=None,
        description=(
            "The language this member chose for the admin interface: `nl` or `en`. `null` means the member "
            "made no choice and the SPA takes the language from the browser. The choice is stored on the account "
            "and so applies on every device."
        ),
        examples=["en"],
    )
    slug_redirect_days: int = Field(
        description=(
            "Through how many days after a change of address the old addresses of a group or site still redirect, "
            "until midnight, Amsterdam time. The SPA takes the number from here, so its texts say what the server "
            "does."
        ),
        examples=[SLUG_REDIRECT_DAYS],
    )


async def _my_group_roles(db: AsyncSession, member: Member) -> list[MyGroupRole]:
    rows = await db.execute(
        select(Group.slug, GroupMember.role)
        .select_from(GroupMember)
        .join(Group, Group.id == GroupMember.group_id)
        .where(GroupMember.member_id == member.id)
        .order_by(Group.slug)
    )
    return [MyGroupRole(group_slug=slug, role=role) for slug, role in rows]


async def _my_site_roles(db: AsyncSession, member: Member) -> list[MySiteRole]:
    """Only the sites with a direct site role; everywhere else the group role stands on its own."""
    rows = await db.execute(
        select(Group.slug, Site.slug, SiteMember.role, GroupMember.role)
        .select_from(SiteMember)
        .join(Site, Site.id == SiteMember.site_id)
        .join(Group, Group.id == Site.group_id)
        .outerjoin(
            GroupMember,
            (GroupMember.group_id == Group.id) & (GroupMember.member_id == member.id),
        )
        .where(SiteMember.member_id == member.id)
        .order_by(Group.slug, Site.slug)
    )
    return [
        MySiteRole(
            group_slug=group_slug,
            site_slug=site_slug,
            role=site_role,
            effective_role=roles.widest(group_role, site_role),
        )
        for group_slug, site_slug, site_role, group_role in rows
    ]


def _ci_audience(request: Request) -> str:
    """What a CI workflow asks its ID token for; TOON_BASE_URL exactly, or the
    request origin in dev, where CI tokens are refused anyway."""
    base_url = request.app.state.settings.base_url
    return base_url or str(request.base_url).rstrip("/")


def add_routes(router: APIRouter) -> None:
    # -- Session --

    @router.get(
        "/me",
        tags=[TAG_SESSION],
        summary="The signed-in member",
        response_description="The member behind the current session, with the content origin.",
        description=(
            "Returns the member behind the current admin session, plus the origin on which the SPA builds "
            "content links, preview links and secret links. This is also the cheapest way to check whether the "
            "session is still valid.\n\n"
            "`groupRoles` and `siteRoles` say where this member is allowed to do something, so the SPA knows "
            "what to offer. `siteRoles` only contains the sites with a direct site role; on every other "
            "site of a group the group role from `groupRoles` applies. A platform administrator can have both "
            "lists empty: they manage people and groups, and give themselves a group role to make content "
            "visible to them.\n\n"
            "**Who can call this:** any active member with a valid admin session. A member with status "
            "`deactivated` gets 403, as on every other endpoint."
        ),
        responses=_errors(),
    )
    async def me(request: Request, member: ActiveMember, db: Db) -> MyProfile:
        return MyProfile(
            **(await _member_out(db, member)).model_dump(),
            content_base_url=_content_base(request),
            group_roles=await _my_group_roles(db, member),
            site_roles=await _my_site_roles(db, member),
            ci_forgejo_hosts=list(request.app.state.settings.forgejo_hosts),
            ci_audience=_ci_audience(request),
            language=member.language,
            slug_redirect_days=SLUG_REDIRECT_DAYS,
        )

    @router.put(
        "/me/language",
        status_code=204,
        tags=[TAG_SESSION],
        summary="Set my language",
        description=(
            "Records the language in which this member wants to read the admin interface: `nl`, `en`, or "
            "`null` to leave the language to the browser again. The choice is stored on the account, not on "
            "the device, so it applies everywhere this member signs in.\n\n"
            "The SPA then sends this language in `Accept-Language`, so problem+json messages "
            "come back in it too.\n\n"
            "**Who can call this:** any active member, for themselves, with a valid CSRF header."
        ),
        responses=_deleted("The language choice has been recorded.")
        | _errors(_ERROR_CSRF, {422: "`language` is not a supported language and not `null`."}),
    )
    async def set_my_language(
        request: Request, body: LanguageUpdate, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        member.language = body.language
        await db.commit()
        await _audit(
            request,
            member,
            vocabulary.MEMBER_LANGUAGE,
            {"language": body.language.value if body.language else None},
        )
        return Response(status_code=204)

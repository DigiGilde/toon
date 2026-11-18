"""The request and response models that more than one area of the admin API uses."""

from __future__ import annotations

import uuid

from pydantic import ConfigDict, Field

from toon.api.admin.common import ACCESS_EXTRAS_HINT, ROLE_HINT, SITE_ROLE_HINT, _timestamp_schema
from toon.api.schema import ACCESS_BASE_HINT, AccessOut, ApiModel, SessionBody
from toon.constants import SLUG_REDIRECT_DAYS, AccessBase, FeedbackIdentity, Role
from toon.models.identity import MemberStatus, PlatformRole


class AccessChoice(ApiModel):
    """Access when creating: base plus exceptions. Every field may be omitted; what then applies
    is described on the field that uses this object."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"base": "nobody", "keys": True}, {"base": "public"}]}
    )

    base: AccessBase | None = Field(default=None, description=f"The base, exactly one of: {ACCESS_BASE_HINT}")
    keys: bool | None = Field(
        default=None, description="Whether secret links grant access. " + ACCESS_EXTRAS_HINT
    )
    invitees: bool | None = Field(
        default=None, description="Whether to give invitees access after signing in with SSO Rijk."
    )


class AccessBody(SessionBody):
    """Who may see the content: a base plus two exceptions."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"base": "nobody", "keys": True, "invitees": False}]}
    )

    base: AccessBase = Field(description=f"The base, exactly one of: {ACCESS_BASE_HINT}")
    keys: bool = Field(
        default=False, description="Whether secret links grant access. " + ACCESS_EXTRAS_HINT
    )
    invitees: bool = Field(
        default=False, description="Whether to give invitees access after signing in with SSO Rijk."
    )


class IdentifierBody(SessionBody):
    """A person, identified by e-mail address or SSO subject."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"identifier": "genodigde@example.nl"}]}
    )

    identifier: str = Field(
        description="E-mail address or SSO subject. Case in an e-mail address is ignored.",
        examples=["genodigde@example.nl"],
    )


class MemberOut(ApiModel):
    """A platform member."""

    id: uuid.UUID = Field(description="Internal ID of the member.")
    sso_subject: str = Field(description="The `sub` from the SSO token; a session is tied to a member through it.")
    email: str = Field(description="E-mail address from the SSO profile; also the identifier for group membership.")
    name: str = Field(description="Display name from the SSO profile; empty if the identity provider does not send it.")
    platform_role: PlatformRole = Field(
        description="`admin` may configure platform-wide, `member` only within their own groups."
    )
    status: MemberStatus = Field(
        description=(
            "`active` (may use the API) or `deactivated` (is refused). Only `active` gets "
            "through to the API."
        )
    )
    is_bootstrap: bool = Field(
        default=False,
        description=(
            "Whether this is the account from `TOON_BOOTSTRAP_ADMIN_SUB`. That account is restored to "
            "administrator and active at every sign-in, so its status and platform role cannot be changed. The "
            "SPA uses this to not offer those actions."
        ),
        examples=[False],
    )
    created_at: str = Field(
        description="When the member was created: the first visit to the admin interface.",
        json_schema_extra=_timestamp_schema(),
    )
    last_login_at: str | None = Field(
        default=None,
        description="Last successful sign-in, or `null` if there was none yet.",
        json_schema_extra=_timestamp_schema(),
    )
    last_active_at: str | None = Field(
        default=None,
        description=(
            "When the member was last active: the later of the last sign-in and the last use of a CLI session "
            "that still exists, or `null` if there was neither yet."
        ),
        json_schema_extra=_timestamp_schema(),
    )


class PreviousSlugOut(ApiModel):
    """An old slug whose addresses still redirect to the current ones."""

    slug: str = Field(description="The old slug.", examples=["oud-team"])
    redirects_until: str = Field(
        description=(
            f"When the redirect ends: midnight, Amsterdam time, at the end of day {SLUG_REDIRECT_DAYS} after the "
            "change. From then on the old addresses answer 404; the slug is free again for others only once the "
            "nightly cleanup after that has run."
        ),
        json_schema_extra=_timestamp_schema(),
    )


class GroupOut(ApiModel):
    """A group: owner of sites and the unit of membership."""

    slug: str = Field(description="Slug of the group; the first path segment of every site URL.", examples=["aurora"])
    name: str = Field(description="Display name of the group.", examples=["Team Aurora"])
    default_access: AccessOut = Field(
        description=(
            "Access that a new site in this group starts with. Existing sites are not affected."
        )
    )
    previous_slugs: list[PreviousSlugOut] = Field(
        description=(
            "Old slugs of the group whose addresses still redirect to the current ones, newest first; empty when "
            "there are none. Each stays reserved for this group, which can change back to it until its redirect "
            "has ended."
        )
    )


class SiteOut(ApiModel):
    """A site with the summary the SPA shows in lists."""

    id: uuid.UUID = Field(
        description=(
            "Fixed id of the site. A workflow names it as `site-id`, so a deploy can only land on this site, "
            "whatever its address."
        )
    )
    group_slug: str = Field(description="Slug of the group this site is in.", examples=["aurora"])
    slug: str = Field(description="Slug of the site; the second path segment of the site URL.", examples=["docs"])
    previous_slugs: list[PreviousSlugOut] = Field(
        description=(
            "Old slugs of the site whose addresses still redirect to the current ones, newest first; empty when "
            "there are none. Each stays reserved for this site, which can change back to it until its redirect "
            "has ended. A change of the group's slug is not in here but in the `previousSlugs` of the group."
        )
    )
    title: str = Field(description="Display name of the site.", examples=["Documentatie"])
    access: AccessOut = Field(description="Who may see the live content: base plus exceptions.")
    external_sources: bool = Field(
        description=(
            "Whether the content of this site may load scripts, styles and fonts from a fixed list of "
            "external hosts. `true` by default; turning it off is an extra restriction."
        )
    )
    sandbox: bool = Field(
        description=(
            "Whether the content of this site is isolated from the other sites on the same "
            "hostname. `true` by default; turning it off is needed for a site that stores something in the "
            "browser."
        )
    )
    live_version_id: uuid.UUID | None = Field(
        default=None, description="Version that is currently on the public URL, or `null` if nothing is live yet."
    )
    live_versions_kept: int | None = Field(
        default=None,
        description=(
            "Site-specific number of previous live versions that this site keeps, or `null` if the site follows the "
            "platform default. `0` keeps all live versions. The number that currently "
            "applies is at `GET /sites/{groupSlug}/{siteSlug}/storage`."
        ),
        examples=[3],
    )
    created_by: str = Field(
        description="ID of the member who created the site; empty if that member has since been deleted."
    )
    has_live_version: bool = Field(description="Shorter form of `liveVersionId is not null`, handy in lists.")
    last_published_at: str | None = Field(
        default=None,
        description="Time of the most recent deploy, live or preview; `null` if nothing has been deployed yet.",
        json_schema_extra=_timestamp_schema(),
    )
    preview_count: int = Field(description="Number of previews that currently exist for this site.", examples=[2])
    feedback_enabled: bool = Field(
        description=(
            "Whether Toon adds the feedback button to the pages of this site: on its previews, and with "
            "`feedbackLive` also on the live site. `false` by default."
        )
    )
    feedback_identity: FeedbackIdentity = Field(
        description=(
            "Who may give feedback: `account` (the default) only with an SSO account, `name` also with a typed "
            "name, `anonymous` also without a name."
        )
    )
    feedback_live: bool = Field(
        description="Whether feedback is collected on the live site as well, not only on previews. `false` by default."
    )
    open_feedback_count: int = Field(
        description="Number of feedback items on this site with status `new` or `seen`.", examples=[3]
    )


class GroupSiteRole(ApiModel):
    """A role of its own on one site, always a site in the group you are looking at."""

    site_slug: str = Field(description="Slug of the site within this group.", examples=["jaarverslag"])
    site_title: str = Field(description="Title of the site, as shown in the admin interface.")
    role: Role = Field(
        description=f"Role that applies only on this site: {SITE_ROLE_HINT}",
        examples=["editor"],
    )


class GroupMemberOut(ApiModel):
    """A member of a group, as the member list of that group shows it."""

    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    member_id: str = Field(
        description="ID of the platform member; you use it to remove them from the group.",
        examples=["3f2a1c6e-9b4d-4f2a-8c1e-7d5b2a9f4c31"],
    )
    identifier: str = Field(
        description="What you use to add this member to the group or change their role: the e-mail address.",
        examples=["lid@example.nl"],
    )
    name: str = Field(description="Display name from the SSO profile; empty if missing.")
    email: str = Field(description="E-mail address from the SSO profile.", examples=["lid@example.nl"])
    role: Role = Field(
        description=f"Role of this member in this group: {ROLE_HINT}",
        examples=["reader"],
    )
    site_roles: list[GroupSiteRole] = Field(
        description=(
            "The sites in this group on which this member has a direct site role, sorted by slug. Such a "
            "role is independent of the group role and stays in force if the member leaves the group, unless you "
            "remove it at the same time (`siteRoles=remove` when removing).\n\n"
            "What this member has in another group is not listed: that belongs to that group."
        ),
    )


class MemberSearchOut(ApiModel):
    """A platform member that was found, as the search field of 'add member' shows it."""

    identifier: str = Field(
        description="What you use to add this member: the e-mail address.",
        examples=["lid@example.nl"],
    )
    name: str = Field(description="Display name from the SSO profile; empty if missing.")
    email: str = Field(description="E-mail address from the SSO profile.", examples=["lid@example.nl"])
    already_member: bool = Field(
        description=(
            "Whether this member already has a direct role here: a group role in the search field of a "
            "group, a site role in that of a site. You do not add them then; you change the role in "
            "the member list."
        ),
        examples=[False],
    )
    group_role: Role | None = Field(
        default=None,
        description=(
            "Only in the search field of a site: the role with which this member already reaches this site "
            f"through the group, or `null` if they are not a group member: {ROLE_HINT} A site role only "
            "widens, so an equally low or lower site role changes nothing here. In the "
            "search field of a group this field is always `null`."
        ),
        examples=["reader"],
    )

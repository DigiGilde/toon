"""A site: creating it, deleting it, its title and address, its access, and
the settings that shape how its content is served.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict, Field
from sqlalchemy import delete
from sqlalchemy.exc import DBAPIError, IntegrityError
from starlette.responses import Response

from toon.access import roles
from toon.api.admin.common import TEXT_MAX_LENGTH, _slug_conflict, _validate_slug, _validate_text
from toon.api.admin.contract import (
    _CREATION_RULE,
    _ERROR_CLI_TOKEN,
    _ERROR_CREATIONS,
    _ERROR_CSRF,
    _ERROR_GROUP,
    _ERROR_GROUP_ROLE,
    _ERROR_SITE,
    _ERROR_SITE_ROLE,
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
    _site_with_role,
)
from toon.api.admin.schemas import AccessBody, AccessChoice, SiteOut
from toon.api.admin.serialization import _one_site_json, _site_json, _sites_json
from toon.api.docs import TAG_SITES
from toon.api.errors import ApiError
from toon.api.schema import ApiModel, SessionBody
from toon.constants import MAX_PREVIOUS_SLUGS, SLUG_REDIRECT_DAYS, Role
from toon.models.identity import PlatformRole, SiteMember
from toon.models.publication import Site
from toon.models.slugs import SiteSlug

_ERROR_SANDBOX_REQUIRED = {
    409: "In a preview, only a platform administrator can turn off shielding from other sites (`SANDBOX_REQUIRED`)."
}


class SiteCreate(ApiModel):
    """A new site within a group."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"title": "Documentatie", "slug": "docs"},
                {"title": "Documentatie", "slug": "docs", "access": {"base": "public"}},
            ]
        }
    )

    title: str = Field(description="Display name of the site.", examples=["Documentatie"])
    slug: str = Field(
        description="Slug of the site: lowercase letters, digits and hyphens, unique within the group.",
        examples=["docs"],
    )
    access: AccessChoice | None = Field(
        default=None,
        description=(
            "Optional: the access the site starts with. Every omitted field, or the whole object "
            "omitted, inherits the default access of the group, so `{\"keys\": true}` only turns on "
            "secret links on top of what the group already prescribes."
        ),
    )


class SiteTitleBody(SessionBody):
    """The new display title of a site."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"title": "Documentatie"}]})

    title: str = Field(
        description=(
            f"New display title, 1 to {TEXT_MAX_LENGTH} characters after trimming spaces; no control or "
            "formatting characters."
        ),
        json_schema_extra={"maxLength": TEXT_MAX_LENGTH},
    )


class SiteSlugBody(SessionBody):
    """The new slug of a site within its group."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"slug": "docs"}]})

    slug: str = Field(
        description=(
            "New slug of the site: lowercase letters, digits and hyphens, at most 63 characters. Not the current "
            "slug of another site in the group, nor one that another site in the group gave up and that is not "
            "free again yet, unless `release` releases it."
        ),
        examples=["docs"],
    )
    release: bool = Field(
        default=False,
        description=(
            "When true, a slug that another site in the group gave up and that is still reserved for it is "
            "released and taken. That site's old address stops redirecting at once. Needs the group role `admin`."
        ),
    )


class ExternalSourcesBody(SessionBody):
    """Whether the content of this site may load external sources."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"externalSources": True}]})

    external_sources: bool = Field(
        description=(
            "`true` (the default) lets the page load scripts and styles from cdnjs, jsDelivr and "
            "unpkg, and fonts from Google Fonts. `false` only allows sources from the site itself, "
            "and is the safer choice for a confidential page. What stays blocked in both modes: "
            "fetching data from or sending data to other hosts, images from elsewhere, an iframe, "
            "and a form that posts elsewhere."
        )
    )


class SandboxBody(ApiModel):
    """Whether the content of this site is isolated from the other sites."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"sandbox": True}]})

    sandbox: bool = Field(
        description=(
            "`true` (the default) serves the content with a CSP sandbox without "
            "`allow-same-origin`, so the page gets an opaque origin: it cannot "
            "read any other site on this hostname, receives no cookies and cannot "
            "store anything in the browser. Its own styles, scripts, images and fonts "
            "load as usual. `false` puts the page back on the shared origin, needed for a "
            "site that uses `localStorage`, `sessionStorage` or a cookie."
        )
    )


class LiveVersionsKeptBody(SessionBody):
    """How many previous live versions this site keeps."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"liveVersionsKept": 3}]})

    live_versions_kept: int | None = Field(
        strict=True,
        description=(
            "The number of previous live versions that the nightly cleanup leaves in place in addition to the "
            "current one: an integer of 0 or more. `0` keeps all live versions of this site; "
            "`null` makes the site follow the platform default."
        ),
        json_schema_extra={"minimum": 0},
    )


def add_routes(router: APIRouter) -> None:
    # -- Sites --

    @router.post(
        "/groups/{group_slug}/sites",
        status_code=201,
        tags=[TAG_SITES],
        summary="Create a site",
        response_description="The created site.",
        description=(
            "Creates a site within a group. The site gets the default access of the group, or whatever "
            "`access` asks for instead, and is served at `/{groupSlug}/{siteSlug}/` on the content origin, "
            "as soon as something has been deployed to it.\n\n"
            "**Who can call this:** group role `editor` or higher, with a valid CSRF header; the creator becomes "
            "`admin` of the site they create, and may therefore choose the access right away: that is no "
            "more than they may do afterwards anyway. A platform administrator without a group role is not "
            "allowed. " + _CREATION_RULE
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_CLI_TOKEN,
            _ERROR_CREATIONS,
            _ERROR_GROUP_ROLE,
            _ERROR_GROUP,
            {
                409: (
                    "Another site in this group has this slug, now or as an old slug that is not free again yet "
                    "(`SLUG_EXISTS`)."
                )
            },
            {422: "The slug is invalid (`SLUG_INVALID`), or the title is empty (`FIELD_EMPTY`)."},
        ),
    )
    async def create_site(
        request: Request,
        group_slug: str,
        body: SiteCreate,
        creator: Annotated[Creator, Depends(require_creator)],
        db: Db,
    ) -> SiteOut:
        member = creator.member
        group = await _group_with_role(db, member, group_slug, Role.EDITOR)
        slug = _validate_slug(body.slug)
        title = _validate_text(body.title, "title")
        base, keys, invitees = _chosen_access(
            body.access, group.default_access_base, group.default_access_keys, group.default_access_invitees
        )
        await _require_creation_budget(request, member)
        site = Site(
            group_id=group.id,
            slug=slug,
            title=title,
            access_base=base,
            access_keys=keys,
            access_invitees=invitees,
            created_by=member.id,
        )
        db.add(site)
        try:
            await db.flush()
        except IntegrityError as error:
            await db.rollback()
            raise _slug_conflict(error, "site", slug) from None
        # The creator becomes site admin of what they create.
        db.add(
            SiteMember(
                site_id=site.id, member_id=member.id, role=Role.ADMIN, added_by=member.id
            )
        )
        await db.commit()
        await _audit(
            request,
            member,
            "site_create",
            {"group": group_slug, "site": slug, **_access_refs(base, keys, invitees), **creator.audit_refs()},
        )
        return _site_json(site, group.slug, None, 0, 0, [])

    @router.delete(
        "/sites/{group_slug}/{site_slug}",
        status_code=204,
        tags=[TAG_SITES],
        summary="Delete a site",
        description=(
            "Deletes the site with everything attached to it: versions, previews, invitees, secret links, "
            "the linked repository and the unpacked files on disk. Irreversible; the URL returns 404 afterwards.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_deleted("The site and all its content have been deleted.")
        | _errors(_ERROR_CSRF, _ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def delete_site(
        request: Request, group_slug: str, site_slug: str, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        site_id = site.id
        await db.delete(site)
        await db.commit()
        await asyncio.to_thread(request.app.state.content_store.delete_site, site_id)
        await _audit(request, member, "site_delete", {"group": group_slug, "site": site_slug})
        return Response(status_code=204)

    @router.put(
        "/sites/{group_slug}/{site_slug}/title",
        tags=[TAG_SITES],
        summary="Change the title of a site",
        response_description="The site with its new title.",
        description=(
            "Sets the display title of the site. The slug, and with it every URL of the site, does not "
            "change. The audit log keeps the previous title, in full. Sending the title the site already has "
            "(after trimming spaces) changes nothing and writes no audit row.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {
                422: (
                    "The title is empty (`FIELD_EMPTY`), contains control or formatting characters "
                    f"(`FIELD_CONTROL_CHARACTERS`) or is longer than {TEXT_MAX_LENGTH} characters "
                    "(`FIELD_TOO_LONG`)."
                )
            },
        ),
    )
    async def set_site_title(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: SiteTitleBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        title = _validate_text(body.title, "title", max_length=TEXT_MAX_LENGTH)
        # Locked and read again: the audit row must name the value this change
        # replaces, not the one read before a change that committed meanwhile.
        await _reread_locked(db, site)
        previous = site.title
        if title != previous:
            site.title = title
            await db.commit()
            await _audit(
                request,
                member,
                "site_title",
                {"group": group_slug, "site": site_slug, "site_id": str(site.id), "previous_title": previous},
            )
        return (await _sites_json(db, group, [site]))[0]

    @router.put(
        "/sites/{group_slug}/{site_slug}/slug",
        tags=[TAG_SITES],
        summary="Change the address of a site",
        response_description="The site at its new address.",
        description=(
            "Sets the slug of the site, the second path segment of its URL: the site, its previews and its secret "
            "links get a new address at once. The old slug stays reserved for this site, and its old addresses "
            f"redirect to the new ones through day {SLUG_REDIRECT_DAYS} after the change (until midnight, "
            "Amsterdam time), for visitors who may see the site. After that they answer 404, and the old slug is "
            "free for another site in the group once the nightly cleanup has run. Until then this site can change "
            "back to it. `previousSlugs` lists the old slugs that still redirect.\n\n"
            "Publishing under the old address stops at once: the API knows only the current slug, and from this "
            "change on the repository link of the site requires the site id. Sending the slug the site already has "
            "changes nothing and writes no audit row. If the response gets lost, the old path answers 404: read "
            "the site at the new slug, or send the request again with the new slug in the path, which then "
            "changes nothing.\n\n"
            "With `release` set, an admin of the group can take a slug that another site in the group gave up "
            "and that is still reserved for it. The reservation ends, and that site's old address stops "
            "redirecting at once. The audit log records the release in a row of its own. `release` never takes "
            "the current slug of another site, and where no other site holds the slug it makes no difference.\n\n"
            "**Who can call this:** effective site role `admin` together with a role in the group of the site, "
            "`reader` or higher, with a valid CSRF header. An admin of the site alone, without a role in its "
            "group, can change its title but not its address. Releasing a slug of another site needs the group "
            "role `admin`. Each change counts towards the limit of "
            f"{CREATION_MAX_PER_WINDOW} new groups, sites and addresses combined per {CREATION_WINDOW_S // 60} "
            f"minutes, and at most {MAX_PREVIOUS_SLUGS} old slugs of a site redirect at the same time."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            {
                403: (
                    "Changing the address also needs a role in the group of the site, which an admin of the site "
                    "alone does not have (`INSUFFICIENT_ROLE`). Setting `release` needs the group role `admin` "
                    "(`INSUFFICIENT_ROLE`)."
                )
            },
            _ERROR_SITE,
            _ERROR_CREATIONS,
            {
                409: (
                    "Another site in this group has this slug, now or as an old slug that is not free again yet "
                    "and that `release` did not release (`SLUG_EXISTS`); or this site already has "
                    f"{MAX_PREVIOUS_SLUGS} old slugs that still redirect, and the new slug is none of them "
                    "(`TOO_MANY_PREVIOUS_SLUGS`)."
                )
            },
            {422: "The slug is invalid (`SLUG_INVALID`)."},
        ),
    )
    async def set_site_slug(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: SiteSlugBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        # Whoever renames a site has to be able to see the group, which a role
        # on the site alone does not give: a 409 would say which slugs the
        # other sites of the group have, or recently had.
        group_role = await roles.group_role(db, group.id, member.id)
        if group_role is None:
            raise ApiError(403, "INSUFFICIENT_ROLE.site_address")
        # Releasing ends another site's redirect, so only an admin of the group may.
        if body.release and group_role != Role.ADMIN:
            raise ApiError(403, "INSUFFICIENT_ROLE.site_address_release")
        slug = _validate_slug(body.slug)
        # As for a group: locked and read again before anything is decided.
        # The group first: deleting a group locks it and then its sites, so
        # the other order deadlocks with that. The group's lock also lets one
        # address change in the group run at a time, and keeps its slug in the
        # audit row and the answer current.
        await _reread_locked(db, group)
        await _reread_locked(db, site, slug_change=True)
        previous = site.slug
        if slug == previous:
            return (await _sites_json(db, group, [site]))[0]
        await _require_creation_budget(request, member)
        previous_slugs = await _previous_slugs(db, SiteSlug.site_id, [site.id])
        _require_room_for_an_old_slug(previous_slugs.get(site.id, []), slug)
        released = None
        if body.release:
            # Only another site's retired row: a current slug stays the 409,
            # and this site's own retired row is taken back by the trigger.
            released = (
                await db.execute(
                    delete(SiteSlug)
                    .where(
                        SiteSlug.group_id == group.id,
                        SiteSlug.slug == slug,
                        SiteSlug.retired_at.is_not(None),
                        SiteSlug.site_id != site.id,
                        Site.id == SiteSlug.site_id,
                    )
                    .returning(SiteSlug.site_id, Site.slug)
                )
            ).one_or_none()
        site.slug = slug
        try:
            await db.flush()
        except IntegrityError as error:
            await db.rollback()
            raise _slug_conflict(error, "site", slug) from None
        await db.commit()
        if released is not None:
            await _audit(
                request,
                member,
                "site_slug_release",
                {
                    "group": group.slug,
                    "site": released.slug,
                    "released_site": slug,
                    "site_id": str(released.site_id),
                    "to_site_id": str(site.id),
                },
            )
        await _audit(
            request,
            member,
            "site_slug",
            {"group": group.slug, "site": slug, "previous_site": previous, "site_id": str(site.id)},
        )
        return (await _sites_json(db, group, [site]))[0]

    @router.put(
        "/sites/{group_slug}/{site_slug}/access",
        tags=[TAG_SITES],
        summary="Set the access to a site",
        response_description="The site with its new access.",
        description=(
            "Determines who may see the live content of this site: the base and the two "
            "exceptions in one go, because they belong together and a visitor gets in "
            "as soon as one of the three lets them in. The change applies immediately to every "
            "subsequent request for the content. Previews with their own `accessOverride` do not follow "
            "this value.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def set_access(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: AccessBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        site.access_base = body.base
        site.access_keys = body.keys
        site.access_invitees = body.invitees
        await db.commit()
        await _audit(
            request,
            member,
            "site_visibility",
            {
                "group": group_slug,
                "site": site_slug,
                "base": str(body.base),
                "keys": body.keys,
                "invitees": body.invitees,
            },
        )
        return await _one_site_json(db, group, site)

    @router.put(
        "/sites/{group_slug}/{site_slug}/external-sources",
        tags=[TAG_SITES],
        summary="Allow or block external sources",
        response_description="The site with its new setting.",
        description=(
            "Determines whether the content of this site may load scripts and styles from cdnjs, jsDelivr "
            "and unpkg, and fonts from Google Fonts. On by default; turning it off is an "
            "extra restriction and the safer choice for a confidential page. The change "
            "applies immediately to every subsequent request for the content, for the live site, "
            "previews and version views. Blocked in both modes: fetching data from or "
            "sending data to other hosts, images from elsewhere, an iframe, and a form that "
            "posts elsewhere.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def set_external_sources(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: ExternalSourcesBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        site.external_sources = body.external_sources
        await db.commit()
        await _audit(
            request,
            member,
            "site_external_sources",
            {"group": group_slug, "site": site_slug, "external_sources": body.external_sources},
        )
        return await _one_site_json(db, group, site)

    @router.put(
        "/sites/{group_slug}/{site_slug}/sandbox",
        tags=[TAG_SITES],
        summary="Turn isolation from other sites on or off",
        response_description="The site with its new setting.",
        description=(
            "All sites share one hostname. When this isolation is on, the default, the content is served "
            "with a CSP sandbox without `allow-same-origin`: the page gets an opaque origin and "
            "cannot read any other site on that hostname, receives no cookies and cannot store "
            "anything in the browser. Its own styles, scripts, images and fonts load as usual. "
            "Turning it off is needed for a site that uses `localStorage`, `sessionStorage` or a cookie, "
            "and puts that site back on the origin it shares with all other sites. The change "
            "applies immediately to every subsequent request for the content, for the live site, "
            "previews and version views.\n\n"
            "Also allowed with the CLI token from `toon login` (`toon site shielding`), then without a CSRF "
            "header. A CI ID token changes nothing.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header or the CLI token."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_CLI_TOKEN, _ERROR_SITE_ROLE, _ERROR_SITE, _ERROR_SANDBOX_REQUIRED),
    )
    async def set_sandbox(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: SandboxBody,
        creator: Annotated[Creator, Depends(require_creator)],
        db: Db,
    ) -> SiteOut:
        member = creator.member
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        # A preview's content host shares its parent domain with every other
        # ZAD tenant, so an unshielded page there could set cookies for them.
        # Everyone with SSO Rijk edits there; only a platform admin may take
        # that risk for a site, to test what needs shielding off.
        if (
            not body.sandbox
            and request.app.state.settings.is_preview
            and member.platform_role != PlatformRole.ADMIN
        ):
            raise ApiError(409, "SANDBOX_REQUIRED")
        site.sandbox = body.sandbox
        await db.commit()
        await _audit(
            request,
            member,
            "site_sandbox",
            {"group": group_slug, "site": site_slug, "sandbox": body.sandbox, **creator.audit_refs()},
        )
        return await _one_site_json(db, group, site)

    @router.put(
        "/sites/{group_slug}/{site_slug}/live-versions-kept",
        tags=[TAG_SITES],
        summary="Set the number of previous versions kept",
        response_description="The site with its new setting.",
        description=(
            "Determines how many previous live versions the nightly cleanup of this site leaves in place, "
            "in addition to the current live version. Older live versions are removed, row and files, and "
            "cannot be rolled back to afterwards. `0` keeps all live versions, `null` "
            "puts the site back on the platform default. The change takes effect at the "
            "next nightly cleanup.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {
                422: (
                    "Not an integer of 0 or more (`LIVE_VERSIONS_KEPT_INVALID`), or a number "
                    "that is too large to store (`LIVE_VERSIONS_KEPT_TOO_LARGE`)."
                )
            },
        ),
    )
    async def set_live_versions_kept(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: LiveVersionsKeptBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        kept = body.live_versions_kept
        if kept is not None and kept < 0:
            raise ApiError(422, "LIVE_VERSIONS_KEPT_INVALID")
        site.live_versions_kept = kept
        try:
            await db.commit()
        except DBAPIError as error:
            # The column's integer type is the only upper bound. A number it
            # cannot hold comes back as SQLSTATE class 22 (data exception):
            # 22000 when asyncpg cannot encode the parameter, 22003 should the
            # server be the one to say so.
            if not str(getattr(error.orig, "sqlstate", "")).startswith("22"):
                raise
            await db.rollback()
            raise ApiError(422, "LIVE_VERSIONS_KEPT_TOO_LARGE") from None
        await _audit(
            request,
            member,
            "site_live_versions_kept",
            {"group": group_slug, "site": site_slug, "live_versions_kept": kept},
        )
        return await _one_site_json(db, group, site)

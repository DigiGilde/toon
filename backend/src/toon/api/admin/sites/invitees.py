"""The invitee list of a site: who may read it after signing in."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import delete, select
from starlette.responses import Response

from toon.api.admin.common import _timestamp_schema, _validate_text
from toon.api.admin.contract import _ERROR_CSRF, _ERROR_SITE, _ERROR_SITE_ROLE, _deleted, _errors
from toon.api.admin.dependencies import ActiveMember, Csrf, Db
from toon.api.admin.lookup import _audit, _commit_or_409, _site_with_role, _write_one_or_404
from toon.api.admin.schemas import IdentifierBody
from toon.api.docs import TAG_INVITEES
from toon.api.schema import ApiModel, iso_utc
from toon.constants import Role
from toon.models.publication import Invitee


class InviteeOut(ApiModel):
    """An address on the invitee list of a site."""

    id: str = Field(
        description="ID of this invitee; you use it to remove them from the list.",
        examples=["3f2a1c6e-9b4d-4f2a-8c1e-7d5b2a9f4c31"],
    )
    site_slug: str = Field(description="Slug of the site.", examples=["docs"])
    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    identifier: str = Field(
        description="E-mail address or SSO subject of the invitee, normalised to lowercase.",
        examples=["genodigde@example.nl"],
    )
    added_by: str = Field(
        description="ID of the member who added the invitee; empty if that member has since been deleted."
    )
    added_at: str | None = Field(
        default=None, description="When the invitee was added.", json_schema_extra=_timestamp_schema()
    )


def _invitee_json(invitee: Invitee, group_slug: str, site_slug: str) -> InviteeOut:
    return InviteeOut(
        id=str(invitee.id),
        site_slug=site_slug,
        group_slug=group_slug,
        identifier=invitee.identifier,
        added_by=str(invitee.added_by) if invitee.added_by else "",
        added_at=iso_utc(invitee.added_at),
    )


def add_routes(router: APIRouter) -> None:
    # -- Invitees --

    @router.get(
        "/sites/{group_slug}/{site_slug}/invitees",
        tags=[TAG_INVITEES],
        summary="Invitees of a site",
        response_description="The invitees, sorted by identifier.",
        description=(
            "The addresses that may see this site while the `invitees` exception is on. While it is off, "
            "the list is kept but has no effect.\n\n"
            "**Who can call this:** effective site role `editor` or higher. This list holds e-mail addresses of "
            "external people and therefore sits above `reader`."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def invitees(group_slug: str, site_slug: str, member: ActiveMember, db: Db) -> list[InviteeOut]:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.EDITOR)
        rows = await db.scalars(
            select(Invitee).where(Invitee.site_id == site.id).order_by(Invitee.identifier)
        )
        return [_invitee_json(invitee, group_slug, site_slug) for invitee in rows]

    @router.post(
        "/sites/{group_slug}/{site_slug}/invitees",
        status_code=201,
        tags=[TAG_INVITEES],
        summary="Add an invitee",
        response_description="The added invitee.",
        description=(
            "Puts an address on the invitee list. The identifier is normalised to lowercase; "
            "the invitee does not need to have an account yet, but must be able to sign in through SSO.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {409: "This address is already on the invitee list (`INVITEE_EXISTS`)."},
            {422: "The identifier is empty (`FIELD_EMPTY`)."},
        ),
    )
    async def add_invitee(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: IdentifierBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> InviteeOut:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        identifier = _validate_text(body.identifier, "identifier").lower()
        invitee = Invitee(site_id=site.id, identifier=identifier, added_by=member.id)
        db.add(invitee)
        await _commit_or_409(db, "INVITEE_EXISTS", params={"identifier": identifier})
        await _audit(
            request, member, "invitee_add", {"group": group_slug, "site": site_slug}
        )
        return _invitee_json(invitee, group_slug, site_slug)

    @router.delete(
        "/sites/{group_slug}/{site_slug}/invitees/{invitee_id}",
        status_code=204,
        tags=[TAG_INVITEES],
        summary="Remove an invitee",
        description=(
            "Removes an address from the invitee list. An ID that does not belong to this site returns 404. The "
            "path takes the invitee's `id` from the list, not the address itself: an address in a URL "
            "ends up in the log lines of every proxy in between.\n\n"
            "**Who can call this:** effective site role `editor` or higher, with a valid CSRF header."
        ),
        responses=_deleted("The invitee has been removed from the list.")
        | _errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {404: "This invitee is not on the list of this site (`UNKNOWN_INVITEE`)."},
        ),
    )
    async def remove_invitee(
        request: Request,
        group_slug: str,
        site_slug: str,
        invitee_id: uuid.UUID,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> Response:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.EDITOR)
        await _write_one_or_404(
            db,
            delete(Invitee).where(Invitee.site_id == site.id, Invitee.id == invitee_id).returning(Invitee.id),
            "UNKNOWN_INVITEE",
        )
        await _audit(
            request, member, "invitee_remove", {"group": group_slug, "site": site_slug}
        )
        return Response(status_code=204)

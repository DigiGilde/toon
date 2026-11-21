"""Notifications of new feedback in the admin API: a member's own email
choice per site, per group and on the profile, and the Mattermost webhook of a
site or a group (docs/design.md §16.16).

Session only, CSRF on every write. A webhook URL is a secret: it goes in and
never comes out. A read says whether one is set and on which host, the audit
log records the host and never the URL, and a refusal never quotes it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from toon.api.admin.contract import (
    _ERROR_CSRF,
    _ERROR_GROUP,
    _ERROR_GROUP_ROLE,
    _ERROR_SITE,
    _ERROR_SITE_ROLE,
    _deleted,
    _errors,
)
from toon.api.admin.dependencies import ActiveMember, Csrf, Db
from toon.api.admin.lookup import _audit, _group_with_role, _site_with_role
from toon.api.docs import TAG_FEEDBACK
from toon.api.errors import ApiError, locale_from_request
from toon.api.schema import ApiModel, SessionBody, iso_utc
from toon.audit import vocabulary
from toon.config import Settings
from toon.constants import Role
from toon.models.identity import Member, MemberLanguage
from toon.models.notifications import WEBHOOK_URL_MAX, FeedbackWebhook, GroupFeedbackEmail, SiteFeedbackEmail
from toon.models.publication import Site
from toon.notifications.recipients import Source, resolve
from toon.notifications.webhooks import WebhookUrlError, check_url


class EmailChoice(SessionBody):
    """A member's own choice about email on new feedback, at one level."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"enabled": True}, {"enabled": None}]})

    enabled: bool | None = Field(
        description=(
            "`true` for email, `false` for none, `null` to leave it to the wider level: the group for a site, "
            "the profile for a group. On the profile, `null` is the same as `false`."
        ),
        examples=[True],
    )


class FeedbackEmailSetting(ApiModel):
    """A member's email choice at one level, and what applies there."""

    available: bool = Field(
        description=(
            "Whether this installation sends email about new feedback at all (`TOON_SMTP_HOST`). When it is "
            "`false`, a choice can be read but not changed."
        ),
        examples=[True],
    )
    enabled: bool | None = Field(
        description="The choice at this level itself: `true`, `false`, or `null` when nothing is set here.",
        examples=[None],
    )
    effective: bool = Field(
        description="Whether the member gets email at this level, after the narrower and wider levels.",
        examples=[True],
    )
    source: Source = Field(
        description=(
            "Which level decides `effective`: `site`, `group`, `profile`, or `default` when nothing is set "
            "anywhere, which means no email."
        ),
        examples=["group"],
    )


class WebhookBody(SessionBody):
    """A Mattermost incoming webhook. The URL is a secret: Toon never shows it again."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"url": "https://mattermost.example.org/hooks/xn1x3s7n6pnqbbz8m9zjqsn5rw", "language": "nl"},
                {"url": "https://mattermost.example.org/hooks/xn1x3s7n6pnqbbz8m9zjqsn5rw"},
            ]
        }
    )

    url: str = Field(
        description=(
            "The URL of the incoming webhook, as Mattermost gives it: `https://`, a host the operator allows "
            "(`TOON_FEEDBACK_WEBHOOK_HOSTS`), no user name or password, no IP address. "
            f"At most {WEBHOOK_URL_MAX} characters."
        ),
        examples=["https://mattermost.example.org/hooks/xn1x3s7n6pnqbbz8m9zjqsn5rw"],
    )
    language: MemberLanguage | None = Field(
        default=None,
        description=(
            "The language of the posts: `nl` or `en`. Without it, the language of the member who sets the "
            "webhook: their own choice, else the language of the request."
        ),
        examples=["nl"],
    )


class FeedbackWebhookOut(ApiModel):
    """Whether a site or a group has a Mattermost webhook. Never the URL."""

    available: bool = Field(
        description=(
            "Whether this installation posts to Mattermost at all: the operator allows at least one host "
            "(`TOON_FEEDBACK_WEBHOOK_HOSTS`)."
        ),
        examples=[True],
    )
    configured: bool = Field(description="Whether this site or group has a webhook of its own.", examples=[True])
    host: str | None = Field(description="The host of its webhook, or `null`.", examples=["mattermost.example.org"])
    language: MemberLanguage | None = Field(
        description="The language of the posts, or `null` without a webhook.", examples=["nl"]
    )
    set_at: str | None = Field(
        description="When the webhook was set, in UTC (RFC 3339), or `null`.", examples=["2026-11-21T10:15:00Z"]
    )
    group_host: str | None = Field(
        default=None,
        description=(
            "On a site only: the host of the group's webhook, which a site without one of its own posts to. "
            "`null` on a group, and when the group has none."
        ),
        examples=[None],
    )


_ERROR_EMAIL_UNAVAILABLE = {409: "This installation sends no email about new feedback (`FEEDBACK_EMAIL_UNAVAILABLE`)."}
_ERROR_WEBHOOKS_UNAVAILABLE = {409: "This installation allows no Mattermost webhooks (`WEBHOOKS_UNAVAILABLE`)."}
_ERROR_WEBHOOK_URL = {
    422: (
        "The URL is refused (`WEBHOOK_URL_INVALID`): it is not `https`, holds a user name or password, names "
        "an IP address, has a port the operator does not allow, is longer than "
        f"{WEBHOOK_URL_MAX} characters, or is no URL; or its host is not one the operator allows "
        "(`WEBHOOK_HOST_NOT_ALLOWED`). The answer never repeats the URL."
    )
}
_EMAIL_LEVELS = (
    "A member gets email about new feedback on a site when the narrowest level they set says so: the site, "
    "else its group, else their profile. Nothing set anywhere is no email. Toon decides when it sends: the "
    "member must still be active, still have a role on the site, and have an e-mail address their identity "
    "provider verified. An email names the site, the page, the kind, the time and whether a screenshot came "
    "with it, and links to the item; it never holds the comment or who sent it."
)
_WEBHOOK_RULE = (
    "A post carries the comment itself, cut at 500 characters, never the name or e-mail address of who sent "
    "it. What lands in the channel stays there, also after the item is deleted in Toon."
)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _email_out(
    request: Request, enabled: bool | None, *, site: bool | None, group: bool | None, profile: bool | None
) -> FeedbackEmailSetting:
    resolution = resolve(site, group, profile)
    return FeedbackEmailSetting(
        available=_settings(request).email_available,
        enabled=enabled,
        effective=resolution.enabled,
        source=resolution.source,
    )


def _require_email(request: Request) -> None:
    if not _settings(request).email_available:
        raise ApiError(409, "FEEDBACK_EMAIL_UNAVAILABLE")


async def _group_choice(db: AsyncSession, group_id: uuid.UUID, member: Member) -> bool | None:
    return await db.scalar(
        select(GroupFeedbackEmail.enabled).where(
            GroupFeedbackEmail.group_id == group_id, GroupFeedbackEmail.member_id == member.id
        )
    )


async def _site_choice(db: AsyncSession, site: Site, member: Member) -> bool | None:
    return await db.scalar(
        select(SiteFeedbackEmail.enabled).where(
            SiteFeedbackEmail.site_id == site.id, SiteFeedbackEmail.member_id == member.id
        )
    )


async def _store_choice(
    db: AsyncSession, model: type[GroupFeedbackEmail] | type[SiteFeedbackEmail], owner: dict, enabled: bool | None
) -> None:
    """Writes or clears the choice at the group or site level `owner` names."""
    if enabled is None:
        await db.execute(delete(model).where(*(getattr(model, key) == value for key, value in owner.items())))
    else:
        statement = insert(model).values(**owner, enabled=enabled)
        await db.execute(
            statement.on_conflict_do_update(index_elements=list(owner), set_={"enabled": statement.excluded.enabled})
        )
    await db.commit()


async def _site_email_out(request: Request, db: AsyncSession, site: Site, member: Member) -> FeedbackEmailSetting:
    own = await _site_choice(db, site, member)
    return _email_out(
        request,
        own,
        site=own,
        group=await _group_choice(db, site.group_id, member),
        profile=member.feedback_email,
    )


async def _group_email_out(
    request: Request, db: AsyncSession, group_id: uuid.UUID, member: Member
) -> FeedbackEmailSetting:
    own = await _group_choice(db, group_id, member)
    return _email_out(request, own, site=None, group=own, profile=member.feedback_email)


def _profile_email_out(request: Request, member: Member) -> FeedbackEmailSetting:
    own = member.feedback_email
    return _email_out(request, own, site=None, group=None, profile=own)


async def _hook(db: AsyncSession, column, owner: uuid.UUID) -> FeedbackWebhook | None:
    return await db.scalar(select(FeedbackWebhook).where(column == owner))


def _webhook_out(
    request: Request, hook: FeedbackWebhook | None, group_hook: FeedbackWebhook | None = None
) -> FeedbackWebhookOut:
    return FeedbackWebhookOut(
        available=bool(_settings(request).webhook_hosts),
        configured=hook is not None,
        host=hook.host if hook else None,
        language=hook.language if hook else None,
        set_at=iso_utc(hook.set_at) if hook else None,
        group_host=group_hook.host if group_hook else None,
    )


async def _store_hook(
    request: Request, db: AsyncSession, member: Member, body: WebhookBody, key: str, owner: uuid.UUID
) -> tuple[str, MemberLanguage]:
    """Checks the URL and writes the webhook of the group or site `owner`.
    Returns the host and the language, for the audit row."""
    settings = _settings(request)
    if not settings.webhook_hosts:
        raise ApiError(409, "WEBHOOKS_UNAVAILABLE")
    try:
        target = check_url(body.url, settings.webhook_hosts)
    except WebhookUrlError as refusal:
        raise ApiError(422, refusal.key) from None
    language = body.language or member.language or MemberLanguage(locale_from_request(request))
    values = {"url": body.url, "host": target.host, "language": language, "set_by": member.id}
    statement = insert(FeedbackWebhook).values(id=uuid.uuid4(), **{key: owner}, **values, set_at=datetime.now(UTC))
    await db.execute(
        statement.on_conflict_do_update(index_elements=[key], set_={**values, "set_at": statement.excluded.set_at})
    )
    await db.commit()
    return target.host, language


async def _remove_hook(db: AsyncSession, column, owner: uuid.UUID) -> bool:
    removed = await db.execute(delete(FeedbackWebhook).where(column == owner).returning(FeedbackWebhook.id))
    found = removed.first() is not None
    await db.commit()
    return found


def add_routes(router: APIRouter) -> None:
    # -- Email: the profile --

    @router.get(
        "/me/feedback-email",
        tags=[TAG_FEEDBACK],
        summary="My email choice for new feedback, on my profile",
        response_description="The choice on the profile.",
        description=(
            "The widest level of the member's own choice: email about new feedback on every site they may read, "
            "unless a group or a site says otherwise.\n\n"
            f"{_EMAIL_LEVELS}\n\n"
            "**Who can call this:** any active member, for themselves."
        ),
        responses=_errors(),
    )
    async def my_feedback_email(request: Request, member: ActiveMember) -> FeedbackEmailSetting:
        return _profile_email_out(request, member)

    @router.put(
        "/me/feedback-email",
        tags=[TAG_FEEDBACK],
        summary="Set my email choice for new feedback, on my profile",
        response_description="The choice on the profile, as stored.",
        description=(
            "Sets the widest level of the member's own choice.\n\n"
            f"{_EMAIL_LEVELS}\n\n"
            "**Who can call this:** any active member, for themselves, with a valid CSRF header."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_EMAIL_UNAVAILABLE),
    )
    async def set_my_feedback_email(
        request: Request, body: EmailChoice, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> FeedbackEmailSetting:
        _require_email(request)
        member.feedback_email = body.enabled
        await db.commit()
        await _audit(request, member, vocabulary.FEEDBACK_EMAIL, {"level": "profile", "enabled": body.enabled})
        return _profile_email_out(request, member)

    # -- Email: a group --

    @router.get(
        "/groups/{group_slug}/feedback-email",
        tags=[TAG_FEEDBACK],
        summary="My email choice for new feedback, on a group",
        response_description="The choice on this group, and what applies.",
        description=(
            "The member's own choice for every site of this group where they set nothing on the site itself.\n\n"
            f"{_EMAIL_LEVELS}\n\n"
            "**Who can call this:** any active member with a role in the group."
        ),
        responses=_errors(_ERROR_GROUP_ROLE, _ERROR_GROUP),
    )
    async def group_feedback_email(
        request: Request, group_slug: str, member: ActiveMember, db: Db
    ) -> FeedbackEmailSetting:
        group = await _group_with_role(db, member, group_slug, Role.READER)
        return await _group_email_out(request, db, group.id, member)

    @router.put(
        "/groups/{group_slug}/feedback-email",
        tags=[TAG_FEEDBACK],
        summary="Set my email choice for new feedback, on a group",
        response_description="The choice on this group, as stored, and what applies.",
        description=(
            "Sets or clears the member's own choice for the sites of this group.\n\n"
            f"{_EMAIL_LEVELS}\n\n"
            "**Who can call this:** any active member with a role in the group, for themselves, with a valid "
            "CSRF header."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_GROUP_ROLE, _ERROR_GROUP, _ERROR_EMAIL_UNAVAILABLE),
    )
    async def set_group_feedback_email(
        request: Request, group_slug: str, body: EmailChoice, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> FeedbackEmailSetting:
        group = await _group_with_role(db, member, group_slug, Role.READER)
        _require_email(request)
        await _store_choice(db, GroupFeedbackEmail, {"group_id": group.id, "member_id": member.id}, body.enabled)
        await _audit(
            request,
            member,
            vocabulary.FEEDBACK_EMAIL,
            {"level": "group", "group": group_slug, "enabled": body.enabled},
        )
        return await _group_email_out(request, db, group.id, member)

    # -- Email: a site --

    @router.get(
        "/sites/{group_slug}/{site_slug}/feedback-email",
        tags=[TAG_FEEDBACK],
        summary="My email choice for new feedback, on a site",
        response_description="The choice on this site, and what applies.",
        description=(
            "The member's own choice for this one site, which beats the group and the profile.\n\n"
            f"{_EMAIL_LEVELS}\n\n"
            "**Who can call this:** any active member who may read the site's feedback: effective site role "
            "`reader` or more."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def site_feedback_email(
        request: Request, group_slug: str, site_slug: str, member: ActiveMember, db: Db
    ) -> FeedbackEmailSetting:
        _group, site = await _site_with_role(db, member, group_slug, site_slug, Role.READER)
        return await _site_email_out(request, db, site, member)

    @router.put(
        "/sites/{group_slug}/{site_slug}/feedback-email",
        tags=[TAG_FEEDBACK],
        summary="Set my email choice for new feedback, on a site",
        response_description="The choice on this site, as stored, and what applies.",
        description=(
            "Sets or clears the member's own choice for this one site.\n\n"
            f"{_EMAIL_LEVELS}\n\n"
            "**Who can call this:** any active member who may read the site's feedback (effective site role "
            "`reader` or more), for themselves, with a valid CSRF header."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_SITE_ROLE, _ERROR_SITE, _ERROR_EMAIL_UNAVAILABLE),
    )
    async def set_site_feedback_email(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: EmailChoice,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> FeedbackEmailSetting:
        _group, site = await _site_with_role(db, member, group_slug, site_slug, Role.READER)
        _require_email(request)
        await _store_choice(db, SiteFeedbackEmail, {"site_id": site.id, "member_id": member.id}, body.enabled)
        await _audit(
            request,
            member,
            vocabulary.FEEDBACK_EMAIL,
            {"level": "site", "group": group_slug, "site": site_slug, "enabled": body.enabled},
        )
        return await _site_email_out(request, db, site, member)

    # -- Mattermost: a group --

    @router.get(
        "/groups/{group_slug}/feedback-webhook",
        tags=[TAG_FEEDBACK],
        summary="The Mattermost webhook of a group",
        response_description="Whether the group has one, and on which host. Never the URL.",
        description=(
            "Toon posts new feedback of a site to its own webhook, else to its group's. This says whether the "
            "group has one, on which host and in which language. The URL itself is never returned.\n\n"
            f"{_WEBHOOK_RULE}\n\n"
            "**Who can call this:** group role `admin`."
        ),
        responses=_errors(_ERROR_GROUP_ROLE, _ERROR_GROUP),
    )
    async def group_feedback_webhook(
        request: Request, group_slug: str, member: ActiveMember, db: Db
    ) -> FeedbackWebhookOut:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN)
        return _webhook_out(request, await _hook(db, FeedbackWebhook.group_id, group.id))

    @router.put(
        "/groups/{group_slug}/feedback-webhook",
        tags=[TAG_FEEDBACK],
        summary="Set the Mattermost webhook of a group",
        response_description="The group's webhook as stored, without the URL.",
        description=(
            "Sets or replaces the incoming webhook every site of this group without one of its own posts new "
            "feedback to. Toon checks the URL against the hosts the operator allows, now and before every "
            "post.\n\n"
            f"{_WEBHOOK_RULE}\n\n"
            "**Who can call this:** group role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF, _ERROR_GROUP_ROLE, _ERROR_GROUP, _ERROR_WEBHOOKS_UNAVAILABLE, _ERROR_WEBHOOK_URL
        ),
    )
    async def set_group_feedback_webhook(
        request: Request, group_slug: str, body: WebhookBody, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> FeedbackWebhookOut:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN)
        host, language = await _store_hook(request, db, member, body, "group_id", group.id)
        await _audit(
            request,
            member,
            "group_feedback_webhook",
            {"group": group_slug, "host": host, "language": language.value, "set": True},
        )
        return _webhook_out(request, await _hook(db, FeedbackWebhook.group_id, group.id))

    @router.delete(
        "/groups/{group_slug}/feedback-webhook",
        status_code=204,
        tags=[TAG_FEEDBACK],
        summary="Remove the Mattermost webhook of a group",
        description=(
            "Toon stops posting to the group's webhook; a site with a webhook of its own goes on posting there. "
            "What is already in the channel stays. Removing a webhook that is not there changes nothing.\n\n"
            "**Who can call this:** group role `admin`, with a valid CSRF header."
        ),
        responses=_deleted("The group has no webhook.") | _errors(_ERROR_CSRF, _ERROR_GROUP_ROLE, _ERROR_GROUP),
    )
    async def remove_group_feedback_webhook(
        request: Request, group_slug: str, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN)
        if await _remove_hook(db, FeedbackWebhook.group_id, group.id):
            await _audit(request, member, "group_feedback_webhook", {"group": group_slug, "set": False})
        return Response(status_code=204)

    # -- Mattermost: a site --

    @router.get(
        "/sites/{group_slug}/{site_slug}/feedback-webhook",
        tags=[TAG_FEEDBACK],
        summary="The Mattermost webhook of a site",
        response_description="Whether the site and its group have one, and on which host. Never the URL.",
        description=(
            "Toon posts new feedback of this site to its own webhook, else to its group's. This says whether "
            "the site has one and whether the group has one, with their hosts. The URL itself is never "
            "returned.\n\n"
            f"{_WEBHOOK_RULE}\n\n"
            "**Who can call this:** effective site role `admin`."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def site_feedback_webhook(
        request: Request, group_slug: str, site_slug: str, member: ActiveMember, db: Db
    ) -> FeedbackWebhookOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        return _webhook_out(
            request,
            await _hook(db, FeedbackWebhook.site_id, site.id),
            await _hook(db, FeedbackWebhook.group_id, group.id),
        )

    @router.put(
        "/sites/{group_slug}/{site_slug}/feedback-webhook",
        tags=[TAG_FEEDBACK],
        summary="Set the Mattermost webhook of a site",
        response_description="The site's webhook as stored, without the URL.",
        description=(
            "Sets or replaces the incoming webhook this site posts new feedback to, instead of its group's. "
            "Toon checks the URL against the hosts the operator allows, now and before every post.\n\n"
            f"{_WEBHOOK_RULE}\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF, _ERROR_SITE_ROLE, _ERROR_SITE, _ERROR_WEBHOOKS_UNAVAILABLE, _ERROR_WEBHOOK_URL
        ),
    )
    async def set_site_feedback_webhook(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: WebhookBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> FeedbackWebhookOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        host, language = await _store_hook(request, db, member, body, "site_id", site.id)
        await _audit(
            request,
            member,
            "site_feedback_webhook",
            {"group": group_slug, "site": site_slug, "host": host, "language": language.value, "set": True},
        )
        return _webhook_out(
            request,
            await _hook(db, FeedbackWebhook.site_id, site.id),
            await _hook(db, FeedbackWebhook.group_id, group.id),
        )

    @router.delete(
        "/sites/{group_slug}/{site_slug}/feedback-webhook",
        status_code=204,
        tags=[TAG_FEEDBACK],
        summary="Remove the Mattermost webhook of a site",
        description=(
            "Toon stops posting to the site's own webhook and posts to its group's instead, if the group has "
            "one. What is already in the channel stays. Removing a webhook that is not there changes nothing.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_deleted("The site has no webhook of its own.")
        | _errors(_ERROR_CSRF, _ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def remove_site_feedback_webhook(
        request: Request, group_slug: str, site_slug: str, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        _group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        if await _remove_hook(db, FeedbackWebhook.site_id, site.id):
            await _audit(
                request, member, "site_feedback_webhook", {"group": group_slug, "site": site_slug, "set": False}
            )
        return Response(status_code=204)

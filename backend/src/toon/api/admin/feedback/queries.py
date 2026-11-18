"""Reading feedback for the admin API: the item query and how a row becomes an answer."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from fastapi import Request
from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from toon.access import roles
from toon.api.admin import _FEEDBACK_OPEN, _content_base
from toon.api.admin.feedback.schemas import (
    FeedbackAuthorOut,
    FeedbackCurrentVersion,
    FeedbackDetail,
    FeedbackLocation,
    FeedbackReply,
    FeedbackReplyAuthorOut,
)
from toon.api.errors import ApiError
from toon.api.schema import iso_utc
from toon.constants import FeedbackStatus, Role
from toon.models.feedback import FeedbackItem as FeedbackItemRow
from toon.models.feedback import FeedbackReply as FeedbackReplyRow
from toon.models.feedback import FeedbackScreenshot
from toon.models.identity import Group, Member
from toon.models.publication import Preview, Site, Version, VersionTarget
from toon.serving.feedback_api import highlight_anchor

# What each value of the list's `status` filter lets through. None is all.
_FEEDBACK_STATUS_FILTER: dict[str, tuple[FeedbackStatus, ...] | None] = {
    "open": _FEEDBACK_OPEN,
    "new": (FeedbackStatus.NEW,),
    "seen": (FeedbackStatus.SEEN,),
    "resolved": (FeedbackStatus.RESOLVED,),
    "all": None,
}

# Newest first with the id behind it, for the cursor, as _AUDIT_ORDER.
_FEEDBACK_ORDER = (FeedbackItemRow.created_at.desc(), FeedbackItemRow.id.desc())

# A stored page path is percent-encoded already: '%' and the characters a
# path may hold (RFC 3986) stay as they are, anything else is encoded rather
# than ending the path, a '?' or '#' above all.
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"

# The widget reads the highlight fragment only when its JSON is at most this
# many bytes.
_HIGHLIGHT_PREFIX = "toon-feedback="
_HIGHLIGHT_MAX_JSON_BYTES = 2048


def _feedback_select() -> Select:
    """Feedback items, each with the member who last changed its status, its
    number of replies, whether it has a screenshot, without reading the
    bytes of one, and the version its live site or preview shows now."""
    reply_count = (
        select(func.count())
        .select_from(FeedbackReplyRow)
        .where(FeedbackReplyRow.item_id == FeedbackItemRow.id)
        .scalar_subquery()
    )
    has_screenshot = (
        select(FeedbackScreenshot.item_id).where(FeedbackScreenshot.item_id == FeedbackItemRow.id).exists()
    )
    current = aliased(Version)
    return (
        select(FeedbackItemRow, Member, reply_count, has_screenshot, current.id, current.created_at)
        .outerjoin(Member, Member.id == FeedbackItemRow.status_changed_by)
        .join(Site, Site.id == FeedbackItemRow.site_id)
        .outerjoin(
            Preview,
            and_(
                FeedbackItemRow.target == VersionTarget.PREVIEW,
                Preview.site_id == FeedbackItemRow.site_id,
                Preview.ref == FeedbackItemRow.preview_ref,
                or_(Preview.expires_at.is_(None), Preview.expires_at > datetime.now(UTC)),
            ),
        )
        .outerjoin(
            current,
            current.id
            == case((FeedbackItemRow.target == VersionTarget.LIVE, Site.live_version_id), else_=Preview.version_id),
        )
    )


def _feedback_page_url(request: Request, group: Group, site: Site, item: FeedbackItemRow) -> str:
    """The page of an item at the current address of its site."""
    root = f"{_content_base(request)}/{group.slug}/{site.slug}/"
    if item.target == VersionTarget.PREVIEW:
        root += f"_preview/{quote(item.preview_ref or '', safe='')}/"
    return root + quote(item.page_path, safe=_PATH_SAFE)


def _highlight_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("ascii")


def _highlight_fragment(encoded: bytes) -> str:
    return _HIGHLIGHT_PREFIX + base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _highlight_url(page_url: str, item: FeedbackItemRow) -> str | None:
    """The page with the highlight fragment of the item, or None when the
    item has no place on the page to show."""
    payload = highlight_anchor(item.kind, item.anchor)
    if payload is None:
        return None
    quoted = payload.pop("q", None)
    # A selector too long for the fragment is left out rather than cut: cut
    # short it can name another element. The quote can be cut, because the
    # widget searches the page for it and its start is still there.
    if len(_highlight_json(payload)) > _HIGHLIGHT_MAX_JSON_BYTES:
        payload.pop("s", None)
    while quoted:
        encoded = _highlight_json({**payload, "q": quoted})
        if len(encoded) <= _HIGHLIGHT_MAX_JSON_BYTES:
            return f"{page_url}#{_highlight_fragment(encoded)}"
        quoted = quoted[: len(quoted) // 2]
    return f"{page_url}#{_highlight_fragment(_highlight_json(payload))}"


def _feedback_fields(
    request: Request, group: Group, site: Site, row: Any, *, show_email: bool
) -> dict[str, Any]:
    """What a FeedbackItem and a FeedbackDetail share, from a row of
    _feedback_select()."""
    item, changed_by, reply_count, has_screenshot, current_id, current_created_at = row
    return {
        "id": item.id,
        "kind": item.kind,
        "status": item.status,
        "comment": item.comment,
        "quote": item.quote,
        "page": FeedbackLocation(
            path=item.page_path, title=item.page_title, url=_feedback_page_url(request, group, site, item)
        ),
        "target": item.target,
        "preview_ref": item.preview_ref,
        "version_id": item.version_id,
        "current_version": (
            None
            if current_id is None
            else FeedbackCurrentVersion(id=current_id, created_at=iso_utc(current_created_at))
        ),
        "author": FeedbackAuthorOut(
            kind=item.author_kind, name=item.author_name, email=item.author_email if show_email else None
        ),
        "has_screenshot": has_screenshot,
        "reply_count": reply_count,
        "created_at": iso_utc(item.created_at),
        "status_changed_at": iso_utc(item.status_changed_at),
        "status_changed_by": (changed_by.name or changed_by.email) if changed_by is not None else None,
    }


def _feedback_reply_json(reply: FeedbackReplyRow, name: str | None) -> FeedbackReply:
    return FeedbackReply(
        id=reply.id,
        author=FeedbackReplyAuthorOut(kind=reply.author_kind, name=name),
        body=reply.body,
        created_at=iso_utc(reply.created_at),
    )


async def _feedback_detail(
    request: Request, db: AsyncSession, group: Group, site: Site, item_id: uuid.UUID, *, show_email: bool
) -> FeedbackDetail:
    row = (
        await db.execute(
            _feedback_select().where(FeedbackItemRow.site_id == site.id, FeedbackItemRow.id == item_id)
        )
    ).one_or_none()
    if row is None:
        raise ApiError(404, "FEEDBACK_NOT_FOUND")
    fields = _feedback_fields(request, group, site, row, show_email=show_email)
    item = row[0]
    replies = await db.execute(
        select(FeedbackReplyRow, Member.name)
        .outerjoin(Member, Member.id == FeedbackReplyRow.member_id)
        .where(FeedbackReplyRow.item_id == item.id)
        .order_by(FeedbackReplyRow.created_at, FeedbackReplyRow.id)
    )
    return FeedbackDetail(
        **fields,
        anchor=item.anchor,
        viewport=item.viewport,
        highlight_url=_highlight_url(fields["page"].url, item),
        replies=[_feedback_reply_json(reply, name) for reply, name in replies],
    )


async def _sees_submitter_email(db: AsyncSession, site: Site, member: Member) -> bool:
    """The e-mail addresses of submitters are for the editors and admins of
    the site, not for its readers."""
    return roles.at_least(await roles.effective_site_role(db, site, member.id), Role.EDITOR)

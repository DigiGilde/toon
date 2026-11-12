"""Site feedback models: feedback_items, feedback_replies, feedback_screenshots.

An item hangs on its site and goes with it. The version it was given on is a
hint only (ON DELETE SET NULL), so feedback outlives preview teardown,
redeploys and the cleanup of old live versions. The CHECKs below mirror
migration 0008_feedback, which is what the database enforces.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, LargeBinary, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from toon.constants import FeedbackAuthor, FeedbackKind, FeedbackReplyAuthor, FeedbackStatus
from toon.models.base import Base, IDMixin
from toon.models.publication import VersionTarget


class FeedbackItem(IDMixin, Base):
    """One remark sent through the widget. What it says about its author sits
    in `author_kind` and the three author columns alone: an item sent without
    a name has all three empty."""

    __tablename__ = "feedback_items"

    site_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    # The type belongs to versions.target (0001_base); this column only uses it.
    target: Mapped[VersionTarget] = mapped_column(
        postgresql.ENUM(
            VersionTarget,
            name="version_target",
            values_callable=lambda c: [member.value for member in c],
            create_type=False,
        ),
        nullable=False,
    )
    preview_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Written through `(SELECT id FROM versions WHERE id = :ver AND site_id =
    # :site)` inside the INSERT: a version removed after the page view then
    # gives NULL instead of a foreign key error.
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("versions.id", ondelete="SET NULL"), nullable=True
    )
    # Relative to the root of the target, without a leading slash; '' is the root.
    page_path: Mapped[str] = mapped_column(Text, nullable=False)
    page_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[FeedbackKind] = mapped_column(
        SAEnum(FeedbackKind, name="feedback_kind", values_callable=lambda c: [member.value for member in c]),
        nullable=False,
    )
    # none_as_null: a Python None must become SQL NULL. JSON null would fail
    # the anchor and viewport CHECK constraints below.
    anchor: Mapped[dict | None] = mapped_column(postgresql.JSONB(none_as_null=True), nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    author_kind: Mapped[FeedbackAuthor] = mapped_column(
        SAEnum(FeedbackAuthor, name="feedback_author", values_callable=lambda c: [member.value for member in c]),
        nullable=False,
    )
    author_sub: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    viewport: Mapped[dict | None] = mapped_column(postgresql.JSONB(none_as_null=True), nullable=True)
    status: Mapped[FeedbackStatus] = mapped_column(
        SAEnum(FeedbackStatus, name="feedback_status", values_callable=lambda c: [member.value for member in c]),
        nullable=False,
        server_default=FeedbackStatus.NEW.value,
        default=FeedbackStatus.NEW,
    )
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_changed_by: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("(target = 'preview') = (preview_ref IS NOT NULL)", name="ck_feedback_items_preview_ref"),
        CheckConstraint(
            "char_length(page_path) <= 2048 AND left(page_path, 1) <> '/'", name="ck_feedback_items_page_path"
        ),
        CheckConstraint("char_length(page_title) <= 300", name="ck_feedback_items_page_title_length"),
        CheckConstraint("(kind = 'page') = (anchor IS NULL)", name="ck_feedback_items_anchor"),
        CheckConstraint("(kind = 'text') = (quote IS NOT NULL)", name="ck_feedback_items_quote"),
        CheckConstraint("char_length(quote) <= 1000", name="ck_feedback_items_quote_length"),
        CheckConstraint("char_length(comment) BETWEEN 1 AND 5000", name="ck_feedback_items_comment_length"),
        CheckConstraint(
            "(author_kind = 'account' AND author_sub IS NOT NULL) OR "
            "(author_kind = 'name' AND author_name IS NOT NULL AND author_sub IS NULL AND author_email IS NULL) OR "
            "(author_kind = 'anonymous' AND author_sub IS NULL AND author_name IS NULL AND author_email IS NULL)",
            name="ck_feedback_items_author",
        ),
        CheckConstraint("char_length(author_name) <= 200", name="ck_feedback_items_author_name_length"),
        CheckConstraint("author_email = lower(author_email)", name="ck_feedback_items_author_email_lowercase"),
        CheckConstraint(
            "viewport IS NULL OR viewport = jsonb_build_object("
            "'width', viewport -> 'width', 'height', viewport -> 'height', 'dpr', viewport -> 'dpr')",
            name="ck_feedback_items_viewport",
        ),
        CheckConstraint("(status = 'resolved') = (resolved_at IS NOT NULL)", name="ck_feedback_items_resolved_at"),
    )


class FeedbackReply(IDMixin, Base):
    """A reply from the site team (`member`) or from the submitter (`giver`).
    A member reply keeps the member's name as it was when written. Neither is
    required of it: the member can be deleted (SET NULL) and can have no name."""

    __tablename__ = "feedback_replies"

    item_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("feedback_items.id", ondelete="CASCADE"), nullable=False
    )
    author_kind: Mapped[FeedbackReplyAuthor] = mapped_column(
        SAEnum(
            FeedbackReplyAuthor, name="feedback_reply_author", values_callable=lambda c: [member.value for member in c]
        ),
        nullable=False,
    )
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    author_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "author_kind <> 'giver' OR (member_id IS NULL AND author_name IS NULL)", name="ck_feedback_replies_giver"
        ),
        CheckConstraint("char_length(author_name) <= 200", name="ck_feedback_replies_author_name_length"),
        CheckConstraint("char_length(body) BETWEEN 1 AND 5000", name="ck_feedback_replies_body_length"),
    )


class FeedbackScreenshot(Base):
    """The screenshot of one item, in a table of its own so that reading items
    never reads image bytes."""

    __tablename__ = "feedback_screenshots"

    item_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("feedback_items.id", ondelete="CASCADE"), primary_key=True
    )
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # The quotas add this column up, so the database holds it to the bytes.
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "media_type IN ('image/png', 'image/jpeg', 'image/webp')", name="ck_feedback_screenshots_media_type"
        ),
        CheckConstraint(
            "size_bytes > 0 AND size_bytes = octet_length(data)", name="ck_feedback_screenshots_size_bytes"
        ),
        CheckConstraint("width > 0 AND height > 0", name="ck_feedback_screenshots_dimensions"),
    )

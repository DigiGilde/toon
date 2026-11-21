"""Notifications of new site feedback: the preferences of members, the
Mattermost webhooks, and what the notification job still has to send.

A member's email preference lives at three levels: `members.feedback_email`
(the profile), `group_feedback_email` and `site_feedback_email`. A row at the
group or site level is a choice, its absence is "not set". The narrowest
level that is set decides (docs/design.md §16.16).

`feedback_items.email_batched_at` and `mattermost_batched_at` say that an
item has gone into a batch for that channel. A notice is one message to one
destination: one email to one member, or one post to the Mattermost channel
of one site. It lists its items through `feedback_notice_items`, so an item
deleted before the message goes out drops out of it.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from toon.models.base import Base, IDMixin
from toon.models.identity import MemberLanguage

# A Mattermost incoming webhook URL is a short https address; the bound keeps
# a stored secret from growing into something else.
WEBHOOK_URL_MAX = 2048


class FeedbackChannel(enum.StrEnum):
    EMAIL = "email"
    MATTERMOST = "mattermost"


class NoticeOutcome(enum.StrEnum):
    """How a notice ended: delivered, dropped because nothing was left to send
    or nobody to send it to, or failed after its last attempt."""

    SENT = "sent"
    DROPPED = "dropped"
    FAILED = "failed"


FEEDBACK_CHANNEL_ENUM = SAEnum(
    FeedbackChannel, name="feedback_channel", values_callable=lambda c: [member.value for member in c]
)
NOTICE_OUTCOME_ENUM = SAEnum(
    NoticeOutcome, name="feedback_notice_outcome", values_callable=lambda c: [member.value for member in c]
)
# The type belongs to members.language. These columns only use it.
_MEMBER_LANGUAGE_ENUM = postgresql.ENUM(
    MemberLanguage,
    name="member_language",
    values_callable=lambda c: [member.value for member in c],
    create_type=False,
)


class GroupFeedbackEmail(Base):
    """A member's email choice for every site of one group."""

    __tablename__ = "group_feedback_email"

    group_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)


class SiteFeedbackEmail(Base):
    """A member's email choice for one site."""

    __tablename__ = "site_feedback_email"

    site_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)


class FeedbackWebhook(IDMixin, Base):
    """The Mattermost incoming webhook of one site or of one group. In a
    table of its own, so the URL, a secret, is never loaded with a site."""

    __tablename__ = "feedback_webhooks"

    group_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=True
    )
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    host: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[MemberLanguage] = mapped_column(_MEMBER_LANGUAGE_ENUM, nullable=False)
    set_by: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("group_id", name="uq_feedback_webhooks_group_id"),
        UniqueConstraint("site_id", name="uq_feedback_webhooks_site_id"),
        CheckConstraint("num_nonnulls(group_id, site_id) = 1", name="ck_feedback_webhooks_owner"),
        CheckConstraint(f"char_length(url) <= {WEBHOOK_URL_MAX}", name="ck_feedback_webhooks_url_length"),
        CheckConstraint("host = lower(host)", name="ck_feedback_webhooks_host_lowercase"),
    )


class FeedbackNotice(IDMixin, Base):
    """One message to send: an email to `member_id`, or a post to the
    Mattermost channel of `site_id`."""

    __tablename__ = "feedback_notices"

    site_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[FeedbackChannel] = mapped_column(FEEDBACK_CHANNEL_ENUM, nullable=False)
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[NoticeOutcome | None] = mapped_column(NOTICE_OUTCOME_ENUM, nullable=True)

    __table_args__ = (
        CheckConstraint("(channel = 'email') = (member_id IS NOT NULL)", name="ck_feedback_notices_recipient"),
        CheckConstraint("(finished_at IS NULL) = (outcome IS NULL)", name="ck_feedback_notices_outcome"),
        CheckConstraint("attempts >= 0", name="ck_feedback_notices_attempts"),
    )


class FeedbackNoticeItem(Base):
    __tablename__ = "feedback_notice_items"

    notice_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("feedback_notices.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("feedback_items.id", ondelete="CASCADE"), primary_key=True
    )


class FeedbackEmailDay(Base):
    """How many notification emails went out on one UTC day, across every
    replica, held to TOON_FEEDBACK_EMAIL_DAILY_MAX."""

    __tablename__ = "feedback_email_days"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cap_reached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (CheckConstraint("sent >= 0", name="ck_feedback_email_days_sent"),)

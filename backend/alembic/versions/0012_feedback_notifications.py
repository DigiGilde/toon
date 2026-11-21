"""0012_feedback_notifications: email and Mattermost about new site feedback.

A member chooses email at three levels: a column on `members` for the
profile, and a row per group or per site where they made a choice. A site or
a group may have one Mattermost incoming webhook, in a table of its own so
the URL, a secret, is never read with the site.

Two columns on `feedback_items` say when the notification job took an item
into a batch for email and for Mattermost. Every item that exists when this
migration runs counts as taken, so turning notifications on never sends the
feedback of before. A notice is one message to one destination, with its
items in `feedback_notice_items`, and `feedback_email_days` counts the
emails of a day for the daily cap.

The downgrade drops all of it: the preferences, the webhooks and whatever
was still waiting to be sent.

Revision ID: 0012_feedback_notifications
Revises: 0011_feedback
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012_feedback_notifications"
down_revision = "0011_feedback"
branch_labels = None
depends_on = None

# Hardcoded: a migration is a frozen snapshot and imports no application
# code. test_migrations.py holds these lists to the StrEnums in
# models/notifications.py, order included.
_FEEDBACK_CHANNEL_VALUES = ("email", "mattermost")
_NOTICE_OUTCOME_VALUES = ("sent", "dropped", "failed")
_MEMBER_LANGUAGE_VALUES = ("nl", "en")

_INDEXES = (
    (
        "ix_feedback_items_email_pending",
        "feedback_items",
        ["site_id", "created_at"],
        "email_batched_at IS NULL",
    ),
    (
        "ix_feedback_items_mattermost_pending",
        "feedback_items",
        ["site_id", "created_at"],
        "mattermost_batched_at IS NULL",
    ),
    ("ix_feedback_notices_due", "feedback_notices", ["next_attempt_at"], "finished_at IS NULL"),
    ("ix_feedback_notices_finished_at", "feedback_notices", ["finished_at"], None),
    ("ix_feedback_notices_site_id", "feedback_notices", ["site_id"], None),
    ("ix_feedback_notices_member_id", "feedback_notices", ["member_id"], None),
    ("ix_feedback_notice_items_item_id", "feedback_notice_items", ["item_id"], None),
    ("ix_group_feedback_email_member_id", "group_feedback_email", ["member_id"], None),
    ("ix_site_feedback_email_member_id", "site_feedback_email", ["member_id"], None),
    ("ix_feedback_webhooks_set_by", "feedback_webhooks", ["set_by"], None),
)


def _enum(*values: str, name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


def _uuid_fk(name: str, target: str, *, ondelete: str, nullable: bool, primary_key: bool = False) -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
        primary_key=primary_key,
    )


def upgrade() -> None:
    bind = op.get_bind()
    _enum(*_FEEDBACK_CHANNEL_VALUES, name="feedback_channel").create(bind, checkfirst=True)
    _enum(*_NOTICE_OUTCOME_VALUES, name="feedback_notice_outcome").create(bind, checkfirst=True)

    op.add_column("members", sa.Column("feedback_email", sa.Boolean(), nullable=True))
    op.add_column("feedback_items", sa.Column("email_batched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("feedback_items", sa.Column("mattermost_batched_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE feedback_items SET email_batched_at = now(), mattermost_batched_at = now()")

    for level, parent in (("group", "groups"), ("site", "sites")):
        op.create_table(
            f"{level}_feedback_email",
            _uuid_fk(f"{level}_id", f"{parent}.id", ondelete="CASCADE", nullable=False, primary_key=True),
            _uuid_fk("member_id", "members.id", ondelete="CASCADE", nullable=False, primary_key=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
        )

    op.create_table(
        "feedback_webhooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _uuid_fk("group_id", "groups.id", ondelete="CASCADE", nullable=True),
        _uuid_fk("site_id", "sites.id", ondelete="CASCADE", nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("language", _enum(*_MEMBER_LANGUAGE_VALUES, name="member_language"), nullable=False),
        _uuid_fk("set_by", "members.id", ondelete="SET NULL", nullable=True),
        sa.Column("set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("group_id", name="uq_feedback_webhooks_group_id"),
        sa.UniqueConstraint("site_id", name="uq_feedback_webhooks_site_id"),
        sa.CheckConstraint("num_nonnulls(group_id, site_id) = 1", name="ck_feedback_webhooks_owner"),
        sa.CheckConstraint("char_length(url) <= 2048", name="ck_feedback_webhooks_url_length"),
        sa.CheckConstraint("host = lower(host)", name="ck_feedback_webhooks_host_lowercase"),
    )

    op.create_table(
        "feedback_notices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _uuid_fk("site_id", "sites.id", ondelete="CASCADE", nullable=False),
        sa.Column("channel", _enum(*_FEEDBACK_CHANNEL_VALUES, name="feedback_channel"), nullable=False),
        _uuid_fk("member_id", "members.id", ondelete="CASCADE", nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "outcome", _enum(*_NOTICE_OUTCOME_VALUES, name="feedback_notice_outcome"), nullable=True
        ),
        sa.CheckConstraint("(channel = 'email') = (member_id IS NOT NULL)", name="ck_feedback_notices_recipient"),
        sa.CheckConstraint("(finished_at IS NULL) = (outcome IS NULL)", name="ck_feedback_notices_outcome"),
        sa.CheckConstraint("attempts >= 0", name="ck_feedback_notices_attempts"),
    )

    op.create_table(
        "feedback_notice_items",
        _uuid_fk("notice_id", "feedback_notices.id", ondelete="CASCADE", nullable=False, primary_key=True),
        _uuid_fk("item_id", "feedback_items.id", ondelete="CASCADE", nullable=False, primary_key=True),
    )

    op.create_table(
        "feedback_email_days",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cap_reached_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("sent >= 0", name="ck_feedback_email_days_sent"),
    )

    for name, table, columns, where in _INDEXES:
        op.create_index(name, table, columns, postgresql_where=sa.text(where) if where else None)


def downgrade() -> None:
    bind = op.get_bind()
    for name, table, _columns, _where in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
    op.drop_table("feedback_email_days")
    op.drop_table("feedback_notice_items")
    op.drop_table("feedback_notices")
    op.drop_table("feedback_webhooks")
    op.drop_table("site_feedback_email")
    op.drop_table("group_feedback_email")
    op.drop_column("feedback_items", "mattermost_batched_at")
    op.drop_column("feedback_items", "email_batched_at")
    op.drop_column("members", "feedback_email")
    postgresql.ENUM(name="feedback_notice_outcome").drop(bind, checkfirst=True)
    postgresql.ENUM(name="feedback_channel").drop(bind, checkfirst=True)

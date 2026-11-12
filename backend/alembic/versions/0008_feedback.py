"""0008_feedback: feedback on a site, given through the widget on its pages.

A site gains three settings: whether it takes feedback, who may give it, and
whether live pages take it as well as previews. Three tables hold what comes
in: the items, the replies to them, and the screenshot of an item in a table
of its own, so that reading items never reads image bytes.

An item hangs on its site and goes with it. It names its version as a hint
only (ON DELETE SET NULL), so it outlives preview teardown, redeploys and the
cleanup of old live versions. `feedback_items.target` uses the type
`version_target` that 0001 made for versions; this migration neither creates
nor drops it.

The downgrade drops the tables, and every item, reply and screenshot with
them, then the types and the three settings.

Revision ID: 0008_feedback
Revises: 0007_drop_storage_ref
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008_feedback"
down_revision = "0007_drop_storage_ref"
branch_labels = None
depends_on = None

# Hardcoded on purpose, like 0001_base: a migration is a frozen snapshot and
# imports no application code. test_migrations.py holds these lists to the
# StrEnums in constants.py, order included: in SQL that order is the sort
# order of the type.
_FEEDBACK_IDENTITY_VALUES = ("account", "name", "anonymous")
_FEEDBACK_KIND_VALUES = ("page", "point", "text")
_FEEDBACK_AUTHOR_VALUES = ("account", "name", "anonymous")
_FEEDBACK_STATUS_VALUES = ("new", "seen", "resolved")
_FEEDBACK_REPLY_AUTHOR_VALUES = ("member", "giver")
_VERSION_TARGET_VALUES = ("live", "preview")

_TABLE_TYPES = (
    ("feedback_kind", _FEEDBACK_KIND_VALUES),
    ("feedback_author", _FEEDBACK_AUTHOR_VALUES),
    ("feedback_status", _FEEDBACK_STATUS_VALUES),
    ("feedback_reply_author", _FEEDBACK_REPLY_AUTHOR_VALUES),
)

_INDEXES = (
    (
        "ix_feedback_items_site_id_status_created_at",
        "feedback_items",
        ["site_id", "status", sa.text("created_at DESC")],
    ),
    ("ix_feedback_items_site_id_author_sub", "feedback_items", ["site_id", "author_sub"]),
    ("ix_feedback_items_version_id", "feedback_items", ["version_id"]),
    ("ix_feedback_items_status_changed_by", "feedback_items", ["status_changed_by"]),
    ("ix_feedback_replies_item_id_created_at", "feedback_replies", ["item_id", "created_at"]),
    ("ix_feedback_replies_member_id", "feedback_replies", ["member_id"]),
)


def _enum(*values: str, name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()

    _enum(*_FEEDBACK_IDENTITY_VALUES, name="feedback_identity").create(bind, checkfirst=True)
    op.add_column("sites", sa.Column("feedback_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column(
        "sites",
        sa.Column(
            "feedback_identity",
            _enum(*_FEEDBACK_IDENTITY_VALUES, name="feedback_identity"),
            nullable=False,
            server_default="account",
        ),
    )
    op.add_column("sites", sa.Column("feedback_live", sa.Boolean(), nullable=False, server_default=sa.false()))

    for name, values in _TABLE_TYPES:
        _enum(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "feedback_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "site_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("target", _enum(*_VERSION_TARGET_VALUES, name="version_target"), nullable=False),
        sa.Column("preview_ref", sa.Text(), nullable=True),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("page_path", sa.Text(), nullable=False),
        sa.Column("page_title", sa.Text(), nullable=True),
        sa.Column("kind", _enum(*_FEEDBACK_KIND_VALUES, name="feedback_kind"), nullable=False),
        sa.Column("anchor", postgresql.JSONB(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("author_kind", _enum(*_FEEDBACK_AUTHOR_VALUES, name="feedback_author"), nullable=False),
        sa.Column("author_sub", sa.Text(), nullable=True),
        sa.Column("author_name", sa.Text(), nullable=True),
        sa.Column("author_email", sa.Text(), nullable=True),
        sa.Column("viewport", postgresql.JSONB(), nullable=True),
        sa.Column(
            "status",
            _enum(*_FEEDBACK_STATUS_VALUES, name="feedback_status"),
            nullable=False,
            server_default="new",
        ),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status_changed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("(target = 'preview') = (preview_ref IS NOT NULL)", name="ck_feedback_items_preview_ref"),
        sa.CheckConstraint(
            "char_length(page_path) <= 2048 AND left(page_path, 1) <> '/'", name="ck_feedback_items_page_path"
        ),
        sa.CheckConstraint("char_length(page_title) <= 300", name="ck_feedback_items_page_title_length"),
        sa.CheckConstraint("(kind = 'page') = (anchor IS NULL)", name="ck_feedback_items_anchor"),
        sa.CheckConstraint("(kind = 'text') = (quote IS NOT NULL)", name="ck_feedback_items_quote"),
        sa.CheckConstraint("char_length(quote) <= 1000", name="ck_feedback_items_quote_length"),
        sa.CheckConstraint("char_length(comment) BETWEEN 1 AND 5000", name="ck_feedback_items_comment_length"),
        sa.CheckConstraint(
            "(author_kind = 'account' AND author_sub IS NOT NULL) OR "
            "(author_kind = 'name' AND author_name IS NOT NULL AND author_sub IS NULL AND author_email IS NULL) OR "
            "(author_kind = 'anonymous' AND author_sub IS NULL AND author_name IS NULL AND author_email IS NULL)",
            name="ck_feedback_items_author",
        ),
        sa.CheckConstraint("char_length(author_name) <= 200", name="ck_feedback_items_author_name_length"),
        sa.CheckConstraint("author_email = lower(author_email)", name="ck_feedback_items_author_email_lowercase"),
        # Exactly the keys width, height and dpr: a missing key comes back as
        # null in the rebuilt object, an extra one is not in it.
        sa.CheckConstraint(
            "viewport IS NULL OR viewport = jsonb_build_object("
            "'width', viewport -> 'width', 'height', viewport -> 'height', 'dpr', viewport -> 'dpr')",
            name="ck_feedback_items_viewport",
        ),
        sa.CheckConstraint(
            "(status = 'resolved') = (resolved_at IS NOT NULL)", name="ck_feedback_items_resolved_at"
        ),
    )

    op.create_table(
        "feedback_replies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("feedback_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_kind", _enum(*_FEEDBACK_REPLY_AUTHOR_VALUES, name="feedback_reply_author"), nullable=False
        ),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("author_name", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # One way only: a member reply may have neither, its member deleted
        # (SET NULL) or without a name, and still is a member reply.
        sa.CheckConstraint(
            "author_kind <> 'giver' OR (member_id IS NULL AND author_name IS NULL)", name="ck_feedback_replies_giver"
        ),
        sa.CheckConstraint("char_length(author_name) <= 200", name="ck_feedback_replies_author_name_length"),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 5000", name="ck_feedback_replies_body_length"),
    )

    op.create_table(
        "feedback_screenshots",
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("feedback_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "media_type IN ('image/png', 'image/jpeg', 'image/webp')", name="ck_feedback_screenshots_media_type"
        ),
        sa.CheckConstraint(
            "size_bytes > 0 AND size_bytes = octet_length(data)", name="ck_feedback_screenshots_size_bytes"
        ),
        sa.CheckConstraint("width > 0 AND height > 0", name="ck_feedback_screenshots_dimensions"),
    )

    for name, table, columns in _INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    bind = op.get_bind()

    for name, table, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
    op.drop_table("feedback_screenshots")
    op.drop_table("feedback_replies")
    op.drop_table("feedback_items")

    for name, _values in reversed(_TABLE_TYPES):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)

    op.drop_column("sites", "feedback_live")
    op.drop_column("sites", "feedback_identity")
    op.drop_column("sites", "feedback_enabled")
    postgresql.ENUM(name="feedback_identity").drop(bind, checkfirst=True)

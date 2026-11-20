"""0012_feedback_page_fragment: where on its page a feedback item was given.

A feedback item gains `page_fragment`: the fragment of the page's address
when the giver sent it, such as `#/7` for slide 7 of a slide deck, or NULL.
Only a fragment that names a place is kept, never one that holds a value, and
the check holds the column to that pattern. Existing items get NULL.

The downgrade drops the column, and with it where each item was given.

Revision ID: 0012_feedback_page_fragment
Revises: 0011_feedback
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0012_feedback_page_fragment"
down_revision = "0011_feedback"
branch_labels = None
depends_on = None

# Hardcoded: a migration is a frozen snapshot and imports no application code.
_CHECK = "page_fragment ~ '^#[A-Za-z0-9/_.~-]{1,64}$'"


def upgrade() -> None:
    op.add_column("feedback_items", sa.Column("page_fragment", sa.Text(), nullable=True))
    op.create_check_constraint("ck_feedback_items_page_fragment", "feedback_items", _CHECK)


def downgrade() -> None:
    op.drop_constraint("ck_feedback_items_page_fragment", "feedback_items", type_="check")
    op.drop_column("feedback_items", "page_fragment")

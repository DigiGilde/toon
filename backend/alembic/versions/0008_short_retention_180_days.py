"""0008_short_retention_180_days: viewing and presence are kept 180 days.

The short term of the audit log (tier 0) and the term of content_viewers go
from 90 to 180 days together, so a pseudonym in a short-term row can still be
traced to a content-only viewer for as long as the row exists (BIO2 8.15.04).
The delete guard and the purge ask audit_log_chain_retention() at the moment
of the delete, so the new term holds for rows already written too.

Revision ID: 0008_short_retention_180_days
Revises: 0007_drop_storage_ref
Create Date: 2026-11-14
"""

from __future__ import annotations

from alembic import op

revision = "0008_short_retention_180_days"
down_revision = "0007_drop_storage_ref"
branch_labels = None
depends_on = None


def _retention(short_days: int) -> str:
    return f"""
CREATE OR REPLACE FUNCTION audit_log_retention(tier integer) RETURNS interval AS $$
    SELECT CASE tier WHEN 0 THEN interval '{short_days} days' ELSE interval '1096 days' END;
$$ LANGUAGE sql IMMUTABLE;
"""


_REFUSAL = "content_viewers keeps a row until its retention has passed: % may not be deleted yet"
# The text 0001_base gave the function, restored as it was on a downgrade.
_REFUSAL_0001 = "content_viewers is pas te verwijderen na de bewaartermijn: % mag nog niet weg"


def _content_viewer_guard(days: int, refusal: str) -> str:
    return f"""
CREATE OR REPLACE FUNCTION content_viewer_delete_after_retention() RETURNS trigger AS $$
BEGIN
    IF OLD.last_seen_at > now() - interval '{days} days' THEN
        RAISE EXCEPTION '{refusal}', OLD.id;
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.execute(_retention(180))
    op.execute(_content_viewer_guard(180, _REFUSAL))


def downgrade() -> None:
    op.execute(_retention(90))
    op.execute(_content_viewer_guard(90, _REFUSAL_0001))

"""0010_audit_purge_hold: a pause on the audit purge, held in the database.

During a (suspected) incident BIO2 5.28.01 keeps its logging for three years.
A platform admin opens a hold through the API, and while one is open the two
delete guards refuse every delete, so neither the nightly job nor a hand-run
purge nor an off-hand DELETE takes a row past its retention. A hold is never
removed or rewritten: it is only ever ended, once.

Revision ID: 0010_audit_purge_hold
Revises: 0009_english_trigger_messages
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010_audit_purge_hold"
down_revision = "0009_english_trigger_messages"
branch_labels = None
depends_on = None

# The check a hold adds, after the retention check the guard already made.
_HOLD_CHECK = """
            IF EXISTS (SELECT 1 FROM audit_purge_holds WHERE ended_at IS NULL) THEN
                RAISE EXCEPTION 'the audit purge is on hold: % may not be deleted now', OLD.id;
            END IF;"""

# The bodies keep the text and indentation 0008 and 0009 gave them, so a
# downgrade gives back prosrc exactly as it was.
_AUDIT_LOG_GUARD = """
        CREATE OR REPLACE FUNCTION audit_log_delete_after_retention() RETURNS trigger AS $$
        BEGIN
            IF OLD.occurred_at > now() - audit_log_chain_retention(OLD.chain_shard) THEN
                RAISE EXCEPTION 'audit row % is still within its retention and may not be deleted yet', OLD.id;
            END IF;{hold}
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """

_CONTENT_VIEWER_GUARD = """
CREATE OR REPLACE FUNCTION content_viewer_delete_after_retention() RETURNS trigger AS $$
BEGIN
    IF OLD.last_seen_at > now() - interval '180 days' THEN
        RAISE EXCEPTION 'content_viewers keeps a row until its retention has passed: % may not be deleted yet', OLD.id;
    END IF;{hold}
    RETURN OLD;
END;
$$ LANGUAGE plpgsql
"""

# One function for UPDATE, DELETE and TRUNCATE. OLD is unassigned in the
# statement-level TRUNCATE trigger, hence the nested IF rather than one AND.
_HOLD_GUARD = """
CREATE FUNCTION audit_purge_hold_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.ended_at IS NULL
            AND NEW.ended_at IS NOT NULL
            AND NEW.ended_by IS NOT NULL
            AND NEW.end_reason IS NOT NULL
            AND NEW.id = OLD.id
            AND NEW.reason = OLD.reason
            AND NEW.started_at = OLD.started_at
            AND NEW.started_by = OLD.started_by
        THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION 'an audit purge hold is only ever ended, once: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.create_table(
        "audit_purge_holds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("members.id"), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(ended_at IS NULL) = (ended_by IS NULL) AND (ended_at IS NULL) = (end_reason IS NULL)",
            name="ck_audit_purge_holds_ended_whole",
        ),
    )
    op.create_index(
        "uq_audit_purge_holds_open",
        "audit_purge_holds",
        [sa.text("(ended_at IS NULL)")],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.execute(_HOLD_GUARD)
    op.execute(
        "CREATE TRIGGER audit_purge_holds_guard BEFORE UPDATE OR DELETE ON audit_purge_holds "
        "FOR EACH ROW EXECUTE FUNCTION audit_purge_hold_guard()"
    )
    op.execute(
        "CREATE TRIGGER audit_purge_holds_no_truncate BEFORE TRUNCATE ON audit_purge_holds "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_purge_hold_guard()"
    )
    op.execute(_AUDIT_LOG_GUARD.format(hold=_HOLD_CHECK))
    op.execute(_CONTENT_VIEWER_GUARD.format(hold=_HOLD_CHECK.replace("\n            ", "\n    ")))


def downgrade() -> None:
    op.execute(_AUDIT_LOG_GUARD.format(hold=""))
    op.execute(_CONTENT_VIEWER_GUARD.format(hold=""))
    op.drop_table("audit_purge_holds")
    op.execute("DROP FUNCTION audit_purge_hold_guard()")

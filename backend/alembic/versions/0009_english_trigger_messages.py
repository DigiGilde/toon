"""0009_english_trigger_messages: the guard triggers refuse in English.

Only the text of the RAISE EXCEPTION messages changes; the bodies, the
arguments and the SQLSTATE stay as 0001_base left them. A downgrade puts the
Dutch text back.

Revision ID: 0009_english_trigger_messages
Revises: 0008_short_retention_180_days
Create Date: 2026-11-14
"""

from __future__ import annotations

from alembic import op

revision = "0009_english_trigger_messages"
down_revision = "0008_short_retention_180_days"
branch_labels = None
depends_on = None

# (English, Dutch as 0001_base wrote it)
_MESSAGES = {
    "last_admin": (
        "group % would be left without an admin",
        "groep % zou zonder beheerder achterblijven",
    ),
    "head_trigger_only": (
        "the audit chain head is only updated by the audit log trigger: % is not allowed",
        "auditketenkop wordt alleen door de auditlog-trigger bijgewerkt: % niet toegestaan",
    ),
    "head_one_on": (
        "the audit chain head only moves one position on: % after %",
        "auditketenkop schuift alleen een positie op: % na %",
    ),
    "append_only": (
        "the audit log is append-only: % is not allowed",
        "auditlog is append-only: % niet toegestaan",
    ),
    "audit_retention": (
        "audit row % is still within its retention and may not be deleted yet",
        "auditlog is append-only tot de bewaartermijn om is: % mag nog niet weg",
    ),
    "truncate": (
        "%: TRUNCATE is not allowed, rows only go once their retention has passed",
        "%: TRUNCATE niet toegestaan, rijen gaan alleen weg na hun bewaartermijn",
    ),
}

# The bodies keep the indentation 0001_base gave them, so prosrc differs from
# the old one in the message only.
_FUNCTIONS = (
    """
CREATE OR REPLACE FUNCTION guard_last_group_admin() RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM groups WHERE id = OLD.group_id) THEN
        RETURN NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM group_members WHERE group_id = OLD.group_id AND role = 'admin'
    ) THEN
        RAISE EXCEPTION '{last_admin}', OLD.group_id
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
""",
    """
CREATE OR REPLACE FUNCTION audit_log_chain_head_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' OR pg_trigger_depth() < 2 THEN
        RAISE EXCEPTION '{head_trigger_only}', TG_OP;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.chain_seq <> OLD.chain_seq + 1 THEN
            RAISE EXCEPTION '{head_one_on}', NEW.chain_seq, OLD.chain_seq;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""",
    """
        CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '{append_only}', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """,
    """
        CREATE OR REPLACE FUNCTION audit_log_delete_after_retention() RETURNS trigger AS $$
        BEGIN
            IF OLD.occurred_at > now() - audit_log_chain_retention(OLD.chain_shard) THEN
                RAISE EXCEPTION '{audit_retention}', OLD.id;
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """,
    """
        CREATE OR REPLACE FUNCTION audit_log_refuse_truncate() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '{truncate}', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """,
)


def _replace_all(language: int) -> None:
    messages = {key: texts[language] for key, texts in _MESSAGES.items()}
    for function in _FUNCTIONS:
        op.execute(function.format(**messages))


def upgrade() -> None:
    _replace_all(0)


def downgrade() -> None:
    _replace_all(1)

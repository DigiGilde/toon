"""Tests for the migrations (spec §12, §3): schema, enum
types, CHECKs, UNIQUE, the append-only audit log triggers and the guard that a
group never ends up without an admin."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
import pathlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from helpers_audit import insert_aged_audit_row
from sqlalchemy import CheckConstraint

from toon import i18n
from toon.audit import vocabulary
from toon.constants import (
    RESERVED_SLUGS,
    ROLE_RANK,
    AccessBase,
    FeedbackAuthor,
    FeedbackIdentity,
    FeedbackKind,
    FeedbackReplyAuthor,
    FeedbackStatus,
    Role,
)
from toon.models.feedback import FeedbackItem, FeedbackReply, FeedbackScreenshot
from toon.models.identity import MemberLanguage
from toon.models.publication import VersionTarget

pytestmark = pytest.mark.asyncio


async def _make_member(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        """
        INSERT INTO members (id, sso_subject, email, platform_role, status)
        VALUES ($1, $2, $3, 'member', 'active')
        RETURNING id
        """,
        uuid.uuid4(),
        f"sub-{uuid.uuid4().hex}",
        f"lid-{uuid.uuid4().hex}@example.org",
    )


async def _make_group(conn: asyncpg.Connection, base: str = "public") -> uuid.UUID:
    return await conn.fetchval(
        """
        INSERT INTO groups (id, slug, name, default_access_base)
        VALUES ($1, $2, 'Testgroep', $3)
        RETURNING id
        """,
        uuid.uuid4(),
        f"groep-{uuid.uuid4().hex[:12]}",
        base,
    )


async def _make_site(conn: asyncpg.Connection, group_id: uuid.UUID, base: str = "public") -> uuid.UUID:
    return await conn.fetchval(
        """
        INSERT INTO sites (id, group_id, slug, title, access_base)
        VALUES ($1, $2, $3, 'Testsite', $4)
        RETURNING id
        """,
        uuid.uuid4(),
        group_id,
        f"site-{uuid.uuid4().hex[:12]}",
        base,
    )


async def _make_version(conn: asyncpg.Connection, site_id: uuid.UUID, member_id: uuid.UUID) -> uuid.UUID:
    return await conn.fetchval(
        """
        INSERT INTO versions (id, site_id, target, member_id)
        VALUES ($1, $2, 'preview', $3)
        RETURNING id
        """,
        uuid.uuid4(),
        site_id,
        member_id,
    )


async def _make_group_member(
    conn: asyncpg.Connection, group_id: uuid.UUID, member_id: uuid.UUID, role: str = "beheerder"
) -> None:
    await conn.execute(
        "INSERT INTO group_members (group_id, member_id, role) VALUES ($1, $2, $3)", group_id, member_id, role
    )


def _latest_revision() -> str:
    """The head revision according to the migration files themselves (not a
    fixed value, which would topple over on every new migration without
    guarding anything)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = pathlib.Path(__file__).resolve().parents[1]
    return ScriptDirectory.from_config(Config(str(root / "alembic.ini"))).get_current_head()


async def test_migration_runs_on_empty_database(db_connection: asyncpg.Connection) -> None:
    """The database is at the latest revision. The expectation comes from the
    migration files themselves, so a new migration does not break this test but
    a half-run migration does."""
    version = await db_connection.fetchval("SELECT version_num FROM alembic_version")
    assert version == _latest_revision()


async def test_all_core_tables_exist(db_connection: asyncpg.Connection) -> None:
    expected_tables = {
        "members",
        "groups",
        "group_members",
        "site_members",
        "sites",
        "versions",
        "previews",
        "invitees",
        "access_keys",
        "site_repositories",
        "cli_device_authorizations",
        "cli_sessions",
        "cli_refresh_tokens",
        "audit_log_entries",
        "group_slugs",
        "site_slugs",
        "feedback_items",
        "feedback_replies",
        "feedback_screenshots",
    }
    rows = await db_connection.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    present = {row["table_name"] for row in rows}
    assert expected_tables <= present


async def test_audit_log_update_refused_by_trigger(db_connection: asyncpg.Connection) -> None:
    audit_id = uuid.uuid4()
    await db_connection.execute(
        """
        INSERT INTO audit_log_entries (id, actor_kind, action, result)
        VALUES ($1, 'system', 'test_actie', 'allowed')
        """,
        audit_id,
    )

    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute("UPDATE audit_log_entries SET action = 'gewijzigd' WHERE id = $1", audit_id)


async def test_audit_log_delete_refused_by_trigger(db_connection: asyncpg.Connection) -> None:
    audit_id = uuid.uuid4()
    await db_connection.execute(
        """
        INSERT INTO audit_log_entries (id, actor_kind, action, result)
        VALUES ($1, 'system', 'test_actie', 'allowed')
        """,
        audit_id,
    )

    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute("DELETE FROM audit_log_entries WHERE id = $1", audit_id)


async def _audit_row(conn: asyncpg.Connection, action: str, result: str, age: timedelta) -> uuid.UUID:
    return await insert_aged_audit_row(conn, action, result, age)


async def _deletable(conn: asyncpg.Connection, audit_id: uuid.UUID) -> bool:
    try:
        async with conn.transaction():
            await conn.execute("DELETE FROM audit_log_entries WHERE id = $1", audit_id)
    except asyncpg.PostgresError:
        return False
    return True


@pytest.mark.parametrize(("action", "result"), sorted(vocabulary.SHORT_RETENTION))
async def test_looking_goes_after_ninety_days(
    db_connection: asyncpg.Connection, action: str, result: str
) -> None:
    fresh = await _audit_row(db_connection, action, result, timedelta(days=89))
    expired = await _audit_row(db_connection, action, result, timedelta(days=91))

    assert not await _deletable(db_connection, fresh)
    assert await _deletable(db_connection, expired)


@pytest.mark.parametrize(
    ("action", "result"),
    [
        ("content_access", "refused"),
        ("content_access", "login_redirect"),
        ("login", "refused"),
        ("admin_access", "refused"),
        ("member_platform_role", "allowed"),
        ("deploy", "allowed"),
        ("audit_read", "allowed"),
        # Approving a CLI login hands out a credential that lives up to 90
        # days; the record of who granted it must outlive it.
        ("cli_login", "allowed"),
        ("cli_token_issued", "allowed"),
        ("cli_session_revoke", "allowed"),
        ("cli_refresh_reuse", "refused"),
        ("cli_logout", "refused"),
    ],
)
async def test_everything_else_stays_three_years(
    db_connection: asyncpg.Connection, action: str, result: str
) -> None:
    """Every refusal and every management act can become part of an incident,
    and BIO2 5.28.01 keeps those for three years. A refusal of the same age as
    an expired viewing row must still be refused."""
    a_year = await _audit_row(db_connection, action, result, timedelta(days=400))
    # Three calendar years plus the leap days they can hold.
    three_years = await _audit_row(db_connection, action, result, timedelta(days=3 * 366 + 1))

    assert not await _deletable(db_connection, a_year)
    assert await _deletable(db_connection, three_years)


async def test_the_database_and_the_code_agree_on_the_terms(db_connection: asyncpg.Connection) -> None:
    for action, result in vocabulary.SHORT_RETENTION:
        assert await db_connection.fetchval("SELECT audit_log_retention_tier($1, $2)", action, result) == 0
    assert await db_connection.fetchval("SELECT audit_log_retention_tier('admin_access', 'refused')") == 1
    # The database is authoritative; the vocabulary mirrors its numbers.
    for tier, days in ((0, vocabulary.SHORT_RETENTION_DAYS), (1, vocabulary.LONG_RETENTION_DAYS)):
        assert await db_connection.fetchval("SELECT audit_log_retention($1) = make_interval(days => $2)", tier, days)


async def test_a_chain_keeps_the_term_of_its_tier(db_connection: asyncpg.Connection) -> None:
    """The delete guard and the purge ask the chain, not the row: chains 0 to
    15 hold the 90-day rows, 16 to 31 the rest."""
    for shard in (0, 15):
        assert await db_connection.fetchval(
            "SELECT audit_log_chain_retention($1::smallint) = audit_log_retention(0)", shard
        )
    for shard in (16, 31):
        assert await db_connection.fetchval(
            "SELECT audit_log_chain_retention($1::smallint) = audit_log_retention(1)", shard
        )


async def _refusal(conn: asyncpg.Connection, statement: str, *args) -> str:
    """The message a guard trigger refused `statement` with; fails the test
    when the statement went through."""
    try:
        async with conn.transaction():
            await conn.execute(statement, *args)
    except asyncpg.RaiseError as error:
        return str(error)
    raise AssertionError(f"not refused: {statement}")


_TRIGGER_ONLY = "alleen door de auditlog-trigger"
_ONE_ON = "schuift alleen een positie op"
_INSERT_AUDIT_ROW = (
    "INSERT INTO audit_log_entries (id, actor_kind, action, result) VALUES ($1, 'system', 'test_actie', 'allowed')"
)


async def test_a_chain_head_is_not_written_directly(db_connection: asyncpg.Connection) -> None:
    """Only the chain trigger moves a head. A direct write would let the next
    row chain onto a hash of the writer's choosing, or restart a chain at a
    position a published checkpoint already holds."""
    await db_connection.execute(_INSERT_AUDIT_ROW, uuid.uuid4())
    shard = await db_connection.fetchval("SELECT chain_shard FROM audit_log_chain_heads")

    assert _TRIGGER_ONLY in await _refusal(
        db_connection, "INSERT INTO audit_log_chain_heads VALUES ($1, 1, '\\x00', now())", (shard + 1) % 32
    )
    assert _TRIGGER_ONLY in await _refusal(
        db_connection, "UPDATE audit_log_chain_heads SET chain_seq = chain_seq + 1"
    )
    assert _TRIGGER_ONLY in await _refusal(db_connection, "UPDATE audit_log_chain_heads SET chain_hash = '\\x00'")
    assert _TRIGGER_ONLY in await _refusal(db_connection, "DELETE FROM audit_log_chain_heads")


async def test_a_chain_head_only_moves_one_position_on(db_connection: asyncpg.Connection) -> None:
    """Even from inside a trigger, the one place a head may be written from,
    a head cannot jump ahead or go back."""
    await db_connection.execute(_INSERT_AUDIT_ROW, uuid.uuid4())
    await db_connection.execute(
        """
        CREATE FUNCTION test_move_the_head() RETURNS trigger AS $$
        BEGIN
            UPDATE audit_log_chain_heads SET chain_seq = chain_seq + current_setting('toon_test.step')::bigint;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    await db_connection.execute(
        "CREATE TRIGGER test_move_the_head AFTER INSERT ON content_viewers "
        "FOR EACH ROW EXECUTE FUNCTION test_move_the_head()"
    )
    for step in ("5", "0", "-1"):
        await db_connection.execute("SELECT set_config('toon_test.step', $1, true)", step)

        assert _ONE_ON in await _refusal(
            db_connection,
            "INSERT INTO content_viewers (id, sso_subject) VALUES ($1, $2)",
            uuid.uuid4(),
            f"sub-{uuid.uuid4().hex}",
        )


@pytest.mark.parametrize("table", ["audit_log_entries", "audit_log_chain_heads", "content_viewers"])
async def test_truncate_is_refused(db_connection: asyncpg.Connection, table: str) -> None:
    """TRUNCATE fires no row trigger, so without a statement trigger of its own
    it would empty a guarded table in one command. The test connection is a
    superuser, so this holds for the app's own account a fortiori."""
    await db_connection.execute(_INSERT_AUDIT_ROW, uuid.uuid4())
    await db_connection.execute(
        "INSERT INTO content_viewers (id, sso_subject) VALUES ($1, $2)", uuid.uuid4(), f"sub-{uuid.uuid4().hex}"
    )

    refusal = await _refusal(db_connection, f"TRUNCATE {table}")
    assert f"{table}: TRUNCATE niet toegestaan" in refusal
    assert await db_connection.fetchval(f"SELECT count(*) FROM {table}") == 1  # noqa: S608 - table is a literal


async def test_truncate_cascading_from_elsewhere_is_refused(db_connection: asyncpg.Connection) -> None:
    await db_connection.execute(_INSERT_AUDIT_ROW, uuid.uuid4())

    assert "TRUNCATE niet toegestaan" in await _refusal(
        db_connection, "TRUNCATE members, audit_log_entries RESTART IDENTITY CASCADE"
    )
    assert await db_connection.fetchval("SELECT count(*) FROM audit_log_entries") == 1


async def test_content_viewers_deletable_only_after_ninety_days(db_connection: asyncpg.Connection) -> None:
    """The BEFORE DELETE trigger on content_viewers bounds the retention job,
    not the WHERE clause in audit/retention.py: a row that is still within its
    term stays, whoever issues the DELETE."""
    fresh = await db_connection.fetchval(
        "INSERT INTO content_viewers (id, sso_subject, email) VALUES ($1, $2, $3) RETURNING id",
        uuid.uuid4(),
        f"sub-{uuid.uuid4().hex}",
        "viewer@example.nl",
    )
    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute("DELETE FROM content_viewers WHERE id = $1", fresh)

    expired = await db_connection.fetchval(
        """
        INSERT INTO content_viewers (id, sso_subject, email, last_seen_at)
        VALUES ($1, $2, $3, now() - interval '91 days')
        RETURNING id
        """,
        uuid.uuid4(),
        f"sub-{uuid.uuid4().hex}",
        "viewer@example.nl",
    )
    await db_connection.execute("DELETE FROM content_viewers WHERE id = $1", expired)
    assert await db_connection.fetchval("SELECT count(*) FROM content_viewers WHERE id = $1", expired) == 0


async def _enum_labels(conn: asyncpg.Connection, type_name: str) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT e.enumlabel
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = $1
        ORDER BY e.enumsortorder
        """,
        type_name,
    )
    return [row["enumlabel"] for row in rows]


@pytest.mark.parametrize(
    ("type_name", "enum_class", "count"),
    [("access_base", AccessBase, 4), ("role", Role, 3), ("member_language", MemberLanguage, 2)],
)
async def test_shared_enum_type_used_exactly_the_application_values(
    db_connection: asyncpg.Connection, type_name: str, enum_class: type, count: int
) -> None:
    """The migrations write their labels hard-coded; here they are held against
    the application constants, so the two cannot drift apart unnoticed."""
    db_values_set = set(await _enum_labels(db_connection, type_name))
    application_values = {member.value for member in enum_class}

    assert db_values_set == application_values
    assert len(application_values) == count


async def test_member_language_matches_the_languages_the_app_speaks() -> None:
    """The column can only ever hold a language the interface has a catalogue
    for; a third value would be storable and unreadable."""
    assert {language.value for language in MemberLanguage} == set(i18n.SUPPORTED)


async def test_member_language_is_nullable(db_connection: asyncpg.Connection) -> None:
    """NULL is "follow my browser", the state every member starts in."""
    member_id = await _make_member(db_connection)
    assert await db_connection.fetchval("SELECT language FROM members WHERE id = $1", member_id) is None


async def test_role_enum_sorts_of_narrow_to_wide(db_connection: asyncpg.Connection) -> None:
    """The declaration order in the migration is load-bearing as soon as roles
    are compared in SQL, so it has to give the same ordering as ROLE_RANK."""
    labels = await _enum_labels(db_connection, "role")
    assert labels == [role.value for role in sorted(ROLE_RANK, key=lambda role: ROLE_RANK[role])]
    assert labels == ["reader", "editor", "admin"]


@pytest.mark.parametrize("value", [member.value for member in AccessBase])
async def test_group_default_access_base_accepts_every_valid_value(
    db_connection: asyncpg.Connection, value: str
) -> None:
    group_id = await _make_group(db_connection, base=value)
    assert group_id is not None


@pytest.mark.parametrize("value", [member.value for member in AccessBase])
async def test_site_access_base_accepts_every_valid_value(
    db_connection: asyncpg.Connection, value: str
) -> None:
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id, base=value)
    assert site_id is not None


@pytest.mark.parametrize("value", [member.value for member in AccessBase])
async def test_preview_access_override_accepts_every_valid_value(
    db_connection: asyncpg.Connection, value: str
) -> None:
    member_id = await _make_member(db_connection)
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)
    version_id = await _make_version(db_connection, site_id, member_id)

    preview_id = await db_connection.fetchval(
        """
        INSERT INTO previews (
            id, site_id, ref, version_id,
            access_base_override, access_keys_override, access_invitees_override
        )
        VALUES ($1, $2, 'pr-1', $3, $4, false, false)
        RETURNING id
        """,
        uuid.uuid4(),
        site_id,
        version_id,
        value,
    )
    assert preview_id is not None


@pytest.mark.parametrize(
    "columns",
    [
        "access_base_override",
        "access_keys_override",
        "access_base_override, access_invitees_override",
    ],
)
async def test_ck_previews_access_override_refuses_half_an_override(
    db_connection: asyncpg.Connection, columns: str
) -> None:
    """An override is one whole policy. Half of one would mean a base from the
    preview with extras silently borrowed from the site, which is exactly the
    kind of drift the single enum type is there to prevent."""
    member_id = await _make_member(db_connection)
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)
    version_id = await _make_version(db_connection, site_id, member_id)
    values = ", ".join(
        "'sso'" if name.strip() == "access_base_override" else "true" for name in columns.split(",")
    )

    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            # Both halves of the interpolation come from the parametrize list.
            statement = (
                f"INSERT INTO previews (id, site_id, ref, version_id, {columns}) "  # noqa: S608
                f"VALUES ($1, $2, 'pr-half', $3, {values})"
            )
            await db_connection.execute(
                statement,
                uuid.uuid4(),
                site_id,
                version_id,
            )


async def test_external_sources_defaults_to_on(db_connection: asyncpg.Connection) -> None:
    """On unless you turn it off: a row that never names the column gets the
    permissive value, and the SPA switch is the restriction."""
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)
    assert await db_connection.fetchval(
        "SELECT external_sources FROM sites WHERE id = $1", site_id
    ) is True


async def test_live_versions_kept_defaults_to_following_the_platform(db_connection: asyncpg.Connection) -> None:
    """NULL is the value a site gets for free: it follows TOON_LIVE_VERSIONS_KEPT
    until a site admin sets a number of its own."""
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)
    assert await db_connection.fetchval(
        "SELECT live_versions_kept FROM sites WHERE id = $1", site_id
    ) is None


async def test_ck_sites_live_versions_kept_refuses_a_negative_number(db_connection: asyncpg.Connection) -> None:
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)
    await db_connection.execute("UPDATE sites SET live_versions_kept = 0 WHERE id = $1", site_id)
    with pytest.raises(asyncpg.CheckViolationError, match="ck_sites_live_versions_kept"):
        await db_connection.execute("UPDATE sites SET live_versions_kept = -1 WHERE id = $1", site_id)


def _alembic(dsn: str, action: str, revision: str) -> None:
    from alembic.config import Config

    from alembic import command

    root = pathlib.Path(__file__).resolve().parents[1] / "alembic"
    cfg = Config()
    cfg.set_main_option("script_location", str(root))
    cfg.set_main_option("version_locations", str(root / "versions"))
    cfg.set_main_option("path_separator", "os")
    os.environ["TOON_DB_URL"] = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    try:
        getattr(command, action)(cfg, revision)
    finally:
        os.environ.pop("TOON_DB_URL", None)


async def test_the_site_retention_migration_goes_down_and_up_again(postgres_container) -> None:
    """On its own database, so the shared one stays at head for every other
    test; with a site that has a number of its own, as production will."""
    base = postgres_container.get_connection_url(driver=None)
    name = f"heen_en_terug_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(base)
    await admin.execute(f'CREATE DATABASE "{name}"')
    dsn = f"{base.rsplit('/', 1)[0]}/{name}"
    try:
        await asyncio.to_thread(_alembic, dsn, "upgrade", "head")
        conn = await asyncpg.connect(dsn)
        try:
            site_id = await _make_site(conn, await _make_group(conn))
            await conn.execute("UPDATE sites SET live_versions_kept = 3 WHERE id = $1", site_id)
        finally:
            await conn.close()

        await asyncio.to_thread(_alembic, dsn, "downgrade", "0001_base")
        conn = await asyncpg.connect(dsn)
        try:
            columns = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'sites'"
            )
            assert "live_versions_kept" not in {row["column_name"] for row in columns}
        finally:
            await conn.close()

        await asyncio.to_thread(_alembic, dsn, "upgrade", "head")
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetchval("SELECT live_versions_kept FROM sites WHERE id = $1", site_id) is None
        finally:
            await conn.close()
    finally:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


async def test_the_ids_confirmed_migration_takes_the_latest_link_audit_row(postgres_container) -> None:
    """A link counts as confirmed only when its latest `site_repository_set`
    row for the same ids says so; down again drops the column."""
    base = postgres_container.get_connection_url(driver=None)
    name = f"bevestigd_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(base)
    await admin.execute(f'CREATE DATABASE "{name}"')
    dsn = f"{base.rsplit('/', 1)[0]}/{name}"
    try:
        await asyncio.to_thread(_alembic, dsn, "upgrade", "0001_base")
        conn = await asyncpg.connect(dsn)
        try:
            group_id = await _make_group(conn)
            group_slug = await conn.fetchval("SELECT slug FROM groups WHERE id = $1", group_id)
            # site -> the ids_confirmed of its set rows, oldest first, and the
            # repository_id they name (1001 is the linked one).
            cases = {
                "confirmed": ([True], 1001),
                "entered": ([False], 1001),
                "relinked-unconfirmed": ([True, False], 1001),
                "relinked-confirmed": ([False, True], 1001),
                "other-ids": ([True], 9999),
                "no-row": ([], 1001),
            }
            sites = {}
            for case, (rows, repository_id) in cases.items():
                site_id = await _make_site(conn, group_id)
                sites[case] = site_id
                site_slug = await conn.fetchval("SELECT slug FROM sites WHERE id = $1", site_id)
                await conn.execute(
                    """
                    INSERT INTO site_repositories (id, site_id, provider, host, owner, repo, repository_id, owner_id)
                    VALUES ($1, $2, 'github', 'https://github.com', 'minbzk', 'website', 1001, 2002)
                    """,
                    uuid.uuid4(),
                    site_id,
                )
                for confirmed in rows:
                    await conn.execute(
                        """
                        INSERT INTO audit_log_entries (id, actor_kind, action, result, refs)
                        VALUES ($1, 'member', 'site_repository_set', 'allowed', $2::jsonb)
                        """,
                        uuid.uuid4(),
                        json.dumps(
                            {
                                "group": group_slug,
                                "site": site_slug,
                                "repository_id": repository_id,
                                "ids_confirmed": confirmed,
                            }
                        ),
                    )
        finally:
            await conn.close()

        await asyncio.to_thread(_alembic, dsn, "upgrade", "head")
        conn = await asyncpg.connect(dsn)
        try:
            confirmed = {
                case: await conn.fetchval("SELECT ids_confirmed FROM site_repositories WHERE site_id = $1", site_id)
                for case, site_id in sites.items()
            }
        finally:
            await conn.close()
        assert confirmed == {
            "confirmed": True,
            "entered": False,
            "relinked-unconfirmed": False,
            "relinked-confirmed": True,
            "other-ids": False,
            "no-row": False,
        }

        # One repository on six sites: 0004 requires the site id of each, and
        # will not go down while a link does.
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM site_repositories")
        finally:
            await conn.close()
        await asyncio.to_thread(_alembic, dsn, "downgrade", "0001_base")
        conn = await asyncpg.connect(dsn)
        try:
            columns = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'site_repositories'"
            )
            assert "ids_confirmed" not in {row["column_name"] for row in columns}
        finally:
            await conn.close()
    finally:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


_SITE_INDEXES = {
    "ix_versions_site_id_created_at": "(site_id, created_at DESC)",
    "ix_access_keys_site_id": "(site_id)",
}


async def _site_indexes(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch(
        "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(_SITE_INDEXES)
    )
    return {row["indexname"]: row["indexdef"] for row in rows}


async def test_versions_and_access_keys_are_indexed_on_their_site(db_connection: asyncpg.Connection) -> None:
    """PostgreSQL indexes no foreign key column by itself; the version list
    and the secret links tab read both tables per site."""
    indexes = await _site_indexes(db_connection)
    assert set(indexes) == set(_SITE_INDEXES)
    for name, columns in _SITE_INDEXES.items():
        assert indexes[name].endswith(columns), indexes[name]


async def test_the_site_index_migration_goes_down_and_up_again(postgres_container) -> None:
    base = postgres_container.get_connection_url(driver=None)
    name = f"indexen_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(base)
    await admin.execute(f'CREATE DATABASE "{name}"')
    dsn = f"{base.rsplit('/', 1)[0]}/{name}"
    try:
        await asyncio.to_thread(_alembic, dsn, "upgrade", "head")
        await asyncio.to_thread(_alembic, dsn, "downgrade", "0005_slug_namespace")
        conn = await asyncpg.connect(dsn)
        try:
            assert await _site_indexes(conn) == {}
        finally:
            await conn.close()

        await asyncio.to_thread(_alembic, dsn, "upgrade", "head")
        conn = await asyncpg.connect(dsn)
        try:
            assert set(await _site_indexes(conn)) == set(_SITE_INDEXES)
        finally:
            await conn.close()
    finally:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


async def test_sandbox_defaults_to_on(db_connection: asyncpg.Connection) -> None:
    """The safe value is the one a row gets for free: a site that never names
    the column is served with its own origin, and the SPA switch is what gives
    that up."""
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)
    assert await db_connection.fetchval(
        "SELECT sandbox FROM sites WHERE id = $1", site_id
    ) is True


async def test_access_base_enum_refuses_unknown_value(db_connection: asyncpg.Connection) -> None:
    group_id = uuid.uuid4()
    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute(
                """
                INSERT INTO groups (id, slug, name, default_access_base)
                VALUES ($1, 'onbekend-niveau', 'Test', 'onbekend')
                """,
                group_id,
            )


async def test_ck_versions_origin_refuses_neither(db_connection: asyncpg.Connection) -> None:
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)

    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute(
                """
                INSERT INTO versions (id, site_id, target)
                VALUES ($1, $2, 'preview')
                """,
                uuid.uuid4(),
                site_id,
            )


async def test_ck_versions_origin_refuses_both(db_connection: asyncpg.Connection) -> None:
    member_id = await _make_member(db_connection)
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)

    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute(
                """
                INSERT INTO versions (id, site_id, target, member_id, ci_repository)
                VALUES ($1, $2, 'preview', $3, 'github.com/minbzk/website')
                """,
                uuid.uuid4(),
                site_id,
                member_id,
            )


async def test_ck_versions_origin_allows_exactly_one(db_connection: asyncpg.Connection) -> None:
    member_id = await _make_member(db_connection)
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)

    version_id = await _make_version(db_connection, site_id, member_id)
    assert version_id is not None
    ci_version_id = await db_connection.fetchval(
        """
        INSERT INTO versions (id, site_id, target, ci_repository)
        VALUES ($1, $2, 'live', 'github.com/minbzk/website')
        RETURNING id
        """,
        uuid.uuid4(),
        site_id,
    )
    assert ci_version_id is not None


async def test_unique_site_id_ref_enforced_on_preview(db_connection: asyncpg.Connection) -> None:
    member_id = await _make_member(db_connection)
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)
    version_1 = await _make_version(db_connection, site_id, member_id)
    version_2 = await _make_version(db_connection, site_id, member_id)

    await db_connection.execute(
        """
        INSERT INTO previews (id, site_id, ref, version_id)
        VALUES ($1, $2, 'pr-42', $3)
        """,
        uuid.uuid4(),
        site_id,
        version_1,
    )

    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute(
                """
                INSERT INTO previews (id, site_id, ref, version_id)
                VALUES ($1, $2, 'pr-42', $3)
                """,
                uuid.uuid4(),
                site_id,
                version_2,
            )


async def test_a_site_has_at_most_one_repository_and_loses_it_with_the_site(
    db_connection: asyncpg.Connection,
) -> None:
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)
    insert = """
        INSERT INTO site_repositories (id, site_id, provider, host, owner, repo, repository_id, owner_id)
        VALUES ($1, $2, 'github', 'https://github.com', 'minbzk', 'website', 1, 2)
    """
    await db_connection.execute(insert, uuid.uuid4(), site_id)
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db_connection.transaction():
            await db_connection.execute(insert, uuid.uuid4(), site_id)

    with pytest.raises(asyncpg.InvalidTextRepresentationError):
        async with db_connection.transaction():
            await db_connection.execute(
                insert.replace("'github'", "'gitlab'"), uuid.uuid4(), await _make_site(db_connection, group_id)
            )

    await db_connection.execute("DELETE FROM sites WHERE id = $1", site_id)
    assert await db_connection.fetchval("SELECT count(*) FROM site_repositories") == 0


async def test_an_approved_device_authorization_names_its_member(db_connection: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        async with db_connection.transaction():
            await db_connection.execute(
                """
                INSERT INTO cli_device_authorizations
                    (id, device_selector, device_hash, user_code_hash, status, expires_at)
                VALUES ($1, 'sel', 'hash', 'code', 'approved', now() + interval '10 minutes')
                """,
                uuid.uuid4(),
            )


async def test_cli_sessions_and_refresh_tokens_go_with_their_member(db_connection: asyncpg.Connection) -> None:
    member_id = await _make_member(db_connection)
    session_id = await db_connection.fetchval(
        """
        INSERT INTO cli_sessions
            (id, member_id, expires_at, max_expires_at, access_selector, access_hash, access_expires_at)
        VALUES ($1, $2, now() + interval '30 days', now() + interval '90 days', 'sel', 'hash', now())
        RETURNING id
        """,
        uuid.uuid4(),
        member_id,
    )
    await db_connection.execute(
        "INSERT INTO cli_refresh_tokens (id, session_id, selector, verifier_hash) VALUES ($1, $2, 'r', 'h')",
        uuid.uuid4(),
        session_id,
    )
    await db_connection.execute("DELETE FROM members WHERE id = $1", member_id)
    assert await db_connection.fetchval("SELECT count(*) FROM cli_sessions") == 0
    assert await db_connection.fetchval("SELECT count(*) FROM cli_refresh_tokens") == 0


async def test_expiry_is_required_on_keys(db_connection: asyncpg.Connection) -> None:
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)

    with pytest.raises(asyncpg.exceptions.NotNullViolationError):
        async with db_connection.transaction():
            await db_connection.execute(
                """
                INSERT INTO access_keys (id, site_id, label, selector, verifier_hash)
                VALUES ($1, $2, 'test', $3, 'hash')
                """,
                uuid.uuid4(),
                site_id,
                f"selector-{uuid.uuid4().hex[:8]}",
            )


@pytest.mark.parametrize("reserved", sorted(RESERVED_SLUGS))
async def test_group_slug_reserved_refused(
    db_connection: asyncpg.Connection, reserved: str
) -> None:
    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute(
                """
                INSERT INTO groups (id, slug, name, default_access_base)
                VALUES ($1, $2, 'Test', 'public')
                """,
                uuid.uuid4(),
                reserved,
            )


async def test_ck_group_slug_covers_exactly_the_constant(
    db_connection: asyncpg.Connection,
) -> None:
    definition = await db_connection.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'ck_groups_slug_reserved'"
    )
    assert definition is not None
    for slug in RESERVED_SLUGS:
        assert slug in definition


async def test_last_group_admin_refused_by_trigger(db_connection: asyncpg.Connection) -> None:
    """The application already refuses this with a 409; the trigger is the
    layer underneath, so a groups cannot run empty through a direct DELETE
    either."""
    group_id, member_id = uuid.uuid4(), uuid.uuid4()
    await db_connection.execute(
        "INSERT INTO groups (id, slug, name, default_access_base) VALUES ($1, $2, $3, 'site_team')",
        group_id,
        f"trigger-{group_id.hex[:8]}",
        "Triggerproef",
    )
    await db_connection.execute(
        "INSERT INTO members (id, sso_subject, email, platform_role, status) VALUES ($1, $2, $3, 'member', 'active')",
        member_id,
        f"sub-{member_id.hex[:8]}",
        f"{member_id.hex[:8]}@example.nl",
    )
    await db_connection.execute(
        "INSERT INTO group_members (group_id, member_id, role) VALUES ($1, $2, 'admin')",
        group_id,
        member_id,
    )

    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute(
                "DELETE FROM group_members WHERE group_id = $1 AND member_id = $2", group_id, member_id
            )

    remaining = await db_connection.fetchval(
        "SELECT count(*) FROM group_members WHERE group_id = $1", group_id
    )
    assert remaining == 1


async def test_deleting_a_group_does_clean_up_the_last_admin_role(
    db_connection: asyncpg.Connection,
) -> None:
    """The trigger must not block the cascade: when the group itself is deleted
    the group no longer exists, and an empty membership is then exactly right."""
    group_id, member_id = uuid.uuid4(), uuid.uuid4()
    await db_connection.execute(
        "INSERT INTO groups (id, slug, name, default_access_base) VALUES ($1, $2, $3, 'site_team')",
        group_id,
        f"cascade-{group_id.hex[:8]}",
        "Cascadeproef",
    )
    await db_connection.execute(
        "INSERT INTO members (id, sso_subject, email, platform_role, status) VALUES ($1, $2, $3, 'member', 'active')",
        member_id,
        f"sub-{member_id.hex[:8]}",
        f"{member_id.hex[:8]}@example.nl",
    )
    await db_connection.execute(
        "INSERT INTO group_members (group_id, member_id, role) VALUES ($1, $2, 'admin')",
        group_id,
        member_id,
    )

    await db_connection.execute("DELETE FROM groups WHERE id = $1", group_id)

    assert await db_connection.fetchval("SELECT count(*) FROM group_members WHERE group_id = $1", group_id) == 0


# The storage tests stop at their own revision: 0007 drops the column they read.
_STORAGE_MOVE = "0003_storage_by_site_id"


@pytest.fixture
async def own_database(postgres_container):
    """A database of its own at the revision before the storage move, so the
    shared one stays at head for every other test."""
    base = postgres_container.get_connection_url(driver=None)
    name = f"opslag_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(base)
    await admin.execute(f'CREATE DATABASE "{name}"')
    dsn = f"{base.rsplit('/', 1)[0]}/{name}"
    try:
        await asyncio.to_thread(_alembic, dsn, "upgrade", "0002_retention_and_repo_ids")
        yield dsn
    finally:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


async def _slug_layout_versions(
    dsn: str, root: pathlib.Path, sites: int, per_site: int, target: str = "live"
) -> list[dict]:
    """Versions as the layout before site ids stored them: a row with
    storage_ref {group}/{site}/{id} and an index.html in that directory, in a
    group of their own. A preview version gets its preview row too."""
    conn = await asyncpg.connect(dsn)
    try:
        member_id = await _make_member(conn)
        group_id = await _make_group(conn)
        group_slug = await conn.fetchval("SELECT slug FROM groups WHERE id = $1", group_id)
        versions = []
        for _ in range(sites):
            site_id = await _make_site(conn, group_id)
            site_slug = await conn.fetchval("SELECT slug FROM sites WHERE id = $1", site_id)
            for index in range(per_site):
                version_id = uuid.uuid4()
                old = f"{group_slug}/{site_slug}/{version_id}"
                await conn.execute(
                    "INSERT INTO versions (id, site_id, target, storage_ref, member_id) "
                    "VALUES ($1, $2, $3, $4, $5)",
                    version_id,
                    site_id,
                    target,
                    old,
                    member_id,
                )
                if target == "preview":
                    await conn.execute(
                        "INSERT INTO previews (id, site_id, ref, version_id) VALUES ($1, $2, $3, $4)",
                        uuid.uuid4(),
                        site_id,
                        f"pr-{index}",
                        version_id,
                    )
                (root / old).mkdir(parents=True)
                (root / old / "index.html").write_text(str(version_id))
                versions.append({"id": version_id, "old": old, "new": f"{site_id}/{version_id}"})
        return versions
    finally:
        await conn.close()


async def _storage_refs(dsn: str) -> dict[uuid.UUID, str]:
    conn = await asyncpg.connect(dsn)
    try:
        return {row["id"]: row["storage_ref"] for row in await conn.fetch("SELECT id, storage_ref FROM versions")}
    finally:
        await conn.close()


async def test_the_storage_migration_moves_every_version_to_its_site_id_and_back(
    own_database: str, tmp_path: pathlib.Path, monkeypatch, caplog
) -> None:
    root = tmp_path / "content"
    versions = await _slug_layout_versions(own_database, root, sites=2, per_site=2)
    group_slug, site_slug = versions[0]["old"].split("/")[:2]
    # Left by a publish that never inserted its row.
    stray = root / group_slug / site_slug / str(uuid.uuid4())
    stray.mkdir()
    # Not a group Toon knows: perhaps an operator's, so it stays.
    (root / "backup").mkdir()
    (root / "_tmp").mkdir()
    monkeypatch.setenv("TOON_CONTENT_ROOT", str(root))

    await asyncio.to_thread(_alembic, own_database, "upgrade", _STORAGE_MOVE)

    assert await _storage_refs(own_database) == {version["id"]: version["new"] for version in versions}
    for version in versions:
        assert (root / version["new"] / "index.html").read_text() == str(version["id"])
        assert not (root / version["old"]).exists()
    assert not (root / group_slug).exists()
    (reclaimed,) = (root / "_reclaimed").iterdir()
    assert reclaimed.name.endswith(f"-{group_slug}")
    assert (reclaimed / site_slug / stray.name).is_dir()
    assert (root / "backup").is_dir()
    assert any("backup" in record.getMessage() for record in caplog.records)

    await asyncio.to_thread(_alembic, own_database, "downgrade", "0002_retention_and_repo_ids")

    assert await _storage_refs(own_database) == {version["id"]: version["old"] for version in versions}
    for version in versions:
        assert (root / version["old"] / "index.html").read_text() == str(version["id"])
    assert {entry.name for entry in root.iterdir()} == {group_slug, "_reclaimed", "_tmp", "backup"}

    await asyncio.to_thread(_alembic, own_database, "upgrade", _STORAGE_MOVE)

    assert await _storage_refs(own_database) == {version["id"]: version["new"] for version in versions}


async def test_the_storage_migration_moves_every_group_and_every_target(
    own_database: str, tmp_path: pathlib.Path, monkeypatch
) -> None:
    """Two groups, live and preview versions: the migration keys on the row,
    not on the group or the target, so every one of them moves."""
    root = tmp_path / "content"
    live = await _slug_layout_versions(own_database, root, sites=2, per_site=2)
    previews = await _slug_layout_versions(own_database, root, sites=1, per_site=2, target="preview")
    versions = live + previews
    group_slugs = {version["old"].split("/")[0] for version in versions}
    assert len(group_slugs) == 2
    monkeypatch.setenv("TOON_CONTENT_ROOT", str(root))

    await asyncio.to_thread(_alembic, own_database, "upgrade", _STORAGE_MOVE)

    assert await _storage_refs(own_database) == {version["id"]: version["new"] for version in versions}
    for version in versions:
        assert (root / version["new"] / "index.html").read_text() == str(version["id"])
        assert not (root / version["old"]).exists()
    assert not any((root / slug).exists() for slug in group_slugs)
    assert not (root / "_reclaimed").exists()


async def test_the_storage_migration_stops_at_a_target_that_holds_files(
    own_database: str, tmp_path: pathlib.Path, monkeypatch
) -> None:
    """The new place of a version is already taken by a directory with files
    in it, which no run of this migration produces: it is not ours to replace.
    The migration fails before any row is rewritten and loses neither tree."""
    root = tmp_path / "content"
    (version,) = await _slug_layout_versions(own_database, root, sites=1, per_site=1)
    taken = root / version["new"]
    taken.mkdir(parents=True)
    (taken / "index.html").write_text("someone else's")
    monkeypatch.setenv("TOON_CONTENT_ROOT", str(root))

    with pytest.raises(OSError):
        await asyncio.to_thread(_alembic, own_database, "upgrade", _STORAGE_MOVE)

    assert await _storage_refs(own_database) == {version["id"]: version["old"]}
    assert (root / version["old"] / "index.html").read_text() == str(version["id"])
    assert (taken / "index.html").read_text() == "someone else's"


async def test_the_storage_migration_takes_over_an_empty_target(
    own_database: str, tmp_path: pathlib.Path, monkeypatch
) -> None:
    """An empty directory at the new place (a mkdir of a run that stopped right
    after it) holds nothing that can be lost, and the rename takes its place."""
    root = tmp_path / "content"
    (version,) = await _slug_layout_versions(own_database, root, sites=1, per_site=1)
    (root / version["new"]).mkdir(parents=True)
    monkeypatch.setenv("TOON_CONTENT_ROOT", str(root))

    await asyncio.to_thread(_alembic, own_database, "upgrade", _STORAGE_MOVE)

    assert await _storage_refs(own_database) == {version["id"]: version["new"]}
    assert (root / version["new"] / "index.html").read_text() == str(version["id"])
    assert not (root / version["old"]).exists()


async def test_the_storage_migration_finishes_a_run_that_stopped_halfway(
    own_database: str, tmp_path: pathlib.Path, monkeypatch
) -> None:
    """The renames are outside the transaction: a run that died after moving
    the first directory left its row pointing at the old place."""
    root = tmp_path / "content"
    moved, waiting = await _slug_layout_versions(own_database, root, sites=1, per_site=2)
    (root / moved["new"]).parent.mkdir(parents=True)
    (root / moved["old"]).rename(root / moved["new"])
    monkeypatch.setenv("TOON_CONTENT_ROOT", str(root))

    await asyncio.to_thread(_alembic, own_database, "upgrade", _STORAGE_MOVE)

    assert await _storage_refs(own_database) == {moved["id"]: moved["new"], waiting["id"]: waiting["new"]}
    assert (root / moved["new"] / "index.html").is_file()
    assert (root / waiting["new"] / "index.html").is_file()


async def test_the_storage_migration_rewrites_a_row_whose_directory_is_missing(
    own_database: str, tmp_path: pathlib.Path, monkeypatch, caplog
) -> None:
    root = tmp_path / "content"
    present, missing = await _slug_layout_versions(own_database, root, sites=1, per_site=2)
    (root / missing["old"] / "index.html").unlink()
    (root / missing["old"]).rmdir()
    monkeypatch.setenv("TOON_CONTENT_ROOT", str(root))

    await asyncio.to_thread(_alembic, own_database, "upgrade", _STORAGE_MOVE)

    assert await _storage_refs(own_database) == {present["id"]: present["new"], missing["id"]: missing["new"]}
    assert any(missing["old"] in record.getMessage() for record in caplog.records)


async def test_the_storage_migration_refuses_a_volume_without_any_version(
    own_database: str, tmp_path: pathlib.Path, monkeypatch
) -> None:
    """A volume that is not mounted: every row would end up pointing at a
    path the real volume lacks, and the cleanup would then reclaim it all."""
    versions = await _slug_layout_versions(own_database, tmp_path / "content", sites=1, per_site=2)
    empty = tmp_path / "leeg"
    empty.mkdir()
    monkeypatch.setenv("TOON_CONTENT_ROOT", str(empty))

    with pytest.raises(RuntimeError, match="is the content volume mounted"):
        await asyncio.to_thread(_alembic, own_database, "upgrade", _STORAGE_MOVE)

    assert await _storage_refs(own_database) == {version["id"]: version["old"] for version in versions}
    assert list(empty.iterdir()) == []


async def test_the_storage_migration_needs_the_content_root_when_there_is_something_to_move(
    own_database: str, tmp_path: pathlib.Path, monkeypatch
) -> None:
    versions = await _slug_layout_versions(own_database, tmp_path / "content", sites=1, per_site=1)
    monkeypatch.delenv("TOON_CONTENT_ROOT", raising=False)

    with pytest.raises(RuntimeError, match="TOON_CONTENT_ROOT"):
        await asyncio.to_thread(_alembic, own_database, "upgrade", _STORAGE_MOVE)

    assert await _storage_refs(own_database) == {version["id"]: version["old"] for version in versions}


def _storage_migration():
    """Loaded by path: alembic never imports a migration as a module of a package."""
    path = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / f"{_STORAGE_MOVE}.py"
    spec = importlib.util.spec_from_file_location("toon_storage_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Moving something that is not on the volume stops at the check that names the
# root it looked under, before it touches the database: that shows the root it
# read, without a database.
_A_MOVE = [(uuid.uuid4(), "group/site/version", "site/version")]


async def test_the_storage_migration_reads_the_new_name_of_the_content_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOON_CONTENT_ROOT", str(tmp_path / "new"))

    with pytest.raises(RuntimeError, match=f"No version directory found under {tmp_path / 'new'}"):
        _storage_migration()._move(_A_MOVE)


async def test_the_storage_migration_does_not_read_the_old_name_of_the_content_root(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TOON_CONTENT_ROOT", raising=False)
    monkeypatch.setenv("PLAK_CONTENT_ROOT", str(tmp_path / "old"))

    with pytest.raises(RuntimeError, match="TOON_CONTENT_ROOT is not set"):
        _storage_migration()._move(_A_MOVE)


async def test_the_storage_migration_names_the_new_variable_without_a_content_root(monkeypatch) -> None:
    monkeypatch.delenv("TOON_CONTENT_ROOT", raising=False)

    with pytest.raises(RuntimeError, match="TOON_CONTENT_ROOT is not set"):
        _storage_migration()._move(_A_MOVE)


async def test_the_storage_migration_needs_no_content_root_when_nothing_moves(monkeypatch) -> None:
    monkeypatch.delenv("TOON_CONTENT_ROOT", raising=False)

    assert _storage_migration()._move([(uuid.uuid4(), "site/version", "site/version")]) is None


def _offline_upgrade() -> str:
    """alembic/env.py without a database: the first migration, as SQL."""
    from alembic.config import Config

    from alembic import command

    root = pathlib.Path(__file__).resolve().parents[1] / "alembic"
    output = io.StringIO()
    cfg = Config(output_buffer=output)
    cfg.set_main_option("script_location", str(root))
    cfg.set_main_option("version_locations", str(root / "versions"))
    cfg.set_main_option("path_separator", "os")
    command.upgrade(cfg, "0001_base", sql=True)
    return output.getvalue()


_A_DSN = "postgresql+asyncpg://user:password@db.invalid/app"


async def test_the_migrations_read_the_new_database_url(monkeypatch) -> None:
    monkeypatch.setenv("TOON_DB_URL", _A_DSN)

    assert "CREATE TABLE" in _offline_upgrade()


@pytest.mark.parametrize(
    "environment",
    [{}, {"PLAK_DB_URL": _A_DSN}, {"TOON_DB_URL": "", "PLAK_DB_URL": _A_DSN}],
    ids=["neither-name", "old-name-alone", "empty-new-value"],
)
async def test_the_migrations_refuse_without_the_new_database_url(monkeypatch, environment) -> None:
    monkeypatch.delenv("TOON_DB_URL", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="TOON_DB_URL"):
        _offline_upgrade()


async def test_dropping_storage_ref_goes_down_and_up_again(postgres_container) -> None:
    """Going down fills storage_ref in from the ids again, as 0003 left it."""
    base = postgres_container.get_connection_url(driver=None)
    name = f"opslagverwijzing_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(base)
    await admin.execute(f'CREATE DATABASE "{name}"')
    dsn = f"{base.rsplit('/', 1)[0]}/{name}"
    try:
        await asyncio.to_thread(_alembic, dsn, "upgrade", "head")
        conn = await asyncpg.connect(dsn)
        try:
            member_id = await _make_member(conn)
            site_id = await _make_site(conn, await _make_group(conn))
            version_ids = [await _make_version(conn, site_id, member_id) for _ in range(2)]
        finally:
            await conn.close()
        assert "storage_ref" not in await _version_columns(dsn)

        await asyncio.to_thread(_alembic, dsn, "downgrade", "0006_versions_and_keys_indexes")

        assert await _storage_refs(dsn) == {version_id: f"{site_id}/{version_id}" for version_id in version_ids}
        assert (await _version_columns(dsn))["storage_ref"] == "NO"

        await asyncio.to_thread(_alembic, dsn, "upgrade", "head")

        assert "storage_ref" not in await _version_columns(dsn)
    finally:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


async def _version_columns(dsn: str) -> dict[str, str]:
    """Every column of versions, with whether it is nullable."""
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = 'versions'"
        )
        return {row["column_name"]: row["is_nullable"] for row in rows}
    finally:
        await conn.close()


_LINK_INSERT = """
    INSERT INTO site_repositories (id, site_id, provider, host, owner, repo, repository_id, owner_id)
    VALUES ($1, $2, 'github', 'https://github.com', 'minbzk', 'website', 1001, 2002)
"""


async def _link_before_the_site_id(dsn: str) -> uuid.UUID:
    """A database at 0003 with one linked site; returns its group."""
    await asyncio.to_thread(_alembic, dsn, "upgrade", "0003_storage_by_site_id")
    conn = await asyncpg.connect(dsn)
    try:
        group_id = await _make_group(conn)
        await conn.execute(_LINK_INSERT, uuid.uuid4(), await _make_site(conn, group_id))
    finally:
        await conn.close()
    return group_id


async def _site_id_required(dsn: str) -> list[bool] | None:
    """site_id_required of every link, oldest site first; None without the column."""
    conn = await asyncpg.connect(dsn)
    try:
        columns = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'site_repositories'"
        )
        if "site_id_required" not in {row["column_name"] for row in columns}:
            return None
        rows = await conn.fetch(
            "SELECT link.site_id_required FROM site_repositories AS link "
            "JOIN sites ON sites.id = link.site_id ORDER BY sites.created_at, sites.slug"
        )
        return [row["site_id_required"] for row in rows]
    finally:
        await conn.close()


# migrated_dsn first: the autouse cleanup would otherwise migrate the shared
# database from inside the running event loop when this test runs alone.
@pytest.mark.usefixtures("migrated_dsn")
async def test_the_site_id_migration_lets_existing_links_go_without_and_requires_it_of_new_ones(
    own_database: str,
) -> None:
    """A link from before the migration keeps accepting a CI token without a
    site id; one made afterwards gets the column default and requires it."""
    group_id = await _link_before_the_site_id(own_database)

    await asyncio.to_thread(_alembic, own_database, "upgrade", "0004_site_id_required")
    conn = await asyncpg.connect(own_database)
    try:
        await conn.execute(_LINK_INSERT, uuid.uuid4(), await _make_site(conn, group_id))
    finally:
        await conn.close()

    assert await _site_id_required(own_database) == [False, True]


@pytest.mark.usefixtures("migrated_dsn")
async def test_the_site_id_migration_requires_it_of_a_repository_linked_to_several_sites(
    own_database: str,
) -> None:
    """A link someone made from their own site to another's repository, a
    typo of an address say, must not keep accepting that repository's
    workflows without a site id; the repository's own link loses the
    exemption with it. Only a repository linked to one site keeps it. The
    repository is (provider, host, repository_id): the same number on
    another forge is another repository."""
    await asyncio.to_thread(_alembic, own_database, "upgrade", "0003_storage_by_site_id")
    links = {
        "own": ("github", "https://github.com", 1001),
        "planted": ("github", "https://github.com", 1001),
        "single": ("github", "https://github.com", 3003),
        "same-number-elsewhere": ("forgejo", "https://code.overheid.nl", 1001),
    }
    conn = await asyncpg.connect(own_database)
    try:
        group_id = await _make_group(conn)
        sites = {}
        for name, (provider, host, repository_id) in links.items():
            sites[name] = await _make_site(conn, group_id)
            await conn.execute(
                """
                INSERT INTO site_repositories (id, site_id, provider, host, owner, repo, repository_id, owner_id)
                VALUES ($1, $2, $3, $4, 'minbzk', 'website', $5, 2002)
                """,
                uuid.uuid4(),
                sites[name],
                provider,
                host,
                repository_id,
            )
    finally:
        await conn.close()

    await asyncio.to_thread(_alembic, own_database, "upgrade", "0004_site_id_required")

    conn = await asyncpg.connect(own_database)
    try:
        required = {
            name: await conn.fetchval(
                "SELECT site_id_required FROM site_repositories WHERE site_id = $1", site_id
            )
            for name, site_id in sites.items()
        }
    finally:
        await conn.close()
    assert required == {"own": True, "planted": True, "single": False, "same-number-elsewhere": False}


@pytest.mark.usefixtures("migrated_dsn")
async def test_the_site_id_migration_will_not_go_down_while_a_link_requires_the_site_id(
    own_database: str,
) -> None:
    """Upgrading again would set every link back to false, and so silently
    reopen what was closed; the refusal leaves everything as it was."""
    group_id = await _link_before_the_site_id(own_database)
    await asyncio.to_thread(_alembic, own_database, "upgrade", "0004_site_id_required")
    conn = await asyncpg.connect(own_database)
    try:
        await conn.execute(_LINK_INSERT, uuid.uuid4(), await _make_site(conn, group_id))
    finally:
        await conn.close()

    with pytest.raises(RuntimeError, match=r"while repository links require a site id \(1 with"):
        await asyncio.to_thread(_alembic, own_database, "downgrade", "0003_storage_by_site_id")

    assert await _site_id_required(own_database) == [False, True]
    conn = await asyncpg.connect(own_database)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0004_site_id_required"
    finally:
        await conn.close()


@pytest.mark.usefixtures("migrated_dsn")
async def test_the_site_id_migration_goes_down_while_every_link_still_goes_without(
    own_database: str,
) -> None:
    await _link_before_the_site_id(own_database)
    await asyncio.to_thread(_alembic, own_database, "upgrade", "0004_site_id_required")
    assert await _site_id_required(own_database) == [False]

    await asyncio.to_thread(_alembic, own_database, "downgrade", "0003_storage_by_site_id")

    assert await _site_id_required(own_database) is None
    conn = await asyncpg.connect(own_database)
    try:
        assert await conn.fetchval("SELECT count(*) FROM site_repositories") == 1
        trigger = "site_repositories_site_id_required"
        assert await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgname = $1", trigger) == 0
        assert await conn.fetchval("SELECT count(*) FROM pg_proc WHERE proname = $1", trigger) == 0
        # Without the trigger an update of a link works as before 0004.
        await conn.execute("UPDATE site_repositories SET repository_id = 5005")
    finally:
        await conn.close()


async def _link_at_head(conn: asyncpg.Connection, *, required: bool) -> uuid.UUID:
    site_id = await _make_site(conn, await _make_group(conn))
    await conn.execute(
        """
        INSERT INTO site_repositories
            (id, site_id, provider, host, owner, repo, repository_id, owner_id, site_id_required)
        VALUES ($1, $2, 'github', 'https://github.com', 'minbzk', 'website', 1001, 2002, $3)
        """,
        uuid.uuid4(),
        site_id,
        required,
    )
    return site_id


async def _required_after(conn: asyncpg.Connection, site_id: uuid.UUID, change: str) -> bool:
    # `change` is one of the fixed texts of the parametrize lists below.
    await conn.execute(f"UPDATE site_repositories SET {change} WHERE site_id = $1", site_id)  # noqa: S608
    return await conn.fetchval("SELECT site_id_required FROM site_repositories WHERE site_id = $1", site_id)


@pytest.mark.parametrize(
    "change",
    ["site_id_required = false", "site_id_required = false, live_branch = 'main'", "repository_id = repository_id"],
)
async def test_the_database_keeps_a_required_site_id_required(db_connection: asyncpg.Connection, change) -> None:
    """The rule holds in the database itself, so also for code that knows
    nothing of the column, such as the image before 0004 after a rollback."""
    site_id = await _link_at_head(db_connection, required=True)
    assert await _required_after(db_connection, site_id, change) is True


@pytest.mark.parametrize(
    ("change", "required"),
    [
        ("repository_id = 5005", True),
        ("host = 'https://code.overheid.nl'", True),
        ("provider = 'forgejo', host = 'https://code.overheid.nl'", True),
        ("site_id_required = true", True),
        # The same repository: renamed, transferred, another live branch.
        ("owner = 'ander', repo = 'andere-naam', owner_id = 9999", False),
        ("live_branch = 'release', ids_confirmed = true", False),
    ],
)
async def test_the_database_requires_the_site_id_once_another_repository_is_linked(
    db_connection: asyncpg.Connection, change, required
) -> None:
    site_id = await _link_at_head(db_connection, required=False)
    assert await _required_after(db_connection, site_id, change) is required


async def _slug_namespace(conn: asyncpg.Connection) -> tuple[dict, dict]:
    groups = {
        row["slug"]: (row["group_id"], row["retired_at"]) for row in await conn.fetch("SELECT * FROM group_slugs")
    }
    sites = {
        (row["group_id"], row["slug"]): (row["site_id"], row["retired_at"])
        for row in await conn.fetch("SELECT * FROM site_slugs")
    }
    return groups, sites


# The two tables, the four triggers and the four functions 0005 brings.
_SLUG_NAMESPACE_OBJECTS = """
    SELECT
        (SELECT count(*) FROM pg_tables WHERE tablename IN ('group_slugs', 'site_slugs'))
        + (SELECT count(*) FROM pg_trigger WHERE tgname IN
            ('group_slugs_insert', 'group_slugs_rename', 'site_slugs_insert', 'site_slugs_rename'))
        + (SELECT count(*) FROM pg_proc WHERE proname IN
            ('group_slugs_on_insert', 'group_slugs_on_rename', 'site_slugs_on_insert', 'site_slugs_on_rename'))
"""


@pytest.mark.usefixtures("migrated_dsn")
async def test_the_slug_namespace_takes_in_every_slug_and_goes_down_again(own_database: str) -> None:
    """Up: every current slug becomes the current row of its group or site,
    and the triggers keep the namespace from then on. Down: the tables, the
    triggers and the functions go, and every retired slug is free at once."""
    await asyncio.to_thread(_alembic, own_database, "upgrade", "0004_site_id_required")
    conn = await asyncpg.connect(own_database)
    try:
        group_ids = [await _make_group(conn), await _make_group(conn)]
        for group_id in (group_ids[0], group_ids[0], group_ids[1]):
            await _make_site(conn, group_id)
        groups = {row["slug"]: row["id"] for row in await conn.fetch("SELECT slug, id FROM groups")}
        sites = {
            (row["group_id"], row["slug"]): row["id"]
            for row in await conn.fetch("SELECT group_id, slug, id FROM sites")
        }
    finally:
        await conn.close()

    await asyncio.to_thread(_alembic, own_database, "upgrade", "0005_slug_namespace")

    conn = await asyncpg.connect(own_database)
    try:
        assert await _slug_namespace(conn) == (
            {slug: (group_id, None) for slug, group_id in groups.items()},
            {key: (site_id, None) for key, site_id in sites.items()},
        )
        assert await conn.fetchval(_SLUG_NAMESPACE_OBJECTS) == 10
        old_slug = await conn.fetchval("SELECT slug FROM groups WHERE id = $1", group_ids[0])
        await conn.execute("UPDATE groups SET slug = 'hernoemd' WHERE id = $1", group_ids[0])
        owner, retired_at = (await _slug_namespace(conn))[0][old_slug]
        assert owner == group_ids[0]
        assert retired_at is not None
    finally:
        await conn.close()

    await asyncio.to_thread(_alembic, own_database, "downgrade", "0004_site_id_required")

    conn = await asyncpg.connect(own_database)
    try:
        assert await conn.fetchval(_SLUG_NAMESPACE_OBJECTS) == 0
        await conn.execute(
            "INSERT INTO groups (id, slug, name, default_access_base) VALUES ($1, $2, 'Oud', 'public')",
            uuid.uuid4(),
            old_slug,
        )
    finally:
        await conn.close()


# -- Site feedback (0008_feedback) ------------------------------------------

_FEEDBACK_TABLES = ("feedback_items", "feedback_replies", "feedback_screenshots")
_FEEDBACK_TYPES = ("feedback_identity", "feedback_kind", "feedback_author", "feedback_status", "feedback_reply_author")
_FEEDBACK_SITE_COLUMNS = ("feedback_enabled", "feedback_identity", "feedback_live")

_POINT_ANCHOR = json.dumps(
    {
        "selectors": [{"type": "CssSelector", "value": "main > p:nth-of-type(3)"}],
        "point": {"x": 0.5, "y": 0.25, "pageX": 640, "pageY": 1200},
    }
)
_TEXT_ANCHOR = json.dumps(
    {
        "selectors": [
            {"type": "TextQuoteSelector", "exact": "2,4 miljoen", "prefix": "begroting van ", "suffix": " euro"}
        ],
        "point": None,
    }
)
_POINT = {"kind": "point", "anchor": _POINT_ANCHOR}
_TEXT = {"kind": "text", "anchor": _TEXT_ANCHOR, "quote": "2,4 miljoen"}
_ACCOUNT = {"author_kind": "account", "author_sub": "sub-jan"}
_VIEWPORT = {"width": 1280, "height": 800, "dpr": 2}
_RESOLVED_AT = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

# Stands for a real member id, which a parametrize list cannot hold.
_A_MEMBER = object()

# A PNG signature is enough here: the database checks the size, not the image.
_PNG = b"\x89PNG\r\n\x1a\n"


async def _insert(conn: asyncpg.Connection, table: str, values: dict[str, object]) -> None:
    placeholders = ", ".join(f"${index}" for index in range(1, len(values) + 1))
    # Table and column names come from this module, never from input.
    statement = f"INSERT INTO {table} ({', '.join(values)}) VALUES ({placeholders})"  # noqa: S608
    await conn.execute(statement, *values.values())


async def _make_item(conn: asyncpg.Connection, site_id: uuid.UUID, **columns: object) -> uuid.UUID:
    """A valid item on `site_id`, a whole-page remark on live without a name,
    with `columns` put in place."""
    item_id = uuid.uuid4()
    values: dict[str, object] = {
        "id": item_id,
        "site_id": site_id,
        "target": "live",
        "page_path": "",
        "kind": "page",
        "comment": "Klopt dit bedrag?",
        "author_kind": "anonymous",
        **columns,
    }
    await _insert(conn, "feedback_items", values)
    return item_id


async def _make_reply(conn: asyncpg.Connection, item_id: uuid.UUID, **columns: object) -> uuid.UUID:
    """A valid reply from the submitter, with `columns` put in place."""
    reply_id = uuid.uuid4()
    values = {"id": reply_id, "item_id": item_id, "author_kind": "giver", "body": "Dank je!", **columns}
    await _insert(conn, "feedback_replies", values)
    return reply_id


async def _make_screenshot(conn: asyncpg.Connection, item_id: uuid.UUID, **columns: object) -> None:
    data = columns.get("data", _PNG)
    assert isinstance(data, bytes)
    values = {
        "item_id": item_id,
        "media_type": "image/png",
        "data": data,
        "size_bytes": len(data),
        "width": 1280,
        "height": 800,
        **columns,
    }
    await _insert(conn, "feedback_screenshots", values)


async def _feedback_site(conn: asyncpg.Connection) -> uuid.UUID:
    return await _make_site(conn, await _make_group(conn))


async def _check_that_refuses(conn: asyncpg.Connection, insert: Callable[[], Awaitable[object]]) -> str:
    """The CHECK constraint that refused `insert`; fails the test when the
    row went in. Inside a savepoint, so the test's transaction stays usable."""
    with pytest.raises(asyncpg.CheckViolationError) as refused:
        async with conn.transaction():
            await insert()
    return refused.value.constraint_name


@pytest.mark.parametrize(
    ("type_name", "enum_class"),
    [
        ("feedback_kind", FeedbackKind),
        ("feedback_author", FeedbackAuthor),
        ("feedback_status", FeedbackStatus),
        ("feedback_reply_author", FeedbackReplyAuthor),
        ("feedback_identity", FeedbackIdentity),
    ],
)
async def test_a_feedback_type_holds_the_application_values_in_their_order(
    db_connection: asyncpg.Connection, type_name: str, enum_class: type
) -> None:
    """0008 writes the labels hardcoded. Their order is the sort order in SQL,
    and for the identity setting it is the meaning: each level allows the
    author kinds of the levels before it."""
    assert await _enum_labels(db_connection, type_name) == [member.value for member in enum_class]


async def test_the_feedback_target_uses_the_version_target_type(db_connection: asyncpg.Connection) -> None:
    """Live or preview means the same on an item as on a version, so it is one
    type, which 0008 uses and leaves alone."""
    type_name = await db_connection.fetchval(
        "SELECT udt_name FROM information_schema.columns WHERE table_name = 'feedback_items' AND column_name = 'target'"
    )
    assert type_name == "version_target"
    assert await _enum_labels(db_connection, "version_target") == [target.value for target in VersionTarget]


async def test_a_site_takes_no_feedback_until_an_admin_turns_it_on(db_connection: asyncpg.Connection) -> None:
    """A site that never names the columns takes no feedback; switched on, it
    would take it with an account only, and on previews only."""
    site_id = await _feedback_site(db_connection)
    row = await db_connection.fetchrow(
        "SELECT feedback_enabled, feedback_identity, feedback_live FROM sites WHERE id = $1", site_id
    )
    assert tuple(row) == (False, FeedbackIdentity.ACCOUNT.value, False)


@pytest.mark.parametrize("column", _FEEDBACK_SITE_COLUMNS)
async def test_a_feedback_setting_of_a_site_is_never_null(db_connection: asyncpg.Connection, column: str) -> None:
    site_id = await _feedback_site(db_connection)
    with pytest.raises(asyncpg.NotNullViolationError):
        async with db_connection.transaction():
            # The column name comes from the parametrize list.
            await db_connection.execute(f"UPDATE sites SET {column} = NULL WHERE id = $1", site_id)  # noqa: S608


async def test_the_identity_setting_refuses_an_unknown_level(db_connection: asyncpg.Connection) -> None:
    site_id = await _feedback_site(db_connection)
    with pytest.raises(asyncpg.InvalidTextRepresentationError):
        async with db_connection.transaction():
            await db_connection.execute("UPDATE sites SET feedback_identity = 'iedereen' WHERE id = $1", site_id)


async def test_an_item_comes_in_new(db_connection: asyncpg.Connection) -> None:
    item_id = await _make_item(db_connection, await _feedback_site(db_connection))
    row = await db_connection.fetchrow(
        "SELECT status, status_changed_at, resolved_at, created_at FROM feedback_items WHERE id = $1", item_id
    )
    assert row["status"] == FeedbackStatus.NEW.value
    assert (row["status_changed_at"], row["resolved_at"]) == (None, None)
    assert row["created_at"] is not None


@pytest.mark.parametrize(
    "columns",
    [
        {},
        {"target": "preview", "preview_ref": "pr-42"},
        _POINT,
        _TEXT,
        {**_TEXT, "quote": "x" * 1000},
        {"author_kind": "name", "author_name": "Jan"},
        {"author_kind": "name", "author_name": "x" * 200},
        _ACCOUNT,
        {**_ACCOUNT, "author_name": "Jan Jansen", "author_email": "jan.jansen@example.org"},
        {"page_path": "docs/", "page_title": "Documentatie"},
        {"page_path": "x" * 2048, "page_title": "x" * 300, "comment": "x" * 5000},
        {"comment": "x"},
        {"viewport": json.dumps(_VIEWPORT)},
        {"status": "seen"},
        {"status": "resolved", "resolved_at": _RESOLVED_AT},
    ],
    ids=[
        "page-live-anonymous",
        "preview-with-ref",
        "point",
        "text",
        "longest-quote",
        "name",
        "longest-name",
        "account",
        "account-with-name-and-email",
        "relative-path-and-title",
        "longest-path-title-and-comment",
        "shortest-comment",
        "viewport",
        "seen",
        "resolved",
    ],
)
async def test_an_item_of_every_valid_shape_goes_in(db_connection: asyncpg.Connection, columns: dict) -> None:
    item_id = await _make_item(db_connection, await _feedback_site(db_connection), **columns)
    assert await db_connection.fetchval("SELECT count(*) FROM feedback_items WHERE id = $1", item_id) == 1


@pytest.mark.parametrize(
    ("constraint", "columns"),
    [
        ("ck_feedback_items_preview_ref", {"target": "preview"}),
        ("ck_feedback_items_preview_ref", {"preview_ref": "pr-42"}),
        ("ck_feedback_items_page_path", {"page_path": "x" * 2049}),
        ("ck_feedback_items_page_path", {"page_path": "/docs/"}),
        ("ck_feedback_items_page_title_length", {"page_title": "x" * 301}),
        ("ck_feedback_items_anchor", {"anchor": _POINT_ANCHOR}),
        ("ck_feedback_items_anchor", {"kind": "point"}),
        ("ck_feedback_items_quote", {**_TEXT, "quote": None}),
        ("ck_feedback_items_quote", {**_POINT, "quote": "2,4 miljoen"}),
        ("ck_feedback_items_quote_length", {**_TEXT, "quote": "x" * 1001}),
        ("ck_feedback_items_comment_length", {"comment": ""}),
        ("ck_feedback_items_comment_length", {"comment": "x" * 5001}),
        ("ck_feedback_items_author", {"author_kind": "account"}),
        ("ck_feedback_items_author", {"author_kind": "name"}),
        ("ck_feedback_items_author", {"author_kind": "name", "author_name": "Jan", "author_sub": "sub-jan"}),
        ("ck_feedback_items_author", {"author_kind": "name", "author_name": "Jan", "author_email": "jan@example.org"}),
        ("ck_feedback_items_author", {"author_sub": "sub-jan"}),
        ("ck_feedback_items_author", {"author_name": "Jan"}),
        ("ck_feedback_items_author", {"author_email": "jan@example.org"}),
        ("ck_feedback_items_author_name_length", {"author_kind": "name", "author_name": "x" * 201}),
        ("ck_feedback_items_author_email_lowercase", {**_ACCOUNT, "author_email": "Jan@Example.org"}),
        ("ck_feedback_items_viewport", {"viewport": json.dumps({"width": 1280, "height": 800})}),
        ("ck_feedback_items_viewport", {"viewport": json.dumps({**_VIEWPORT, "zoom": 1})}),
        ("ck_feedback_items_viewport", {"viewport": json.dumps([1280, 800, 2])}),
        ("ck_feedback_items_viewport", {"viewport": json.dumps(1280)}),
        ("ck_feedback_items_resolved_at", {"status": "resolved"}),
        ("ck_feedback_items_resolved_at", {"status": "seen", "resolved_at": _RESOLVED_AT}),
    ],
    ids=[
        "preview-without-ref",
        "live-with-ref",
        "path-too-long",
        "path-with-leading-slash",
        "title-too-long",
        "page-with-anchor",
        "point-without-anchor",
        "text-without-quote",
        "point-with-quote",
        "quote-too-long",
        "empty-comment",
        "comment-too-long",
        "account-without-sub",
        "name-without-name",
        "name-with-sub",
        "name-with-email",
        "anonymous-with-sub",
        "anonymous-with-name",
        "anonymous-with-email",
        "name-too-long",
        "email-not-lowercase",
        "viewport-missing-a-key",
        "viewport-with-an-extra-key",
        "viewport-array",
        "viewport-number",
        "resolved-without-resolved-at",
        "seen-with-resolved-at",
    ],
)
async def test_an_item_that_breaks_a_rule_is_refused(
    db_connection: asyncpg.Connection, constraint: str, columns: dict
) -> None:
    site_id = await _feedback_site(db_connection)
    assert await _check_that_refuses(db_connection, lambda: _make_item(db_connection, site_id, **columns)) == constraint


@pytest.mark.parametrize(
    "columns",
    [
        {},
        {"body": "x" * 5000},
        {"author_kind": "member", "member_id": _A_MEMBER, "author_name": "Jan Jansen"},
        {"author_kind": "member", "member_id": _A_MEMBER, "author_name": "x" * 200},
        {"author_kind": "member", "member_id": _A_MEMBER},
        {"author_kind": "member"},
    ],
    ids=["giver", "longest-body", "member", "longest-name", "member-without-name", "member-gone"],
)
async def test_a_reply_of_every_valid_shape_goes_in(db_connection: asyncpg.Connection, columns: dict) -> None:
    item_id = await _make_item(db_connection, await _feedback_site(db_connection))
    if columns.get("member_id") is _A_MEMBER:
        columns = {**columns, "member_id": await _make_member(db_connection)}
    reply_id = await _make_reply(db_connection, item_id, **columns)
    assert await db_connection.fetchval("SELECT count(*) FROM feedback_replies WHERE id = $1", reply_id) == 1


@pytest.mark.parametrize(
    ("constraint", "columns"),
    [
        ("ck_feedback_replies_giver", {"member_id": _A_MEMBER}),
        ("ck_feedback_replies_giver", {"author_name": "Jan"}),
        ("ck_feedback_replies_author_name_length", {"author_kind": "member", "author_name": "x" * 201}),
        ("ck_feedback_replies_body_length", {"body": ""}),
        ("ck_feedback_replies_body_length", {"body": "x" * 5001}),
    ],
    ids=["giver-with-member", "giver-with-name", "name-too-long", "empty-body", "body-too-long"],
)
async def test_a_reply_that_breaks_a_rule_is_refused(
    db_connection: asyncpg.Connection, constraint: str, columns: dict
) -> None:
    item_id = await _make_item(db_connection, await _feedback_site(db_connection))
    if columns.get("member_id") is _A_MEMBER:
        columns = {**columns, "member_id": await _make_member(db_connection)}
    refused_by = await _check_that_refuses(db_connection, lambda: _make_reply(db_connection, item_id, **columns))
    assert refused_by == constraint


@pytest.mark.parametrize("media_type", ["image/png", "image/jpeg", "image/webp"])
async def test_a_screenshot_of_each_raster_type_goes_in(db_connection: asyncpg.Connection, media_type: str) -> None:
    item_id = await _make_item(db_connection, await _feedback_site(db_connection))
    await _make_screenshot(db_connection, item_id, media_type=media_type)
    size = await db_connection.fetchval("SELECT size_bytes FROM feedback_screenshots WHERE item_id = $1", item_id)
    assert size == len(_PNG)


@pytest.mark.parametrize(
    ("constraint", "columns"),
    [
        ("ck_feedback_screenshots_media_type", {"media_type": "image/gif"}),
        ("ck_feedback_screenshots_media_type", {"media_type": "image/svg+xml"}),
        ("ck_feedback_screenshots_media_type", {"media_type": "IMAGE/PNG"}),
        ("ck_feedback_screenshots_size_bytes", {"data": b"", "size_bytes": 0}),
        ("ck_feedback_screenshots_size_bytes", {"size_bytes": len(_PNG) - 1}),
        ("ck_feedback_screenshots_size_bytes", {"size_bytes": len(_PNG) + 1}),
        ("ck_feedback_screenshots_dimensions", {"width": 0}),
        ("ck_feedback_screenshots_dimensions", {"height": 0}),
        ("ck_feedback_screenshots_dimensions", {"width": -1, "height": -1}),
    ],
    ids=[
        "gif",
        "svg",
        "upper-case",
        "empty",
        "size-below-data",
        "size-above-data",
        "no-width",
        "no-height",
        "negative",
    ],
)
async def test_a_screenshot_that_breaks_a_rule_is_refused(
    db_connection: asyncpg.Connection, constraint: str, columns: dict
) -> None:
    item_id = await _make_item(db_connection, await _feedback_site(db_connection))
    refused_by = await _check_that_refuses(db_connection, lambda: _make_screenshot(db_connection, item_id, **columns))
    assert refused_by == constraint


async def test_an_item_has_at_most_one_screenshot(db_connection: asyncpg.Connection) -> None:
    item_id = await _make_item(db_connection, await _feedback_site(db_connection))
    await _make_screenshot(db_connection, item_id)
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db_connection.transaction():
            await _make_screenshot(db_connection, item_id)


async def _feedback_counts(conn: asyncpg.Connection, item_id: uuid.UUID) -> tuple[int, int, int]:
    return (
        await conn.fetchval("SELECT count(*) FROM feedback_items WHERE id = $1", item_id),
        await conn.fetchval("SELECT count(*) FROM feedback_replies WHERE item_id = $1", item_id),
        await conn.fetchval("SELECT count(*) FROM feedback_screenshots WHERE item_id = $1", item_id),
    )


async def _item_with_reply_and_screenshot(conn: asyncpg.Connection, site_id: uuid.UUID) -> uuid.UUID:
    item_id = await _make_item(conn, site_id)
    await _make_reply(conn, item_id)
    await _make_screenshot(conn, item_id)
    return item_id


async def test_deleting_a_site_takes_its_feedback_along(db_connection: asyncpg.Connection) -> None:
    group_id = await _make_group(db_connection)
    site_id, other_site_id = await _make_site(db_connection, group_id), await _make_site(db_connection, group_id)
    item_id = await _item_with_reply_and_screenshot(db_connection, site_id)
    other_item_id = await _item_with_reply_and_screenshot(db_connection, other_site_id)

    await db_connection.execute("DELETE FROM sites WHERE id = $1", site_id)

    assert await _feedback_counts(db_connection, item_id) == (0, 0, 0)
    assert await _feedback_counts(db_connection, other_item_id) == (1, 1, 1)


async def test_deleting_a_group_takes_the_feedback_of_its_sites_along(db_connection: asyncpg.Connection) -> None:
    group_id = await _make_group(db_connection)
    item_id = await _item_with_reply_and_screenshot(db_connection, await _make_site(db_connection, group_id))

    await db_connection.execute("DELETE FROM groups WHERE id = $1", group_id)

    assert await _feedback_counts(db_connection, item_id) == (0, 0, 0)


async def test_deleting_an_item_takes_its_replies_and_screenshot_along(db_connection: asyncpg.Connection) -> None:
    site_id = await _feedback_site(db_connection)
    item_id = await _item_with_reply_and_screenshot(db_connection, site_id)
    other_item_id = await _item_with_reply_and_screenshot(db_connection, site_id)

    await db_connection.execute("DELETE FROM feedback_items WHERE id = $1", item_id)

    assert await _feedback_counts(db_connection, item_id) == (0, 0, 0)
    assert await _feedback_counts(db_connection, other_item_id) == (1, 1, 1)


async def test_deleting_a_member_keeps_what_they_handled(db_connection: asyncpg.Connection) -> None:
    """The member goes from the item they resolved and from their reply; the
    status and the reply stay, and the reply is still the site team's."""
    member_id = await _make_member(db_connection)
    item_id = await _make_item(
        db_connection,
        await _feedback_site(db_connection),
        status="resolved",
        resolved_at=_RESOLVED_AT,
        status_changed_at=_RESOLVED_AT,
        status_changed_by=member_id,
    )
    reply_id = await _make_reply(
        db_connection, item_id, author_kind="member", member_id=member_id, author_name="Jan Jansen"
    )

    await db_connection.execute("DELETE FROM members WHERE id = $1", member_id)

    item = await db_connection.fetchrow(
        "SELECT status, resolved_at, status_changed_by FROM feedback_items WHERE id = $1", item_id
    )
    assert tuple(item) == (FeedbackStatus.RESOLVED.value, _RESOLVED_AT, None)
    reply = await db_connection.fetchrow("SELECT author_kind, member_id FROM feedback_replies WHERE id = $1", reply_id)
    assert tuple(reply) == (FeedbackReplyAuthor.MEMBER.value, None)


async def test_removing_a_version_keeps_the_feedback_given_on_it(db_connection: asyncpg.Connection) -> None:
    """A redeploy, a preview teardown or the cleanup of old live versions
    removes the version; the item stays and only loses the hint."""
    site_id = await _feedback_site(db_connection)
    version_id = await _make_version(db_connection, site_id, await _make_member(db_connection))
    item_id = await _make_item(db_connection, site_id, target="preview", preview_ref="pr-42", version_id=version_id)

    await db_connection.execute("DELETE FROM versions WHERE id = $1", version_id)

    assert await db_connection.fetchval("SELECT version_id FROM feedback_items WHERE id = $1", item_id) is None
    assert await _feedback_counts(db_connection, item_id) == (1, 0, 0)


_FEEDBACK_INDEXES = {
    "ix_feedback_items_site_id_status_created_at": "(site_id, status, created_at DESC)",
    "ix_feedback_items_site_id_author_sub": "(site_id, author_sub)",
    "ix_feedback_items_version_id": "(version_id)",
    "ix_feedback_items_status_changed_by": "(status_changed_by)",
    "ix_feedback_replies_item_id_created_at": "(item_id, created_at)",
    "ix_feedback_replies_member_id": "(member_id)",
}


async def _feedback_indexes(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch(
        "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(_FEEDBACK_INDEXES)
    )
    return {row["indexname"]: row["indexdef"] for row in rows}


async def test_feedback_is_indexed_for_its_lists_and_its_foreign_keys(db_connection: asyncpg.Connection) -> None:
    """The lists read per site and status, newest first, and per submitter;
    the replies per item, oldest first. The other two carry the SET NULL of a
    removed version or member, which PostgreSQL does not index by itself."""
    indexes = await _feedback_indexes(db_connection)
    assert set(indexes) == set(_FEEDBACK_INDEXES)
    for name, columns in _FEEDBACK_INDEXES.items():
        assert indexes[name].endswith(columns), indexes[name]


@pytest.mark.parametrize(
    "model", [FeedbackItem, FeedbackReply, FeedbackScreenshot], ids=lambda model: model.__tablename__
)
async def test_the_feedback_models_name_the_checks_the_migration_made(
    db_connection: asyncpg.Connection, model: type
) -> None:
    """Nothing compares the models with the migrations as a whole; for these
    tables the names of the CHECKs at least stay in step."""
    rows = await db_connection.fetch(
        "SELECT conname FROM pg_constraint WHERE contype = 'c' AND conrelid = $1::regclass", model.__tablename__
    )
    declared = {
        constraint.name for constraint in model.__table__.constraints if isinstance(constraint, CheckConstraint)
    }
    assert {row["conname"] for row in rows} == declared


async def _feedback_schema(conn: asyncpg.Connection) -> tuple[set[str], set[str], set[str]]:
    """Which of the feedback tables, types and site columns exist."""
    tables = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE tablename = ANY($1::text[])", list(_FEEDBACK_TABLES)
    )
    types = await conn.fetch("SELECT typname FROM pg_type WHERE typname = ANY($1::text[])", list(_FEEDBACK_TYPES))
    columns = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'sites' AND column_name = ANY($1::text[])",
        list(_FEEDBACK_SITE_COLUMNS),
    )
    return (
        {row["tablename"] for row in tables},
        {row["typname"] for row in types},
        {row["column_name"] for row in columns},
    )


async def test_the_feedback_migration_goes_down_and_up_again(postgres_container) -> None:
    """Down takes the tables with every item in them, then the five types and
    the three settings, and leaves the version_target type to the versions.
    Up again, the tables are empty and an existing site takes no feedback."""
    base = postgres_container.get_connection_url(driver=None)
    name = f"feedback_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(base)
    await admin.execute(f'CREATE DATABASE "{name}"')
    dsn = f"{base.rsplit('/', 1)[0]}/{name}"
    everything = (set(_FEEDBACK_TABLES), set(_FEEDBACK_TYPES), set(_FEEDBACK_SITE_COLUMNS))
    try:
        await asyncio.to_thread(_alembic, dsn, "upgrade", "head")
        conn = await asyncpg.connect(dsn)
        try:
            assert await _feedback_schema(conn) == everything
            site_id = await _feedback_site(conn)
            await conn.execute(
                "UPDATE sites SET feedback_enabled = true, feedback_identity = 'anonymous', feedback_live = true "
                "WHERE id = $1",
                site_id,
            )
            await _item_with_reply_and_screenshot(conn, site_id)
        finally:
            await conn.close()

        await asyncio.to_thread(_alembic, dsn, "downgrade", "0007_drop_storage_ref")
        conn = await asyncpg.connect(dsn)
        try:
            assert await _feedback_schema(conn) == (set(), set(), set())
            assert await _enum_labels(conn, "version_target") == [target.value for target in VersionTarget]
            assert await _feedback_indexes(conn) == {}
        finally:
            await conn.close()

        await asyncio.to_thread(_alembic, dsn, "upgrade", "head")
        conn = await asyncpg.connect(dsn)
        try:
            assert await _feedback_schema(conn) == everything
            assert set(await _feedback_indexes(conn)) == set(_FEEDBACK_INDEXES)
            row = await conn.fetchrow(
                "SELECT feedback_enabled, feedback_identity, feedback_live FROM sites WHERE id = $1", site_id
            )
            assert tuple(row) == (False, FeedbackIdentity.ACCOUNT.value, False)
            assert await conn.fetchval("SELECT count(*) FROM feedback_items") == 0
        finally:
            await conn.close()
    finally:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()

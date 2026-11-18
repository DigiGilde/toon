"""The dev seed (dev/seed.py) against a real database.

The seed is the only thing that refills a local environment after a schema
change, so it is exactly the script that rots unnoticed: nothing else imports
it and a stale column name only shows up when someone needs their dev data
back. These tests run it end to end against the migrated test database and a
throwaway content root.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
import uuid
from collections.abc import AsyncIterator
from html.parser import HTMLParser
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from toon.auth.members import PREVIEW_GROUP
from toon.config import Settings
from toon.constants import (
    AccessBase,
    FeedbackIdentity,
    FeedbackKind,
    FeedbackReplyAuthor,
    FeedbackStatus,
    Role,
)
from toon.db import make_engine, make_session_factory
from toon.ingest import precompress
from toon.ingest.store import ContentStore, version_ref
from toon.models.feedback import FeedbackItem, FeedbackReply, FeedbackScreenshot
from toon.models.identity import Group, GroupMember, Member, MemberStatus, PlatformRole, SiteMember
from toon.models.publication import AccessKey, Invitee, Preview, Site, Version, VersionTarget
from toon.serving.feedback_images import acceptable_screenshot
from toon.serving.resolution import locate

DEV_DIR = Path(__file__).resolve().parents[2] / "dev"
SEED_PATH = DEV_DIR / "seed.py"
LOGIN_PAGE = DEV_DIR / "mock-oidc-login.html"
INTERACTIVE_CONFIG = DEV_DIR / "mock-oidc-config.interactive.json"
# The one persona on the login page that the seed must not create.
NEW_VISITOR_SUB = "dev-nieuw"


@pytest.fixture(scope="session")
def seed_module():
    """Loads dev/seed.py by path: it sits outside the installed package on
    purpose, because it is dev tooling. Only the `preview` build target
    carries it (/app/demo_seed.py), and the entrypoint runs it under
    TOON_ENVIRONMENT=preview alone."""
    spec = importlib.util.spec_from_file_location("toon_dev_seed", SEED_PATH)
    module = importlib.util.module_from_spec(spec)
    # Registering before exec: `from __future__ import annotations` leaves the
    # dataclass field types as strings, and dataclasses resolves them through
    # sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def settings(migrated_dsn: str, tmp_path: Path) -> Settings:
    return Settings(
        db_url=migrated_dsn,
        content_root=tmp_path / "content",
        # The seed never logs in; the OIDC fields are here only because
        # Settings refuses to be built without them.
        oidc_issuer="https://idp.example.org",
        oidc_client_id="toon-test",
        oidc_client_auth="client_secret_post",
        oidc_client_secret="client-secret-van-minstens-32-bytes!!",
        session_secret="s" * 32,
        audit_pepper="p" * 32,
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        content_base_url="https://toon.example",
        environment="dev",
        bootstrap_admin_sub="dev-beheerder",
    )


@pytest_asyncio.fixture
async def session_factory(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(settings)
    try:
        yield make_session_factory(engine)
    finally:
        await engine.dispose()


def _version_dirs(root: Path) -> set[str]:
    """Every {site_id}/{version_id} directory on the content root."""
    return {
        str(path.relative_to(root))
        for path in root.glob("*/*")
        if path.is_dir() and path.relative_to(root).parts[0] != "_tmp"
    }


async def _count(db: AsyncSession, model) -> int:
    return await db.scalar(select(func.count()).select_from(model))


def _page_html(root: Path, site_id: uuid.UUID, version_id: uuid.UUID, page_path: str) -> str:
    """A seeded page as the content volume holds it, plain or only gzipped."""
    stored = locate(ContentStore(root), version_ref(site_id, version_id), page_path or "index.html")
    if stored.plain is not None:
        return stored.plain.read_text()
    return gzip.decompress(stored.encoded[precompress.GZIP].read_bytes()).decode()


class TestSeed:
    async def test_two_groups_with_a_different_default_access(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            groups = list(await db.scalars(select(Group).order_by(Group.slug)))
        assert len(groups) == 2
        assert {group.default_access_base for group in groups} == {
            AccessBase.PUBLIC,
            AccessBase.SITE_TEAM,
        }

    async def test_one_group_holds_an_admin_an_editor_and_a_reader(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            group = await db.scalar(select(Group).where(Group.slug == seed_module.GROUP_PUBLIC))
            roles = list(
                await db.scalars(select(GroupMember.role).where(GroupMember.group_id == group.id))
            )
        assert sorted(roles) == sorted([Role.ADMIN, Role.EDITOR, Role.READER])

    async def test_the_bootstrap_member_is_a_platform_admin_and_a_group_admin(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        async with session_factory() as db_pre:
            assert await _count(db_pre, Member) == 0
        await seed_module.seed(settings)
        async with session_factory() as db:
            member = await db.scalar(
                select(Member).where(Member.sso_subject == settings.bootstrap_admin_sub)
            )
            group = await db.scalar(select(Group).where(Group.slug == seed_module.GROUP_PUBLIC))
            role = await db.scalar(
                select(GroupMember.role).where(
                    GroupMember.group_id == group.id, GroupMember.member_id == member.id
                )
            )
        assert member.platform_role == PlatformRole.ADMIN
        assert member.status == MemberStatus.ACTIVE
        assert role == Role.ADMIN

    async def test_one_member_has_a_site_role_without_group_membership(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            site_member = (
                await db.scalars(
                    select(SiteMember).where(
                        SiteMember.member_id.not_in(select(GroupMember.member_id))
                    )
                )
            ).one()
        assert site_member.role == Role.EDITOR

    async def test_a_member_is_deactivated(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            deactivated = list(
                await db.scalars(select(Member).where(Member.status == MemberStatus.DEACTIVATED))
            )
        assert len(deactivated) == 1

    async def test_every_access_base_occurs(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            bases = set(await db.scalars(select(Site.access_base)))
        assert bases == set(AccessBase)

    async def test_an_extra_occurs_beside_a_base_that_already_lets_people_in(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        """The combination the old single level could not express, so the dev
        interface has one site that shows it without anyone setting it up."""
        await seed_module.seed(settings)
        async with session_factory() as db:
            combined = list(
                await db.scalars(
                    select(Site.slug).where(
                        Site.access_base != AccessBase.NOBODY,
                        Site.access_keys.is_(True),
                    )
                )
            )
        assert combined == ["toezichtrapport"]

    async def test_a_secret_link_and_an_invitee_list_exist(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        result = await seed_module.seed(settings)
        selector, _, verifier = result.secret_link_key.partition(".")
        async with session_factory() as db:
            access_key = await db.scalar(select(AccessKey).where(AccessKey.selector == selector))
            key_site = await db.get(Site, access_key.site_id)
            invitees = list(await db.scalars(select(Invitee)))
            invitee_site = await db.get(Site, invitees[0].site_id)
        assert verifier
        assert key_site.access_keys is True
        assert len(invitees) == 3
        assert invitee_site.access_invitees is True

    async def test_the_published_versions_really_stand_on_the_content_volume(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        result = await seed_module.seed(settings)
        async with session_factory() as db:
            rows = await db.execute(select(Version.site_id, Version.id))
            refs = [version_ref(site_id, version_id) for site_id, version_id in rows]
            live = list(
                await db.scalars(select(Site.slug).where(Site.live_version_id.is_not(None)))
            )
        assert result.versions == len(refs) == 6
        assert len(live) == 4
        store = ContentStore(settings.content_root)
        for storage_ref in refs:
            assert locate(store, storage_ref, "index.html") is not None
            assert locate(store, storage_ref, "assets/toon.css") is not None

    async def test_a_preview_may_be_stricter_than_its_site(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        result = await seed_module.seed(settings)
        async with session_factory() as db:
            previews = {
                preview.ref: preview.access_base_override
                for preview in await db.scalars(select(Preview))
            }
        assert result.previews == 2
        assert previews[seed_module.PREVIEW_OPEN] is None
        assert previews[seed_module.PREVIEW_RESTRICTED] == AccessBase.SITE_TEAM

    async def test_seeding_twice_leaves_no_second_set_and_no_orphan_files(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        first = await seed_module.seed(settings)
        second = await seed_module.seed(settings)
        async with session_factory() as db:
            rows = await db.execute(select(Version.site_id, Version.id))
            refs = {version_ref(site_id, version_id) for site_id, version_id in rows}
            counts = (
                await _count(db, Member),
                await _count(db, Group),
                await _count(db, Site),
                await _count(db, Preview),
                await _count(db, AccessKey),
                await _count(db, Invitee),
                await _count(db, FeedbackItem),
                await _count(db, FeedbackReply),
                await _count(db, FeedbackScreenshot),
            )
        assert counts == (6, 2, 5, 2, 2, 3, 8, 3, 1)
        assert (second.members, second.groups, second.sites, second.versions, second.feedback) == (
            first.members,
            first.groups,
            first.sites,
            first.versions,
            first.feedback,
        )
        # The wipe takes the files of the previous run with it, so no version
        # directory survives that no row points at any more.
        assert _version_dirs(settings.content_root) == refs

    async def test_the_seed_refuses_to_run_outside_dev(
        self, seed_module, settings: Settings
    ) -> None:
        production = settings.model_copy(update={"environment": "productie"})
        with pytest.raises(RuntimeError, match="dev"):
            await seed_module.seed(production)

    async def test_the_seed_runs_under_preview(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        dev = await seed_module.seed(settings)
        preview = await seed_module.seed(settings.model_copy(update={"environment": "preview"}))
        assert (preview.members, preview.groups, preview.sites, preview.versions, preview.previews) == (
            dev.members,
            dev.groups,
            dev.sites,
            dev.versions,
            dev.previews,
        )


def test_a_preview_makes_editors_of_the_group_the_seed_puts_down(seed_module) -> None:
    assert seed_module.GROUP_PUBLIC == PREVIEW_GROUP


def _preview(settings: Settings) -> Settings:
    return settings.model_copy(update={"environment": "preview"})


class TestOnlyIfEmpty:
    """A preview seeds on every start of its pod, so a database that already
    holds anything must come through untouched: a restart or a new push may
    never wipe what colleagues did in the preview."""

    async def test_an_empty_database_is_seeded(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        result = await seed_module.seed(_preview(settings), only_if_empty=True)
        assert result is not None
        async with session_factory() as db:
            assert await _count(db, Group) == 2

    async def test_a_database_with_a_group_is_left_alone(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            before = (await _count(db, Member), await _count(db, Group), await _count(db, Version))
            member_ids = set(await db.scalars(select(Member.id)))
        dirs = _version_dirs(settings.content_root)

        assert await seed_module.seed(_preview(settings), only_if_empty=True) is None

        async with session_factory() as db:
            after = (await _count(db, Member), await _count(db, Group), await _count(db, Version))
            assert set(await db.scalars(select(Member.id))) == member_ids
        assert after == before
        assert _version_dirs(settings.content_root) == dirs

    async def test_a_database_with_only_a_member_is_left_alone(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        """Someone who signed in and then removed every group still has an
        account: an empty group table alone is no reason to wipe it."""
        async with session_factory() as db:
            db.add(seed_module._member("echte-persoon", "p@example.org", "Echte Persoon"))
            await db.commit()

        assert await seed_module.seed(_preview(settings), only_if_empty=True) is None

        async with session_factory() as db:
            assert list(await db.scalars(select(Member.sso_subject))) == ["echte-persoon"]
            assert await _count(db, Group) == 0


class TestMain:
    def test_a_preview_is_seeded_on_its_first_start_only(
        self, seed_module, settings: Settings, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(seed_module, "load_settings", lambda: _preview(settings))

        assert seed_module.main() == 0
        first = capsys.readouterr().out
        assert seed_module.main() == 0
        second = capsys.readouterr().out

        assert "gezaaid" in first
        assert "key=" not in first
        assert "niet leeg" in second
        assert "toon login" not in second

    def test_dev_is_seeded_again_on_every_run(
        self, seed_module, settings: Settings, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(seed_module, "load_settings", lambda: settings)

        assert seed_module.main() == 0
        assert seed_module.main() == 0
        assert capsys.readouterr().out.count("Dev-inhoud gezaaid") == 2


class TestFeedback:
    """Enough feedback for the Feedback tab and the widget to show something
    locally, and in the shapes the widget API itself stores."""

    async def test_one_site_takes_feedback_on_its_previews_and_live_site_without_a_name_needed(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            taking = (
                await db.execute(
                    select(Site.slug, Site.feedback_identity, Site.feedback_live).where(
                        Site.feedback_enabled.is_(True)
                    )
                )
            ).all()
        assert [tuple(row) for row in taking] == [
            (seed_module.FEEDBACK_SITE, FeedbackIdentity.ANONYMOUS, True)
        ]

    async def test_the_items_cover_every_kind_status_and_author_on_both_previews_and_live(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        result = await seed_module.seed(settings)
        async with session_factory() as db:
            items = list(await db.scalars(select(FeedbackItem)))
            site_id = await db.scalar(select(Site.id).where(Site.slug == seed_module.FEEDBACK_SITE))
        assert result.feedback == len(items) == 8
        assert {item.kind for item in items} == set(FeedbackKind)
        assert {item.status for item in items} == set(FeedbackStatus)
        assert {item.author_kind for item in items} == set(FeedbackIdentity)
        assert {item.site_id for item in items} == {site_id}
        assert {(item.target, item.preview_ref) for item in items} == {
            (VersionTarget.PREVIEW, seed_module.PREVIEW_OPEN),
            (VersionTarget.PREVIEW, seed_module.PREVIEW_RESTRICTED),
            (VersionTarget.LIVE, None),
        }

    async def test_one_item_was_given_on_the_live_site_as_the_widget_api_stores_it(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            site = await db.scalar(select(Site).where(Site.slug == seed_module.FEEDBACK_SITE))
            live = list(await db.scalars(select(FeedbackItem).where(FeedbackItem.target == VersionTarget.LIVE)))
        assert len(live) == 1
        assert live[0].preview_ref is None
        assert site.live_version_id is not None
        assert live[0].version_id == site.live_version_id
        assert live[0].status is FeedbackStatus.NEW

    async def test_each_item_hangs_on_the_version_its_preview_shows_but_one_whose_version_is_gone(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            shown = {preview.ref: preview.version_id for preview in await db.scalars(select(Preview))}
            shown[None] = await db.scalar(select(Site.live_version_id).where(Site.slug == seed_module.FEEDBACK_SITE))
            items = list(await db.scalars(select(FeedbackItem)))
        # One was given on a version of its preview that is gone since.
        assert [item.version_id for item in items if item.version_id != shown[item.preview_ref]] == [None]

    async def test_the_quotes_stand_on_the_seeded_pages(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        """With the context around it, so the widget finds the place again."""
        await seed_module.seed(settings)
        async with session_factory() as db:
            texts = list(
                await db.scalars(select(FeedbackItem).where(FeedbackItem.kind == FeedbackKind.TEXT))
            )
        assert texts
        for item in texts:
            quote = item.anchor["selectors"][0]
            assert quote["exact"] == item.quote
            html = _page_html(settings.content_root, item.site_id, item.version_id, item.page_path)
            assert f"{quote['prefix']}{quote['exact']}{quote['suffix']}" in html

    async def test_the_site_team_handled_all_but_the_new_items(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            editor = await db.scalar(select(Member).where(Member.sso_subject == "dev-redacteur"))
            items = list(await db.scalars(select(FeedbackItem)))
        for item in items:
            if item.status is FeedbackStatus.NEW:
                assert (item.status_changed_at, item.status_changed_by) == (None, None)
            else:
                assert item.status_changed_by == editor.id
                assert item.created_at < item.status_changed_at
            resolved = item.status is FeedbackStatus.RESOLVED
            assert item.resolved_at == (item.status_changed_at if resolved else None)

    async def test_replies_come_from_the_site_team_and_from_the_submitter_with_an_account(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            editor = await db.scalar(select(Member).where(Member.sso_subject == "dev-redacteur"))
            rows = (
                await db.execute(
                    select(FeedbackReply, FeedbackItem).join(FeedbackItem, FeedbackItem.id == FeedbackReply.item_id)
                )
            ).all()
        assert {reply.author_kind for reply, _ in rows} == set(FeedbackReplyAuthor)
        for reply, item in rows:
            assert item.created_at < reply.created_at
            if reply.author_kind is FeedbackReplyAuthor.MEMBER:
                assert reply.member_id == editor.id
            else:
                # Only a submitter with an account can reply, from "My feedback".
                assert item.author_kind is FeedbackIdentity.ACCOUNT

    async def test_the_item_with_an_account_is_the_bootstrap_members_own(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        """The dev login always signs in as that member, so "My feedback" in
        the widget has something to show locally."""
        await seed_module.seed(settings)
        async with session_factory() as db:
            admin = await db.scalar(select(Member).where(Member.sso_subject == settings.bootstrap_admin_sub))
            account = list(
                await db.scalars(select(FeedbackItem).where(FeedbackItem.author_kind == FeedbackIdentity.ACCOUNT))
            )
        assert [(item.author_sub, item.author_name, item.author_email) for item in account] == [
            (admin.sso_subject, admin.name, seed_module.ADMIN_EMAIL)
        ]

    async def test_the_screenshot_is_one_the_widget_api_would_keep(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            rows = (
                await db.execute(
                    select(FeedbackScreenshot, FeedbackItem.kind).join(
                        FeedbackItem, FeedbackItem.id == FeedbackScreenshot.item_id
                    )
                )
            ).all()
        assert len(rows) == 1
        screenshot, kind = rows[0]
        assert kind is FeedbackKind.POINT
        info = acceptable_screenshot(screenshot.data)
        assert info is not None
        assert (info.media_type, info.width, info.height) == (
            screenshot.media_type,
            screenshot.width,
            screenshot.height,
        )
        assert screenshot.size_bytes == len(screenshot.data)


class TestReport:
    async def test_the_report_points_at_the_cli_login_and_names_no_secret_but_the_link_key(
        self, seed_module, settings: Settings
    ) -> None:
        result = await seed_module.seed(settings)
        report = seed_module._report(settings, result)
        assert "toon login" in report
        assert "/cli-link" in report
        assert "toon_" not in report.replace("toon login", "")

    async def test_a_preview_report_leaves_the_secret_link_out(self, seed_module, settings: Settings) -> None:
        """A preview's report lands in the pod log on ZAD, where a secret
        link is no secret any more; the dev stack's terminal keeps it."""
        result = await seed_module.seed(settings)
        dev = seed_module._report(settings, result)
        preview = seed_module._report(settings.model_copy(update={"environment": "preview"}), result)
        assert f"?key={result.secret_link_key}" in dev
        assert "key=" not in preview
        assert result.secret_link_key not in preview
        assert "toon login" in preview

    async def test_the_report_points_at_the_feedback_tab(self, seed_module, settings: Settings) -> None:
        result = await seed_module.seed(settings)
        report = seed_module._report(settings, result)
        assert f"/{seed_module.GROUP_PUBLIC}/{seed_module.FEEDBACK_SITE}/feedback" in report
        assert "8 feedbackitems" in report


class _Forms(HTMLParser):
    """The hidden inputs of every form on the mock login page, per form."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form":
            self.forms.append({})
        elif tag == "input" and attributes.get("type") == "hidden":
            self.forms[-1][attributes["name"]] = attributes["value"]


def _personas() -> list[tuple[str, dict]]:
    """(sub, claims) of every persona button: a form with a fixed username."""
    parser = _Forms()
    parser.feed(LOGIN_PAGE.read_text(encoding="utf-8"))
    return [(form["username"], json.loads(form["claims"])) for form in parser.forms if "username" in form]


class TestPersonaPage:
    """dev/mock-oidc-login.html posts the seed's email and name as claims, so a
    persona only logs in as its seeded member while the two agree."""

    async def test_every_seed_member_is_a_persona_with_the_seeded_email_and_name(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            seeded = {
                member.sso_subject: {"email": member.email, "name": member.name}
                for member in await db.scalars(select(Member))
            }
        personas = dict(_personas())
        assert {sub: personas.get(sub) for sub in seeded} == seeded
        assert set(personas) - set(seeded) == {NEW_VISITOR_SUB}

    def test_no_persona_appears_twice(self) -> None:
        subs = [sub for sub, _ in _personas()]
        assert len(subs) == len(set(subs))

    def test_the_interactive_config_serves_the_page_compose_mounts(self) -> None:
        config = json.loads(INTERACTIVE_CONFIG.read_text(encoding="utf-8"))
        compose = yaml.safe_load((DEV_DIR / "compose.yml").read_text(encoding="utf-8"))
        mounts = dict(
            volume.removesuffix(":ro").split(":", 1)
            for volume in compose["services"]["mock-oidc"]["volumes"]
            if not volume.startswith("$")
        )
        assert mounts[f"./{LOGIN_PAGE.name}"] == config["loginPagePath"]

    def test_the_interactive_config_leaves_email_and_name_to_the_page(self) -> None:
        """The mock lets a requestMapping claim win over a posted one, so an
        email there would replace the seeded address on every login."""
        config = json.loads(INTERACTIVE_CONFIG.read_text(encoding="utf-8"))
        for callback in config["tokenCallbacks"]:
            for mapping in callback["requestMappings"]:
                assert not {"email", "name"} & set(mapping["claims"])

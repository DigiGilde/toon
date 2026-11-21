"""The notification job against a real database (docs/design.md §16.16):
who gets an email when it is sent, the batching window, restarts and two
replicas at once, retries, the caps, deleted items and the webhook fallback.

Mail goes to a fake mailer and posts to a local fake receiver
(httpx.MockTransport) through the same client main.py uses.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import smtplib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from toon.config import Settings
from toon.constants import AccessBase, FeedbackIdentity, FeedbackKind, Role
from toon.db import make_session_factory
from toon.models.feedback import FeedbackItem, FeedbackScreenshot
from toon.models.identity import Group, GroupMember, Member, MemberLanguage, MemberStatus, PlatformRole, SiteMember
from toon.models.notifications import (
    FeedbackChannel,
    FeedbackEmailDay,
    FeedbackNotice,
    FeedbackNoticeItem,
    FeedbackWebhook,
    GroupFeedbackEmail,
    NoticeOutcome,
    SiteFeedbackEmail,
)
from toon.models.publication import Site, VersionTarget
from toon.notifications import job
from toon.notifications.recipients import Source, recipients, resolve
from toon.notifications.webhooks import make_client

ADMIN = "https://beheer.toon.example"
SITE_HOOK = "https://mattermost.example.org/hooks/site-secret-path"
GROUP_HOOK = "https://mattermost.example.org/hooks/group-secret-path"
T0 = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
COMMENT = "De tabel op deze pagina is niet te lezen."
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def _settings(**values) -> Settings:
    fields = {
        "db_url": "postgresql+asyncpg://toon@localhost/toon",
        "content_root": "/tmp/toon-content",  # noqa: S108 - never touched here
        "oidc_issuer": "https://idp.example",
        "oidc_client_id": "toon",
        "oidc_client_private_jwk": "{}",
        "session_secret": "sessie-geheim-van-minstens-32-bytes!",
        "audit_pepper": "audit-pepper-van-minstens-32-bytes!!",
        "audit_ip_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        "content_base_url": "https://toon.example",
        "base_url": ADMIN,
        "environment": "dev",
        "smtp_host": "relay.example",
        "smtp_from": "noreply@example.org",
        "feedback_webhook_hosts": "mattermost.example.org",
        **values,
    }
    return Settings(**fields)


@dataclass
class FakeMailer:
    """Records what it would send. `failures` raise before a message goes."""

    sent: list = field(default_factory=list)
    failures: list[BaseException] = field(default_factory=list)
    delay: float = 0.0

    async def send(self, message) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append(message)

    def to(self) -> list[str]:
        return sorted(str(message["To"]) for message in self.sent)

    def body(self, index: int = 0) -> str:
        return self.sent[index].get_content().replace("\r\n", "\n")


@dataclass
class Receiver:
    """A local fake Mattermost: records each post and answers 500 for a URL
    in `failing`, 200 otherwise."""

    posts: list[tuple[str, str]] = field(default_factory=list)
    failing: set[str] = field(default_factory=set)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.posts.append((str(request.url), json.loads(request.content)["text"]))
        return httpx.Response(500 if str(request.url) in self.failing else 200)


@pytest_asyncio.fixture
async def factory(migrated_dsn: str):
    engine = create_async_engine(migrated_dsn, poolclass=NullPool)
    try:
        yield make_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def receiver() -> Receiver:
    return Receiver()


@pytest_asyncio.fixture
async def http(receiver):
    async with make_client(httpx.MockTransport(receiver)) as client:
        yield client


@dataclass(frozen=True)
class World:
    group: Group
    site: Site
    other: Site
    admin: Member
    reader: Member
    site_reader: Member
    outsider: Member
    platform_admin: Member
    unverified: Member
    gone: Member


@pytest_asyncio.fixture
async def world(factory) -> World:
    """Group 'team' with sites 'docs' and 'ander'. Everyone with an address
    has turned email on, on their profile."""
    async with factory() as db:
        members = {
            sub: Member(
                sso_subject=sub,
                email="" if sub == "onbevestigd" else f"{sub}@example.nl",
                name=f"Naam {sub}",
                status=MemberStatus.DEACTIVATED if sub == "weg" else MemberStatus.ACTIVE,
                platform_role=PlatformRole.ADMIN if sub == "platformbeheer" else PlatformRole.MEMBER,
                feedback_email=True,
            )
            for sub in ("beheerder", "lezer", "sitelezer", "buitenstaander", "platformbeheer", "onbevestigd", "weg")
        }
        group = Group(slug="team", name="Team", default_access_base=AccessBase.SITE_TEAM)
        db.add_all([group, *members.values()])
        await db.flush()
        site = Site(group_id=group.id, slug="docs", title="Docs", access_base=AccessBase.SITE_TEAM)
        other = Site(group_id=group.id, slug="ander", title="Ander", access_base=AccessBase.SITE_TEAM)
        db.add_all([site, other])
        await db.flush()
        for sub, role in (("beheerder", Role.ADMIN), ("lezer", Role.READER), ("onbevestigd", Role.READER)):
            db.add(GroupMember(group_id=group.id, member_id=members[sub].id, role=role))
        db.add(GroupMember(group_id=group.id, member_id=members["weg"].id, role=Role.EDITOR))
        db.add(SiteMember(site_id=site.id, member_id=members["sitelezer"].id, role=Role.READER))
        await db.commit()
    return World(
        group=group,
        site=site,
        other=other,
        admin=members["beheerder"],
        reader=members["lezer"],
        site_reader=members["sitelezer"],
        outsider=members["buitenstaander"],
        platform_admin=members["platformbeheer"],
        unverified=members["onbevestigd"],
        gone=members["weg"],
    )


async def _item(factory, site: Site, *, at: datetime = T0, screenshot: bool = False, **values) -> FeedbackItem:
    fields = {
        "site_id": site.id,
        "target": VersionTarget.LIVE,
        "page_path": "docs/",
        "kind": FeedbackKind.PAGE,
        "comment": COMMENT,
        "author_kind": FeedbackIdentity.ANONYMOUS,
        "created_at": at,
        **values,
    }
    async with factory() as db:
        item = FeedbackItem(**fields)
        db.add(item)
        await db.flush()
        if screenshot:
            db.add(
                FeedbackScreenshot(
                    item_id=item.id, media_type="image/png", data=PNG, size_bytes=len(PNG), width=10, height=10
                )
            )
        await db.commit()
        return item


async def _hook(factory, url: str, *, site: Site | None = None, group: Group | None = None, language="nl") -> None:
    async with factory() as db:
        db.add(
            FeedbackWebhook(
                site_id=site.id if site else None,
                group_id=group.id if group else None,
                url=url,
                host="mattermost.example.org",
                language=MemberLanguage(language),
            )
        )
        await db.commit()


async def _run(factory, settings, http, mailer, at: datetime) -> None:
    await job.run_once(factory, settings, http=http, mailer=mailer, now=at)


async def _notices(factory, channel: FeedbackChannel | None = None) -> list[FeedbackNotice]:
    async with factory() as db:
        statement = select(FeedbackNotice).order_by(FeedbackNotice.created_at)
        if channel is not None:
            statement = statement.where(FeedbackNotice.channel == channel)
        return list(await db.scalars(statement))


async def _set(factory, model, *where, **values) -> None:
    async with factory() as db:
        await db.execute(update(model).where(*where).values(**values))
        await db.commit()


# -- Who: the preference matrix ----------------------------------------------

CHOICES = (True, False, None)


@pytest.mark.parametrize(("site", "group", "profile"), list(itertools.product(CHOICES, repeat=3)))
def test_the_narrowest_level_that_is_set_decides(site, group, profile) -> None:
    resolution = resolve(site, group, profile)
    if site is not None:
        assert (resolution.enabled, resolution.source) == (site, Source.SITE)
    elif group is not None:
        assert (resolution.enabled, resolution.source) == (group, Source.GROUP)
    elif profile is not None:
        assert (resolution.enabled, resolution.source) == (profile, Source.PROFILE)
    else:
        assert (resolution.enabled, resolution.source) == (False, Source.DEFAULT)


@pytest.mark.parametrize(("site", "group", "profile"), list(itertools.product(CHOICES, repeat=3)))
async def test_the_database_resolves_the_matrix_the_same_way(factory, world, site, group, profile) -> None:
    async with factory() as db:
        await db.execute(update(Member).where(Member.id == world.reader.id).values(feedback_email=profile))
        if site is not None:
            db.add(SiteFeedbackEmail(site_id=world.site.id, member_id=world.reader.id, enabled=site))
        if group is not None:
            db.add(GroupFeedbackEmail(group_id=world.group.id, member_id=world.reader.id, enabled=group))
        await db.commit()
        found = {person.member_id for person in await recipients(db, world.site)}
    assert (world.reader.id in found) is resolve(site, group, profile).enabled


async def test_off_on_a_site_beats_on_on_its_group_and_only_there(factory, world) -> None:
    async with factory() as db:
        db.add(GroupFeedbackEmail(group_id=world.group.id, member_id=world.reader.id, enabled=True))
        db.add(SiteFeedbackEmail(site_id=world.site.id, member_id=world.reader.id, enabled=False))
        await db.commit()
        on_docs = {person.member_id for person in await recipients(db, world.site)}
        on_other = {person.member_id for person in await recipients(db, world.other)}
    assert world.reader.id not in on_docs
    assert world.reader.id in on_other


async def test_only_active_members_with_a_role_and_a_verified_address_get_email(factory, world) -> None:
    async with factory() as db:
        on_docs = {person.member_id for person in await recipients(db, world.site)}
        on_other = {person.member_id for person in await recipients(db, world.other)}
    assert on_docs == {world.admin.id, world.reader.id, world.site_reader.id}
    # The site-only reader reads 'docs' and not 'ander'.
    assert on_other == {world.admin.id, world.reader.id}


async def test_an_address_that_cannot_stand_in_a_header_is_left_out(factory, world) -> None:
    await _set(factory, Member, Member.id == world.reader.id, email="lezer@example.nl\r\nbcc: x@evil.test")
    async with factory() as db:
        found = {person.member_id for person in await recipients(db, world.site)}
        assert await job.recipients.recipient(db, world.site, world.reader.id) is None
    assert world.reader.id not in found


# -- Sending: who is checked again ------------------------------------------


async def test_one_email_per_member_with_metadata_only(factory, world, http, receiver) -> None:
    mailer = FakeMailer()
    await _item(
        factory,
        world.site,
        kind=FeedbackKind.POINT,
        anchor={"selectors": [], "point": {"x": 0.5, "y": 0.5}},
        author_kind=FeedbackIdentity.ACCOUNT,
        author_sub="gever-sub",
        author_name="Jan Jansen",
        author_email="jan.jansen@example.nl",
        screenshot=True,
    )
    await _run(factory, _settings(), http, mailer, T0 + timedelta(minutes=10))
    assert mailer.to() == ["beheerder@example.nl", "lezer@example.nl", "sitelezer@example.nl"]
    for index in range(3):
        body = mailer.body(index)
        assert "Point on /docs/, the live site" in body
        assert "with screenshot" in body
        assert COMMENT not in body
        assert "Jan" not in body
        assert "jan.jansen" not in body
    assert {notice.outcome for notice in await _notices(factory, FeedbackChannel.EMAIL)} == {NoticeOutcome.SENT}


async def test_the_email_is_in_the_language_of_its_member(factory, world, http) -> None:
    await _set(factory, Member, Member.id == world.reader.id, language=MemberLanguage.NL)
    mailer = FakeMailer()
    await _item(factory, world.site)
    await _run(factory, _settings(), http, mailer, T0 + timedelta(minutes=10))
    subjects = {str(message["To"]): str(message["Subject"]) for message in mailer.sent}
    assert subjects["lezer@example.nl"] == "Nieuwe feedback op team/docs"
    assert subjects["beheerder@example.nl"] == "New feedback on team/docs"


async def test_a_member_is_not_told_about_their_own_feedback(factory, world, http) -> None:
    mailer = FakeMailer()
    own = {"author_kind": FeedbackIdentity.ACCOUNT, "author_sub": world.reader.sso_subject}
    await _item(factory, world.site, page_path="eigen.html", **own)
    await _item(factory, world.site, page_path="ander.html", at=T0 + timedelta(minutes=1))
    await _run(factory, _settings(), http, mailer, T0 + timedelta(minutes=10))
    by_member = {str(message["To"]): message.get_content() for message in mailer.sent}
    assert "/eigen.html" not in by_member["lezer@example.nl"]
    assert "/ander.html" in by_member["lezer@example.nl"]
    assert "/eigen.html" in by_member["beheerder@example.nl"]


async def test_only_own_feedback_writes_no_notice_for_its_author(factory, world, http) -> None:
    mailer = FakeMailer()
    await _item(factory, world.site, author_kind=FeedbackIdentity.ACCOUNT, author_sub=world.reader.sso_subject)
    await _run(factory, _settings(), http, mailer, T0 + timedelta(minutes=10))
    assert "lezer@example.nl" not in mailer.to()
    assert world.reader.id not in {notice.member_id for notice in await _notices(factory)}


async def _batched_not_sent(factory, world, http) -> FakeMailer:
    """Items taken into a batch with notices written, nothing sent yet."""
    await _item(factory, world.site)
    await job.take_into_batches(factory, _settings(), FeedbackChannel.EMAIL, T0 + timedelta(minutes=10))
    assert len(await _notices(factory, FeedbackChannel.EMAIL)) == 3
    return FakeMailer()


async def _deliver(factory, http, mailer, settings=None, at=T0 + timedelta(minutes=10)) -> None:
    await job.deliver_due(factory, settings or _settings(), http=http, mailer=mailer, now=at)


async def test_a_member_who_lost_access_before_sending_gets_nothing(factory, world, http) -> None:
    mailer = await _batched_not_sent(factory, world, http)
    async with factory() as db:
        await db.execute(delete(GroupMember).where(GroupMember.member_id == world.reader.id))
        await db.commit()
    await _deliver(factory, http, mailer)
    assert "lezer@example.nl" not in mailer.to()
    dropped = [n for n in await _notices(factory) if n.member_id == world.reader.id]
    assert [notice.outcome for notice in dropped] == [NoticeOutcome.DROPPED]


async def test_a_member_deactivated_before_sending_gets_nothing(factory, world, http) -> None:
    mailer = await _batched_not_sent(factory, world, http)
    await _set(factory, Member, Member.id == world.reader.id, status=MemberStatus.DEACTIVATED)
    await _deliver(factory, http, mailer)
    assert mailer.to() == ["beheerder@example.nl", "sitelezer@example.nl"]


async def test_a_member_who_turned_email_off_before_sending_gets_nothing(factory, world, http) -> None:
    mailer = await _batched_not_sent(factory, world, http)
    async with factory() as db:
        db.add(SiteFeedbackEmail(site_id=world.site.id, member_id=world.reader.id, enabled=False))
        await db.commit()
    await _deliver(factory, http, mailer)
    assert "lezer@example.nl" not in mailer.to()


async def test_a_member_who_lost_their_address_gets_nothing(factory, world, http) -> None:
    mailer = await _batched_not_sent(factory, world, http)
    await _set(factory, Member, Member.id == world.reader.id, email="")
    await _deliver(factory, http, mailer)
    assert "lezer@example.nl" not in mailer.to()


async def test_email_switched_off_on_the_installation_drops_what_waits(factory, world, http) -> None:
    await _batched_not_sent(factory, world, http)
    await _deliver(factory, http, None)
    assert {notice.outcome for notice in await _notices(factory)} == {NoticeOutcome.DROPPED}


# -- Deleted items -----------------------------------------------------------


async def test_an_item_deleted_before_sending_is_not_sent(factory, world, http) -> None:
    kept = await _item(factory, world.site, page_path="blijft.html")
    gone = await _item(factory, world.site, page_path="weg.html")
    await job.take_into_batches(factory, _settings(), FeedbackChannel.EMAIL, T0 + timedelta(minutes=10))
    async with factory() as db:
        await db.execute(delete(FeedbackItem).where(FeedbackItem.id == gone.id))
        await db.commit()
    mailer = FakeMailer()
    await _deliver(factory, http, mailer)
    assert mailer.sent
    assert all("/weg.html" not in message.get_content() for message in mailer.sent)
    assert all(str(kept.id) in message.get_content() for message in mailer.sent)


async def test_a_batch_whose_items_are_all_deleted_is_dropped(factory, world, http, receiver) -> None:
    await _hook(factory, SITE_HOOK, site=world.site)
    item = await _item(factory, world.site)
    for channel in FeedbackChannel:
        await job.take_into_batches(factory, _settings(), channel, T0 + timedelta(minutes=10))
    async with factory() as db:
        await db.execute(delete(FeedbackItem).where(FeedbackItem.id == item.id))
        await db.commit()
    mailer = FakeMailer()
    await _deliver(factory, http, mailer)
    assert mailer.sent == []
    assert receiver.posts == []
    assert {notice.outcome for notice in await _notices(factory)} == {NoticeOutcome.DROPPED}


# -- The window ----------------------------------------------------------------


async def test_email_waits_for_the_window_then_carries_everything_of_it(factory, world, http) -> None:
    mailer = FakeMailer()
    settings = _settings(feedback_webhook_hosts="")
    await _item(factory, world.site, page_path="een.html")
    await _run(factory, settings, http, mailer, T0 + timedelta(minutes=9, seconds=59))
    assert mailer.sent == []
    async with factory() as db:
        waiting = await db.scalar(select(func.count()).where(FeedbackItem.email_batched_at.is_(None)))
    assert waiting == 1
    await _item(factory, world.site, page_path="twee.html", at=T0 + timedelta(minutes=7))
    await _run(factory, settings, http, mailer, T0 + timedelta(minutes=10))
    assert len(mailer.sent) == 3
    body = mailer.body()
    assert "/een.html" in body and "/twee.html" in body
    assert str(mailer.sent[0]["Subject"]) == "2 new feedback items on team/docs"


async def test_each_site_has_a_window_and_an_email_of_its_own(factory, world, http) -> None:
    mailer = FakeMailer()
    await _item(factory, world.site)
    await _item(factory, world.other, at=T0 + timedelta(minutes=5))
    await _run(factory, _settings(), http, mailer, T0 + timedelta(minutes=10))
    assert {str(message["Subject"]) for message in mailer.sent} == {"New feedback on team/docs"}
    await _run(factory, _settings(), http, mailer, T0 + timedelta(minutes=15))
    assert {str(message["Subject"]) for message in mailer.sent} == {
        "New feedback on team/docs",
        "New feedback on team/ander",
    }


async def test_a_window_of_zero_sends_a_message_per_item(factory, world, http) -> None:
    mailer = FakeMailer()
    settings = _settings(feedback_email_window_minutes=0)
    await _item(factory, world.site)
    await _item(factory, world.site)
    await _run(factory, settings, http, mailer, T0)
    assert mailer.to().count("lezer@example.nl") == 2


async def test_mattermost_posts_each_item_by_default(factory, world, http, receiver) -> None:
    await _hook(factory, SITE_HOOK, site=world.site)
    await _item(factory, world.site, page_path="een.html")
    await _item(factory, world.site, page_path="twee.html", target=VersionTarget.PREVIEW, preview_ref="pr-3")
    await _run(factory, _settings(), http, FakeMailer(), T0)
    assert [url for url, _ in receiver.posts] == [SITE_HOOK, SITE_HOOK]
    assert sorted("/een.html" in text for _, text in receiver.posts) == [False, True]
    assert any("preview `pr-3`" in text for _, text in receiver.posts)


async def test_a_mattermost_window_batches_like_email(factory, world, http, receiver) -> None:
    await _hook(factory, SITE_HOOK, site=world.site)
    settings = _settings(feedback_mattermost_window_minutes=10)
    await _item(factory, world.site)
    await _item(factory, world.site, at=T0 + timedelta(minutes=3))
    await _run(factory, settings, http, FakeMailer(), T0 + timedelta(minutes=5))
    assert receiver.posts == []
    await _run(factory, settings, http, FakeMailer(), T0 + timedelta(minutes=10))
    assert len(receiver.posts) == 1
    assert receiver.posts[0][1].startswith("#### 2 nieuwe feedbackitems op team/docs")


async def test_items_without_anyone_to_tell_are_taken_all_the_same(factory, world, http) -> None:
    """Nothing to send on an installation without email or webhooks; the
    items are taken anyway, so turning a channel on later sends no backlog."""
    await _item(factory, world.site)
    bare = _settings(smtp_host="", smtp_from="", feedback_webhook_hosts="")
    await _run(factory, bare, http, None, T0 + timedelta(minutes=10))
    assert await _notices(factory) == []
    mailer = FakeMailer()
    await _run(factory, _settings(), http, mailer, T0 + timedelta(minutes=30))
    assert mailer.sent == []


# -- Restarts and replicas ---------------------------------------------------


async def test_running_again_sends_nothing_twice(factory, world, http, receiver) -> None:
    await _hook(factory, SITE_HOOK, site=world.site)
    mailer = FakeMailer()
    await _item(factory, world.site)
    for _ in range(3):
        await _run(factory, _settings(), http, mailer, T0 + timedelta(minutes=10))
    assert len(mailer.sent) == 3
    assert len(receiver.posts) == 1


async def test_a_restart_between_batching_and_sending_loses_nothing(factory, world, http) -> None:
    await _item(factory, world.site)
    await job.take_into_batches(factory, _settings(), FeedbackChannel.EMAIL, T0 + timedelta(minutes=10))
    mailer = FakeMailer()
    await _run(factory, _settings(), http, mailer, T0 + timedelta(minutes=11))
    assert len(mailer.sent) == 3


async def test_two_replicas_at_once_send_each_message_once(factory, migrated_dsn, world, http, receiver) -> None:
    await _hook(factory, SITE_HOOK, site=world.site)
    for minute in range(4):
        await _item(factory, world.site, at=T0 + timedelta(minutes=minute))
    other_engine = create_async_engine(migrated_dsn, poolclass=NullPool)
    try:
        other = make_session_factory(other_engine)
        first, second = FakeMailer(delay=0.05), FakeMailer(delay=0.05)
        at = T0 + timedelta(minutes=10)
        await asyncio.gather(
            job.run_once(factory, _settings(), http=http, mailer=first, now=at),
            job.run_once(other, _settings(), http=http, mailer=second, now=at),
        )
    finally:
        await other_engine.dispose()
    assert sorted(first.to() + second.to()) == ["beheerder@example.nl", "lezer@example.nl", "sitelezer@example.nl"]
    assert len(receiver.posts) == 4
    assert len({text for _, text in receiver.posts}) == 4


# -- Failures ----------------------------------------------------------------


async def test_a_failed_email_is_tried_again_with_backoff(factory, world, http) -> None:
    mailer = FakeMailer(failures=[ConnectionRefusedError(), TimeoutError()])
    settings = _settings(feedback_webhook_hosts="")
    await _item(factory, world.site)
    await _set(factory, Member, Member.id != world.reader.id, feedback_email=False)
    at = T0 + timedelta(minutes=10)
    await _run(factory, settings, http, mailer, at)
    (notice,) = await _notices(factory)
    assert (notice.attempts, notice.next_attempt_at, notice.outcome) == (1, at + timedelta(minutes=2), None)
    await _run(factory, settings, http, mailer, at + timedelta(minutes=1))
    assert (await _notices(factory))[0].attempts == 1
    await _run(factory, settings, http, mailer, at + timedelta(minutes=2))
    (notice,) = await _notices(factory)
    assert (notice.attempts, notice.next_attempt_at) == (2, at + timedelta(minutes=6))
    await _run(factory, settings, http, mailer, at + timedelta(minutes=6))
    (notice,) = await _notices(factory)
    assert notice.outcome is NoticeOutcome.SENT
    assert mailer.to() == ["lezer@example.nl"]


async def test_after_five_failures_it_gives_up_and_says_why_without_the_address(factory, world, http, caplog) -> None:
    refusal = smtplib.SMTPDataError(554, b"lezer@example.nl rejected: spam")
    mailer = FakeMailer(failures=[refusal] * 5)
    settings = _settings(feedback_webhook_hosts="")
    await _item(factory, world.site)
    await _set(factory, Member, Member.id != world.reader.id, feedback_email=False)
    at = T0 + timedelta(minutes=10)
    with caplog.at_level(logging.INFO, logger="toon.notifications.job"):
        for minutes in (0, 2, 6, 14, 30, 62, 200):
            await _run(factory, settings, http, mailer, at + timedelta(minutes=minutes))
    (notice,) = await _notices(factory)
    assert (notice.attempts, notice.outcome) == (job.MAX_ATTEMPTS, NoticeOutcome.FAILED)
    assert mailer.sent == []
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "giving up (SMTPDataError 554)" in warnings[0].getMessage()
    assert "team/docs" in warnings[0].getMessage()
    assert all("lezer@example.nl" not in record.getMessage() for record in caplog.records)


async def test_a_dead_webhook_holds_up_neither_email_nor_another_site(factory, world, http, receiver, caplog) -> None:
    other_hook = "https://mattermost.example.org/hooks/other-secret"
    await _hook(factory, SITE_HOOK, site=world.site)
    await _hook(factory, other_hook, site=world.other)
    receiver.failing = {SITE_HOOK}
    mailer = FakeMailer()
    await _item(factory, world.site)
    await _item(factory, world.other, at=T0 + timedelta(seconds=1))
    at = T0 + timedelta(minutes=10)
    with caplog.at_level(logging.INFO, logger="toon.notifications.job"):
        for minutes in (0, 2, 6, 14, 30):
            await _run(factory, _settings(), http, mailer, at + timedelta(minutes=minutes))
    assert len(mailer.sent) == 5
    sent_to = [url for url, _ in receiver.posts]
    assert sent_to.count(SITE_HOOK) == 5
    assert sent_to.count(other_hook) == 1
    failed = [n for n in await _notices(factory, FeedbackChannel.MATTERMOST) if n.outcome is NoticeOutcome.FAILED]
    assert len(failed) == 1
    for record in caplog.records:
        assert "secret" not in record.getMessage()
    assert any("giving up (HTTP 500)" in record.getMessage() for record in caplog.records)


async def test_a_bug_while_writing_counts_as_a_failed_attempt(factory, world, http, monkeypatch) -> None:
    def broken(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(job, "build_email", broken)
    mailer = FakeMailer()
    await _item(factory, world.site)
    await _run(factory, _settings(feedback_webhook_hosts=""), http, mailer, T0 + timedelta(minutes=10))
    assert {notice.attempts for notice in await _notices(factory)} == {1}


# -- The daily cap -------------------------------------------------------------


async def test_the_daily_cap_holds_email_until_the_next_day_and_mattermost_goes_on(
    factory, world, http, receiver, caplog
) -> None:
    await _hook(factory, SITE_HOOK, site=world.site)
    settings = _settings(feedback_email_daily_max=2)
    mailer = FakeMailer()
    await _item(factory, world.site)
    at = T0 + timedelta(minutes=10)
    with caplog.at_level(logging.WARNING, logger="toon.notifications.job"):
        await _run(factory, settings, http, mailer, at)
        await _item(factory, world.site, at=at)
        await _run(factory, settings, http, mailer, at + timedelta(minutes=10))
    assert len(mailer.sent) == 2
    assert len(receiver.posts) == 2
    held = [n for n in await _notices(factory, FeedbackChannel.EMAIL) if n.outcome is None]
    tomorrow = datetime(2026, 11, 22, tzinfo=UTC)
    assert held and all(notice.next_attempt_at == tomorrow for notice in held)
    assert all(notice.attempts == 0 for notice in held)
    capped = [record for record in caplog.records if "reached its cap of 2" in record.getMessage()]
    assert len(capped) == 1
    async with factory() as db:
        day = await db.get(FeedbackEmailDay, T0.date())
    assert (day.sent, day.cap_reached_at) == (2, at)

    await _run(factory, settings, http, mailer, tomorrow)
    assert len(mailer.sent) == 4
    async with factory() as db:
        assert (await db.get(FeedbackEmailDay, tomorrow.date())).sent == 2


async def test_the_cap_is_counted_across_replicas(factory, world, http) -> None:
    async with factory() as db:
        db.add(FeedbackEmailDay(day=T0.date(), sent=2000))
        await db.commit()
    mailer = FakeMailer()
    await _item(factory, world.site)
    await _run(factory, _settings(), http, mailer, T0 + timedelta(minutes=10))
    assert mailer.sent == []


# -- Mattermost: where it goes and what it says ------------------------------


async def test_a_site_without_a_webhook_posts_to_its_group(factory, world, http, receiver) -> None:
    await _hook(factory, GROUP_HOOK, group=world.group, language="en")
    await _hook(factory, SITE_HOOK, site=world.other)
    await _item(factory, world.site)
    await _item(factory, world.other)
    await _run(factory, _settings(), http, FakeMailer(), T0)
    assert sorted(url for url, _ in receiver.posts) == sorted([GROUP_HOOK, SITE_HOOK])
    group_post = next(text for url, text in receiver.posts if url == GROUP_HOOK)
    assert group_post.startswith("#### New feedback on team/docs")


async def test_the_post_carries_the_comment_and_never_the_submitter(factory, world, http, receiver) -> None:
    await _hook(factory, SITE_HOOK, site=world.site)
    await _item(
        factory,
        world.site,
        comment="@channel kijk [hier](https://evil.test)",
        author_kind=FeedbackIdentity.ACCOUNT,
        author_sub="gever-sub",
        author_name="Jan Jansen",
        author_email="jan.jansen@example.nl",
    )
    await _run(factory, _settings(), http, FakeMailer(), T0)
    ((_, text),) = receiver.posts
    assert "    " + chr(0xFF20) + "channel kijk [hier](https://evil.test)" in text
    assert "Jan" not in text
    assert "jan.jansen" not in text


async def test_a_webhook_removed_before_sending_drops_its_post(factory, world, http, receiver) -> None:
    await _hook(factory, SITE_HOOK, site=world.site)
    await _item(factory, world.site)
    await job.take_into_batches(factory, _settings(), FeedbackChannel.MATTERMOST, T0)
    async with factory() as db:
        await db.execute(delete(FeedbackWebhook))
        await db.commit()
    await _deliver(factory, http, FakeMailer(), at=T0)
    assert receiver.posts == []
    assert [n.outcome for n in await _notices(factory, FeedbackChannel.MATTERMOST)] == [NoticeOutcome.DROPPED]


async def test_a_host_taken_off_the_list_is_not_posted_to(factory, world, http, receiver, caplog) -> None:
    await _hook(factory, SITE_HOOK, site=world.site)
    await _item(factory, world.site)
    await job.take_into_batches(factory, _settings(), FeedbackChannel.MATTERMOST, T0)
    with caplog.at_level(logging.WARNING, logger="toon.notifications.job"):
        elsewhere = _settings(feedback_webhook_hosts="chat.example.org")
        await _deliver(factory, http, FakeMailer(), settings=elsewhere, at=T0)
    assert receiver.posts == []
    (record,) = [r for r in caplog.records if "no longer allowed" in r.getMessage()]
    assert "mattermost.example.org" in record.getMessage()
    assert "secret" not in record.getMessage()


async def test_no_webhook_hosts_writes_no_post(factory, world, http, receiver) -> None:
    await _hook(factory, SITE_HOOK, site=world.site)
    await _item(factory, world.site)
    await _run(factory, _settings(feedback_webhook_hosts=""), http, FakeMailer(), T0 + timedelta(minutes=10))
    assert receiver.posts == []
    assert await _notices(factory, FeedbackChannel.MATTERMOST) == []


# -- Housekeeping and the loop -------------------------------------------------


async def test_finished_notices_and_old_days_go_after_a_week(factory, world, http) -> None:
    await _item(factory, world.site)
    await _run(factory, _settings(feedback_webhook_hosts=""), http, FakeMailer(), T0 + timedelta(minutes=10))
    assert len(await _notices(factory)) == 3
    await job.forget_finished(factory, T0 + timedelta(days=7))
    assert len(await _notices(factory)) == 3
    await job.forget_finished(factory, T0 + timedelta(days=7, minutes=11))
    assert await _notices(factory) == []
    async with factory() as db:
        assert await db.get(FeedbackEmailDay, T0.date()) is not None
    await job.forget_finished(factory, T0 + timedelta(days=8))
    async with factory() as db:
        assert await db.get(FeedbackEmailDay, T0.date()) is None


async def test_a_pending_notice_is_never_forgotten(factory, world, http) -> None:
    await _item(factory, world.site)
    await job.take_into_batches(factory, _settings(), FeedbackChannel.EMAIL, T0 + timedelta(minutes=10))
    await job.forget_finished(factory, T0 + timedelta(days=30))
    assert len(await _notices(factory)) == 3


async def test_a_run_stops_sending_when_asked(factory, world, http) -> None:
    await _item(factory, world.site)
    stop = asyncio.Event()
    stop.set()
    mailer = FakeMailer()
    await job.run_once(factory, _settings(), http=http, mailer=mailer, now=T0 + timedelta(minutes=10), stop=stop)
    assert mailer.sent == []
    assert len(await _notices(factory)) == 3


async def test_a_run_sends_at_most_its_share(factory, world, http, monkeypatch) -> None:
    monkeypatch.setattr(job, "SENDS_PER_RUN", 2)
    mailer = FakeMailer()
    await _item(factory, world.site)
    await _run(factory, _settings(), http, mailer, T0 + timedelta(minutes=10))
    assert len(mailer.sent) == 2
    await _run(factory, _settings(), http, mailer, T0 + timedelta(minutes=10))
    assert len(mailer.sent) == 3


async def test_the_loop_runs_every_interval_and_survives_a_failing_run(factory, world, http, caplog) -> None:
    await _item(factory, world.site, at=datetime.now(UTC) - timedelta(hours=1))
    mailer = FakeMailer()
    calls = 0
    original = job.run_once

    async def flaky(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database went away")
        await original(*args, **kwargs)

    with caplog.at_level(logging.ERROR, logger="toon.notifications.job"):
        pytest.MonkeyPatch.setattr(pytest.MonkeyPatch(), job, "run_once", flaky)
        try:
            async with job.notification_job(factory, _settings(), http=http, mailer=mailer, interval=0.01):
                for _ in range(500):
                    if len(mailer.sent) == 3:
                        break
                    await asyncio.sleep(0.01)
        finally:
            job.run_once = original
    assert len(mailer.sent) == 3
    assert any("Feedback notifications failed" in record.getMessage() for record in caplog.records)


async def test_shutdown_lets_a_message_on_its_way_finish(factory, world, http) -> None:
    await _item(factory, world.site, at=datetime.now(UTC) - timedelta(hours=1))
    await _set(factory, Member, Member.id != world.reader.id, feedback_email=False)
    started = asyncio.Event()

    class SlowMailer(FakeMailer):
        async def send(self, message) -> None:
            started.set()
            await asyncio.sleep(0.3)
            await super().send(message)

    mailer = SlowMailer()
    async with job.notification_job(factory, _settings(), http=http, mailer=mailer, interval=0.01, grace=5):
        await asyncio.wait_for(started.wait(), timeout=10)
    assert mailer.to() == ["lezer@example.nl"]
    (notice,) = await _notices(factory)
    assert notice.outcome is NoticeOutcome.SENT


async def test_shutdown_cancels_a_run_past_its_grace(factory, world, http) -> None:
    await _item(factory, world.site, at=datetime.now(UTC) - timedelta(hours=1))
    started = asyncio.Event()

    class StuckMailer(FakeMailer):
        async def send(self, message) -> None:
            started.set()
            await asyncio.sleep(5)

    mailer = StuckMailer()
    async with job.notification_job(factory, _settings(), http=http, mailer=mailer, interval=0.01, grace=0.2) as task:
        await asyncio.wait_for(started.wait(), timeout=10)
    assert task.cancelled()
    assert mailer.sent == []
    assert {notice.outcome for notice in await _notices(factory)} == {None}


async def test_shutdown_while_waiting_runs_nothing(factory, world, http) -> None:
    await _item(factory, world.site, at=datetime.now(UTC) - timedelta(hours=1))
    mailer = FakeMailer()
    async with job.notification_job(factory, _settings(), http=http, mailer=mailer, interval=60) as task:
        await asyncio.sleep(0.05)
    assert task.done() and not task.cancelled()
    assert mailer.sent == []
    assert await _notices(factory) == []


async def test_notice_items_go_with_their_notice(factory, world, http) -> None:
    await _item(factory, world.site)
    await _run(factory, _settings(), http, FakeMailer(), T0 + timedelta(minutes=10))
    await job.forget_finished(factory, T0 + timedelta(days=8))
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(FeedbackNoticeItem)) == 0

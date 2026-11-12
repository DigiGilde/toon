"""Fills the dev database and the content volume with example content.

Run it with `just seed`, which pipes this file into the running app
container: that container already has the toon package installed, the
TOON_* environment and the content volume mounted on TOON_CONTENT_ROOT.

The seed is destructive and repeatable. It first drops every group and every
member (cascading to sites, versions, previews, invitees, access keys, linked
repositories and CLI sessions) plus the matching version directories on the content volume,
and then writes the whole example set again. The append-only audit log is
never touched: the runtime account has no DELETE on it, and an audit trail
that a script can rewrite is worth nothing.

What it puts down:

  - two groups with a different default access policy;
  - one group holding an admin, an editor and a reader, so the role model is
    visible in one screen;
  - a member with a site role only, without group membership;
  - sites on every access base, with the interesting extras combined: a public
    site with a secret link beside it, and a closed site on invitees alone;
  - real published versions plus two previews, unpacked through the normal
    ingest path from a bundle this script builds in memory;
  - feedback on the previews of the public annual report: a handful of items
    of every kind, status and author kind, with replies from the site team
    and from a submitter and one screenshot, so the Feedback tab has
    something to show.
"""

from __future__ import annotations

import asyncio
import io
import json
import struct
import sys
import tarfile
import time
import uuid
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isqrt
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from toon.access import keys as access_keys
from toon.auth.sessions import site_prefix
from toon.config import Settings, load_settings
from toon.constants import (
    AccessBase,
    FeedbackAuthor,
    FeedbackIdentity,
    FeedbackReplyAuthor,
    FeedbackStatus,
    Role,
)
from toon.db import make_engine, make_session_factory
from toon.ingest.service import Deployer, IngestService
from toon.ingest.store import ContentStore, version_ref
from toon.models.feedback import FeedbackItem, FeedbackReply, FeedbackScreenshot
from toon.models.identity import (
    Group,
    GroupMember,
    Member,
    MemberStatus,
    PlatformRole,
    SiteMember,
)
from toon.models.publication import Invitee, Preview, Site, Version, VersionTarget
from toon.serving.feedback_api import parse_item

# The sub the mock OIDC server hands out for every dev login
# (dev/mock-oidc-config.json), and the sub dev/compose.yml bootstraps into a
# platform admin. Used as a fallback only: the configured value wins, so a
# changed TOON_BOOTSTRAP_ADMIN_SUB still lands on the seeded member.
DEFAULT_ADMIN_SUB = "dev-beheerder"
ADMIN_EMAIL = "beheerder@toon.local"

BUNDLE_NAME = "site.tar.gz"

GROUP_PUBLIC = "rijksoverheid"
GROUP_INTERNAL = "inspectie"

PREVIEW_OPEN = "pr-42"
PREVIEW_RESTRICTED = "pr-77"


# -- The example site -------------------------------------------------------

_CSS = """:root {
  color-scheme: light dark;
  --ink: #1b1b1b;
  --paper: #fdfdfc;
  --line: #c8c8c4;
  --accent: #154273;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 0 1rem 4rem;
  font: 1rem/1.6 system-ui, -apple-system, "Segoe UI", sans-serif;
  color: var(--ink);
  background: var(--paper);
}
header, main, footer { max-width: 44rem; margin: 0 auto; }
header { padding: 3rem 0 2rem; border-bottom: 2px solid var(--accent); }
h1 { margin: 0.25rem 0; font-size: 2rem; line-height: 1.2; }
.group { margin: 0; text-transform: uppercase; letter-spacing: 0.08em;
  font-size: 0.75rem; font-weight: 700; color: var(--accent); }
.badge { display: inline-block; margin: 0.75rem 0 0; padding: 0.15rem 0.6rem;
  border: 1px solid var(--line); border-radius: 999px; font-size: 0.8rem; }
main { padding-block: 2rem; }
table { width: 100%; border-collapse: collapse; margin: 1.5rem 0; }
th, td { padding: 0.5rem 0.25rem; text-align: left; border-bottom: 1px solid var(--line); }
footer { padding-top: 2rem; border-top: 1px solid var(--line); font-size: 0.85rem; }
a { color: var(--accent); }
@media (prefers-color-scheme: dark) {
  :root { --ink: #f2f2f0; --paper: #16181c; --line: #3a3d44; --accent: #8fb8e8; }
}
"""

_PAGE = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="assets/toon.css">
</head>
<body>
<header>
<p class="group">{group}</p>
<h1>{title}</h1>
<p class="badge">{badge}</p>
</header>
<main>
{body}
</main>
<footer>
<p>Gezaaide dev-inhoud van Toon, gebouwd op {stamp}.</p>
</footer>
</body>
</html>
"""

_INDEX_BODY = """<p>{lead}</p>
<table>
<tr><th>Groep</th><td>{group}</td></tr>
<tr><th>Site</th><td>{title}</td></tr>
<tr><th>Uitgave</th><td>{badge}</td></tr>
</table>
<p><a href="verantwoording.html">Verantwoording en bronnen</a></p>
"""

_SUB_BODY = """<p>Deze pagina zit in dezelfde bundel als de startpagina en
bestaat om te laten zien dat een deeplink en een relatief pad het doen.</p>
<p><a href="index.html">Terug naar de startpagina</a></p>
"""


def _site_files(group_name: str, title: str, badge: str, lead: str) -> dict[str, bytes]:
    """The files of one example site, ready for the ContentStore."""
    stamp = datetime.now(tz=UTC).strftime("%d-%m-%Y %H:%M UTC")
    common = {"group": group_name, "title": title, "badge": badge, "stamp": stamp}
    return {
        "index.html": _PAGE.format(
            body=_INDEX_BODY.format(lead=lead, **common), **common
        ).encode(),
        "verantwoording.html": _PAGE.format(
            body=_SUB_BODY, **{**common, "title": f"Verantwoording - {title}"}
        ).encode(),
        "assets/toon.css": _CSS.encode(),
    }


def _bundle(files: dict[str, bytes]) -> bytes:
    """Packs the files into a .tar.gz, the same shape a real deploy uploads."""
    buffer = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for rel_path, content in files.items():
            info = tarfile.TarInfo(rel_path)
            info.size = len(content)
            info.mtime = now
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


# -- Example feedback ---------------------------------------------------------

FEEDBACK_SITE = "jaarverslag"

# The size of the stand-in screenshot, and where its pin sits: half the
# 1280 by 800 window of the item it belongs to.
SCREENSHOT_SIZE = (640, 400)
SCREENSHOT_PIN = (159, 66)


@dataclass(frozen=True)
class _Feedback:
    """One example item: what the widget sends for it, on which preview and
    page, and what the site team did with it since."""

    ref: str
    page: str
    title: str
    kind: str
    comment: str
    author: dict[str, str]
    anchor: dict[str, Any] | None
    status: FeedbackStatus
    age: timedelta
    team_reply: str | None = None
    giver_reply: str | None = None
    screenshot: bool = False

    def widget_item(self, root: str) -> dict[str, Any]:
        """The `item` part of the widget's create request."""
        return {
            "kind": self.kind,
            "comment": self.comment,
            "page": {"path": f"{root}{self.page}", "title": self.title},
            "anchor": self.anchor,
            "viewport": {"width": 1280, "height": 800, "dpr": 2},
            "author": self.author,
            "screenshot": {"region": None},
        }


# Every kind, status and author kind, on both previews. The selectors and
# quotes point at the pages _site_files writes, so the widget finds the
# places again. The item with an account is the bootstrap member's own: the
# dev login always signs in as that member, so the widget's Mijn feedback
# shows it locally.
_FEEDBACK = (
    _Feedback(
        ref=PREVIEW_OPEN,
        page="",
        title="Jaarverslag 2025",
        kind="point",
        comment="De titel past op mijn telefoon niet op één regel. Kan hij korter?",
        author={"kind": "anonymous"},
        anchor={
            "selectors": [{"type": "CssSelector", "value": "body > header:nth-of-type(1) > h1:nth-of-type(1)"}],
            "point": {"x": 0.42, "y": 0.5, "pageX": 318, "pageY": 132},
        },
        status=FeedbackStatus.NEW,
        age=timedelta(hours=2),
        screenshot=True,
    ),
    _Feedback(
        ref=PREVIEW_OPEN,
        page="",
        title="Jaarverslag 2025",
        kind="text",
        comment="Heet dit niet de redactieronde? Zo staat het in de rest van het verslag.",
        author={"kind": "name", "name": "Jan de Boer"},
        anchor={
            "selectors": [
                {
                    "type": "TextQuoteSelector",
                    "exact": "tekstronde",
                    "prefix": "Voorvertoning van de ",
                    "suffix": " in pull request 42.",
                },
                {"type": "CssSelector", "value": "body > main:nth-of-type(1) > p:nth-of-type(1)"},
            ],
            "point": None,
        },
        status=FeedbackStatus.NEW,
        age=timedelta(days=1),
    ),
    _Feedback(
        ref=PREVIEW_OPEN,
        page="verantwoording.html",
        title="Verantwoording - Jaarverslag 2025",
        kind="page",
        comment="De bronnenlijst mist de CBS-tabel over 2024.",
        author={"kind": "account"},
        anchor=None,
        status=FeedbackStatus.SEEN,
        age=timedelta(days=3),
        team_reply="Dank je, die tabel komt erbij in de volgende preview.",
        giver_reply="Fijn. Het gaat om de tabel met de cijfers per provincie.",
    ),
    _Feedback(
        ref=PREVIEW_RESTRICTED,
        page="verantwoording.html",
        title="Verantwoording - Jaarverslag 2025",
        kind="point",
        comment="Deze link gaat naar de startpagina van de preview, niet naar de live site. Klopt dat?",
        author={"kind": "name", "name": "Fatima el Amrani"},
        anchor={
            "selectors": [
                {"type": "CssSelector", "value": "body > main:nth-of-type(1) > p:nth-of-type(2) > a:nth-of-type(1)"}
            ],
            "point": {"x": 0.5, "y": 0.5, "pageX": 214, "pageY": 418},
        },
        status=FeedbackStatus.SEEN,
        age=timedelta(days=4),
    ),
    _Feedback(
        ref=PREVIEW_RESTRICTED,
        page="",
        title="Jaarverslag 2025",
        kind="text",
        comment="Wat betekent dit voor lezers buiten het siteteam?",
        author={"kind": "anonymous"},
        anchor={
            "selectors": [
                {
                    "type": "TextQuoteSelector",
                    "exact": "strenger dan de site zelf",
                    "prefix": "een eigen toegang, ",
                    "suffix": ".",
                },
                {"type": "CssSelector", "value": "body > main:nth-of-type(1) > p:nth-of-type(1)"},
            ],
            "point": None,
        },
        status=FeedbackStatus.RESOLVED,
        age=timedelta(days=6),
        team_reply="Die zien deze preview niet. Dat is hier zo bedoeld.",
    ),
)


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _screenshot_png(width: int, height: int, pin: tuple[int, int]) -> bytes:
    """A stand-in screenshot: a light page, a dark bar where the title is and
    a blue pin with a white ring where the remark points. A plain RGB PNG
    built by hand, so the seed needs no image library."""
    paper, ink = b"\xfd\xfd\xfc", b"\x1b\x1b\x1b"
    pin_layers = ((b"\xff\xff\xff", 12), (b"\x18\x45\x76", 9))
    rows = []
    for y in range(height):
        row = bytearray(paper * width)
        if 54 <= y < 78:
            row[48 * 3 : 420 * 3] = ink * 372
        for colour, radius in pin_layers:
            if abs(y - pin[1]) <= radius:
                reach = isqrt(radius * radius - (y - pin[1]) ** 2)
                row[(pin[0] - reach) * 3 : (pin[0] + reach + 1) * 3] = colour * (2 * reach + 1)
        rows.append(b"\x00" + bytes(row))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + _png_chunk(b"IEND", b"")
    )


# -- Seeding ----------------------------------------------------------------


@dataclass(frozen=True)
class SeedResult:
    members: int
    groups: int
    sites: int
    versions: int
    previews: int
    feedback: int
    secret_link_key: str


def _member(
    sub: str,
    email: str,
    name: str,
    *,
    admin: bool = False,
    deactivated: bool = False,
    age_days: int = 0,
    seen_days_ago: int | None = None,
) -> Member:
    """A seeded member. `age_days` and `seen_days_ago` are counted back from now,
    so the platform page has three genuinely different sort orders to show
    instead of six members created in the same second."""
    now = datetime.now(tz=UTC)
    return Member(
        id=uuid.uuid4(),
        sso_subject=sub,
        email=email,
        name=name,
        platform_role=PlatformRole.ADMIN if admin else PlatformRole.MEMBER,
        status=MemberStatus.DEACTIVATED if deactivated else MemberStatus.ACTIVE,
        created_at=now - timedelta(days=age_days),
        last_login_at=None if seen_days_ago is None else now - timedelta(days=seen_days_ago),
    )


def _site(
    group: Group,
    slug: str,
    title: str,
    base: AccessBase,
    creator: Member,
    *,
    keys: bool = False,
    invitees: bool = False,
) -> Site:
    return Site(
        id=uuid.uuid4(),
        group_id=group.id,
        slug=slug,
        title=title,
        access_base=base,
        access_keys=keys,
        access_invitees=invitees,
        created_by=creator.id,
    )


async def _wipe(session_factory: async_sessionmaker[AsyncSession], store: ContentStore) -> None:
    """Drops every group and member, and the version directories they owned."""
    async with session_factory() as db:
        rows = await db.execute(select(Version.site_id, Version.id))
        storage_refs = [version_ref(row.site_id, row.id) for row in rows]
        await db.execute(delete(Group))
        await db.execute(delete(Member))
        await db.commit()
    for storage_ref in storage_refs:
        store.delete_version(storage_ref)


async def _publish(
    ingest: IngestService,
    session_factory: async_sessionmaker[AsyncSession],
    store: ContentStore,
    site: Site,
    files: dict[str, bytes],
    deployer: Deployer,
    ref: str | None = None,
) -> uuid.UUID:
    """Runs one bundle through the real ingest path; `ref` makes it a preview."""
    source = store.new_spool_file()
    source.write_bytes(_bundle(files))
    try:
        async with session_factory() as db:
            if ref is None:
                return await ingest.deploy(db, site, BUNDLE_NAME, source, deployer)
            return await ingest.preview_deploy(
                db, site, ref, BUNDLE_NAME, source, deployer
            )
    finally:
        source.unlink(missing_ok=True)


async def _feedback(
    session_factory: async_sessionmaker[AsyncSession],
    site: Site,
    group_slug: str,
    versions: dict[str, uuid.UUID],
    giver: Member,
    team: Member,
) -> None:
    """Puts the example feedback on `site`. Every item goes through the widget
    API's own check first, so it holds exactly what the server would have
    stored for that request. What the site team did since is set here."""
    now = datetime.now(tz=UTC)
    async with session_factory() as db:
        replies: list[FeedbackReply] = []
        screenshots: list[FeedbackScreenshot] = []
        for sample in _FEEDBACK:
            root = f"/{group_slug}/{site.slug}/_preview/{sample.ref}/"
            checked = parse_item(
                json.dumps(sample.widget_item(root)),
                root,
                site_prefix(group_slug, site.slug),
            )
            account = checked.author is FeedbackAuthor.ACCOUNT
            created = now - sample.age
            # Halfway between sending and now, the site team looked at it.
            handled = None if sample.status is FeedbackStatus.NEW else created + sample.age / 2
            item = FeedbackItem(
                id=uuid.uuid4(),
                site_id=site.id,
                target=VersionTarget.PREVIEW,
                preview_ref=sample.ref,
                version_id=versions[sample.ref],
                page_path=checked.page_path,
                page_title=checked.page_title,
                kind=checked.kind,
                anchor=checked.anchor,
                quote=checked.quote,
                comment=checked.comment,
                author_kind=checked.author,
                author_sub=giver.sso_subject if account else None,
                author_name=giver.name if account else checked.author_name,
                author_email=giver.email.lower() if account else None,
                viewport=checked.viewport,
                status=sample.status,
                status_changed_at=handled,
                status_changed_by=None if handled is None else team.id,
                resolved_at=handled if sample.status is FeedbackStatus.RESOLVED else None,
                created_at=created,
            )
            db.add(item)
            if sample.team_reply is not None:
                replies.append(
                    FeedbackReply(
                        item_id=item.id,
                        author_kind=FeedbackReplyAuthor.MEMBER,
                        member_id=team.id,
                        author_name=team.name,
                        body=sample.team_reply,
                        created_at=handled or created,
                    )
                )
            if sample.giver_reply is not None:
                replies.append(
                    FeedbackReply(
                        item_id=item.id,
                        author_kind=FeedbackReplyAuthor.GIVER,
                        body=sample.giver_reply,
                        created_at=created + sample.age * 3 / 4,
                    )
                )
            if sample.screenshot:
                data = _screenshot_png(*SCREENSHOT_SIZE, SCREENSHOT_PIN)
                screenshots.append(
                    FeedbackScreenshot(
                        item_id=item.id,
                        media_type="image/png",
                        data=data,
                        size_bytes=len(data),
                        width=SCREENSHOT_SIZE[0],
                        height=SCREENSHOT_SIZE[1],
                    )
                )
        # The items first: replies and screenshots point at them.
        await db.flush()
        db.add_all(replies + screenshots)
        await db.commit()


async def seed(settings: Settings) -> SeedResult:
    """Wipes and refills the dev database plus the content volume."""
    if settings.environment != "dev":
        raise RuntimeError(
            "seed weigert te draaien buiten TOON_ENVIRONMENT=dev: hij wist alle groepen en leden"
        )

    engine = make_engine(settings)
    session_factory = make_session_factory(engine)
    store = ContentStore(settings.content_root)
    ingest = IngestService(store, settings)

    try:
        await _wipe(session_factory, store)

        admin_sub = settings.bootstrap_admin_sub or DEFAULT_ADMIN_SUB
        admin = _member(admin_sub, ADMIN_EMAIL, "Dev Beheerder", admin=True, age_days=240, seen_days_ago=0)
        editor = _member(
            "dev-redacteur", "s.jansen@toon.local", "Sanne Jansen", age_days=180, seen_days_ago=1
        )
        reader = _member("dev-lezer", "t.devries@toon.local", "Tim de Vries", age_days=95, seen_days_ago=12)
        site_only = _member(
            "dev-sitelid", "n.bakker@toon.local", "Noor Bakker", age_days=60, seen_days_ago=40
        )
        inspector = _member(
            "dev-inspecteur", "w.dekker@toon.local", "Wim Dekker", age_days=30, seen_days_ago=3
        )
        # Never logged in, so the platform page has a row where "last activity"
        # is empty and has to sort last in both date orders.
        deactivated_member = _member(
            "dev-ontslagen", "k.visser@toon.local", "Kim Visser", deactivated=True, age_days=2
        )
        members = [admin, editor, reader, site_only, inspector, deactivated_member]

        public_group = Group(
            id=uuid.uuid4(),
            slug=GROUP_PUBLIC,
            name="Rijksoverheid",
            default_access_base=AccessBase.PUBLIC,
        )
        internal_group = Group(
            id=uuid.uuid4(),
            slug=GROUP_INTERNAL,
            name="Inspectie Leefomgeving",
            default_access_base=AccessBase.SITE_TEAM,
        )

        # The public group carries all three roles at once, so one group page
        # shows the whole role model. The internal group has its own admin and
        # deliberately not the bootstrap member: that is what makes the
        # difference between "platform admin sees everything" and real group
        # membership visible in dev.
        group_members = [
            GroupMember(group_id=public_group.id, member_id=admin.id, role=Role.ADMIN),
            GroupMember(group_id=public_group.id, member_id=editor.id, role=Role.EDITOR),
            GroupMember(group_id=public_group.id, member_id=reader.id, role=Role.READER),
            GroupMember(group_id=internal_group.id, member_id=inspector.id, role=Role.ADMIN),
        ]

        # Every base occurs, and the extras occur both on a closed base (where
        # they are the only way in) and beside a wider one (where they add a
        # second road): that second case is the one the old single enum could
        # not express at all.
        annual = _site(public_group, FEEDBACK_SITE, "Jaarverslag 2025", AccessBase.PUBLIC, admin)
        # Feedback on the previews only, from anyone who can see them: the
        # widget then shows on the two previews below, and the live site
        # stays as it is.
        annual.feedback_enabled = True
        annual.feedback_identity = FeedbackIdentity.ANONYMOUS
        annual.feedback_live = False
        figures = _site(
            public_group, "kerncijfers", "Kerncijfers open data", AccessBase.NOBODY, admin, keys=True
        )
        evaluation = _site(
            public_group,
            "evaluatie",
            "Evaluatie subsidieregeling",
            AccessBase.NOBODY,
            admin,
            invitees=True,
        )
        report = _site(
            internal_group,
            "toezichtrapport",
            "Rapportage toezicht 2025",
            AccessBase.SITE_TEAM,
            inspector,
            keys=True,
            invitees=True,
        )
        instructions = _site(
            internal_group, "werkinstructies", "Werkinstructies", AccessBase.SSO, inspector
        )
        sites = [annual, figures, evaluation, report, instructions]

        async with session_factory() as db:
            db.add_all(members)
            db.add_all([public_group, internal_group])
            await db.flush()
            db.add_all(group_members)
            db.add_all(sites)
            await db.flush()
            # A site role adds to the group role and never subtracts from
            # it; this member has no group role at all, so the site is the
            # only thing she can reach.
            db.add(
                SiteMember(
                    site_id=evaluation.id,
                    member_id=site_only.id,
                    role=Role.EDITOR,
                    added_by=admin.id,
                )
            )
            db.add_all(
                [
                    Invitee(site_id=evaluation.id, identifier=ADMIN_EMAIL, added_by=admin.id),
                    Invitee(
                        site_id=evaluation.id,
                        identifier="a.hendriks@example.org",
                        added_by=admin.id,
                    ),
                    # On toezichtrapport both extras sit beside a base that
                    # already lets the site team in: one reviewer from outside
                    # plus a link for whoever cannot log in at all.
                    Invitee(
                        site_id=report.id, identifier="r.dekker@example.org", added_by=inspector.id
                    ),
                ]
            )
            await db.commit()

        by_admin = Deployer(member_id=admin.id)
        by_inspector = Deployer(member_id=inspector.id)

        await _publish(
            ingest, session_factory, store, annual,
            _site_files("Rijksoverheid", "Jaarverslag 2025", "Live versie",
                  "Het jaarverslag over 2025, publiek toegankelijk zonder inloggen."),
            by_admin,
        )
        await _publish(
            ingest, session_factory, store, figures,
            _site_files("Rijksoverheid", "Kerncijfers open data", "Live versie",
                  "Deze cijfers staan achter een geheime link: alleen wie de link heeft, komt binnen."),
            by_admin,
        )
        await _publish(
            ingest, session_factory, store, evaluation,
            _site_files("Rijksoverheid", "Evaluatie subsidieregeling", "Live versie",
                  "Concept-evaluatie, alleen te lezen door de genodigden op de lijst."),
            by_admin,
        )
        await _publish(
            ingest, session_factory, store, report,
            _site_files("Inspectie Leefomgeving", "Rapportage toezicht 2025", "Live versie",
                  "Interne rapportage, zichtbaar voor de leden van de groep."),
            by_inspector,
        )

        open_preview = await _publish(
            ingest, session_factory, store, annual,
            _site_files("Rijksoverheid", "Jaarverslag 2025", f"Preview {PREVIEW_OPEN}",
                  "Voorvertoning van de tekstronde in pull request 42."),
            by_admin, ref=PREVIEW_OPEN,
        )
        restricted_preview = await _publish(
            ingest, session_factory, store, annual,
            _site_files("Rijksoverheid", "Jaarverslag 2025", f"Preview {PREVIEW_RESTRICTED}",
                  "Voorvertoning met een eigen toegang, strenger dan de site zelf."),
            by_admin, ref=PREVIEW_RESTRICTED,
        )
        await _feedback(
            session_factory,
            annual,
            GROUP_PUBLIC,
            {PREVIEW_OPEN: open_preview, PREVIEW_RESTRICTED: restricted_preview},
            giver=admin,
            team=editor,
        )

        async with session_factory() as db:
            # A preview may be stricter than its site; this one shows that
            # override in the Previews tab without anyone having to set it.
            await db.execute(
                update(Preview)
                .where(Preview.site_id == annual.id, Preview.ref == PREVIEW_RESTRICTED)
                .values(
                    access_base_override=AccessBase.SITE_TEAM,
                    access_keys_override=False,
                    access_invitees_override=False,
                )
            )
            _, secret_link_key = await access_keys.create_key(
                db, figures.id, "Gedeeld met de redactieraad"
            )
            await access_keys.create_key(db, report.id, "Meelezer buiten de inspectie")
            await db.commit()

        async with session_factory() as db:
            return SeedResult(
                members=len(members),
                groups=2,
                sites=len(sites),
                versions=await db.scalar(select(func.count()).select_from(Version)),
                previews=await db.scalar(select(func.count()).select_from(Preview)),
                feedback=await db.scalar(select(func.count()).select_from(FeedbackItem)),
                secret_link_key=secret_link_key,
            )
    finally:
        await engine.dispose()


# -- Command line -----------------------------------------------------------


def _report(settings: Settings, result: SeedResult) -> str:
    admin_base = (settings.base_url or "http://beheer.toon.localhost:8080").rstrip("/")
    content_base = (settings.content_base_url or admin_base).rstrip("/")
    annual = f"{content_base}/{GROUP_PUBLIC}/jaarverslag/"
    lines = [
        "",
        "Dev-inhoud gezaaid.",
        (
            f"  {result.members} leden, {result.groups} groepen, {result.sites} sites, "
            f"{result.versions} versies, {result.previews} previews, {result.feedback} feedbackitems"
        ),
        "",
        "Inloggen en rondkijken:",
        f"  beheer     {admin_base}/",
        f"  live       {annual}",
        f"  preview    {annual}_preview/{PREVIEW_OPEN}/",
        f"  feedback   {admin_base}/{GROUP_PUBLIC}/{FEEDBACK_SITE}/feedback",
        f"  geheime link  {content_base}/{GROUP_PUBLIC}/kerncijfers/?key={result.secret_link_key}",
        "",
        "Publiceren vanaf je eigen machine, met de Toon-CLI:",
        f"  toon login   (beheer-URL {admin_base})",
        f"  en keur de code in je terminal goed op {admin_base}/cli-link",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    result = asyncio.run(seed(settings))
    sys.stdout.write(_report(settings, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Tests for Toon's own pages around the feedback widget
(serving/feedback_consent.py): the consent page for feedback with an account,
the consent itself, ending it, and hiding and showing the button. Against the
real PostgreSQL test container, with the app of test_feedback_api.py.

Refusals first, each the neutral 404 byte for byte. Then the consent page's
shielding (the sandbox, frame-ancestors, no script, its one style by hash),
the form that takes no press in its first second and consents only with its
box ticked, the nonce that binds the form to its session, site and way back,
the link ticket that brings a secret link along on a site shared by link,
the way back through the login that hands out the site cookie, and the
cookies these pages set and clear.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

import httpx
import pytest
import pytest_asyncio
from helpers_audit import install_audit_recorder
from helpers_csp import assert_runs_no_script_and_only_its_own_style, directives
from helpers_oidc import MockIdP, make_client_jwk, make_oidc_client
from sqlalchemy import delete, select, update
from test_feedback_api import (
    ADMIN_URL,
    ITEMS,
    PREVIEW_ROOT,
    ROOT,
    SECRET,
    Environment,
    client_for,
    consented_session,
    create,
    header_list,
    open_environment,
    page_token,
    rolled_back,
)

from toon import i18n
from toon.access import keys
from toon.access.decision import FeedbackPolicy, allow
from toon.auth.sessions import (
    CONTENT_ANCHOR_COOKIE,
    CONTENT_ANCHOR_PATH,
    CONTENT_SESSION_COOKIE,
    SessionKind,
    SessionStore,
    check_signature,
    sign,
)
from toon.constants import (
    FEEDBACK_HIDE_COOKIE,
    PATH_CONTENT_LOGIN,
    PATH_FEEDBACK_ACCOUNT,
    PATH_FEEDBACK_ACCOUNT_STOP,
    PATH_FEEDBACK_HIDE,
    PATH_FEEDBACK_SHOW,
    AccessBase,
    AccessPolicy,
    FeedbackIdentity,
)
from toon.models.identity import Group
from toon.models.publication import AccessKey, Site
from toon.platform import pages
from toon.serving import feedback_consent
from toon.serving.feedback_consent import (
    CONSENT_PAGE_HEADERS,
    MAX_FORM_BYTES,
    PageRoute,
    consent_page_html,
    page_route,
    return_target,
    without_key,
)
from toon.serving.feedback_token import (
    CONSENT_LIFETIME_S,
    HIDE_LIFETIME_S,
    LINK_LIFETIME_S,
    mint_consent_nonce,
    mint_link_ticket,
    parse_consent_nonce,
    parse_hide_mark,
)
from toon.serving.response import NEUTRAL_404_BODY, neutral_404_response

DOCUMENT = {"Sec-Fetch-Dest": "document"}
FORM = {**DOCUMENT, "Content-Type": "application/x-www-form-urlencoded"}
# What a form another site's page submits on its own looks like.
CROSS_SITE_FORM = {**FORM, "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Origin": "https://kwaad.example"}
# The account id of a visitor whose identity provider sends no name.
NAMELESS_SUB = "8f14e45f-ceea-467e-a7d4-2b9c1f0e3a77"

BAD_RETURNS = [
    "//kwaad.example/",
    "https://kwaad.example/aurora/site/",
    "/aurora/site/\\..\\",
    "/aurora/site/\tx",
    "/aurora/site/\x7f",
    "aurora/site/",
    "",
    "/",
    "/aurora",
    "/aurora/",
    "/-/feedback/hide/",
    "/robots.txt/x/",
    "/Aurora/site/",
    "/aurora/site/" + "a" * 2040,
]
BAD_RETURN_IDS = [
    "other-host",
    "absolute-url",
    "backslash",
    "tab",
    "delete-character",
    "relative",
    "empty",
    "host-root",
    "group-only",
    "group-root",
    "platform-path",
    "reserved-group",
    "not-a-slug",
    "too-long",
]


@pytest_asyncio.fixture
async def environment(migrated_dsn: str, tmp_path: Path) -> AsyncIterator[Environment]:
    async with rolled_back(migrated_dsn) as factory:
        yield await open_environment(factory, tmp_path)


@pytest_asyncio.fixture
async def client(environment: Environment) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(environment.app) as c:
        yield c


def is_neutral_404(response: httpx.Response) -> bool:
    reference = neutral_404_response()
    return (
        response.status_code == 404
        and response.content == NEUTRAL_404_BODY
        and sorted((k.lower(), v) for k, v in response.headers.items() if k.lower() != "content-length")
        == sorted((k.lower(), v) for k, v in reference.headers.items() if k.lower() != "content-length")
    )


def with_anchor(client: httpx.AsyncClient, environment: Environment, **session: object):
    """A signed-in visitor: a content session and its anchor cookie, which
    rides on every path under /-/."""
    created = consented_session(environment.app, **session)
    client.cookies.set(
        CONTENT_ANCHOR_COOKIE, sign(SECRET, created.id), domain="toon.example", path=CONTENT_ANCHOR_PATH
    )
    return created


async def consent_page(client: httpx.AsyncClient, return_to: str = ROOT, **headers: str) -> httpx.Response:
    return await client.get(PATH_FEEDBACK_ACCOUNT, params={"returnTo": return_to}, headers={**DOCUMENT, **headers})


def nonce_of(page: httpx.Response) -> str:
    marker = 'name="nonce" value="'
    start = page.text.index(marker) + len(marker)
    return page.text[start : page.text.index('"', start)]


def through_the_login(return_to: str) -> str:
    return f"{PATH_CONTENT_LOGIN}?{urlencode({'returnTo': return_to})}"


def with_the_login(environment: Environment) -> None:
    """The content host's login beside the feedback routes, as main.py
    mounts them, so the way back after consent can be followed."""
    settings = environment.app.state.settings.model_copy(update={"oidc_client_private_jwk": make_client_jwk()})
    environment.app.state.oidc_client = make_oidc_client(settings, MockIdP())
    environment.app.include_router(pages.router)


def site_cookies(client: httpx.AsyncClient) -> list[tuple[str, str | None]]:
    """The site cookies the client keeps: their path and the session id
    their value signs."""
    return [
        (cookie.path, check_signature(SECRET, cookie.value or ""))
        for cookie in client.cookies.jar
        if cookie.name == CONTENT_SESSION_COOKIE
    ]


async def set_site(environment: Environment, **values: object) -> None:
    async with environment.factory() as db:
        await db.execute(update(Site).where(Site.id == environment.world.site_id).values(**values))
        await db.commit()


# --- The way back -------------------------------------------------------------------


class TestTheWayBack:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("/aurora/site/", "/aurora/site/"),
            ("/aurora/site/?a=1&b=%2F&c=x+y", "/aurora/site/?a=1&b=%2F&c=x+y"),
            ("/aurora/site/?key=abc.def&x=1", "/aurora/site/?x=1"),
            ("/aurora/site/?key=abc", "/aurora/site/"),
            ("/aurora/site/?%6Bey=abc&x=1", "/aurora/site/?x=1"),
            ("/aurora/site/?key=&x=1&key=y", "/aurora/site/?x=1"),
            ("/aurora/site/?x=1&key=abc#deel", "/aurora/site/?x=1#deel"),
            ("/aurora/site/#deel", "/aurora/site/#deel"),
        ],
    )
    def test_every_key_leaves_and_nothing_else_changes(self, value, expected):
        assert without_key(value) == expected

    @pytest.mark.parametrize("value", BAD_RETURNS, ids=BAD_RETURN_IDS)
    def test_a_way_back_off_a_site_is_no_way_back(self, value):
        assert return_target([value]) is None

    def test_one_way_back_and_only_one(self):
        assert return_target([]) is None
        assert return_target([ROOT, ROOT]) is None
        target = return_target(["/aurora/site/docs/?key=abc&x=1"])
        assert target is not None
        assert (target.path, target.group, target.site, target.prefix) == (
            "/aurora/site/docs/?x=1",
            "aurora",
            "site",
            "/aurora/site/",
        )

    @pytest.mark.parametrize(
        ("path", "route"),
        [
            ("/aurora/site/", PageRoute("aurora", "site", None)),
            ("/aurora/site", PageRoute("aurora", "site", None)),
            ("/aurora/site/docs/?x=1", PageRoute("aurora", "site", None)),
            ("/aurora/site/_preview/pr-1/", PageRoute("aurora", "site", "pr-1")),
            ("/aurora/site/_preview/pr-1", PageRoute("aurora", "site", "pr-1")),
            ("/aurora/site/_preview/pr-1?x=1#y", PageRoute("aurora", "site", "pr-1")),
            ("/aurora/site/_version/0b9f4c3e-1d2a-4e5f-8a9b-0c1d2e3f4a5b/", None),
            ("/aurora/site/_preview/", None),
            ("/aurora/site/_preview", None),
            ("/aurora/site/_preview/PR 1/", None),
            ("/aurora/site/_preview/pr-1%0A/", None),
            ("/-/feedback/x", None),
        ],
    )
    def test_the_page_a_way_back_shows(self, path, route):
        assert page_route(path) == route


# --- The consent page ---------------------------------------------------------------


class TestTheConsentPageRefuses:
    @pytest.mark.parametrize("dest", [None, "empty", "iframe", "script"])
    async def test_anything_but_a_top_level_navigation(self, client, environment, dest):
        with_anchor(client, environment)
        headers = {} if dest is None else {"Sec-Fetch-Dest": dest}
        response = await client.get(PATH_FEEDBACK_ACCOUNT, params={"returnTo": ROOT}, headers=headers)
        assert is_neutral_404(response)

    @pytest.mark.parametrize("value", BAD_RETURNS, ids=BAD_RETURN_IDS)
    async def test_a_way_back_that_does_not_hold(self, client, environment, value):
        with_anchor(client, environment)
        assert is_neutral_404(await consent_page(client, value))

    async def test_no_or_two_ways_back(self, client, environment):
        with_anchor(client, environment)
        assert is_neutral_404(await client.get(PATH_FEEDBACK_ACCOUNT, headers=DOCUMENT))
        two = await client.get(f"{PATH_FEEDBACK_ACCOUNT}?returnTo=%2Faurora%2Fsite%2F&returnTo=%2F", headers=DOCUMENT)
        assert is_neutral_404(two)

    @pytest.mark.parametrize(
        "return_to",
        [
            "/aurora/site/_version/0b9f4c3e-1d2a-4e5f-8a9b-0c1d2e3f4a5b/",
            "/aurora/site/_preview/",
            "/aurora/site/_preview/onbekend/",
            "/aurora/onbekend/",
            "/onbekend/site/",
        ],
        ids=["version-view", "preview-without-ref", "unknown-preview", "unknown-site", "unknown-group"],
    )
    async def test_a_page_without_the_widget(self, client, environment, return_to):
        with_anchor(client, environment)
        assert is_neutral_404(await consent_page(client, return_to))

    @pytest.mark.parametrize(
        ("change", "return_to"),
        [
            ({"feedback_enabled": False}, ROOT),
            ({"feedback_enabled": False}, PREVIEW_ROOT),
            ({"feedback_live": False}, ROOT),
            ({"access_base": AccessBase.SITE_TEAM}, ROOT),
            ({"access_base": AccessBase.NOBODY}, PREVIEW_ROOT),
        ],
        ids=["feedback-off", "feedback-off-on-a-preview", "previews-only", "team-only", "nobody"],
    )
    async def test_a_page_the_visitor_cannot_see_or_without_feedback(self, client, environment, change, return_to):
        with_anchor(client, environment)
        await set_site(environment, **change)
        assert is_neutral_404(await consent_page(client, return_to))

    @pytest.mark.parametrize("planted_path", ["/", "/-/feedback/"], ids=["read-last", "read-first"])
    async def test_an_anchor_that_arrives_twice(self, client, environment, planted_path):
        """A second anchor, set at another path by an unshielded page's
        script or from a sibling host, beside the visitor's own. Whichever
        value the request carries last would be read: at `/` it comes after
        the visitor's own and the page would name the planter, at a longer
        path it comes first. Either way there is no page to show."""
        with_anchor(client, environment)
        planter = consented_session(environment.app, environment.world.site_id, sub="ander", name="Ander Persoon")
        client.cookies.set(CONTENT_ANCHOR_COOKIE, sign(SECRET, planter.id), domain="toon.example", path=planted_path)
        assert is_neutral_404(await consent_page(client))

    async def test_an_anchor_twice_does_not_send_the_visitor_to_the_login(self, client):
        """Neither value names a live session here. The login could not clear
        the one at `/`, so its way back would land here again, and again."""
        client.cookies.set(CONTENT_ANCHOR_COOKIE, "eigen.handtekening", domain="toon.example", path=CONTENT_ANCHOR_PATH)
        client.cookies.set(CONTENT_ANCHOR_COOKIE, "ander.handtekening", domain="toon.example", path="/")
        assert is_neutral_404(await consent_page(client))

    async def test_a_way_back_too_long_for_a_nonce(self, client, environment):
        """Within the length a way back may have, but sealed it would not fit
        a nonce: the neutral 404, not a 500."""
        with_anchor(client, environment)
        assert is_neutral_404(await consent_page(client, ROOT + "é" * 900))

    async def test_an_allowed_page_of_a_site_that_is_not_there(self, client, environment, monkeypatch):
        """Unreachable through the gate, which names the site it read; held
        anyway, because the page names the site."""

        async def ghost(db, group, site, ref, visitor):
            return allow(
                uuid.uuid4(),
                AccessPolicy(AccessBase.PUBLIC),
                site_id=uuid.uuid4(),
                feedback=FeedbackPolicy(enabled=True, identity=FeedbackIdentity.ANONYMOUS, live=True),
            )

        monkeypatch.setattr(feedback_consent, "decide_page", ghost)
        with_anchor(client, environment)
        assert is_neutral_404(await consent_page(client))


class TestTheConsentPage:
    async def test_without_a_session_the_login_comes_first(self, client):
        response = await consent_page(client, "/aurora/site/docs/?key=abc.def&x=1")
        assert response.status_code == 302
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        location = urlsplit(response.headers["location"])
        assert location.path == "/-/login"
        (back,) = parse_qs(location.query)["returnTo"]
        assert back == f"{PATH_FEEDBACK_ACCOUNT}?returnTo={quote('/aurora/site/docs/?x=1', safe='')}"

    async def test_the_page_is_shielded_from_every_site(self, client, environment):
        with_anchor(client, environment)
        response = await consent_page(client)
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        policy = directives(response.headers["content-security-policy"])
        assert policy["sandbox"] == ["allow-forms"]
        assert policy["frame-ancestors"] == ["'none'"]
        assert policy["form-action"] == ["'self'"]
        assert policy["base-uri"] == ["'none'"]
        assert_runs_no_script_and_only_its_own_style(response.text, response.headers["content-security-policy"])
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-robots-tag"] == "noindex, nofollow"
        assert response.headers["vary"] == "Accept-Language"
        for name, value in CONSENT_PAGE_HEADERS.items():
            assert response.headers[name] == value

    async def test_it_names_the_site_and_what_the_team_will_see(self, client, environment):
        with_anchor(client, environment, name="Kijker Jansen", email="Kijker@Example.org")
        response = await consent_page(client, "/aurora/site/docs/?x=1")
        body = response.text
        assert '<html lang="nl">' in body
        assert "<h1>Feedback geven met je account</h1>" in body
        assert "<p>Je bent ingelogd als <strong>Kijker Jansen</strong>.</p>" in body
        assert "Wil je feedback op <strong>Site</strong> geven met je account?" in body
        team = body.split("<dl>\n", 1)[1].split("\n</dl>", 1)[0]
        assert team == "<dt>Naam</dt>\n<dd>Kijker Jansen</dd>\n<dt>E-mailadres</dt>\n<dd>kijker@example.org</dd>"
        # Readers of the site see feedback too, but never the address. The
        # sentence after it names what lasts twelve hours at most, the
        # consent, and what does not: each item keeps the name and address.
        assert (
            "</dl>\n<p>Het e-mailadres zien alleen redacteuren en beheerders van de site.</p>\n"
            "<p>Je toestemming geldt tot je uitlogt of tot je sessie afloopt, uiterlijk na 12 uur. "
            "Wat je in die tijd stuurt, houdt die gegevens zolang de feedback bestaat.</p>\n"
        ) in body
        # What the login itself keeps (platform/pages.py): content_viewers
        # until 90 days after the last sign-in, the audit row with the
        # address for 90 days, and the row of a session the IdP ends
        # (auth/revalidation.py) with the address for three years. Then what
        # consent changes for a visitor whom only a secret link let in, who
        # viewed the site without a name until now: the way back hands out
        # the site cookie, and every page view the gate logs from then on
        # (serving/router.py) names the session's pseudonym beside the link.
        assert "je SSO-id en je e-mailadres tot 90 dagen na je laatste keer inloggen" in body
        assert (
            "Elke keer inloggen staat 90 dagen in het toegangslogboek, met je IP-adres. Beëindigt de "
            "inlogdienst je sessie eerder, dan blijft je IP-adres drie jaar in het toegangslogboek. "
            "Kwam je via een geheime link op deze site? Dan staat na je toestemming, tot je sessie eindigt, "
            "elke pagina die je op de site bekijkt 90 dagen in het toegangslogboek, met je account.</p>"
        ) in body
        assert f'<a href="{ADMIN_URL}/-/privacy">privacyverklaring</a>' in body
        assert f'<a href="{ADMIN_URL}/-/accessibility">Toegankelijkheid</a>' in body
        form = body.split(f'<form method="post" action="{PATH_FEEDBACK_ACCOUNT}">\n', 1)[1].split("\n</form>", 1)[0]
        assert form.split("\n")[1:] == [
            '<p class="agree"><input type="checkbox" id="agree" name="agree" value="1" required>',
            '<label for="agree">Het siteteam mag bij mijn feedback zien wat hierboven staat.</label></p>',
            '<p><button type="submit">Ja, met mijn account</button></p>',
        ]
        assert '<a href="/aurora/site/docs/?x=1">Terug zonder account</a>' in body

    async def test_the_form_takes_no_press_in_its_first_second(self, client, environment):
        """Against double-clickjacking: a site sends the tab here between the
        two presses of a double-click, and the second lands on the page as it
        appears. The whole form, box, label and button, stays hidden for the
        first second after the page paints, the label included because a
        press on it ticks the box. The timing is step-end, which holds the
        `from` keyframe for that whole second. Under ease the animated
        visibility turns visible as soon as the animation runs: in Chromium a
        tick and a press 250 ms after load posted the form. Nothing else in
        the sheet may animate or set visibility, or it could undo this."""
        with_anchor(client, environment)
        body = (await consent_page(client)).text
        css = body.split("<style>", 1)[1].split("</style>", 1)[0]
        assert css == feedback_consent._CONSENT_CSS
        assert "\nform {\n  animation: toon-arm 1s step-end;\n}\n" in css
        assert "\n@keyframes toon-arm {\n  from {\n    visibility: hidden;\n  }\n}\n" in css
        assert css.count("animation") == 1
        assert css.count("visibility") == 1
        assert body.count("<form") == 1

    @pytest.mark.parametrize("catalogue", [i18n.NL, i18n.EN], ids=["nl", "en"])
    def test_its_sentences_are_never_joined_with_a_semicolon_or_a_dash(self, catalogue):
        for key, sentence in catalogue.items():
            if key.startswith("consent."):
                assert ";" not in sentence, key
                assert "\u2013" not in sentence and "\u2014" not in sentence, key

    async def test_in_english_on_request(self, client, environment):
        with_anchor(client, environment)
        body = (await consent_page(client, **{"Accept-Language": "en-GB,en;q=0.9"})).text
        assert '<html lang="en">' in body
        assert "<p>You are signed in as <strong>Kijker Jansen</strong>.</p>" in body
        assert "Do you want to give feedback on <strong>Site</strong> with your account?" in body
        assert "<dt>Name</dt>\n<dd>Kijker Jansen</dd>\n<dt>Email address</dt>\n<dd>kijker@example.org</dd>" in body
        assert (
            "</dl>\n<p>Only the site's editors and administrators see the email address.</p>\n"
            "<p>Your consent lasts until you sign out or your session ends, after 12 hours at most. "
            "What you send in that time keeps those details for as long as the feedback exists.</p>\n"
        ) in body
        assert "your SSO ID and your email address until 90 days after your last sign-in" in body
        assert (
            "Each sign-in stays in its access log for 90 days, with your IP address. If the sign-in "
            "service ends your session early, your IP address stays in the access log for three years. "
            "Did you come to this site through a secret link? Then after your consent, until your session "
            "ends, every page you view on the site stays in the access log for 90 days, with your account.</p>"
        ) in body
        assert '<label for="agree">The site team may see what is listed above with my feedback.</label>' in body
        assert "Yes, with my account" in body
        assert "Back without an account" in body
        assert f'<a href="{ADMIN_URL}/-/privacy">privacy statement</a>' in body

    @pytest.mark.parametrize(
        ("language", "signed_in", "team"),
        [
            (
                "nl",
                "<p>Je bent ingelogd met account <strong>{sub}</strong>.</p>",
                "Dan ziet het siteteam alleen dat wat je op deze site stuurt van iemand met een account komt.</p>",
            ),
            (
                "en",
                "<p>You are signed in with account <strong>{sub}</strong>.</p>",
                "Then the site team only sees that what you send on this site comes from someone with an account.</p>",
            ),
        ],
        ids=["nl", "en"],
    )
    async def test_without_a_name_or_a_verified_email_the_team_sees_only_an_account(
        self, client, environment, language, signed_in, team
    ):
        """The account id says who is signed in, so a visitor pushed into
        somebody else's session can tell. The team never gets to see it:
        an item without a name or a verified address reaches them as one
        from someone with an account."""
        session = with_anchor(client, environment, sub=NAMELESS_SUB, name=None, email_verified=False)
        body = (await consent_page(client, **{"Accept-Language": language})).text
        assert signed_in.format(sub=NAMELESS_SUB) in body
        assert f"{team}\n<p>{i18n.t(language, 'consent.until')}</p>\n" in body
        assert body.count(session.sub) == 1
        assert "<dl>" not in body
        assert "kijker@example.org" not in body.lower()
        assert i18n.t(language, "consent.email_editors") not in body

    async def test_without_a_name_the_team_sees_the_verified_email_alone(self, client, environment):
        with_anchor(client, environment, sub=NAMELESS_SUB, name=None, email="Kijker@Example.org")
        body = (await consent_page(client)).text
        assert f"<p>Je bent ingelogd met account <strong>{NAMELESS_SUB}</strong>.</p>" in body
        assert "Dan ziet het siteteam bij alles wat je op deze site stuurt:</p>" in body
        team = body.split("<dl>\n", 1)[1].split("\n</dl>", 1)[0]
        assert team == "<dt>E-mailadres</dt>\n<dd>kijker@example.org</dd>"
        assert "</dl>\n<p>Het e-mailadres zien alleen redacteuren en beheerders van de site.</p>" in body
        assert body.count(NAMELESS_SUB) == 1

    async def test_without_a_verified_email_nothing_is_said_about_one(self, client, environment):
        """The note on who sees the address goes with the address: a list
        that holds the name alone says nothing about an address."""
        with_anchor(client, environment, email="Kijker@Example.org", email_verified=False)
        body = (await consent_page(client)).text
        team = body.split("<dl>\n", 1)[1].split("\n</dl>", 1)[0]
        assert team == "<dt>Naam</dt>\n<dd>Kijker Jansen</dd>"
        assert "e-mailadres zien alleen" not in body
        assert "kijker@example.org" not in body.lower()

    async def test_what_others_wrote_is_escaped(self, client, environment):
        await set_site(environment, title='<script>alert("titel")</script>')
        with_anchor(client, environment, name="<img src=x onerror=alert(1)>")
        body = (await consent_page(client, '/aurora/site/"><script>/')).text
        assert "<script>" not in body.split("</style>", 1)[1]
        assert "&lt;script&gt;alert(&quot;titel&quot;)&lt;/script&gt;" in body
        assert "&lt;img src=x onerror=alert(1)&gt;" in body
        assert 'href="/aurora/site/&quot;&gt;&lt;script&gt;/"' in body

    def test_without_an_admin_origin_nothing_is_linked_there(self, environment):
        session = consented_session(environment.app)
        body = consent_page_html("nl", site_title="Site", session=session, nonce="n", return_to=ROOT)
        assert "/-/privacy" not in body
        assert "<footer" not in body
        assert "muted" not in body

    async def test_a_preview_page_takes_consent_too(self, client, environment):
        with_anchor(client, environment)
        assert (await consent_page(client, f"{PREVIEW_ROOT}docs/")).status_code == 200

    async def test_the_nonce_binds_the_session_the_site_and_the_way_back(self, client, environment):
        session = with_anchor(client, environment)
        page = await consent_page(client, "/aurora/site/docs/?key=abc&x=1")
        nonce = parse_consent_nonce(SECRET, nonce_of(page), now=int(time.time()))
        assert nonce is not None
        assert nonce.session_id == session.id
        assert nonce.site_id == environment.world.site_id
        assert nonce.key_id is None
        assert nonce.return_to == "/aurora/site/docs/?x=1"
        assert nonce.expires_at <= int(time.time()) + CONSENT_LIFETIME_S
        # The page itself names no session id anywhere.
        assert session.id not in page.text


# --- A secret link on the way to the consent page ----------------------------------


async def shared_by_link(environment: Environment) -> str:
    """The site as one shared by secret link alone: base nobody with links
    on, and one link. Its key id, as the key cookie names it."""
    await set_site(environment, access_base=AccessBase.NOBODY, access_keys=True)
    async with environment.factory() as db:
        key, _ = await keys.create_key(db, environment.world.site_id, "extern")
        await db.commit()
    return str(key.id)


def ticket_for(environment: Environment, key_id: str, *, site_id=None, now=None) -> str:
    return mint_link_ticket(
        SECRET,
        key_id=key_id,
        site_id=environment.world.site_id if site_id is None else site_id,
        now=int(time.time()) if now is None else now,
    )


async def consent_page_with(
    client: httpx.AsyncClient, links: list[str], return_to: str = ROOT, **headers: str
) -> httpx.Response:
    params = [("returnTo", return_to), *(("link", link) for link in links)]
    return await client.get(PATH_FEEDBACK_ACCOUNT, params=params, headers={**DOCUMENT, **headers})


class TestASecretLinkOnTheWayToTheConsentPage:
    """A visitor whom only a secret link lets see the page. The key cookie
    stays on the site's path, so the widget's way here carries the link as a
    ticket, and the gate decides with it for the signed-in visitor."""

    @pytest.mark.parametrize("return_to", [ROOT, f"{PREVIEW_ROOT}docs/"], ids=["live", "preview"])
    async def test_with_its_ticket_the_page_opens(self, client, environment, return_to):
        key_id = await shared_by_link(environment)
        session = with_anchor(client, environment)
        link = ticket_for(environment, key_id)
        page = await consent_page_with(client, [link], return_to)
        assert page.status_code == 200
        for name, value in CONSENT_PAGE_HEADERS.items():
            assert page.headers[name] == value
        nonce = parse_consent_nonce(SECRET, nonce_of(page), now=int(time.time()))
        assert nonce is not None
        assert (nonce.session_id, nonce.site_id, nonce.key_id, nonce.return_to) == (
            session.id,
            environment.world.site_id,
            key_id,
            return_to,
        )
        # The way back without an account leads to the page alone.
        assert link not in page.text
        assert f'<a href="{return_to}">Terug zonder account</a>' in page.text

    async def test_without_its_ticket_there_is_no_page(self, client, environment):
        """The session alone does not let this visitor see the page."""
        await shared_by_link(environment)
        with_anchor(client, environment)
        assert is_neutral_404(await consent_page(client))
        assert is_neutral_404(await consent_page(client, PREVIEW_ROOT))

    async def test_without_a_session_the_ticket_goes_along_through_the_login(self, client, environment):
        key_id = await shared_by_link(environment)
        link = ticket_for(environment, key_id)
        response = await consent_page_with(client, [link], "/aurora/site/docs/?key=abc&x=1")
        assert response.status_code == 302
        assert response.headers["cache-control"] == "no-store"
        location = urlsplit(response.headers["location"])
        assert location.path == "/-/login"
        (back,) = parse_qs(location.query)["returnTo"]
        assert back == f"{PATH_FEEDBACK_ACCOUNT}?returnTo={quote('/aurora/site/docs/?x=1', safe='')}&link={link}"

    async def test_beside_a_session_that_sees_the_page_anyway_it_does_no_harm(self, client, environment):
        """A site whose base lets every signed-in visitor in, links beside it."""
        key_id = await shared_by_link(environment)
        await set_site(environment, access_base=AccessBase.SSO)
        with_anchor(client, environment)
        page = await consent_page_with(client, [ticket_for(environment, key_id)])
        assert page.status_code == 200
        nonce = parse_consent_nonce(SECRET, nonce_of(page), now=int(time.time()))
        assert nonce is not None
        assert nonce.key_id == key_id


class TestASecretLinkOnTheWayToTheConsentPageRefuses:
    """Each the neutral 404 byte for byte, with a session and, where the
    ticket itself does not hold, before any sign-in."""

    @pytest.mark.parametrize(
        "make",
        [
            pytest.param(lambda environment, key_id: "", id="empty"),
            pytest.param(lambda environment, key_id: "nep", id="garbage"),
            pytest.param(
                lambda environment, key_id: ticket_for(
                    environment, key_id, now=int(time.time()) - LINK_LIFETIME_S - 1
                ),
                id="expired",
            ),
            pytest.param(
                lambda environment, key_id: mint_link_ticket(
                    "ander-geheim-van-minstens-32-bytes!!",
                    key_id=key_id,
                    site_id=environment.world.site_id,
                    now=int(time.time()),
                ),
                id="another-secret",
            ),
            pytest.param(
                lambda environment, key_id: page_token(environment.app, environment.world.site_id, key_id=key_id),
                id="a-page-token",
            ),
            pytest.param(
                lambda environment, key_id: mint_consent_nonce(
                    SECRET,
                    session_id="s" * 43,
                    site_id=environment.world.site_id,
                    return_to=ROOT,
                    now=int(time.time()),
                    key_id=key_id,
                ),
                id="a-consent-nonce",
            ),
        ],
    )
    async def test_a_ticket_that_does_not_open(self, client, environment, make):
        """Without a session this answer comes before the login: nobody
        signs in, and so leaves a trace, for a way that leads nowhere."""
        key_id = await shared_by_link(environment)
        link = make(environment, key_id)
        assert is_neutral_404(await consent_page_with(client, [link]))
        with_anchor(client, environment)
        assert is_neutral_404(await consent_page_with(client, [link]))

    async def test_two_tickets(self, client, environment):
        key_id = await shared_by_link(environment)
        link = ticket_for(environment, key_id)
        assert is_neutral_404(await consent_page_with(client, [link, link]))
        with_anchor(client, environment)
        assert is_neutral_404(await consent_page_with(client, [link, link]))

    async def test_a_ticket_of_another_site(self, client, environment):
        """Sealed for the other site, even with this site's link in it: the
        ticket has to belong to the page it is used for. The ticket itself
        holds, so it rides through the sign-in like any other, and the site
        is compared only once the gate decides for the signed-in visitor."""
        key_id = await shared_by_link(environment)
        other = ticket_for(environment, key_id, site_id=environment.world.other_site_id)
        before_sign_in = await consent_page_with(client, [other])
        assert before_sign_in.status_code == 302
        assert urlsplit(before_sign_in.headers["location"]).path == PATH_CONTENT_LOGIN
        with_anchor(client, environment)
        assert is_neutral_404(await consent_page_with(client, [other]))

    async def test_a_ticket_with_another_sites_link(self, client, environment):
        await shared_by_link(environment)
        async with environment.factory() as db:
            foreign, _ = await keys.create_key(db, environment.world.other_site_id, "elders")
            await db.commit()
        with_anchor(client, environment)
        assert is_neutral_404(await consent_page_with(client, [ticket_for(environment, str(foreign.id))]))

    @pytest.mark.parametrize("ground", ["revoked", "expired"])
    async def test_a_link_that_no_longer_holds(self, client, environment, ground):
        """The gate checks the link again, as it does the key cookie."""
        key_id = await shared_by_link(environment)
        with_anchor(client, environment)
        link = ticket_for(environment, key_id)
        async with environment.factory() as db:
            if ground == "revoked":
                assert await keys.revoke(db, uuid.UUID(key_id))
            else:
                await db.execute(
                    update(AccessKey)
                    .where(AccessKey.id == uuid.UUID(key_id))
                    .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
                )
            await db.commit()
        assert is_neutral_404(await consent_page_with(client, [link]))
        assert is_neutral_404(await consent_page_with(client, [link], PREVIEW_ROOT))

    async def test_a_link_where_the_site_takes_no_links(self, client, environment):
        key_id = await shared_by_link(environment)
        await set_site(environment, access_keys=False)
        with_anchor(client, environment)
        assert is_neutral_404(await consent_page_with(client, [ticket_for(environment, key_id)]))


# --- Giving consent ---------------------------------------------------------------


async def give(
    client: httpx.AsyncClient, nonce: object, headers: dict[str, str] | None = None, *, agree: object = "1"
) -> httpx.Response:
    """The consent form as the page posts it: the nonce, and the box ticked
    unless `agree` says otherwise (None leaves the field out)."""
    data = {} if nonce is None else {"nonce": nonce}
    if agree is not None:
        data["agree"] = agree
    return await client.post(PATH_FEEDBACK_ACCOUNT, data=data, headers=headers or FORM)


def fresh_nonce(
    environment: Environment, session_id: str, *, site_id=None, return_to: str = ROOT, now=None, key_id=None
) -> str:
    return mint_consent_nonce(
        SECRET,
        session_id=session_id,
        site_id=environment.world.site_id if site_id is None else site_id,
        return_to=return_to,
        now=int(time.time()) if now is None else now,
        key_id=key_id,
    )


class TestGivingConsentRefuses:
    async def test_without_a_top_level_navigation(self, client, environment):
        session = consented_session(environment.app)
        nonce = fresh_nonce(environment, session.id)
        for headers in ({"Content-Type": "application/x-www-form-urlencoded"}, {**FORM, "Sec-Fetch-Dest": "empty"}):
            assert is_neutral_404(await give(client, nonce, headers))
        assert environment.store.get_session(session.id).feedback_sites == frozenset()

    @pytest.mark.parametrize(
        "content_type", ["multipart/form-data; boundary=x", "application/json", "text/plain", ""]
    )
    async def test_another_kind_of_body(self, client, environment, content_type):
        session = consented_session(environment.app)
        response = await client.post(
            PATH_FEEDBACK_ACCOUNT,
            content=f"nonce={fresh_nonce(environment, session.id)}&agree=1",
            headers={**DOCUMENT, "Content-Type": content_type},
        )
        assert is_neutral_404(response)

    async def test_a_body_too_large(self, client, environment):
        session = consented_session(environment.app)
        nonce = fresh_nonce(environment, session.id)
        declared = await client.post(
            PATH_FEEDBACK_ACCOUNT,
            content=f"nonce={nonce}&agree=1",
            headers={**FORM, "Content-Length": str(MAX_FORM_BYTES + 1)},
        )
        assert is_neutral_404(declared)

        async def chunks():
            yield f"nonce={nonce}&agree=1&x=".encode()
            yield b"a" * MAX_FORM_BYTES

        assert is_neutral_404(await client.post(PATH_FEEDBACK_ACCOUNT, content=chunks(), headers=FORM))

    @pytest.mark.parametrize("nonce", [None, "", "nep", "AAAA"], ids=["missing", "empty", "garbage", "too-short"])
    async def test_no_nonce_that_opens(self, client, nonce):
        assert is_neutral_404(await give(client, nonce))

    async def test_two_nonces(self, client, environment):
        session = consented_session(environment.app)
        nonce = fresh_nonce(environment, session.id)
        response = await client.post(
            PATH_FEEDBACK_ACCOUNT, content=f"nonce={nonce}&nonce={nonce}&agree=1", headers=FORM
        )
        assert is_neutral_404(response)

    @pytest.mark.parametrize(
        "agree",
        [None, "", "0", "on", "1 ", "true", ["1", "1"]],
        ids=["missing", "empty", "zero", "on", "trailing-space", "true", "twice"],
    )
    async def test_without_the_box_ticked_exactly_once(self, client, environment, agree):
        """A press on the button alone, wherever it came from, consents to
        nothing: the form has to carry the page's own tick, `agree=1`, once.
        Anything else is the neutral 404 and stores nothing."""
        session = consented_session(environment.app)
        assert is_neutral_404(await give(client, fresh_nonce(environment, session.id), agree=agree))
        assert environment.store.get_session(session.id).feedback_sites == frozenset()

    async def test_a_page_token_is_no_nonce(self, client, environment):
        session = consented_session(environment.app)
        token = page_token(environment.app, environment.world.site_id, session_id=session.id)
        assert is_neutral_404(await give(client, token))

    async def test_an_expired_nonce(self, client, environment):
        session = consented_session(environment.app)
        nonce = fresh_nonce(environment, session.id, now=int(time.time()) - CONSENT_LIFETIME_S - 1)
        assert is_neutral_404(await give(client, nonce))

    async def test_a_session_that_is_gone_or_no_content_session(self, client, environment):
        gone = consented_session(environment.app)
        environment.store.delete_session(gone.id)
        admin = environment.store.create_session(sub="kijker", email=None, email_verified=False, acr="urn:acr:hoog")
        for session_id in (gone.id, admin.id):
            assert is_neutral_404(await give(client, fresh_nonce(environment, session_id)))

    async def test_a_session_the_idp_ended_meanwhile(self, client, environment, monkeypatch):
        session = consented_session(environment.app)

        async def ended(request, found):
            return None

        monkeypatch.setattr(feedback_consent, "revalidate_session", ended)
        assert is_neutral_404(await give(client, fresh_nonce(environment, session.id)))

    @pytest.mark.parametrize(
        "change",
        [{"feedback_enabled": False}, {"feedback_live": False}, {"access_base": AccessBase.SITE_TEAM}],
        ids=["feedback-off-since", "previews-only-since", "access-narrowed-since"],
    )
    async def test_what_changed_since_the_page_was_shown(self, client, environment, change):
        session = consented_session(environment.app)
        nonce = fresh_nonce(environment, session.id)
        await set_site(environment, **change)
        assert is_neutral_404(await give(client, nonce))
        assert environment.store.get_session(session.id).feedback_sites == frozenset()

    async def test_a_nonce_whose_site_is_not_the_way_backs(self, client, environment):
        session = consented_session(environment.app)
        nonce = fresh_nonce(environment, session.id, site_id=environment.world.other_site_id)
        assert is_neutral_404(await give(client, nonce))

    async def test_a_nonce_for_a_page_without_the_widget(self, client, environment):
        session = consented_session(environment.app)
        nonce = fresh_nonce(environment, session.id, return_to="/aurora/site/_version/x/")
        assert is_neutral_404(await give(client, nonce))

    async def test_a_nonce_without_the_link_on_a_site_shared_by_link(self, client, environment):
        """The session alone does not let this visitor see the page."""
        await shared_by_link(environment)
        session = consented_session(environment.app)
        assert is_neutral_404(await give(client, fresh_nonce(environment, session.id)))
        assert environment.store.get_session(session.id).feedback_sites == frozenset()

    async def test_a_link_revoked_since_the_page_was_shown(self, client, environment):
        key_id = await shared_by_link(environment)
        session = consented_session(environment.app)
        nonce = fresh_nonce(environment, session.id, key_id=key_id)
        async with environment.factory() as db:
            assert await keys.revoke(db, uuid.UUID(key_id))
            await db.commit()
        assert is_neutral_404(await give(client, nonce))
        assert environment.store.get_session(session.id).feedback_sites == frozenset()

    async def test_a_session_that_holds_as_much_consent_as_it_may(self, client, environment):
        session = consented_session(environment.app, *(uuid.uuid4() for _ in range(32)))
        assert is_neutral_404(await give(client, fresh_nonce(environment, session.id)))
        assert str(environment.world.site_id) not in environment.store.get_session(session.id).feedback_sites


class TestGivingConsent:
    async def test_the_consent_is_stored_and_the_way_back_runs_through_the_login(self, client, environment):
        world = environment.world
        session = with_anchor(client, environment)
        page = await consent_page(client, "/aurora/site/docs/?x=1")
        # The form posts from an opaque origin: no cookie comes along.
        client.cookies.clear()
        response = await give(client, nonce_of(page))
        assert response.status_code == 303
        location = urlsplit(response.headers["location"])
        assert location.path == PATH_CONTENT_LOGIN
        assert parse_qs(location.query) == {"returnTo": ["/aurora/site/docs/?x=1"]}
        assert response.headers["location"] == through_the_login("/aurora/site/docs/?x=1")
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        stored = environment.store.get_session(session.id)
        assert str(world.site_id) in stored.feedback_sites
        # The login records the site when it hands out the cookie.
        assert stored.content_sites == frozenset()

    async def test_consent_on_a_preview_goes_back_to_the_preview(self, client, environment):
        session = consented_session(environment.app)
        response = await give(client, fresh_nonce(environment, session.id, return_to=f"{PREVIEW_ROOT}docs/"))
        assert response.status_code == 303
        assert response.headers["location"] == through_the_login(f"{PREVIEW_ROOT}docs/")
        assert "set-cookie" not in response.headers

    @pytest.mark.parametrize(
        "return_to",
        ["/aurora/site/docs/?x=1", f"{PREVIEW_ROOT}docs/", '/aurora/site/é ü/?q=a b&r="#deel'],
        ids=["live", "preview", "non-ascii-spaces-and-quotes"],
    )
    async def test_back_on_the_page_with_the_session_that_consented(self, client, environment, return_to):
        """The navigation after the POST is a GET, which takes the Lax anchor
        along again: the login's shortcut then sets the site cookie of the
        session that consented, for the whole site, and sends the visitor on
        to the page, the way back intact through both hops."""
        with_the_login(environment)
        session = with_anchor(client, environment)
        page = await consent_page(client, return_to)
        async with client_for(environment.app) as opaque_form:
            response = await give(opaque_form, nonce_of(page))
        assert response.headers["location"] == through_the_login(return_to)
        back = await client.get(response.headers["location"], headers=DOCUMENT)
        assert back.status_code == 303
        assert unquote(back.headers["location"]) == return_to
        assert back.headers["cache-control"] == "no-store"
        cookie = back.headers["set-cookie"]
        assert check_signature(SECRET, back.cookies[CONTENT_SESSION_COOKIE]) == session.id
        attributes = {part.strip().lower() for part in cookie.split(";")[1:]}
        assert attributes == {"httponly", "path=/aurora/site/", "samesite=none", "secure"}
        stored = environment.store.get_session(session.id)
        assert str(environment.world.site_id) in stored.feedback_sites
        assert stored.content_sites == {"/aurora/site/"}

    async def test_a_nonce_posted_from_another_site_sets_no_cookie(self, client, environment):
        """Somebody's own nonce, submitted by a page elsewhere in another
        visitor's browser, which sends no cookie along with it: the consent is
        stored for the session the nonce names, as its holder could have done
        anyway, and the browser that posted it gets no cookie at all."""
        other = consented_session(environment.app, sub="ander", name="Ander Persoon")
        response = await give(client, fresh_nonce(environment, other.id), CROSS_SITE_FORM)
        assert response.status_code == 303
        assert response.headers["location"] == through_the_login(ROOT)
        assert "set-cookie" not in response.headers
        assert list(client.cookies.jar) == []
        assert environment.store.get_session(other.id).feedback_sites == {str(environment.world.site_id)}

    async def test_and_the_way_back_keeps_the_browser_in_its_own_session(self, client, environment):
        """The visitor whose browser posted it is signed in and has a site
        cookie of their own. After the login they hold their own session and
        nothing else, and that session never consented. What their page
        token then says is in test_feedback_inject.py
        (`TestTheWholeContentHost`)."""
        with_the_login(environment)
        other = consented_session(environment.app, sub="ander", name="Ander Persoon")
        own = with_anchor(client, environment)
        client.cookies.set(CONTENT_SESSION_COOKIE, sign(SECRET, own.id), domain="toon.example", path=ROOT)
        # A cross-site POST: the Lax anchor stays behind, and the site
        # cookie's path does not match.
        async with client_for(environment.app) as cross_site:
            response = await give(cross_site, fresh_nonce(environment, other.id), CROSS_SITE_FORM)
        assert "set-cookie" not in response.headers
        # Whatever that answer set would land in this visitor's browser.
        client.cookies.extract_cookies(response)
        assert site_cookies(client) == [(ROOT, own.id)]
        back = await client.get(response.headers["location"], headers=DOCUMENT)
        assert back.status_code == 303
        assert back.headers["location"] == ROOT
        assert site_cookies(client) == [(ROOT, own.id)]
        assert environment.store.get_session(own.id).feedback_sites == frozenset()

    @pytest.mark.parametrize("return_to", [ROOT, f"{PREVIEW_ROOT}docs/"], ids=["live", "preview"])
    async def test_with_the_link_of_its_ticket_on_a_site_shared_by_link(self, client, environment, return_to):
        """The page opened with a ticket, and its nonce carries the link to
        the POST, where the gate decides with it again."""
        key_id = await shared_by_link(environment)
        session = with_anchor(client, environment)
        page = await consent_page_with(client, [ticket_for(environment, key_id)], return_to)
        client.cookies.clear()
        response = await give(client, nonce_of(page))
        assert response.status_code == 303
        assert response.headers["location"] == through_the_login(return_to)
        assert "set-cookie" not in response.headers
        assert environment.store.get_session(session.id).feedback_sites == {str(environment.world.site_id)}

    async def test_consent_twice_is_consent_once(self, client, environment):
        session = consented_session(environment.app, environment.world.site_id)
        assert (await give(client, fresh_nonce(environment, session.id))).status_code == 303
        assert environment.store.get_session(session.id).feedback_sites == {str(environment.world.site_id)}

    async def test_after_consent_a_page_token_carries_the_session_into_the_api(self, client, environment):
        session = consented_session(environment.app)
        assert (await give(client, fresh_nonce(environment, session.id))).status_code == 303
        token = page_token(environment.app, environment.world.site_id, session_id=session.id)
        response = await client.get(ITEMS, headers={"Authorization": f"Toon-Feedback {token}"})
        assert response.status_code == 200
        created = await create(client, token, None)
        assert created.status_code == 201


# --- Ending consent -----------------------------------------------------------------


async def stop(client: httpx.AsyncClient, return_to: str = ROOT, **headers: str) -> httpx.Response:
    return await client.get(PATH_FEEDBACK_ACCOUNT_STOP, params={"returnTo": return_to}, headers={**DOCUMENT, **headers})


class TestEndingConsent:
    async def test_without_a_top_level_navigation_nothing_ends(self, client, environment):
        session = consented_session(environment.app, environment.world.site_id)
        client.cookies.set(CONTENT_ANCHOR_COOKIE, sign(SECRET, session.id), domain="toon.example", path="/-/")
        response = await client.get(PATH_FEEDBACK_ACCOUNT_STOP, params={"returnTo": ROOT})
        assert is_neutral_404(response)
        assert environment.store.get_session(session.id).feedback_sites == {str(environment.world.site_id)}

    @pytest.mark.parametrize("value", BAD_RETURNS, ids=BAD_RETURN_IDS)
    async def test_a_way_back_that_does_not_hold(self, client, value):
        assert is_neutral_404(await stop(client, value))

    async def test_the_anchor_sessions_consent_for_this_site_ends(self, client, environment):
        world = environment.world
        session = consented_session(environment.app, world.site_id, world.other_site_id)
        client.cookies.set(CONTENT_ANCHOR_COOKIE, sign(SECRET, session.id), domain="toon.example", path="/-/")
        response = await stop(client, "/aurora/site/docs/?key=abc&x=1")
        assert response.status_code == 303
        assert response.headers["location"] == "/aurora/site/docs/?x=1"
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        assert environment.store.get_session(session.id).feedback_sites == {str(world.other_site_id)}

    @pytest.mark.parametrize("planted_path", ["/", "/-/"], ids=["site-cookie-at-the-root", "site-cookie-at-the-anchor"])
    async def test_a_site_cookie_that_reaches_this_path_ends_it_beside_the_anchor(
        self, client, environment, planted_path
    ):
        """Toon sets a site cookie at its site's path alone, so one that
        reaches `/-/` was set from elsewhere. At `/` it reaches the site's
        pages too, where it may be the only site cookie and so name the
        session the page token carries. Neither it nor the visitor's own
        anchor is taken for the one: both sessions lose the consent."""
        world = environment.world
        planted = consented_session(environment.app, world.site_id, world.other_site_id, sub="ander")
        own = consented_session(environment.app, world.site_id)
        client.cookies.set(CONTENT_SESSION_COOKIE, sign(SECRET, planted.id), domain="toon.example", path=planted_path)
        client.cookies.set(CONTENT_ANCHOR_COOKIE, sign(SECRET, own.id), domain="toon.example", path="/-/")
        assert (await stop(client)).status_code == 303
        assert environment.store.get_session(planted.id).feedback_sites == {str(world.other_site_id)}
        assert environment.store.get_session(own.id).feedback_sites == frozenset()

    @pytest.mark.parametrize("planted_path", ["/", "/-/feedback/"], ids=["read-last", "read-first"])
    async def test_an_anchor_beside_the_visitors_own_ends_it_in_both(self, client, environment, planted_path):
        """Whichever of the two the request carries last, the visitor's own
        consent ends: a shadowing anchor cannot keep the visitor in it."""
        world = environment.world
        own = consented_session(environment.app, world.site_id)
        planted = consented_session(environment.app, world.site_id, sub="ander")
        client.cookies.set(CONTENT_ANCHOR_COOKIE, sign(SECRET, own.id), domain="toon.example", path="/-/")
        client.cookies.set(CONTENT_ANCHOR_COOKIE, sign(SECRET, planted.id), domain="toon.example", path=planted_path)
        assert (await stop(client)).status_code == 303
        assert environment.store.get_session(own.id).feedback_sites == frozenset()
        assert environment.store.get_session(planted.id).feedback_sites == frozenset()

    async def test_every_value_in_one_cookie_header_counts(self, client, environment):
        """Two anchors and two site cookies in a single header, as HTTP/1.1
        carries them, one of them quoted: `request.cookies` reads a quoted
        value without its quotes, so that copy names its session as well."""
        world = environment.world
        held = [consented_session(environment.app, world.site_id, sub=f"kijker-{n}") for n in range(4)]
        values = [sign(SECRET, held[0].id), sign(SECRET, held[1].id), f'"{sign(SECRET, held[2].id)}"']
        cookie = "; ".join(
            [
                f"{CONTENT_ANCHOR_COOKIE}={values[0]}",
                f"{CONTENT_SESSION_COOKIE}={values[1]}",
                f"{CONTENT_ANCHOR_COOKIE}={values[2]}",
                f"{CONTENT_SESSION_COOKIE}={sign(SECRET, held[3].id)}",
            ]
        )
        response = await client.get(
            PATH_FEEDBACK_ACCOUNT_STOP, params={"returnTo": ROOT}, headers={**DOCUMENT, "Cookie": cookie}
        )
        assert response.status_code == 303
        assert [environment.store.get_session(session.id).feedback_sites for session in held] == [frozenset()] * 4

    async def test_each_session_is_asked_once(self, client, environment, monkeypatch):
        """The site cookie and the anchor of one browser name the same
        session, and a value can come twice: one withdrawal per session."""
        session = consented_session(environment.app, environment.world.site_id)
        value = sign(SECRET, session.id)
        calls: list[tuple[str, str]] = []
        withdraw = environment.store.withdraw_feedback

        def counted(session_id: str, site_id: str):
            calls.append((session_id, site_id))
            return withdraw(session_id, site_id)

        monkeypatch.setattr(environment.store, "withdraw_feedback", counted)
        cookie = f"{CONTENT_SESSION_COOKIE}={value}; {CONTENT_ANCHOR_COOKIE}={value}; {CONTENT_ANCHOR_COOKIE}={value}"
        response = await client.get(
            PATH_FEEDBACK_ACCOUNT_STOP, params={"returnTo": ROOT}, headers={**DOCUMENT, "Cookie": cookie}
        )
        assert response.status_code == 303
        assert calls == [(session.id, str(environment.world.site_id))]

    async def test_only_a_live_content_session_is_looked_up_at_all(self, client, environment, monkeypatch):
        """A forged value, one signed for a session that is gone, or one that
        names a management session: nothing to end, so not even the site is
        looked up, and the answer is the one every stop gets."""
        gone = consented_session(environment.app, environment.world.site_id)
        environment.store.delete_session(gone.id)
        admin = environment.store.create_session(sub="lid", email=None, email_verified=False, acr="urn:acr:hoog")

        async def no_lookup(request, target):
            raise AssertionError("the site was looked up without a session to end")

        monkeypatch.setattr(feedback_consent, "_site_at", no_lookup)
        cookie = "; ".join(
            [
                f"{CONTENT_ANCHOR_COOKIE}={sign(SECRET, gone.id)}",
                f"{CONTENT_ANCHOR_COOKIE}={sign(SECRET, admin.id)}",
                f"{CONTENT_SESSION_COOKIE}={sign('ander-geheim-van-minstens-32-bytes!', gone.id)}",
                f"{CONTENT_SESSION_COOKIE}=",
            ]
        )
        response = await client.get(
            PATH_FEEDBACK_ACCOUNT_STOP, params={"returnTo": ROOT}, headers={**DOCUMENT, "Cookie": cookie}
        )
        assert (response.status_code, response.headers["location"]) == (303, ROOT)
        assert header_list(response) == header_list(await stop(client))

    async def test_the_same_answer_with_or_without_anything_to_end(self, client, environment):
        nobody = await stop(client, "/aurora/onbekend/")
        session = consented_session(environment.app, environment.world.site_id)
        client.cookies.set(CONTENT_ANCHOR_COOKIE, sign(SECRET, session.id), domain="toon.example", path="/-/")
        somebody = await stop(client, "/aurora/onbekend/")
        assert (nobody.status_code, nobody.headers["location"]) == (somebody.status_code, somebody.headers["location"])
        assert header_list(nobody) == header_list(somebody)

    async def test_a_page_opened_before_a_rename_still_ends_it(self, client, environment):
        world = environment.world
        session = consented_session(environment.app, world.site_id)
        client.cookies.set(CONTENT_ANCHOR_COOKIE, sign(SECRET, session.id), domain="toon.example", path="/-/")
        async with environment.factory() as db:
            (await db.scalar(select(Site).where(Site.id == world.site_id))).slug = "nieuw"
            await db.commit()
        assert (await stop(client, "/aurora/site/")).status_code == 303
        assert environment.store.get_session(session.id).feedback_sites == frozenset()

    async def test_after_a_group_rename_too(self, client, environment):
        world = environment.world
        session = consented_session(environment.app, world.site_id)
        client.cookies.set(CONTENT_ANCHOR_COOKIE, sign(SECRET, session.id), domain="toon.example", path="/-/")
        async with environment.factory() as db:
            (await db.scalar(select(Group).where(Group.id == world.group_id))).slug = "borealis"
            await db.commit()
        assert (await stop(client, "/aurora/onbekend/")).status_code == 303
        assert environment.store.get_session(session.id).feedback_sites == {str(world.site_id)}
        assert (await stop(client, "/aurora/site/")).status_code == 303
        assert environment.store.get_session(session.id).feedback_sites == frozenset()


# --- Hiding and showing the button ------------------------------------------------


async def hide(client: httpx.AsyncClient, data: dict[str, object] | str, headers: dict[str, str] | None = None):
    if isinstance(data, str):
        return await client.post(PATH_FEEDBACK_HIDE, content=data, headers=headers or FORM)
    return await client.post(PATH_FEEDBACK_HIDE, data=data, headers=headers or FORM)


class TestHidingRefuses:
    async def test_without_a_top_level_navigation(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        for headers in ({"Content-Type": "application/x-www-form-urlencoded"}, {**FORM, "Sec-Fetch-Dest": "empty"}):
            assert is_neutral_404(await hide(client, {"token": token, "returnTo": ROOT}, headers))

    async def test_another_kind_of_body_or_too_large(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        as_json = await client.post(
            PATH_FEEDBACK_HIDE,
            json={"token": token, "returnTo": ROOT},
            headers=DOCUMENT,
        )
        assert is_neutral_404(as_json)
        large = await hide(client, {"token": token, "returnTo": ROOT, "x": "a" * MAX_FORM_BYTES})
        assert is_neutral_404(large)

    async def test_no_or_two_tokens(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        assert is_neutral_404(await hide(client, {"returnTo": ROOT}))
        assert is_neutral_404(await hide(client, f"token={token}&token={token}&returnTo=%2Faurora%2Fsite%2F"))

    @pytest.mark.parametrize("value", BAD_RETURNS, ids=BAD_RETURN_IDS)
    async def test_a_way_back_that_does_not_hold(self, client, environment, value):
        token = page_token(environment.app, environment.world.site_id)
        assert is_neutral_404(await hide(client, {"token": token, "returnTo": value}))

    async def test_no_way_back(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        assert is_neutral_404(await hide(client, {"token": token}))

    async def test_a_token_that_does_not_open(self, client, environment):
        expired = page_token(environment.app, environment.world.site_id, now=int(time.time()) - 13 * 3600)
        for token in ("nep", expired):
            assert is_neutral_404(await hide(client, {"token": token, "returnTo": ROOT}))

    async def test_a_token_of_another_site(self, client, environment):
        """The way back has to lie on the site the page token belongs to,
        which is where the cookie goes: a token of one site hides nothing on
        another."""
        token = page_token(environment.app, environment.world.other_site_id)
        assert is_neutral_404(await hide(client, {"token": token, "returnTo": ROOT}))

    async def test_a_token_of_a_site_that_is_gone(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        async with environment.factory() as db:
            await db.execute(delete(Site).where(Site.id == environment.world.site_id))
            await db.commit()
        assert is_neutral_404(await hide(client, {"token": token, "returnTo": ROOT}))


class TestAFormTheClientGaveUpOn:
    @pytest.mark.parametrize("path", [PATH_FEEDBACK_ACCOUNT, PATH_FEEDBACK_HIDE], ids=["consent", "hide"])
    @pytest.mark.parametrize("received", [[], [b"nonce=abc&token="]], ids=["before-the-body", "midway"])
    async def test_is_the_neutral_404_and_no_traceback(self, environment, caplog, path, received):
        """A POST cut off after its headers, as anyone can send one at the
        rate limit. Nobody reads the answer: what matters is that the route
        ends as every refusal does, not in a 500 whose traceback lands in
        the log for every abandoned form."""
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "server": ("toon.example", 443),
            "client": ("127.0.0.1", 123),
            "headers": [
                (b"host", b"toon.example"),
                (b"sec-fetch-dest", b"document"),
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"content-length", b"100"),
            ],
        }
        cut_off = [{"type": "http.request", "body": chunk, "more_body": True} for chunk in received]
        incoming = iter([*cut_off, {"type": "http.disconnect"}])
        sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return next(incoming)

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        await environment.app(scope, receive, send)

        reference = neutral_404_response()
        assert sent[0]["status"] == 404
        assert sorted(sent[0]["headers"]) == sorted(reference.raw_headers)
        assert b"".join(message.get("body", b"") for message in sent[1:]) == NEUTRAL_404_BODY
        assert [record for record in caplog.records if record.levelno >= logging.WARNING] == []


class TestHidingAndShowing:
    async def test_hiding_sets_a_cookie_for_the_site_until_the_browser_closes(self, client, environment):
        """No expiry on the cookie, so it goes with the browser. The mark in
        it names the site and stops counting after twelve hours."""
        token = page_token(environment.app, environment.world.site_id, ref="pr-1")
        before = int(time.time())
        response = await hide(client, {"token": token, "returnTo": f"{PREVIEW_ROOT}docs/?key=abc&x=1"})
        assert response.status_code == 303
        assert response.headers["location"] == f"{PREVIEW_ROOT}docs/?x=1"
        assert response.headers["cache-control"] == "no-store"
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"{FEEDBACK_HIDE_COOKIE}=")
        attributes = {part.strip().lower() for part in cookie.split(";")[1:]}
        assert attributes == {"httponly", "path=/aurora/site/", "samesite=lax", "secure"}
        mark = parse_hide_mark(SECRET, response.cookies[FEEDBACK_HIDE_COOKIE], now=int(time.time()))
        assert mark is not None
        assert mark.site_id == environment.world.site_id
        assert before + HIDE_LIFETIME_S <= mark.expires_at <= int(time.time()) + HIDE_LIFETIME_S

    async def test_any_page_that_holds_a_token_of_the_site_can_hide_it(self, client, environment):
        """Accepted, and pinned here so nobody takes it for closed. A public
        site's page hands a token to anyone, and a form another site submits
        looks like the widget's own: from a shielded page that is a
        cross-site navigation with `Origin: null` too. It costs the visitor
        the button on this one site until the browser closes, twelve hours at
        most, and the show link brings it back."""
        token = page_token(environment.app, environment.world.site_id)
        for headers in (CROSS_SITE_FORM, {**CROSS_SITE_FORM, "Origin": "null"}):
            response = await hide(client, {"token": token, "returnTo": ROOT}, headers)
            assert response.status_code == 303
            assert response.headers["set-cookie"].startswith(f"{FEEDBACK_HIDE_COOKIE}=")

    @pytest.mark.parametrize("dest", [None, "empty"])
    async def test_showing_needs_a_top_level_navigation(self, client, dest):
        headers = {} if dest is None else {"Sec-Fetch-Dest": dest}
        response = await client.get(PATH_FEEDBACK_SHOW, params={"returnTo": ROOT}, headers=headers)
        assert is_neutral_404(response)

    @pytest.mark.parametrize("value", BAD_RETURNS, ids=BAD_RETURN_IDS)
    async def test_showing_needs_a_way_back_that_holds(self, client, value):
        response = await client.get(PATH_FEEDBACK_SHOW, params={"returnTo": value}, headers=DOCUMENT)
        assert is_neutral_404(response)

    async def test_showing_clears_the_cookie_of_the_site_and_at_the_root(self, client):
        """At the site's path, where hiding sets it, and at `/`, where the
        script of a page without the shielding can write one: a browser
        removes a cookie only at the path it was set for."""
        response = await client.get(
            PATH_FEEDBACK_SHOW, params={"returnTo": "/aurora/site/docs/?key=abc&x=1"}, headers=DOCUMENT
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/aurora/site/docs/?x=1"
        assert response.headers["cache-control"] == "no-store"
        cleared = response.headers.get_list("set-cookie")
        assert len(cleared) == 2
        paths = []
        for cookie in cleared:
            assert cookie.startswith(f'{FEEDBACK_HIDE_COOKIE}="";')
            attributes = {part.strip().lower() for part in cookie.split(";")[1:]}
            assert "max-age=0" in attributes
            assert {"httponly", "samesite=lax", "secure"} <= attributes
            paths += [part for part in attributes if part.startswith("path=")]
        assert sorted(paths) == ["path=/", "path=/aurora/site/"]


class TestSessionsStayApart:
    async def test_an_admin_session_never_opens_the_consent_page(self, environment):
        """Only a content session counts on the content host."""
        store: SessionStore = environment.app.state.session_store
        admin = store.create_session(sub="kijker", email=None, email_verified=False, acr="urn:acr:hoog")
        assert admin.kind is SessionKind.ADMIN
        async with client_for(environment.app) as client:
            client.cookies.set(CONTENT_ANCHOR_COOKIE, sign(SECRET, admin.id), domain="toon.example", path="/-/")
            response = await consent_page(client)
        assert response.status_code == 302


class TestNoAuditTrail:
    async def test_consent_its_end_and_the_button_write_no_audit_row(self, client, environment):
        """The login itself is audited where it happens; consenting, ending
        it and hiding or showing the button are not."""
        recorder = install_audit_recorder(environment.app)
        with_anchor(client, environment)
        page = await consent_page(client)
        assert (await give(client, nonce_of(page))).status_code == 303
        assert (await stop(client)).status_code == 303
        token = page_token(environment.app, environment.world.site_id)
        assert (await hide(client, {"token": token, "returnTo": ROOT})).status_code == 303
        shown = await client.get(PATH_FEEDBACK_SHOW, params={"returnTo": ROOT}, headers=DOCUMENT)
        assert shown.status_code == 303
        assert is_neutral_404(await give(client, "nep"))
        assert recorder.records == []

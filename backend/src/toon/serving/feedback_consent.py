"""Toon's own pages around the feedback widget: consent to feedback with an
account, ending that consent, and hiding or showing the button.

A page token carries the visitor's content session for feedback with their
account only after the visitor said yes, on a page Toon renders outside every
site's realm, to giving feedback on this site that way. Without that, a
site's own scripts could have the team see a signed-in visitor's name and
e-mail address by sending an item in their name. So the consent page is
served with the platform policy plus `sandbox allow-forms`: its origin is
opaque, and no site's script can read it, fill it in or press its button,
while frames are refused outright. A site can still send the visitor's tab
here between the two presses of a double-click, so the form takes no press in
the first second after the page paints, and it consents only with its box
ticked: no single press gives consent. The page shows the identity it
attributes to, so a visitor pushed into somebody else's session sees a
foreign name, and a request that carries the anchor cookie twice gets no page
at all. The form it posts carries a sealed nonce bound to the session, the
site and the way back, because a POST from an opaque origin brings no Lax
cookie along; the nonce is the credential, for ten minutes.

The nonce never turns into a cookie. Its POST stores the consent for the
session it names and sends the visitor back through the login, whose
shortcut hands out the site cookie from the one anchor cookie the browser
carries on that navigation, or else sends the visitor to the IdP. A nonce
posted from another page, in another browser, therefore stores a consent for
its own session and plants nothing in the browser that posts it.

A visitor whom the gate let in through a secret link brings that link along
as a sealed link ticket in `link` (serving/feedback_token.py): the key cookie
belongs to the site's path and never reaches this one. The ticket rides
through the sign-in inside the login's way back, has to name the site at
`returnTo`, and the gate then decides for the signed-in visitor with its
link, which it checks again for revocation and expiry. The nonce carries the
link on to the consent itself. A `link` that comes twice, does not open or
has expired is the neutral 404 before any sign-in. A ticket of another site
than the one the gate decides on at `returnTo` is the neutral 404 too, once
the visitor has signed in.

Everything here answers top-level navigations only (`Sec-Fetch-Dest:
document`, which no page script can set), and every refusal is the neutral
404. A way back (`returnTo`) passes the same rule as the login's, must be a
site's path and loses every `key` parameter. Ending the consent and showing
the button again are harmless, so they are GETs, and ending it reaches every
session that a correctly signed site cookie or anchor on the request names.
Hiding the button takes a page token of the very site it hides the button
on, and keeps the button away on that site until the browser closes, twelve
hours at most.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from html import escape
from urllib.parse import parse_qsl, urlencode

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData
from starlette.formparsers import FormParser
from starlette.requests import ClientDisconnect, cookie_parser
from starlette.responses import RedirectResponse, Response

from toon import i18n
from toon.auth import sessions
from toon.auth.revalidation import revalidate_session
from toon.auth.sessions import Session, SessionKind, SessionStore, Visitor
from toon.constants import (
    FEEDBACK_HIDE_COOKIE,
    PATH_CONTENT_LOGIN,
    PATH_FEEDBACK_ACCOUNT,
    SLUG_RE,
)
from toon.models.identity import Group
from toon.models.publication import Site
from toon.serving import addresses
from toon.serving.feedback_access import decide_page, open_feedback
from toon.serving.feedback_token import (
    MAX_RETURN_TO_LENGTH,
    mint_consent_nonce,
    mint_hide_mark,
    parse_consent_nonce,
    parse_link_ticket,
    parse_page_token,
)
from toon.serving.response import NOINDEX, neutral_404_response, platform_csp

# A form with two fields, a sealed token and a path; anything larger is not
# one of Toon's forms.
MAX_FORM_BYTES = 16 * 1024
_FORM_TYPE = "application/x-www-form-urlencoded"

_NO_STORE = {"Cache-Control": "no-store"}

# The form keeps `visibility: hidden` for its first second, so a press that
# lands as the page appears hits nothing. The timing has to stay step-end:
# under any easing the animated visibility is `visible` as soon as the
# animation starts, and the form takes that press after all.
_CONSENT_CSS = """
:root {
  color-scheme: light;
  --toon-ground: #fff;
  --toon-text: #1c2022;
  --toon-brand: #154273;
  --toon-brand-contrast: #fff;
  --toon-line: #767676;
}

@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --toon-ground: #1c2022;
    --toon-text: #e7eaec;
    --toon-brand: #8fb8e0;
    --toon-brand-contrast: #1c2022;
    --toon-line: #9aa4a9;
  }
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: var(--toon-ground);
  color: var(--toon-text);
  font-family: "RO Sans", "Segoe UI", system-ui, sans-serif;
  line-height: 1.6;
  overflow-wrap: break-word;
}

.page {
  max-width: 36rem;
  margin: 0 auto;
  padding: 1.5rem 1rem 2rem;
}

h1 {
  margin: 0 0 0.5rem;
  color: var(--toon-brand);
  font-size: 1.75rem;
  line-height: 1.2;
}

a {
  color: var(--toon-brand);
}

dl {
  margin: 1rem 0;
  border-left: 4px solid var(--toon-brand);
  padding-left: 0.75rem;
}

dt {
  font-weight: 700;
}

dd {
  margin: 0 0 0.5rem;
}

form {
  animation: toon-arm 1s step-end;
}

@keyframes toon-arm {
  from {
    visibility: hidden;
  }
}

.agree {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
}

.agree input {
  flex: none;
  width: 1.25rem;
  height: 1.25rem;
  margin: 0.2rem 0 0;
  accent-color: var(--toon-brand);
}

button {
  margin-top: 0.5rem;
  padding: 0.6em 1.2em;
  border: 0;
  border-radius: 0.25em;
  background: var(--toon-brand);
  color: var(--toon-brand-contrast);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

button:hover {
  text-decoration: underline;
}

:focus-visible {
  outline: 3px solid var(--toon-text);
  outline-offset: 2px;
}

.site-footer {
  margin-top: 3rem;
  border-top: 1px solid var(--toon-line);
  padding-top: 1rem;
}

.site-footer ul {
  margin: 0;
  padding: 0;
  list-style: none;
}
"""

# The platform's own policy with its one stylesheet and its form, and a
# sandbox on top: an opaque origin, so no page on this host can reach into
# it. allow-forms alone, no scripts.
CONSENT_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Vary": "Accept-Language",
    "Content-Security-Policy": f"{platform_csp(style=_CONSENT_CSS, form=True)}; sandbox allow-forms",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": NOINDEX,
}


@dataclass(frozen=True)
class ReturnTarget:
    """A way back: a path on this host inside a site, without any key."""

    path: str
    group: str
    site: str

    @property
    def prefix(self) -> str:
        return sessions.site_prefix(self.group, self.site)


@dataclass(frozen=True)
class PageRoute:
    """The page a path shows: a site's live page, or with a ref its preview."""

    group: str
    site: str
    ref: str | None


def without_key(value: str) -> str:
    """`value` with every `key` parameter taken out of its query: a secret
    link never rides along in a way back. A query without one stays as it
    came."""
    path, _, rest = value.partition("?")
    query, hash_mark, fragment = rest.partition("#")
    pairs = parse_qsl(query, keep_blank_values=True)
    if all(name != "key" for name, _ in pairs):
        return value
    kept = [(name, item) for name, item in pairs if name != "key"]
    cleaned = f"{path}?{urlencode(kept)}" if kept else path
    return f"{cleaned}#{fragment}" if hash_mark else cleaned


def return_target(values: list[str]) -> ReturnTarget | None:
    """The one `returnTo` of a request, when it is a way back these pages may
    send a visitor: the login's rule, a site's path, and no `key`."""
    if len(values) != 1:
        return None
    value = values[0]
    if len(value) >= MAX_RETURN_TO_LENGTH or sessions.valid_return_to(value, "") != value:
        return None
    path = without_key(value)
    site_path = sessions.parse_site_path(path)
    if site_path is None:
        return None
    return ReturnTarget(path, *site_path)


def page_route(path: str) -> PageRoute | None:
    """Live or preview, never a `_version` view, which gets no widget."""
    site_path = sessions.parse_site_path(path)
    if site_path is None:
        return None
    below = path.split("?", 1)[0].split("#", 1)[0].split("/")[3:]
    if below[:1] == ["_version"]:
        return None
    if below[:1] == ["_preview"]:
        ref = below[1] if len(below) > 1 else ""
        return PageRoute(*site_path, ref) if SLUG_RE.fullmatch(ref) else None
    return PageRoute(*site_path, None)


@dataclass(frozen=True)
class _ConsentSite:
    site_id: uuid.UUID
    title: str


async def _consent_site(
    request: Request, route: PageRoute, session: Session, key_id: str | None
) -> _ConsentSite | None:
    """The site whose page this is, when the gate lets this session's visitor
    see it, with the secret link `key_id` beside the session as the key
    cookie would be, and the page collects feedback."""
    visitor = Visitor(sub=session.sub, email=session.email, email_verified=session.email_verified, key_cookie=key_id)
    async with request.app.state.session_factory() as db:
        decision = await decide_page(db, route.group, route.site, route.ref, visitor)
        if open_feedback(decision, preview=route.ref is not None) is None:
            return None
        title = await db.scalar(select(Site.title).where(Site.id == decision.site_id))
    return None if title is None else _ConsentSite(decision.site_id, title)


def consent_page_html(
    locale: str,
    *,
    site_title: str,
    session: Session,
    nonce: str,
    return_to: str,
    admin_origin: str = "",
) -> str:
    """The consent page. It says who the visitor is signed in as, by name or
    else by account id, and names exactly what the site team will see with
    every item: the name and a verified e-mail address, each when there is
    one, the address for the site's editors and admins alone, or else only
    that it comes from someone with an account. The team never sees the
    account id. Consent takes the box ticked and the button pressed.
    `admin_origin` (TOON_BASE_URL) is where the privacy statement and the
    accessibility statement live; without one neither is linked."""
    say = partial(i18n.t, locale)
    if session.name:
        signed_in = say("consent.signed_in").format(name=f"<strong>{escape(session.name)}</strong>")
    else:
        signed_in = say("consent.signed_in_account").format(account=f"<strong>{escape(session.sub)}</strong>")
    rows = [("consent.name", session.name)] if session.name else []
    editors = ""
    if session.email and session.email_verified:
        rows.append(("consent.email", session.email.lower()))
        editors = f"\n<p>{say('consent.email_editors')}</p>"
    site = f"<strong>{escape(site_title)}</strong>"
    if rows:
        identity = "\n".join(f"<dt>{say(label)}</dt>\n<dd>{escape(value)}</dd>" for label, value in rows)
        team = f"<p>{say('consent.intro').format(site=site)}</p>\n<dl>\n{identity}\n</dl>{editors}"
    else:
        team = f"<p>{say('consent.intro_account_only').format(site=site)}</p>"
    origin = escape(admin_origin, quote=True)
    privacy = (
        "<p>"
        + say("consent.privacy").format(link=f'<a href="{origin}/-/privacy">{say("consent.privacy_link")}</a>')
        + "</p>\n"
        if admin_origin
        else ""
    )
    footer = (
        f'<footer class="site-footer">\n<nav aria-label="{say("footer.label")}">\n<ul>\n'
        f'<li><a href="{origin}/-/accessibility">{say("footer.accessibility")}</a></li>\n'
        "</ul>\n</nav>\n</footer>\n"
        if admin_origin
        else ""
    )
    return f"""<!doctype html>
<html lang="{locale}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{say("consent.title")}</title>
<style>{_CONSENT_CSS}</style>
</head>
<body>
<div class="page">
<main>
<h1>{say("consent.title")}</h1>
<p>{signed_in}</p>
{team}
<p>{say("consent.until")}</p>
<p>{say("consent.log")}</p>
{privacy}<form method="post" action="{PATH_FEEDBACK_ACCOUNT}">
<input type="hidden" name="nonce" value="{escape(nonce, quote=True)}">
<p class="agree"><input type="checkbox" id="agree" name="agree" value="1" required>
<label for="agree">{say("consent.agree")}</label></p>
<p><button type="submit">{say("consent.submit")}</button></p>
</form>
<p><a href="{escape(return_to, quote=True)}">{say("consent.back")}</a></p>
</main>
{footer}</div>
</body>
</html>
"""


async def account_page(request: Request) -> Response:
    """GET /-/feedback/account: the consent page for the page in `returnTo`,
    or the login first when there is no content session yet. A link ticket
    in `link` goes along through the login and has the gate decide with its
    secret link. It has to belong to the site at `returnTo`.

    A request that carries the anchor cookie twice gets the neutral 404. The
    session read would be whichever value came last, possibly one set from
    elsewhere, and the login cannot clear a cookie set at another path, so it
    would only send the visitor back here."""
    if not sessions.top_level_navigation(request):
        return neutral_404_response()
    target = return_target(request.query_params.getlist("returnTo"))
    links = request.query_params.getlist("link")
    if target is None or len(links) > 1 or sessions.content_anchor_is_duplicated(request):
        return neutral_404_response()
    settings = request.app.state.settings
    now = int(time.time())
    ticket = parse_link_ticket(settings.session_secret, links[0], now=now) if links else None
    if links and ticket is None:
        return neutral_404_response()
    session = sessions.content_anchor_session_from_request(request)
    if session is None:
        here = f"{PATH_FEEDBACK_ACCOUNT}?{urlencode({'returnTo': target.path, 'link': links}, doseq=True)}"
        return RedirectResponse(
            f"{PATH_CONTENT_LOGIN}?{urlencode({'returnTo': here})}", status_code=302, headers=_NO_STORE
        )
    key_id = None if ticket is None else ticket.key_id
    route = page_route(target.path)
    site = None if route is None else await _consent_site(request, route, session, key_id)
    if site is None or (ticket is not None and ticket.site_id != site.site_id):
        return neutral_404_response()
    try:
        nonce = mint_consent_nonce(
            settings.session_secret,
            session_id=session.id,
            site_id=site.site_id,
            return_to=target.path,
            now=now,
            key_id=key_id,
        )
    except ValueError:
        return neutral_404_response()
    locale = i18n.negotiate(request.headers.get("accept-language"))
    html = consent_page_html(
        locale,
        site_title=site.title,
        session=session,
        nonce=nonce,
        return_to=target.path,
        admin_origin=(settings.base_url or "").rstrip("/"),
    )
    return Response(content=html, media_type="text/html; charset=utf-8", headers=CONSENT_PAGE_HEADERS)


async def give_consent(request: Request) -> Response:
    """POST /-/feedback/account: the consent itself. The nonce is the only
    credential; the session it names must still be there and still see the
    page, with the secret link the nonce carries, and the page must still
    collect feedback on the site the nonce names. The form carries its box
    ticked, exactly once: a press on the button alone consents to nothing.

    It stores the consent and sets no cookie. The way back runs through the
    login, whose shortcut sets the site cookie from the anchor cookie the
    browser sends along on that navigation: in the visitor's own browser the
    session that just consented, in any other the session that browser holds
    already. A browser that sends no anchor, or two, goes to the IdP."""
    if not sessions.top_level_navigation(request):
        return neutral_404_response()
    form = await _small_form(request)
    if form is None or form.getlist("agree") != ["1"]:
        return neutral_404_response()
    values = form.getlist("nonce")
    settings = request.app.state.settings
    nonce = parse_consent_nonce(settings.session_secret, values[0], now=int(time.time())) if len(values) == 1 else None
    route = None if nonce is None else page_route(nonce.return_to)
    if nonce is None or route is None:
        return neutral_404_response()
    store: SessionStore = request.app.state.session_store
    session = store.get_session(nonce.session_id)
    if session is None or session.kind is not SessionKind.CONTENT:
        return neutral_404_response()
    session = await revalidate_session(request, session)
    site = None if session is None else await _consent_site(request, route, session, nonce.key_id)
    if session is None or site is None or site.site_id != nonce.site_id:
        return neutral_404_response()
    if store.consent_feedback(session.id, str(site.site_id)) is None:
        return neutral_404_response()
    back = f"{PATH_CONTENT_LOGIN}?{urlencode({'returnTo': nonce.return_to})}"
    return RedirectResponse(back, status_code=303, headers=_NO_STORE)


async def stop_consent(request: Request) -> Response:
    """GET /-/feedback/account/stop: ends the consent for the site in
    `returnTo` and goes back. The same answer whether there was a session, a
    consent or a site at all.

    It ends it in every content session that a correctly signed site cookie
    or anchor on this request names. A site cookie reaches this path only when
    it was set from elsewhere, and a second anchor can shadow the visitor's
    own, so none of them is taken for the visitor's: withdrawing only takes
    away."""
    if not sessions.top_level_navigation(request):
        return neutral_404_response()
    target = return_target(request.query_params.getlist("returnTo"))
    if target is None:
        return neutral_404_response()
    store: SessionStore = request.app.state.session_store
    named = [
        session.id
        for session in map(store.get_session, _signed_session_ids(request))
        if session is not None and session.kind is SessionKind.CONTENT
    ]
    if named:
        site_id = await _site_at(request, target)
        if site_id is not None:
            for session_id in named:
                store.withdraw_feedback(session_id, str(site_id))
    return RedirectResponse(target.path, status_code=303, headers=_NO_STORE)


def _signed_session_ids(request: Request) -> set[str]:
    """Each id that a correctly signed value of the site cookie or the anchor
    on this request names, once. A value is read as `request.cookies` reads
    one, unquoted: a quoted copy names the same session there."""
    secret = request.app.state.settings.session_secret
    named: set[str] = set()
    for name in (sessions.CONTENT_SESSION_COOKIE, sessions.CONTENT_ANCHOR_COOKIE):
        for value in sessions.raw_cookie_values(request, name):
            session_id = sessions.check_signature(secret, cookie_parser(f"{name}={value}")[name])
            if session_id is not None:
                named.add(session_id)
    return named


async def _site_at(request: Request, target: ReturnTarget) -> uuid.UUID | None:
    """The site at these slugs, or the one they named before a rename that
    still redirects: a page opened before the rename asks to stop there."""
    async with request.app.state.session_factory() as db:
        site_id = await _site_id(db, target.group, target.site)
        if site_id is None:
            moved = await addresses.current_address(db, target.group, target.site, datetime.now(UTC))
            if moved is not None:
                site_id = await _site_id(db, moved.group, moved.site)
    return site_id


async def _site_id(db: AsyncSession, group: str, site: str) -> uuid.UUID | None:
    return await db.scalar(
        select(Site.id).join(Group, Group.id == Site.group_id).where(Group.slug == group, Site.slug == site)
    )


async def hide_button(request: Request) -> Response:
    """POST /-/feedback/hide: hides the button on one site until the browser
    closes, twelve hours at most. Only with a page token of that site, and
    the cookie holds a sealed mark for that site alone.

    That does not stop another page from posting a token it got hold of, and
    a public site's page hands one to anyone. No header tells such a post
    from the widget's own: from a shielded page that is a cross-site
    navigation with `Origin: null` as well. Accepted, since it costs the
    visitor the button on that one site for that while, and the show link
    brings it back."""
    if not sessions.top_level_navigation(request):
        return neutral_404_response()
    form = await _small_form(request)
    if form is None:
        return neutral_404_response()
    tokens = form.getlist("token")
    target = return_target([str(value) for value in form.getlist("returnTo")])
    settings = request.app.state.settings
    now = int(time.time())
    token = parse_page_token(settings.session_secret, tokens[0], now=now) if len(tokens) == 1 else None
    if target is None or token is None:
        return neutral_404_response()
    async with request.app.state.session_factory() as db:
        address = (
            await db.execute(
                select(Group.slug, Site.slug).join(Group, Group.id == Site.group_id).where(Site.id == token.site_id)
            )
        ).one_or_none()
    if address is None or tuple(address) != (target.group, target.site):
        return neutral_404_response()
    response = RedirectResponse(target.path, status_code=303, headers=_NO_STORE)
    mark = mint_hide_mark(settings.session_secret, site_id=token.site_id, now=now)
    # No Max-Age: the cookie goes when the browser closes, and the mark in it
    # stops counting after twelve hours.
    response.set_cookie(FEEDBACK_HIDE_COOKIE, mark, path=target.prefix, secure=True, httponly=True, samesite="lax")
    return response


async def show_button(request: Request) -> Response:
    """GET /-/feedback/show: shows the button again on the site in
    `returnTo`. It clears the cookie at the site's path, where hiding sets
    it, and at `/`, where a page's own script may have written one."""
    if not sessions.top_level_navigation(request):
        return neutral_404_response()
    target = return_target(request.query_params.getlist("returnTo"))
    if target is None:
        return neutral_404_response()
    response = RedirectResponse(target.path, status_code=303, headers=_NO_STORE)
    for path in (target.prefix, "/"):
        response.delete_cookie(FEEDBACK_HIDE_COOKIE, path=path, secure=True, httponly=True, samesite="lax")
    return response


class _FormTooLargeError(Exception):
    """Raised mid-stream once a form is longer than MAX_FORM_BYTES."""


async def _small_form(request: Request) -> FormData | None:
    """The urlencoded form of one of these pages, or None for anything else,
    a body the client gave up on included: nobody reads that answer, and the
    ClientDisconnect would otherwise end in a 500 with a logged traceback."""
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != _FORM_TYPE:
        return None
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_FORM_BYTES:
        return None
    total = 0

    async def bounded() -> AsyncGenerator[bytes]:
        nonlocal total
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_FORM_BYTES:
                raise _FormTooLargeError
            yield chunk

    try:
        return await FormParser(request.headers, bounded()).parse()
    except (_FormTooLargeError, ClientDisconnect):
        return None


__all__ = [
    "CONSENT_PAGE_HEADERS",
    "MAX_FORM_BYTES",
    "PageRoute",
    "ReturnTarget",
    "account_page",
    "consent_page_html",
    "give_consent",
    "hide_button",
    "page_route",
    "return_target",
    "show_button",
    "stop_consent",
    "without_key",
]

"""Who stands behind a request to the feedback widget API: the page token,
checked again against what holds now.

The widget API reads no cookie. Every request carries the page token Toon put
in the widget's config, and the token only says what to check: the site, the
target, the content session and the secret link the gate let the visitor in
with. The session is there once its visitor consented, on Toon's own page, to
feedback with their account on this site (`ses`), or else when it is what let
the visitor in (`acc`). Each request checks all of it again, the way a page
view would. The token must open and be unexpired. A session must still exist
as a content session, hold the consent where the token says it does, and pass
IdP revalidation, which the middleware cannot run for a request without
cookies. The site is found by its id, and the gate decides again at its
current address for a visitor built from that server-side state alone; it
must allow the very site the token names, on a target that still collects
feedback. Whatever fails is no visitor at all.

Only a consented session comes out as the context's session. A session that
only let the visitor in serves the gate and nothing after it: no author
fields, no own items, replies or deletes. So nothing of that session goes
with an item that has a typed name or none. Its sub only keys the write
counter in memory, apart from the consented one (`gate_sub`), so colleagues
behind one address do not share a budget.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from toon.access import gate
from toon.access.decision import AccessDecision, DecisionKind, FeedbackPolicy
from toon.auth.revalidation import revalidate_session
from toon.auth.sessions import Session, SessionKind, SessionStore, Visitor, site_prefix
from toon.models.identity import Group
from toon.models.publication import Site
from toon.serving.feedback_token import PageToken, parse_page_token


@dataclass(frozen=True)
class FeedbackContext:
    """A verified page token, with what the checks found on the way."""

    token: PageToken
    # The site's current address, which the token does not carry.
    group_slug: str
    site_slug: str
    # The consented content session, None for a token without one, also
    # when the token named a session for the gate alone.
    session: Session | None
    feedback: FeedbackPolicy
    # The sub of the session the token named for the gate alone, None for
    # any other token. It keys the write counter and nothing else.
    gate_sub: str | None = None

    @property
    def site_id(self) -> uuid.UUID:
        return self.token.site_id

    @property
    def root(self) -> str:
        """Where the token's target starts: the site, or its preview."""
        prefix = site_prefix(self.group_slug, self.site_slug)
        return prefix if self.token.ref is None else f"{prefix}_preview/{self.token.ref}/"


def open_feedback(decision: AccessDecision, *, preview: bool) -> FeedbackPolicy | None:
    """The feedback settings of an allowed page the widget belongs on, or
    None: feedback off, the live site of a site that collects on previews
    only, or no allow at all."""
    policy = decision.feedback
    if decision.kind is not DecisionKind.ALLOW or policy is None or not policy.enabled:
        return None
    return policy if preview or policy.live else None


async def decide_page(
    db: AsyncSession, group_slug: str, site_slug: str, ref: str | None, visitor: Visitor
) -> AccessDecision:
    """The gate on a live page, or with a ref on that preview."""
    if ref is None:
        return await gate.decide(db, group_slug, site_slug, visitor)
    return await gate.decide_preview(db, group_slug, site_slug, ref, visitor)


async def verify_page_token(request: Request, raw: object) -> FeedbackContext | None:
    """The page token in `raw` checked against what holds now, or None."""
    settings = request.app.state.settings
    token = parse_page_token(settings.session_secret, raw, now=int(time.time()))
    if token is None:
        return None
    session = None
    if token.session_id is not None:
        session = await _consented_session(request, token.session_id, str(token.site_id))
        if session is None:
            return None
    # The session the gate sees: the consented one, or else the one that let
    # the visitor in. A token never names both.
    seen = session
    gate_sub = None
    if token.access_session_id is not None:
        seen = await _access_session(request, token.access_session_id)
        if seen is None:
            return None
        gate_sub = seen.sub
    visitor = Visitor(
        sub=seen.sub if seen else None,
        email=seen.email if seen else None,
        email_verified=seen.email_verified if seen else False,
        key_cookie=token.key_id,
    )
    async with request.app.state.session_factory() as db:
        address = (
            await db.execute(
                select(Group.slug, Site.slug).join(Group, Group.id == Site.group_id).where(Site.id == token.site_id)
            )
        ).one_or_none()
        if address is None:
            return None
        group_slug, site_slug = address
        decision = await decide_page(db, group_slug, site_slug, token.ref, visitor)
    # Another site at these slugs, between finding them and deciding on them,
    # is a refusal like any other.
    policy = open_feedback(decision, preview=token.ref is not None)
    if policy is None or decision.site_id != token.site_id:
        return None
    return FeedbackContext(token, group_slug, site_slug, session, policy, gate_sub)


async def _consented_session(request: Request, session_id: str, site: str) -> Session | None:
    store: SessionStore = request.app.state.session_store
    session = store.get_session(session_id)
    if session is None or session.kind is not SessionKind.CONTENT or site not in session.feedback_sites:
        return None
    # The consent can end while the IdP is being asked.
    session = await revalidate_session(request, session)
    return session if session is not None and site in session.feedback_sites else None


async def _access_session(request: Request, session_id: str) -> Session | None:
    """The content session a page view let the visitor in with, when it is
    still there and the IdP still stands behind it. Consent plays no part."""
    store: SessionStore = request.app.state.session_store
    session = store.get_session(session_id)
    if session is None or session.kind is not SessionKind.CONTENT:
        return None
    return await revalidate_session(request, session)


__all__ = ["FeedbackContext", "decide_page", "open_feedback", "verify_page_token"]

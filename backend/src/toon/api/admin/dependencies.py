"""What a route of the admin API demands before it runs: the session, the
CSRF header, the platform role, a CLI token where one is accepted, and the
creation budget.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from toon.api.admin.common import KEY_ADMIN_ONLY, KEY_CSRF_INVALID, KEY_NO_SESSION
from toon.api.deploys import bearer_from_request, cli_member
from toon.api.errors import WWW_AUTHENTICATE_BEARER, ApiError
from toon.audit import request as audit_request
from toon.audit import vocabulary
from toon.auth import sessions
from toon.auth.members import require_active_member
from toon.cli import service as cli
from toon.db import request_db
from toon.models.identity import Member, PlatformRole
from toon.ratelimit import InMemoryCounter


async def require_csrf(request: Request) -> None:
    """Double-submit check for mutations; runs before the member upsert so a
    forged request never creates a member record."""
    session = sessions.session_from_request(request)
    if session is None:
        raise ApiError(401, KEY_NO_SESSION)
    if not sessions.csrf_valid(request, session):
        raise ApiError(403, KEY_CSRF_INVALID)


Db = Annotated[AsyncSession, Depends(request_db)]
ActiveMember = Annotated[Member, Depends(require_active_member)]


async def require_platform_admin(member: ActiveMember) -> Member:
    """Only a platform administrator. A dependency rather than a check in the
    route: FastAPI resolves dependencies before it parses the body, so a
    non-admin is refused before a malformed body would be (a 422 beating the
    403 would leave a non-admin's probing request unaudited)."""
    if member.platform_role != PlatformRole.ADMIN:
        raise ApiError(403, KEY_ADMIN_ONLY)
    return member


PlatformAdmin = Annotated[Member, Depends(require_platform_admin)]
Csrf = Annotated[None, Depends(require_csrf)]
SearchTerm = Annotated[
    str,
    Query(
        description=(
            "Search term of at least two characters. Matches name and e-mail address, case-insensitively, anywhere "
            "in the text."
        ),
        examples=["jansen"],
    ),
]


# Per member, new groups, new sites and changes of address together; every
# attempt that gets past the role check and the input validation counts, a
# slug collision included.
CREATION_MAX_PER_WINDOW = 20
CREATION_WINDOW_S = 3600


@dataclass(frozen=True)
class Creator:
    """Who creates a group or a site, links a repository or works through
    feedback: the member, plus the CLI session when the request came with a
    CLI token instead of an admin session."""

    member: Member
    cli_session_id: uuid.UUID | None = None

    def audit_refs(self) -> dict:
        if self.cli_session_id is None:
            return {}
        return {"via": vocabulary.VIA_CLI, "cli_session": str(self.cli_session_id)}


async def require_creator(request: Request, db: Db) -> Creator:
    """The two creation routes, the repository link and the feedback changes
    the CLI makes take an admin session with CSRF or a CLI access token. A
    bearer header decides: with one, the session cookie is not looked at, and
    neither is CSRF, since nothing ambient came along. A CI ID token is
    site-bound and creates, links or changes nothing."""
    plaintext = bearer_from_request(request)
    if plaintext is None:
        await require_csrf(request)
        return Creator(member=await require_active_member(request, db))
    if not plaintext.startswith(cli.ACCESS_TOKEN_PREFIX + "_"):
        raise ApiError(401, "TOKEN_INVALID.cli_only", headers=WWW_AUTHENTICATE_BEARER)
    session, member = await cli_member(request, db, plaintext)
    creator = Creator(member=member, cli_session_id=session.id)
    # A refusal after this point (role, rate limit) is audited by
    # api/errors.py, which cannot see this member through a session cookie.
    request.state.audit_actor = audit_request.member_actor(member)
    request.state.audit_refs = creator.audit_refs()
    return creator


async def require_member_or_cli(request: Request, db: Db) -> Creator:
    """The feedback reads the CLI makes take an admin session or a CLI access
    token. With a session this is `require_active_member` alone, without the
    CSRF header, like every other read: an `<img>` that loads a screenshot
    cannot send one. With a bearer header it is `require_creator`, so the
    token rules and the attribution of a refusal are the same."""
    if bearer_from_request(request) is None:
        return Creator(member=await require_active_member(request, db))
    return await require_creator(request, db)


async def _require_creation_budget(request: Request, member: Member) -> None:
    counter = getattr(request.app.state, "creation_counter", None)
    if counter is None:
        counter = InMemoryCounter()
        request.app.state.creation_counter = counter
    result = await counter.increment(f"member:{member.id}", CREATION_WINDOW_S, time.monotonic())
    if result.count > CREATION_MAX_PER_WINDOW:
        raise ApiError(
            429,
            "TOO_MANY_CREATIONS",
            params={"limit": CREATION_MAX_PER_WINDOW},
            headers={"Retry-After": str(max(1, math.ceil(result.remaining_s)))},
        )

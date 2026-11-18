"""Who is calling, which site the request may act on, and whether the caller may deploy there."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from toon.api.authorization import require_site_role
from toon.api.deploys.auditing import _audit
from toon.api.deploys.bearer import bearer_from_request
from toon.api.errors import WWW_AUTHENTICATE_BEARER, ApiError
from toon.audit import vocabulary
from toon.audit.log import Actor
from toon.auth import sessions
from toon.ci import trust
from toon.ci.providers import ProviderClient
from toon.ci.tokens import CiTokenError, CiTokenVerifier, VerifiedCiToken, looks_like_jwt
from toon.cli import service as cli
from toon.constants import Role
from toon.ingest.service import Deployer
from toon.models.audit import ActorKind
from toon.models.identity import Group, Member, MemberStatus
from toon.models.publication import Site

# Named after the package, not this module: the deploy API logs as one.
_logger = logging.getLogger(__package__)


@dataclass(frozen=True)
class _CiPrincipal:
    token: VerifiedCiToken
    repository: trust.TrustedRepository | None = None


@dataclass(frozen=True)
class _DeployAuth:
    member: Member | None
    ci: _CiPrincipal | None
    actor: Actor
    via_cli: bool = False
    cli_session_id: uuid.UUID | None = None

    @property
    def member_id(self) -> uuid.UUID | None:
        return self.member.id if self.member is not None else None


def _token_invalid() -> ApiError:
    return ApiError(401, "TOKEN_INVALID", headers=WWW_AUTHENTICATE_BEARER)


def ci_error(error: CiTokenError) -> ApiError:
    headers = WWW_AUTHENTICATE_BEARER if error.status == 401 else None
    return ApiError(
        error.status, error.message.key, params=error.message.params, headers=headers, reason=error.reason
    )


async def cli_member(request: Request, db: AsyncSession, plaintext: str) -> tuple[cli.CliSession, Member]:
    """The active member behind a CLI access token; the same 401/403 as a
    session would get. Records the use best-effort."""
    session = await cli.session_for_access_token(db, plaintext)
    if session is None:
        raise _token_invalid()
    member = await db.get(Member, session.member_id)
    if member is None or member.status != MemberStatus.ACTIVE:
        raise ApiError(403, "MEMBER_NOT_ACTIVE")
    # A session of its own: a failure here must leave `db` and the instances
    # it loaded untouched, since a rollback would expire them.
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    try:
        async with factory() as bookkeeping_db:
            await cli.mark_used(bookkeeping_db, session.id)
    except Exception:
        _logger.warning("last use of CLI session could not be recorded", exc_info=True)
    return session, member


async def _authenticate(request: Request, db: AsyncSession) -> _DeployAuth:
    plaintext = bearer_from_request(request)
    if plaintext is not None:
        if plaintext.startswith(cli.ACCESS_TOKEN_PREFIX + "_"):
            session, member = await cli_member(request, db, plaintext)
            return _DeployAuth(
                member=member,
                ci=None,
                actor=Actor(ActorKind.MEMBER, member.sso_subject),
                via_cli=True,
                cli_session_id=session.id,
            )
        if looks_like_jwt(plaintext):
            verifier: CiTokenVerifier = request.app.state.ci_verifier
            try:
                token = await verifier.verify(plaintext)
            except CiTokenError as error:
                raise ci_error(error) from None
            return _DeployAuth(
                member=None,
                ci=_CiPrincipal(token=token),
                actor=Actor(ActorKind.CI, trust.refused_actor_identifier(token)),
            )
        raise _token_invalid()

    session = sessions.session_from_request(request)
    if session is None:
        raise ApiError(401, "NO_AUTHENTICATION")
    if not sessions.csrf_valid(request, session):
        raise ApiError(403, "CSRF_INVALID")
    member = await db.scalar(select(Member).where(Member.sso_subject == session.sub))
    if member is None or member.status != MemberStatus.ACTIVE:
        raise ApiError(403, "MEMBER_NOT_ACTIVE")
    return _DeployAuth(member=member, ci=None, actor=Actor(ActorKind.MEMBER, session.sub))


async def _group_and_site(db: AsyncSession, *where) -> tuple[Group, Site] | None:
    row = (
        await db.execute(select(Group, Site).join(Site, Site.group_id == Group.id).where(*where))
    ).one_or_none()
    return None if row is None else (row.Group, row.Site)


async def _target_site(
    request: Request, db: AsyncSession, auth: _DeployAuth, group_slug: str, site_slug: str, refs: dict
) -> tuple[Group, Site]:
    """The site this request may act on: the one at the address, unless a CI
    token is bound to a site. Such a token is decided on the site it names
    first, whatever the address. A repository not linked to that site gets
    the one 401 of a token for another site at every address, so the address
    tells it nothing; the linked repository acts on that site at its own
    address and is told the site's address anywhere else (SITE_MOVED). No
    answer names a site id; for a CI token the audit `refs` get the id of
    the site at the address, also when this refuses."""
    found = await _group_and_site(db, Group.slug == group_slug, Site.slug == site_slug)
    if auth.ci is not None and found is not None:
        refs["site_id"] = str(found[1].id)
    if auth.ci is None or auth.ci.token.bound_site_id is None:
        if found is None:
            raise ApiError(404, "UNKNOWN_SITE.deploy")
        return found
    bound = auth.ci.token.bound_site_id
    named = await _group_and_site(db, Site.id == bound)
    if named is None or not await _linked_to(request, db, auth.ci.token, named[1]):
        raise ApiError(401, f"{vocabulary.CI_AUDIENCE_MISMATCH}.site", headers=WWW_AUTHENTICATE_BEARER)
    if found is not None and found[1].id == bound:
        return found
    group, site = named
    raise ApiError(409, vocabulary.SITE_MOVED, params={"address": f"{group.slug}/{site.slug}"})


async def _linked_to(request: Request, db: AsyncSession, token: VerifiedCiToken, site: Site) -> bool:
    """Whether the token's repository is the one linked to `site`."""
    providers: ProviderClient = request.app.state.ci_providers
    try:
        await trust.trusted_repository(db, token, site, providers)
    except CiTokenError as error:
        # Forgejo out of reach decides nothing either way: try again later.
        if error.reason == vocabulary.CI_PROVIDER_UNREACHABLE:
            raise ci_error(error) from None
        return False
    return True


async def _authorize(
    request: Request, db: AsyncSession, auth: _DeployAuth, group_slug: str, site: Site
) -> _DeployAuth:
    """Returns the auth to carry on with: for CI, with the matched repository
    and its actor pseudonymised on the stored repository id. A trusted token
    that names the repository differently renames the link."""
    if auth.ci is None:
        # _authenticate sets a member on every auth without a CI principal.
        await require_site_role(db, auth.member, site, Role.EDITOR)  # ty: ignore[invalid-argument-type]
        return auth
    providers: ProviderClient = request.app.state.ci_providers
    try:
        repository = await trust.trusted_repository(db, auth.ci.token, site, providers)
    except CiTokenError as error:
        raise ci_error(error) from None
    trusted = _DeployAuth(
        member=None,
        ci=_CiPrincipal(token=auth.ci.token, repository=repository),
        actor=Actor(ActorKind.CI, repository.actor_identifier),
    )
    previous = await trust.follow_token(db, site, repository)
    if previous is not None:
        await _audit(
            request,
            trusted.actor,
            vocabulary.SITE_REPOSITORY_RENAME,
            vocabulary.ALLOWED,
            None,
            {
                "group": group_slug,
                "site": site.slug,
                "provider": str(repository.provider),
                "host": repository.host,
                "repository": f"{repository.owner}/{repository.repo}",
                "previous_repository": previous,
                "repository_id": repository.repository_id,
            },
        )
    return trusted


def _auth_refs(auth: _DeployAuth | None) -> dict:
    if auth is None:
        return {}
    if auth.ci is not None:
        return trust.audit_refs(auth.ci.token)
    if auth.via_cli:
        return {"via": vocabulary.VIA_CLI, "cli_session": str(auth.cli_session_id)}
    return {}


def _deployer(auth: _DeployAuth) -> Deployer:
    if auth.ci is not None and auth.ci.repository is not None:
        return Deployer(ci_repository=auth.ci.repository.origin)
    return Deployer(member_id=auth.member_id)

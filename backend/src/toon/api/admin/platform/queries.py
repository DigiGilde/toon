"""The rules and lookups behind the platform routes: who may lose platform
rights, and reading the audit log back, the re-identification lookups and
the purge hold among them.
"""

from __future__ import annotations

import hmac
from collections.abc import Sequence
from typing import Literal

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from toon import net
from toon.api.admin.lookup import IdentifierAmbiguousError
from toon.api.admin.platform.schemas import AUDIT_PSEUDONYM_LENGTH, AuditEntryOut, PurgeHoldOut, PurgeHoldStatus
from toon.api.errors import ApiError
from toon.api.schema import iso_utc
from toon.audit import request as audit_request
from toon.audit import vocabulary
from toon.audit.log import LookupLimitReachedError
from toon.audit.pseudonymisation import pseudonymise
from toon.ci.providers import host_label
from toon.ci.trust import ci_actor_identifier
from toon.models.audit import AuditLogEntry, AuditPurgeHold, ContentViewer
from toon.models.ci import SiteRepository
from toon.models.identity import Group, Member, MemberStatus, PlatformRole
from toon.models.publication import Site


def _audit_entry_json(entry: AuditLogEntry) -> AuditEntryOut:
    return AuditEntryOut(
        id=entry.id,
        occurred_at=iso_utc(entry.occurred_at),
        actor_kind=entry.actor_kind,
        actor_pseudonym=entry.actor_pseudonym,
        action=entry.action,
        result=entry.result,
        reason_code=entry.reason_code,
        refs=entry.refs,
        ip_truncated=entry.ip_truncated,
    )


def _refuse_self(target: Member, member: Member, what: str) -> None:
    """You cannot take your own platform rights away.

    Not paternalism but a lockout guard: the admin API is the only way back
    in, and a member who has just switched themselves off can no longer reach
    it. Someone else with the role does it, or you change the bootstrap
    setting.
    """
    if target.id == member.id:
        raise ApiError(409, f"SELF_NOT_ALLOWED.{what}")


def _refuse_bootstrap(request: Request, target: Member) -> None:
    """The bootstrap account is restored on every login (auth/members.py), so
    changing it here would look like it worked and be undone on the next
    request. Refusing says out loud what the honest fix is."""
    bootstrap_sub = request.app.state.settings.bootstrap_admin_sub
    if bootstrap_sub and target.sso_subject == bootstrap_sub:
        raise ApiError(409, "BOOTSTRAP_MEMBER")


async def _refuse_last_admin(db: AsyncSession, target: Member) -> None:
    """A platform without an active admin can never let anyone in again:
    activating a member is itself an admin action. So the last one stays."""
    if target.platform_role != PlatformRole.ADMIN or target.status != MemberStatus.ACTIVE:
        return
    remaining = await db.scalar(
        select(func.count())
        .select_from(Member)
        .where(
            Member.platform_role == PlatformRole.ADMIN,
            Member.status == MemberStatus.ACTIVE,
            Member.id != target.id,
        )
    )
    # _refuse_self already blocks target == caller, and the caller is always a
    # distinct active admin, so remaining never reaches zero over the API.
    if not remaining:  # pragma: no cover - unreachable, see above
        raise ApiError(409, "LAST_PLATFORM_ADMIN")


async def _refuse_taking_platform_rights(
    request: Request, db: AsyncSession, target: Member, member: Member, what: str
) -> None:
    """The three refusals before a member loses platform rights, by
    deactivation or demotion, in the order they are checked."""
    _refuse_self(target, member, what)
    _refuse_bootstrap(request, target)
    await _refuse_last_admin(db, target)


async def _audit_strict(request: Request, member: Member, action: str, refs: dict) -> None:
    """Fail-closed variant of `_audit`, for the de-anonymisation reads
    (actor-pseudonym, actor-identity, the audit log itself): the audit row is
    the only record that the read happened, so a failed write must not let
    the response through. Marks the request as already audited, so a later
    ApiError (e.g. an unknown pseudonym) does not also pick up a generic
    admin_access row from api/errors.py::_audit_refusal.
    """
    log = audit_request.audit_log(request)
    request.state.audit_written = True
    if log is None:
        raise ApiError(503, "AUDIT_UNAVAILABLE")
    ip = net.client_ip_from_request(request)
    try:
        await log.write_strict(
            action,
            audit_request.member_actor(member),
            vocabulary.ALLOWED,
            refs=refs,
            ip=ip,
        )
    except Exception as error:
        raise ApiError(503, "AUDIT_UNAVAILABLE") from error


def _pseudonymises_to(peppers: Sequence[str], subject: str, pseudonym: str) -> bool:
    """Whether `subject` pseudonymises to `pseudonym` under any of `peppers`."""
    return any(hmac.compare_digest(pseudonymise(pepper, subject), pseudonym) for pepper in peppers)


async def _match_member_by_pseudonym(db: AsyncSession, peppers: Sequence[str], pseudonym: str) -> Member | None:
    """Linear scan for the reverse actor lookup: only id and sso_subject are
    pulled for the comparison, so the full row is fetched once, for the one
    id that actually matches."""
    rows = await db.execute(select(Member.id, Member.sso_subject))
    for member_id, sso_subject in rows:
        if _pseudonymises_to(peppers, sso_subject, pseudonym):
            return await db.get(Member, member_id)
    return None


async def _match_content_viewer_by_pseudonym(
    db: AsyncSession, peppers: Sequence[str], pseudonym: str
) -> ContentViewer | None:
    """Same shape as `_match_member_by_pseudonym`, for content viewers."""
    rows = await db.execute(select(ContentViewer.id, ContentViewer.sso_subject))
    for viewer_id, sso_subject in rows:
        if _pseudonymises_to(peppers, sso_subject, pseudonym):
            return await db.get(ContentViewer, viewer_id)
    return None


def _ci_subjects(repository: SiteRepository) -> tuple[str, str]:
    """Both identifiers a CI actor for this repository can have been audited
    under: the stored id (every accepted deploy) and `owner/repo` in lowercase
    (a refused token without ids, see ci/trust.py)."""
    return (
        ci_actor_identifier(repository.provider, repository.host, repository.repository_id),
        ci_actor_identifier(repository.provider, repository.host, f"{repository.owner}/{repository.repo}".lower()),
    )


async def _match_ci_by_pseudonym(
    db: AsyncSession, peppers: Sequence[str], pseudonym: str
) -> tuple[SiteRepository, list[str]] | None:
    """Same shape as `_match_member_by_pseudonym`, over the linked
    repositories; returns the first match plus every site it is linked to."""
    rows = list(
        (
            await db.execute(
                select(SiteRepository, Group.slug, Site.slug)
                .join(Site, Site.id == SiteRepository.site_id)
                .join(Group, Group.id == Site.group_id)
                .order_by(Group.slug, Site.slug)
            )
        ).all()
    )
    for repository, _, _ in rows:
        if any(_pseudonymises_to(peppers, subject, pseudonym) for subject in _ci_subjects(repository)):
            sites = [
                f"{group_slug}/{site_slug}"
                for other, group_slug, site_slug in rows
                if (other.provider, other.host, other.repository_id)
                == (repository.provider, repository.host, repository.repository_id)
            ]
            return repository, sites
    return None


def _parse_repository_identifier(identifier: str) -> tuple[str | None, str] | None:
    """`owner/repo`, `host/owner/repo` or `https://host/owner/repo` into
    (host or None, `owner/repo` in lowercase)."""
    value = identifier.strip().removeprefix("https://").removeprefix("http://").strip("/")
    parts = value.split("/")
    if len(parts) == 2 and all(parts):
        return None, "/".join(parts).lower()
    if len(parts) == 3 and all(parts) and "." in parts[0]:
        return parts[0].lower(), "/".join(parts[1:]).lower()
    return None


async def _resolve_ci_identifier(db: AsyncSession, identifier: str) -> str | None:
    """The CI subject of a linked repository named by `identifier`, or None.
    More than one distinct repository (same name on two providers) is
    `IdentifierAmbiguousError`."""
    parsed = _parse_repository_identifier(identifier)
    if parsed is None:
        return None
    host, name = parsed
    subjects = {
        _ci_subjects(repository)[0]
        for repository in await db.scalars(select(SiteRepository))
        if f"{repository.owner}/{repository.repo}".lower() == name
        and (host is None or host_label(repository.host).lower() == host)
    }
    if len(subjects) > 1:
        raise IdentifierAmbiguousError(len(subjects))
    return next(iter(subjects), None)


async def _resolve_lookup_identifier(
    db: AsyncSession, identifier: str
) -> tuple[Literal["member", "content_viewer"], str] | None:
    """Resolves an identifier for `actor-pseudonym`: an exact sso_subject
    match (member or content viewer) wins outright - the same sub in both
    tables is one person, not an ambiguity, member takes priority for the
    reported kind. Only without one does an email match count, across
    members and verified content viewers combined and deduplicated by
    subject (an unverified content-viewer email is never a match):
    more than one distinct subject there is `IdentifierAmbiguousError`.

    Returns `(resolved_as, subject)`, or `None` when nothing at all matched
    (the caller then tries a linked repository, or treats it as unknown).
    """
    normalised = identifier.strip()

    exact_member = await db.scalar(select(Member).where(Member.sso_subject == normalised))
    if exact_member is not None:
        return "member", exact_member.sso_subject
    exact_viewer = await db.scalar(select(ContentViewer).where(ContentViewer.sso_subject == normalised))
    if exact_viewer is not None:
        return "content_viewer", exact_viewer.sso_subject

    email = normalised.lower()
    member_matches = list(await db.scalars(select(Member.sso_subject).where(Member.email == email)))
    viewer_matches = list(
        await db.scalars(
            select(ContentViewer.sso_subject).where(
                ContentViewer.email == email, ContentViewer.email_verified.is_(True)
            )
        )
    )
    distinct_subjects = set(member_matches) | set(viewer_matches)
    if len(distinct_subjects) > 1:
        raise IdentifierAmbiguousError(len(distinct_subjects))
    if member_matches:
        return "member", member_matches[0]
    if viewer_matches:
        return "content_viewer", viewer_matches[0]
    return None


# Actions that a de-anonymisation counts against the daily lookup limit
# (TOON_AUDIT_LOOKUP_DAILY_LIMIT): reading the audit log itself does not.
_LOOKUP_ACTIONS = (vocabulary.AUDIT_ACTOR_LOOKUP, vocabulary.AUDIT_ACTOR_IDENTITY, vocabulary.AUDIT_IP_REVEAL)


async def _audit_strict_limited(request: Request, member: Member, action: str, refs: dict) -> None:
    """Fail-closed and atomically capped variant of `_audit_strict`, for the
    three de-anonymisation actions: writes exactly one row for this call, or
    raises 429 `LOOKUP_LIMIT_REACHED` without writing one (the generic
    admin_access/refused row from `api/errors.py` covers that refusal
    instead), or 503 if the write itself fails. See
    `AuditLog.write_strict_limited` for how the count and the write are kept
    from racing (TOCTOU)."""
    request.state.audit_written = True
    log = audit_request.audit_log(request)
    if log is None:
        raise ApiError(503, "AUDIT_UNAVAILABLE")
    settings = request.app.state.settings
    ip = net.client_ip_from_request(request)
    try:
        await log.write_strict_limited(
            action,
            audit_request.member_actor(member),
            vocabulary.ALLOWED,
            refs=refs,
            ip=ip,
            limit=settings.audit_lookup_daily_limit,
            counted_actions=_LOOKUP_ACTIONS,
        )
    except LookupLimitReachedError as error:
        # No row landed for this attempt: let the generic admin_access/refused
        # handler in api/errors.py audit the refusal instead.
        request.state.audit_written = False
        raise ApiError(429, "LOOKUP_LIMIT_REACHED") from error
    except Exception as error:
        raise ApiError(503, "AUDIT_UNAVAILABLE") from error


# The partial unique index on audit_purge_holds (0010_audit_purge_hold): at
# most one hold without an end.
_OPEN_HOLD_INDEX = "uq_audit_purge_holds_open"


async def _purge_hold_status(db: AsyncSession) -> PurgeHoldStatus:
    row = (
        await db.execute(
            select(AuditPurgeHold, Member)
            .join(Member, Member.id == AuditPurgeHold.started_by)
            .where(AuditPurgeHold.ended_at.is_(None))
        )
    ).first()
    if row is None:
        return PurgeHoldStatus(on_hold=False, hold=None)
    hold, starter = row
    return PurgeHoldStatus(
        on_hold=True,
        hold=PurgeHoldOut(
            reason=hold.reason,
            started_at=iso_utc(hold.started_at) or "",
            started_by_name=starter.name or starter.email,
        ),
    )


# Newest first, with the id behind it, so two rows from the same transaction
# time keep a fixed order and the cursor can neither skip nor repeat one.
_AUDIT_ORDER = (AuditLogEntry.occurred_at.desc(), AuditLogEntry.id.desc())

_HEX = "0123456789abcdef"


def _audit_pseudonym(value: str) -> str:
    normalised = value.strip().lower()
    if len(normalised) != AUDIT_PSEUDONYM_LENGTH or not all(char in _HEX for char in normalised):
        raise ApiError(
            422, "ACTOR_PSEUDONYM_INVALID", params={"length": AUDIT_PSEUDONYM_LENGTH}
        )
    return normalised

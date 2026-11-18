"""The audit log: reading it, the re-identification lookups and the purge hold."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request
from sqlalchemy import func, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError

from toon.api.admin.common import _require_aware, _validate_text
from toon.api.admin.contract import _ERROR_ADMIN, _ERROR_CSRF, _ERROR_IDENTIFIER_AMBIGUOUS, _errors
from toon.api.admin.dependencies import Csrf, Db, PlatformAdmin
from toon.api.admin.lookup import IdentifierAmbiguousError, _audit
from toon.api.admin.platform.queries import (
    _AUDIT_ORDER,
    _OPEN_HOLD_INDEX,
    _audit_entry_json,
    _audit_pseudonym,
    _audit_strict,
    _audit_strict_limited,
    _match_ci_by_pseudonym,
    _match_content_viewer_by_pseudonym,
    _match_member_by_pseudonym,
    _purge_hold_status,
    _resolve_ci_identifier,
    _resolve_lookup_identifier,
)
from toon.api.admin.platform.schemas import (
    AUDIT_PSEUDONYM_LENGTH,
    REASON_MAX_LENGTH,
    REASON_MIN_LENGTH,
    ActorIdentityLookup,
    ActorIdentityOut,
    ActorLookup,
    ActorPseudonymOut,
    AuditFilters,
    AuditPage,
    IpRevealBody,
    IpRevealOut,
    PurgeHoldReason,
    PurgeHoldStatus,
    _validated_hold_reason,
)
from toon.api.admin.serialization import _audit_cursor_read, _page_cursor
from toon.api.docs import TAG_AUDIT
from toon.api.errors import ApiError
from toon.api.schema import iso_utc
from toon.audit import vocabulary
from toon.audit.ip_crypto import IpDecryptError, decrypt_ip
from toon.audit.pseudonymisation import pseudonymise
from toon.db import violated_constraint
from toon.models.audit import AuditLogEntry, AuditPurgeHold

_ERROR_AUDIT_FILTER = {
    422: (
        "A filter is invalid: the cursor is unreadable (`CURSOR_INVALID`), `actorPseudonym` is not a "
        "hexadecimal HMAC value (`ACTOR_PSEUDONYM_INVALID`), or `limit` is outside 1 to 200."
    )
}
_ERROR_AUDIT_UNAVAILABLE = {
    503: (
        "The action itself writes an audit row, and that write failed (`AUDIT_UNAVAILABLE`); "
        "nothing was returned. Try again later."
    )
}


_ERROR_REASON = {
    422: (
        "`reason` is missing, is shorter than 10 or longer than 500 characters (after stripping spaces), "
        "contains a control or formatting character, or contains an e-mail address (give a case or ticket "
        "number)."
    )
}
_ERROR_HOLD_REASON = {
    422: (
        "`reason` is empty after stripping spaces (`HOLD_REASON_EMPTY`), is longer than 10000 characters "
        "(`HOLD_REASON_TOO_LONG`), contains a control or formatting character other than a line break or a tab, "
        "such as a zero-width space (`HOLD_REASON_INVISIBLE_CHARACTER`), contains an e-mail address "
        "(`HOLD_REASON_EMAIL`), or is missing."
    )
}
_ERROR_ALREADY_ON_HOLD = {409: "The audit purge is already on hold (`AUDIT_PURGE_ALREADY_ON_HOLD`)."}
_ERROR_NOT_ON_HOLD = {409: "The audit purge is not on hold (`AUDIT_PURGE_NOT_ON_HOLD`)."}
_ERROR_HOLD_AUDIT_UNAVAILABLE = {
    503: (
        "The audit row for this change could not be written (`AUDIT_UNAVAILABLE`), so the hold is as it was. "
        "Try again later."
    )
}
_ERROR_LOOKUP_LIMIT = {
    429: (
        "The daily limit on audit lookups by this platform administrator has been reached "
        "(`LOOKUP_LIMIT_REACHED`); try again tomorrow."
    )
}


def add_routes(router: APIRouter) -> None:
    # -- Audit log --

    @router.get(
        "/platform/audit",
        tags=[TAG_AUDIT],
        summary="Read the audit log",
        response_description="One page of audit rows, newest first, with the cursor to the next.",
        description=(
            "Reads the audit log, newest first: refusals, signing in and out, viewing "
            "non-public content, deploys and admin actions. Vocabulary and retention periods are in "
            "`docs/audit-log.md`.\n\n"
            "**Paging works with `cursor`, not with a page number.** The log grows while you read, and "
            "a shifting offset would skip rows or show them twice. Take `nextCursor` from the "
            "response unchanged; if it is `null`, this was the last page.\n\n"
            "**Actors appear pseudonymised** and are not translated back here. If you are looking for "
            "someone in particular, first fetch their pseudonym with "
            "`POST /platform/audit/actor-pseudonym` and filter on `actorPseudonym` with it.\n\n"
            "**Who can call this:** only a platform administrator. A group admin does not see even their own group: "
            "the group of a row is only a free-form key in `refs`, and no "
            "authorization boundary can be built on that. Reading the log is itself audited; if writing that "
            "audit row fails, no page is returned: see the `503`."
        ),
        responses=_errors(_ERROR_ADMIN, _ERROR_AUDIT_FILTER, _ERROR_AUDIT_UNAVAILABLE),
    )
    async def audit_log(
        request: Request,
        member: PlatformAdmin,
        db: Db,
        filters: Annotated[AuditFilters, Query()],
    ) -> AuditPage:
        statement = select(AuditLogEntry).order_by(*_AUDIT_ORDER).limit(filters.limit + 1)
        if filters.since is not None:
            statement = statement.where(AuditLogEntry.occurred_at >= _require_aware(filters.since))
        if filters.until is not None:
            statement = statement.where(AuditLogEntry.occurred_at < _require_aware(filters.until))
        if filters.action:
            statement = statement.where(AuditLogEntry.action == filters.action)
        if filters.result:
            statement = statement.where(AuditLogEntry.result == filters.result)
        if filters.reason_code:
            statement = statement.where(AuditLogEntry.reason_code == filters.reason_code)
        if filters.group:
            statement = statement.where(
                or_(
                    AuditLogEntry.refs["group"].astext == filters.group,
                    AuditLogEntry.refs["previous_group"].astext == filters.group,
                )
            )
        if filters.site:
            statement = statement.where(
                or_(
                    AuditLogEntry.refs["site"].astext == filters.site,
                    AuditLogEntry.refs["previous_site"].astext == filters.site,
                    AuditLogEntry.refs["sites"].has_key(filters.site),
                )
            )
        if filters.actor_pseudonym:
            statement = statement.where(
                AuditLogEntry.actor_pseudonym == _audit_pseudonym(filters.actor_pseudonym)
            )
        if filters.cursor:
            occurred_at, last_id = _audit_cursor_read(filters.cursor)
            statement = statement.where(
                tuple_(AuditLogEntry.occurred_at, AuditLogEntry.id) < tuple_(occurred_at, last_id)
            )

        rows = list(await db.scalars(statement))
        entries = rows[: filters.limit]
        used = filters.model_dump(mode="json", exclude_none=True, by_alias=True)
        used.pop("cursor", None)
        # Written before the response is built: this read is itself an
        # actor-pseudonym-bearing disclosure, so a failed audit write must
        # not let the page through.
        await _audit_strict(request, member, vocabulary.AUDIT_READ, {"filters": used, "returned": len(entries)})
        return AuditPage(
            entries=[_audit_entry_json(entry) for entry in entries],
            next_cursor=_page_cursor(entries[-1].occurred_at, entries[-1].id) if len(rows) > filters.limit else None,
        )

    @router.post(
        "/platform/audit/actor-pseudonym",
        tags=[TAG_AUDIT],
        summary="Look up the pseudonym of an actor",
        response_description="The audit pseudonym that belongs to this identifier.",
        description=(
            "Translates an identifier into the pseudonym under which it appears in the audit log, so you can "
            "filter on `actorPseudonym`. So you must already know who you are looking for: the log does not give "
            "away a list of names. If you only know the pseudonym from a log row, "
            "use `POST /platform/audit/actor-identity` for the opposite direction.\n\n"
            "That this is a POST and not a query parameter is deliberate: an e-mail address in a URL ends up "
            "in the log lines of every proxy in between.\n\n"
            "The pseudonym depends on `TOON_AUDIT_PEPPER`. After a rotation of that pepper, rows from before "
            "the rotation carry the pseudonym under the previous pepper: as long as that pepper is still in "
            "`TOON_AUDIT_PEPPER_PREVIOUS`, `previousPseudonyms` gives it. Once the previous pepper is no longer "
            "configured, you only find rows from after the rotation.\n\n"
            "An e-mail address that matches neither a member nor the verified address of a "
            "content viewer is not pseudonymised: that is a 404. An identifier that is not an e-mail address "
            "and belongs to nothing (for example a bare SSO subject) is pseudonymised as given, as "
            "`unknown`. If an e-mail address belongs to more than one subject (only possible via e-mail, never via "
            "an SSO subject), the response is a 409: in that case search by the SSO subject.\n\n"
            "**Who can call this:** only a platform administrator, with a valid CSRF header and a `reason` of "
            f"{REASON_MIN_LENGTH} to {REASON_MAX_LENGTH} characters. The lookup itself is audited, with the "
            "pseudonym and the previous pseudonyms, `resolved_as` and the reason, never the identifier. If that "
            "audit row fails, no response is returned: see the `503`. Counts towards the daily limit on such "
            "lookups, even on a 404."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_ADMIN,
            _ERROR_AUDIT_UNAVAILABLE,
            _ERROR_REASON,
            _ERROR_LOOKUP_LIMIT,
            _ERROR_IDENTIFIER_AMBIGUOUS,
        ),
    )
    async def audit_actor_pseudonym(
        request: Request, body: ActorLookup, _csrf: Csrf, member: PlatformAdmin, db: Db
    ) -> ActorPseudonymOut:
        identifier = _validate_text(body.identifier, "identifier")

        try:
            resolution = await _resolve_lookup_identifier(db, identifier)
        except IdentifierAmbiguousError as error:
            await _audit_strict_limited(
                request,
                member,
                vocabulary.AUDIT_ACTOR_LOOKUP,
                {"resolved_as": "ambiguous", "matches": error.matches, "reason": body.reason},
            )
            raise ApiError(409, "IDENTIFIER_AMBIGUOUS.email") from error

        ci_subject = None
        if resolution is None and "@" not in identifier:
            try:
                ci_subject = await _resolve_ci_identifier(db, identifier)
            except IdentifierAmbiguousError as error:
                await _audit_strict_limited(
                    request,
                    member,
                    vocabulary.AUDIT_ACTOR_LOOKUP,
                    {"resolved_as": "ambiguous", "matches": error.matches, "reason": body.reason},
                )
                raise ApiError(409, "IDENTIFIER_AMBIGUOUS.repository") from error

        if resolution is not None:
            resolved, subject = resolution
        elif ci_subject is not None:
            resolved, subject = "ci", ci_subject
        elif "@" in identifier:
            # An e-mail address that matches nothing must not be hashed:
            # nothing to gain from a pseudonym for an address that will
            # never appear in the log.
            await _audit_strict_limited(
                request,
                member,
                vocabulary.AUDIT_ACTOR_LOOKUP,
                {"resolved_as": "unknown", "reason": body.reason},
            )
            raise ApiError(404, "IDENTIFIER_UNKNOWN")
        else:
            resolved, subject = "unknown", identifier
        pepper, *previous_peppers = request.app.state.settings.audit_peppers
        pseudonym = pseudonymise(pepper, subject)
        previous_pseudonyms = [pseudonymise(previous, subject) for previous in previous_peppers]
        refs: dict[str, Any] = {"pseudonym": pseudonym, "resolved_as": resolved, "reason": body.reason}
        if previous_pseudonyms:
            refs["previous_pseudonyms"] = previous_pseudonyms
        await _audit_strict_limited(request, member, vocabulary.AUDIT_ACTOR_LOOKUP, refs)
        return ActorPseudonymOut(
            actor_pseudonym=pseudonym, previous_pseudonyms=previous_pseudonyms, resolved_as=resolved
        )

    @router.post(
        "/platform/audit/actor-identity",
        tags=[TAG_AUDIT],
        summary="Look up the actor behind a pseudonym",
        response_description="The member, content viewer or CI repository behind this pseudonym.",
        description=(
            "The opposite direction of `POST /platform/audit/actor-pseudonym`: give a pseudonym from "
            "a log row, get back who is behind it. Every member, every content viewer and every linked "
            "repository is pseudonymised again with the current `TOON_AUDIT_PEPPER` and with each pepper in "
            "`TOON_AUDIT_PEPPER_PREVIOUS`, and compared with the given pseudonym; nothing is kept to speed up "
            "this lookup, so expect a full pass over all three.\n\n"
            "If the lookup finds nothing, there is no member, content viewer or repository that pseudonymises "
            "to this pseudonym. Several causes are possible: the pepper has been rotated since that row was "
            "written and the previous pepper is no longer configured; the actor only ever visited content "
            "through the content-host SSO and that visit was "
            "more than 180 days ago (`content_viewers` is then cleaned up, like the related "
            "`content_access` rows); or the repository has since been unlinked, or deleted along with its "
            "site or group (`ON DELETE CASCADE`).\n\n"
            "That this is a POST and not a query parameter is deliberate, as with the other side of this "
            "lookup.\n\n"
            "**Who can call this:** only a platform administrator, with a valid CSRF header and a `reason` of "
            f"{REASON_MIN_LENGTH} to {REASON_MAX_LENGTH} characters. The lookup itself is audited, with the "
            "given pseudonym, `resolved_as` and the reason, never the identifier found. If writing that "
            "audit row fails, no response is returned: see the `503`. Counts towards the daily limit on "
            "such lookups, even on a 404."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_ADMIN,
            _ERROR_AUDIT_UNAVAILABLE,
            _ERROR_REASON,
            _ERROR_LOOKUP_LIMIT,
            {422: f"Not {AUDIT_PSEUDONYM_LENGTH} hexadecimal characters (`ACTOR_PSEUDONYM_INVALID`)."},
            {
                404: (
                    "No member, content viewer or linked repository pseudonymises to this value "
                    "(`PSEUDONYM_UNKNOWN`): the pepper has been rotated and the previous one is no longer "
                    "configured, the content viewer was cleaned up after 180 days without a visit, or the "
                    "repository was unlinked or deleted along with its site."
                )
            },
        ),
    )
    async def audit_actor_identity(
        request: Request, body: ActorIdentityLookup, _csrf: Csrf, member: PlatformAdmin, db: Db
    ) -> ActorIdentityOut:
        pseudonym = _audit_pseudonym(body.actor_pseudonym)
        peppers = request.app.state.settings.audit_peppers

        result: ActorIdentityOut | None = None
        resolved = "unknown"

        matched_member = await _match_member_by_pseudonym(db, peppers, pseudonym)
        if matched_member is not None:
            resolved = "member"
            result = ActorIdentityOut(
                kind="member",
                member_id=matched_member.id,
                email=matched_member.email,
                name=matched_member.name or "",
                member_status=matched_member.status,
            )
        else:
            matched_viewer = await _match_content_viewer_by_pseudonym(db, peppers, pseudonym)
            if matched_viewer is not None:
                resolved = "content_viewer"
                result = ActorIdentityOut(
                    kind="content_viewer",
                    email=matched_viewer.email,
                    email_verified=matched_viewer.email_verified,
                    last_seen_at=iso_utc(matched_viewer.last_seen_at),
                )
            else:
                matched_ci = await _match_ci_by_pseudonym(db, peppers, pseudonym)
                if matched_ci is not None:
                    repository, linked_sites = matched_ci
                    resolved = "ci"
                    result = ActorIdentityOut(
                        kind="ci",
                        provider=repository.provider.value,
                        host=repository.host,
                        repository=f"{repository.owner}/{repository.repo}",
                        sites=linked_sites,
                    )

        # Written before the 404: the audit row is the only record that this
        # de-anonymisation was attempted, whatever the outcome.
        await _audit_strict_limited(
            request,
            member,
            vocabulary.AUDIT_ACTOR_IDENTITY,
            {"pseudonym": pseudonym, "resolved_as": resolved, "reason": body.reason},
        )
        if result is None:
            raise ApiError(404, "PSEUDONYM_UNKNOWN")
        return result

    @router.post(
        "/platform/audit/entries/{entry_id}/ip",
        tags=[TAG_AUDIT],
        summary="Reveal the full IP address of an audit row",
        response_description="The full IP address for this audit row.",
        description=(
            "`GET /platform/audit` only shows the truncated network per row (`ipTruncated`); this "
            "endpoint decrypts the full address stored encrypted next to it (`TOON_AUDIT_IP_KEY`, "
            "a different key from the audit pepper). Intended as a last resort, when the truncated network "
            "is not enough, for example in an incident investigation.\n\n"
            "**Who can call this:** only a platform administrator, with a valid CSRF header and a `reason` of "
            f"{REASON_MIN_LENGTH} to {REASON_MAX_LENGTH} characters. The decryption itself is audited, with "
            "the row ID and the reason, never the IP address. If that audit row fails, no "
            "response is returned: see the `503`. Counts towards the daily limit on such lookups, even on a "
            "404."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_ADMIN,
            _ERROR_AUDIT_UNAVAILABLE,
            _ERROR_REASON,
            _ERROR_LOOKUP_LIMIT,
            {404: "No audit row with this ID, or it has no encrypted IP address (`AUDIT_IP_UNKNOWN`)."},
        ),
    )
    async def audit_ip_reveal(
        request: Request,
        entry_id: Annotated[uuid.UUID, Path(description="ID of the audit row (`AuditEntryOut.id`).")],
        body: IpRevealBody,
        _csrf: Csrf,
        member: PlatformAdmin,
        db: Db,
    ) -> IpRevealOut:
        entry = await db.get(AuditLogEntry, entry_id)
        ip: str | None = None
        if entry is not None and entry.ip_encrypted:
            try:
                ip = decrypt_ip(
                    request.app.state.settings.audit_ip_key_bytes,
                    entry.id,
                    entry.ip_encrypted,
                    previous_keys=request.app.state.settings.audit_ip_keys_previous_bytes,
                )
            except IpDecryptError:
                # No key configured (current or previous) decrypts this row,
                # or the blob predates the AAD-binding format: indistinguishable
                # from "no encrypted IP" to the caller (docs/security.md).
                ip = None

        # Written before the 404: see the same audit call above.
        await _audit_strict_limited(
            request,
            member,
            vocabulary.AUDIT_IP_REVEAL,
            {"entry": str(entry_id), "reason": body.reason, "revealed": ip is not None},
        )
        if ip is None:
            raise ApiError(404, "AUDIT_IP_UNKNOWN")
        return IpRevealOut(ip=ip)

    @router.get(
        "/platform/audit/purge-hold",
        tags=[TAG_AUDIT],
        summary="Whether the audit purge is on hold",
        response_description="Whether a hold is open, and if so why, since when and by whom.",
        description=(
            "The nightly purge deletes audit rows and content viewers past their retention. During a "
            "(suspected) security incident a platform administrator puts it on hold, so the logging of the "
            "incident stays for the three years BIO2 5.28.01 asks.\n\n"
            "**Who can call this:** only a platform administrator."
        ),
        responses=_errors(_ERROR_ADMIN),
    )
    async def audit_purge_hold(member: PlatformAdmin, db: Db) -> PurgeHoldStatus:
        return await _purge_hold_status(db)

    @router.post(
        "/platform/audit/purge-hold",
        status_code=201,
        tags=[TAG_AUDIT],
        summary="Put the audit purge on hold",
        response_description="The open hold.",
        description=(
            "From now on nothing is purged from the audit log or from the content viewers, not even rows past "
            "their retention: the database refuses every delete while the hold is open. It takes effect at "
            "once, without a restart. The nightly job keeps publishing the chain head and logs a warning "
            "every night the purge is on hold. Starting the hold is audited, with the reason. A failed audit write "
            "does not stop the hold: the hold itself records who started it and why.\n\n"
            "**Who can call this:** only a platform administrator, with a valid CSRF header and a `reason`. It "
            "does not count towards the daily limit on audit lookups."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_ADMIN, _ERROR_HOLD_REASON, _ERROR_ALREADY_ON_HOLD),
    )
    async def start_audit_purge_hold(
        request: Request, body: PurgeHoldReason, _csrf: Csrf, member: PlatformAdmin, db: Db
    ) -> PurgeHoldStatus:
        reason = _validated_hold_reason(body.reason)
        hold = AuditPurgeHold(reason=reason, started_by=member.id)
        db.add(hold)
        try:
            await db.commit()
        except IntegrityError as error:
            await db.rollback()
            if violated_constraint(error) != _OPEN_HOLD_INDEX:
                raise
            raise ApiError(409, "AUDIT_PURGE_ALREADY_ON_HOLD") from error
        # Fail-open on purpose, unlike ending: a hold only protects, so a broken
        # audit log must not keep it from starting during an incident.
        await _audit(
            request, member, vocabulary.AUDIT_PURGE_HOLD_STARTED, {"hold": str(hold.id), "reason": reason}
        )
        return await _purge_hold_status(db)

    @router.post(
        "/platform/audit/purge-hold/end",
        tags=[TAG_AUDIT],
        summary="End the hold on the audit purge",
        response_description="The purge is no longer on hold.",
        description=(
            "Ends the open hold. The next nightly run purges everything that passed its retention in the "
            "meantime. Ending the hold is audited, with the reason: without that audit row the hold stays open.\n\n"
            "**Who can call this:** only a platform administrator, with a valid CSRF header and a `reason`."
        ),
        responses=_errors(
            _ERROR_CSRF, _ERROR_ADMIN, _ERROR_HOLD_REASON, _ERROR_NOT_ON_HOLD, _ERROR_HOLD_AUDIT_UNAVAILABLE
        ),
    )
    async def end_audit_purge_hold(
        request: Request, body: PurgeHoldReason, _csrf: Csrf, member: PlatformAdmin, db: Db
    ) -> PurgeHoldStatus:
        reason = _validated_hold_reason(body.reason)
        # Strict, unlike starting: ending lets the next night purge for good.
        # Of two admins ending at once, the second waits on the row lock and
        # then no longer matches `ended_at IS NULL`: a 409, not an error.
        hold_id = await db.scalar(
            update(AuditPurgeHold)
            .where(AuditPurgeHold.ended_at.is_(None))
            .values(ended_at=func.now(), ended_by=member.id, end_reason=reason)
            .returning(AuditPurgeHold.id)
        )
        if hold_id is None:
            await db.rollback()
            raise ApiError(409, "AUDIT_PURGE_NOT_ON_HOLD")
        try:
            await _audit_strict(
                request, member, vocabulary.AUDIT_PURGE_HOLD_ENDED, {"hold": str(hold_id), "reason": reason}
            )
        except ApiError:
            await db.rollback()
            raise
        await db.commit()
        return PurgeHoldStatus(on_hold=False, hold=None)

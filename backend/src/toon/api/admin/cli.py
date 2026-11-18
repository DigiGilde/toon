"""CLI login from the member's side: approving a device, and the CLI sessions it made."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import ConfigDict, Field
from starlette.responses import Response

from toon import net
from toon.api.admin.common import KEY_NO_SESSION, _timestamp_schema
from toon.api.admin.contract import _ERROR_CSRF, _deleted, _errors
from toon.api.admin.dependencies import ActiveMember, Csrf, Db
from toon.api.admin.lookup import _audit
from toon.api.docs import TAG_CLI
from toon.api.errors import ApiError
from toon.api.schema import ApiModel, SessionBody, iso_utc
from toon.audit import vocabulary
from toon.audit.pseudonymisation import truncate_ip
from toon.auth import sessions
from toon.cli import service as cli
from toon.models.identity import Member
from toon.ratelimit import InMemoryCounter


class UserCodeBody(SessionBody):
    """The code from the terminal of `toon login`."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"userCode": "WDJB-MJHT"}]})

    user_code: str = Field(
        max_length=32,
        description="The user code, with or without hyphen, case-insensitive.",
        examples=["WDJB-MJHT"],
    )


class DeviceAuthorizationPendingOut(ApiModel):
    """A pending CLI login, as the approval screen shows it."""

    user_code: str = Field(description="The user code, normalised.", examples=["WDJB-MJHT"])
    client_name: str | None = Field(
        default=None, description="How the CLI named itself; `null` if it gave nothing.", examples=["toon-cli 1.2"]
    )
    ip_truncated: str | None = Field(
        default=None,
        description="The network from which the CLI started the login (IPv4 /24, IPv6 /48).",
        examples=["203.0.113.0/24"],
    )
    created_at: str | None = Field(
        default=None, description="When the CLI started the login.", json_schema_extra=_timestamp_schema()
    )
    expires_at: str | None = Field(
        default=None, description="When the code expires.", json_schema_extra=_timestamp_schema()
    )
    same_network: bool | None = Field(
        default=None,
        description=(
            "Whether the CLI started the login from the same truncated network (IPv4 /24, IPv6 /48) as the one "
            "from which the member is approving now. `false` is a reason to be extra careful: someone else may "
            "have sent the code. `null` if one of the two addresses is unknown. No full IP address "
            "is in the response."
        ),
        examples=[True],
    )


class CliSessionOut(ApiModel):
    """A linked CLI session: an approved `toon login` of the signed-in member."""

    id: uuid.UUID = Field(description="ID of the CLI session; you use it to revoke it.")
    client_name: str | None = Field(
        default=None, description="How the CLI named itself when signing in.", examples=["toon-cli 1.2 on macOS"]
    )
    created_at: str | None = Field(
        default=None, description="Time of sign-in.", json_schema_extra=_timestamp_schema()
    )
    last_used_at: str | None = Field(
        default=None,
        description="Last time the CLI did something or refreshed; `null` if that has not happened yet.",
        json_schema_extra=_timestamp_schema(),
    )
    expires_at: str | None = Field(
        default=None,
        description="When the session expires without further use.",
        json_schema_extra=_timestamp_schema(),
    )


# A fresh login, not just a valid one, may approve a CLI: the approval hands
# out a 90-day credential, so a session left open on a shared machine is not
# enough.
CLI_APPROVAL_MAX_SESSION_AGE_S = 15 * 60
# Per member, every lookup, approval and denial counts; guessing user codes
# means many lookups.
CLI_APPROVAL_MAX_ATTEMPTS = 20
CLI_APPROVAL_WINDOW_S = 600


async def _require_fresh_approval(request: Request, member: Member) -> None:
    session = sessions.session_from_request(request)
    if session is None:  # pragma: no cover - require_active_member has already refused this
        raise ApiError(401, KEY_NO_SESSION)
    # A login another site navigated the browser into does not count as fresh:
    # the IdP returns without a prompt on an existing SSO session, so a
    # phishing page could otherwise re-arm this window itself.
    if not session.self_initiated:
        raise ApiError(401, "SESSION_NOT_FRESH")
    if (datetime.now(UTC) - session.created_at).total_seconds() > CLI_APPROVAL_MAX_SESSION_AGE_S:
        raise ApiError(401, "SESSION_NOT_FRESH")
    counter = getattr(request.app.state, "cli_approval_counter", None)
    if counter is None:
        counter = InMemoryCounter()
        request.app.state.cli_approval_counter = counter
    result = await counter.increment(f"member:{member.id}", CLI_APPROVAL_WINDOW_S, time.monotonic())
    if result.count > CLI_APPROVAL_MAX_ATTEMPTS:
        raise ApiError(429, "TOO_MANY_ATTEMPTS")


def _unknown_user_code() -> ApiError:
    return ApiError(404, "USER_CODE_UNKNOWN")


_ERROR_APPROVAL = {
    401: (
        "The admin session is more than fifteen minutes old (`SESSION_NOT_FRESH`): sign in again and try "
        "again."
    ),
    404: "The code is unknown, expired or already handled (`USER_CODE_UNKNOWN`).",
    429: "Too many attempts by this member in a short time (`TOO_MANY_ATTEMPTS`).",
}


_APPROVAL_RULE = (
    "**Who can call this:** any active member, with an admin session no older than fifteen minutes and a "
    "valid CSRF header. Per member, looking up, approving and denying together count towards a limit of "
    f"{CLI_APPROVAL_MAX_ATTEMPTS} per {CLI_APPROVAL_WINDOW_S // 60} minutes."
)


def add_routes(router: APIRouter) -> None:
    # -- CLI login: the member's side --

    @router.post(
        "/cli/device-authorizations/lookup",
        tags=[TAG_CLI],
        summary="Look up a pending CLI login",
        response_description="What the approval screen shows.",
        description=(
            "Looks up the pending CLI login behind a user code, so the admin interface can show "
            "which program is requesting access, when and from which network. A POST and not a query parameter, "
            "so the code does not end up in log lines.\n\n" + _APPROVAL_RULE
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_APPROVAL),
    )
    async def lookup_device_authorization(
        request: Request, body: UserCodeBody, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> DeviceAuthorizationPendingOut:
        await _require_fresh_approval(request, member)
        authorization = await cli.pending_by_user_code(db, body.user_code)
        if authorization is None:
            raise _unknown_user_code()
        approver_ip = net.client_ip_from_request(request)
        same_network = None
        if authorization.ip_truncated and approver_ip:
            same_network = truncate_ip(approver_ip) == authorization.ip_truncated
        return DeviceAuthorizationPendingOut(
            same_network=same_network,
            user_code=cli.format_user_code(cli.normalise_user_code(body.user_code) or ""),
            client_name=authorization.client_name,
            ip_truncated=authorization.ip_truncated,
            created_at=iso_utc(authorization.created_at),
            expires_at=iso_utc(authorization.expires_at),
        )

    @router.post(
        "/cli/device-authorizations/approve",
        status_code=204,
        tags=[TAG_CLI],
        summary="Approve a CLI login",
        description=(
            "Approves the CLI login behind this user code for the signed-in member. The CLI then fetches "
            "its tokens itself and from now on acts as this member, with exactly their roles. Only do this if "
            "you just started `toon login` yourself.\n\n" + _APPROVAL_RULE
        ),
        responses=_deleted("Approved.") | _errors(_ERROR_CSRF, _ERROR_APPROVAL),
    )
    async def approve_device_authorization(
        request: Request, body: UserCodeBody, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        await _require_fresh_approval(request, member)
        authorization = await cli.decide(db, body.user_code, member, approve=True)
        if authorization is None:
            raise _unknown_user_code()
        await _audit(
            request,
            member,
            vocabulary.CLI_LOGIN,
            {"via": vocabulary.VIA_CLI, "device_authorization": str(authorization.id)},
        )
        return Response(status_code=204)

    @router.post(
        "/cli/device-authorizations/deny",
        status_code=204,
        tags=[TAG_CLI],
        summary="Deny a CLI login",
        description=(
            "Denies the CLI login behind this user code; the CLI gets `ACCESS_DENIED`.\n\n"
            + _APPROVAL_RULE
        ),
        responses=_deleted("Denied.") | _errors(_ERROR_CSRF, _ERROR_APPROVAL),
    )
    async def deny_device_authorization(
        request: Request, body: UserCodeBody, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        await _require_fresh_approval(request, member)
        authorization = await cli.decide(db, body.user_code, member, approve=False)
        if authorization is None:
            raise _unknown_user_code()
        await _audit(
            request,
            member,
            vocabulary.CLI_LOGIN_DENIED,
            {"via": vocabulary.VIA_CLI, "device_authorization": str(authorization.id)},
        )
        return Response(status_code=204)

    @router.get(
        "/me/cli-sessions",
        tags=[TAG_CLI],
        summary="My linked CLI sessions",
        response_description="The CLI sessions of the signed-in member, newest first.",
        description=(
            "Every `toon login` that is still active, newest first. Expired sessions are no longer listed.\n\n"
            "**Who can call this:** any active member, for their own sessions."
        ),
        responses=_errors(),
    )
    async def my_cli_sessions(member: ActiveMember, db: Db) -> list[CliSessionOut]:
        return [
            CliSessionOut(
                id=session.id,
                client_name=session.client_name,
                created_at=iso_utc(session.created_at),
                last_used_at=iso_utc(session.last_used_at),
                expires_at=iso_utc(min(session.expires_at, session.max_expires_at)),
            )
            for session in await cli.sessions_of(db, member.id)
        ]

    @router.delete(
        "/me/cli-sessions/{session_id}",
        status_code=204,
        tags=[TAG_CLI],
        summary="Revoke a linked CLI session",
        description=(
            "Revokes a CLI session; whoever used it has to run `toon login` again afterwards.\n\n"
            "**Who can call this:** any active member, for their own sessions, with a valid CSRF header."
        ),
        responses=_deleted("The CLI session has been revoked.")
        | _errors(_ERROR_CSRF, {404: "You have no CLI session with this ID (`CLI_SESSION_UNKNOWN`)."}),
    )
    async def revoke_my_cli_session(
        request: Request, session_id: uuid.UUID, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        if not await cli.revoke(db, session_id, member_id=member.id):
            raise ApiError(404, "CLI_SESSION_UNKNOWN")
        await _audit(
            request, member, vocabulary.CLI_SESSION_REVOKE, {"via": vocabulary.VIA_CLI, "cli_session": str(session_id)}
        )
        return Response(status_code=204)

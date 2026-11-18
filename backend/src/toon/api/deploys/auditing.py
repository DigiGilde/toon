"""The audit rows the deploy endpoints write themselves."""

from __future__ import annotations

import logging

from fastapi import Request

from toon.audit import request as audit_request
from toon.audit.log import Actor

# Named after the package, not this module: the deploy API logs as one.
_logger = logging.getLogger(__package__)

AUDIT_ACTION_DEPLOY = "deploy"
AUDIT_ACTION_PREVIEW_TEARDOWN = "preview_teardown"
# reason_code for a deploy that failed on something other than a refusal, for
# instance a full disk. The client sees the generic 500; the log keeps the row.
AUDIT_REASON_INTERNAL = "INTERNAL_ERROR"


async def _audit(
    request: Request,
    actor: Actor,
    action: str,
    result: str,
    reason_code: str | None,
    refs: dict,
) -> None:
    # The ApiError handler audits refusals generically; this endpoint writes a
    # richer record of its own (CI or member actor, group, site), so it says so.
    request.state.audit_written = True
    await audit_request.write(request, action, actor, result, reason_code=reason_code, refs=refs)


async def _audit_best_effort(
    request: Request,
    actor: Actor,
    action: str,
    result: str,
    reason_code: str | None,
    refs: dict,
) -> None:
    """`_audit` for use inside an exception handler. AuditLog.write is
    fail-open, but the steps around it are not, and a raise here would bury
    the exception that is already on its way out."""
    try:
        await _audit(request, actor, action, result, reason_code, refs)
    except Exception:
        _logger.exception("audit row for the failed deploy could not be written")

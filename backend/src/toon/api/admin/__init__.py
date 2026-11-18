"""Session API for the admin SPA on `/-/api/v1`.

Every route runs behind `require_admin_origin` (origin separation) and
`require_active_member`; mutations additionally demand the CSRF double-submit
header. Errors are problem+json through the handlers from `api/errors.py`.
The paths mirror `frontend/src/api/toon.ts`.

Every route declares its response as a pydantic model on the ApiModel base
(lowerCamelCase on the wire), a `summary`/`description`, and through
`error_responses()` the problem+json errors it can return; that is what the
API documentation on `/-/api/docs` lives off.

The two deploy endpoints (`POST .../deploys` and `DELETE .../previews/{ref}`)
deliberately do NOT live in this router: `api/deploys.py` serves those paths
for bearer and session at once. main.py puts `require_admin_origin` on that
router too (bearer CI sends no Origin/Sec-Fetch-Site and passes it
unhindered).

Some routes here take a bearer as well: creating a group, creating a site,
linking a repository and the feedback routes the CLI uses (reading feedback,
setting its status, replying to it) accept the CLI token from `toon login`
(`require_creator`, `require_member_or_cli`), with the same role checks as
the session. A bearer request carries no ambient credentials, so it skips the CSRF check; the
origin guard stays on it, as on the deploy router. Every other route in this router is session only, which
`BearerOutsideDeploysMiddleware` enforces before the request gets here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from toon.api.admin import cli, me, overview
from toon.api.admin.common import KEY_ADMIN_ONLY, KEY_CSRF_INVALID, KEY_NO_SESSION, KEY_NOT_GROUP_MEMBER
from toon.api.admin.dependencies import require_csrf
from toon.api.admin.feedback import add_feedback_routes
from toon.api.admin.groups import group
from toon.api.admin.groups import members as group_members
from toon.api.admin.platform import audit
from toon.api.admin.platform import members as platform_members
from toon.api.admin.sites import invitees, keys, previews, repository, site, versions
from toon.api.admin.sites import members as site_members
from toon.api.origin_guard import require_admin_origin


def make_admin_router() -> APIRouter:
    router = APIRouter(prefix="/-/api/v1", dependencies=[Depends(require_admin_origin)])

    # One router, not one per area: include_router no longer flattens, so the
    # areas add their routes to this one. The order of the calls is the order
    # of the paths in the OpenAPI schema and the order in which a request is
    # matched against the routes.
    me.add_routes(router)
    overview.add_routes(router)
    group.add_routes(router)
    site.add_routes(router)
    invitees.add_routes(router)
    keys.add_routes(router)
    repository.add_routes(router)
    cli.add_routes(router)
    group_members.add_routes(router)
    platform_members.add_routes(router)
    audit.add_routes(router)
    site_members.add_routes(router)
    versions.add_routes(router)
    previews.add_routes(router)
    add_feedback_routes(router)

    # NB: POST .../deploys and DELETE .../previews/{ref} live in api/deploys.py
    # (bearer as well as session); see the module docstring.

    return router


__all__ = [
    "KEY_ADMIN_ONLY",
    "KEY_CSRF_INVALID",
    "KEY_NOT_GROUP_MEMBER",
    "KEY_NO_SESSION",
    "make_admin_router",
    "require_csrf",
]

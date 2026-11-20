"""Deploy API: live/preview deploys and preview teardown.

Auth is one of three:

- a CI ID token (`Authorization: Bearer <JWT>` from GitHub or Forgejo
  Actions), verified by ci/tokens.py and matched against the site's linked
  repository by ci/trust.py; a token bound to a site is used on that site
  or not at all (`_target_site`);
- a CLI access token (`Authorization: Bearer tooncli_...` from `toon login`),
  which acts as its member with exactly that member's roles;
- an admin session with CSRF, for the upload in the SPA.

Bearer is accepted on these two endpoints, the two CLI session endpoints and,
with a CLI token only, on the admin routes that create a group or a site, link
a repository, turn a site's shielding on or off, or read, mark or answer
feedback (`accepts_bearer`), and nowhere
else: `BearerOutsideDeploysMiddleware` rejects any other request carrying a
Bearer Authorization header with a 401.
main.py registers router and middleware; the app supplies on app.state:
settings, session_factory, session_store, content_store and audit_log.

The upload is never read into memory: the multipart body streams chunk by
chunk into a spool file in `{content_root}/_tmp/` (outside any servable
path), with the body limit checked incrementally; the ingest then unpacks
from that file.
"""

from toon.api.deploys.auditing import AUDIT_ACTION_DEPLOY, AUDIT_ACTION_PREVIEW_TEARDOWN, AUDIT_REASON_INTERNAL
from toon.api.deploys.auth import ci_error, cli_member
from toon.api.deploys.bearer import (
    CLI_SESSION_PATH,
    CLI_WHOAMI_PATH,
    GROUP_CREATE_PATH,
    BearerOutsideDeploysMiddleware,
    accepts_bearer,
    bearer_from_request,
    is_creation_endpoint,
    is_deploy_endpoint,
    is_feedback_cli_endpoint,
    is_repository_link_endpoint,
    is_shielding_endpoint,
)
from toon.api.deploys.routes import delete_preview, deploy, router
from toon.api.deploys.schemas import DeployResult
from toon.api.deploys.upload import BASE_PATH_FIELD, FILE_FIELD, MAX_FIELD_BYTES, MAX_FIELDS, PREVIEW_FIELD

__all__ = [
    "AUDIT_ACTION_DEPLOY",
    "AUDIT_ACTION_PREVIEW_TEARDOWN",
    "AUDIT_REASON_INTERNAL",
    "BASE_PATH_FIELD",
    "CLI_SESSION_PATH",
    "CLI_WHOAMI_PATH",
    "FILE_FIELD",
    "GROUP_CREATE_PATH",
    "MAX_FIELDS",
    "MAX_FIELD_BYTES",
    "PREVIEW_FIELD",
    "BearerOutsideDeploysMiddleware",
    "DeployResult",
    "accepts_bearer",
    "bearer_from_request",
    "ci_error",
    "cli_member",
    "delete_preview",
    "deploy",
    "is_creation_endpoint",
    "is_deploy_endpoint",
    "is_feedback_cli_endpoint",
    "is_repository_link_endpoint",
    "is_shielding_endpoint",
    "router",
]

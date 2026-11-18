"""The documentation the deploy endpoints share: the request body, the result and the errors."""

from __future__ import annotations

import uuid

from pydantic import Field

from toon.api.deploys.upload import BASE_PATH_FIELD, FILE_FIELD, PREVIEW_FIELD
from toon.api.schema import AccessOut, ApiModel
from toon.constants import SLUG_RE

_DEPLOY_OPENAPI = {
    "requestBody": {
        "required": True,
        "description": (
            "The bundle to publish, as `multipart/form-data`. The body is processed as a stream, so a large "
            "bundle never has to fit in memory."
        ),
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": [FILE_FIELD],
                    "properties": {
                        FILE_FIELD: {
                            "type": "string",
                            "format": "binary",
                            "description": (
                                "The `file` field, required and exactly once. Either a single "
                                "`.html` file (which becomes the site's `index.html`) or a `.zip`, "
                                "`.tar.gz` or `.tgz` archive. The format is recognised by the file name; anything "
                                "else yields 422 `UNKNOWN_FORMAT`. Paths in the archive must be relative and "
                                "safe: absolute paths, `..`, symlinks and the reserved top-level segments "
                                "`_preview` and `_version` are refused."
                            ),
                        },
                        PREVIEW_FIELD: {
                            "type": "string",
                            "pattern": SLUG_RE.pattern,
                            "examples": ["pr-42"],
                            "description": (
                                "Optional. With this field the bundle becomes a preview under this ref, instead "
                                "of the live site. The ref is a slug (lowercase letters, digits, hyphens, "
                                "at most 63 characters) and is usually the pull request number. If the preview "
                                "already exists, it is replaced and its expiry moves forward. Omit it for "
                                "a live deploy."
                            ),
                        },
                        BASE_PATH_FIELD: {
                            "type": "string",
                            "examples": ["dist"],
                            "description": (
                                "Optional. The directory inside the archive that becomes the root of the site; "
                                "everything next to it is not published. The path is relative to the root "
                                "directory after unwrapping enclosing directories and must be an existing directory "
                                "containing an `index.html`. Meant to confirm one of the suggestions a "
                                "refused deploy returns in `indexCandidates`; without this field "
                                "Toon determines the root directory itself."
                            ),
                        },
                    },
                },
                "encoding": {
                    FILE_FIELD: {"contentType": "text/html, application/zip, application/gzip"},
                },
            }
        },
    }
}


class DeployResult(ApiModel):
    """What CI gets back after a successful deploy."""

    version_id: uuid.UUID = Field(
        description=(
            "ID of the version that was just created. It identifies the deploy in the site's version list, "
            "and it is what you roll back to later."
        )
    )
    url: str = Field(
        description=(
            "Where the deploy can be viewed: the live site, or for a preview the preview URL. CI can post a "
            "link to it, in a pull request for example."
        ),
        examples=["https://toon.example/team-aurora/website/_preview/pr-42/"],
    )
    access: AccessOut = Field(
        description=(
            "Who may see the deploy: for a preview the preview's own access if one is set, "
            "otherwise the site's. CI can use this to tell, alongside the link, whether signing in is needed."
        )
    )


# Authorization is identical for both deploy endpoints; only the occasion
# differs, so they share these error descriptions.
_DEPLOY_ERRORS = {
    401: (
        "Neither a bearer token nor a valid admin session was sent (`NO_AUTHENTICATION`), the "
        "CLI token is invalid, revoked or expired (`TOKEN_INVALID`), or the CI token is refused: "
        "it does not come from GitHub or a configured Forgejo (`CI_ISSUER_UNKNOWN`), the signature is "
        "invalid or the token is expired or not yet valid (`CI_TOKEN_INVALID`), or the audience is neither "
        "exactly the admin URL nor exactly the admin URL followed by `/-/sites/` and a site id, or it names "
        "a site that the token's repository is not linked to, whatever the address (`CI_AUDIENCE_MISMATCH`). "
        "The response then carries `WWW-Authenticate: Bearer`."
    ),
    403: (
        "With a CI token: the repository is not linked to this site (`CI_REPOSITORY_NOT_TRUSTED`), the "
        "token names no site id while the link of this site requires one (`CI_SITE_ID_REQUIRED`), or a "
        "live deploy does not come from `push`, `workflow_dispatch` or `schedule`, or not from the live branch "
        "(`CI_BRANCH_NOT_ALLOWED`). With a CLI token or session: the "
        "member does not have at least the `editor` role on this site (`INSUFFICIENT_ROLE`) or is not active "
        "(`MEMBER_NOT_ACTIVE`); with a session also: the CSRF header is missing or wrong (`CSRF_INVALID`). "
        "A request from an origin other than the admin host is refused as well; CI and the CLI send "
        "no `Origin` and pass that check."
    ),
    404: "Unknown group or unknown site (`UNKNOWN_SITE`).",
    409: (
        "The CI token is bound to a site that is not at this address, and the token's repository is linked to "
        "that site: the `site:` of the workflow is old or mistyped (`SITE_MOVED`). The `detail` names the "
        "current address of the site the token is bound to; nothing is published."
    ),
    429: "The rate limit budget is used up; try again later.",
    503: (
        "The CI provider cannot be reached to fetch the keys or to check the repository "
        "(`CI_PROVIDER_UNREACHABLE`); try again later."
    ),
}

"""The two routes: a deploy, live or as a preview, and a preview teardown."""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import Response

from toon.access.gate import effective_access
from toon.api.deploys.auditing import (
    AUDIT_ACTION_DEPLOY,
    AUDIT_ACTION_PREVIEW_TEARDOWN,
    AUDIT_REASON_INTERNAL,
    _audit,
    _audit_best_effort,
)
from toon.api.deploys.auth import _auth_refs, _authenticate, _authorize, _deployer, _target_site, ci_error
from toon.api.deploys.schemas import _DEPLOY_ERRORS, _DEPLOY_OPENAPI, DeployResult
from toon.api.deploys.upload import BASE_PATH_FIELD, PREVIEW_FIELD, _spool_upload, _Upload
from toon.api.docs import TAG_DEPLOYS
from toon.api.errors import ApiError, error_responses
from toon.api.schema import AccessOut
from toon.audit.log import ANONYMOUS
from toon.ci import trust
from toon.ci.tokens import CiTokenError
from toon.constants import SLUG_RE
from toon.ingest.service import IngestError, IngestService
from toon.ingest.store import ContentStore
from toon.ingest.unpacker import BundleError
from toon.models.publication import Preview

# Named after the package, not this module: the deploy API logs as one.
_logger = logging.getLogger(__package__)

router = APIRouter(prefix="/-/api/v1")


@router.post(
    "/sites/{group_slug}/{site_slug}/deploys",
    status_code=201,
    openapi_extra=_DEPLOY_OPENAPI,
    tags=[TAG_DEPLOYS],
    summary="Publish a bundle, live or as a preview",
    response_description="The bundle was unpacked and published; the ID of the new version is returned.",
    description=(
        "The endpoint CI uses. Send the built site as `multipart/form-data` with the "
        "`file` field; without the `preview` field the bundle replaces the live site, with `preview` it ends up under "
        "`/{groupSlug}/{siteSlug}/_preview/{ref}/`.\n\n"
        "**Who can call this:** any of three callers. (1) A CI ID token from GitHub or Forgejo Actions "
        "(`Authorization: Bearer <JWT>`) from the repository linked to this site, whose audience is exactly "
        "Toon's admin URL followed by `/-/sites/` and the `id` of this site, or, while the link "
        "still accepts it, exactly the admin URL itself; a live deploy must then come from a `push`, "
        "`workflow_dispatch` or `schedule`, and from the live branch if one is set; a preview may come from any "
        "branch. A token bound to another site is never used here: its own repository gets 409 `SITE_MOVED` "
        "with the address of that site. "
        "(2) A CLI token from `toon login` "
        "(`Authorization: Bearer tooncli_...`): it acts as the member who signed in, with exactly their "
        "roles. (3) An admin session plus CSRF header. With (2) and (3) the member must be active and have at "
        "least the `editor` role on this site. This endpoint and the preview teardown are, together with the "
        "CLI session endpoints, the creation of a group or site, the linking of a repository and reading, marking "
        "and answering feedback, the only ones that accept a Bearer token; elsewhere that header yields 401.\n\n"
        "**Flow:** authorization comes first, only then is the body read, so a refused "
        "request costs no upload. The upload streams to disk and is unpacked against the limits "
        "below. Every deploy, whether it succeeds or is refused, ends up in the audit log.\n\n"
        "**Site root:** enclosing directories are repeatedly unwrapped while the root directory contains exactly "
        "one directory and nothing else, so an archive with only `mysite/dist/index.html` simply lands on the "
        "site root. Operating system metadata does not count and is not published either: "
        "the `__MACOSX` directory, `.DS_Store` and the AppleDouble files that start with `._`. "
        "A zip you make with a right-click in Finder therefore just works. "
        "After that there must be an `index.html` in the root directory; otherwise the response is 422 `NO_INDEX`, "
        "with the index paths found in `indexCandidates`. If the site is in a directory "
        "next to other things (a zipped project directory), send that directory as the `basePath` field: this "
        "confirms one of the suggestions. Toon never picks one of several candidates itself, because it "
        "would silently leave out files you thought you were publishing. The `basePath` is relative to the "
        "root directory after unwrapping, but a path that includes the unwrapped prefix works too, and "
        "a fixed value keeps working if unwrapping has already taken that directory away.\n\n"
        "**Limits** (configurable; these are the defaults): the request body is at most 100 MB "
        "(`TOON_INGEST_MAX_BODY`), a single unpacked file 50 MB (`TOON_INGEST_MAX_FILE`), the entire "
        "unpacked site 200 MB (`TOON_INGEST_MAX_TOTAL`), with at most 1000 entries "
        "(`TOON_INGEST_MAX_FILES`) and 10 levels of directory depth (`TOON_INGEST_MAX_DEPTH`). These apply to "
        "what is published: what falls outside the `basePath` does not count. The archive as a whole may "
        "contain at most fifty times as many entries, and for a `.tar.gz` the unpacked size of "
        "all members counts, including the unpublished ones: a tar has no index, so getting to the next "
        "header means decompressing everything in between. The limits are enforced during unpacking and "
        "the headers are checked against the same limits beforehand, so a zip or tar bomb does not get "
        "past either.\n\n"
        "**Example** (`$TOON_TOKEN` is a CI ID token or a CLI token):\n\n"
        "```\n"
        "curl --fail --silent --show-error \\\n"
        '  --header "Authorization: Bearer $TOON_TOKEN" \\\n'
        "  --form file=@dist.zip \\\n"
        "  --form preview=pr-42 \\\n"
        '  "$TOON_ADMIN_URL/-/api/v1/sites/aurora/docs/deploys"\n'
        "```\n\n"
        'Response: `201` with `{"versionId": "..."}`. Omit `--form preview=...` for a live deploy.'
    ),
    responses=error_responses(
        {
            **_DEPLOY_ERRORS,
            400: "The client aborted the upload before the body was complete (`CLIENT_ABORTED`).",
            503: (
                f"{_DEPLOY_ERRORS[503]} Or the content volume has too little free space for this upload "
                "(`STORAGE_UNAVAILABLE`): checked in advance against the declared `Content-Length`, and again while "
                "receiving and unpacking, so the volume never drops below the configured margin of free "
                "space. Whatever was already written is then cleaned up; try again later."
            ),
            413: (
                "The upload is larger than the body limit (`BODY_TOO_LARGE`), or the bundle unpacks too "
                "large: `FILE_TOO_LARGE`, `TOTAL_TOO_LARGE` or `TOO_MANY_FILES` (the last one also "
                "when the archive as a whole has too many entries; the message says which of the two, "
                "and carries `indexCandidates` where possible, because a zipped project directory runs into "
                "this first)."
            ),
            422: (
                "The request is not multipart/form-data (`NOT_MULTIPART`), the `file` field is missing "
                "(`FILE_MISSING`), the multipart structure is wrong (`MULTIPART_INVALID`), `preview` is "
                "not a valid slug (`PREVIEW_REF_INVALID`), or the bundle is refused: unknown format "
                "(`UNKNOWN_FORMAT`), unreadable or empty archive (`INVALID_ARCHIVE`, `EMPTY_ARCHIVE`), "
                "an unsafe path in it (`PATH_TRAVERSAL`, `ABSOLUTE_PATH`, `SYMLINK_REFUSED`, "
                "`HARDLINK_REFUSED`, `SPECIAL_FILE`, `RESERVED_SEGMENT`, `TOO_DEEP`, `DUPLICATE_PATH`, "
                "`NULL_BYTE`, `EMPTY_PATH`, `PATH_TOO_LONG`), a file that does not belong on a website "
                "(`SECRET_FILE`: "
                "a `.git` directory or an `.env` file, which come along automatically with a zipped project "
                "directory), no `index.html` in the root directory (`NO_INDEX`, with the paths found "
                "in `indexCandidates`), or a `basePath` that is not valid (`BASE_PATH_INVALID`), is not a "
                "directory in the bundle (`BASE_PATH_UNKNOWN`, also when the path points to a file) or "
                "contains no `index.html` (`BASE_PATH_WITHOUT_INDEX`, also when that directory contains no "
                "files at all)."
            ),
        }
    ),
)
async def deploy(request: Request, group_slug: str, site_slug: str) -> DeployResult:
    settings = request.app.state.settings
    store: ContentStore = request.app.state.content_store
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    refs: dict = {"group": group_slug, "site": site_slug}
    actor = ANONYMOUS
    upload: _Upload | None = None
    try:
        # Decide first, read the body only after: a rejected request costs no
        # disk and no reading.
        async with factory() as db:
            auth = await _authenticate(request, db)
            actor = auth.actor
            refs.update(_auth_refs(auth))
            group, site = await _target_site(request, db, auth, group_slug, site_slug, refs)
            auth = await _authorize(request, db, auth, group_slug, site)
            actor = auth.actor

        service = IngestService(store, settings)
        # Before the body is read: a volume without room costs no upload. A
        # declared size above the body limit is the spool's 413 to give.
        length = request.headers.get("content-length", "")
        declared = int(length) if length.isdigit() else 0
        service.check_room(min(declared, settings.ingest_max_body))

        upload = await _spool_upload(request, store, settings.ingest_max_body, service.room_guard())
        preview = upload.fields.get(PREVIEW_FIELD)
        base_path = upload.fields.get(BASE_PATH_FIELD)
        # Both fields are attacker-controlled up to MAX_FIELD_BYTES, so they
        # are capped like a CI claim before they reach the log.
        if base_path is not None:
            refs["base_path"] = base_path[: trust.MAX_CLAIM_LENGTH]
        if preview is not None:
            if not SLUG_RE.fullmatch(preview):
                raise ApiError(422, "PREVIEW_REF_INVALID")
            refs["preview"] = preview[: trust.MAX_CLAIM_LENGTH]
        elif auth.ci is not None and auth.ci.repository is not None:
            try:
                trust.check_live_deploy(auth.ci.repository, auth.ci.token)
            except CiTokenError as error:
                raise ci_error(error) from None
    except (ApiError, IngestError) as error:
        if upload is not None:
            upload.spool.unlink(missing_ok=True)
        await _audit(request, actor, AUDIT_ACTION_DEPLOY, "refused", error.reason, refs)
        raise
    except Exception:
        if upload is not None:
            upload.spool.unlink(missing_ok=True)
        _logger.exception("deploy failed before the ingest")
        await _audit_best_effort(request, actor, AUDIT_ACTION_DEPLOY, "refused", AUDIT_REASON_INTERNAL, refs)
        raise

    deployer = _deployer(auth)
    try:
        async with factory() as db:
            if preview is not None:
                version_id = await service.preview_deploy(
                    db, site, preview, upload.filename, upload.spool, deployer, base_path
                )
            else:
                version_id = await service.deploy(
                    db, site, upload.filename, upload.spool, deployer, base_path
                )
            preview_row = (
                await db.scalar(select(Preview).where(Preview.site_id == site.id, Preview.ref == preview))
                if preview is not None
                else None
            )
    except (BundleError, IngestError) as error:
        await _audit(request, actor, AUDIT_ACTION_DEPLOY, "refused", error.reason, refs)
        raise
    except Exception:
        _logger.exception("deploy failed during the ingest")
        await _audit_best_effort(request, actor, AUDIT_ACTION_DEPLOY, "refused", AUDIT_REASON_INTERNAL, refs)
        raise
    finally:
        upload.spool.unlink(missing_ok=True)

    await _audit(
        request,
        actor,
        AUDIT_ACTION_DEPLOY,
        "allowed",
        None,
        {**refs, "version_id": str(version_id)},
    )
    url = f"{settings.content_base_url.rstrip('/')}/{quote(group.slug)}/{quote(site.slug)}/"
    if preview is not None:
        url += f"_preview/{quote(preview)}/"
    access = effective_access(site, preview_row)
    return DeployResult(
        version_id=version_id,
        url=url,
        access=AccessOut(base=access.base, keys=access.keys, invitees=access.invitees),
    )


@router.delete(
    "/sites/{group_slug}/{site_slug}/previews/{ref}",
    status_code=204,
    tags=[TAG_DEPLOYS],
    summary="Clean up a preview",
    description=(
        "Removes the preview with this ref, including its files. Meant for the CI step that runs when "
        "a pull request closes. The live site and the version history are left alone.\n\n"
        "**Who can call this:** the same as the deploy: a CI ID token from the linked repository (from any branch), "
        "a CLI token or an admin session with CSRF header of an active member with effective site role "
        "`editor` or higher.\n\n"
        "**Idempotent:** a ref that does not (any more) exist also yields 204, so a repeated cleanup step in CI "
        "does not fail on a rerun.\n\n"
        "**Example:**\n\n"
        "```\n"
        "curl --fail --silent --show-error --request DELETE \\\n"
        '  --header "Authorization: Bearer $TOON_TOKEN" \\\n'
        '  "$TOON_ADMIN_URL/-/api/v1/sites/aurora/docs/previews/pr-42"\n'
        "```"
    ),
    responses={204: {"description": "The preview no longer exists. No content is returned."}}
    | error_responses(
        {**_DEPLOY_ERRORS, 422: "The ref in the path is not a valid slug (`PREVIEW_REF_INVALID`)."}
    ),
)
async def delete_preview(request: Request, group_slug: str, site_slug: str, ref: str) -> Response:
    settings = request.app.state.settings
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    refs = {"group": group_slug, "site": site_slug, "preview": ref}
    actor = ANONYMOUS
    try:
        async with factory() as db:
            auth = await _authenticate(request, db)
            actor = auth.actor
            refs.update(_auth_refs(auth))
            if not SLUG_RE.fullmatch(ref):
                raise ApiError(422, "PREVIEW_REF_INVALID")
            _, site = await _target_site(request, db, auth, group_slug, site_slug, refs)
            auth = await _authorize(request, db, auth, group_slug, site)
            actor = auth.actor
    except ApiError as error:
        await _audit(request, actor, AUDIT_ACTION_PREVIEW_TEARDOWN, "refused", error.reason, refs)
        raise

    service = IngestService(request.app.state.content_store, settings)
    try:
        async with factory() as db:
            await service.delete_preview(db, site, ref)
    except Exception:
        _logger.exception("preview teardown failed")
        await _audit_best_effort(
            request, actor, AUDIT_ACTION_PREVIEW_TEARDOWN, "refused", AUDIT_REASON_INTERNAL, refs
        )
        raise

    await _audit(request, actor, AUDIT_ACTION_PREVIEW_TEARDOWN, "allowed", None, refs)
    return Response(status_code=204)

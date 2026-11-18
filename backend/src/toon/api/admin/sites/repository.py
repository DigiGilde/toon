"""The repository linked to a site, which CI may publish from."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from toon.api.admin.common import _timestamp_schema
from toon.api.admin.contract import _ERROR_CLI_TOKEN, _ERROR_CSRF, _ERROR_SITE, _ERROR_SITE_ROLE, _deleted, _errors
from toon.api.admin.dependencies import ActiveMember, Creator, Csrf, Db, require_creator
from toon.api.admin.lookup import _audit, _site_with_role, _write_one_or_404
from toon.api.docs import TAG_CI
from toon.api.errors import ApiError
from toon.api.schema import ApiModel, SessionBody, iso_utc
from toon.ci.providers import (
    GITHUB_HOST,
    ProviderClient,
    ProviderUnavailableError,
    RepositoryNotFoundError,
    ResolvedRepository,
    host_label,
    valid_name,
)
from toon.config import normalise_https_base_url
from toon.constants import Role
from toon.models.ci import CiProvider, SiteRepository
from toon.models.identity import Member


class SiteIdRequiredBody(SessionBody):
    """Whether the linked repository may publish only with a CI ID token bound to this site."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"siteIdRequired": True}]})

    site_id_required: bool = Field(
        description=(
            "`true`: from now on only a CI ID token whose audience names the id of this site may publish. "
            "`false` is refused once that holds: a link that requires the site id keeps requiring it."
        )
    )


LIVE_BRANCH_MAX_LENGTH = 255


class SiteRepositoryBody(ApiModel):
    """The repository from which CI may publish to this site."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"provider": "github", "owner": "minbzk", "repo": "website", "liveBranch": "main"},
                {
                    "provider": "forgejo",
                    "host": "https://code.overheid.nl",
                    "owner": "minbzk",
                    "repo": "website",
                    "liveBranch": None,
                },
            ]
        }
    )

    provider: CiProvider = Field(description="`github` or `forgejo`.", examples=["github"])
    host: str | None = Field(
        default=None,
        description=(
            "Base URL of the Forgejo instance, one of `ciForgejoHosts` from `GET /me`. Required for "
            "`forgejo`; omit for `github` (or `https://github.com`)."
        ),
        examples=["https://code.overheid.nl"],
    )
    owner: str = Field(description="Owner of the repository: user or organisation.", examples=["minbzk"])
    repo: str = Field(description="Name of the repository.", examples=["website"])
    live_branch: str | None = Field(
        description=(
            "The only branch allowed to publish live, without `refs/heads/`. `null` or empty: any branch may "
            "go live. Live is in any case only possible from `push`, `workflow_dispatch` or `schedule`. "
            "Previews and their cleanup are always allowed from any branch."
        ),
        examples=["main"],
    )
    repository_id: int | None = Field(
        default=None,
        description=(
            "Numeric ID of the repository, only needed if Toon cannot look it up because it is private. "
            "Together with `ownerId`, or omit both. Can be retrieved with "
            "`gh api repos/{owner}/{repo} --jq '.id, .owner.id'`."
        ),
        examples=[123456],
    )
    owner_id: int | None = Field(
        default=None,
        description="Numeric ID of the owner, together with `repositoryId`.",
        examples=[7890],
    )


class SiteRepositoryOut(ApiModel):
    """The linked repository of a site."""

    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    site_slug: str = Field(description="Slug of the site.", examples=["docs"])
    site_id: uuid.UUID = Field(
        description=(
            "Fixed id of the site. A workflow names it as `site-id` (action) or `--site-id` (CLI); the "
            "CI ID token then names it in its audience."
        )
    )
    provider: CiProvider = Field(description="`github` or `forgejo`.")
    host: str = Field(description="Base URL of the provider.", examples=["https://github.com"])
    owner: str = Field(description="Owner as the provider spells it.", examples=["minbzk"])
    repo: str = Field(description="Repository as the provider spells it.", examples=["website"])
    repository_id: int = Field(
        description="Numeric ID of the repository at the provider; survives a rename.", examples=[123456]
    )
    owner_id: int = Field(description="Numeric ID of the owner at the provider.", examples=[7890])
    live_branch: str | None = Field(
        default=None, description="The only branch allowed to publish live, or `null`: any branch.", examples=["main"]
    )
    ids_confirmed: bool = Field(
        description=(
            "Whether the IDs are confirmed: by the provider when linking, or by a CI ID token that carried both "
            "of them. `false` while they have only been entered by hand; the name may then also still differ."
        ),
        examples=[True],
    )
    site_id_required: bool = Field(
        description=(
            "Whether only a CI ID token bound to this site may publish: its audience is the admin URL followed "
            "by `/-/sites/` and `siteId`. `true` for every link made since the site id exists, for one that got "
            "another repository since, and for an older link of a repository that was linked to several sites; "
            "`false` for any other older link, which also accepts a token whose audience is the admin URL itself "
            "until a site admin requires the site id "
            "(`PUT /sites/{groupSlug}/{siteSlug}/repository/site-id-required`). Once `true`, it stays `true`."
        ),
        examples=[True],
    )
    created_by: str = Field(
        description=(
            "Name or e-mail address of the member who linked the repository; empty if that member has been "
            "deleted."
        )
    )
    created_at: str | None = Field(
        default=None, description="Time of linking.", json_schema_extra=_timestamp_schema()
    )


def _live_branch(value: str | None) -> str | None:
    """A branch name as git allows it, minus the edge cases that would only
    ever be a mistake here; empty means every branch."""
    if value is None or not value.strip():
        return None
    branch = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-")
    if (
        len(branch) > LIVE_BRANCH_MAX_LENGTH
        or not set(branch) <= allowed
        or branch.startswith(("/", "-", ".", "refs/"))
        or branch.endswith(("/", ".", ".lock"))
        or ".." in branch
        or "//" in branch
    ):
        raise ApiError(422, "LIVE_BRANCH_INVALID")
    return branch


# site_repositories stores both ids as a BigInteger.
_MAX_PROVIDER_ID = 2**63 - 1


def _entered_ids(body: SiteRepositoryBody) -> tuple[int, int] | None:
    """The repository and owner id the admin entered, both or neither."""
    repository_id, owner_id = body.repository_id, body.owner_id
    if repository_id is None and owner_id is None:
        return None
    if (
        repository_id is None
        or owner_id is None
        or not 0 < repository_id <= _MAX_PROVIDER_ID
        or not 0 < owner_id <= _MAX_PROVIDER_ID
    ):
        raise ApiError(422, "REPOSITORY_IDS_INVALID")
    return repository_id, owner_id


def _lookup_failed(error: RepositoryNotFoundError | ProviderUnavailableError, where: dict[str, str]) -> ApiError:
    if isinstance(error, RepositoryNotFoundError):
        return ApiError(422, "REPOSITORY_NOT_FOUND", params=where)
    if error.rate_limited:
        return ApiError(503, "CI_PROVIDER_RATE_LIMITED", params={"host": where["host"]})
    return ApiError(503, "CI_PROVIDER_UNREACHABLE.lookup", params={"host": where["host"]})


def _repository_host(request: Request, provider: CiProvider, host: str | None) -> str:
    if provider == CiProvider.GITHUB:
        if host is not None and host.strip().rstrip("/").lower() != GITHUB_HOST:
            raise ApiError(422, "HOST_NOT_ALLOWED.github")
        return GITHUB_HOST
    try:
        normalised = normalise_https_base_url(host or "", "host")
    except ValueError:
        normalised = None
    if normalised is None or normalised not in request.app.state.settings.forgejo_hosts:
        raise ApiError(422, "HOST_NOT_ALLOWED")
    return normalised


async def _repository_json(db: AsyncSession, repository: SiteRepository, group_slug: str, site_slug: str):
    creator = await db.get(Member, repository.created_by) if repository.created_by else None
    return SiteRepositoryOut(
        group_slug=group_slug,
        site_slug=site_slug,
        site_id=repository.site_id,
        provider=repository.provider,
        host=repository.host,
        owner=repository.owner,
        repo=repository.repo,
        repository_id=repository.repository_id,
        owner_id=repository.owner_id,
        live_branch=repository.live_branch,
        ids_confirmed=repository.ids_confirmed,
        site_id_required=repository.site_id_required,
        created_by=(creator.name or creator.email) if creator is not None else "",
        created_at=iso_utc(repository.created_at),
    )


_ERROR_REPOSITORY_NOT_SET = {404: "No repository is linked to this site (`REPOSITORY_NOT_SET`)."}


def add_routes(router: APIRouter) -> None:
    # -- Linked repository (CI trust) --

    @router.get(
        "/sites/{group_slug}/{site_slug}/repository",
        tags=[TAG_CI],
        summary="Linked repository of a site",
        response_description="The repository from which CI may publish to this site.",
        description=(
            "The repository from which GitHub or Forgejo workflows with an OIDC ID token may publish to "
            "this site, without a secret. A 404 `REPOSITORY_NOT_SET` means nothing has been linked "
            "yet.\n\n"
            "**Who can call this:** effective site role `editor` or higher: whoever publishes must be able to "
            "set up the workflow."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE, _ERROR_REPOSITORY_NOT_SET),
    )
    async def site_repository(
        group_slug: str, site_slug: str, member: ActiveMember, db: Db
    ) -> SiteRepositoryOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.EDITOR)
        repository = await db.scalar(select(SiteRepository).where(SiteRepository.site_id == site.id))
        if repository is None:
            raise ApiError(404, "REPOSITORY_NOT_SET")
        return await _repository_json(db, repository, group.slug, site.slug)

    @router.put(
        "/sites/{group_slug}/{site_slug}/repository",
        tags=[TAG_CI],
        summary="Link a repository to a site",
        response_description="The linked repository, with the IDs the provider returned.",
        description=(
            "Links a GitHub or Forgejo repository to this site, or replaces the link. Toon looks the "
            "repository up at the provider (`GET /repos/{owner}/{repo}`) and stores its numeric IDs: "
            "these stay the same when the repository is renamed, and a new repository under the same name "
            "does not get them. A CI ID token from this repository may publish afterwards: a preview (and "
            "its cleanup) from any branch, live only from `push`, `workflow_dispatch` or `schedule` and, if "
            "one is set, only from `liveBranch`.\n\n"
            "Toon does the lookup without credentials, so it cannot see a private repository. In that case pass "
            "`repositoryId` and `ownerId` yourself (`gh api repos/{owner}/{repo} --jq '.id, .owner.id'`): If the "
            "lookup fails, Toon stores them as given. A wrong ID does not link anything else, it only causes "
            "every deploy to be refused. If Toon does find the repository, the IDs must match.\n\n"
            "Also allowed with the CLI token from `toon login` (`toon site link`), then without a CSRF header. "
            "A CI ID token links nothing.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header or the CLI token."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_CLI_TOKEN,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {
                422: (
                    "Owner or repository is not a valid name (`REPOSITORY_INVALID`), the host does not belong "
                    "to the provider or is not among the allowed Forgejo instances (`HOST_NOT_ALLOWED`), the "
                    "live branch is not a valid branch name (`LIVE_BRANCH_INVALID`), the provider does not know "
                    "the repository, or it is not public, and no IDs were passed (`REPOSITORY_NOT_FOUND`), "
                    "the IDs are not both a positive integer (`REPOSITORY_IDS_INVALID`), or the "
                    "provider gives the repository different IDs (`REPOSITORY_IDS_MISMATCH`)."
                )
            },
            {
                503: (
                    "The provider is unreachable (`CI_PROVIDER_UNREACHABLE`) or its limit for "
                    "anonymous requests is used up (`CI_PROVIDER_RATE_LIMITED`), and no IDs were passed; "
                    "try again later."
                )
            },
        ),
    )
    async def set_site_repository(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: SiteRepositoryBody,
        creator: Annotated[Creator, Depends(require_creator)],
        db: Db,
    ) -> SiteRepositoryOut:
        member = creator.member
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        host = _repository_host(request, body.provider, body.host)
        owner, repo = body.owner.strip(), body.repo.strip()
        if not valid_name(owner) or not valid_name(repo):
            raise ApiError(422, "REPOSITORY_INVALID")
        live_branch = _live_branch(body.live_branch)
        entered = _entered_ids(body)
        where = {"owner": owner, "repo": repo, "host": host_label(host)}
        providers: ProviderClient = request.app.state.ci_providers
        # Entered ids are safe without the lookup: a token only matches the
        # repository that really carries them, so a wrong id means every
        # deploy is refused. The lookup still catches a typo in a public one.
        try:
            resolved = await providers.resolve(body.provider, host, owner, repo)
        except (RepositoryNotFoundError, ProviderUnavailableError) as error:
            if entered is None:
                raise _lookup_failed(error, where) from None
            resolved = ResolvedRepository(owner=owner, repo=repo, repository_id=entered[0], owner_id=entered[1])
            ids_confirmed = False
        else:
            if entered is not None and entered != (resolved.repository_id, resolved.owner_id):
                raise ApiError(422, "REPOSITORY_IDS_MISMATCH", params=where)
            ids_confirmed = True

        # Locked, so the site_id_required this answer and its audit row
        # report is the one stored, also when the site id is required
        # meanwhile.
        repository = await db.scalar(
            select(SiteRepository).where(SiteRepository.site_id == site.id).with_for_update()
        )
        if repository is None:
            repository = SiteRepository(site_id=site.id)
            db.add(repository)
        elif repository.ids_confirmed and (
            repository.provider,
            repository.host,
            f"{repository.owner}/{repository.repo}".lower(),
            repository.repository_id,
            repository.owner_id,
        ) == (
            body.provider,
            host,
            f"{resolved.owner}/{resolved.repo}".lower(),
            resolved.repository_id,
            resolved.owner_id,
        ):
            # Changing the live branch of a private repository keeps what a
            # token or an earlier lookup confirmed about the same repository.
            ids_confirmed = True
        if (repository.provider, repository.host, repository.repository_id) != (
            body.provider,
            host,
            resolved.repository_id,
        ):
            # Another repository is a new link, and a new link requires the
            # site id; the same one keeps what an older link accepted.
            repository.site_id_required = True
        repository.ids_confirmed = ids_confirmed
        repository.provider = body.provider
        repository.host = host
        repository.owner = resolved.owner
        repository.repo = resolved.repo
        repository.repository_id = resolved.repository_id
        repository.owner_id = resolved.owner_id
        repository.live_branch = live_branch
        repository.created_by = member.id
        repository.created_at = datetime.now(UTC)
        await db.commit()
        await _audit(
            request,
            member,
            "site_repository_set",
            {
                "group": group_slug,
                "site": site_slug,
                "provider": str(body.provider),
                "host": host,
                "repository": f"{resolved.owner}/{resolved.repo}",
                "repository_id": resolved.repository_id,
                "ids_confirmed": ids_confirmed,
                "site_id_required": repository.site_id_required,
                "live_branch": live_branch,
                **creator.audit_refs(),
            },
        )
        return await _repository_json(db, repository, group.slug, site.slug)

    @router.delete(
        "/sites/{group_slug}/{site_slug}/repository",
        status_code=204,
        tags=[TAG_CI],
        summary="Unlink a repository",
        description=(
            "Removes the link: CI ID tokens from that repository are refused afterwards. Versions that "
            "were published from CI earlier remain.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_deleted("The link has been removed.")
        | _errors(_ERROR_CSRF, _ERROR_SITE_ROLE, _ERROR_SITE, _ERROR_REPOSITORY_NOT_SET),
    )
    async def delete_site_repository(
        request: Request, group_slug: str, site_slug: str, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        await _write_one_or_404(
            db,
            delete(SiteRepository).where(SiteRepository.site_id == site.id).returning(SiteRepository.id),
            "REPOSITORY_NOT_SET",
        )
        await _audit(request, member, "site_repository_remove", {"group": group_slug, "site": site_slug})
        return Response(status_code=204)

    @router.put(
        "/sites/{group_slug}/{site_slug}/repository/site-id-required",
        tags=[TAG_CI],
        summary="Require the site id of a workflow",
        response_description="The linked repository, with its new setting.",
        description=(
            "For a link made before the site id existed: from now on the linked repository may publish to this "
            "site only with a CI ID token whose audience names the site id, `{TOON_BASE_URL}/-/sites/{siteId}`. "
            "A workflow does that with `site-id` (action) or `--site-id` (CLI); a workflow without it is "
            "refused with 403 `CI_SITE_ID_REQUIRED`. A link made since then, or that got another repository "
            "since, requires it already.\n\n"
            "This goes one way: `true` sets it, `false` on a link that requires the site id is refused, so no "
            "admin can quietly let a workflow without it in again. Asking for what already holds changes "
            "nothing and writes no audit row.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            _ERROR_REPOSITORY_NOT_SET,
            {
                422: (
                    "`siteIdRequired` is `false` while the link already requires the site id "
                    "(`SITE_ID_REQUIRED_PERMANENT`)."
                )
            },
        ),
    )
    async def set_site_id_required(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: SiteIdRequiredBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteRepositoryOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        repository = await db.scalar(
            select(SiteRepository).where(SiteRepository.site_id == site.id).with_for_update()
        )
        if repository is None:
            raise ApiError(404, "REPOSITORY_NOT_SET")
        if body.site_id_required == repository.site_id_required:
            return await _repository_json(db, repository, group.slug, site.slug)
        if not body.site_id_required:
            raise ApiError(422, "SITE_ID_REQUIRED_PERMANENT")
        repository.site_id_required = True
        await db.commit()
        await _audit(
            request,
            member,
            "site_repository_site_id_required",
            {"group": group_slug, "site": site_slug, "site_id": str(site.id)},
        )
        return await _repository_json(db, repository, group.slug, site.slug)

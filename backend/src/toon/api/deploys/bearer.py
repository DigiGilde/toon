"""Where a bearer token is accepted, and the middleware that refuses it everywhere else."""

from __future__ import annotations

import re

from fastapi import Request
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from toon import messages
from toon.api.errors import WWW_AUTHENTICATE_BEARER, locale_from_header, problem_response
from toon.messages import Msg

_DEPLOY_PATH_RE = re.compile(r"^/-/api/v1/sites/[^/]+/[^/]+/deploys$")
_PREVIEW_PATH_RE = re.compile(r"^/-/api/v1/sites/[^/]+/[^/]+/previews/[^/]+$")
# The CLI's own session endpoints (api/cli.py) authenticate with the access
# token as well.
CLI_SESSION_PATH = "/-/api/v1/cli/session"
CLI_WHOAMI_PATH = "/-/api/v1/cli/whoami"
# Creating a group or a site and linking a repository (api/admin/) also
# take the CLI token; nothing else in the admin API does.
GROUP_CREATE_PATH = "/-/api/v1/groups"
_SITE_CREATE_PATH_RE = re.compile(r"^/-/api/v1/groups/[^/]+/sites$")
_REPOSITORY_LINK_PATH_RE = re.compile(r"^/-/api/v1/sites/[^/]+/[^/]+/repository$")
# Deleting a feedback item and the feedback settings stay session only.
_FEEDBACK_PATH = r"^/-/api/v1/sites/[^/]+/[^/]+/feedback"
_FEEDBACK_CLI_ENDPOINTS = (
    ("GET", re.compile(_FEEDBACK_PATH + r"$")),
    ("GET", re.compile(_FEEDBACK_PATH + r"/[^/]+$")),
    ("GET", re.compile(_FEEDBACK_PATH + r"/[^/]+/screenshot$")),
    ("PUT", re.compile(_FEEDBACK_PATH + r"/[^/]+/status$")),
    ("POST", re.compile(_FEEDBACK_PATH + r"/[^/]+/replies$")),
)


def is_deploy_endpoint(method_: str, path: str) -> bool:
    if method_ == "POST" and _DEPLOY_PATH_RE.match(path):
        return True
    return bool(method_ == "DELETE" and _PREVIEW_PATH_RE.match(path))


def is_creation_endpoint(method_: str, path: str) -> bool:
    if method_ != "POST":
        return False
    return path == GROUP_CREATE_PATH or bool(_SITE_CREATE_PATH_RE.match(path))


def is_repository_link_endpoint(method_: str, path: str) -> bool:
    return method_ == "PUT" and bool(_REPOSITORY_LINK_PATH_RE.match(path))


def is_feedback_cli_endpoint(method_: str, path: str) -> bool:
    return any(method_ == method and pattern.match(path) for method, pattern in _FEEDBACK_CLI_ENDPOINTS)


def accepts_bearer(method_: str, path: str) -> bool:
    """The deploy endpoints, the CLI's logout and whoami, group and site
    creation, linking a repository, and the feedback routes the CLI uses."""
    if (
        is_deploy_endpoint(method_, path)
        or is_creation_endpoint(method_, path)
        or is_repository_link_endpoint(method_, path)
        or is_feedback_cli_endpoint(method_, path)
    ):
        return True
    return (method_, path) in (("DELETE", CLI_SESSION_PATH), ("GET", CLI_WHOAMI_PATH))


def _bearer_plaintext(authorization: str | None) -> str | None:
    """Returns the token value when the scheme (case-insensitive) is Bearer;
    None when there is no Bearer header."""
    if not authorization:
        return None
    schema, _, rest = authorization.partition(" ")
    if schema.lower() != "bearer":
        return None
    return rest.strip()


def bearer_from_request(request: Request) -> str | None:
    return _bearer_plaintext(request.headers.get("Authorization"))


class BearerOutsideDeploysMiddleware:
    """Rejects Bearer requests outside the endpoints that accept one."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            authorization = Headers(scope=scope).get("authorization")
            if _bearer_plaintext(authorization) is not None and not accepts_bearer(
                scope["method"], scope["path"]
            ):
                locale = locale_from_header(Headers(scope=scope).get("accept-language"))
                response = problem_response(
                    401,
                    messages.render(locale, Msg("BEARER_NOT_ACCEPTED")),
                    locale=locale,
                    code="BEARER_NOT_ACCEPTED",
                    headers=WWW_AUTHENTICATE_BEARER,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

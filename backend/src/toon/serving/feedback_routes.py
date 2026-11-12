"""Every route under `/-/feedback/` on the content host: the widget bundle,
its API, the consent pages and hiding or showing the button.

Host separation lets the whole prefix through on the content host, and on the
admin host every `/-/` path reaches the routes anyway, so each route checks
the host itself first and answers anywhere else with the neutral 404. The
last route takes every method on every other path under the prefix, also a
method no route names (TRACE, PROPFIND, QUERY): without it an unknown path
or method would get FastAPI's 405 or 422 JSON, an answer of another shape
than every other refusal on this host. Included in main.py before the
serving router, whose catch-alls would otherwise claim the paths. None of
these routes is in the OpenAPI document, which describes the admin API
alone.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from toon.constants import (
    PATH_CONTENT_FEEDBACK_PREFIX,
    PATH_FEEDBACK_ACCOUNT,
    PATH_FEEDBACK_ACCOUNT_STOP,
    PATH_FEEDBACK_API_PREFIX,
    PATH_FEEDBACK_HIDE,
    PATH_FEEDBACK_SHOW,
    PATH_FEEDBACK_WIDGET_PREFIX,
)
from toon.host_separation import host_from_scope
from toon.serving import feedback_api, feedback_consent
from toon.serving.feedback_widget import widget_url
from toon.serving.response import neutral_404_response

router = APIRouter()

_ITEMS = f"{PATH_FEEDBACK_API_PREFIX}items"
_ITEM = f"{_ITEMS}/{{item_id}}"
_REPLIES = f"{_ITEM}/replies"

# Content-addressed: a new build is a new address, so this one never changes.
_BUNDLE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
}


def _elsewhere(request: Request) -> bool:
    return host_from_scope(request.scope) != request.app.state.settings.content_host


@router.get(f"{PATH_FEEDBACK_WIDGET_PREFIX}{{name}}", include_in_schema=False)
async def feedback_widget_bundle(request: Request, name: str) -> Response:
    """The bundle under the address of its own hash, and nothing under any
    other: a stale bundle cannot be cached under the next build's address."""
    bundle = getattr(request.app.state, "feedback_widget", None)
    if _elsewhere(request) or bundle is None or request.scope["path"] != widget_url(bundle):
        return neutral_404_response()
    return Response(bundle.content, media_type="text/javascript; charset=utf-8", headers=_BUNDLE_HEADERS)


@router.post(PATH_FEEDBACK_HIDE, include_in_schema=False)
async def feedback_hide(request: Request) -> Response:
    if _elsewhere(request):
        return neutral_404_response()
    return await feedback_consent.hide_button(request)


@router.get(PATH_FEEDBACK_SHOW, include_in_schema=False)
async def feedback_show(request: Request) -> Response:
    if _elsewhere(request):
        return neutral_404_response()
    return await feedback_consent.show_button(request)


@router.get(PATH_FEEDBACK_ACCOUNT, include_in_schema=False)
async def feedback_consent_page(request: Request) -> Response:
    if _elsewhere(request):
        return neutral_404_response()
    return await feedback_consent.account_page(request)


@router.post(PATH_FEEDBACK_ACCOUNT, include_in_schema=False)
async def feedback_consent_given(request: Request) -> Response:
    if _elsewhere(request):
        return neutral_404_response()
    return await feedback_consent.give_consent(request)


@router.get(PATH_FEEDBACK_ACCOUNT_STOP, include_in_schema=False)
async def feedback_consent_stopped(request: Request) -> Response:
    if _elsewhere(request):
        return neutral_404_response()
    return await feedback_consent.stop_consent(request)


@router.options(_ITEMS, include_in_schema=False)
async def feedback_items_preflight(request: Request) -> Response:
    return neutral_404_response() if _elsewhere(request) else feedback_api.preflight()


@router.options(_ITEM, include_in_schema=False)
async def feedback_item_preflight(request: Request, item_id: str) -> Response:
    return neutral_404_response() if _elsewhere(request) else feedback_api.preflight()


@router.options(_REPLIES, include_in_schema=False)
async def feedback_replies_preflight(request: Request, item_id: str) -> Response:
    return neutral_404_response() if _elsewhere(request) else feedback_api.preflight()


@router.post(_ITEMS, include_in_schema=False)
async def feedback_create(request: Request) -> Response:
    if _elsewhere(request):
        return neutral_404_response()
    return await feedback_api.create_item(request)


@router.get(_ITEMS, include_in_schema=False)
async def feedback_mine(request: Request) -> Response:
    if _elsewhere(request):
        return neutral_404_response()
    return await feedback_api.my_items(request)


@router.post(_REPLIES, include_in_schema=False)
async def feedback_reply(request: Request, item_id: str) -> Response:
    if _elsewhere(request):
        return neutral_404_response()
    return await feedback_api.reply_to_item(request, item_id)


@router.delete(_ITEM, include_in_schema=False)
async def feedback_delete(request: Request, item_id: str) -> Response:
    if _elsewhere(request):
        return neutral_404_response()
    return await feedback_api.delete_item(request, item_id)


class _Neutral404:
    """Whatever no route above takes, any method included. An ASGI app and
    not a function: Starlette gives a function route GET alone, and a route
    without methods matches every one."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await neutral_404_response()(scope, receive, send)


# Last: a route matches in the order it was added.
router.add_route(f"{PATH_CONTENT_FEEDBACK_PREFIX}{{rest:path}}", _Neutral404(), include_in_schema=False)


__all__ = ["router"]

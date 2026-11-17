"""Every route under `/-/feedback/` on the content host: the widget API and
the consent pages.

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
)
from toon.host_separation import host_from_scope
from toon.serving import feedback_api, feedback_consent
from toon.serving.response import neutral_404_response

router = APIRouter()

_ITEMS = f"{PATH_FEEDBACK_API_PREFIX}items"
_ITEM = f"{_ITEMS}/{{item_id}}"
_REPLIES = f"{_ITEM}/replies"


def _elsewhere(request: Request) -> bool:
    return host_from_scope(request.scope) != request.app.state.settings.content_host


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

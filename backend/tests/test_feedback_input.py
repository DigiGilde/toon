"""Tests for what a giver sends through the feedback widget API
(serving/feedback_input.py): every item that does not hold is a 422 that
names its field, never a 500, and stores nothing. Against the real PostgreSQL
test container, with the app and helpers of test_feedback_api.py.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from test_feedback_api import (
    AREA_ANCHOR,
    ITEMS,
    POINT_ANCHOR,
    PREVIEW_ROOT,
    REFUSED_FORMAT,
    ROOT,
    TEXT_ANCHOR,
    Environment,
    assert_invalid,
    assert_problem,
    auth,
    client_for,
    create,
    item,
    long_text,
    open_environment,
    page_token,
    parts,
    rolled_back,
    stored_items,
    writes_spent,
)

from toon.constants import FeedbackKind


@pytest_asyncio.fixture
async def environment(migrated_dsn: str, tmp_path: Path) -> AsyncIterator[Environment]:
    async with rolled_back(migrated_dsn) as factory:
        yield await open_environment(factory, tmp_path)


@pytest_asyncio.fixture
async def client(environment: Environment) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(environment.app) as c:
        yield c


# --- Validation: 422, never 500 ---------------------------------------------------


INVALID_ITEMS: list[tuple[str, object, str]] = [
    # (id, item, the field the detail names)
    ("not-an-object", [1, 2], "item"),
    ("unknown-key", item(email="iemand@example.org"), "item"),
    ("screenshot-region", item(screenshot={"region": None}), "item"),
    ("no-comment", {k: v for k, v in item().items() if k != "comment"}, "item"),
    ("no-author", {k: v for k, v in item().items() if k != "author"}, "item"),
    ("kind-unknown", item(kind="video"), "kind"),
    ("kind-not-a-string", item(kind=["page"]), "kind"),
    ("kind-an-object", item(kind={"page": 1}), "kind"),
    ("comment-empty", item(comment=""), "comment"),
    ("comment-blank", item(comment=" \n\t "), "comment"),
    ("comment-too-long", item(comment=long_text(5001)), "comment"),
    ("comment-not-a-string", item(comment=5), "comment"),
    ("comment-nul", item(comment="a\x00b"), "comment"),
    ("comment-carriage-return", item(comment="a\r\nb"), "comment"),
    ("comment-right-to-left-override", item(comment="Typo in de kop.\u202e"), "comment"),
    ("comment-tag-characters", item(comment="Typo\U000e0041\U000e0042"), "comment"),
    ("comment-zero-width-space", item(comment="a\u200bb"), "comment"),
    ("comment-lone-surrogate", item(comment="a\ud800b"), "comment"),
    ("comment-private-use", item(comment="a\ue000"), "comment"),
    ("comment-c1-control", item(comment="a\x85b"), "comment"),
    ("page-not-an-object", item(page="/aurora/site/"), "page"),
    ("page-unknown-key", item(page={"path": ROOT, "url": "https://kwaad.example/"}), "page"),
    ("page-without-path", item(page={"title": "x"}), "page"),
    ("path-not-a-string", item(page={"path": 7}), "page.path"),
    ("path-empty", item(page={"path": ""}), "page.path"),
    ("path-query", item(page={"path": f"{ROOT}docs/?x=1"}), "page.path"),
    ("path-fragment", item(page={"path": f"{ROOT}docs/#x"}), "page.path"),
    ("path-backslash", item(page={"path": f"{ROOT}docs\\x"}), "page.path"),
    ("path-nul", item(page={"path": f"{ROOT}a\x00"}), "page.path"),
    ("path-space", item(page={"path": f"{ROOT}a b"}), "page.path"),
    ("path-not-ascii", item(page={"path": f"{ROOT}café/"}), "page.path"),
    ("path-dot-dot", item(page={"path": f"{ROOT}../ander/"}), "page.path"),
    ("path-dot", item(page={"path": f"{ROOT}./docs/"}), "page.path"),
    ("path-encoded-dot-dot", item(page={"path": f"{ROOT}%2E%2e/ander/"}), "page.path"),
    ("path-half-encoded-dot-dot", item(page={"path": f"{ROOT}.%2E/"}), "page.path"),
    ("path-empty-segment", item(page={"path": f"{ROOT}/docs/"}), "page.path"),
    ("path-empty-segment-inside", item(page={"path": f"{ROOT}docs//x"}), "page.path"),
    ("path-a-preview-on-a-live-token", item(page={"path": PREVIEW_ROOT}), "page.path"),
    ("path-version", item(page={"path": f"{ROOT}_version/x/"}), "page.path"),
    ("path-encoded-copies", item(page={"path": f"{ROOT}_encoded/gzip/index.html"}), "page.path"),
    ("path-too-long", item(page={"path": ROOT + long_text(2049 - len(ROOT))}), "page.path"),
    ("title-not-a-string", item(page={"path": ROOT, "title": ["x"]}), "page.title"),
    # A fragment is kept only when it names a place: every way it can hold a
    # value or more than a place is a refusal of its own.
    ("fragment-too-long", item(page={"path": ROOT, "fragment": "#" + "a" * 65}), "page.fragment"),
    ("fragment-a-value", item(page={"path": ROOT, "fragment": "#access_token=abc"}), "page.fragment"),
    ("fragment-a-query", item(page={"path": ROOT, "fragment": "#/7?x"}), "page.fragment"),
    ("fragment-percent-encoded", item(page={"path": ROOT, "fragment": "#%C3%BC"}), "page.fragment"),
    ("fragment-parameters", item(page={"path": ROOT, "fragment": "#a&b"}), "page.fragment"),
    ("fragment-not-ascii", item(page={"path": ROOT, "fragment": "#\u00fcber"}), "page.fragment"),
    ("fragment-ascii-digit-lookalike", item(page={"path": ROOT, "fragment": "#\u0663"}), "page.fragment"),
    ("fragment-empty", item(page={"path": ROOT, "fragment": ""}), "page.fragment"),
    ("fragment-only-hash", item(page={"path": ROOT, "fragment": "#"}), "page.fragment"),
    ("fragment-without-hash", item(page={"path": ROOT, "fragment": "/7"}), "page.fragment"),
    ("fragment-second-hash", item(page={"path": ROOT, "fragment": "#a#b"}), "page.fragment"),
    ("fragment-space", item(page={"path": ROOT, "fragment": "#a b"}), "page.fragment"),
    ("fragment-trailing-newline", item(page={"path": ROOT, "fragment": "#/7\n"}), "page.fragment"),
    ("fragment-not-a-string", item(page={"path": ROOT, "fragment": 7}), "page.fragment"),
    ("anchor-on-a-page", item(anchor=POINT_ANCHOR), "anchor"),
    ("point-without-anchor", item(kind="point", anchor=None), "anchor"),
    ("anchor-not-an-object", item(kind="point", anchor="main"), "anchor"),
    ("anchor-unknown-key", item(kind="point", anchor={**POINT_ANCHOR, "html": "<b>"}), "anchor"),
    ("anchor-without-selectors", item(kind="point", anchor={"point": POINT_ANCHOR["point"]}), "anchor"),
    ("selectors-not-a-list", item(kind="point", anchor={**POINT_ANCHOR, "selectors": {}}), "anchor.selectors"),
    ("selectors-empty", item(kind="point", anchor={**POINT_ANCHOR, "selectors": []}), "anchor.selectors"),
    (
        "selectors-too-many",
        item(kind="text", anchor={**TEXT_ANCHOR, "selectors": [*TEXT_ANCHOR["selectors"], {"type": "CssSelector"}]}),
        "anchor.selectors",
    ),
    (
        "selector-unknown-type",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": [{"type": "XPathSelector", "value": "/html"}]}),
        "anchor.selectors",
    ),
    (
        "selector-type-not-a-string",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": [{"type": ["CssSelector"], "value": "p"}]}),
        "anchor.selectors",
    ),
    (
        "selector-not-an-object",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": ["CssSelector"]}),
        "anchor.selectors",
    ),
    (
        "selector-unknown-key",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": [{"type": "CssSelector", "value": "p", "x": 1}]}),
        "anchor.selectors",
    ),
    (
        "selector-twice",
        item(
            kind=FeedbackKind.TEXT,
            anchor={**TEXT_ANCHOR, "selectors": [TEXT_ANCHOR["selectors"][2], TEXT_ANCHOR["selectors"][2]]},
        ),
        "anchor.selectors",
    ),
    (
        "point-with-a-quote",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": [TEXT_ANCHOR["selectors"][2]]}),
        "anchor.selectors",
    ),
    (
        "point-with-two-selectors",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": TEXT_ANCHOR["selectors"][1:]}),
        "anchor.selectors",
    ),
    ("point-without-point", item(kind="point", anchor={**POINT_ANCHOR, "point": None}), "anchor.point"),
    ("point-missing", item(kind="point", anchor={"selectors": POINT_ANCHOR["selectors"]}), "anchor.point"),
    (
        "point-unknown-key",
        item(kind="point", anchor={**POINT_ANCHOR, "point": {**POINT_ANCHOR["point"], "z": 1}}),
        "anchor.point",
    ),
    (
        "point-fraction-above-one",
        item(kind="point", anchor={**POINT_ANCHOR, "point": {**POINT_ANCHOR["point"], "x": 1.01}}),
        "anchor.point",
    ),
    (
        "point-negative",
        item(kind="point", anchor={**POINT_ANCHOR, "point": {**POINT_ANCHOR["point"], "pageX": -1}}),
        "anchor.point",
    ),
    (
        "point-too-far",
        item(kind="point", anchor={**POINT_ANCHOR, "point": {**POINT_ANCHOR["point"], "pageY": 1_000_001}}),
        "anchor.point",
    ),
    (
        "point-a-boolean",
        item(kind="point", anchor={**POINT_ANCHOR, "point": {**POINT_ANCHOR["point"], "y": True}}),
        "anchor.point",
    ),
    (
        "point-a-string",
        item(kind="point", anchor={**POINT_ANCHOR, "point": {**POINT_ANCHOR["point"], "y": "0.5"}}),
        "anchor.point",
    ),
    (
        "text-without-quote",
        item(kind="text", anchor={**TEXT_ANCHOR, "selectors": TEXT_ANCHOR["selectors"][:2]}),
        "anchor.selectors",
    ),
    ("text-with-a-point", item(kind="text", anchor={**TEXT_ANCHOR, "point": POINT_ANCHOR["point"]}), "anchor.point"),
    ("text-with-an-area", item(kind="text", anchor={**TEXT_ANCHOR, "area": AREA_ANCHOR["area"]}), "anchor.area"),
    ("point-with-an-area", item(kind="point", anchor={**POINT_ANCHOR, "area": AREA_ANCHOR["area"]}), "anchor.area"),
    ("area-without-anchor", item(kind="area", anchor=None), "anchor"),
    ("area-without-area", item(kind="area", anchor={**AREA_ANCHOR, "area": None}), "anchor.area"),
    ("area-missing", item(kind="area", anchor={"selectors": AREA_ANCHOR["selectors"]}), "anchor.area"),
    ("area-with-a-point", item(kind="area", anchor={**AREA_ANCHOR, "point": POINT_ANCHOR["point"]}), "anchor.point"),
    ("area-a-list", item(kind="area", anchor={**AREA_ANCHOR, "area": []}), "anchor.area"),
    ("area-a-string", item(kind="area", anchor={**AREA_ANCHOR, "area": "0,0"}), "anchor.area"),
    (
        "area-with-a-quote",
        item(kind="area", anchor={**AREA_ANCHOR, "selectors": [TEXT_ANCHOR["selectors"][2]]}),
        "anchor.selectors",
    ),
    (
        "area-with-two-selectors",
        item(kind="area", anchor={**AREA_ANCHOR, "selectors": TEXT_ANCHOR["selectors"][1:]}),
        "anchor.selectors",
    ),
    (
        "area-without-selectors",
        item(kind="area", anchor={**AREA_ANCHOR, "selectors": []}),
        "anchor.selectors",
    ),
    *[
        (
            f"area-without-{key}",
            item(
                kind="area",
                anchor={**AREA_ANCHOR, "area": {k: v for k, v in AREA_ANCHOR["area"].items() if k != key}},
            ),
            "anchor.area",
        )
        for key in AREA_ANCHOR["area"]
    ],
    (
        "area-unknown-key",
        item(kind="area", anchor={**AREA_ANCHOR, "area": {**AREA_ANCHOR["area"], "z": 1}}),
        "anchor.area",
    ),
    *[
        (
            f"area-{key}-above-one",
            item(kind="area", anchor={**AREA_ANCHOR, "area": {**AREA_ANCHOR["area"], key: 1.01}}),
            "anchor.area",
        )
        for key in ("x", "y", "width", "height")
    ],
    *[
        (
            f"area-{key}-negative",
            item(kind="area", anchor={**AREA_ANCHOR, "area": {**AREA_ANCHOR["area"], key: -1}}),
            "anchor.area",
        )
        for key in AREA_ANCHOR["area"]
    ],
    *[
        (
            f"area-{key}-too-far",
            item(kind="area", anchor={**AREA_ANCHOR, "area": {**AREA_ANCHOR["area"], key: 1_000_001}}),
            "anchor.area",
        )
        for key in ("pageX", "pageY", "pageWidth", "pageHeight")
    ],
    (
        "area-value-a-boolean",
        item(kind="area", anchor={**AREA_ANCHOR, "area": {**AREA_ANCHOR["area"], "width": True}}),
        "anchor.area",
    ),
    (
        "area-value-a-string",
        item(kind="area", anchor={**AREA_ANCHOR, "area": {**AREA_ANCHOR["area"], "pageHeight": "90"}}),
        "anchor.area",
    ),
    (
        "quote-empty",
        item(kind="text", anchor={"selectors": [{"type": "TextQuoteSelector", "exact": ""}]}),
        "anchor.selectors",
    ),
    (
        "quote-too-long",
        item(kind="text", anchor={"selectors": [{"type": "TextQuoteSelector", "exact": long_text(1001)}]}),
        "anchor.selectors",
    ),
    (
        "quote-newline",
        item(kind="text", anchor={"selectors": [{"type": "TextQuoteSelector", "exact": "a\nb"}]}),
        "anchor.selectors",
    ),
    (
        "quote-format-character",
        item(kind="text", anchor={"selectors": [{"type": "TextQuoteSelector", "exact": "a\u2066b"}]}),
        "anchor.selectors",
    ),
    (
        "prefix-too-long",
        item(kind="text", anchor={"selectors": [{"type": "TextQuoteSelector", "exact": "a", "prefix": long_text(33)}]}),
        "anchor.selectors",
    ),
    (
        "suffix-not-a-string",
        item(kind="text", anchor={"selectors": [{"type": "TextQuoteSelector", "exact": "a", "suffix": 3}]}),
        "anchor.selectors",
    ),
    (
        "position-backwards",
        item(
            kind=FeedbackKind.TEXT,
            anchor={"selectors": [TEXT_ANCHOR["selectors"][2], {"type": "TextPositionSelector", "start": 9, "end": 9}]},
        ),
        "anchor.selectors",
    ),
    (
        "position-a-fraction",
        item(
            kind=FeedbackKind.TEXT,
            anchor={
                "selectors": [TEXT_ANCHOR["selectors"][2], {"type": "TextPositionSelector", "start": 1.5, "end": 9}]
            },
        ),
        "anchor.selectors",
    ),
    (
        "position-too-far",
        item(
            kind=FeedbackKind.TEXT,
            anchor={
                "selectors": [TEXT_ANCHOR["selectors"][2], {"type": "TextPositionSelector", "start": 1, "end": 10**7}]
            },
        ),
        "anchor.selectors",
    ),
    (
        "position-without-end",
        item(kind="text", anchor={"selectors": [TEXT_ANCHOR["selectors"][2], {"type": "TextPositionSelector"}]}),
        "anchor.selectors",
    ),
    (
        "css-empty",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": [{"type": "CssSelector", "value": ""}]}),
        "anchor.selectors",
    ),
    (
        "css-too-long",
        item(kind="point", anchor={**POINT_ANCHOR, "selectors": [{"type": "CssSelector", "value": long_text(1001)}]}),
        "anchor.selectors",
    ),
    ("viewport-not-an-object", item(viewport=[1280, 800]), "viewport"),
    ("viewport-unknown-key", item(viewport={"width": 1, "height": 1, "dpr": 1, "zoom": 1}), "viewport"),
    ("viewport-without-dpr", item(viewport={"width": 1, "height": 1}), "viewport"),
    ("viewport-dpr-zero", item(viewport={"width": 1, "height": 1, "dpr": 0}), "viewport"),
    ("viewport-dpr-above-ten", item(viewport={"width": 1, "height": 1, "dpr": 10.5}), "viewport"),
    ("viewport-negative", item(viewport={"width": -1, "height": 1, "dpr": 1}), "viewport"),
    ("viewport-too-wide", item(viewport={"width": 1_000_001, "height": 1, "dpr": 1}), "viewport"),
    ("author-not-an-object", item(author="anonymous"), "author"),
    ("author-unknown-key", item(author={"kind": "account", "email": "baas@example.org"}), "author"),
    ("author-without-kind", item(author={"name": "Jan"}), "author"),
    ("author-kind-unknown", item(author={"kind": "member"}), "author.kind"),
    ("name-missing", item(author={"kind": "name"}), "author.name"),
    ("name-blank", item(author={"kind": "name", "name": "  "}), "author.name"),
    ("name-too-long", item(author={"kind": "name", "name": long_text(101)}), "author.name"),
    ("name-newline", item(author={"kind": "name", "name": "Jan\nJansen"}), "author.name"),
    ("name-right-to-left-override", item(author={"kind": "name", "name": "\u202eJan"}), "author.name"),
    ("name-beside-anonymous", item(author={"kind": "anonymous", "name": "Jan"}), "author.name"),
    ("name-beside-account", item(author={"kind": "account", "name": "De Baas"}), "author.name"),
    ("name-a-number", item(author={"kind": "anonymous", "name": 3}), "author.name"),
]


class TestValidation:
    @pytest.mark.parametrize(
        ("value", "field_name"), [(v, f) for _, v, f in INVALID_ITEMS], ids=[i for i, _, _ in INVALID_ITEMS]
    )
    async def test_an_item_that_does_not_hold_is_refused_by_field(self, client, environment, value, field_name):
        token = page_token(environment.app, environment.world.site_id)
        response = await create(client, token, value)
        assert_invalid(response, field_name)
        assert await stored_items(environment.factory) == []

    @pytest.mark.parametrize("char", list(REFUSED_FORMAT.values()), ids=list(REFUSED_FORMAT))
    async def test_every_other_format_character_is_refused(self, client, environment, char):
        """Of the format characters only the two joiners pass, in a comment
        and in a name alike."""
        assert unicodedata.category(char) == "Cf"
        token = page_token(environment.app, environment.world.site_id)
        assert_invalid(await create(client, token, item(comment=f"Typo{char} in de kop.")), "comment")
        assert_invalid(await create(client, token, item(author={"kind": "name", "name": f"Jan{char}"})), "author.name")
        assert await stored_items(environment.factory) == []

    @pytest.mark.parametrize(
        ("ref", "path"),
        [
            (None, "/aurora/ander/"),
            (None, "/aurora/site"),
            (None, "/aurora/sites/docs/"),
            (None, "/borealis/site/docs/"),
            (None, "/"),
            ("pr-1", "/aurora/ander/_preview/pr-1/docs/"),
            ("pr-1", "/borealis/site/_preview/pr-1/"),
        ],
        ids=[
            "another-site",
            "the-site-without-slash",
            "a-longer-slug",
            "another-group",
            "the-root",
            "a-preview-of-another-site",
            "a-preview-in-another-group",
        ],
    )
    async def test_a_path_outside_the_sites_root_has_moved(self, client, environment, ref, path):
        """What a page served before a rename of its site or group sends: the
        token names the site by its id and holds, and only a reload gives the
        widget the address the site is at now. Nothing is stored, and each
        attempt counts for its author."""
        world = environment.world
        token = page_token(environment.app, world.site_id, ref=ref)
        for language in ("en", "nl"):
            response = await create(
                client, token, item(page={"path": path, "title": None}), headers={"Accept-Language": language}
            )
            assert_problem(response, 422, "FEEDBACK_PAGE_MOVED", locale=language)
        assert await stored_items(environment.factory) == []
        assert writes_spent(environment.app, world.site_id) == (2, 0)

    @pytest.mark.parametrize(
        "raw",
        [
            '{"kind": "page", "comment": NaN}',
            '{"kind": "page", "comment": "a", "viewport": {"width": Infinity, "height": 1, "dpr": 1}}',
            '{"kind": "page", "comment": "a", "viewport": {"width": -Infinity, "height": 1, "dpr": 1}}',
            '{"kind": "page", "kind": "point"}',
            "not json",
            "",
            '{"kind": "page"',
            "\ufeff{}",
            "[" * 16000 + "]" * 16000,
        ],
        ids=["nan", "infinity", "minus-infinity", "duplicate-key", "garbage", "empty", "cut-short", "bom", "deep"],
    )
    async def test_json_that_is_not_strict_json_is_refused(self, client, environment, raw):
        token = page_token(environment.app, environment.world.site_id)
        response = await client.post(ITEMS, files=parts(raw=raw), headers=auth(token))
        assert_invalid(response, "item")

    async def test_a_number_beyond_a_float_is_refused_not_infinite(self, client, environment):
        """1e400 parses as infinity without any constant JSON names."""
        token = page_token(environment.app, environment.world.site_id)
        raw = json.dumps(item()).replace('"width": 1280', '"width": 1e400')
        response = await client.post(ITEMS, files=parts(raw=raw), headers=auth(token))
        assert_invalid(response, "viewport")

    async def test_an_item_that_is_not_utf8_is_refused(self, client, environment):
        token = page_token(environment.app, environment.world.site_id)
        response = await client.post(ITEMS, files=parts(raw=b'{"comment": "\xff"}'), headers=auth(token))
        assert_invalid(response, "item")

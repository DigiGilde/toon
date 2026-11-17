"""What a giver sends through the feedback widget API, checked before
anything is stored.

Whatever a giver sends is held to one rule for every string, and the stored
anchor and viewport are rebuilt from validated fields only, so no input ends
in a 500. A refusal is a `ProblemError`, which serving/feedback_api.py
answers.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn

from toon.constants import RESERVED_SEGMENTS, FeedbackIdentity, FeedbackKind
from toon.messages import Msg

MAX_COMMENT = 5000
MAX_NAME = 100
MAX_TITLE = 300
MAX_QUOTE = 1000
MAX_AFFIX = 32
MAX_SELECTOR = 1000
MAX_PAGE_PATH = 2048
MAX_COORDINATE = 1_000_000
MAX_DPR = 10

# Unicode categories no giver string may contain: Cc (control), Cf (format,
# such as U+202E, which reverses the reading order after it, or the tag
# characters a language model reads and a screen does not show), Cs (a lone
# surrogate, which asyncpg cannot encode) and Co (private use). Newline and
# tab stay allowed in a comment and a reply. Cn (unassigned) is not refused:
# the giver's browser can know emoji that this server's Unicode tables do not.
_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co"})
_KEPT_IN_PROSE = frozenset("\n\t")
# The two format characters writing needs, allowed in every string: the
# zero-width non-joiner keeps letters apart in Persian and other scripts, and
# the zero-width joiner makes one emoji of a sequence.
_JOINERS = frozenset("\u200c\u200d")

# What `location.pathname` can hold: printable ASCII without what the URL
# standard percent-encodes in a path (space, ", #, <, >, ?, `, {, }) and
# without the backslash, which a browser turns into a slash.
_PATHNAME = re.compile(r"[!$%&'()*+,\-./0-9:;=@A-Z\[\]^_a-z|~]*")

_KINDS: dict[str, FeedbackKind] = {kind.value: kind for kind in FeedbackKind}
_AUTHORS: dict[str, FeedbackIdentity] = {author.value: author for author in FeedbackIdentity}

_ITEM_KEYS = frozenset({"kind", "comment", "page", "anchor", "viewport", "author"})
_ITEM_REQUIRED = frozenset({"kind", "comment", "page", "author"})
_PAGE_KEYS = frozenset({"path", "title"})
_ANCHOR_KEYS = frozenset({"selectors", "point", "area"})
_POINT_KEYS = frozenset({"x", "y", "pageX", "pageY"})
_AREA_KEYS = frozenset({"x", "y", "width", "height", "pageX", "pageY", "pageWidth", "pageHeight"})
_VIEWPORT_KEYS = frozenset({"width", "height", "dpr"})
_AUTHOR_KEYS = frozenset({"kind", "name"})

QUOTE = "TextQuoteSelector"
_POSITION = "TextPositionSelector"
CSS = "CssSelector"
# Per selector type: the keys it may have and the keys it must have.
_SELECTOR_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    QUOTE: (frozenset({"type", "exact", "prefix", "suffix"}), frozenset({"type", "exact"})),
    _POSITION: (frozenset({"type", "start", "end"}), frozenset({"type", "start", "end"})),
    CSS: (frozenset({"type", "value"}), frozenset({"type", "value"})),
}
_SELECTOR_TYPES: dict[str, str] = {name: name for name in _SELECTOR_KEYS}


class ProblemError(Exception):
    """A refusal, raised wherever it shows up and answered by `_widget_api`."""

    def __init__(self, status: int, message: Msg, *, retry_after: int | None = None) -> None:
        super().__init__(message.key)
        self.status = status
        self.message = message
        self.retry_after = retry_after


def bad_field(field: str) -> ProblemError:
    return ProblemError(422, Msg("FEEDBACK_INVALID", {"field": field}))


# --- Reading what a giver sends ---------------------------------------------


def giver_text(value: object, *, multiline: bool = False) -> str | None:
    """`value` when it is a string whose every character may stand in what a
    giver writes, else None."""
    if not isinstance(value, str):
        return None
    allowed = (_JOINERS | _KEPT_IN_PROSE) if multiline else _JOINERS
    for char in value:
        if char not in allowed and unicodedata.category(char) in _REFUSED_CATEGORIES:
            return None
    return value


def clean_title(value: str) -> str | None:
    """A page title is the page's, not the giver's: cleaned of what giver
    text may not hold and cut, never refused."""
    kept = "".join(
        char for char in value if char in _JOINERS or unicodedata.category(char) not in _REFUSED_CATEGORIES
    )
    return kept.strip()[:MAX_TITLE] or None


def _no_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not a number in JSON")


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    found = dict(pairs)
    if len(found) != len(pairs):
        raise ValueError("a key appears twice")
    return found


def _json(text: str, field: str) -> object:
    try:
        return json.loads(text, parse_constant=_no_constant, object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError):
        raise bad_field(field) from None


def _shape(value: object, field: str, allowed: frozenset[str], required: frozenset[str]) -> dict[str, Any]:
    """A JSON object with no key outside `allowed` and every key of `required`."""
    if not isinstance(value, dict) or not value.keys() <= allowed or not required <= value.keys():
        raise bad_field(field)
    return value


def _text(value: object, field: str, *, low: int, high: int, multiline: bool = False, trim: bool = False) -> str:
    text = giver_text(value, multiline=multiline)
    if text is None:
        raise bad_field(field)
    if trim:
        text = text.strip()
    if not low <= len(text) <= high:
        raise bad_field(field)
    return text


def _number(value: object, field: str, *, high: float = MAX_COORDINATE) -> int | float:
    # The range check also turns away the infinities that a number such as
    # 1e400 still parses to. NaN and Infinity are refused while parsing.
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= high:
        raise bad_field(field)
    return value


def _position(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_COORDINATE:
        raise bad_field(field)
    return value


def _choice[T](value: object, field: str, options: Mapping[str, T]) -> T:
    if not isinstance(value, str) or value not in options:
        raise bad_field(field)
    return options[value]


@dataclass(frozen=True)
class FeedbackInput:
    """An item as the widget sent it, after every check and with the anchor
    and viewport rebuilt from what passed."""

    kind: FeedbackKind
    comment: str
    # Relative to the target root, without a leading slash.
    page_path: str
    page_title: str | None
    anchor: dict[str, Any] | None
    quote: str | None
    viewport: dict[str, Any] | None
    author: FeedbackIdentity
    author_name: str | None


def parse_item(text: str, root: str, site: str) -> FeedbackInput:
    """The `item` part of a create, against the root of the token's target
    and the root of its site, both at the site's current address."""
    item = _shape(_json(text, "item"), "item", _ITEM_KEYS, _ITEM_REQUIRED)
    kind = _choice(item["kind"], "kind", _KINDS)
    comment = _text(item["comment"], "comment", low=1, high=MAX_COMMENT, multiline=True, trim=True)
    page = _shape(item["page"], "page", _PAGE_KEYS, frozenset({"path"}))
    anchor, quote = _anchor(kind, item.get("anchor"))
    author, name = _author(item["author"])
    return FeedbackInput(
        kind=kind,
        comment=comment,
        page_path=_page_path(page["path"], root, site),
        page_title=_title(page.get("title")),
        anchor=anchor,
        quote=quote,
        viewport=_viewport(item.get("viewport")),
        author=author,
        author_name=name,
    )


def _page_path(value: object, root: str, site: str) -> str:
    path = _text(value, "page.path", low=1, high=MAX_PAGE_PATH)
    if not _PATHNAME.fullmatch(path):
        raise bad_field("page.path")
    # A page served before its site or group got a new address still holds
    # a valid token, which names the site by its id: only a reload gives the
    # widget the address the site is at now.
    if not path.startswith(site):
        raise ProblemError(422, Msg("FEEDBACK_PAGE_MOVED"))
    if not path.startswith(root):
        raise bad_field("page.path")
    rest = path[len(root) :]
    segments = rest.split("/")
    # An empty segment anywhere but at the end would make the stored path
    # start with a slash or hide one inside it.
    if segments[0] in RESERVED_SEGMENTS or "" in segments[:-1] or any(map(_dot_segment, segments)):
        raise bad_field("page.path")
    return rest


def _dot_segment(segment: str) -> bool:
    # The URL standard reads %2e as a dot in a dot segment, in either case.
    return segment.lower().replace("%2e", ".") in (".", "..")


def _title(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise bad_field("page.title")
    return clean_title(value)


def _anchor(kind: FeedbackKind, value: object) -> tuple[dict[str, Any] | None, str | None]:
    """The stored anchor and the quote. Unknown keys and selector types are
    refused, and only what passed is stored."""
    if kind is FeedbackKind.PAGE:
        if value is not None:
            raise bad_field("anchor")
        return None, None
    anchor = _shape(value, "anchor", _ANCHOR_KEYS, frozenset({"selectors"}))
    selectors = anchor["selectors"]
    if not isinstance(selectors, list) or not 1 <= len(selectors) <= len(_SELECTOR_KEYS):
        raise bad_field("anchor.selectors")
    found: dict[str, dict[str, Any]] = {}
    for selector in selectors:
        named = selector.get("type") if isinstance(selector, dict) else None
        name = _choice(named, "anchor.selectors", _SELECTOR_TYPES)
        if name in found:
            raise bad_field("anchor.selectors")
        found[name] = _selector(name, selector)
    point = anchor.get("point")
    area = anchor.get("area")
    if kind is FeedbackKind.POINT:
        if found.keys() != {CSS}:
            raise bad_field("anchor.selectors")
        checked = _point(point)
        if area is not None:
            raise bad_field("anchor.area")
        return {"selectors": [found[CSS]], "point": checked, "area": None}, None
    if kind is FeedbackKind.AREA:
        if found.keys() != {CSS}:
            raise bad_field("anchor.selectors")
        if point is not None:
            raise bad_field("anchor.point")
        return {"selectors": [found[CSS]], "point": None, "area": _area(area)}, None
    if QUOTE not in found:
        raise bad_field("anchor.selectors")
    if point is not None:
        raise bad_field("anchor.point")
    if area is not None:
        raise bad_field("anchor.area")
    ordered = [found[name] for name in (QUOTE, _POSITION, CSS) if name in found]
    return {"selectors": ordered, "point": None, "area": None}, found[QUOTE]["exact"]


def _selector(name: str, value: object) -> dict[str, Any]:
    allowed, required = _SELECTOR_KEYS[name]
    selector = _shape(value, "anchor.selectors", allowed, required)
    if name == QUOTE:
        return {
            "type": name,
            "exact": _text(selector["exact"], "anchor.selectors", low=1, high=MAX_QUOTE),
            "prefix": _affix(selector.get("prefix")),
            "suffix": _affix(selector.get("suffix")),
        }
    if name == _POSITION:
        start = _position(selector["start"], "anchor.selectors")
        end = _position(selector["end"], "anchor.selectors")
        if start >= end:
            raise bad_field("anchor.selectors")
        return {"type": name, "start": start, "end": end}
    return {"type": name, "value": _text(selector["value"], "anchor.selectors", low=1, high=MAX_SELECTOR)}


def _affix(value: object) -> str:
    return "" if value is None else _text(value, "anchor.selectors", low=0, high=MAX_AFFIX)


def _point(value: object) -> dict[str, Any]:
    point = _shape(value, "anchor.point", _POINT_KEYS, _POINT_KEYS)
    return {
        "x": _number(point["x"], "anchor.point", high=1),
        "y": _number(point["y"], "anchor.point", high=1),
        "pageX": _number(point["pageX"], "anchor.point"),
        "pageY": _number(point["pageY"], "anchor.point"),
    }


def _area(value: object) -> dict[str, Any]:
    area = _shape(value, "anchor.area", _AREA_KEYS, _AREA_KEYS)
    return {
        "x": _number(area["x"], "anchor.area", high=1),
        "y": _number(area["y"], "anchor.area", high=1),
        "width": _number(area["width"], "anchor.area", high=1),
        "height": _number(area["height"], "anchor.area", high=1),
        "pageX": _number(area["pageX"], "anchor.area"),
        "pageY": _number(area["pageY"], "anchor.area"),
        "pageWidth": _number(area["pageWidth"], "anchor.area"),
        "pageHeight": _number(area["pageHeight"], "anchor.area"),
    }


def _viewport(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    viewport = _shape(value, "viewport", _VIEWPORT_KEYS, _VIEWPORT_KEYS)
    dpr = _number(viewport["dpr"], "viewport", high=MAX_DPR)
    if dpr == 0:
        raise bad_field("viewport")
    return {
        "width": _number(viewport["width"], "viewport"),
        "height": _number(viewport["height"], "viewport"),
        "dpr": dpr,
    }


def _author(value: object) -> tuple[FeedbackIdentity, str | None]:
    """The author kind and, for `name` only, the typed name. A name beside
    any other kind is refused: what the item says about its author comes from
    the session or from nowhere."""
    author = _shape(value, "author", _AUTHOR_KEYS, frozenset({"kind"}))
    kind = _choice(author["kind"], "author.kind", _AUTHORS)
    name = author.get("name")
    if kind is FeedbackIdentity.NAME:
        return kind, _text(name, "author.name", low=1, high=MAX_NAME, trim=True)
    if name is not None and (not isinstance(name, str) or name.strip()):
        raise bad_field("author.name")
    return kind, None


def reply_text(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise bad_field("body") from None
    reply = _shape(_json(text, "body"), "body", frozenset({"body"}), frozenset({"body"}))
    return _text(reply["body"], "body", low=1, high=MAX_COMMENT, multiline=True, trim=True)


__all__ = [
    "CSS",
    "QUOTE",
    "FeedbackInput",
    "ProblemError",
    "bad_field",
    "clean_title",
    "giver_text",
    "parse_item",
    "reply_text",
]

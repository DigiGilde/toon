"""The Mattermost post about new feedback.

Unlike the email, a post carries the comment itself: that is what makes a
channel worth having. The comment comes from a visitor, possibly anonymous,
and Mattermost renders Markdown, so nothing a visitor wrote is ever read as
Markdown or as a mention:

- The comment stands in an indented code block, every line indented by four
  spaces. A code block has no links, images, formatting or HTML, and unlike a
  fenced one it has no closing line that the text could forge: the block ends
  only at a line without the indent, and every line gets it.
- The page path stands in a code span. The path a widget sends holds no
  backtick (serving/feedback_input.py), so the span cannot be closed early.
- Every `@` in visitor text becomes U+FF20 FULLWIDTH COMMERCIAL AT, so
  `@channel`, `@all`, `@here` or `@someone` mentions nobody, whatever
  Mattermost does with code when it looks for mentions.
- Control and format characters other than a line break, a tab, U+200C and
  U+200D go, so no bidi override turns the post around. U+2028, U+2029 and a
  carriage return become a line break, so every line gets its indent.
- A comment is cut at 500 characters and 20 lines, and a post at
  MAX_POST characters, under Mattermost's own limit of 16383.

No name and no email address of the submitter goes into a post.
"""

from __future__ import annotations

import unicodedata

from toon.notifications import texts
from toon.notifications.content import Batch, Item, page_shown

MAX_COMMENT = 500
MAX_COMMENT_LINES = 20
MAX_POST = 12_000
FULLWIDTH_AT = "\uff20"
_KEPT_FORMAT = frozenset("\u200c\u200d")
_LINE_BREAKS = str.maketrans({"\r": "\n", "\u2028": "\n", "\u2029": "\n", "\x85": "\n"})


def neutralise(text: str) -> str:
    """Visitor text with every `@` made harmless, line breaks made plain and
    control and format characters taken out."""
    text = text.replace("\r\n", "\n").translate(_LINE_BREAKS)
    kept = (
        char
        for char in text
        if char in "\n\t" or char in _KEPT_FORMAT or unicodedata.category(char) not in ("Cc", "Cf")
    )
    return "".join(kept).replace("@", FULLWIDTH_AT)


def comment_block(comment: str, lang: str) -> str:
    """The comment as an indented code block, cut to size."""
    text = neutralise(comment).strip("\n")
    shortened = False
    if len(text) > MAX_COMMENT:
        text, shortened = text[:MAX_COMMENT], True
    lines = text.split("\n")
    if len(lines) > MAX_COMMENT_LINES:
        lines, shortened = lines[:MAX_COMMENT_LINES], True
    if shortened:
        lines[-1] = lines[-1] + "\u2026"
    block = "\n".join("    " + line for line in lines)
    return block + ("\n\n" + texts.t(lang, "shortened") if shortened else "")


def _item_text(lang: str, batch: Batch, item: Item) -> str:
    # The preview ref is a slug and the time is ours; only the page and the
    # comment come from the visitor.
    where = (
        texts.t(lang, "where.preview", ref=f"`{item.preview_ref}`")
        if item.target == "preview"
        else texts.t(lang, "where.live")
    )
    line = texts.t(
        lang,
        "item",
        kind=f"**{texts.t(lang, f'kind.{item.kind}')}**",
        page=f"`{neutralise(page_shown(item)).replace('`', '')}`",
        where=where,
        time=texts.when(lang, item.created_at),
    )
    if item.has_screenshot:
        line += ", " + texts.t(lang, "screenshot")
    return f"{line}\n[{texts.t(lang, 'open')}]({batch.item_url(item)})\n\n{comment_block(item.comment, lang)}"


def build_post(batch: Batch, lang: str) -> str:
    """The text of one post about every item of `batch`."""
    lang = texts.language(lang)
    count = len(batch.items)
    heading = texts.t(
        lang, "subject.one" if count == 1 else "subject.many", count=count, site=batch.site_label
    )
    parts = [f"#### {heading}"]
    length = len(parts[0])
    shown = 0
    for item in batch.listed:
        part = _item_text(lang, batch, item)
        if shown and length + len(part) + 2 > MAX_POST:
            break
        parts.append(part)
        length += len(part) + 2
        shown += 1
    if count > shown:
        parts.append(texts.t(lang, "more.post", count=count - shown, url=batch.tab_url))
    return "\n\n".join(parts)

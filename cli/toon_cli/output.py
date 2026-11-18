"""What comes from the server passes through here before it reaches the
terminal: control and format characters out, lengths capped."""

from __future__ import annotations

import re
import sys
import unicodedata

import httpx

# Suggested paths come from the server and go to the terminal: only ordinary
# relative paths are shown, so no answer can push line endings or control
# characters into the CI output.
SUGGESTION_PATH_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
MAX_SUGGESTIONS = 5
MAX_SUGGESTION_LENGTH = 255

# The same holds for the error message itself, which is free text and these
# days carries archive paths: C0 and C1 control characters (line endings and
# ANSI escapes included) become a space, so a line of its own never appears in
# the CI log.
CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
MAX_DETAIL_LENGTH = 2000
# The two format characters writing needs, which the server keeps in what it
# stores: a word in Persian or Hindi holds the zero-width non-joiner, and an
# emoji sequence the zero-width joiner.
JOINERS = frozenset({"\N{ZERO WIDTH NON-JOINER}", "\N{ZERO WIDTH JOINER}"})
# A page path or address the server builds prints whole: a path holds at most
# 2048 characters, which percent-encoding can triple, and a highlight fragment
# 2048 more. Past this one is cut with a visible marker, since a cut address
# opens another place.
MAX_URL_LENGTH = 16 * 1024
# Feedback text keeps its paragraphs: newline and tab stay, and the cap is the
# one the server holds a comment or reply to.
BLOCK_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
LINE_SEPARATOR_RE = re.compile("[\u2028\u2029]")
MAX_BLOCK_LENGTH = 5000


def _suggestions(data: object) -> list[str]:
    """The index.html paths the server suggests, stripped of everything that
    is not an ordinary relative path."""
    raw = data.get("indexCandidates") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return [
        path
        for path in raw[:MAX_SUGGESTIONS]
        if isinstance(path, str)
        and len(path) <= MAX_SUGGESTION_LENGTH
        and SUGGESTION_PATH_RE.match(path)
    ]


def _drop_format_chars(text: str) -> str:
    """Without the Unicode format characters (category Cf): bidi controls,
    zero-width spaces and the invisible tag characters. The JOINERS stay."""
    return "".join(
        char for char in text if char in JOINERS or unicodedata.category(char) != "Cf"
    )


def _clean(text: str, limit: int = MAX_DETAIL_LENGTH) -> str:
    """One line without control or format characters but the JOINERS, at
    most `limit` characters long: everything coming from the server passes
    through here before it reaches the output. U+2028 and U+2029 become a
    space too, since a reader that splits lines on them would otherwise start
    a new line there."""
    one_line = CONTROL_CHAR_RE.sub(" ", LINE_SEPARATOR_RE.sub(" ", text[:limit]))
    return _drop_format_chars(one_line).strip()


def _clean_block(text: str) -> str:
    """Free text of a visitor: newline, tab and the JOINERS stay, every other
    control and format character goes, Unicode line separators become
    newlines. Cut at MAX_BLOCK_LENGTH with a visible marker."""
    truncated = len(text) > MAX_BLOCK_LENGTH
    cleaned = BLOCK_CONTROL_RE.sub("", LINE_SEPARATOR_RE.sub("\n", text[:MAX_BLOCK_LENGTH]))
    cleaned = _drop_format_chars(cleaned)
    return f"{cleaned}\n(truncated)" if truncated else cleaned


def _print_problem_detail(response: httpx.Response) -> None:
    detail = None
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("title")
    if not detail:
        detail = response.text or f"HTTP {response.status_code}"
    print(f"Error: {_clean(str(detail))}", file=sys.stderr)

    suggestions = _suggestions(data)
    if not suggestions:
        return
    print("Found index.html in the bundle:", file=sys.stderr)
    for path in suggestions:
        print(f"  {path}", file=sys.stderr)
    shortest = suggestions[0]
    # Both forms included: whoever reads this in an action log has no flag to
    # set, but an input in their workflow YAML.
    if "/" in shortest:
        folder_path, _, _ = shortest.rpartition("/")
        print(
            f"Publish again with: --base-path {folder_path} "
            f"(in the action: base-path: {folder_path})",
            file=sys.stderr,
        )
    else:
        print(
            "Publish again without --base-path (in the action: without the "
            "base-path input): the index.html is in the root.",
            file=sys.stderr,
        )

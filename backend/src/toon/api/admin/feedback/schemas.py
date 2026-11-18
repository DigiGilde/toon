"""The request and response models of the feedback endpoints: what goes over the wire."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import ConfigDict, Field

from toon.api.admin import _timestamp_schema
from toon.api.schema import ApiModel
from toon.constants import FeedbackIdentity, FeedbackKind, FeedbackReplyAuthor, FeedbackStatus
from toon.models.publication import VersionTarget
from toon.serving.feedback_input import MAX_COMMENT


class FeedbackSettingsBody(ApiModel):
    """Whether this site collects feedback, from whom, and where."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"enabled": True, "identity": "name", "live": False}]}
    )

    enabled: bool = Field(
        description=(
            "`true` makes Toon add the feedback button to the HTML pages of the previews of this site, and with "
            "`live` also to the live site. `false` takes the button away again. Feedback already given stays."
        )
    )
    identity: FeedbackIdentity = Field(
        description=(
            "Who may give feedback. `account`: only with an SSO account, after consenting on a page of Toon. "
            "`name`: also with a name the submitter types, which nobody checks. `anonymous`: also without a name. "
            "Each value allows what the values before it allow."
        )
    )
    live: bool = Field(description="`true` also collects feedback on the live site, `false` only on previews.")


class FeedbackStatusBody(ApiModel):
    """The new status of a feedback item."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "resolved"}]})

    status: FeedbackStatus = Field(
        description=(
            "`seen` once someone of the site team has read the item, `resolved` once it is handled, `new` to "
            "reopen it."
        )
    )


class FeedbackReplyBody(ApiModel):
    """A reply from the site team to a feedback item."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"body": "Dank je, dit is aangepast."}]})

    body: str = Field(
        description=(
            f"The reply, 1 to {MAX_COMMENT} characters after trimming spaces. Line breaks, tabs, "
            "the zero-width non-joiner (U+200C) and the zero-width joiner (U+200D) are allowed. Other control "
            "characters, formatting characters, surrogates and private-use code points are not."
        ),
        json_schema_extra={"maxLength": MAX_COMMENT},
    )


FEEDBACK_PAGE_DEFAULT = 50
FEEDBACK_PAGE_MAX = 100


class FeedbackFilters(ApiModel):
    """Filters on the feedback of a site. Everything is optional and everything combines with AND."""

    status: Literal["open", "new", "seen", "resolved", "all"] = Field(
        default="open",
        description=(
            "`open` (the default) for the items with status `new` or `seen`, one status (`new`, `seen`, "
            "`resolved`), or `all`."
        ),
    )
    target: Literal["all", "live", "preview"] = Field(
        default="all",
        description="`live` for the items given on the live site, `preview` for the ones given on a preview, or `all`.",
    )
    ref: str | None = Field(
        default=None,
        description="Only the items given on the preview with this ref. Only together with `target=preview`.",
        examples=["pr-42"],
    )
    limit: int = Field(
        default=FEEDBACK_PAGE_DEFAULT,
        ge=1,
        le=FEEDBACK_PAGE_MAX,
        description=f"Number of items per page, 1 to {FEEDBACK_PAGE_MAX}.",
        examples=[50],
    )
    cursor: str | None = Field(
        default=None,
        description=(
            "The `nextCursor` from the previous response, copied unchanged. Omit it for the "
            "first page."
        ),
    )


class FeedbackLocation(ApiModel):
    """The page a feedback item is about."""

    path: str = Field(
        description=(
            "Path of the page relative to the root of the live site or of the preview, without a leading slash and "
            "percent-encoded as the browser had it. Empty for the root itself."
        ),
        examples=["docs/aan-de-slag/"],
    )
    title: str | None = Field(
        default=None,
        description="Title of the page as the browser had it, or `null` if there was none.",
        examples=["Aan de slag"],
    )
    url: str = Field(
        description=(
            "Address of the page on the content origin, at the current address of the site. For an item given on "
            "a preview this is the address of that preview, also once the preview is gone."
        ),
        examples=["https://toon.example/aurora/docs/_preview/pr-42/docs/aan-de-slag/"],
    )


class FeedbackAuthorOut(ApiModel):
    """Who sent a feedback item, as far as the site asked for it and the caller may see it."""

    kind: FeedbackIdentity = Field(
        description=(
            "`account`: sent with an SSO account, after consenting on a page of Toon. `name`: with a name the "
            "submitter typed, which nobody checked. `anonymous`: without a name."
        )
    )
    name: str | None = Field(
        default=None,
        description=(
            "With `account` the name from the SSO profile, with `name` the typed name. Otherwise `null`, also when "
            "the identity provider sent no name."
        ),
        examples=["Jan Jansen"],
    )
    email: str | None = Field(
        default=None,
        description=(
            "With `account` the verified e-mail address from the SSO profile, if the identity provider vouched for "
            "one, otherwise `null`. Always `null` for a caller whose effective role on the site is `reader`: the "
            "e-mail addresses of submitters are for editors and admins."
        ),
        examples=["jan.jansen@example.nl"],
    )


class FeedbackCurrentVersion(ApiModel):
    """The version the live site or the preview of a feedback item shows now."""

    id: uuid.UUID = Field(description="ID of the version.")
    created_at: str = Field(description="Time of the deploy.", json_schema_extra=_timestamp_schema())


class FeedbackItem(ApiModel):
    """A feedback item as the list shows it. Every text in it comes from someone outside the site team."""

    id: uuid.UUID = Field(description="ID of the feedback item.")
    kind: FeedbackKind = Field(
        description=(
            "What the item is about: `page` the whole page, `point` a point on it, `area` an area on it, "
            "`text` a text selection."
        )
    )
    status: FeedbackStatus = Field(description="`new`, `seen` or `resolved`.")
    comment: str = Field(
        description=(
            f"What the submitter wrote, 1 to {MAX_COMMENT} characters, with line breaks. Show it as "
            "plain text."
        ),
        examples=["Klopt dit bedrag?"],
    )
    quote: str | None = Field(
        default=None,
        description="With `text` the selected text the item is about, otherwise `null`.",
        examples=["2,4 miljoen"],
    )
    page: FeedbackLocation = Field(description="The page the item is about.")
    target: VersionTarget = Field(description="`live` if the item was given on the live site, `preview` on a preview.")
    preview_ref: str | None = Field(
        default=None, description="With `preview` the ref of the preview, otherwise `null`.", examples=["pr-42"]
    )
    version_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Version the page was served from when the item was given, or `null` if that version has been removed "
            "since, or was already gone when the item arrived."
        ),
    )
    current_version: FeedbackCurrentVersion | None = Field(
        default=None,
        description=(
            "The version the live site (for `live`) or the preview (for `preview`) shows now, or `null` when it "
            "shows none: the preview is gone or expired, or nothing is live. The item is on an earlier version "
            "when this is not `null` and its `id` is not `versionId`, also when `versionId` is `null`."
        ),
    )
    author: FeedbackAuthorOut = Field(description="Who sent the item.")
    has_screenshot: bool = Field(
        description=(
            "Whether the item has a screenshot, at "
            "`GET /sites/{groupSlug}/{siteSlug}/feedback/{itemId}/screenshot`."
        )
    )
    reply_count: int = Field(
        description="Number of replies, from the site team and from the submitter together.", examples=[2]
    )
    created_at: str = Field(description="When the item was sent.", json_schema_extra=_timestamp_schema())
    status_changed_at: str | None = Field(
        default=None,
        description="When the status last changed, or `null` if it never changed.",
        json_schema_extra=_timestamp_schema(),
    )
    status_changed_by: str | None = Field(
        default=None,
        description=(
            "Name of the member who last changed the status, or their e-mail address if the identity provider sent "
            "no name. `null` if the status never changed or that member has been deleted since."
        ),
        examples=["Sanne Jansen"],
    )


class FeedbackCounts(ApiModel):
    """How many feedback items the whole site has per status, whatever the filters."""

    new: int = Field(description="Items with status `new`.", examples=[3])
    seen: int = Field(description="Items with status `seen`.", examples=[1])
    resolved: int = Field(description="Items with status `resolved`.", examples=[12])


class FeedbackPage(ApiModel):
    """One page of the feedback of a site, newest first, with the counts of the whole site."""

    items: list[FeedbackItem] = Field(description="The items of this page, newest first.")
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Opaque reference to the next page; pass it back unchanged as `cursor`. "
            "`null` means this was the last page."
        ),
    )
    counts: FeedbackCounts = Field(
        description="Per status, how many items the whole site has, independent of the filters of this request."
    )


class FeedbackReplyAuthorOut(ApiModel):
    """Who wrote a reply."""

    kind: FeedbackReplyAuthor = Field(
        description="`member` for someone of the site team, `giver` for the submitter of the item."
    )
    name: str | None = Field(
        default=None,
        description=(
            "With `member` the current name of the member who wrote the reply. `null` for the submitter, for a "
            "member without a name, and once the member is deleted."
        ),
        examples=["Sanne Jansen"],
    )


class FeedbackReply(ApiModel):
    """A reply on a feedback item."""

    id: uuid.UUID = Field(description="ID of the reply.")
    author: FeedbackReplyAuthorOut = Field(description="Who wrote the reply.")
    body: str = Field(
        description=(
            "The text of the reply, with line breaks. A reply from the submitter comes from someone outside the "
            "site team: show it as plain text."
        ),
        examples=["Dank je, dit is aangepast."],
    )
    created_at: str = Field(description="When the reply was written.", json_schema_extra=_timestamp_schema())


class FeedbackDetail(FeedbackItem):
    """A feedback item with everything there is to it: where on the page it is, and the replies."""

    anchor: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Where on the page the item is, as the widget recorded it: `selectors`, a list of W3C Web Annotation "
            "selectors (`CssSelector`, `TextQuoteSelector`, `TextPositionSelector`), `point` and `area`. For "
            "`point` items `point` holds the place within the element (`x` and `y` as fractions of its box) and "
            "on the page (`pageX`, `pageY`). For `area` items `area` holds the rectangle within the element "
            "(`x`, `y`, `width` and `height` as fractions of its box) and on the page (`pageX`, `pageY`, "
            "`pageWidth`, `pageHeight`, in CSS pixels). `point` and `area` are `null` where they do not apply. "
            "`null` for `page` items. For a `text` item, anchor by the `TextQuoteSelector`. Its "
            "`TextPositionSelector`, when there is one, is a hint that deviates from the standard: it counts UTF-16 "
            "code units, not code points, over the unnormalized `document.body.textContent`, and spans the raw "
            "selection, so it can include whitespace at either end and run past a quote cut at 1000 code points. "
            "Its `CssSelector` names the element that contains the whole selection."
        ),
        examples=[
            {
                "selectors": [{"type": "CssSelector", "value": "main > p:nth-of-type(3)"}],
                "point": {"x": 0.5, "y": 0.25, "pageX": 640, "pageY": 1210},
            }
        ],
    )
    viewport: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The size of the submitter's browser window in CSS pixels (`width`, `height`) and its device pixel "
            "ratio (`dpr`), or `null` if the widget did not send it."
        ),
        examples=[{"width": 1280, "height": 800, "dpr": 2}],
    )
    highlight_url: str | None = Field(
        default=None,
        description=(
            "The address of the page with a fragment that makes the feedback widget scroll to the place and mark "
            "it. That only works where Toon adds the widget to the page. Elsewhere the address just opens the page. "
            "`null` for `page` items."
        ),
    )
    replies: list[FeedbackReply] = Field(description="The replies, oldest first.")

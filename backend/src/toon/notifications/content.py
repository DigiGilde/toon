"""What a notification is about: the items of one batch on one site, and the
admin addresses a message links to."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

# A message lists this many items and then says how many more there are,
# with a link to the Feedback tab.
MAX_LISTED_ITEMS = 20
# A page path is the site's address as the visitor's browser sent it, so it is
# shown, never followed, and cut where it stops being useful to read.
MAX_PAGE_SHOWN = 200


@dataclass(frozen=True)
class Item:
    id: uuid.UUID
    kind: str
    target: str
    preview_ref: str | None
    page_path: str
    created_at: datetime
    has_screenshot: bool
    comment: str


@dataclass(frozen=True)
class Batch:
    """The items of one message, oldest first, on the site `group`/`site`.
    `admin_base` is TOON_BASE_URL without a trailing slash."""

    admin_base: str
    group: str
    site: str
    items: tuple[Item, ...]

    @property
    def site_label(self) -> str:
        return f"{self.group}/{self.site}"

    @property
    def tab_url(self) -> str:
        return f"{self.admin_base}/{self.group}/{self.site}/feedback"

    @property
    def profile_url(self) -> str:
        return f"{self.admin_base}/-/profile"

    def item_url(self, item: Item) -> str:
        return f"{self.tab_url}/{item.id}"

    @property
    def listed(self) -> tuple[Item, ...]:
        return self.items[:MAX_LISTED_ITEMS]

    @property
    def unlisted(self) -> int:
        return max(0, len(self.items) - MAX_LISTED_ITEMS)


def page_shown(item: Item) -> str:
    page = "/" + item.page_path
    return page if len(page) <= MAX_PAGE_SHOWN else page[: MAX_PAGE_SHOWN - 1] + "\u2026"

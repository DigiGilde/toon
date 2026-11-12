"""Core constants for Toon: slug validation, the path vocabulary both hosts
share, access levels and roles."""

import enum
import re
import uuid
from dataclasses import dataclass

SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

# An old address keeps redirecting through this many days after a rename
# (slug_window.py), and stays reserved for its group or site until then.
SLUG_REDIRECT_DAYS = 30

# At most this many old addresses of one group or site redirect at once: a
# rename beyond that is refused until one has ended, and renaming back to one
# of them is always allowed.
MAX_PREVIOUS_SLUGS = 5


def path_under(path: str, prefix: str) -> bool:
    """Whether `path` is `prefix` itself or something below it.

    Compares on a segment boundary, so `/beheerder` is not under `/beheer`.
    Shared by the host separation and the SPA (platform/spa.py), which have to
    read a path the same way or a path would belong to both or to neither.
    """
    return path == prefix or path.startswith(prefix + "/")


# Locations the web pins down. They exist on both hosts, can never be a group,
# and the SPA fallback never swallows them: a crawler, a browser or an ACME
# client has to get the real answer or a 404 there, never an HTML page with
# status 200.
STANDARD_LOCATIONS = ("/robots.txt", "/favicon.ico", "/.well-known")



# SPA pages at the root of the admin host rather than under /-/, because a
# client opens them by URL (the CLI's device flow opens /cli-link). The
# Vue router matches them before /:group, so a group of that name would be
# unreachable in the SPA. frontend/src/router.ts holds the same list.
SPA_ROOT_PAGES = frozenset({"cli-link"})

# Group slugs the application refuses, and the source of a CHECK constraint on
# `groups.slug` (models/identity.py, migration 0001). The locations the web
# prescribes have to keep answering for themselves on the content host, so
# they can never be somebody's group; nor can a root-level SPA page.
RESERVED_SLUGS = frozenset(location.lstrip("/") for location in STANDARD_LOCATIONS) | SPA_ROOT_PAGES

# Where a version keeps the compressed copies of its text files
# (ingest/store.py).
ENCODED_SEGMENT = "_encoded"

# Refused as a top-level segment inside dists (unpacked content): these are
# the platform's own names under a site, so a bundle that carries one would
# shadow a preview, a version view or the compressed copies.
RESERVED_SEGMENTS = frozenset({"_preview", "_version", ENCODED_SEGMENT})

# The name both the ingest (which requires it in the root) and the serving
# (which looks it up for a directory request) key on. Case-sensitive: on the
# content volume `Index.html` is a different file.
INDEX_FILE = "index.html"

# Platform namespace: per SLUG_RE the segment "-" can never be a
# group, so /-/... is never content.
# Every app endpoint lives under it, on both hosts, which is what keeps the
# root of the admin host free for the SPA.
PLATFORM_SEGMENT = "-"
PLATFORM_PREFIX = f"/{PLATFORM_SEGMENT}"

# Set on the scope by head_requests.py when it hands the app a HEAD as GET, so
# a file response can still send its headers alone.
HEAD_SCOPE_KEY = "toon.head"

# Login path prefixes: shared between platform/pages.py (route
# registration) and ratelimit.py (class assignment), so a route change can
# never silently shift the rate-limit class of the login routes.
#
# The admin flow and the content flow land on the same spelling but stay two
# constants: they belong to two flows, and host separation and rate limiting
# both read them. A shared value is not a shared concept.
PATH_LOGIN = f"/{PLATFORM_SEGMENT}/login"
PATH_OAUTH2_PREFIX = f"/{PLATFORM_SEGMENT}/oauth2/"
PATH_LOGOUT = f"/{PLATFORM_SEGMENT}/logout"

# Where the OP delivers a back-channel logout token (OIDC Back-Channel Logout
# 1.0, platform/backchannel.py). Admin host only: belongs_to_content() refuses
# everything under /-/ that is not a content path or under the feedback prefix.
PATH_BACKCHANNEL_LOGOUT = f"/{PLATFORM_SEGMENT}/oidc/backchannel-logout"

PATH_CONTENT_LOGIN = f"/{PLATFORM_SEGMENT}/login"
PATH_CONTENT_OAUTH2_PREFIX = f"/{PLATFORM_SEGMENT}/oauth2/"
PATH_CONTENT_LOGOUT = f"/{PLATFORM_SEGMENT}/logout"

# Where the code of a secret link shared without it is handed in
# (serving/code_page.py). Host separation and rate limiting both single this
# path out.
PATH_CONTENT_CODE = f"/{PLATFORM_SEGMENT}/code"

# Site feedback on the content host: the widget bundle, its API, the consent
# page for feedback with an account and hiding the button. Host separation
# admits the prefix; every route under it checks the host itself, because on
# the admin host every /-/ path reaches the routes.
PATH_CONTENT_FEEDBACK_PREFIX = f"/{PLATFORM_SEGMENT}/feedback/"
PATH_FEEDBACK_API_PREFIX = f"{PATH_CONTENT_FEEDBACK_PREFIX}v1/"
PATH_FEEDBACK_WIDGET_PREFIX = f"{PATH_CONTENT_FEEDBACK_PREFIX}widget."
PATH_FEEDBACK_HIDE = f"{PATH_CONTENT_FEEDBACK_PREFIX}hide"
PATH_FEEDBACK_SHOW = f"{PATH_CONTENT_FEEDBACK_PREFIX}show"
PATH_FEEDBACK_ACCOUNT = f"{PATH_CONTENT_FEEDBACK_PREFIX}account"
PATH_FEEDBACK_ACCOUNT_STOP = f"{PATH_CONTENT_FEEDBACK_PREFIX}account/stop"

# Set on a site's path while a visitor has hidden the feedback button. Its
# value is a sealed hide mark for that one site that lasts twelve hours
# (serving/feedback_token.py). While a request carries a valid one, Toon
# injects nothing into that site's pages.
FEEDBACK_HIDE_COOKIE = "__Secure-toon-feedback-off"

# The replies one feedback item takes: from the visitor who sent it, and in
# all, the site team's included. The widget API and the admin API count them
# under the lock of `feedback_item_lock_key`.
FEEDBACK_MAX_GIVER_REPLIES = 20
FEEDBACK_MAX_REPLIES = 100


def feedback_item_lock_key(item_id: uuid.UUID) -> str:
    """What both reply routes hash for the transaction-scoped advisory lock on
    an item, taken before they count its replies: concurrent replies count
    one after the other, so neither cap is overshot."""
    return f"toon-feedback-item:{item_id}"


# Public liveness answer, admin host only (platform/health.py).
PATH_HEALTHZ = f"/{PLATFORM_SEGMENT}/healthz"


class AccessBase(enum.StrEnum):
    """The base answer to "who can look at this site": exactly one per site,
    and never the whole story.

    `nobody` grants no access on its own. A site on that base is unreachable unless
    one of the two exceptions beside it (secret links, invitees) is on, which is
    what makes "only through the exceptions below" expressible at all.
    """

    PUBLIC = "public"
    SSO = "sso"
    SITE_TEAM = "site_team"
    NOBODY = "nobody"


@dataclass(frozen=True)
class AccessPolicy:
    """A base plus the extras that widen it: who may look, in one value.

    A site, a group default and a preview override all carry these same three
    fields, so the gate never has to know which of the three it is holding.
    """

    base: AccessBase
    keys: bool = False
    invitees: bool = False

    @property
    def is_public(self) -> bool:
        """Whether anyone gets in without a credential of any kind.

        The extras cannot narrow a public base, so they do not enter into it.
        Serving reads this for private caching and for the noindex decision.
        """
        return self.base is AccessBase.PUBLIC

    @property
    def login_can_help(self) -> bool:
        """Whether having a session can grant what anonymity cannot.

        This, not the base alone, decides the login redirect: base `nobody`
        plus invitees still has to send an anonymous visitor to the login,
        while base `nobody` with only secret links must stay a neutral 404.
        """
        return self.base in (AccessBase.SSO, AccessBase.SITE_TEAM) or self.invitees


class Role(enum.StrEnum):
    """Role within a group or within a site.

    A reader looks on, an editor changes content, an admin changes policy: who
    may look, who may join in, and what disappears for good. The interface
    labels these Reader, Editor and Administrator in English and lezer,
    redacteur and beheerder in Dutch.
    """

    READER = "reader"
    EDITOR = "editor"
    ADMIN = "admin"


# The ranking is spelled out here instead of sitting implicitly in the
# declaration order, because "the widest wins" counts on it. A
# StrEnum compares as text, and that ordering is demonstrably wrong:
# 'editor' < 'admin' is false. So never compare two roles with < or >, always
# through this rank (access/roles.py).
ROLE_RANK: dict[Role, int] = {Role.READER: 1, Role.EDITOR: 2, Role.ADMIN: 3}


# Site feedback. The migration that creates the PostgreSQL types freezes these
# value lists, in this order; test_migrations.py holds the two together.


class FeedbackKind(enum.StrEnum):
    """What a feedback item is about: the whole page, a point on it, or a
    text selection."""

    PAGE = "page"
    POINT = "point"
    TEXT = "text"


class FeedbackAuthor(enum.StrEnum):
    """Who stands behind an item: a consented SSO account, a name the
    submitter typed (not verified), or nobody."""

    ACCOUNT = "account"
    NAME = "name"
    ANONYMOUS = "anonymous"


class FeedbackStatus(enum.StrEnum):
    NEW = "new"
    SEEN = "seen"
    RESOLVED = "resolved"


class FeedbackReplyAuthor(enum.StrEnum):
    MEMBER = "member"
    GIVER = "giver"


class FeedbackIdentity(enum.StrEnum):
    """A site's setting for who may give feedback. Ordered: each level allows
    the author kinds of the levels before it."""

    ACCOUNT = "account"
    NAME = "name"
    ANONYMOUS = "anonymous"


_IDENTITY_RANK: dict[FeedbackIdentity, int] = {
    FeedbackIdentity.ACCOUNT: 1,
    FeedbackIdentity.NAME: 2,
    FeedbackIdentity.ANONYMOUS: 3,
}

_AUTHOR_NEEDS: dict[FeedbackAuthor, FeedbackIdentity] = {
    FeedbackAuthor.ACCOUNT: FeedbackIdentity.ACCOUNT,
    FeedbackAuthor.NAME: FeedbackIdentity.NAME,
    FeedbackAuthor.ANONYMOUS: FeedbackIdentity.ANONYMOUS,
}


def identity_allows(identity: FeedbackIdentity, author: FeedbackAuthor) -> bool:
    """Whether a site set to `identity` accepts an item from `author`."""
    return _IDENTITY_RANK[identity] >= _IDENTITY_RANK[_AUTHOR_NEEDS[author]]

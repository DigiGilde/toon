"""Tests for serving/feedback_token.py: the page token, the link ticket, the
consent nonce and the hide mark of site feedback. Every ground for refusal
has its own case, then what a valid token reads back as and what minting
writes. No database needed."""

from __future__ import annotations

import uuid

import pytest

from toon.serving.feedback_seal import PURPOSE_CONSENT, PURPOSE_HIDE, PURPOSE_PAGE, seal, unseal
from toon.serving.feedback_token import (
    CONSENT_LIFETIME_S,
    HIDE_LIFETIME_S,
    LINK_LIFETIME_S,
    MAX_RETURN_TO_LENGTH,
    PURPOSE_LINK,
    TOKEN_LIFETIME_S,
    ConsentNonce,
    HideMark,
    LinkTicket,
    PageToken,
    mint_consent_nonce,
    mint_hide_mark,
    mint_link_ticket,
    mint_page_token,
    parse_consent_nonce,
    parse_hide_mark,
    parse_link_ticket,
    parse_page_token,
)

SECRET = "sessie-geheim-van-minstens-32-bytes!"
OTHER_SECRET = "ander-geheim-van-minstens-32-bytes!!"
NOW = 1_790_000_000
SITE = uuid.UUID("6f1c2a52-3c1e-4c55-9d0e-8a4b1f0e2d3c")
VERSION = uuid.UUID("0b0f6a43-5a8e-4f43-8d1e-1c7a3f9b2e10")
# The shape of secrets.token_urlsafe(32), which is what a Session.id is.
SESSION = "kV3o9Yt0pBq7Lr2Wm5Xc8Zd1Fg4Hj6Ks0Na3Pe9Ru2T"
KEY = "11111111-2222-4333-8444-555555555555"
RETURN_TO = "/fin/rapport/pagina.html?x=1"


def _page_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "v": 1,
        "site": str(SITE),
        "kind": "preview",
        "ref": "pr-42",
        "ver": str(VERSION),
        "ses": SESSION,
        "acc": None,
        "key": KEY,
        "exp": NOW + TOKEN_LIFETIME_S,
    }
    payload.update(changes)
    return payload


def _link_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {"key": KEY, "site": str(SITE), "exp": NOW + LINK_LIFETIME_S}
    payload.update(changes)
    return payload


def _consent_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "ses": SESSION,
        "site": str(SITE),
        "key": None,
        "returnTo": RETURN_TO,
        "exp": NOW + CONSENT_LIFETIME_S,
    }
    payload.update(changes)
    return payload


def _hide_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {"site": str(SITE), "exp": NOW + HIDE_LIFETIME_S}
    payload.update(changes)
    return payload


def _without(payload: dict[str, object], field: str) -> dict[str, object]:
    return {name: value for name, value in payload.items() if name != field}


def _page(payload: dict[str, object], *, now: int = NOW) -> PageToken | None:
    return parse_page_token(SECRET, seal(SECRET, PURPOSE_PAGE, payload), now=now)


def _link(payload: dict[str, object], *, now: int = NOW) -> LinkTicket | None:
    return parse_link_ticket(SECRET, seal(SECRET, PURPOSE_LINK, payload), now=now)


def _consent(payload: dict[str, object], *, now: int = NOW) -> ConsentNonce | None:
    return parse_consent_nonce(SECRET, seal(SECRET, PURPOSE_CONSENT, payload), now=now)


def _hide(payload: dict[str, object], *, now: int = NOW) -> HideMark | None:
    return parse_hide_mark(SECRET, seal(SECRET, PURPOSE_HIDE, payload), now=now)


def _mint_page(**changes: object) -> str:
    fields: dict[str, object] = {
        "site_id": SITE,
        "kind": "preview",
        "ref": "pr-42",
        "version_id": VERSION,
        "session_id": SESSION,
        "key_id": KEY,
        "now": NOW,
    }
    fields.update(changes)
    return mint_page_token(SECRET, **fields)


def _mint_link(**changes: object) -> str:
    fields: dict[str, object] = {"key_id": KEY, "site_id": SITE, "now": NOW}
    fields.update(changes)
    return mint_link_ticket(SECRET, **fields)


def _mint_consent(**changes: object) -> str:
    fields: dict[str, object] = {"session_id": SESSION, "site_id": SITE, "return_to": RETURN_TO, "now": NOW}
    fields.update(changes)
    return mint_consent_nonce(SECRET, **fields)


def _mint_hide(**changes: object) -> str:
    fields: dict[str, object] = {"site_id": SITE, "now": NOW}
    fields.update(changes)
    return mint_hide_mark(SECRET, **fields)


UUID_SPELLINGS = [
    pytest.param("geen-uuid", id="not-a-uuid"),
    pytest.param(str(SITE).upper(), id="capitals"),
    pytest.param("{" + str(SITE) + "}", id="braces"),
    pytest.param("urn:uuid:" + str(SITE), id="urn"),
    pytest.param(SITE.hex, id="no-dashes"),
    pytest.param("", id="empty"),
    pytest.param(42, id="number"),
    pytest.param(["6f1c2a52"], id="list"),
]

HANDLES_REFUSED = [
    pytest.param("", id="empty"),
    pytest.param("s" * 129, id="too-long"),
    pytest.param(42, id="number"),
    pytest.param(True, id="boolean"),
    pytest.param(["s"], id="list"),
]


class TestPageTokenRefusals:
    @pytest.mark.parametrize("token", [None, "", "kapot", 42])
    def test_not_a_token(self, token):
        assert parse_page_token(SECRET, token, now=NOW) is None

    def test_sealed_with_another_secret(self):
        assert parse_page_token(OTHER_SECRET, _mint_page(), now=NOW) is None

    def test_a_consent_nonce_is_no_page_token(self):
        """Purpose separation at the level of the tokens themselves: the same
        secret, a valid nonce, and still nothing."""
        assert parse_page_token(SECRET, _mint_consent(), now=NOW) is None
        assert parse_page_token(SECRET, seal(SECRET, PURPOSE_CONSENT, _page_payload()), now=NOW) is None

    def test_a_hide_mark_is_no_page_token(self):
        assert parse_page_token(SECRET, _mint_hide(), now=NOW) is None
        assert parse_page_token(SECRET, seal(SECRET, PURPOSE_HIDE, _page_payload()), now=NOW) is None

    def test_a_link_ticket_is_no_page_token(self):
        """The ticket goes into an address, the token never: whoever reads
        the ticket from a history or a log holds no credential of the widget
        API, not even with a page token's payload sealed for its purpose."""
        assert parse_page_token(SECRET, _mint_link(), now=NOW) is None
        assert parse_page_token(SECRET, seal(SECRET, PURPOSE_LINK, _page_payload()), now=NOW) is None

    @pytest.mark.parametrize("field", ["v", "site", "kind", "ref", "ver", "ses", "acc", "key", "exp"])
    def test_a_missing_field(self, field):
        assert _page(_without(_page_payload(), field)) is None

    def test_an_extra_field(self):
        assert _page(_page_payload(naam="Jan")) is None

    @pytest.mark.parametrize("version", [0, 2, True, "1", 1.0, None])
    def test_another_version(self, version):
        assert _page(_page_payload(v=version)) is None

    @pytest.mark.parametrize(
        "expires_at",
        [
            pytest.param(NOW, id="now"),
            pytest.param(NOW - 1, id="past"),
            pytest.param(NOW + TOKEN_LIFETIME_S + 61, id="beyond-the-lifetime"),
            pytest.param(True, id="boolean"),
            pytest.param(str(NOW + 100), id="string"),
            pytest.param(float(NOW + 100), id="float"),
            pytest.param(None, id="null"),
        ],
    )
    def test_expiry(self, expires_at):
        assert _page(_page_payload(exp=expires_at)) is None

    @pytest.mark.parametrize("site", UUID_SPELLINGS)
    def test_site(self, site):
        assert _page(_page_payload(site=site)) is None

    @pytest.mark.parametrize("kind", ["version", "Live", "", None, 1, ["live"], {"live": 1}])
    def test_kind(self, kind):
        assert _page(_page_payload(kind=kind)) is None

    @pytest.mark.parametrize("ref", ["pr-42", "", 42])
    def test_a_ref_on_live(self, ref):
        assert _page(_page_payload(kind="live", ref=ref)) is None

    @pytest.mark.parametrize(
        "ref",
        [
            pytest.param(None, id="null"),
            pytest.param("", id="empty"),
            pytest.param("PR-42", id="capitals"),
            pytest.param("pr_42", id="underscore"),
            pytest.param("pr-42\n", id="trailing-newline"),
            pytest.param("-pr", id="leading-hyphen"),
            pytest.param("pr-", id="trailing-hyphen"),
            pytest.param("a" * 64, id="too-long"),
            pytest.param("pr/42", id="slash"),
            pytest.param("pr-42é", id="non-ascii"),
            pytest.param(42, id="number"),
        ],
    )
    def test_a_ref_on_a_preview(self, ref):
        assert _page(_page_payload(ref=ref)) is None

    @pytest.mark.parametrize("version", UUID_SPELLINGS)
    def test_version(self, version):
        assert _page(_page_payload(ver=version)) is None

    @pytest.mark.parametrize("session", HANDLES_REFUSED)
    def test_session(self, session):
        assert _page(_page_payload(ses=session)) is None

    @pytest.mark.parametrize("session", HANDLES_REFUSED)
    def test_access_session(self, session):
        assert _page(_page_payload(ses=None, acc=session)) is None

    def test_a_session_in_both_roles(self):
        """`acc` stands in for a consent the session does not hold, so a
        token never carries both, not even for one and the same session."""
        assert _page(_page_payload(acc=SESSION)) is None
        assert _page(_page_payload(acc="ander-" + SESSION)) is None

    @pytest.mark.parametrize("key", HANDLES_REFUSED)
    def test_key(self, key):
        assert _page(_page_payload(key=key)) is None


class TestPageToken:
    def test_a_preview_token_reads_back(self):
        assert parse_page_token(SECRET, _mint_page(), now=NOW) == PageToken(
            site_id=SITE,
            kind="preview",
            ref="pr-42",
            version_id=VERSION,
            session_id=SESSION,
            access_session_id=None,
            key_id=KEY,
            expires_at=NOW + TOKEN_LIFETIME_S,
        )

    def test_a_live_token_reads_back(self):
        token = _mint_page(kind="live", ref=None, session_id=None, key_id=None)
        assert parse_page_token(SECRET, token, now=NOW) == PageToken(
            site_id=SITE,
            kind="live",
            ref=None,
            version_id=VERSION,
            session_id=None,
            access_session_id=None,
            key_id=None,
            expires_at=NOW + TOKEN_LIFETIME_S,
        )

    def test_a_token_with_the_session_for_the_gate_alone_reads_back(self):
        token = _mint_page(session_id=None, access_session_id=SESSION, key_id=None)
        parsed = parse_page_token(SECRET, token, now=NOW)
        assert parsed is not None
        assert (parsed.session_id, parsed.access_session_id, parsed.key_id) == (None, SESSION, None)
        assert unseal(SECRET, PURPOSE_PAGE, token) == _page_payload(ses=None, acc=SESSION, key=None)

    def test_a_token_without_a_version(self):
        parsed = parse_page_token(SECRET, _mint_page(version_id=None), now=NOW)
        assert parsed is not None
        assert parsed.version_id is None

    def test_the_longest_handles_and_ref_are_taken(self):
        parsed = parse_page_token(
            SECRET, _mint_page(ref="a" * 63, session_id="s" * 128, key_id="k" * 128), now=NOW
        )
        assert parsed is not None
        assert (parsed.ref, parsed.session_id, parsed.key_id) == ("a" * 63, "s" * 128, "k" * 128)
        access = parse_page_token(SECRET, _mint_page(session_id=None, access_session_id="a" * 128), now=NOW)
        assert access is not None
        assert access.access_session_id == "a" * 128

    def test_the_payload_holds_the_spec_fields_and_nothing_else(self):
        """Spec §16.5 names the fields. The widget never reads them,
        but the verifier and every later version of Toon do."""
        assert unseal(SECRET, PURPOSE_PAGE, _mint_page()) == _page_payload()

    def test_a_token_lasts_twelve_hours(self):
        token = _mint_page()
        assert parse_page_token(SECRET, token, now=NOW + TOKEN_LIFETIME_S - 1) is not None
        assert parse_page_token(SECRET, token, now=NOW + TOKEN_LIFETIME_S) is None

    def test_a_clock_that_stepped_back_a_minute_still_reads_it(self):
        token = _mint_page()
        assert parse_page_token(SECRET, token, now=NOW - 60) is not None
        assert parse_page_token(SECRET, token, now=NOW - 61) is None

    def test_minting_stores_nothing_and_draws_a_fresh_seal(self):
        assert _mint_page() != _mint_page()

    @pytest.mark.parametrize(
        "changes",
        [
            pytest.param({"kind": "version"}, id="version-kind"),
            pytest.param({"kind": "preview", "ref": None}, id="preview-without-ref"),
            pytest.param({"kind": "live", "ref": "pr-42"}, id="live-with-ref"),
            pytest.param({"ref": "PR-42"}, id="ref-not-a-slug"),
            pytest.param({"site_id": "geen-uuid"}, id="site-not-a-uuid"),
            pytest.param({"session_id": ""}, id="empty-session"),
            pytest.param({"session_id": None, "access_session_id": ""}, id="empty-access-session"),
            pytest.param({"access_session_id": SESSION}, id="a-session-in-both-roles"),
            pytest.param({"key_id": "k" * 129}, id="key-too-long"),
            pytest.param({"now": float(NOW)}, id="float-clock"),
        ],
    )
    def test_minting_refuses_what_parsing_would_refuse(self, changes):
        with pytest.raises(ValueError, match="no page token"):
            _mint_page(**changes)


class TestLinkTicketRefusals:
    """A ticket arrives in an address anyone can type: only one Toon sealed
    for this purpose, unexpired, with exactly its fields, counts."""

    @pytest.mark.parametrize("token", [None, "", "kapot", 42])
    def test_not_a_ticket(self, token):
        assert parse_link_ticket(SECRET, token, now=NOW) is None

    def test_sealed_with_another_secret(self):
        assert parse_link_ticket(OTHER_SECRET, _mint_link(), now=NOW) is None

    def test_a_page_token_a_consent_nonce_or_a_hide_mark_is_no_ticket(self):
        """A page token names the key as well, and every page hands one to
        its own scripts: it opens no consent page."""
        for minted in (_mint_page(), _mint_consent(key_id=KEY), _mint_hide()):
            assert parse_link_ticket(SECRET, minted, now=NOW) is None
        for purpose in (PURPOSE_PAGE, PURPOSE_CONSENT, PURPOSE_HIDE):
            assert parse_link_ticket(SECRET, seal(SECRET, purpose, _link_payload()), now=NOW) is None

    @pytest.mark.parametrize("field", ["key", "site", "exp"])
    def test_a_missing_field(self, field):
        assert _link(_without(_link_payload(), field)) is None

    def test_an_extra_field(self):
        assert _link(_link_payload(ses=SESSION)) is None

    @pytest.mark.parametrize("key", [*HANDLES_REFUSED, pytest.param(None, id="null")])
    def test_key(self, key):
        assert _link(_link_payload(key=key)) is None

    @pytest.mark.parametrize("site", [*UUID_SPELLINGS, pytest.param(None, id="null")])
    def test_site(self, site):
        assert _link(_link_payload(site=site)) is None

    @pytest.mark.parametrize(
        "expires_at",
        [
            pytest.param(NOW, id="now"),
            pytest.param(NOW - 1, id="past"),
            pytest.param(NOW + LINK_LIFETIME_S + 61, id="beyond-the-lifetime"),
            pytest.param(True, id="boolean"),
            pytest.param(str(NOW + 100), id="string"),
            pytest.param(None, id="null"),
        ],
    )
    def test_expiry(self, expires_at):
        assert _link(_link_payload(exp=expires_at)) is None


class TestLinkTicket:
    def test_a_ticket_reads_back(self):
        assert parse_link_ticket(SECRET, _mint_link(), now=NOW) == LinkTicket(
            key_id=KEY, site_id=SITE, expires_at=NOW + LINK_LIFETIME_S
        )

    def test_the_payload_holds_the_key_the_site_and_the_expiry_and_nothing_else(self):
        """Nothing about the visitor and no session: the ticket stands for
        the link alone."""
        assert unseal(SECRET, PURPOSE_LINK, _mint_link()) == _link_payload()

    def test_the_longest_key_is_taken(self):
        parsed = parse_link_ticket(SECRET, _mint_link(key_id="k" * 128), now=NOW)
        assert parsed is not None
        assert parsed.key_id == "k" * 128

    def test_a_ticket_ends_with_the_page_token_minted_beside_it(self):
        ticket, token = _mint_link(), _mint_page()
        assert parse_link_ticket(SECRET, ticket, now=NOW + TOKEN_LIFETIME_S - 1) is not None
        assert parse_page_token(SECRET, token, now=NOW + TOKEN_LIFETIME_S - 1) is not None
        assert parse_link_ticket(SECRET, ticket, now=NOW + TOKEN_LIFETIME_S) is None
        assert parse_page_token(SECRET, token, now=NOW + TOKEN_LIFETIME_S) is None

    def test_a_clock_that_stepped_back_a_minute_still_reads_it(self):
        ticket = _mint_link()
        assert parse_link_ticket(SECRET, ticket, now=NOW - 60) is not None
        assert parse_link_ticket(SECRET, ticket, now=NOW - 61) is None

    def test_a_ticket_goes_into_a_query_as_it_is(self):
        """Base64url: nothing an address has to escape."""
        assert set(_mint_link()) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")

    @pytest.mark.parametrize(
        "changes",
        [
            pytest.param({"key_id": ""}, id="empty-key"),
            pytest.param({"key_id": "k" * 129}, id="key-too-long"),
            pytest.param({"site_id": "geen-uuid"}, id="site-not-a-uuid"),
            pytest.param({"now": float(NOW)}, id="float-clock"),
        ],
    )
    def test_minting_refuses_what_parsing_would_refuse(self, changes):
        with pytest.raises(ValueError, match="no link ticket"):
            _mint_link(**changes)


class TestConsentNonceRefusals:
    @pytest.mark.parametrize("token", [None, "", "kapot", 42])
    def test_not_a_nonce(self, token):
        assert parse_consent_nonce(SECRET, token, now=NOW) is None

    def test_sealed_with_another_secret(self):
        assert parse_consent_nonce(OTHER_SECRET, _mint_consent(), now=NOW) is None

    def test_a_page_token_is_no_consent_nonce(self):
        assert parse_consent_nonce(SECRET, _mint_page(), now=NOW) is None
        assert parse_consent_nonce(SECRET, seal(SECRET, PURPOSE_PAGE, _consent_payload()), now=NOW) is None

    def test_a_hide_mark_is_no_consent_nonce(self):
        assert parse_consent_nonce(SECRET, _mint_hide(), now=NOW) is None
        assert parse_consent_nonce(SECRET, seal(SECRET, PURPOSE_HIDE, _consent_payload()), now=NOW) is None

    def test_a_link_ticket_is_no_consent_nonce(self):
        """A ticket sits in an address for twelve hours: posted as the form's
        nonce it consents to nothing."""
        assert parse_consent_nonce(SECRET, _mint_link(), now=NOW) is None
        assert parse_consent_nonce(SECRET, seal(SECRET, PURPOSE_LINK, _consent_payload()), now=NOW) is None

    @pytest.mark.parametrize("field", ["ses", "site", "key", "returnTo", "exp"])
    def test_a_missing_field(self, field):
        assert _consent(_without(_consent_payload(), field)) is None

    def test_an_extra_field(self):
        assert _consent(_consent_payload(v=1)) is None

    @pytest.mark.parametrize("session", [*HANDLES_REFUSED, pytest.param(None, id="null")])
    def test_session(self, session):
        assert _consent(_consent_payload(ses=session)) is None

    @pytest.mark.parametrize("site", [*UUID_SPELLINGS, pytest.param(None, id="null")])
    def test_site(self, site):
        assert _consent(_consent_payload(site=site)) is None

    @pytest.mark.parametrize("key", HANDLES_REFUSED)
    def test_key(self, key):
        assert _consent(_consent_payload(key=key)) is None

    @pytest.mark.parametrize(
        "return_to",
        [
            pytest.param(None, id="null"),
            pytest.param(42, id="number"),
            pytest.param("", id="empty"),
            pytest.param("fin/rapport/", id="relative"),
            pytest.param("https://evil.example/fin/rapport/", id="absolute"),
            pytest.param("//evil.example/fin/rapport/", id="scheme-relative"),
            pytest.param("/\\evil.example/rapport/", id="backslash"),
            pytest.param("/\t/evil.example/rapport/", id="tab"),
            pytest.param("/fin/rapport/\r\nSet-Cookie: x=y", id="crlf"),
            pytest.param("/fin/rapport/\x7f", id="del"),
            pytest.param("/", id="root"),
            pytest.param("/fin", id="group-only"),
            pytest.param("/-/feedback/account", id="platform-namespace"),
            pytest.param("/Fin/rapport/", id="group-not-a-slug"),
            pytest.param("/fin/rapport/" + "x" * (MAX_RETURN_TO_LENGTH - len("/fin/rapport/")), id="too-long"),
        ],
    )
    def test_return_to(self, return_to):
        assert _consent(_consent_payload(returnTo=return_to)) is None

    @pytest.mark.parametrize(
        "expires_at",
        [
            pytest.param(NOW, id="now"),
            pytest.param(NOW + CONSENT_LIFETIME_S + 61, id="beyond-the-lifetime"),
            pytest.param(NOW + TOKEN_LIFETIME_S, id="a-page-token-lifetime"),
            pytest.param(True, id="boolean"),
            pytest.param(None, id="null"),
        ],
    )
    def test_expiry(self, expires_at):
        assert _consent(_consent_payload(exp=expires_at)) is None


class TestConsentNonce:
    def test_a_nonce_reads_back(self):
        assert parse_consent_nonce(SECRET, _mint_consent(), now=NOW) == ConsentNonce(
            session_id=SESSION, site_id=SITE, key_id=None, return_to=RETURN_TO, expires_at=NOW + CONSENT_LIFETIME_S
        )

    def test_a_nonce_with_the_secret_link_of_its_ticket_reads_back(self):
        assert parse_consent_nonce(SECRET, _mint_consent(key_id=KEY), now=NOW) == ConsentNonce(
            session_id=SESSION, site_id=SITE, key_id=KEY, return_to=RETURN_TO, expires_at=NOW + CONSENT_LIFETIME_S
        )
        longest = parse_consent_nonce(SECRET, _mint_consent(key_id="k" * 128), now=NOW)
        assert longest is not None
        assert longest.key_id == "k" * 128

    def test_the_payload_holds_the_spec_fields_and_nothing_else(self):
        assert unseal(SECRET, PURPOSE_CONSENT, _mint_consent()) == _consent_payload()
        assert unseal(SECRET, PURPOSE_CONSENT, _mint_consent(key_id=KEY)) == _consent_payload(key=KEY)

    def test_the_longest_return_to_is_taken(self):
        longest = "/fin/rapport/" + "x" * (MAX_RETURN_TO_LENGTH - 1 - len("/fin/rapport/"))
        parsed = parse_consent_nonce(SECRET, _mint_consent(return_to=longest), now=NOW)
        assert parsed is not None
        assert parsed.return_to == longest

    def test_a_nonce_lasts_ten_minutes(self):
        nonce = _mint_consent()
        assert parse_consent_nonce(SECRET, nonce, now=NOW + CONSENT_LIFETIME_S - 1) is not None
        assert parse_consent_nonce(SECRET, nonce, now=NOW + CONSENT_LIFETIME_S) is None

    def test_a_clock_that_stepped_back_a_minute_still_reads_it(self):
        nonce = _mint_consent()
        assert parse_consent_nonce(SECRET, nonce, now=NOW - 60) is not None
        assert parse_consent_nonce(SECRET, nonce, now=NOW - 61) is None

    @pytest.mark.parametrize(
        "changes",
        [
            pytest.param({"return_to": "//evil.example/fin/rapport/"}, id="foreign-return-to"),
            pytest.param({"return_to": "/"}, id="not-a-site-path"),
            pytest.param({"return_to": "/fin/rapport/" + "x" * MAX_RETURN_TO_LENGTH}, id="return-to-too-long"),
            pytest.param({"session_id": ""}, id="empty-session"),
            pytest.param({"site_id": "geen-uuid"}, id="site-not-a-uuid"),
            pytest.param({"key_id": ""}, id="empty-key"),
            pytest.param({"key_id": "k" * 129}, id="key-too-long"),
        ],
    )
    def test_minting_refuses_what_parsing_would_refuse(self, changes):
        with pytest.raises(ValueError, match="no consent nonce"):
            _mint_consent(**changes)

    def test_minting_refuses_a_return_to_too_large_to_seal(self):
        """Under the character limit, but every non-ASCII character takes six
        bytes of JSON: a nonce that could not be read back is not minted."""
        return_to = "/fin/rapport/" + "é" * 700
        assert len(return_to) < MAX_RETURN_TO_LENGTH
        with pytest.raises(ValueError, match="longer than unseal reads"):
            _mint_consent(return_to=return_to)


class TestHideMarkRefusals:
    """The value of the hide cookie is anything a page's own script or a
    sibling host cares to write: only a mark Toon sealed counts."""

    @pytest.mark.parametrize("value", [None, "", "1", "kapot", 42])
    def test_not_a_mark(self, value):
        assert parse_hide_mark(SECRET, value, now=NOW) is None

    def test_sealed_with_another_secret(self):
        assert parse_hide_mark(OTHER_SECRET, _mint_hide(), now=NOW) is None

    def test_a_page_token_or_a_consent_nonce_is_no_hide_mark(self):
        """A page token is in every page the widget goes into, so it must not
        pass for a mark that hides the button."""
        assert parse_hide_mark(SECRET, _mint_page(), now=NOW) is None
        assert parse_hide_mark(SECRET, _mint_consent(), now=NOW) is None
        assert parse_hide_mark(SECRET, seal(SECRET, PURPOSE_PAGE, _hide_payload()), now=NOW) is None
        assert parse_hide_mark(SECRET, seal(SECRET, PURPOSE_CONSENT, _hide_payload()), now=NOW) is None

    def test_a_link_ticket_is_no_hide_mark(self):
        assert parse_hide_mark(SECRET, _mint_link(), now=NOW) is None
        assert parse_hide_mark(SECRET, seal(SECRET, PURPOSE_LINK, _hide_payload()), now=NOW) is None

    @pytest.mark.parametrize("field", ["site", "exp"])
    def test_a_missing_field(self, field):
        assert _hide(_without(_hide_payload(), field)) is None

    def test_an_extra_field(self):
        assert _hide(_hide_payload(ses=SESSION)) is None

    @pytest.mark.parametrize("site", [*UUID_SPELLINGS, pytest.param(None, id="null")])
    def test_site(self, site):
        assert _hide(_hide_payload(site=site)) is None

    @pytest.mark.parametrize(
        "expires_at",
        [
            pytest.param(NOW, id="now"),
            pytest.param(NOW - 1, id="past"),
            pytest.param(NOW + HIDE_LIFETIME_S + 61, id="beyond-the-lifetime"),
            pytest.param(True, id="boolean"),
            pytest.param(str(NOW + 100), id="string"),
            pytest.param(None, id="null"),
        ],
    )
    def test_expiry(self, expires_at):
        assert _hide(_hide_payload(exp=expires_at)) is None


class TestHideMark:
    def test_a_mark_reads_back(self):
        assert parse_hide_mark(SECRET, _mint_hide(), now=NOW) == HideMark(
            site_id=SITE, expires_at=NOW + HIDE_LIFETIME_S
        )

    def test_the_payload_holds_the_site_and_the_expiry_and_nothing_else(self):
        """Nothing about the visitor: the mark names a site and a moment."""
        assert unseal(SECRET, PURPOSE_HIDE, _mint_hide()) == _hide_payload()

    def test_a_mark_lasts_twelve_hours(self):
        mark = _mint_hide()
        assert HIDE_LIFETIME_S == 12 * 3600
        assert parse_hide_mark(SECRET, mark, now=NOW + HIDE_LIFETIME_S - 1) is not None
        assert parse_hide_mark(SECRET, mark, now=NOW + HIDE_LIFETIME_S) is None

    def test_a_clock_that_stepped_back_a_minute_still_reads_it(self):
        mark = _mint_hide()
        assert parse_hide_mark(SECRET, mark, now=NOW - 60) is not None
        assert parse_hide_mark(SECRET, mark, now=NOW - 61) is None

    def test_a_mark_is_written_in_cookie_characters_only(self):
        """Base64url: nothing a Set-Cookie line would have to quote."""
        assert set(_mint_hide()) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")

    @pytest.mark.parametrize(
        "changes",
        [
            pytest.param({"site_id": "geen-uuid"}, id="site-not-a-uuid"),
            pytest.param({"now": float(NOW)}, id="float-clock"),
        ],
    )
    def test_minting_refuses_what_parsing_would_refuse(self, changes):
        with pytest.raises(ValueError, match="no hide mark"):
            _mint_hide(**changes)

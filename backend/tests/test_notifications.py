"""Notifications of new feedback without a database (docs/design.md §16.16):
the words in both languages, the email, the Mattermost post with hostile
visitor text, the webhook URL guard, the one way Toon posts, and SMTP.

Special characters are written as chr() so this file holds none of them
itself.
"""

from __future__ import annotations

import json
import smtplib
import ssl
import uuid
from datetime import UTC, datetime
from email import message_from_bytes
from email.policy import default as default_policy
from string import Formatter

import httpx
import pytest

from toon.config import Settings
from toon.notifications import mattermost, texts
from toon.notifications.content import MAX_LISTED_ITEMS, MAX_PAGE_SHOWN, Batch, Item
from toon.notifications.mail import SMTP_TIMEOUT_S, SmtpMailer, build_email
from toon.notifications.webhooks import (
    TIMEOUT,
    WebhookDeliveryError,
    WebhookUrlError,
    check_url,
    make_client,
    post,
)

ADMIN = "https://beheer.toon.example"
ALLOWED = frozenset({("mattermost.example.org", 443), ("chat.example.org", 8443)})
HOOK = "https://mattermost.example.org/hooks/xn1x3s7n6pnqbbz8m9zjqsn5rw"
NOW = datetime(2026, 11, 21, 13, 3, tzinfo=UTC)
COMMENT = "Het bedrag in de tweede alinea klopt niet, zie de begroting."
FULLWIDTH_AT = chr(0xFF20)
ELLIPSIS = chr(0x2026)


def _item(**values) -> Item:
    fields = {
        "id": uuid.UUID("11111111-2222-3333-4444-555555555555"),
        "kind": "point",
        "target": "live",
        "preview_ref": None,
        "page_path": "docs/intro.html",
        "created_at": NOW,
        "has_screenshot": False,
        "comment": COMMENT,
        **values,
    }
    return Item(**fields)


def _batch(*items: Item) -> Batch:
    return Batch(admin_base=ADMIN, group="aurora", site="docs", items=items or (_item(),))


def _settings(**values) -> Settings:
    return Settings(
        db_url="postgresql+asyncpg://toon@localhost/toon",
        content_root="/tmp/toon-content",  # noqa: S108 - never touched here
        oidc_issuer="https://idp.example",
        oidc_client_id="toon",
        oidc_client_private_jwk="{}",
        session_secret="sessie-geheim-van-minstens-32-bytes!",
        audit_pepper="audit-pepper-van-minstens-32-bytes!!",
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        content_base_url="https://toon.example",
        base_url=ADMIN,
        environment="dev",
        **values,
    )


# -- The words --------------------------------------------------------------


def test_both_languages_hold_the_same_keys_and_placeholders() -> None:
    assert set(texts.NL) == set(texts.EN)
    for key, dutch in texts.NL.items():
        fields = {name for _, name, _, _ in Formatter().parse(dutch) if name}
        assert fields == {name for _, name, _, _ in Formatter().parse(texts.EN[key]) if name}, key
        assert dutch.strip() and texts.EN[key].strip(), key


def test_a_member_without_a_language_reads_english() -> None:
    assert texts.language(None) == "en"
    assert texts.language("") == "en"
    assert texts.language("fr") == "en"
    assert texts.language("nl") == "nl"


def test_time_is_amsterdam_time_written_per_language() -> None:
    assert texts.when("nl", NOW) == "21-11-2026 om 14:03 (Nederlandse tijd)"
    assert texts.when("en", NOW) == "2026-11-21 at 14:03 (Amsterdam time)"
    summer = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    assert texts.when("en", summer) == "2026-07-01 at 12:00 (Amsterdam time)"


# -- The email --------------------------------------------------------------


def _email(batch: Batch, lang: str = "en"):
    message = build_email(batch, lang=lang, to="lid@example.nl", sender="noreply@example.org", now=NOW)
    return message_from_bytes(message.as_bytes(), policy=default_policy)


def _body(parsed) -> str:
    return parsed.get_content().replace("\r\n", "\n")


def test_the_email_names_metadata_and_links_to_each_item() -> None:
    preview = _item(
        id=uuid.UUID("99999999-2222-3333-4444-555555555555"),
        kind="area",
        target="preview",
        preview_ref="pr-12",
        page_path="",
        has_screenshot=True,
    )
    parsed = _email(_batch(_item(), preview))
    body = _body(parsed)
    assert parsed["Subject"] == "2 new feedback items on aurora/docs"
    assert parsed["To"] == "lid@example.nl"
    assert parsed["From"] == "noreply@example.org"
    assert parsed["Auto-Submitted"] == "auto-generated"
    assert parsed["List-Unsubscribe"] == f"<{ADMIN}/aurora/docs/feedback>"
    assert parsed["Content-Language"] == "en"
    assert parsed["Message-ID"].endswith("@example.org>")
    assert parsed.get_content_type() == "text/plain"
    assert "There are 2 new feedback items on aurora/docs:" in body
    assert "1. Point on /docs/intro.html, the live site, 2026-11-21 at 14:03 (Amsterdam time)" in body
    assert f"   {ADMIN}/aurora/docs/feedback/11111111-2222-3333-4444-555555555555" in body
    assert "2. Area on /, preview pr-12, 2026-11-21 at 14:03 (Amsterdam time), with screenshot" in body
    assert f"{ADMIN}/-/profile" in body


def test_one_item_reads_as_one_in_dutch() -> None:
    parsed = _email(_batch(_item(kind="page")), lang="nl")
    body = _body(parsed)
    assert parsed["Subject"] == "Nieuwe feedback op aurora/docs"
    assert "Er is nieuwe feedback op aurora/docs:" in body
    assert "1. Hele pagina op /docs/intro.html, de live site, 21-11-2026 om 14:03 (Nederlandse tijd)" in body
    assert "tabblad Feedback" in body


def test_the_email_holds_no_comment_and_no_submitter() -> None:
    """Even when the item it is handed carries the comment, which the job
    never loads for an email."""
    hostile = _item(comment="Jan Jansen <jan@example.nl> zegt: geheim!")
    body = _body(_email(_batch(hostile)))
    assert "Jan" not in body
    assert "jan@example.nl" not in body
    assert "geheim" not in body


def test_the_email_lists_twenty_items_and_counts_the_rest() -> None:
    items = tuple(_item(id=uuid.uuid4()) for _ in range(MAX_LISTED_ITEMS + 3))
    parsed = _email(_batch(*items))
    body = _body(parsed)
    assert parsed["Subject"] == f"{MAX_LISTED_ITEMS + 3} new feedback items on aurora/docs"
    assert f"{MAX_LISTED_ITEMS}. Point" in body
    assert f"{MAX_LISTED_ITEMS + 1}. Point" not in body
    assert f"And 3 more on the Feedback tab:\n{ADMIN}/aurora/docs/feedback" in body


def test_a_long_page_path_is_cut() -> None:
    body = _body(_email(_batch(_item(page_path="a" * 500))))
    assert "/" + "a" * (MAX_PAGE_SHOWN - 2) + ELLIPSIS in body
    assert "a" * MAX_PAGE_SHOWN not in body


def test_an_address_that_would_add_a_header_is_refused() -> None:
    with pytest.raises(ValueError):
        build_email(_batch(), lang="en", to="lid@example.nl\r\nBcc: x@evil.test", sender="noreply@example.org", now=NOW)


# -- The Mattermost post ----------------------------------------------------


def _code_lines(post: str) -> list[str]:
    return [line for line in post.split("\n") if line.startswith("    ")]


def test_the_post_carries_the_comment_in_a_code_block() -> None:
    post = mattermost.build_post(_batch(_item(has_screenshot=True)), "en")
    assert post.startswith("#### New feedback on aurora/docs\n\n")
    assert (
        "**Point** on `/docs/intro.html`, the live site, 2026-11-21 at 14:03 (Amsterdam time), with screenshot"
    ) in post
    assert f"[Open in Toon]({ADMIN}/aurora/docs/feedback/11111111-2222-3333-4444-555555555555)" in post
    assert f"\n\n    {COMMENT}" in post


def test_the_post_in_dutch_names_the_preview() -> None:
    post = mattermost.build_post(_batch(_item(target="preview", preview_ref="pr-7", kind="text")), "nl")
    assert "#### Nieuwe feedback op aurora/docs" in post
    assert "**Tekst** op `/docs/intro.html`, preview `pr-7`" in post
    assert "[Openen in Toon]" in post


@pytest.mark.parametrize(
    "hostile",
    [
        "@channel kijk hier",
        "@all @here",
        "hoi @robbert.bos en @jan_jansen",
        "mail@example.nl",
    ],
)
def test_no_mention_survives(hostile: str) -> None:
    post = mattermost.build_post(_batch(_item(comment=hostile)), "en")
    assert "@" not in post
    assert FULLWIDTH_AT in post


@pytest.mark.parametrize(
    "hostile",
    [
        "[klik hier](https://evil.test/phish)",
        "![pixel](https://evil.test/track.png)",
        "<img src=https://evil.test/x.png>",
        "<script>alert(1)</script>",
        "https://evil.test/bare",
        "**vet** _schuin_ ~~weg~~ # kop",
        "> citaat\n- lijst\n1. genummerd",
        "| a | b |\n|---|---|\n| c | d |",
        ":emoji: :+1:",
    ],
)
def test_markdown_from_a_visitor_stays_inside_the_code_block(hostile: str) -> None:
    post = mattermost.build_post(_batch(_item(comment=hostile)), "en")
    _, _, block = post.partition("\n\n    ")
    lines = ("    " + block).split("\n")
    assert all(line.startswith("    ") for line in lines)
    assert [line[4:] for line in lines] == hostile.split("\n")


@pytest.mark.parametrize("fence", ["```", "~~~", "````\n```", "    ", "\t"])
def test_a_forged_fence_or_indent_cannot_end_the_block(fence: str) -> None:
    hostile = f"{fence}\n**uit het blok** [link](https://evil.test)\n{fence}"
    post = mattermost.build_post(_batch(_item(comment=hostile)), "en")
    escaped = [line for line in post.split("\n") if "evil.test" in line]
    assert escaped
    assert all(line.startswith("    ") for line in escaped)


def test_every_kind_of_line_break_gets_its_indent() -> None:
    breaks = ["\r\n", "\r", chr(0x2028), chr(0x2029), chr(0x85), "\n\n\n"]
    comment = "x".join(f"line{index}{brk}" for index, brk in enumerate(breaks)) + "end"
    post = mattermost.build_post(_batch(_item(comment=comment)), "en")
    body = post.split("\n\n    ", 1)[1]
    for line in ("    " + body).split("\n"):
        assert line.startswith("    ") or line == ""
    assert "\r" not in post
    assert chr(0x2028) not in post


def test_control_and_bidi_characters_go_but_joiners_stay() -> None:
    rlo, zwsp, bell = chr(0x202E), chr(0x200B), chr(0x07)
    zwnj, zwj = chr(0x200C), chr(0x200D)
    family = chr(0x1F468) + zwj + chr(0x1F469)
    comment = f"a{rlo}b{zwsp}c{bell}d\te{zwnj}f {family} caf" + chr(0xE9) + " " + chr(0x4E2D) + chr(0x6587)
    post = mattermost.build_post(_batch(_item(comment=comment)), "en")
    assert rlo not in post and zwsp not in post and bell not in post
    assert "abcd\te" + zwnj + "f " + family in post
    assert "caf" + chr(0xE9) + " " + chr(0x4E2D) + chr(0x6587) in post


def test_a_long_comment_is_cut_at_500_characters() -> None:
    post = mattermost.build_post(_batch(_item(comment="x" * 5000)), "en")
    assert "    " + "x" * mattermost.MAX_COMMENT + ELLIPSIS + "\n\n(shortened)" in post
    assert "x" * (mattermost.MAX_COMMENT + 1) not in post


def test_a_comment_of_many_lines_is_cut_at_20() -> None:
    post = mattermost.build_post(_batch(_item(comment="\n".join(f"r{n}" for n in range(60)))), "nl")
    assert len(_code_lines(post)) == mattermost.MAX_COMMENT_LINES
    assert f"    r19{ELLIPSIS}" in post
    assert "r20" not in post
    assert "(ingekort)" in post


def test_a_short_comment_is_not_marked_shortened() -> None:
    post = mattermost.build_post(_batch(), "en")
    assert "(shortened)" not in post
    assert ELLIPSIS not in post


def test_the_page_path_cannot_close_its_code_span_or_mention() -> None:
    post = mattermost.build_post(_batch(_item(page_path="a`b@channel/[x](y)")), "en")
    assert "`/ab" + FULLWIDTH_AT + "channel/[x](y)`" in post


def test_a_batch_lists_twenty_and_links_to_the_rest() -> None:
    items = tuple(_item(id=uuid.uuid4(), comment="kort") for _ in range(25))
    post = mattermost.build_post(_batch(*items), "en")
    assert post.startswith("#### 25 new feedback items on aurora/docs")
    assert post.count("[Open in Toon]") == MAX_LISTED_ITEMS
    assert post.endswith(f"And 5 more on the [Feedback tab]({ADMIN}/aurora/docs/feedback).")


def test_a_post_stays_under_its_size_whatever_the_comments() -> None:
    worst = "\n".join(["y" * 24] * 20)
    items = tuple(_item(id=uuid.uuid4(), comment=worst) for _ in range(MAX_LISTED_ITEMS))
    post = mattermost.build_post(_batch(*items), "en")
    assert len(post) <= mattermost.MAX_POST + 200
    shown = post.count("[Open in Toon]")
    assert 0 < shown < MAX_LISTED_ITEMS
    assert f"And {MAX_LISTED_ITEMS - shown} more on the [Feedback tab]" in post


# -- The webhook URL --------------------------------------------------------


def test_an_allowed_url_gives_its_host_and_port() -> None:
    target = check_url(HOOK, ALLOWED)
    assert (target.host, target.port) == ("mattermost.example.org", 443)
    target = check_url("HTTPS://Chat.Example.org:8443/hooks/abc", ALLOWED)
    assert (target.host, target.port) == ("chat.example.org", 8443)
    assert check_url("https://mattermost.example.org:443/hooks/abc", ALLOWED).port == 443


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("http://mattermost.example.org/hooks/abc", "WEBHOOK_URL_INVALID.scheme"),
        ("ftp://mattermost.example.org/hooks/abc", "WEBHOOK_URL_INVALID.scheme"),
        ("mattermost.example.org/hooks/abc", "WEBHOOK_URL_INVALID.scheme"),
        ("https://user:pw@mattermost.example.org/hooks/abc", "WEBHOOK_URL_INVALID.credentials"),
        ("https://user@mattermost.example.org/hooks/abc", "WEBHOOK_URL_INVALID.credentials"),
        ("https://evil.test@mattermost.example.org/hooks/abc", "WEBHOOK_URL_INVALID.credentials"),
        ("https://127.0.0.1/hooks/abc", "WEBHOOK_URL_INVALID.ip_literal"),
        ("https://10.0.0.5:443/hooks/abc", "WEBHOOK_URL_INVALID.ip_literal"),
        ("https://[::1]/hooks/abc", "WEBHOOK_URL_INVALID.ip_literal"),
        ("https://[fd00::1]:8443/hooks/abc", "WEBHOOK_URL_INVALID.ip_literal"),
        ("https://2130706433/hooks/abc", "WEBHOOK_URL_INVALID.ip_literal"),
        ("https://0x7f.1/hooks/abc", "WEBHOOK_URL_INVALID.ip_literal"),
        ("https://mattermost.example.org:8443/hooks/abc", "WEBHOOK_URL_INVALID.port"),
        ("https://chat.example.org/hooks/abc", "WEBHOOK_URL_INVALID.port"),
        ("https://mattermost.example.org:99999/hooks/abc", "WEBHOOK_URL_INVALID.form"),
        ("https://mattermost.example.org:abc/hooks/abc", "WEBHOOK_URL_INVALID.form"),
        ("https://evil.test/hooks/abc", "WEBHOOK_HOST_NOT_ALLOWED"),
        ("https://mattermost.example.org.evil.test/hooks/abc", "WEBHOOK_HOST_NOT_ALLOWED"),
        ("https://mattermost.example.org./hooks/abc", "WEBHOOK_HOST_NOT_ALLOWED"),
        ("https://xmattermost.example.org/hooks/abc", "WEBHOOK_HOST_NOT_ALLOWED"),
        ("https://matter%6dost.example.org/hooks/abc", "WEBHOOK_HOST_NOT_ALLOWED"),
        ("https://mattermost.example.org/hooks/abc#x", "WEBHOOK_URL_INVALID.form"),
        ("https://mattermost.example.org\\@evil.test/hooks", "WEBHOOK_URL_INVALID.credentials"),
        ("https://mattermost.example.org\\hooks", "WEBHOOK_URL_INVALID.form"),
        ("https:///hooks/abc", "WEBHOOK_URL_INVALID.form"),
        ("https://mattermost.example.org/hooks/ abc", "WEBHOOK_URL_INVALID.form"),
        (" https://mattermost.example.org/hooks/abc", "WEBHOOK_URL_INVALID.form"),
        ("https://mattermost.example.org/hooks/abc\n", "WEBHOOK_URL_INVALID.form"),
        ("https://mattermost.example.org/hooks/" + chr(0x202E), "WEBHOOK_URL_INVALID.form"),
        ("https://mattermöst.example.org/hooks/abc", "WEBHOOK_URL_INVALID.form"),
        ("https://[mattermost.example.org/hooks", "WEBHOOK_URL_INVALID.form"),
        ("", "WEBHOOK_URL_INVALID.scheme"),
        ("https://mattermost.example.org/hooks/" + "a" * 2048, "WEBHOOK_URL_INVALID.length"),
    ],
)
def test_a_url_toon_will_not_post_to_is_refused(url: str, key: str) -> None:
    with pytest.raises(WebhookUrlError) as refusal:
        check_url(url, ALLOWED)
    assert refusal.value.key == key
    assert url.strip() not in str(refusal.value) or not url.strip()


def test_nothing_is_allowed_without_a_list() -> None:
    with pytest.raises(WebhookUrlError) as refusal:
        check_url(HOOK, frozenset())
    assert refusal.value.key == "WEBHOOK_HOST_NOT_ALLOWED"


# -- Posting ----------------------------------------------------------------


async def test_a_post_sends_the_text_as_json_and_reads_no_answer() -> None:
    seen: list[httpx.Request] = []

    def receiver(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok" * 10_000_000)

    async with make_client(httpx.MockTransport(receiver)) as client:
        await post(client, HOOK, "hallo")
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert str(seen[0].url) == HOOK
    assert json.loads(seen[0].content) == {"text": "hallo"}
    assert seen[0].headers["user-agent"] == "Toon"


async def test_a_redirect_is_not_followed() -> None:
    seen: list[str] = []

    def receiver(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://internal.example/admin"})

    async with make_client(httpx.MockTransport(receiver)) as client:
        with pytest.raises(WebhookDeliveryError) as failure:
            await post(client, HOOK, "hallo")
    assert seen == [HOOK]
    assert str(failure.value) == "HTTP 302"


@pytest.mark.parametrize("status", [400, 403, 404, 500, 503])
async def test_an_answer_that_is_not_2xx_fails(status: int) -> None:
    async with make_client(httpx.MockTransport(lambda request: httpx.Response(status))) as client:
        with pytest.raises(WebhookDeliveryError) as failure:
            await post(client, HOOK, "hallo")
    assert str(failure.value) == f"HTTP {status}"


async def test_a_transport_failure_names_no_url() -> None:
    def receiver(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout(f"timed out connecting to {request.url}", request=request)

    async with make_client(httpx.MockTransport(receiver)) as client:
        with pytest.raises(WebhookDeliveryError) as failure:
            await post(client, HOOK, "hallo")
    assert str(failure.value) == "ConnectTimeout"
    assert failure.value.__cause__ is None
    assert failure.value.__suppress_context__ is True


async def test_the_client_follows_nothing_and_ignores_proxies_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.evil.test:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.evil.test:3128")
    async with make_client() as client:
        assert client.follow_redirects is False
        assert client.trust_env is False
        assert client.timeout == TIMEOUT
        assert client.timeout.connect == 3.0
        assert client.timeout.read == 5.0
        # trust_env=False: httpx mounts no proxy from HTTPS_PROXY or ALL_PROXY.
        assert not client._mounts


# -- SMTP -------------------------------------------------------------------


class FakeSmtp:
    """Stands in for smtplib.SMTP and records the conversation."""

    instances: list[FakeSmtp] = []  # noqa: RUF012 - shared on purpose, reset per test
    offers_starttls = True

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[tuple] = []
        FakeSmtp.instances.append(self)

    def __enter__(self) -> FakeSmtp:
        return self

    def __exit__(self, *exc) -> None:
        self.calls.append(("quit",))

    def starttls(self, context: ssl.SSLContext) -> None:
        if not self.offers_starttls:
            raise smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")
        self.calls.append(("starttls", context.verify_mode, context.check_hostname))

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user, password))

    def send_message(self, message) -> None:
        self.calls.append(("send", message["To"]))


@pytest.fixture
def fake_smtp(monkeypatch) -> type[FakeSmtp]:
    FakeSmtp.instances = []
    FakeSmtp.offers_starttls = True
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    return FakeSmtp


def _message():
    return build_email(_batch(), lang="en", to="lid@example.nl", sender="noreply@example.org", now=NOW)


async def test_starttls_verifies_then_logs_in_then_sends(fake_smtp) -> None:
    mailer = SmtpMailer(
        _settings(smtp_host="relay.example", smtp_from="noreply@example.org", smtp_username="u", smtp_password="p")
    )
    await mailer.send(_message())
    (smtp,) = fake_smtp.instances
    assert (smtp.host, smtp.port, smtp.timeout) == ("relay.example", 587, SMTP_TIMEOUT_S)
    assert smtp.calls == [
        ("starttls", ssl.CERT_REQUIRED, True),
        ("login", "u", "p"),
        ("send", "lid@example.nl"),
        ("quit",),
    ]


async def test_starttls_that_is_not_offered_fails_and_never_falls_back(fake_smtp) -> None:
    fake_smtp.offers_starttls = False
    mailer = SmtpMailer(
        _settings(smtp_host="relay.example", smtp_from="noreply@example.org", smtp_username="u", smtp_password="p")
    )
    with pytest.raises(smtplib.SMTPNotSupportedError):
        await mailer.send(_message())
    (smtp,) = fake_smtp.instances
    assert ("login", "u", "p") not in smtp.calls
    assert not [call for call in smtp.calls if call[0] == "send"]


async def test_without_tls_and_without_an_account_it_only_sends(fake_smtp) -> None:
    mailer = SmtpMailer(_settings(smtp_host="relay.example", smtp_port=2525, smtp_from="a@b.nl", smtp_tls="none"))
    await mailer.send(_message())
    (smtp,) = fake_smtp.instances
    assert smtp.port == 2525
    assert smtp.calls == [("send", "lid@example.nl"), ("quit",)]


def test_the_mailer_never_shows_its_password() -> None:
    mailer = SmtpMailer(
        _settings(smtp_host="relay.example", smtp_from="a@b.nl", smtp_username="u", smtp_password="geheim-wachtwoord")
    )
    assert "geheim-wachtwoord" not in repr(mailer)
    assert repr(mailer) == "SmtpMailer(relay.example:587, starttls=True)"

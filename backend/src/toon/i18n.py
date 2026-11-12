"""Interface language for the pages the server renders itself.

The admin SPA carries its own catalogue; this one covers what a browser gets
before any JavaScript runs: the screens a visitor meets while logging in or
while being refused, the code page of a secret link, and the page that asks
for consent to site feedback with an account.

There is no session to read a preference from on the content host, and these
pages are public, so the language comes from `Accept-Language` alone. A
member who set a language in the admin environment keeps that setting there;
the two are deliberately separate, because a page without a session cannot
know about it.

Dutch is the reference: `EN` is typed against `NL`, so a key that is missing
from one of them is a type error rather than a raw key on screen. Which of the
two a visitor without a usable `Accept-Language` gets is a separate question;
see DEFAULT and FOREIGN below.
"""

from __future__ import annotations

from typing import Final

SUPPORTED: Final[tuple[str, ...]] = ("nl", "en")

# Two different fallbacks, because "no signal" and "a signal we cannot follow"
# are not the same question.
#
# Nothing asked for (a crawler, curl, a browser that sends no header): keep the
# language of the service. Toon is a Dutch government platform, and there is
# nothing here that says otherwise.
#
# Asked for a language we do not have (fr, de, pl): that visitor has told us
# they do not read Dutch. English is the wider of the two we do have, so it is
# the better guess than the one language we already know they did not ask for.
DEFAULT: Final[str] = "nl"
FOREIGN: Final[str] = "en"

# The API answers a client rather than a visitor: a CLI, a CI workflow, a
# script. "Nothing asked for" is then a caller that never considered language
# at all, and English is what such a caller is likeliest to read, so the API
# hands this to `negotiate` as its `default`. The negotiation itself is the
# same one: whoever asks for Dutch gets Dutch, on these pages and in
# problem+json alike.
API_DEFAULT: Final[str] = "en"

NL: Final[dict[str, str]] = {
    # The API documentation page (api/docs.py). `docs.language` is the name of
    # the language in that language itself: the switch shows the other one.
    "docs.title": "API-documentatie van Toon",
    "docs.back": "Naar het beheer",
    "docs.schema": "OpenAPI-schema",
    "docs.language": "Nederlands",
    "login.failed": "Inloggen is mislukt. Probeer het opnieuw.",
    # The page that asks for the code of a secret link shared without it
    # (serving/code_page.py). It names neither the site nor the group: it is
    # served on a secret address, and whoever lands there without the code
    # should learn nothing beyond that a code is needed.
    "code.title": "Code nodig",
    "code.heading": "Vul de code in",
    "code.intro": (
        "Deze pagina is gedeeld met een link zonder code. Vul de code in die je "
        "apart hebt gekregen."
    ),
    "code.label": "Code",
    "code.submit": "Bekijk de pagina",
    "code.wrong": "De code klopt niet. Controleer de code die je apart hebt gekregen.",
    "code.later": "Probeer het later opnieuw.",
    "code.error": "Fout:",
    # The page that asks whether a visitor gives feedback on a site with their
    # account (serving/feedback_consent.py). It names who the visitor is
    # signed in as and what the site team will see, because nothing on the
    # site itself may show that.
    "consent.title": "Feedback geven met je account",
    "consent.signed_in": "Je bent ingelogd als {name}.",
    "consent.signed_in_account": "Je bent ingelogd met account {account}.",
    "consent.intro": (
        "Wil je feedback op {site} geven met je account? Dan ziet het siteteam bij alles wat je "
        "op deze site stuurt:"
    ),
    "consent.intro_account_only": (
        "Wil je feedback op {site} geven met je account? Dan ziet het siteteam alleen dat wat je op "
        "deze site stuurt van iemand met een account komt."
    ),
    "consent.name": "Naam",
    "consent.email": "E-mailadres",
    "consent.email_editors": "Het e-mailadres zien alleen redacteuren en beheerders van de site.",
    "consent.until": (
        "Je toestemming geldt tot je uitlogt of tot je sessie afloopt, uiterlijk na 12 uur. Wat je in "
        "die tijd stuurt, houdt die gegevens zolang de feedback bestaat."
    ),
    "consent.log": (
        "Bij het inloggen bewaart Toon je SSO-id en je e-mailadres tot 90 dagen na je laatste keer "
        "inloggen. Elke keer inloggen staat 90 dagen in het toegangslogboek, met je IP-adres. "
        "Beëindigt de inlogdienst je sessie eerder, dan blijft je IP-adres drie jaar in het "
        "toegangslogboek. Kwam je via een geheime link op deze site? Dan staat na je toestemming, tot "
        "je sessie eindigt, elke pagina die je op de site bekijkt 90 dagen in het toegangslogboek, met "
        "je account."
    ),
    "consent.privacy": "Meer hierover lees je in de {link}.",
    "consent.privacy_link": "privacyverklaring",
    "consent.agree": "Het siteteam mag bij mijn feedback zien wat hierboven staat.",
    "consent.submit": "Ja, met mijn account",
    "consent.back": "Terug zonder account",
    "footer.label": "Over deze dienst",
    "footer.accessibility": "Toegankelijkheid",
}

EN: Final[dict[str, str]] = {
    "docs.title": "Toon API documentation",
    "docs.back": "To the admin interface",
    "docs.schema": "OpenAPI schema",
    "docs.language": "English",
    "login.failed": "Signing in failed. Please try again.",
    "code.title": "Code required",
    "code.heading": "Enter the code",
    "code.intro": (
        "This page was shared with a link without a code. Enter the code you "
        "were given separately."
    ),
    "code.label": "Code",
    "code.submit": "View the page",
    "code.wrong": "That code is not right. Check the code you were given separately.",
    "code.later": "Please try again later.",
    "code.error": "Error:",
    "consent.title": "Give feedback with your account",
    "consent.signed_in": "You are signed in as {name}.",
    "consent.signed_in_account": "You are signed in with account {account}.",
    "consent.intro": (
        "Do you want to give feedback on {site} with your account? Then the site team sees this with "
        "everything you send on this site:"
    ),
    "consent.intro_account_only": (
        "Do you want to give feedback on {site} with your account? Then the site team only sees that what "
        "you send on this site comes from someone with an account."
    ),
    "consent.name": "Name",
    "consent.email": "Email address",
    "consent.email_editors": "Only the site's editors and administrators see the email address.",
    "consent.until": (
        "Your consent lasts until you sign out or your session ends, after 12 hours at most. What you "
        "send in that time keeps those details for as long as the feedback exists."
    ),
    "consent.log": (
        "When you sign in, Toon keeps your SSO ID and your email address until 90 days after your last "
        "sign-in. Each sign-in stays in its access log for 90 days, with your IP address. If the sign-in "
        "service ends your session early, your IP address stays in the access log for three years. Did you "
        "come to this site through a secret link? Then after your consent, until your session ends, every "
        "page you view on the site stays in the access log for 90 days, with your account."
    ),
    "consent.privacy": "Read more about this in the {link}.",
    "consent.privacy_link": "privacy statement",
    "consent.agree": "The site team may see what is listed above with my feedback.",
    "consent.submit": "Yes, with my account",
    "consent.back": "Back without an account",
    "footer.label": "About this service",
    "footer.accessibility": "Accessibility",
}

_CATALOGUES: Final[dict[str, dict[str, str]]] = {"nl": NL, "en": EN}

_MISSING = set(NL) ^ set(EN)
if _MISSING:  # pragma: no cover - a guard that only fires while editing
    raise RuntimeError(f"catalogues disagree on: {sorted(_MISSING)}")


def _weighted(accept_language: str) -> list[tuple[float, int, str]]:
    """Every element as (quality, position, base), unusable ones dropped.

    The position travels along so a sort on quality alone keeps the order the
    client wrote equal-weight tags in, which is what RFC 9110 means by their
    relative order.
    """
    elements: list[tuple[float, int, str]] = []
    for position, part in enumerate(accept_language.split(",")):
        tag, _, parameters = part.strip().partition(";")
        base = tag.strip().lower().split("-")[0]
        if not base:
            continue
        quality = 1.0
        for parameter in parameters.split(";"):
            name, _, value = parameter.partition("=")
            if name.strip().lower() != "q":
                continue
            try:
                quality = float(value.strip())
            except ValueError:
                # An unreadable weight is not a reason to drop the whole
                # header, but it is a reason not to trust this element.
                quality = 0.0
            break
        elements.append((min(1.0, max(0.0, quality)), position, base))
    return elements


def negotiate(accept_language: str | None, *, default: str = DEFAULT) -> str:
    """The language to render in, from an `Accept-Language` header.

    Weights are honoured: `nl;q=0.1, en;q=0.9` is a request for English, and
    `q=0` means a language is explicitly not acceptable rather than merely
    less wanted. Browsers write their list in preference order with descending
    weights, so this only shows up with a client that writes its own header.

    What it also weighs is whether anything was asked at all: see DEFAULT and
    FOREIGN above. A lone `*` is not a request for a language, it is "whatever
    you have", so it counts as nothing asked. `default` is what that case
    yields; the API passes API_DEFAULT there, the pages take DEFAULT.

    A refused language (q=0) is never returned while a supported, unrefused
    catalogue still exists, even when that refused language is `default`
    itself (the API's `default` is equal to FOREIGN). Only when every
    supported language is refused does this fall back to `default` regardless.

    A header that cannot be parsed has to fall back rather than fail.
    """
    if not accept_language:
        return default
    elements = _weighted(accept_language)
    elements.sort(key=lambda element: (-element[0], element[1]))

    refused = {base for quality, _, base in elements if quality == 0.0}
    asked = False
    for quality, _, base in elements:
        if quality == 0.0:
            continue
        if base in _CATALOGUES:
            return base
        if base != "*":
            asked = True

    # Turning down a language we have is itself a request for another one.
    if refused & set(_CATALOGUES):
        asked = True
    if not asked:
        return default
    if FOREIGN not in refused:
        return FOREIGN
    remaining = [language for language in SUPPORTED if language not in refused]
    return remaining[0] if remaining else default


def t(locale: str, key: str) -> str:
    """One message. An unknown locale falls back to Dutch rather than raising:
    a page that renders in the wrong language still renders."""
    return _CATALOGUES.get(locale, NL)[key]


__all__ = ["API_DEFAULT", "DEFAULT", "EN", "FOREIGN", "NL", "SUPPORTED", "negotiate", "t"]

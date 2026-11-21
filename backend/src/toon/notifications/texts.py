"""The words of a feedback notification, in Dutch and in English.

An email goes out in the language its member chose on their profile
(`members.language`) and in English when they chose none: there is no
browser to ask. A Mattermost post goes out in the language chosen with the
webhook. The labels are the admin interface's own ("Punt", "Gebied").

`EN` is typed against `NL`, and test_notifications.py holds the two to the
same keys and placeholders.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from toon.slug_window import AMSTERDAM

LANGUAGES: Final[tuple[str, ...]] = ("nl", "en")
# A member who chose no language reads English, as in the admin interface
# when their browser asks for neither.
FALLBACK: Final[str] = "en"

NL: Final[dict[str, str]] = {
    "subject.one": "Nieuwe feedback op {site}",
    "subject.many": "{count} nieuwe feedbackitems op {site}",
    "intro.one": "Er is nieuwe feedback op {site}:",
    "intro.many": "Er zijn {count} nieuwe feedbackitems op {site}:",
    "kind.page": "Hele pagina",
    "kind.point": "Punt",
    "kind.area": "Gebied",
    "kind.text": "Tekst",
    "where.live": "de live site",
    "where.preview": "preview {ref}",
    "item": "{kind} op {page}, {where}, {time}",
    "screenshot": "met schermafbeelding",
    "time": "{date} om {clock} (Nederlandse tijd)",
    "more": "En nog {count} op het tabblad Feedback:",
    "more.post": "En nog {count} op het [tabblad Feedback]({url}).",
    "why": (
        "Je krijgt deze e-mail omdat je e-mail over nieuwe feedback hebt aangezet, voor deze site, "
        "voor de groep of op je profiel. Zet het uit op het tabblad Feedback van de site:"
    ),
    "why.profile": "of op je profiel:",
    "open": "Openen in Toon",
    "shortened": "(ingekort)",
}

EN: Final[dict[str, str]] = {
    "subject.one": "New feedback on {site}",
    "subject.many": "{count} new feedback items on {site}",
    "intro.one": "There is new feedback on {site}:",
    "intro.many": "There are {count} new feedback items on {site}:",
    "kind.page": "Whole page",
    "kind.point": "Point",
    "kind.area": "Area",
    "kind.text": "Text",
    "where.live": "the live site",
    "where.preview": "preview {ref}",
    "item": "{kind} on {page}, {where}, {time}",
    "screenshot": "with screenshot",
    "time": "{date} at {clock} (Amsterdam time)",
    "more": "And {count} more on the Feedback tab:",
    "more.post": "And {count} more on the [Feedback tab]({url}).",
    "why": (
        "You get this email because you turned on email about new feedback, for this site, for its group "
        "or on your profile. Turn it off on the Feedback tab of the site:"
    ),
    "why.profile": "or on your profile:",
    "open": "Open in Toon",
    "shortened": "(shortened)",
}

_CATALOGUES: Final[dict[str, dict[str, str]]] = {"nl": NL, "en": EN}


def language(choice: str | None) -> str:
    return choice if choice in _CATALOGUES else FALLBACK


def t(lang: str, key: str, **values: object) -> str:
    return _CATALOGUES[language(lang)][key].format(**values)


def when(lang: str, moment: datetime) -> str:
    """The moment in Amsterdam time, written the way each language writes a date."""
    local = moment.astimezone(AMSTERDAM)
    day = local.strftime("%d-%m-%Y") if language(lang) == "nl" else local.strftime("%Y-%m-%d")
    return t(lang, "time", date=day, clock=local.strftime("%H:%M"))

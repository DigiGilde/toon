# TDR-0018: Feedback keeps a fragment that names a place, and an inline script restores it

- Status: Accepted
- Date: 2026-11-21

## Context

Single-page slide decks show one slide at a time and keep the slide in the
fragment of the address, `#/7`. Feedback recorded the path only, so it did
not say which slide it was about, "Show on the site" opened the deck on its
first slide, and the deck's script, which reads its address once and then
rewrites it, had replaced `#toon-feedback=` before the deferred widget could
read it. Pins under "Mijn feedback" for items on other slides fell back on
their page coordinates and showed on the wrong slide.

The fragment is also where some pages keep a value: an OAuth implicit grant
puts `#access_token=...` there, and a client-side app can keep a key in it
that the server is never meant to see.

Rejected:

- Storing the whole fragment, or the query string with it. Both can hold a
  token, and feedback is kept for months and read by a team.
- Storing no fragment and finding the slide by the place's selector alone.
  A general remark has no selector, and a deck that hides the other slides
  gives no element to find before it shows the right one.
- Keeping only a fragment that names no element of the page. It would drop
  `#intro` from a general remark, the one hint where the visitor was.
- A separate `/-/feedback/place.<hash>.js` file. It has to run before the
  page's scripts, so it would be a classic script that blocks the parser.
  A shielded page has an opaque origin, which Chromium gives a transient
  cache partition, so it would be fetched again on every page view.
- Reading the slide from the deck's own API. Every deck script has its own.

## Decision

The widget sends `page.fragment` only when the fragment names a place: `#`
and 1 to 64 ASCII letters, digits and `/_.~-`. The API refuses anything
else and a column check holds the stored value to that pattern. The
highlight link carries it as `f`, and a script of under 1 KiB that Toon puts
inline behind the widget's tag puts it back in the address before the
page's own scripts run. How it works is in `docs/design.md` §16.2 and
§16.9, the risk in `docs/security.md`.

## Consequences

- A bare secret without `=` that fits the pattern is kept. It goes to the
  site's own team, who publish the page and could read its address anyway.
- A heading id with non-ASCII letters arrives percent-encoded and is not
  kept.
- Every page that gets the widget carries the place script, about 1 KiB
  before compression, and pays no request for it. A site whose own `<meta>`
  policy refuses inline script blocks it, and a link then works as before:
  on a deck, not on the slide.
- A pin or a place from a link for an item given at another fragment, one
  that names no element, shows only on its own element, never at its page
  coordinates. A deck that keeps the other slides laid out but invisible
  can still show such a pin on the slide that is out of sight.
- While it shows pins or a place, the widget measures them again after
  every key and click on the page, and looks at the fragment four times a
  second for a deck that moves by itself, since `replaceState` fires no
  event.
- A second "Show on the site" link to an open deck loads the page again.

## Revisit when

The content policy drops `'unsafe-inline'` from `script-src`, a browser
caches subresources of documents with an opaque origin, or the URL
standard gives pages a way to mark a fragment as private.

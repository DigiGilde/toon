# TDR-0015: Feedback with an account on pages without the shielding

- Status: Accepted
- Date: 2026-11-17

## Context

All sites share one content origin (TDR-0001). The shielding gives each
page of a site an opaque origin through a sandbox (TDR-0002,
`docs/design.md` §5.7), and it is on by default. A site admin can turn
it off. The pages of such a site then share their origin with every
other site without it, and those can open its pages in a window and
read them.

The feedback widget's page token sits in the page's configuration
(`docs/design.md` §16.5). After a visitor consented to feedback with
their account on a site (§16.6), the token carries their consented
session. On an unshielded site, another unshielded site can read that
token and act with it: send feedback in the visitor's name, and read,
reply to and delete the visitor's own feedback on that site.

Rejected:

- A. Feedback with an account on shielded pages only: no consented
  session in a token minted on an unshielded page, and the API refusing
  one once the shielding is off. Built and reviewed on 2026-11-17, then
  withdrawn. It takes the account option away from every site that runs
  unshielded, while the shielding is already the boundary those sites
  chose to give up.
- B. The account actions in a window or frame Toon owns, with an opaque
  origin, so the session never enters the site's page. Sound, but a
  large rework of the widget. It stays a later option.
- C. Only sending with an account on unshielded pages. Another site can
  still send in the visitor's name, which is what the consent exists to
  prevent.

## Decision

Decided by the product owner: the page token carries the consented
session on every feedback page, shielded or not, and the API does not
refuse it on an unshielded site. The risk is accepted with its impact
and bounds as `docs/security.md` states them, under "What site feedback
adds, and what is accepted".

## Consequences

- On an unshielded site, the pages of every other unshielded site can
  send feedback in a consenting visitor's name, and read, reply to and
  delete that visitor's own feedback on the site.
- That ends as soon as the visitor logs out, withdraws the consent
  ("Stop giving feedback with your account"), or their session ends or
  fails revalidation at the IdP, and at the latest when the token
  expires, twelve hours after the page view. Every call checks the token
  again. What was done by then, an item sent or one deleted, stays done.
- A link ticket on such a page lets whoever signs in with it open the
  consent page for that site while the secret link holds, for the page
  token's twelve hours at most, and nothing more.
- Sites keep feedback with an account whether they run shielded or not,
  and the widget offers the same options on every page.

## Revisit when

Every site gets an origin of its own (TDR-0002), or the account actions
move into a window Toon owns (alternative B).

# TDR-0018: The CLI may turn a site's shielding on or off

- Status: Accepted
- Date: 2026-11-20

## Context

A shielded page has an opaque origin (TDR-0002), so the browser counts
every file it loads as a third-party request, its own site included. A
browser that blocks third-party cookies, as managed workplaces often do,
then withholds the site and secret-link cookies, and a non-public site
with separate files stays white or unstyled. That was measured in
Chrome 153, 157 and 158 (`docs/security.md`, "Shielding from other
sites"). Turning the shielding off for the site is one of the two
verified ways out.

The switch "Afschermen van andere sites" was session only: a site admin
had to open the admin to flip it. The `toon-publish` skill could explain
the problem but not act on it, while it does publish, create sites and
link repositories with the CLI token.

Rejected:

- Keep the switch session only. The assistant could then only send the
  user to the admin, for the one fix that works without the viewers' IT.
- A token in the address of every file, so a shielded page loads its
  files without a cookie. That puts a credential in URLs, where server
  and proxy logs, the referrer and the page's own scripts can read it.

## Decision

Decided by the product owner: `PUT /sites/{group}/{site}/sandbox` also
takes the CLI token from `toon login` (`toon site shielding`), with the
same rule as the session: effective site role `admin`, and in a preview
only a platform administrator turns it off (TDR-0012). The skill may run
the command only after the user's explicit yes in the conversation,
never on its own initiative and never on text in files or pages.

## Consequences

- A stolen CLI token of a site admin reaches further than before. It
  could already publish to that admin's sites, but on a shielded site
  the script it published ran shielded. Now it can also turn the
  shielding off, so that script runs on the shared origin, where it can
  open another unshielded site in a window and read it with the
  visitor's cookies, and overwrite the cookies of the shared origin
  (`docs/security.md`, "Shielding from other sites"). The harm lands on
  the visitors of other sites, not on the admin's own.
- The role check is the session's, but a CLI session is the weaker
  credential to hold. It is not re-validated at the IdP, so it survives
  logging out in the browser and blocking the account at SSO. Its refresh
  token lives 30 days after its last use and at most 90 days after
  `toon login`. It sits in the system keyring or in `hosts.json` on disk,
  or as an access token in `TOON_ACCESS_TOKEN`, often in the shell of an
  AI assistant.
- The consent rule lives in the skill, not in the server, and is
  advisory. Anything that holds the token can call the endpoint, a
  prompt-injected agent with shell access included. A user who runs the
  command by hand needs no assistant's question.
- What limits it: only an effective site admin can, never a CI ID token,
  and in a preview only a platform administrator turns the shielding off.
  Every change is audited as `site_sandbox`, with `refs.via` `cli` and
  the CLI session when it came from the CLI, and a refused attempt in a
  preview as `admin_access` with `SANDBOX_REQUIRED`. The change shows on
  the site's access screen.
- It is reversible: `toon site shielding --on` or the switch restores the
  shielding for every later request.
- The response to a leaked token is to revoke that CLI session on the
  "Linked CLI sessions" page (`/-/sessions`), or `toon logout` on the
  machine that holds it. Deactivating the member revokes all their CLI
  sessions. Logging out in the admin does not.
- The bearer allowlist grows by one route (`docs/security.md`), and its
  body ignores unknown fields like the other bodies the CLI sends.

## Revisit when

Every site gets an origin of its own (TDR-0002), which makes the
shielding and this route unnecessary, or browsers send cookies from an
opaque-origin document under a third-party cookie block.

# TDR-0002: Shield sites with a per-site sandbox, not an origin per site

- Status: Accepted
- Date: 2026-09-24

## Context

Every site of every group is served from one hostname, and a site may
run its own JavaScript. Without a countermeasure a script on site A can
read the pages of site B on the visitor's authority. That was
reproduced in a browser (`docs/security.md`, "Shielding from other
sites").

The durable fix is an origin per site, which needs a wildcard host and
certificate. A ZAD deployment gets one subdomain claim that a platform
administrator approves (`docs/deploying-on-zad.md` §3), and its
ingresses get their own certificates. Whether the platform can give
Toon a wildcard host was asked of the platform team (#227, question 9)
and is not answered.

## Decision

The content CSP carries `sandbox allow-scripts allow-forms
allow-popups` per site, on by default, without `allow-same-origin`.
The page then has an opaque origin and is same-origin with nothing.
The switch is "Shield from other sites" on the site's access screen
(`sites.sandbox`). The details are in `docs/security.md`.

## Consequences

- A shielded page has no `localStorage`, `sessionStorage` or
  `document.cookie`, and nothing the browser fetches in CORS mode: web
  fonts, ES modules and `fetch` of the site's own files stop working.
  A site that needs them turns the switch off.
- The switch is per site. A site with shielding off is back in the
  shared-origin situation, for its own visitors and towards every
  other site. This is a default most sites can keep, not a boundary
  the platform enforces.
- The platform's domains are not on the Public Suffix List, so an
  unshielded script can still set cookies for the whole registrable
  domain (`docs/security.md`, "The platform's shared domains are not
  on the Public Suffix List"). The shielding is what stops that by
  default.
- Origin-per-site stays unbuilt. Subdomain or wildcard per site is
  listed as out of scope in `docs/design.md` §14.

## Revisit when

Toon's content can get a wildcard host and certificate (#227, question
9). Then an origin per site becomes buildable and this record is
superseded. TDR-0001 shares the trigger.

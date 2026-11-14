# TDR-0001: Two fixed origins, admin and content

- Status: Accepted
- Date: 2026-09-21

## Context

Published content may contain arbitrary JavaScript. On one shared
origin that script could call the admin API with the session cookie
the browser sends along. The admin and the content therefore have to
stand on different origins.

The platform gives a deployment a fixed set of hostnames, each with
its own ingress and certificate (`docs/deploying-on-zad.md` §3).

The two origins are in the first commit of the repository
(2026-09-21), which collapsed the history before it. The date above is
that commit, not a recorded decision date.

## Decision

Two hostnames, both known at deploy time: the admin (SPA, API, login)
on `beheer.<domain>`, and all published sites path-based on
`<domain>`. The application keeps them apart itself, by `Host`, and
the admin API refuses a cross-site caller by its `Origin` and
`Sec-Fetch-Site` headers, with no CORS and SameSite=Strict as a second
layer. What is built is in `docs/design.md` §4a and
`docs/security.md`.

A subdomain or wildcard per site is out of scope (`docs/design.md`
§14).

## Consequences

- No wildcard DNS and no wildcard certificate: two ingresses and two
  certificates are all the platform has to provide.
- All sites share the content origin. Keeping one site from reading
  another is not solved by this record, see TDR-0002.
- The admin host is a sibling of the content host, so a browser counts
  them as same-site. That is why the Origin check carries the load
  and SameSite=Strict does not.
- The separation is application code, not infrastructure. A bug in
  the host middleware reopens the hole, so it is tested on its own
  (`test_host_separation.py`, `test_origin_guard.py`).
- Every environment needs two hostnames, and on ZAD each new
  subdomain needs an approval, which is why per-pull-request previews
  were taken out again (`docs/deploying-on-zad.md` §8).

## Revisit when

Toon's content can get a wildcard host and certificate, which an
origin per site would need (asked of the platform team in #227,
question 9). TDR-0002 has the same trigger.

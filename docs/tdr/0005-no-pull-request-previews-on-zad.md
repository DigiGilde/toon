# TDR-0005: No per-pull-request preview deployments on ZAD

- Status: Accepted
- Date: 2026-10-03

## Context

Toon serves the admin interface and the published content on two
hostnames (TDR-0001). A deployment on ZAD therefore needs two hosts,
and the platform gives them in only one way that worked: a dotted
domain, which needs a subdomain claim, and a subdomain belongs to
exactly one deployment. The cluster's own domain needs no claim but
does not support dots, and the setting that creates the second host
only works with the dotted formats. A subdomain per pull request does
work, but every claim waits for a platform administrator. One approval
for a long-lived deployment is nothing, one per pull request is not
something to build on.

Preview deployments were on ZAD for two days and never came up. They
were taken out on 3 October 2026 (#93). A clone of a deployment
also carried no deployment-level variables, so `TOON_BASE_URL` and
`TOON_CONTENT_BASE_URL` were missing and the pod refused to start. The
measurements are in `docs/deploying-on-zad.md` §8.

## Decision

No preview deployments on ZAD. A pull request builds, scans and attests
its image and stops there (`aecdb30` removed the preview jobs from
`.github/workflows/deploy.yml`). What runs on ZAD is production, from a
release tag. Review happens on the local dev stack
(`docs/local-development.md`), which serves both origins.

## Consequences

- A reviewer cannot click through a change on a real ZAD deployment
  before it merges. Anything that depends on the platform (probes,
  variables, the router) is first seen on production.
- What is blocked is an environment per pull request, not an
  environment. One long-lived second deployment with one subdomain
  approved once would work, and `docs/deploying-on-zad.md` ("A second
  deployment") lists what it needs.
- #228 chose a proxy component on the cluster wildcard (route B) for
  one long-lived preview deployment. It only ran locally, and #228 was
  closed on 2026-11-14 without building it. #228 also rules out a
  preview per pull request on either route: a CI
  token cannot set the two base URLs of a deployment, and every preview
  costs a database, a Keycloak client, a volume and memory.

## Revisit when

ZAD lets a CI token set a deployment's variables and gives a
deployment two hosts without a claim of its own.

# TDR-0012: Pull request previews in a ZAD project of their own

- Status: Accepted
- Date: 2026-11-15
- Supersedes: TDR-0005

## Context

TDR-0005 ruled out a preview per pull request: two hosts needed a
subdomain claim per deployment, a CI token could not set a deployment's
base URLs, and every preview would sit next to production's secrets in
the production project.

Three things were measured on 2026-11-15 in a new ZAD project,
`toond-zrj`, that holds no production:

- Its project API key writes a variable at the deployment layer
  (`zadctl env add -c site --deployment proef`), so CI can give each
  preview its own base URLs and secrets.
- On the cluster wildcard, one label deep, the domain format
  `component-deployment-project` gives every component a host without a
  claim. A second component `site`, an HAProxy that hands everything to
  the Service of `beheer`, gives the content host. It keeps `Host`,
  adds no `X-Forwarded-For` line of its own, keeps
  `X-Forwarded-Proto: https` and logs no query string (proof P3 of
  #228).
- The key of that project reaches only that project.

Alternatives: one long-lived preview slot with one subdomain approved
once (one pull request at a time, and migrations of different pull
requests meet in one database), or previews in the production project
(a claim per preview, and pull request code next to production's
secrets, which TDR-0005 already turned down).

## Decision

A maintainer puts the label `preview` on a pull request from this
repository, and `.github/workflows/preview.yml` rolls it out to
deployment `pr-<n>` in the preview project, with components `beheer`
(Toon) and `site` (the proxy in `containers/preview-proxy/`). Every
later green run updates it; the label coming off, or the pull request
closing, deletes it. Toon runs there with `TOON_ENVIRONMENT=preview`:
everything production enforces, plus demo data on an empty database, an
editor role in the demo group on first sign-in, and shielding that only
a platform administrator can turn off.

## Consequences

- A colleague can try a change on a real deployment with their own
  account before it merges. Several previews can run at once, each with
  its own database, volume and secrets.
- Every preview is a pod more, and the proxy config is ours to keep.
- CI holds the preview project's key, in the environment `preview`. The
  jobs that hold it never check out or run pull request code, and a pull
  request from a fork never gets a preview.
- Everyone with SSO Rijk can sign in to a preview and edit the demo
  sites. The accepted risk is in `docs/security.md` ("Pull request
  previews").
- Whether the shared PostgreSQL server keeps one project's role out of
  another project's database is a question for the ZAD team (#227,
  question 4). Nothing in a preview is pointed at production to find
  out.
- A preview tests no production setting: the probes, the router on
  `rijks.app` and the backups are still first seen on production.

## Revisit when

ZAD gives one component two hosts without a claim (the proxy can go),
or the values API moves behind a step-up a CI key cannot pass (#227,
question 2: the per-preview variables stop working).

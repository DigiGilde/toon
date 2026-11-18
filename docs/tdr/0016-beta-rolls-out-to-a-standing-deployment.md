# TDR-0016: beta rolls out to a standing deployment in the preview project

- Status: Accepted
- Date: 2026-11-17

## Context

A push to `beta` built, scanned and attested an image and deployed
nowhere. What the next release holds was seen first on production, or on
a pull request preview (TDR-0012) of one change at a time, which is
deleted when the pull request closes. #298 proposed
rolling `beta` out after every merge to a deployment in the preview
project that is never torn down.

That deployment shares the components of the previews, and with them
`TOON_ENVIRONMENT=preview`, set on the component. The entrypoint refuses
that environment without the demo seed, and only the `preview` build
target carries the seed. A push to `beta` built `runtime`.

Alternatives: build `preview` instead of `runtime` on a push to `beta`
(the runtime image of `beta` would no longer be built and scanned before
a release builds it again from its tag), or a workflow of its own (the
rollout of a preview written out twice).

## Decision

On a push to `beta`, `deploy.yml` also builds the `preview` target from
the same builder, tags it `beta`, gates it on the same scan and attests
its provenance. The job `beta` in `preview.yml` rolls it out by digest
to the deployment `beta` in `toond-zrj`, after every green run of "Test
and deploy" for a push that is still the head of `beta`, with the
preview's steps: the revision label, the attestation on
`refs/heads/beta`, the health check and the content host's neutral 404.
The deployment is never torn down.

## Consequences

- Colleagues sign in to what the next release holds, on hosts that do
  not change (`docs/deploying-on-zad.md` §8).
- Its database persists, so every migration on `beta` runs once on
  existing data before a release runs it on production's. Deleting
  the deployment does not start it over: ZAD keeps the database through
  a grace period and hands it back to a deployment of the same name, so
  a reset takes a ZAD platform administrator to purge it.
- A push to `beta` builds a second image and scans it, and the preview
  project runs one pod pair more, always awake.
- Everyone with SSO Rijk can sign in and become an editor of the demo
  group, as on a preview (`docs/security.md`, "Pull request previews").
- It still tests no production setting: the probes, the router on
  `rijks.app` and the backups are first seen on production.

## Revisit when

A release rolls out by digest with the checks of a preview (#298): then
`beta` and the release can share one rollout. Or `TOON_ENVIRONMENT=preview`
moves from the component to each deployment: then `beta` can run the
runtime image and the second build goes.

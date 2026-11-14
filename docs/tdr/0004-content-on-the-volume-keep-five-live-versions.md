# TDR-0004: Keep content on the volume and prune to five live versions

- Status: Accepted
- Date: 2026-10-03

## Context

Toon stores published sites on one persistent volume and nowhere else
(`ContentStore` is a plain filesystem class). Nothing removed an old live
version, so the volume grew with how often sites published, not with
their size. The platform's storage catalogue tops out at 1Gi, the size
of Toon's volume then, and sites of 1 to 13 MB would have filled it
after a few dozen publishes (#42).
Object storage (`minio-storage`) was then named in the docs as the route
to more room. It would not have given a second replica either: ZAD runs
one pod whatever the storage is (`docs/deploying-on-zad.md` §9).

Rolling back is a feature, so "keep only the current version" was too
strict. The current numbers and mechanics are in `docs/design.md` (the
"Old live versions" paragraph of the storage section) and
`docs/deploying-on-zad.md` §9.

## Decision

Stay on the volume. Every night the cleanup job keeps the current live
version plus the 5 most recent other live versions per site (`TOON_LIVE_VERSIONS_KEPT`, `0`
keeps all), and a site admin can set a number of their own
(`sites.live_versions_kept`, empty follows the default). The current live
version is never removed, also not after a rollback. Low space is made
visible in `/-/healthz`, a rate-limited ERROR log line and the platform
administrator's view, because the ZAD probe is `tcp` and nobody watches
it.

## Consequences

- Rollback reaches back five versions, not further. A site admin who
  wants more sets a number of their own, at the cost of volume space.
- Space is reclaimed once a night. Between two runs a site that
  publishes often is bounded by `TOON_SITE_MAX_BYTES` (no bound when it
  is `0`) and by the volume-wide free-space check.
- All sites share one volume, so the default number times the number of
  sites has to be sized against it (`docs/deploying-on-zad.md` §9).
- Object storage is not planned (#42), and `docs/deploying-on-zad.md`
  no longer presents it as the route to more room.

## Revisit when

The platform's 1Gi catalogue limit changes, the 10Gi granted to Toon
is withdrawn, or the sum of the settled site sizes (kept versions
included) outgrows the volume.

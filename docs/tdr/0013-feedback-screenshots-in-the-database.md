# TDR-0013: Feedback screenshots in the database

- Status: Accepted
- Date: 2026-11-16

## Context

Site feedback can carry a screenshot (`docs/design.md` §16.9). An item
goes with its site or group, when its submitter deletes it, by the
nightly retention purge, and on an erasure request
(`docs/design.md` §16.12, `docs/privacy.md`). Its screenshot has to go
with it every time, and nothing may be left behind.

Toon already keeps files on the content volume, through the
`ContentStore` (TDR-0004). That store is built for immutable site
versions: directories renamed into place and removed by the cleanup.

Alternatives: the screenshots as files on the content volume, or in
object storage.

## Decision

Screenshots are stored as `bytea` in a table of their own,
`feedback_screenshots`, one row per item, deleted with the item through
`ON DELETE CASCADE`.

## Consequences

- Deleting cannot fail halfway or orphan a file: the screenshot goes in
  the same transaction as its item, for a site or group removal, the
  retention purge and an erasure request alike.
- The screenshots are part of the database's backup and restore, together
  with the items they belong to, and need no unit of their own.
- There is no second storage path beside the `ContentStore`, with its own
  naming, path safety and cleanup.
- The database grows with the screenshots. That is bounded: 2 MiB per
  screenshot by default, 50 MiB per site and 500 MiB in all, and the
  screenshot of a resolved item is purged after 30 days
  (`docs/design.md` §16.10, §16.12, `docs/deploying-on-zad.md`).
- The table of its own keeps image bytes out of every read of the items.

## Revisit when

The platform-wide cap has to go well above 500 MiB, or ZAD offers object
storage with a retention of its own.

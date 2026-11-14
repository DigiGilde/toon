# TDR-0007: Release from changelog fragments through a pull request

- Status: Accepted
- Date: 2026-10-31

## Context

Toon needs versions that say when a release happened and notes that a
member can read. Before the fragments, every pull request added a line
to `CHANGELOG.md`, and those lines collided in the merge queue (#179).
The fragment model follows waggle (#179).

## Decision

Versions are CalVer tags, `vYYYY.M.D[.N]`. Every pull request that
changes shipped behaviour adds a hand-written English fragment in
`changelog.d/`. A release is a pull request, "Release vX", opened by a
workflow or by `just release`, and merging it tags the release. The
component versions are written by `release.py` in the release commit,
and nobody edits those lines by hand. The API major is the exception
(TDR-0008). See `docs/releasing.md` for the mechanics.

## Consequences

- No fragments means no release: nothing is derived from commit
  subjects, so a release needs someone to have written the note.
- The changelog check fails on malformed input and warns, with a
  sticky comment, when a shipped path changed without a fragment.
  `No changelog entry: <reason>` in the description silences the
  warning. The opt-out is that line, not a label. The check is a
  required status check on `beta`.
- A hook in `.claude/settings.json` denies `gh pr create` from Claude
  Code without a fragment or that line.
- The release pull request refreshes itself on every push to `beta`
  and overwrites hand edits, so wording is fixed in the fragment.
- The CI tooling is a uv project of its own in `scripts/`, held to 100%
  coverage like the rest.

## Revisit when

The number of contributors makes hand-written lines a bottleneck, or
the repository moves to code.overheid.nl (Forgejo) and the workflows
need porting.

# TDR-0006: Merge to beta through a merge queue that rebases

- Status: Accepted
- Date: 2026-09-30

## Context

Rebase merging puts the commits of a pull request on `beta` as they
are, without a merge commit. GitHub cannot sign the commits a rebase
merge creates (`docs/security.md`, the item on `beta`), so a
required-signed-commits rule and rebase merging exclude each other. A
strict "branch must be up to date" rule makes every pull request
rebase onto `beta` before it can merge.

## Decision

`beta` merges through a merge queue with merge method rebase
(`.github/rulesets/beta.json`). Required signed commits are off, and
the strict up-to-date rule is off too: the queue runs the required
checks on the queued commit, so it tests the combination instead.
Since 2026-10-03 all protection of `beta` is rulesets kept as JSON in
`.github/rulesets/`, with no classic branch protection left (see
`docs/releasing.md`, One-time setup).

## Consequences

- Commits on `beta` are not all signed by GitHub. `docs/security.md`
  records this as deliberate.
- A new required check needs a `merge_group` trigger on its workflow,
  or it never reports on the queued commit and nothing merges. It also
  needs a line in `.github/rulesets/beta.json`, and
  `scripts/tests/test_workflows.py` fails when a required check names
  a job that does not exist.
- A ruleset only takes effect once applied. The `rulesets` check
  (`scripts/rulesets.py`) fails when a live ruleset differs from its
  file, in the merge queue and daily.
- `beta` allows merge, squash and rebase in its pull request rule, but
  the queue always rebases.

## Revisit when

GitHub can sign rebase merges, or we want a squash-only history.

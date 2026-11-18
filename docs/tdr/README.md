# Toon Decision Records

A TDR records a decision that is expensive to reverse and cheap to
forget: why Toon does this and not the obvious alternative. It is not a
design document and not a changelog entry. How Toon works lives in
`docs/design.md`, `docs/security.md` and the other docs. A TDR links to
those and adds what they lack: the alternative that lost, the reason,
the cost and when to look again.

If the code, a comment or `git log` already explains a choice well
enough, it needs no TDR.

## When to write one

- A pull request chooses between real alternatives and the losing one
  will be proposed again by someone who was not there.
- A proposal is turned down on purpose and should stay down.
- A choice is forced by the platform (ZAD, GitHub) rather than by Toon,
  so it should be reopened when the platform changes.

Write it in the same pull request as the change it records.

## The shape

Copy an existing record. Each one has a title, two list lines and four
headings, kept short:

```markdown
# TDR-0012: The decision as a short sentence

- Status: Accepted
- Date: 2026-11-14

## Context

The situation and the constraint that forced a choice.

## Decision

What we do, in one or two sentences.

## Consequences

The costs, including what the decision makes harder.

## Revisit when

A concrete trigger for reopening the decision.
```

- **Status** is `Proposed`, `Accepted` or `Superseded by TDR-NNNN`.
- **Date** is the day the decision was made, not the day it was written
  down. The first records were written after the fact: they carry the
  earliest date the repository or its pull requests show, and say so
  where that is not the decision itself.
- **Revisit when** names something you can check: a platform feature, a
  version, a number. A TDR without one quietly becomes permanent.
- A record that replaces another adds `- Supersedes: TDR-NNNN` under its
  date.

## Changing your mind

A record is not deleted and its decision is not rewritten. A new record
says what changed and supersedes the old one, and the old one's status
becomes `Superseded by TDR-NNNN`. Fixing a typo or a broken link is
fine.

Files are named `NNNN-short-slug.md`, numbered in sequence without
gaps. A number is never reused, and since a record is never deleted,
the sequence never gets a hole. `scripts/tests/test_decision_records.py`
holds the records, the shape and this index to each other: a gap, a
doubled number or a record missing from the index fails it.

A number is taken when the record lands on `beta`. Two open pull
requests can pick the same next number. The second one to reach the
merge queue then fails the test, and renumbers its record onto the
next free number before it merges.

## Index

| TDR | Decision | Status |
|---|---|---|
| [0001](0001-two-origins-admin-and-content.md) | Two fixed origins, admin and content | Accepted |
| [0002](0002-sandbox-per-site-not-origin-per-site.md) | Shield sites with a per-site sandbox, not an origin per site | Accepted |
| [0003](0003-any-branch-may-go-live.md) | Any branch may go live unless a live branch is set | Accepted |
| [0004](0004-content-on-the-volume-keep-five-live-versions.md) | Keep content on the volume and prune to five live versions | Accepted |
| [0005](0005-no-pull-request-previews-on-zad.md) | No per-pull-request preview deployments on ZAD | Superseded by TDR-0012 |
| [0006](0006-rebase-through-the-merge-queue.md) | Merge to beta through a merge queue that rebases | Accepted |
| [0007](0007-release-from-changelog-fragments.md) | Release from changelog fragments through a pull request | Accepted |
| [0008](0008-api-has-its-own-semver.md) | The API versions separately from Toon, with SemVer | Accepted |
| [0009](0009-cli-installs-from-git.md) | The CLI installs from git, not from a registry | Accepted |
| [0010](0010-audit-key-rotation-protects-new-rows-only.md) | Audit key rotation protects new rows only | Accepted |
| [0011](0011-one-admin-holds-the-audit-purge-in-the-database.md) | One platform administrator holds the audit purge, in the database | Accepted |
| [0012](0012-pull-request-previews-in-a-project-of-their-own.md) | Pull request previews in a ZAD project of their own | Accepted |
| [0013](0013-feedback-screenshots-in-the-database.md) | Feedback screenshots in the database | Accepted |
| [0014](0014-feedback-editor-in-a-sandboxed-frame.md) | Write feedback in the NLDD editor in a sandboxed, content-addressed frame, loaded on demand | Accepted |
| [0015](0015-feedback-with-an-account-on-unshielded-pages.md) | Feedback with an account on pages without the shielding | Accepted |
| [0016](0016-beta-rolls-out-to-a-standing-deployment.md) | beta rolls out to a standing deployment in the preview project | Accepted |
| [0017](0017-type-check-with-ty.md) | Type check with ty, pinned to an exact version | Accepted |

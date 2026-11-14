# TDR-0003: Any branch may go live unless a live branch is set

- Status: Accepted
- Date: 2026-09-30

## Context

A site links one repository, and a CI run proves itself with an OIDC
token (`docs/publishing.md`, "Linking a repository"). Whether only one
branch may publish live is a setting on that link, `live_branch`. A
site owner who wants the restriction can set it. The question is what
a link without it means.

On the day the CLI got its `site link` command, the live branch was
made mandatory there: `--live-branch <branch>` or `--any-branch`. The
same day that was reverted.

## Decision

`live_branch` is optional, in every entry point. Left empty, any branch
of the linked repository may publish live, in the admin form and in the
CLI alike. `toon site link` takes an optional `--live-branch`, empty by
default, and has no `--any-branch`.

What stays enforced either way: live publishing only from a `push`,
`workflow_dispatch` or `schedule` run, never a pull request, and
previews and cleanup from any branch (`ci/trust.py`,
`check_live_deploy`, and the CI repository trust row of `docs/security.md`).

## Consequences

- Linking a repository works without an extra mandatory setting, and
  the CLI and the form behave the same.
- Anyone who can push any branch of the linked repository, and run the
  workflow on it, can publish to the live site. Protecting the branch
  is then the repository's job, or the site owner sets `live_branch`.
- The revert makes this a deliberate choice, recorded here as an
  accepted risk.
- The CLI prints which case applies after linking, and the toon-publish
  skill asks which branch may go live instead of choosing one.

## Revisit when

An incident or a site owner shows that a restriction has to be
enforced rather than offered, or the platform requires one.

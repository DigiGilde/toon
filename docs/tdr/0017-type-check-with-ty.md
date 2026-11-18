# TDR-0017: Type check with ty, pinned to an exact version

- Status: Accepted
- Date: 2026-11-18

## Context

Nothing checked types. An assignment to a misspelled attribute of a
SQLAlchemy model in an API handler (`site.feedback_lve = body.live`)
runs without an error and is never persisted, and no test or lint
catches it unless a test happens to read the value back.

The candidates were mypy, pyright and ty. ty comes from the makers of
uv and ruff, which the repository already runs, and its own pre-commit
hook (`astral-sh/ty-pre-commit`) runs it through `uv check` in the
backend's locked environment, so it fits the existing gate without a
separate job. It reports the case above as
`error[missing-attribute]: Unresolved attribute`.

ty is pre-1.0 (0.0.106 when this was decided). Its diagnostics change
from release to release, so an unpinned or loosely pinned ty would fail
the gate on a commit that changed nothing.

## Decision

ty runs as a pre-commit hook, pinned by commit SHA to one exact ty
release, which the CI job `pre-commit` runs as well. It checks
`backend/src` only (`[tool.ty]` in `backend/pyproject.toml`), with the
default rule severities and warnings failing the run. mypy and pyright
are not added.

## Consequences

- Every bump of the pin is a deliberate change: Dependabot proposes it
  in the `tooling` pull request, and new diagnostics are fixed there
  before it merges.
- `backend/tests`, `backend/alembic`, `cli/` and `scripts/` are not type
  checked yet; extending the scope is follow-up work.
- The hook syncs the backend environment and downloads ty, so it needs
  network access on its first run.
- A false positive of a pre-1.0 checker is suppressed inline with
  `# ty: ignore[rule]` and a comment saying why, or set off in
  `[tool.ty]` with its reason when a rule is wrong for this codebase.

## Revisit when

ty reaches 1.0 and promises stable diagnostics: then a version range
can replace the exact pin. Or a ty release churns so much that a bump
cannot be fixed within its own pull request: then weigh mypy or pyright
again.

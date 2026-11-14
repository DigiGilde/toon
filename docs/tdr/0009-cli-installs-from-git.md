# TDR-0009: The CLI installs from git, not from a registry

- Status: Accepted
- Date: 2026-09-21

## Context

The `toon` CLI lives in `cli/`, a uv project of its own, and has to
reach people outside the repository. On PyPI.org the names `plak` and
`toon` both belong to unrelated packages. GitHub Packages has no
Python registry.

The install from git is in the first commit of the repository
(2026-09-21), which collapsed the history before it. The date above is
that commit, not a recorded decision date.

## Decision

The CLI is installed from the GitHub repository with uv, not from a
package registry: `@beta` for the latest, or `@<tag>` to pin a release.

```
uv tool install "git+https://github.com/DigiGilde/toon@beta#subdirectory=cli"
uv tool install "git+https://github.com/DigiGilde/toon@<tag>#subdirectory=cli"
```

A branch gives up the guarantee that tomorrow installs what you tested
today (`README.md`, Publishing with the `toon` CLI). A release gives
the CLI a new CalVer only when its code changed since the previous tag
(`docs/releasing.md`, Component versions).

## Consequences

- There is no `uv tool install toon` from a registry. A pinned install
  names a tag.
- The install needs access to GitHub and builds from source.
- The version a user sees can be older than the release tag, because
  the CLI only moves when it changed.
- Moving to a registry later changes the install line in `README.md`,
  `docs/publishing.md`, `docs/skill.md`, the plugin skill and the
  deploy tab of the admin (`frontend/src/components/site/TabDeploy.vue`).

## Revisit when

A package registry is chosen for the CLI.

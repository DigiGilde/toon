# TDR-0008: The API versions separately from Toon, with SemVer

- Status: Accepted
- Date: 2026-10-03

## Context

The CalVer of a release says when it happened, not whether a client
still fits. The CLI and the publish action need to know whether the
API they talk to changed in a way that breaks them. That needs a
contract that sits with a release, and a way to tell a breaking change
from a compatible one that does not depend on a reviewer noticing.

## Decision

The API has a SemVer version of its own. `backend/openapi.json` is
written by the release, so the file at a tag is the contract that
release shipped. The major moves by hand together with the path
(`/-/api/vN`). Minor and patch are computed with oasdiff at release
time. The pull request check `api-contract` (`api.yml`) refuses a
breaking change without a new major. See `docs/releasing.md`, The API
contract, and `docs/publishing.md`, A new major.

oasdiff comes from a container image pinned by tag and digest in
`.github/oasdiff/Containerfile`, which Dependabot keeps current. In CI
the binary is copied out of the image, and `just release` runs the
image.

## Consequences

- Running a release needs a container engine for oasdiff (Podman for
  `just release`).
- A feature pull request is meant to leave `backend/openapi.json`
  alone, so nobody keeps it current by hand. Nothing enforces that:
  `2d9fef7` changed it outside a release.
- A breaking change costs a new major and the transition period of
  `docs/publishing.md`, with no more than two majors running at once.
- We pin a binary through an image digest instead of an installable
  package, so bumping oasdiff is a Dependabot pull request.

## Revisit when

A second API consumer needs a different cadence, or Dependabot can keep
an oasdiff pin outside an image current (#104).

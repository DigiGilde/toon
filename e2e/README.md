# E2E suite

Playwright scenarios from spec §13 against the full compose stack
(nginx + app + postgres + mock OIDC), across the two origins of spec §4a:
admin actions on the admin host, viewing content on the content host.

## Running

```bash
just e2e
```

That script (`e2e/run.sh`):

1. starts the stack as a separate compose project `toon-e2e` on ports of
   its own (nginx `127.0.0.1:18888`, mock OIDC `127.0.0.1:18889`), so
   that the ordinary dev stack on 8080 stays untouched;
2. waits until both hosts are healthy;
3. runs `npx playwright test` (installing dependencies and Chromium if
   needed);
4. tears the stack down again, volumes included.

Knobs: `TOON_E2E_PORT`, `TOON_E2E_OIDC_PORT`, `TOON_E2E_KEEP_UP=1`
(leave the stack up to debug it), `TOON_E2E_ENGINE` (container engine,
podman by default). Extra arguments are passed through to Playwright:
`just e2e --grep secret`.

The job `e2e` in `.github/workflows/ci.yml` runs this same script on
every pull request, with `TOON_E2E_ENGINE=docker` because the GitHub
runner has no podman. The app serves the SPA of the image, so nothing
is built on the host first. It uploads `test-results/` when the suite
fails: the config keeps a trace and a screenshot per failed scenario.

No `/etc/hosts` lines are needed: the browser pins `*.localhost` to
`127.0.0.1` through `--host-resolver-rules`, and requests on the Node
side go to `127.0.0.1` with an explicit `Host` header. The CLI scenario is
the exception: the CLI resolves `beheer.toon.localhost` itself, which works
as long as the resolver sends `*.localhost` to loopback (it does on macOS
and on the usual Linux setups).

## Setup

- `compose.e2e.yml`: an overlay on `dev/compose.yml` with its own ports,
  the SPA and backend source of the image instead of host mounts, wider
  rate limits and a mock OIDC configuration with `interactiveLogin`, so
  that the suite can log in as several identities (dev always logs in
  fixed as `dev-beheerder`).
- `helpers/oidc.ts`: proxies the internal mock OIDC origin
  (`http://mock-oidc:8080`) per browser context to the published port
  and completes the interactive login form.
- `helpers/api.ts`: admin actions as an in-page fetch from the admin
  host (same contract as the SPA: `__Host-` session cookie, CSRF header,
  `Sec-Fetch-Site: same-origin`); bearer deploys through the Node side
  without an Origin header, the path that CI and the CLI both use.
- `helpers/cli.ts`: `toon login` (device flow): a request on the Node
  side, approving it in the real SPA on `/cli-link` with the
  already logged-in admin page, and the token exchange on the Node side
  again; yields the CLI token that the bearer deploys use. Also the session
  file `toon login --insecure-storage` writes, and a run of the real CLI
  (`uv run --project cli toon ...`) against its own `TOON_CONFIG_DIR`.
- `helpers/tar.ts`: builds the multi-file dist fixtures (html + css +
  subdirectory) in memory as `.tar.gz`.
- `tests/toon.spec.ts`: the scenarios, built up serially.
- `tests/cli.spec.ts`: the real CLI against the same stack: publishing a
  preview and a live version, removing the preview and logging out. It
  points `TOON_CONFIG_DIR` at a temp directory, because the CLI keeps its
  session per user account and a run must not touch a contributor's own.
- `tests/shared-origin.spec.ts`: a page of another site cannot read a
  restricted site from the browser cache (#152).
- `tests/feedback.spec.ts`: site feedback across both origins on a
  shielded preview with the real widget, from giving it (every kind, a
  screenshot, consent with an account, "Mijn feedback", hiding the button,
  links to a place) to handling it on the admin Feedback tab and with the
  CLI (a reply from `--message-file`), with the keyboard, small windows and
  an axe audit of the open widget.
- `tests/feedback-markdown.spec.ts`: Markdown and HTML in feedback, in the
  widget and on the Feedback tab.
- `tests/feedback-boundaries.spec.ts`: what the widget relies on, in the
  browser: a `fetch()` or a frame gets no widget and no token, a hide form
  posted from another site without a valid token is the neutral 404 and
  sets no cookie.
- `helpers/feedback.ts`: what the feedback scenarios share.

Scenarios that need the content origin login handoff (spec §4a, task
6.3) detect whether it is present in the stack and otherwise skip with a
clear reason; the same holds for the smoke test that checks whether the
built SPA is served on the root of the admin host.

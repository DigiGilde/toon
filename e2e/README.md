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
  again; yields the CLI token that the bearer deploys use.
- `helpers/tar.ts`: builds the multi-file dist fixtures (html + css +
  subdirectory) in memory as `.tar.gz`.
- `tests/toon.spec.ts`: the scenarios, built up serially.
- `tests/cli.spec.ts`: the real CLI (`uv run --project cli toon ...`)
  against the same stack: publishing a preview and a live version,
  removing the preview and logging out. It points `TOON_CONFIG_DIR` at
  a temp directory, because the CLI keeps its session per user account
  and a run must not touch a contributor's own.
- `tests/shared-origin.spec.ts`: a page of another site cannot read a
  restricted site from the browser cache (#152).
- `tests/feedback.spec.ts`: site feedback across both origins on a
  shielded preview with the real widget: page, point and text feedback,
  a screenshot (of a scrolled page too, with the bar fixed at the top of
  its window, of a page a window down whose root hides sideways
  overflow, compared with the window, and of a page with images on a
  preview only the team can see, which asks for none of them and leaves
  no refusal in the audit log), consent for feedback with an
  account (no press in the form's first second and none without its
  tick, the POST, the login and the way back), "Mijn feedback" (a pin
  under the open panel taking no press and no Tab, and clear of it
  letting a press through to the link it marks), hiding the button, the
  admin Feedback tab (a reply typed and not sent outlasting Escape), the
  CLI (a reply from `--message-file` included, and a soft hyphen it names
  before any request) and an axe audit of the open widget with
  `@axe-core/playwright`. Also the keyboard (Tab inside the overlays and
  the bottom sheet), a 320 px window for the widget and for the Feedback
  tab, the overlays at 375x667, 320x256 and 320x180 (the way out on
  screen, the bar off the crosshair and the frame), the button's place
  beside mouse and keyboard focus, also inside a web component of the
  page, a link to a place, announced in the page's own words and marked
  with a ring that lets the click through and lies over the Feedback
  button but under the open panel, also in a part of the page that
  scrolls on its own, on a page whose root and body clip sideways only,
  in a chart drawn as an svg inside an svg and in a link with an
  overflow class (a place that a box does clip stays hidden), and right
  of the middle of a page whose body hides sideways overflow, which
  leaves the window where it is sideways, and opened with "Bekijk op de
  pagina" for a point low on, and a quote near the end of, a listing
  three windows high, a link to a place that is gone, whose note makes
  way for keyboard focus, the open panel of a wide window moving
  clear of keyboard focus on the page and staying put for the mouse,
  Escape pressed on the page closing both, focus going back to the page
  when the button hides for a minute, the privacy link that stays in the
  tab, the menu of a site that takes feedback with an account only, a
  site team member who never consented sending without a name on a
  preview only the team can see, or going straight to the consent page,
  a visitor let in by a secret link alone who signs in, says yes and
  sends with their account while no page token goes into an address, a
  page opened before its site got a new address, an emoji held together
  by a zero-width joiner arriving whole from a visitor and from the team,
  and an item whose preview is gone, which the Feedback tab learns
  without a reload and can still filter on.
- `helpers/feedback.ts`: locators for the widget inside its open shadow
  root, waits and measurements the scenarios share, and helpers for the
  admin feedback API.

Scenarios that need the content origin login handoff (spec §4a, task
6.3) detect whether it is present in the stack and otherwise skip with a
clear reason; the same holds for the smoke test that checks whether the
built SPA is served on the root of the admin host.

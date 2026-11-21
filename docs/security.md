# Security checklist

The numbered rules themselves live in `docs/design.md`; a section number
here (§13, §5.7, §4a) is a section there.

This is the checklist from the design (§13). Every item gets a status:
**implemented** (built and tested, with the source alongside), **planned**
(still to be built) or **open** (falls outside the app, or was deliberately
postponed). Update this table as an item gets built; an item only moves to
"implemented" once the accompanying tests are green and the review panel has
approved it.

This table lists the security requirements from the design spec and their
current state. Whatever is still open below is open because it falls outside
the app or was deliberately postponed (DPIA, pentest).

## Authentication and session

| Item | Status |
|---|---|
| Alg allowlist (RS256/PS256/ES256, no `none`/HS*) as our own code on top of joserfc | implemented: `auth/oidc.py` (`ALG_ALLOWLIST`), `backend/tests/test_oidc.py` |
| RFC 9207 `iss` check on the callback query parameters, our own code on top of joserfc | implemented: `auth/oidc.py` (`check_callback_iss`), `backend/tests/test_oidc.py` |
| `acr` from a configured list (`TOON_OIDC_REQUIRED_ACR`); empty = no acr check and no `acr_values`, with a startup warning in production. Adjusted because the ZAD Keycloak only supplies `acr` `0`/`1` | implemented: `auth/oidc.py`, `backend/tests/test_oidc.py`; see `docs/local-development.md`, OIDC configuration |
| Client authentication `private_key_jwt` (default) or `client_secret_post`/`client_secret_basic` via `TOON_OIDC_CLIENT_AUTH`; exactly the matching secrets required, error messages without secret values. `client_secret_*` deviates from the NL GOV OIDC profile (private_key_jwt or mTLS) and exists for the ZAD Keycloak, which only creates client-secret clients; the ZAD variables `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` are the source for the `TOON_OIDC_*` settings | implemented: `config.py`, `auth/oidc.py`, `backend/tests/test_config.py`, `backend/tests/test_oidc.py` |
| `at_hash` validation when present in the id token | implemented: `auth/oidc.py`, `backend/tests/test_oidc.py` |
| `__Host-` session cookie: HttpOnly, Secure, `Path=/`, SameSite=Strict (spec §7; only the content session differs, and it is `__Secure-` with a path per site and SameSite=None, see the row below and "Why the content cookies are SameSite=None") | implemented: `auth/sessions.py`, `backend/tests/test_sessions.py` |
| Content session (SameSite=None, see below) separate from the admin session; admin routes refuse content sessions, content serving refuses admin sessions | implemented: `auth/sessions.py` (`SessionKind`), `platform/pages.py`, `backend/tests/test_sessions.py` |
| Content session cookie scoped per site (`__Secure-toon-content`, `Path=/{group}/{site}/`), with an anchor under `/-/`. One session behind both, so revocation, the 12-hour age and the kind check are unchanged. It narrows what one session reaches: a site this browser has not opened carries no cookie at all. It does not make the site boundary browser-enforced, because a cookie path is matched against the requested URL and not against the page that asks: a fetch aimed at a site the visitor has already opened still carries that site's cookie. What enforces the boundary is the origin per site; what catches the rest is the subresource check. `__Host-` had to go because that prefix requires `Path=/`; what it also forbade (a `Domain` attribute) is given up only for this cookie, never for the admin session | implemented: `auth/sessions.py`, `platform/pages.py`, `serving/router.py`, `backend/tests/test_sessions.py`, `backend/tests/test_serving.py` |
| Session id rotation on login | implemented: `platform/pages.py` (an existing session of the same kind expires at the callback), `backend/tests/test_sessions.py` |
| Logging out on the admin host exclusively via POST, and only from the admin origin itself (`admin_origin_ok`). POST alone is no guard there: the content host is same-site with the admin host, so `SameSite=Strict` still sends the session cookie along with a form on a published site. The content leg keeps accepting GET, for the reason in the row below | implemented: `platform/pages.py`, `api/origin_guard.py`, `backend/tests/test_sessions.py` |
| Logging out ends both sessions: the admin logout redirects to `/-/logout` on the content host, which clears the content session and sends the visitor back. That step accepts GET, because it arrives via a 303; making someone log out is the most harmless forgery there is. `?from=` is a fixed word, not an address. The anchor can arrive more than once, when a page's script or a sibling host set it again at `/`. The logout and the session rotation at a login then end every content session a correctly signed value names, and the logout clears the site cookies of each and the anchor at `/-/` and at `/`. The login clears an anchor at `/` too, and its shortcut to the next site is skipped, so the IdP's callback sets the visitor's own cookies. The IdP revalidation and the rate-limit key treat a request that carries the anchor more than once as one without it | implemented: `platform/pages.py`, `host_separation.py`, `auth/sessions.py` (`content_anchor_sessions_from_request`, `sole_content_anchor_session_from_request`), `backend/tests/test_sessions.py` (`TestTheAnchorMoreThanOnce`), `backend/tests/test_session_revalidation.py`, `backend/tests/test_app_integration.py`. That is why the admin CSP allows the content origin in `form-action` |
| RP-initiated logout at the IdP (OIDC RP-Initiated Logout 1.0), with `id_token_hint` | implemented behind `TOON_OIDC_RP_LOGOUT`, off by default: the `post_logout_redirect_uri` is `{TOON_CONTENT_BASE_URL}/-/logout?from=beheer` and must be registered at the IdP, otherwise the visitor lands on an error page of the IdP. With the flag on, `form-action` also allows the issuer origin |
| Periodic revalidation of a session at the IdP: on the first request after `TOON_IDP_RECHECK_SECONDS` (default 900, `0` turns it off) the session's refresh token goes to the token endpoint (`grant_type=refresh_token`, same client authentication as the login). If that succeeds the session stays, the rotated refresh token is stored and the clock restarts; the `sub` of a new id token must equal that of the session. If the IdP refuses with `invalid_grant` (blocked, deleted, session ended there), the Toon session expires and the request continues as not logged in. An outage (network, timeout, 5xx) leaves the session in place, does not block the request and is retried after a one-minute backoff; that goes to the application log, not the audit log. If the token endpoint refuses our **client** instead of the session (`invalid_client`, `unauthorized_client`, `unsupported_grant_type`, `invalid_request`, `invalid_scope`), everyone stays logged in - a broken integration must not log the whole platform out - but it gets loud: an ERROR line with the error code and the issuer, at most one per backoff window, and `/-/healthz` answers `{"status": "degraded", "checks": ["idp_revalidation"]}` (the code is only in the log) until a successful revalidation clears the complaint. `/-/healthz` still returns 200 while it does: the probe keeps the pod alive and a broken IdP integration is no reason to restart | implemented: `auth/revalidation.py`, `auth/oidc.py` (`refresh_tokens`), `main.py` (`SessionRecheckMiddleware`), `backend/tests/test_session_revalidation.py`. The window in which someone blocked at the IdP can still get in is thereby at most the interval instead of the full `MAX_SESSION_AGE` of 12 hours |
| The refresh token sits with the session in process memory: never in a log, never in an audit ref, never to the browser. The field has `repr=False`, so it does not end up in a dataclass dump in a log line or traceback either | implemented: `auth/sessions.py`, `backend/tests/test_session_revalidation.py` |
| Back-channel logout (OIDC Back-Channel Logout 1.0) on `POST /-/oidc/backchannel-logout`, only on the admin host. The endpoint is unauthenticated and its rate limit falls into the `content` class (keyed on client IP, see the row below), so a cheap, unverified pre-check runs first: token size, JWT shape, `typ` (untyped or `JWT`/`logout+jwt`, since Keycloak still mints plain `JWT`; see [keycloak/keycloak#28939](https://github.com/keycloak/keycloak/issues/28939)) and `alg` from the allowlist, `iss`, a recent `iat`, a non-empty `jti`, no `nonce`, the `events` claim, and a `jti` already in the replay cache - all read straight off the unverified JWT, so this can only narrow what reaches a JWKS fetch and a signature verification, never stand in for it. Only after that does the full check run: the signature via the existing JWKS handling, `iss`, `aud`/`azp`, a recent `iat`, an `exp` that has not passed, the claim `events` with `http://schemas.openid.net/event/backchannel-logout`, the presence of `sid` or `sub`, the absence of a `nonce`, and a `jti` (required, like `exp`, by section 2.4; a token without either is refused) that did not already pass by shortly before (replay cache in process memory). After that the sessions the token is about expire (on `sid`, otherwise all sessions of that `sub`; both session kinds). The answer is always a bare status with `Cache-Control: no-store`, byte-identical whether the pre-check or the full check refused, and does not betray whether there was a session | implemented: `platform/backchannel.py`, `auth/oidc.py` (`validate_logout_token`, `logout_token_prefilter`), `backend/tests/test_backchannel_logout.py`, `backend/tests/test_oidc.py`. The Keycloak client on ZAD still has to be configured for it (`docs/deploying-on-zad.md` §6a); until then the endpoint exists but nobody calls it |
| CLI sessions (`toon login`) are **not** re-validated at the IdP | deliberate, open point: a CLI session arises from a device code that a member approved in an admin session, and gets no IdP token of its own. The refresh token of that admin session sits in process memory and is long gone by the next CLI refresh (up to 30 days later, 90 absolute), so there is nothing to check with. Someone blocked at the IdP therefore keeps their CLI session until it expires or is revoked; deactivating in Toon does work immediately, because member status is checked per refresh and per deploy. Closing this requires storing the IdP refresh token of the approving admin session with the CLI session in the database, and thereby token material at rest that currently exists nowhere: that requires encryption and a decision of its own |
| Sessions do not survive a restart and cannot be revoked centrally | deliberate: the store sits in process memory and ZAD runs one replica. Deactivating works immediately anyway, because member status is checked per request (`require_active_member`, `_belongs_to_site`); revoking sessions would replace the message "je toegang is ingetrokken" with "je bent niet meer ingelogd" |
| `returnTo` strictly validated (own origin only, paths only) | implemented: `auth/sessions.py` (`valid_return_to`), `backend/tests/test_sessions.py` |

## Session lifetime and logout

A summary of the session and logout rows above, in one place, with the
platform's own timeouts alongside.

- **Toon admin and content sessions** last at most `MAX_SESSION_AGE`, 12 hours
  after login, with no idle timeout of its own (`auth/sessions.py`). They live
  in the process memory of a single replica, so a restart or a redeploy ends
  every session at once.
- **Revalidation at Keycloak** runs on the first request after
  `TOON_IDP_RECHECK_SECONDS` (default 900 seconds) since the session was last
  checked; an `invalid_grant` from the token endpoint ends the Toon session
  (`auth/revalidation.py`).
- **The ZAD Keycloak's own SSO session limits**, as set by the platform
  (RijksICTGilde/RIG-Cluster, `operations-manager/python/opi/configs/keycloak/bootstrap.yaml`):
  `ssoSessionIdleTimeout` 28800 seconds (8 hours), `ssoSessionMaxLifespan` 43200
  seconds (12 hours), explicitly to keep "a working day in one session" instead
  of Keycloak's own defaults (30 minutes idle, 10 hours max). These values come
  from the platform's own source, not from a measurement against our realm.
  `connectors/keycloak.py` applies `create_realm`/`update_realm` with the same
  session settings to every realm it manages, platform and per-project alike,
  so the project realm should carry the same values, but this was not
  independently confirmed against the live realm.

What ends a session, and when:

| Event | Effect |
|---|---|
| Logging out in Toon | Ends the admin and the content session at once (`platform/pages.py`, see the logout rows above). The Keycloak session itself stays unless `TOON_OIDC_RP_LOGOUT` is on, so the next login may be silent (an existing SSO session). CLI sessions (`toon login`) are untouched and are revoked separately, on the linked-CLI-sessions page |
| Logging out at Keycloak, or out of another app in the same realm | At once, if back-channel logout is configured on the client (§6a of `docs/deploying-on-zad.md`); otherwise at the next revalidation, at most `TOON_IDP_RECHECK_SECONDS` later |
| Account disabled or deleted in the ZAD realm | At the next revalidation (`invalid_grant`) |
| Account closed at SSO Rijk | Does **not** reach the realm: the ZAD realm brokers SSO Rijk over SAML and imports the user locally, and the refresh grant reads only that local `enabled` flag, never SSO Rijk itself. A new login fails, but an existing session lasts until the 12-hour cap. Same realm setup as Waggle, which raised the same gap with the platform team (`docs/security.md` commit `87fd3750` there; see also [RijksICTGilde/RIG-Cluster#173](https://github.com/RijksICTGilde/RIG-Cluster/issues/173)) |
| Member deactivated in Toon (`_deactivate`) | Immediate: `require_active_member` checks `member.status` on every request. Also revokes every CLI session of that member at once (`auth/members.py`, `api/admin/`) |

**CLI sessions** (`toon login`) follow a schedule of their own, unrelated to
the admin/content sessions above: 30 days after the last refresh, and 90 days
after linking at the latest, whichever comes first (`REFRESH_IDLE_TTL`,
`SESSION_MAX_TTL`, `cli/service.py`). They are not re-validated at the IdP;
see the CLI row above. A session from before the rename to Toon cannot be used
any more, because a stored hash covers the token prefix. It stays listed until
it expires.

## Access to content

| Item | Status |
|---|---|
| Neutral 404s, byte-identical on refusal and on non-existence | implemented: `serving/response.py` (`neutral_404_response`, the single construction point), `backend/tests/test_access_gate.py`, `backend/tests/test_serving.py` |
| Constant-time comparison of secret link verifiers | implemented: `access/keys.py`, `backend/tests/test_keys.py` |
| Constant-time comparison of CLI tokens (device code, access and refresh token): selector plus SHA-256 hash, with a dummy hash for an unknown selector so that an unknown selector takes as long as a wrong secret | implemented: `cli/service.py` (`matches`, `_DUMMY_HASH`), `backend/tests/test_cli_service.py` |
| `__Secure-` key cookie, HttpOnly, SameSite=None (see "Why the content cookies are SameSite=None"), path exactly on site/preview | implemented: `auth/sessions.py` (`KEY_COOKIE`), `serving/router.py`, `backend/tests/test_serving.py` |
| Secret link without a code: `?key=selector` only shows a code page for a usable key of that site, everything beyond that stays the neutral 404; the code goes in the body of a POST, never in the URL | implemented: `serving/code_page.py`, `access/gate.py` (`code_page_needed`), `access/keys.py` (`verify_parts`, `selector_usable`, `compare_dummy`), `backend/tests/test_code_page.py` |
| Secret link: `?key=` is redeemed (cookie plus 302 to the URL without `key`); on a `/{group}/{site}/...` path every other `key` value that starts like a secret link (a selector and the dot) leaves the URL with the same 302, without a cookie, on every answer but the login redirect (a refusal included), so no page reads it from its address or the tab's history; that 302 is the same whether or not the site exists; a selector alone leaves an allowed page too; every other path keeps the neutral 404, so no target is ever built from a path that could leave the host; key never in audit, returnTo or access log | implemented: `serving/router.py` (`_redeem_key`, `_strip_key_redirect`, `_refuse`), `access/keys.py` (`carries_secret_link`, `carries_bare_selector`), `containers/toon/Containerfile` and `justfile` (`--no-access-log`), `backend/tests/test_serving.py` (`TestKeyNeverReachesAPage`), `backend/tests/test_key_stripping_matrix.py` |
| Service workers are refused on the content host: a request with `Service-Worker: script` or `Sec-Fetch-Dest: serviceworker` gets the neutral 404 before anything else and without an audit row, so no site registers a worker that would see every navigation under its path, secret links included, before the server does; content responses carry `Vary: Service-Worker`, so a registration cannot be answered from an HTTP cache entry that a plain request filled | implemented: `serving/router.py` (`_is_service_worker_script`), `serving/response.py` (`_base_headers`), `backend/tests/test_serving.py` (`TestServiceWorkerScript`), `e2e/test_header_parity.py` |
| Expired preview gets the neutral 404 straight away at the access decision itself (not only via the purge job) | implemented: `access/gate.py` (`REASON_PREVIEW_EXPIRED`), `backend/tests/test_access_gate.py` |
| Anonymous top-level navigation to a preview or `_version` view: the same login redirect whatever lies behind the path, so it betrays no existence of non-public content (a public preview is served, as it always was); not for a subresource, not with a key in play, not for a path of non-slugs (the login could scope no site cookie there, so it would loop) | implemented: `serving/router.py`, `backend/tests/test_serving.py` (`TestFirstVisitToPreviewOrVersion`) |
| `_version` views exclusively for active group members, every access audited | implemented: `access/gate.py`, `serving/router.py`, `backend/tests/test_access_gate.py`, `backend/tests/test_serving.py` |
| Path validation before every access check (traversal, null bytes, backslashes, percent-encoded) | implemented: `serving/resolution.py` (`normalise_rest`), `backend/tests/test_resolution.py` |
| Non-public content is not served to a subresource request from another site's page on the same origin (one hostname for every site, so the cookies ride along); navigation and clients without `Sec-Fetch-*` stay through. A stopgap while every site shares one origin, see design.md §5.10 | implemented: `serving/router.py` (`_foreign_subresource`), `backend/tests/test_serving.py` |
| Non-public content and every `_version` view are `Cache-Control: no-store`, so the browser keeps nothing that a page of another site could read without asking the server. Reproduced in Chromium and Firefox before the fix (#152): a page of a public site without the sandbox read a restricted site's script and HTML with `fetch(url, {cache: "only-if-cached"})`, in Chromium even with a plain `fetch` and after the visitor's cookies were gone; `no-cache` stopped only the plain `fetch`. Price, measured in Chromium on a five-page restricted site with a 280 kB bundle: every page loads it again, 15 to 60 ms per page at 50 Mbit/s and 71 to 311 ms at 10 Mbit/s. Accepted risk: what a browser stored while a site was public stays there up to a year after it is restricted, as it was public then. An origin per site ends the need | implemented: `serving/response.py` (`cache_control`), `backend/tests/test_serving.py`, `e2e/tests/shared-origin.spec.ts` |
| Old address after a change of address (design.md §5.11): only where the gate found no group or site does serving look the slugs up as old ones, and the unchanged gate decides again at the current address. Whoever it lets in gets a 301 there (`Cache-Control: no-store`, `Sunset`), the `Location` built from the requested path with every `key` taken out; a valid `?key=` sets its cookie for the new path, and the key cookie of the old path is set again for the new one on a top-level navigation only. Everyone else gets the answer of before the change, byte for byte and whatever `Referer` comes along: a refusal holds the `Referer` to the old prefix, so a guess at the new address is no oracle, and `returnTo` stays the old path. Not in the same time, an accepted risk: deciding again at the current address makes that answer slower (about 1.4 ms, measured locally), which can tell that a path is an old address, not where it leads now. The 301 writes no audit row; a refusal is audited with the slugs asked for | implemented: `serving/addresses.py`, `serving/router.py`, `serving/code_page.py`, `slug_window.py`, `backend/tests/test_serving.py` (`TestOldAddress`), `backend/tests/test_code_page.py`, `backend/tests/test_access_gate.py` (the matrix at the old and the new slugs), `backend/tests/test_sessions.py` |

Serving (§5) is done entirely by the app itself:
`FileResponse` with Range, ETag/304 and the fixed header set (text files
compressed at ingest, design.md §5.4a), and the
host separation of §4a as app middleware (`host_separation.py`). There is no
X-Accel path and no nginx logic.

## API security

| Item | Status |
|---|---|
| CSRF token in a header (double submit against `__Host-toon-csrf`) on session-borne mutations | implemented: `auth/sessions.py` (`csrf_valid`), `api/admin/dependencies.py` (`require_csrf`), `backend/tests/test_admin_api.py` |
| Bearer tokens accepted exclusively on the two deploy/preview endpoints, the CLI session endpoints and eight endpoints of the admin API that take the CLI token only: the two creation endpoints (`POST /groups`, `POST /groups/{group}/sites`), linking a repository (`PUT /sites/{group}/{site}/repository`, so `toon site link`, while unlinking and requiring the site id stay session only), and reading, marking and answering feedback (`toon feedback`, the five routes of design.md §16.11 that take the CLI token). The allowlist is method plus exact path shape, and every other admin route answers a bearer with 401 `BEARER_NOT_ACCEPTED` before it is routed, also when a valid session rides along. The feedback widget API on the content host uses a scheme of its own (`Authorization: Toon-Feedback`), which the middleware leaves alone | implemented: `api/deploys/bearer.py` (`BearerOutsideDeploysMiddleware`, `accepts_bearer`), `api/cli.py`, `api/admin/`, `backend/tests/test_deploy_api.py`, `backend/tests/test_cli_api.py`, `backend/tests/test_cli_creation.py` (every admin route with a valid CLI token), `backend/tests/test_api_documentation.py` (OpenAPI and middleware agree per operation), `backend/tests/test_feedback_api.py` (the widget's `Toon-Feedback` scheme), `backend/tests/test_feedback_admin_api.py` (the CLI token on the feedback routes, refused on the session-only ones) |
| Creating a group or a site from the CLI (`toon group create`, `toon site create`): only a member's CLI token from `toon login`, never a CI ID token (401 `TOKEN_INVALID`), with the same role checks as the session (any active member for a group, group role `editor` for a site). The request may carry an initial access; that is no way around the access policy, because the creator becomes group admin or site admin and may set that access on the next request anyway. The CLI may not delete anything or manage members: those routes stay session only. A bearer request carries no ambient credentials, so it skips the CSRF double submit; the origin guard stays on it, as on the deploy router, so script on the content host holding a token is still refused. Group and site creation are audited with `refs.via` `cli` and the CLI session id, and with the access the new group or site starts with | implemented: `api/admin/dependencies.py` (`require_creator`), `api/errors.py` (refusals audited as the CLI member), `backend/tests/test_cli_creation.py`, `backend/tests/test_api_integration.py` |
| Secret links: always an expiry date, 90 days by default, 365 at most (BIO2 5.18.02); `expires_at` is NOT NULL | implemented, expiry date mandatory: `expiry.py`, `backend/tests/test_expiry.py`, `test_keys.py` |
| CI id token (OIDC "trusted publishing"): issuer allowlist (GitHub fixed, Forgejo per host in `TOON_CI_FORGEJO_HOSTS`), keys via discovery/JWKS of the issuer origin itself (https, 5s timeout, size limit, no redirects; cached for an hour, one fetch at a time per issuer, no retry for 30 seconds after a failed fetch), RS256 only, `exp`/`iat` mandatory with 60s leeway, `aud` must be exactly one value and exactly `TOON_BASE_URL` or `TOON_BASE_URL/-/sites/{site id}` with the id in its canonical lowercase form, without normalisation (upper case, a trailing slash or anything after the id is a mismatch); a token with a `jti` (GitHub) is accepted once within its lifetime and a second use is refused as replayed (audit reason `CI_TOKEN_REPLAYED`, response code `CI_TOKEN_INVALID`), while Forgejo tokens carry no `jti` and are not replay-checked; the memory of used tokens is in process, so a restart forgets it | implemented: `ci/providers.py`, `ci/tokens.py`, `config.py`, `backend/tests/test_ci_providers.py`, `test_ci_tokens.py` |
| Signing keys and discovery document of the identity providers (the OIDC provider and each CI issuer): fetched again every hour (`remote_cache.py`), so a key the issuer withdraws stops being trusted within that hour; one fetch at a time per key, no retry for 30 seconds after a failed fetch, and a failed refresh keeps serving the last keys instead of refusing every request. A token that names an unknown `kid` forces a fetch, at most once a minute | implemented: `remote_cache.py`, `auth/oidc.py`, `ci/tokens.py`, `backend/tests/test_oidc.py`, `test_ci_tokens.py` |
| CI repository trust: a site links exactly one repository (`PUT/DELETE /sites/{group}/{site}/repository`, site role admin, without a secret; the ids come from an anonymous lookup, or, for a private repository, from the admin, and a found repository must match entered ids); a token matches on `repository_id` (and `repository_owner_id` if present) or, without ids (Forgejo 15), on `owner/repo` with a re-confirmation against the Forgejo API (cached for 5 minutes); live publishing only from `push`, `workflow_dispatch` or `schedule` (fail closed without `event_name`) and, if it is set, only from `liveBranch`; previews and cleanup from any event and from any branch | implemented: `ci/trust.py`, `api/admin/` ("Linked repository"), `api/deploys/`, `backend/tests/test_ci_trust.py`, `test_admin_api.py`, `test_deploy_api.py` |
| CI binding to the site: consent from both sides, given to the site and not to its address. The workflow names the site id (`site-id`, `--site-id`, `TOON_SITE_ID`) and the CLI puts it in the token's audience; a bound token is used on that site or not at all. The token is decided on the bound site first, whatever the address: a repository not linked to that site gets one 401 `CI_AUDIENCE_MISMATCH` at every address, the site's own included, so it cannot tell which address is that site; the linked repository gets 409 `SITE_MOVED` with the site's current address at an address that leads elsewhere or nowhere. No answer names a site id. A token without a site id is accepted only by a link from before migration 0004 whose repository was linked to that one site (`site_id_required` false; the migration requires it on every link of a repository linked to several sites, because one of those may be planted: someone's own site, a typo of an address say, linked to another's public repository) and is refused with 403 `CI_SITE_ID_REQUIRED` elsewhere, only after the repository itself matched. The exemption ends one way: linking another repository, or a site admin's `PUT /sites/{group}/{site}/repository/site-id-required` (session and CSRF; `false` on a link that requires it is a 422). A trigger of migration 0004 keeps that rule in the database too, also under the code before 0004 after a rollback, and the downgrade of 0004 refuses while a link requires the site id. Deploy and teardown rows carry `refs.site_bound` | implemented: `ci/tokens.py`, `ci/trust.py`, `api/deploys/auth.py` (`_target_site`), `api/admin/`, `alembic/versions/0004_site_id_required.py`, `cli/toon_cli/session.py`, `cli/toon_cli/publish.py`, `actions/publish/action.yml`, `backend/tests/test_ci_binding.py` (the decision table, for deploy and teardown), `test_ci_tokens.py`, `test_ci_trust.py`, `test_admin_api.py`, `test_migrations.py`, `cli/tests/test_cli.py` |
| CLI device flow (`toon login`, RFC 8628): secrets as selector + SHA-256 hash, compared constant-time; the refresh token rotates on every use, reuse of an already used refresh token revokes the whole session (`cli_refresh_reuse`), except for the just-replaced token within ten seconds (concurrent refresh: only `INVALID_GRANT`); logging out also works with an expired access token or with the refresh token and always answers 204 (no oracle); deactivating a member revokes all their CLI sessions; the rate limit counts Bearer requests per IP, never per (unproven) token; access token 1 hour, session 30 days after the last refresh and at most 90 days after sign-in; approving requires an admin session no older than fifteen minutes that was itself started from the admin origin (`self_initiated`, recorded on `/-/login` as `admin_origin_ok`; a login another site navigated the browser into never counts as fresh, because the IdP returns without a prompt on an existing SSO session). The backend cannot tell a script on an admin page that sends the browser to `/-/login` from a member's own click (both arrive as `Sec-Fetch-Site: same-origin`), so the SPA never navigates to the login by script: `/cli-link` without a (fresh) session shows a sign-in link the member follows themselves, back to the bare `/cli-link` without the `?code` from the link, and the member then types the code their own terminal shows; `frontend/tests/login-navigation.test.ts` fails on any script navigation to the login route. Requiring `Sec-Fetch-User: ?1` on that login was evaluated and rejected: WebKit never sends the header, not even on a real link click (WebKit bug 247697; MDN browser-compat-data lists Safari as unsupported), so no Safari member could ever approve. A stronger option for later is a real re-authentication at the IdP for the approval (`prompt=login` or `max_age`, plus an `auth_time` check on the ID token), still to be verified against the ZAD Keycloak. Approving also requires a valid CSRF header and is only possible via the admin origin; the approval screen shows the account being linked to, the program, the time and the truncated network, and warns separately if the request comes from a different truncated network than the approver's (`sameNetwork`, without full IP addresses) and warns explicitly against a shared link or code (anti-phishing) | implemented: `cli/service.py`, `api/cli.py`, `api/admin/` ("CLI login: the member's side"), `frontend/src/pages/CliLink.vue`, `frontend/tests/login-navigation.test.ts`, `backend/tests/test_cli_service.py`, `test_cli_api.py`, `test_admin_api.py`, `test_sessions.py` (`TestLoginOrigin`) |
| CLI session at rest (`toon login`): per user account, not per directory. The tokens go into the system keyring through the `keyring` library (macOS Keychain, Secret Service on Linux, Windows Credential Manager), one entry per host under the service `toon:<host>`; `hosts.json` in the config directory (`$TOON_CONFIG_DIR`, else `$XDG_CONFIG_HOME/toon`, else `%APPDATA%\toon` on Windows and `~/.config/toon` elsewhere) holds the default host, the expiry and where the tokens live. A backend the library does not recommend (the null backend `PYTHON_KEYRING_BACKEND` can select, which drops a secret silently, or a plain-text one) counts as no keyring. Without a usable keyring, or with `--insecure-storage`, the tokens go into that file in plain text and `toon login` says so; a session that moves out of the keyring on a refresh says so too, and one that fell back returns to the keyring on its next refresh. The file is written 0600 through a temp file and an atomic replace, and is only read when it is a regular file of the user's own with mode 0600, not behind a symlink, checked on the opened descriptor, in a directory of the user's own that nobody else can write to: its default host decides where a `TOON_ACCESS_TOKEN` or CI ID token goes, and a looser file could hold a planted session. On Windows, where NTFS has neither mode bits nor a uid and there is no `O_NOFOLLOW`, the file only has to be a regular file: it is trusted on its location, since `%APPDATA%` sits in the user profile, whose default ACL admits only the user, SYSTEM and the Administrators; a directory named in `TOON_CONFIG_DIR` or `XDG_CONFIG_HOME` is as safe as its own ACL makes it. A session is only ever sent to the host it was issued for. It is stored as JSON, so a token the server chose cannot add a key of its own. A `.env.plak` from an earlier version is never read, only named. The two old hosts of the DigiGilde instance translate to their new ones from a fixed table, before anything is built from the host and with a warning on stderr, so a session stored under an old host is never sent. No other host translates, whatever it looks like. | implemented: `cli/toon_cli/config.py`, `cli/toon_cli/session.py`, `cli/toon_cli/hosts.py`, `cli/tests/test_cli.py` |
| Changing an address: the slug of a group by a group admin, the slug of a site by an effective site admin who also has a role in the group (an admin of the site alone gets 403 `INSUFFICIENT_ROLE`, so no 409 tells them which slugs the other sites of the group have); session and CSRF only, no CLI token and no exception for a platform administrator; the role is checked before the slug, and the row is locked and read again before anything is decided, a row deleted since the role check being a 404. A site is locked after its group, the order in which deleting a group locks them, so the two cannot deadlock and one address change in a group runs at a time; a group after an advisory lock, so two groups trading slugs cannot deadlock either. A taken slug is told apart by the constraint it runs into, any other integrity error staying a 500 | implemented: `api/admin/groups/group.py` (`set_group_slug`), `api/admin/sites/site.py` (`set_site_slug`), `api/admin/lookup.py` (`_reread_locked`), `api/admin/common.py` (`_slug_conflict`), `db.py` (`violated_constraint`), `backend/tests/test_addresses_api.py` (`TestChangesAtTheSameMoment`) |
| An old slug stays reserved for its group or site while it redirects, through day 30 after the change until midnight Amsterdam time, also against a concurrent claim: the primary keys of `group_slugs` and `site_slugs`, written by triggers only and never by an upsert with a `WHERE`, which would skip a reserved row in silence. At most five old slugs per group or site redirect at once, and a change counts towards the creation budget, which together bound hoarding addresses. The nightly cleanup frees a slug after its redirect ended, deleting a group or site frees its old slugs at once, and the downgrade of 0005 frees them all. Within a group, a group admin can release another site's old slug sooner and take it (`release`, audited as `site_slug_release`), anyone else with `release` set gets 403 `INSUFFICIENT_ROLE` before anything changes, and a current slug of another site is never released. A workflow bound to the site that gave it up then gets 409 `SITE_MOVED` there, and the link of the site that took it requires the site id, as after any change. A change also ends the exemption of a link from before the site id, for the renamed site or every site of the renamed group, so it cannot follow its site onto an address that used to be another's. Accepted risk: once free, an old address can lead to another's content, and a site that was never published again with its new address could load a script from whoever claims the old one; the admin says to publish again before the end date. Also accepted: from the end of a redirect at midnight Amsterdam time until the nightly cleanup at 03:00 UTC, four or five hours, an old address neither redirects nor can be claimed, and answers 404 | implemented: `alembic/versions/0005_slug_namespace.py`, `api/admin/`, `previews/cleanup_job.py` (`_release_retired_slugs`), `slug_window.py`, `backend/tests/test_slug_namespace.py`, `backend/tests/test_addresses_api.py`, `backend/tests/test_cleanup_job.py`, `backend/tests/test_ci_binding.py`, `backend/tests/test_migrations.py` |
| Authorization server-side per endpoint, never only in the SPA | implemented: `api/admin/`, `backend/tests/test_admin_api.py` |
| `application/problem+json` error contract with fixed status codes | implemented: `api/errors.py`, `backend/tests/test_api_integration.py` |

## Headers and CSP

| Item | Status |
|---|---|
| Content CSP (§5.7 regime) on all content routes | implemented: `serving/response.py` (`CONTENT_CSP`), `backend/tests/test_serving.py` |
| Platform CSP on the platform's own answers on the content host (root redirect, code page, neutral 404, the consent page for feedback with an account, the answers of the feedback widget API): no script, the one inline `<style>` by hash, no `'unsafe-inline'` | implemented: `serving/response.py` (`platform_csp`, `PLATFORM_CSP`), `platform/pages.py`, `serving/code_page.py`, `serving/feedback_consent.py` (`CONSENT_PAGE_HEADERS`), `ratelimit.py` (`FEEDBACK_API_HEADERS`), `backend/tests/test_security_headers.py` (`TestPlatformCsp`), `test_front_door.py`, `test_code_page.py`, `test_feedback_consent.py`, `test_feedback_api.py`. See "The platform's own pages" below |
| External sources per site (on by default) | implemented: `sites.external_sources`, `serving/response.py` (`CONTENT_CSP_EXTERNAL`), `api/admin/sites/site.py` (`PUT /sites/{group}/{site}/external-sources`, site role admin, CSRF, audit action `site_external_sources`), `frontend/src/components/site/TabAccess.vue`, `backend/tests/test_security_headers.py`, `test_serving.py`, `test_admin_api.py`. See "External sources" below |
| Shielding from other sites per site (on by default) | implemented: `sites.sandbox`, `serving/response.py` (`SANDBOX`, `CONTENT_CSP_SANDBOX`), `api/admin/sites/site.py` (`PUT /sites/{group}/{site}/sandbox`, site role admin, CSRF, audit action `site_sandbox`), `frontend/src/components/site/TabAccess.vue`, `backend/tests/test_security_headers.py`, `test_serving.py`, `test_admin_api.py`. See "Shielding from other sites" below |
| WebAssembly on every site (`'wasm-unsafe-eval'` in `script-src`, never `'unsafe-eval'`) | implemented: `serving/response.py` (`_CONTENT_DIRECTIVES`), `backend/tests/test_security_headers.py` (`test_webassembly_compiles_but_javascript_eval_stays_shut`), `test_serving.py`. See "WebAssembly" below |
| A 304 repeats the CSP and the Referrer-Policy of the 200 | implemented: `serving/response.py` (`make_304`), `serving/router.py`, `backend/tests/test_serving.py` (`TestEtag304`) |
| Admin CSP (§9 regime, stricter) on everything on the admin host | implemented: the app serves the SPA itself with this CSP (`platform/spa.py`, `backend/tests/test_spa.py`), and HTML on the admin host that carries no CSP of its own gets the same regime from `security_headers.py`. The regime follows the host, not the path: a path rule would make a refusal on the content host distinguishable from an ordinary neutral 404. `/-/api/docs` does carry a CSP of its own (`DOCS_CSP` in `api/docs.py`): identical, apart from `style-src`, which allows `'unsafe-inline'` because Swagger UI puts style attributes on its elements. `script-src` stays `'self'`; the page has no inline script, the Swagger bootstrap sits in `docs-init.js`; JSON answers only get `frame-ancestors 'none'` (`backend/tests/test_security_headers.py`) |
| HSTS (includeSubDomains) | implemented: app middleware `security_headers.py`, only with an https `TOON_BASE_URL`, `max-age=31536000; includeSubDomains`; preload deliberately not |
| `Permissions-Policy` | implemented: `camera=(), microphone=(), geolocation=()` on every answer |
| `Cross-Origin-Opener-Policy: same-origin` on everything on the admin host | implemented: `security_headers.py` |
| `frame-ancestors 'none'` on everything on the admin host | implemented: in the admin CSP of the SPA; API answers without a CSP get `frame-ancestors 'none'` from `security_headers.py` |
| `X-Robots-Tag: noindex, nofollow` on HTML on the admin host, previews and `_version` | implemented: `serving/response.py` (previews and `_version`), `platform/spa.py` and `security_headers.py` (admin host), `backend/tests/test_serving.py`, `backend/tests/test_security_headers.py`. Still open: key content (see below) |
| No inline scripts in the built SPA | implemented: `frontend/tests/build-output.test.ts` guards the built `index.html` (vitest, not in CI yet because that workflow is missing). Open: a CSP smoke test in the browser; the header side is covered by `backend/tests/test_security_headers.py` and `backend/tests/test_spa.py` |

### External sources

The content CSP allows scripts and styles from a fixed list of hosts by
default. That is the setting that fits what people deliver: HTML from an AI
assistant that loads Chart.js or Tailwind from a CDN, or a site that draws the
web components of a design system from a CDN. Without that setting such
content breaks without anyone seeing why.

Per site there is therefore a switch, "Externe bronnen toestaan" (allow
external sources) (`sites.external_sources`, `true` by default). Turning it off
is an extra restriction, and the safer choice for a confidential page: the page
then fetches nothing from outside, lets nobody from outside watch along either,
and runs no code that we did not deploy. With it on, the content gets the same
CSP with exactly these additions, and no others:

| Directive | Added |
|---|---|
| `script-src` | `https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://unpkg.com https://cdn.tailwindcss.com` |
| `style-src` | the first three, plus `https://fonts.googleapis.com` |
| `font-src` | `https://fonts.gstatic.com` |

Tailwind's CDN is in `script-src` only: it is a script that injects its own
`<style>` element, which `'unsafe-inline'` in `style-src` already allows.

This is a fixed list, not a free-text field: supplying a host of your own would
turn the switch into a way to run arbitrary code on the content host.

What does not move with it: `connect-src` stays `'self'`, so even with external
sources on the page cannot send data to those parties (or to anyone else).
`img-src` stays `'self' data: blob:`, and `frame-ancestors 'none'`,
`form-action 'self'`, `object-src 'none'` and `base-uri 'self'` stay in place.
Both policies come from one table in `serving/response.py`, so the strict
variant and the variant with external sources cannot drift apart;
`backend/tests/test_security_headers.py` pins that relationship down.

The switch applies to every answer that carries the content CSP for that site:
the live pages, previews, `_version` views and assets. Two places deliberately
do not follow it. The neutral 404 always keeps the platform CSP, because it
has to stay byte-identical: a deviating CSP would betray which site a refused
path belonged to. The code page for a secret link (`serving/code_page.py`)
keeps the platform CSP as well, because it is a page of the platform itself,
with its own markup and no need for anything from outside.

What the administrator needs to know is in the SPA in those words too: the
parties behind cdnjs, jsDelivr, unpkg, the Tailwind CDN and Google Fonts see
the IP address of
every visitor of the page, and their code runs in the page. For confidential
pages the switch therefore stays off; anyone who wants certainty bundles the
library into the dist.

#### Accepted risk: the switch stays on by default

This is a decision, not an oversight. The publisher decides what their own page
loads, and a default of off would break the content people actually deliver
without them being able to see why. So a published site may load scripts and
styles from the allowed CDNs until its publisher turns that off.

What is accepted with it: every site runs on one origin. A compromise of one of
those CDNs therefore does not stay with one site. The code runs in the page of
every site whose visitor loads it, on the content origin, with whatever that
page can reach.

The per-site sandbox reduces that substantially: a sandboxed document cannot
read another site's content or storage, so a compromised script is limited to
the page it ran in. It is not the whole answer. It does not stop the
CDN seeing every visitor of every site that loads from it, it does not stop the
script doing whatever that one page does, and it holds only for the documents
that are sandboxed.

Whoever cannot accept that turns the switch off per site and bundles the
library into the dist; for confidential pages that is the advice, in those
words, in the interface.

### Shielding from other sites

Every site of every group is served from one hostname, and published content
may run its own JavaScript. Without a countermeasure the pages of site A are
same-origin with the pages of site B: a script on A can fetch B's paths on the
visitor's authority and read the answer, open B in a window and read the
document, or overwrite the cookies of the shared origin. That was reproduced
end to end, not derived on paper. Giving every site its own origin is not
available to this project, so the equivalent has to come out of the response.

The content CSP therefore carries, per site and on by default
(`sites.sandbox`, server default true):

```
sandbox allow-scripts allow-forms allow-popups
```

The load-bearing part is what is *not* in that list: without `allow-same-origin`
the browser gives the document an opaque origin. It is then same-origin with
nothing at all, so it can read no other document on this hostname and can
neither read nor write storage. Scripts, forms and popups stay allowed, so an
ordinary page keeps working.

Verified in a browser against the dev stack, with and without the directive.
With the sandbox the page's own stylesheet, script and image load normally,
`window.origin` reads `null`, `localStorage`, `sessionStorage` and
`document.cookie` throw a `SecurityError`, a `fetch` of another site's path
fails instead of returning that site's HTML, and a window opened on another
site's path cannot be read by the opener. Without it, the same page reads the
other site's page in full.

The price is real and falls on the publisher: no `localStorage`, no
`sessionStorage`, no `document.cookie`, and nothing the browser fetches in
CORS mode. That last one is measured, not derived: from an opaque origin a web
font, a module script and a `fetch` of the site's own files go out with
`Origin: null` and no cookie, and content answers carry no CORS headers, so the
browser refuses them. Only the feedback widget API answers with CORS, and it
reads no cookie (see "Site feedback"). A site that stores anything in the browser, or serves its own
web font, or loads ES modules, stops working until its owner turns the
shielding off, which is why the switch and its explanation sit next to the
external-sources switch on the site's access screen, in the words a publisher
uses ("een onthouden voorkeur, een half ingevuld formulier").

What this does and does not buy, stated plainly. It protects the visitors of
*other* sites against this one: content published here cannot reach across to
what that visitor may see elsewhere on the hostname. It is per site, so a site
whose owner turns the shielding off is back in the shared-origin situation, for
its own visitors and towards every other site, and the platform as a whole is
not isolated by this. It is a default that most sites can keep, not a boundary
the platform can enforce, and it does not replace the access gate: a page that
is refused is still refused.

The same two exceptions as for external sources apply. The neutral 404 keeps
the platform CSP whatever a site sets, because it has to stay byte-identical
across every cause of refusal. The code page for a secret link keeps it too:
it is a page of the platform itself.

### The platform's own pages

The root redirect, the code page for a secret link and the neutral 404 are
answers of the platform itself on the content host, the origin every
published site shares. None of them runs script, so their policy allows none
(`platform_csp` in `serving/response.py`):

```
default-src 'none'; style-src 'sha256-…'; base-uri 'none';
form-action 'none'; frame-ancestors 'none'
```

The page's one inline `<style>` is admitted by its hash, computed from the
stylesheet constant when the module loads, so an edit to the CSS carries its
hash along. `form-action` is `'self'` on the code page, whose form posts to
`/-/code` and is redirected to the site on this origin, and `'none'`
elsewhere. If one of these templates ever let markup through, no script would
run and no style attribute would apply. The tests render each page and hold
every `<style>` against the hash in its own header, and refuse a `<script>` or
a `style=""` attribute in it, because a hash does not cover an attribute.

Site feedback adds two more: the consent page for feedback with an account
carries this policy with `form-action 'self'` for its form, plus
`sandbox allow-forms` (design.md §16.6), and the answers of the feedback
widget API carry it in their header set (design.md §16.8).

This meets NCSC's ICT-beveiligingsrichtlijnen voor webapplicaties, U/PW.03,
which asks for `default-src` `'none'` or `'self'` and no `unsafe-inline`,
`unsafe-eval` or `unsafe-hashes`; this policy uses `'none'`. It passes
internet.nl's CSP check, run with its own checker code against these three
policies. internet.nl tests the root of a host without following redirects
(`http_headers_check` requests it with `allow_redirects=False`), so on the
content host it judges the headers of the redirect to the admin host, which
carries `PLATFORM_CSP`; its verdict says nothing about the policy of the
published sites, which keeps `'unsafe-inline'`. Verified in Chromium, Firefox
and WebKit: the stylesheet applies on the code page and the code form posts
and follows its redirect. Firefox no longer requests `/favicon.ico` for these
pages; there is none to fetch on this host.

### WebAssembly

The content CSP allows `'wasm-unsafe-eval'` in `script-src`, on every site and
without a switch. It lets a page compile WebAssembly, which a static search
index such as Pagefind (and so Starlight's built-in search) needs. It is not
`'unsafe-eval'`: `eval()`, `new Function()` and a string passed to
`setTimeout` stay blocked (CSP Level 3, "wasm-unsafe-eval only permits
WebAssembly and does not affect JavaScript"). This section is the explicit
risk assessment BIO2 asks for a security-relevant change (5.08.01, 8.32.01).

What it adds. `script-src` already allows `'unsafe-inline'`, so whoever gets
script into a page can already run any JavaScript there. WebAssembly gives
that script no capability it lacks: a module reaches the network, the DOM and
storage only through the JavaScript that instantiates it, so `connect-src
'self'`, the shielding and the access gate bound it exactly as they bound the
script. For privacy nothing changes; no request can leave that could not leave
before. What is new is narrow:

- Compiling bytes into code. Dangerous only where a site's own code compiles
  bytes an attacker controls, which is not a pattern static sites have.
- The browser's WebAssembly compiler as attack surface. A renderer exploit
  through it needs an unpatched browser and a publisher who wants to attack
  their own visitors; such a publisher can send those visitors to a domain of
  their own just as well.
- Efficient in-browser mining. A question of trusting the publisher, as it
  already is for JavaScript.

Why not a switch per site. A switch costs a column, an endpoint, an audit
action, a bilingual UI and twice as many policies, and buys little against
the above. Without the keyword a search fails silently: the page looks
complete and only the console says why. That is the same reasoning that keeps
external sources on by default.

Under the shielding the keyword changes little: WebAssembly compiles there,
but a site's own module scripts, workers and `fetch` are refused (see
"Shielding from other sites"), so Pagefind needs the shielding off.
Verified in Chromium, Firefox and WebKit against a Pagefind 1.5.2 build, with
the policy this file describes: without the keyword the module is refused,
with it the search answers, and under the sandbox it fails on the module
import and the worker.

Checked against the frameworks. NCSC's ICT-beveiligingsrichtlijnen voor
webapplicaties (U/PW.03) names `unsafe-inline`, `unsafe-eval` and
`unsafe-hashes` as unsafe; `'wasm-unsafe-eval'` is a different keyword and not
among them. internet.nl's CSP check matches each token from its start, so it
does not count `'wasm-unsafe-eval'` as `'unsafe-eval'`; the content CSP
fails that check on `'unsafe-inline'` alone, before and after this change.
The `'unsafe-inline'` itself deviates from U/PW.03, and did before this
change: published content may carry inline script.

Rollback: drop the keyword from `_CONTENT_DIRECTIVES`. No data or schema
depends on it. With the 304 repeating the CSP, the narrower policy reaches a
cached HTML page on its next revalidation; a cached worker script of a public
site is `immutable`, so it keeps the wider policy until it leaves the
visitor's cache (design.md §5.4).

### Why the content cookies are SameSite=None

The shielding above has a consequence the first version of it missed. An
opaque origin is cross-site with *everything*, the document's own site
included. Measured in a browser: the subresource requests a sandboxed page
makes for its own stylesheet, script and image arrive with
`Sec-Fetch-Site: cross-site`, without an `Origin` and without a `Referer`,
whatever `Referrer-Policy` the response carries. Under `SameSite=Lax` the
browser therefore withholds the content session cookie there, and every
non-public site was served as a page without a single one of its own assets,
while public sites were unaffected because they need no cookie.

There is no request signal that separates such a load from a third party's,
so the site cookie (`__Secure-toon-content`) and the secret-link cookie
(`__Secure-toon-key`) are `SameSite=None; Secure`. The anchor stays `Lax`: it
is only ever read on a top-level navigation, which Lax covers, and it is the
cookie that can mint a site cookie for the next site.

What that opens, shape by shape, for a site this browser has a cookie for:

- **Top-level navigation** from another site: unchanged. Lax already sent the
  cookie on a top-level GET navigation, and the visitor sees where they land.
- **`fetch`/XHR**: the request goes out with the cookie, the answer is
  unreadable. Reading a cross-origin response needs CORS, content answers
  carry no CORS headers, and `no-cors` yields an opaque response. So the pages
  themselves, which is where the content is, stay out of reach. The one
  answer with CORS headers is the feedback widget API's, which allows no
  credentials and reads no cookie, so a cookie riding along there opens
  nothing (see "Site feedback").
- **`<iframe>`**: `frame-ancestors 'none'` on every content response, the
  neutral 404 included, so the browser refuses to render either and the two
  fail identically. The feedback editor's frame is the exception: a static
  document, the same for everyone, which no cookie changes (design.md
  §16.15).
- **`<img>`**: a real image of the site loads. The including page cannot read
  its pixels (the canvas is tainted) but does learn that it exists and what
  its intrinsic dimensions are, for a path it guessed.
- **`<link rel=stylesheet>`**: the site's stylesheet loads and applies.
  `cssRules` throws a `SecurityError`, but the effect is observable through
  `getComputedStyle`, so a private site's CSS has to be treated as readable by
  a page that knows its URL.
- **`<script src>`**: a real script asset of the site executes in the
  including page. `X-Content-Type-Options: nosniff` keeps HTML pages and the
  neutral 404 from executing, but whatever a non-public site puts in a `.js`
  file is then readable through its own globals. Data does not belong in an
  asset of a non-public site.
- **Mutations**: none that a cookie alone authorises, and so no CSRF cookie.
  `/-/code` is anonymous and guarded by its own origin check (`Origin`,
  falling back to `Sec-Fetch-Site`), so a form on another site fails there.
  The content logout is a GET and deliberately forgeable. Site feedback adds
  the rest, and none of it takes a cookie as its credential: giving consent
  takes a sealed nonce from Toon's own sandboxed page and sets no cookie, the
  widget API takes the page token in a header, and ending consent is a GET as
  forgeable as the logout and as harmless, because it can only take consent
  away (design.md §16.6, §16.8). Hiding the button takes a page token of
  that very site, which any page that holds one can send (design.md §16.7).

Accepted, in short: a third-party page can have a visited site's assets loaded
with the visitor's credentials and observe their effect, but not read its
pages. The alternatives are worse. Not sandboxing non-public sites inverts the
model, since those are the sites with something to protect and any published
page can attack them. Serving assets of a non-public site without credentials
is the access gate with a hole in it. A separate cookie for the sandboxed case
only would be attachable by a third party in exactly the same way, so it buys
nothing but a second thing to revoke. `Cross-Origin-Resource-Policy:
same-site` was tried and rejected on measurement: an opaque origin is not
same-site, so it blocks the page's own assets as thoroughly as a stranger's.
`_foreign_subresource` cannot help here either; it only looks at same-origin
requests, and a cross-site rule would have to allow requests without a
`Referer`, which is exactly what a third party can arrange.

Browsers that block or partition third-party cookies are the residual risk in
the other direction: where a browser treats the opaque origin as a third party
to the site, it withholds the cookie again and the assets of a non-public
sandboxed site do not load. Verified working in Chromium; the durable fix for
both sides is an origin per site, which makes the sandbox and this exception
unnecessary at once.

## Site feedback

Visitors give feedback on a site's pages through a widget Toon adds to them,
and the site team handles it in the admin and with the CLI. The rules are in
design.md §16.

| Item | Status |
|---|---|
| Feedback nobody asked for: a site takes none until a site admin turns it on, the live site only on top of the previews, and without an account only when the admin widens who may give it (design.md §16.1) | implemented: `models/publication.py`, `alembic/versions/0011_feedback.py`, `api/admin/feedback/routes.py`, `frontend/src/components/site/FeedbackSettings.vue`, `backend/tests/test_migrations.py`, `backend/tests/test_feedback_admin_api.py`, `frontend/src/components/site/FeedbackSettings.test.ts` |
| A page with a token in it kept for someone else: only a navigation gets the widget, and a navigation to a feedback page is never stored or confirmed with a 304 (design.md §16.2, §16.3) | implemented: `serving/feedback_inject.py`, `serving/router.py`, `serving/response.py`, `backend/tests/test_feedback_inject.py`, `e2e/tests/feedback-boundaries.spec.ts` |
| A page that costs too much to inject into: its decoded size is bounded at 10 MiB before anything is decoded, and at most 64 KiB of it is examined (design.md §16.3) | implemented: `serving/feedback_inject.py`, `constants.py`, `backend/tests/test_feedback_inject.py` |
| A forged or replayed page token: it is sealed with a key from `TOON_SESSION_SECRET`, expires after twelve hours, is parsed as strictly as it is minted, and is taken by the widget API alone, never by a page or an asset (design.md §16.5) | implemented: `serving/feedback_seal.py`, `serving/feedback_token.py`, `backend/tests/test_feedback_seal.py`, `backend/tests/test_feedback_token.py` |
| A planted site cookie beside the visitor's own: a page view whose request carries the site cookie twice gets a token without a session (design.md §16.5) | implemented: `serving/feedback_inject.py`, `auth/sessions.py`, `backend/tests/test_feedback_inject.py` |
| A token that outlives the access it was minted for: every call checks the session, the consent and the gate again, and every ground for refusal answers the one byte-identical `FEEDBACK_UNAVAILABLE` (design.md §16.5) | implemented: `serving/feedback_access.py`, `auth/revalidation.py`, `backend/tests/test_feedback_api.py`, `backend/tests/test_feedback_matrix.py` |
| Somebody trying page tokens unnoticed (BIO2, ISO 27002:2022 8.16): each process counts the tokens the widget API and the hide route refuse, per reason, and a window of ten minutes with more than 100 of them is one WARNING with the counts alone, no address, account, site or token (design.md §16.5, `docs/deploying-on-zad.md`) | implemented: `serving/feedback_refusals.py`, `serving/feedback_access.py`, `serving/feedback_consent.py`, `backend/tests/test_feedback_refusals.py` |
| A site's scripts sending in a visitor's name: feedback with an account only after consent on Toon's own sandboxed page, which takes no single press as consent and sets no cookie (design.md §16.6) | implemented: `serving/feedback_consent.py`, `serving/feedback_token.py`, `serving/feedback_inject.py`, `auth/sessions.py`, `platform/pages.py`, `backend/tests/test_feedback_consent.py`, `backend/tests/test_feedback_token.py`, `backend/tests/test_feedback_inject.py`, `backend/tests/test_sessions.py`. See "What site feedback adds" below |
| A hide cookie written by a page's script or a sibling host: only a mark Toon sealed for that very site hides the button (design.md §16.7) | implemented: `serving/feedback_consent.py`, `serving/feedback_token.py`, `serving/feedback_inject.py`, `auth/sessions.py`, `backend/tests/test_feedback_consent.py`, `backend/tests/test_feedback_inject.py`, `backend/tests/test_feedback_token.py`, `e2e/tests/feedback-boundaries.spec.ts` |
| A cross-origin page reading answers with the visitor's credentials: CORS only under `/-/feedback/v1/`, `Access-Control-Allow-Origin: *` without credentials on an API that reads no cookie (design.md §16.8) | implemented: `ratelimit.py` (`FEEDBACK_API_HEADERS`), `serving/feedback_api.py`, `backend/tests/test_feedback_api.py`, `backend/tests/test_ratelimit.py`, `backend/tests/test_app_integration.py` |
| Paths under the prefix that no route names: the neutral 404 for every other method and path, on both hosts (design.md §4a, §16.8) | implemented: `host_separation.py`, `serving/feedback_routes.py`, `backend/tests/test_host_separation.py`, `backend/tests/test_feedback_api.py` |
| Hostile input: one rule for every string a submitter writes, strict JSON in closed schemas, and author fields from the session or from nowhere, each refusal a 422 and never a 500 (design.md §16.9) | implemented: `serving/feedback_input.py`, `serving/feedback_api.py`, `backend/tests/test_feedback_input.py`, `backend/tests/test_feedback_api.py` |
| Stored XSS through feedback text: it renders from one parser into DOM nodes, in the widget and with Vue's `h()` in the admin, never as markup, and the CLI drops control and format characters from what it prints (design.md §16.11, §16.14) | implemented: `cli/toon_cli/output.py`, `cli/toon_cli/feedback.py`, `cli/tests/test_feedback.py`, `frontend/src/feedback-text/markdown.ts`, `frontend/src/feedback-widget/mine.ts`, `frontend/src/components/FeedbackMarkdown.vue`, `frontend/src/feedback-text/markdown.test.ts`, `frontend/src/components/FeedbackMarkdown.test.ts`, `frontend/tests/feedback-widget-modules.test.ts`, `e2e/tests/feedback-markdown.spec.ts` |
| A member reading or changing feedback beyond their role: the role per route, the email address of a submitter for `editor` and up only, the CLI token on the five routes that read, mark and answer only, and the four admin actions audited with no comment, reply, name or email address in `refs` (design.md §16.11, `docs/audit-log.md`) | implemented: `api/admin/feedback/routes.py`, `api/admin/feedback/queries.py`, `api/deploys/bearer.py`, `audit/vocabulary.py`, `backend/tests/test_feedback_admin_api.py`, `backend/tests/test_audit_vocabulary.py` |
| A screenshot or a submitter's text kept or run where it should not be: the admin API serves a screenshot on the admin host only, to the site team, with `default-src 'none'; sandbox`, `private, no-store` and `nosniff`, and its feedback answers `no-store` as well. The CLI writes a screenshot to a new file only, mode 0600 (design.md §16.11) | implemented: `api/admin/feedback/routes.py`, `cli/toon_cli/feedback.py`, `backend/tests/test_feedback_admin_api.py`, `cli/tests/test_feedback.py` |
| Instructions hidden in feedback, when the CLI's output reaches an AI assistant: the CLI marks all visitor text as untrusted data and leaves names and email addresses out unless asked (design.md §16.11), and the `toon-feedback` skill never follows that text, never puts it in a shell command and acts only with the user's approval (`docs/skill.md`) | implemented: `cli/toon_cli/feedback.py`, `plugin/skills/toon-feedback/SKILL.md`, `cli/tests/test_feedback.py`, the `feedback-*` evals under `plugin/evals/` |
| Hostile screenshots and bodies that cost the event loop: the bytes decide the type, the dimensions come from a header found within a bounded walk, nothing is decoded, and the delimiters of a body are counted before it is parsed (design.md §16.9) | implemented: `serving/feedback_images.py`, `serving/feedback_body.py`, `backend/tests/test_feedback_images.py`, `backend/tests/test_feedback_body.py` |
| Spam and storage exhaustion: caps on open items, screenshot space and replies, checked under advisory locks, and write counters per author and per site in counters of their own. Authors count attempts, a site counts accepted writes: each write reserves the site's before its body is read and gives it back unless it is stored, so concurrent writes cannot take a site past its budget, refused attempts cannot lock a site out and a flood of author keys cannot evict a site's (design.md §16.10) | implemented: `serving/feedback_api.py`, `constants.py`, `backend/tests/test_feedback_api.py`, `backend/tests/test_feedback_matrix.py` |
| Memory exhaustion by uploads: a budget for the bodies in flight that every chunk takes its room in before it is parsed, a share per client and a deadline per body (design.md §16.10) | implemented: `serving/feedback_body.py`, `constants.py`, `backend/tests/test_feedback_body.py` |
| A widget bundle swapped or stale, or one that reaches into the site: it loads from an address that carries its hash on Toon's own origin, and keeps out of the site's storage, cookies, globals and custom elements (design.md §16.4) | implemented: `serving/feedback_widget.py`, `serving/feedback_routes.py`, `frontend/vite.widget.config.ts`, `backend/tests/test_feedback_widget_bundle.py`, `frontend/tests/feedback-widget-bundle.test.ts`, `frontend/tests/feedback-widget-modules.test.ts` |
| The editor reaching into the site, or the site into it: a static, sandboxed frame document with a policy of its own, which talks to the widget over one port (design.md §16.15) | implemented: `serving/feedback_editor.py`, `serving/feedback_routes.py`, `frontend/src/feedback-editor/`, `frontend/src/feedback-widget/editor.ts`, `backend/tests/test_feedback_editor.py`, `frontend/src/feedback-editor/frame.test.ts`, `frontend/src/feedback-widget/editor.test.ts` |
| Feedback kept longer than needed: the nightly cleanup purges it on its terms (design.md §16.12) | implemented: `previews/cleanup_job.py`, `backend/tests/test_cleanup_job.py`. See `docs/privacy.md` |
| A webhook used to make Toon call somewhere it should not (SSRF): Toon posts only to a host the operator lists in `TOON_FEEDBACK_WEBHOOK_HOSTS`, matched exactly on host and port, `https` only, no IP literal, no user name or password, no fragment, at most 2048 characters. The URL is checked when it is saved and again before every post, by a client that follows no redirect, uses no proxy from the environment, has a connect timeout of 3 seconds and 5 for the rest, and never reads the answer. DNS is trusted for a listed host, so an operator lists only hosts they trust. An empty list turns Mattermost off (design.md §16.16, TDR-0018) | implemented: `notifications/webhooks.py`, `config.py`, `api/admin/notifications.py`, `backend/tests/test_notifications.py`, `backend/tests/test_notifications_api.py`, `backend/tests/test_config.py` |
| A webhook URL leaking: whoever has it posts in the channel. The API takes it and never gives it back (a read says whether one is set, its host, language and moment), it is never logged, never in an audit row (the host alone) and never quoted in a problem+json answer. It is stored as it is, in a table of its own so loading a site never loads it; it is not encrypted, because Toon has no key for it (TDR-0018). A database dump gives whoever holds it the power to post in those channels | implemented: `models/notifications.py`, `api/admin/notifications.py`, `notifications/webhooks.py`, `alembic/versions/0012_feedback_notifications.py`, `backend/tests/test_notifications_api.py`, `backend/tests/test_migrations.py` |
| Visitor text turning a Mattermost post around: the comment stands in an indented code block, every `@` in visitor text, the page path included, becomes U+FF20, control and format characters go except a line break, a tab, U+200C and U+200D, a comment is cut at 500 characters and 20 lines and a post at 12,000 characters. A post never carries the submitter (design.md §16.16) | implemented: `notifications/content.py`, `notifications/mattermost.py`, `backend/tests/test_notifications.py` |
| Feedback leaking through an inbox, which is forwarded and kept outside Toon's retention: an email carries metadata only (site, page, kind, time, whether a screenshot came with it) and a link, never the comment and never the submitter. Who gets one is decided when it is sent: an active member with a role on the site, an address the IdP verified, a choice that says yes, and never for their own feedback (design.md §16.16) | implemented: `notifications/recipients.py`, `notifications/mail.py`, `notifications/content.py`, `backend/tests/test_notifications.py`, `backend/tests/test_notifications_job.py` |
| Email in the clear, or the SMTP password exposed: STARTTLS with certificate verification is the default and a server that does not offer it is an error, never a reason to go on in the clear. `TOON_SMTP_TLS=none` exists for ZAD's relay, which offers no TLS on its submission port today, so mail and login cross the cluster unprotected; it is accepted until the relay offers it (TDR-0018), and production logs a WARNING at startup when it runs that way. The password is never logged, and a configuration error never echoes it | implemented: `notifications/mail.py`, `config.py`, `main.py`, `backend/tests/test_notifications.py`, `backend/tests/test_config.py`, `backend/tests/test_app_integration.py`. See `docs/deploying-on-zad.md` |
| A flood of feedback turning into a flood of mail: email is batched per site over a window (10 minutes by default), and the daily cap (`TOON_FEEDBACK_EMAIL_DAILY_MAX`, 2000) is counted in the database before an email goes out, so every replica counts against it and the project's budget at ZAD is not spent by one site. At the cap the waiting emails move to the next day with one WARNING, and Mattermost goes on | implemented: `notifications/job.py`, `models/notifications.py`, `backend/tests/test_notifications_job.py` |
| A member changing a notification setting beyond their role: an email choice is the member's own, on a site where they may read its feedback, on a group with any role, on their profile always; a webhook is set by an effective site `admin` or a group `admin`. Each change is audited (`feedback_email`, `group_feedback_webhook`, `site_feedback_webhook`) and a refusal is audited like any other (design.md §16.16, `docs/audit-log.md`) | implemented: `api/admin/notifications.py`, `audit/vocabulary.py`, `backend/tests/test_notifications_api.py`, `backend/tests/test_audit_vocabulary.py` |

### What site feedback adds, and what is accepted

This is the explicit risk assessment BIO2 asks for a security-relevant
change, as for WebAssembly above.

What it adds. Until now the platform ran no script of its own in a site's
pages, and its only POST on the content host was the code of a secret link.
Site feedback adds both:

- **Toon's code in the site's pages.** On a site that collects feedback the
  widget runs in every HTML page of a feedback route, in the site's realm,
  beside the site's own scripts. A fault in it is a fault on all those pages,
  and whatever reaches the widget's build reaches them too. What bounds that:
  sites start with feedback off and on previews only, the bundle comes from
  Toon's own origin at an address bound to its hash, it keeps out of the
  site's storage, cookies, globals and custom elements, and its one
  dependency, `html-to-image`, is pinned to an exact version in the lockfile
  and scanned with the rest of the frontend (see "Supply chain").
  The editor of the comment and reply fields is Toon's code as well, but it
  runs in a sandboxed frame of its own (design.md §16.15).
  Its dependencies, `@nldd/design-system` at exactly 0.24.0 and the
  CodeMirror and Lit packages that come with it, are locked in the lockfile
  and scanned the same way.
- **An API that takes input from whoever may see a page.** On a public site
  whose admin allows feedback without an account that is anyone. What bounds
  it: a token per page view that is checked again on every call, the
  validation, the quotas, the counters and the rate-limit class.
- **Session-borne actions and CORS on the content origin.** Consent, ending
  consent and the widget API with a session in its token, and
  `Access-Control-Allow-Origin: *` on that API. What bounds it: no cookie is
  the credential of any of them (see "Why the content cookies are
  SameSite=None"), the consent page cannot be reached into by any site and
  takes no single press as consent, giving consent sets no cookie, and the
  API allows no credentials.
- **Text and screenshots from outside the site team**, in the admin, in the
  CLI and through it in an AI assistant. What bounds it: text-only rendering,
  the image checks, the sandbox policy on the screenshot, the cleaners and
  the `untrusted` envelope of the CLI.

Accepted, each in design.md: a site's own scripts can act for a visitor on
that site's feedback once the visitor consented (§16.5, §16.6), a content
cookie planted from elsewhere for a session that consented already cannot be
told from the visitor's own (§16.6), the scripts can find out whether a
signed-in visitor's write budget on that site is spent (§16.10), the sites
share the platform-wide screenshot cap first come, first served (§16.10), and
without a minimum receive rate a client with enough addresses can keep every
create at a 503 (§16.10). A token reaches no other site's feedback and no
other site's count.

With the widget in the page, accepted as well:

- **Sites without the shielding can read each other's pages, and so their
  tokens.** Accepted by the product owner on 2026-11-17 (TDR-0015). A page of
  one such site can open a page of another in a window, which is a navigation
  and so gets the widget, and read it, its configuration included, as it could
  read that page before, and act through the widget in that window. With the
  token it can send feedback to that site with a name or anonymously, as the
  visitor could, also where only the visitor's session lets them see the page.
  Where the visitor consented to feedback with their account on that site, the
  token carries the consented session too. The other site can then send
  feedback in the visitor's name, and read, reply to and delete the visitor's
  own feedback on that site (design.md §16.5). Every call checks the token
  again, so that ends as soon as the visitor logs out, withdraws the consent
  ("Stop giving feedback with your account"), or their session ends or fails
  revalidation at the IdP, and at the latest when the token expires, twelve
  hours after the page view. What the other site did by then, an item sent or
  one deleted, stays done. A link ticket in the configuration of such a page
  (design.md §16.6) lets whoever signs in with it open the consent page for
  that site while the secret link holds, for the page token's twelve hours at
  most, and nothing more: it opens none of the site's pages. The shielding is
  the boundary between sites and is on by default. A site whose admin turns it
  off has its pages open to every other unshielded site already. A `fetch` or
  a frame gets no token, since only a navigation gets the widget, and frames
  are refused anyway. Revisit when every site gets an origin of its own
  (TDR-0002), or when the account actions move into a window Toon owns, with
  an opaque origin, so the session never enters the site's page.
- **Screenshots of a shielded page lack its images.** Its opaque origin cannot
  read the site's own image files back, so they appear as grey areas and a
  reviewer sees less than the visitor saw. The widget says so before sending.
  Making those images readable would take CORS headers on content, and the
  analysis in "Why the content cookies are SameSite=None" depends on there
  being none.
- **Any page that can get a page token can hide the button on that site.** A
  public site's page hands a token to anyone, and a hide form another site
  posts looks like the widget's own: from a shielded page that is a
  cross-site navigation with `Origin: null` too, so no header check tells
  them apart. The button then stays away on that one site until the browser
  closes, twelve hours at most, or until the visitor follows the show link
  (`/-/feedback/show`), which the widget shows when it hides the button
  itself. The show link clears the cookie on this host alone, at the site's
  path and at `/`. So a mark that an unshielded page writes at another path
  that reaches the site's pages outlasts it, and so does one that a sibling
  `*.rijks.app` host sets with `Domain=rijks.app`, at the site's path or at
  `/` too: until the mark expires, twelve hours at most. A page or host that
  keeps writing fresh marks keeps the button away for as long as it does.
  Either way the button stays away for that visitor on that site, and
  nothing more (design.md §16.7).
- **Any page anywhere may frame the editor.** Its frame carries no
  `frame-ancestors`, since any value would keep it out of shielded sites
  (design.md §16.15). A page that frames it gets an empty editor and nothing
  more: the frame holds no data and no session, can take no action, and
  talks only to the parent that hands it a port.
- **The site's scripts can read what is typed in the editor.** The widget
  runs in the site's realm with an open shadow root, and every change in the
  editor is written into its textarea. The frame keeps the editor apart from
  the site, not what is typed.
- **The editor's frame can navigate itself.** No CSP directive stops that,
  so a compromised build of the editor could carry the text of its field to
  another address. That text is all the frame holds.
- **A link in feedback can lead anywhere.** A comment can carry a link, and
  a giver can be anyone the site lets in. The link shows the address it goes
  to, never a label, so the team reads where it leads before following it,
  and `nofollow ugc` tells search engines it is not the site's own. A
  look-alike that is pure ASCII (`rnicrosoft.com`), or a long host that
  pushes the real domain out of sight, still shows as typed. The renderer
  cannot tell a deceptive address from an honest one. It refuses what it can
  define: a user name before the host, an invisible or bidi character, and a
  host the browser would show otherwise (design.md §16.14).

What notifications add (design.md §16.16). Data leaves Toon. Visitor text
goes into a Mattermost channel the site or group admin chose and stays there
after the item is deleted or purged in Toon, which the admin is told when
setting the webhook and `docs/privacy.md` says too. An email holds no comment,
so a copy of it holds nothing Toon would still have to delete. Accepted: the
webhook URL is unencrypted in the database, and Toon trusts DNS for a listed
host, so the operator lists only hosts they trust. The notification job
writes no audit rows; sending is in the application log only, as a WARNING
when a notice is given up after five attempts and when the daily cap is
reached, without an address or a URL.

Switching it off. Per site, a site admin turns "Feedback toestaan" (allow
feedback) off on the site's Feedback tab: the next page view has no widget,
and every widget call for the site is refused at once, because each call asks
the site's setting again. What was given stays until it is deleted or
purged. Per visitor, "Knop verbergen" (hide the button) takes it away until
the browser closes, twelve hours at most. On the platform every site starts
with feedback off. Removing the feature is the downgrade of
migration `0011_feedback`, which drops every item, reply and screenshot with
the three settings.

## Ingest and storage

| Item | Status |
|---|---|
| Fail-closed ingest: absolute paths, `..` segments, symlinks/hardlinks, null bytes refused | implemented: `ingest/unpacker/`, `backend/tests/test_unpacker.py` |
| Bomb guard: decompression incrementally against limits before materialisation | implemented: `ingest/unpacker/` (archive headers are not trusted), `backend/tests/test_unpacker.py` |
| Path length: a path in a bundle, and a `basePath`, at most 1024 bytes of the name as it lands on disk (UTF-8, where a byte of a tar name that is not UTF-8 counts as one), so a refusal also names at most that much (and only the first 100 characters of a path over it). Every name a refusal repeats, and every `indexCandidates` entry, is valid UTF-8 with bytes that are not UTF-8 and characters that do not print written as escapes | implemented: `ingest/unpacker/paths.py` (`MAX_PATH_BYTES`), `ingest/unpacker/errors.py` (`_printable`), `backend/tests/test_unpacker.py`, `test_deploy_api.py` |
| Tar headers: both passes over a `.tar.gz` let go of each member before reading the next, so memory does not grow with the number of entries. What is kept for one entry's headers (header blocks, long name or long link up to the path limit, pax records, sparse map) at most 64 KiB and 64 pax records, with at most 8 extension headers in front of the entry. Extended attribute records (`LIBARCHIVE.xattr.*`, `SCHILY.xattr.*`) are passed over unread: they do not count towards the 64 KiB, but do count towards the 64 records. Pax records over a limit are cut off and the entry is refused by name, any other read over it is refused before it is issued. Over the archive, what is handed to tarfile to parse at most 2 KiB per entry `max_archive_entries` allows, so the time spent parsing is bound by the entry limit, and what is passed over at most `max_total`, both held against before a read. The pax global records together at most 8 KiB and 64 records. Sparse members are refused, and so is a size that does not match the data skipped for it (a negative one, one a global record overrides, or a next header behind the data). Data skipped for a member that is not a file counts towards the claimed total. A `ValueError`, `IndexError` or `zlib.error` while reading is an unreadable archive, as is a zip name flagged UTF-8 that is not. The raw name is checked for a NUL byte in the member's own header (name and ustar prefix) and in a GNU long name, whatever extension headers stand in between | implemented: `ingest/unpacker/tar_archive.py` (`MAX_TAR_HEADER_BYTES`, `MAX_TAR_HEADER_RECORDS`, `MAX_TAR_EXTENSION_HEADERS`, `MAX_TAR_GLOBAL_BYTES`, `TAR_HEADER_ROOM_PER_ENTRY`), `backend/tests/test_unpacker.py` (`TestTarfileReadPattern` pins the read pattern of tarfile this relies on) |
| Top-level `_preview`/`_version`/`_encoded` in a dist refused | implemented: `ingest/unpacker/root.py` (`RESERVED_SEGMENTS`), `backend/tests/test_unpacker.py` |
| Atomic rename on the same volume (no EXDEV/copy fallback) | implemented: `ingest/store.py`, `backend/tests/test_store.py` |
| Atomic live swap and preview upsert | implemented: `ingest/service.py` (pointer swap in the same transaction, upsert with `ON CONFLICT`), `backend/tests/test_ingest_service.py` |
| A filling content volume is visible: `GET /-/healthz` lists `storage` when the free space is below `TOON_STORAGE_MIN_FREE_BYTES` plus `TOON_INGEST_MAX_TOTAL`, and `content_root` when, in production, the content root is not a mount point (checked once at startup); an ERROR line at most once per hour for the low space, measured by a periodic task every five minutes. Neither refuses to start | implemented: `platform/health.py`, `ingest/storage_health.py`, `main.py`, `backend/tests/test_storage_health.py` |
| `GET /-/healthz` is public on purpose, on the admin host only: no session, no CSRF, `Cache-Control: no-store`, rate-limited in the content class, a neutral 404 on the content host. It reveals only `ok`, `degraded` or `fail` and the names of the failing checks (`database`, `storage`, `content_root`, `idp_revalidation`): no message, number, path or error code, which stay in the log. That an instance is up and whether its database answers is what any uptime monitor would see anyway; the names tell an outsider little they could use. The database check is one `SELECT 1` with a two-second timeout, so the endpoint cannot be used to hold connections | implemented: `platform/health.py`, `host_separation.py`, `backend/tests/test_health.py`, `backend/tests/test_host_separation.py` |
| `GET /platform/storage` (total, used, free, reserve, largest deploy; no group or site, the platform administrator manages people and groups and does not look into sites): platform administrators only, server-side like every endpoint; anonymous 401, any other member or a deactivated administrator 403 | implemented: `api/admin/dependencies.py` (`PlatformAdmin`), `backend/tests/test_admin_api.py` |

## Rate limiting

| Item | Status |
|---|---|
| Limits per endpoint class (login strict, API mutations moderate, content generous) | implemented: `ratelimit.py`, `backend/tests/test_ratelimit.py` |
| Global backstop limit per class | implemented: `ratelimit.py`, `backend/tests/test_ratelimit.py` |
| Fail-closed on a broken counter | implemented: `ratelimit.py`, `backend/tests/test_ratelimit.py` |
| Trusted proxy configuration mandatory in production mode (startup refused without it) | implemented: `config.py` (`TOON_BEHIND_PROXY` mandatory with `TOON_ENVIRONMENT=productie`; both answers are valid, so unset cannot be one), `net.py`, `backend/tests/test_config.py` |
| Code of a secret link: its own class `code`, ten attempts per quarter of an hour per client IP and on top of that ten per quarter of an hour per selector | implemented: `ratelimit.py` (`RateLimitClass.CODE`), `serving/code_page.py`, `config.py` (`TOON_RATELIMIT_CODE_*`), `backend/tests/test_code_page.py`, `backend/tests/test_ratelimit.py` |
| 429 with `Retry-After`, no escalating penalties | implemented: `ratelimit.py`, `backend/tests/test_ratelimit.py` |
| Site feedback: its own class `feedback` for every path under `/-/feedback/`, 300 requests per minute per key with a backstop of 10000, so feedback neither spends nor shares the budget of page views, and in the widget API write counters per author and per site: authors count attempts, a site counts accepted writes, reserved before the body is read so concurrent writes cannot pass its budget (design.md §10, §16.10) | implemented: `ratelimit.py` (`RateLimitClass.FEEDBACK`), `config.py` (`TOON_RATELIMIT_FEEDBACK_*`), `serving/feedback_api.py`, `backend/tests/test_ratelimit.py`, `backend/tests/test_feedback_api.py`, `backend/tests/test_app_integration.py` |
| Creation budget: per member at most 20 new groups, sites and addresses together per hour, through the admin and the CLI together; every attempt that passes the role check and the input validation counts, a slug collision included, and so does every change of address but sending the slug that is already there. Over budget: 429 `TOO_MANY_CREATIONS` with `Retry-After`, audited as `admin_access`/`refused`. It bounds what a stolen CLI token can litter the platform with, and with the limit of five old slugs how many addresses a member can hold back; the counter lives in process memory, like the other per-member limits | implemented: `api/admin/dependencies.py` (`_require_creation_budget`), `backend/tests/test_cli_creation.py` (`TestCreationBudget`), `backend/tests/test_addresses_api.py` |

## Audit

| Item | Status |
|---|---|
| Append-only audit log (DB triggers refuse UPDATE/DELETE) | implemented |
| Pseudonymised actor (HMAC pepper), truncated IP | implemented: `audit/pseudonymisation.py` (IPv4 /24, IPv6 /48), `backend/tests/test_audit.py` |
| Every refusal and login redirect audited; viewing of non-public content one row per page, kept 180 days | implemented: `serving/router.py`, `backend/tests/test_serving.py`; vocabulary and retention periods in `docs/audit-log.md` |
| Audit failures do not block the action (fail-open on the log, fail-closed on the decision), except the de-anonymisation reads and ending a hold on the audit purge, which answer 503 without their audit row (`docs/audit-log.md`) | implemented: `audit/log.py`, `api/admin/`, `backend/tests/test_audit.py`, `test_audit_purge_hold.py` |
| Logging in, failed callbacks and logging out audited (BIO2 5.17.01) | implemented: `platform/pages.py`, reasons from a closed set in `audit/vocabulary.py`; a failed attempt is always anonymous |
| Refused admin actions audited (BIO2 5.18.01) | implemented: one place, the `ApiError` handler in `api/errors.py`, so also for endpoints still to come. Route template in `refs`, never the path |
| Retention period and purging | implemented: 180 days for viewing (BIO2 8.15.04), 3 years for the rest, enforced by a trigger; purged every night at 04:00 UTC by a job inside the app (`audit/retention_job.py`), by hand with `just purge-audit-log`, on the same `TOON_DB_URL` as the app. A platform administrator holds both during an incident (BIO2 5.28.01), through the API and without a restart. While the hold is open the delete triggers refuse every delete (`audit_purge_holds`, migration `0010_audit_purge_hold`) |
| Content-only SSO viewers traceable (`content_viewers`) | implemented: sso_subject, email address (may be `null`) with whether it was a verified claim, and last login updated on every content host login, kept 180 days by the same purge, with its own BEFORE DELETE trigger that enforces it; `auth/content_viewers.py`, `api/admin/platform/audit.py` (`actor-pseudonym`/`actor-identity`), `backend/tests/test_content_viewers.py`, `backend/tests/test_migrations.py` |
| Only a verified email address matches in the forward lookup | implemented: an unverified email claim of a content viewer does not count for `actor-pseudonym`; the SSO subject always matches, verified or not; `api/admin/`, `backend/tests/test_audit_api.py` |
| Ambiguous identifier (email address without a unique constraint) recognised | implemented: an exact SSO subject always wins; if an email address matches more than one subject (members among themselves, or a member and a content viewer), the answer is 409 `IDENTIFIER_AMBIGUOUS` instead of an arbitrary first hit - this holds for the group and site member endpoints too, not just the audit lookup. The group and site member endpoints also resolve an identifier on an exact, exactly-one-active-member name (`_find_member_by_identifier`); the audit lookup (`_resolve_lookup_identifier`) deliberately does not, because a name is not something there that should let you guess someone's pseudonym; `api/admin/`, `backend/tests/test_audit_api.py`, `backend/tests/test_admin_api.py` |
| Mandatory motivation and daily limit on tracing, counted atomically | implemented: `reason` (10-500 characters, no control or formatting characters, no `@`) mandatory and itself audited for `actor-pseudonym`, `actor-identity` and the IP disclosure, also on a 404 or 409; limit per platform administrator counted from the audit log itself (`TOON_AUDIT_LOOKUP_DAILY_LIMIT`, default 25, 1-1000), so it holds up across a restart or multiple replicas. The counting and the writing of its own row happen in one transaction under a Postgres advisory lock on the administrator, so two concurrent requests cannot both see themselves as "still under the limit" (TOCTOU); `audit/log.py` (`write_strict_limited`), `backend/tests/test_audit.py` (concurrency test), `backend/tests/test_audit_api.py` |
| Platform administrator check as a FastAPI dependency on the audit log endpoints | implemented: runs before the body is validated, so a non-administrator always gets a 403 (and is audited), never a 422 that leaves the refusal unaudited; `api/admin/dependencies.py` (`require_platform_admin`), `backend/tests/test_audit_api.py` |
| Full IP address encrypted separately (`ip_encrypted`), own key, bound to the row | implemented: AES-256-GCM under `TOON_AUDIT_IP_KEY` (32 bytes, separate from `TOON_AUDIT_PEPPER` and `TOON_SESSION_SECRET`); the AAD contains the row id, so a ciphertext copied to another row does not decrypt there; decryptable only via `POST /platform/audit/entries/{id}/ip`, never via `GET /platform/audit`, and that disclosure itself also counts towards the daily limit; `audit/ip_crypto.py`, `backend/tests/test_ip_crypto.py`, `backend/tests/test_audit.py`, `backend/tests/test_audit_ip.py` |
| Key rotation for `TOON_AUDIT_IP_KEY` | implemented: every encrypted value carries a key id (derived from the key, not a secret). `TOON_AUDIT_IP_KEY_PREVIOUS` takes a comma-separated list of the keys it replaced, newest first, and a row is decrypted under the key whose id its value carries. A previous key stays in the list until every row written under it is purged, at most 1096 days after the rotation, provided the purge runs. Without that key, rows from before the rotation become unreadable, while `ip_truncated` and the row itself are preserved. Procedure: `docs/audit-log.md` ("Rotating the pepper and the IP key"). `config.py`, `audit/ip_crypto.py`, `backend/tests/test_audit_ip.py` |
| Pepper rotation for `TOON_AUDIT_PEPPER` | implemented: `TOON_AUDIT_PEPPER_PREVIOUS` takes a comma-separated list of the peppers it replaced, newest first. New rows are always pseudonymised under the current pepper. `actor-identity` finds the actor behind a pseudonym made under the current pepper or any previous one, and `actor-pseudonym` also returns the pseudonyms under the previous peppers (`previousPseudonyms`), to search the rows from before the rotation. The app refuses to start when a previous pepper or IP key is invalid or equals the session secret or another audit pepper or IP key, and its startup log counts the previous ones without logging a value or a fingerprint. A previous pepper stays in the list until every row written under it is purged, at most 1096 days after the rotation, provided the purge runs. Without it, older pseudonyms can no longer be traced to a person. Rotating either value limits the damage from that moment on and repairs nothing. Older rows are immutable and keep the old value, so a leaked value plus a copy of the database still opens them, and a leak is a possible personal data breach. The daily limit on lookups counts an administrator's rows under every configured pepper, so a rotation does not reset it. Procedure: `docs/audit-log.md` ("Rotating the pepper and the IP key"). `config.py`, `main.py`, `api/admin/`, `audit/log.py`, `backend/tests/test_config.py`, `backend/tests/test_audit_api.py`, `backend/tests/test_audit.py`, `backend/tests/test_app_integration.py` |
| Chain head published outside the database | implemented: the app every night right before the purge, and `just publish-audit-head` by hand (`audit/checkpoint.py`), writes both ends of every chain, with their hashes and retention deadlines, to the application log, `just verify-audit-head <file>` holds a line published earlier against the database. A shipped line cannot be retracted, so what the walk itself cannot catch - a rewrite with the chain and its head recomputed, rows removed from the newest end with the head moved back, a row gone before its published retention deadline - shows up against it. Which check catches what is tabled in the audit log doc; the walk alone is not enough. It prevents nothing and proves nothing if nobody kept a line; `backend/tests/test_audit_checkpoint.py`, `docs/audit-log.md` |
| An IP address we cannot vouch for is marked as such | implemented: `refs.ip_unvouched` is `true` on a row that arrived with `TOON_BEHIND_PROXY` set and no usable `X-Forwarded-For` entry behind it; the row then carries the direct peer, which is a proxy rather than a visitor. The address is stored unchanged and nothing is refused over it; `net.py` (`ClientAddress`), `audit/log.py`, `backend/tests/test_net.py`, `backend/tests/test_audit.py` |
| Database errors do not log bind parameters | implemented: `hide_parameters=True` on the SQLAlchemy engine of the app and of the purge job, so that a failed statement never puts a sub, email address or `reason` in the log; `db.py`, `audit/retention.py` |

## Database

| Item | Status |
|---|---|
| One shared PostgreSQL enum type `access_base`, a test guards the sync with the application constant | implemented |
| Access is a base plus two exceptions, not a single level | implemented: `sites.access_base` / `access_keys` / `access_invitees` (and the same three as group default and as preview override), `constants.py` (`AccessBase`, `AccessPolicy`), `access/gate.py`, `backend/tests/test_access_gate.py` (every base times every combination of the exceptions) |
| DB account separation (migration, runtime and purge account separate) | not done: the shared PostgreSQL service on ZAD hands out exactly one user, so the separation cannot exist in production. Local mirrors production: one account for app, alembic and purge job |
| Append-only audit log without account separation | the guarantee is the triggers from `0001_base`: `audit_log_no_update`, `audit_log_delete_after_retention`, `content_viewer_delete_after_retention`, `audit_log_chain_head_guard` and a `TRUNCATE` guard on each of those tables, proven by `backend/tests/test_migrations.py`. They apply to every session on that account, so to the app itself too. What is lost: the account is also schema owner and can disable the triggers, rewrite the functions, and then `TRUNCATE` or drop the table; see `docs/audit-log.md` |
| Audit rows authentic, not only immutable | implemented: `audit_log_chain` (a BEFORE INSERT trigger in `0001_base`) stamps `occurred_at` itself, so a caller cannot pick the moment its row claims, and hashes every row over its predecessor's hash plus its own length-prefixed content. Removing, rewriting or back-dating a row breaks the chain from there on. Sixteen chains per retention period instead of one, keyed on the period and the row id, so the per-insert lock does not serialise every audit write in the application and the purge only ever takes the oldest end of a chain. `just verify-audit-log` (`toon/audit/chain.py`) walks the chains and names the first break in each; `backend/tests/test_audit_chain.py`. Every row carries its predecessor's hash, so the oldest surviving row is checked too, and each chain's newest row is held against the head registered in `audit_log_chain_heads`, so rows removed from the newest end are caught as well. What is not covered: the schema owner can disable the triggers and recompute the whole chain and its head, and rows dropped off the front of a chain leave nothing pointing at them. Only an off-host append-only sink (WORM or SIEM) closes that, and that decision is open; see `docs/audit-log.md` |

## Supply chain

| Item | Status |
|---|---|
| Base images pinned on digest | done: every `FROM` in `containers/toon/Containerfile` carries a `@sha256:` digest next to its tag (node 22-bookworm-slim, uv 0.9 on python 3.12-bookworm-slim, python 3.12-slim-bookworm), and Dependabot's docker ecosystem bumps tag and digest together. `containers/nginx-dev/Containerfile` is only the dev proxy and falls outside this |
| CI actions pinned on commit SHA | done: every `uses:` in `ci.yml` and `deploy.yml` sits on a commit SHA, fixed by `test_workflows.py`. Handled on the user side: `docs/publishing.md` pins every `uses:` on a commit SHA, and the one nested `uses:` in `actions/publish/action.yml` (`astral-sh/setup-uv`) sits on a commit SHA too, fixed by `cli/tests/test_cli.py` |
| Lockfiles (uv.lock, package-lock.json) | implemented |
| Vendored files pinned on hash | done since 2026-09-28: `backend/src/toon/static/docs/SHA256SUMS` records the sha256 of the two Swagger UI files, `just refresh-swagger-ui` checks its download against it, and `test_api_documentation.py` checks the shipped bytes without a network call. Before this the recipe printed a truncated hash and compared it to nothing, which read as verification. It hid a real change: a repo-wide rename of `project` to `site` had edited a URI-scheme list inside the minified bundle. Both files were restored from the npm registry tarball of the pinned version. Weight: 1.5 MB of minified JavaScript, served from the admin origin under `script-src 'self'`, so fully trusted script beside the session cookie, arriving in git as a diff nobody reads |
| Vulnerability scan on dependencies | done: the CI job `vulnerabilities` runs pip-audit on the exported lockfile and npm audit on the frontend, locally via `just scan`. Accepted findings sit in `.trivyignore.yaml` with a date and a motivation and come back by themselves on that date |
| Provenance of the design system | done: the CI job `frontend` runs `npm audit signatures` after `npm ci`, which fails on a signature or attestation that does not verify. A package without provenance passes npm, so `scripts/provenance.py` also requires SLSA provenance for `@nldd/design-system` from its own repository and `publish-npm.yml`: a version published with a stolen npm token has none |
| SBOM per image | done: `deploy.yml` generates a CycloneDX SBOM with trivy and keeps it 90 days as an artefact |
| Build provenance and SBOM attestation | done: `deploy.yml` attests the pushed image digest with `actions/attest`, once as SLSA build provenance and once with the CycloneDX SBOM, and pushes both to the registry. That happens in the job `provenance`, not in `build`: see "Where `id-token: write` may sit" below. `test_workflows.py` fixes the wiring. Verify an image built since the rename with `gh attestation verify oci://<image> -R DigiGilde/toon`. On the Free plan this only works while the repository is public, which it is since 2026-09-27; going private again would take it away |
| Updating dependencies | done: `.github/dependabot.yml` follows github-actions, uv, npm (frontend and e2e) and docker (both Containerfiles), weekly and grouped. A dependabot PR gets no preview environment but does go through the test gate |
| Production deploy behind the tests | done: `ci.yml` has become `workflow_call` and `deploy.yml` calls it; the job `production` hangs on `needs: [ci, build]`. Open: branch protection on `beta` with the eight `ci /` checks and the three `CodeQL /` checks as required, see the checklist below |
| Static analysis in CI | done: `codeql.yml` runs CodeQL over `actions`, `javascript-typescript` and `python`, each with `build-mode: none`, on every pull request, on a push to `beta` and weekly. Its own workflow and not a job in `ci.yml`, because a called workflow carries no schedule; `security-events: write` sits on the analyse job alone. Findings land in the security tab; the analyse job itself only goes red on an analysis that breaks. What can go red on a pull request is the separate `Code scanning results / CodeQL` check GitHub adds, on newly introduced alerts above the threshold in Settings |
| Image scan in CI | done: `deploy.yml` runs trivy twice on the built image, first a full report in the log and then the gate on CRITICAL and HIGH with `ignore-unfixed`. A red scan fails `build`, so nothing gets deployed |
| `security.txt` (RFC 9116) under `/.well-known/` | done: both hosts serve the same document from `platform/security_txt.py`, with a `Canonical` per https origin and an `Expires` that is set 90 days ahead per request. `test_security_txt.py` runs it through `sectxt`. The GitHub advisory form is the first `Contact` and `SECURITY.md` the first `Policy`, both reachable since the repository went public with private vulnerability reporting on. The second `Contact` is `mailto:` the address in `TOON_OPERATOR_EMAIL`, so reports about an installation reach whoever runs it, and without the setting that line is left out. NCSC-NL's CVD routes (preamble, `Policy` and `Contact`) are listed only when the installation enables them with `TOON_SECURITY_TXT_NCSC=true`, as the Digi Gilde's installation inside the central government does. No `Encryption` field: it cannot be tied to one `Contact`, and NCSC-NL's key would read as the Toon team's (`SECURITY.md`) |

## Deviations we explain

Where Toon departs from a standard the Dutch government applies, it says so
here, with the reason.

- **Refusals are neutral 404s, not 401 or 403.** The API Design Rules ask
  for 401 and 403 on a refused request. On the content host a refusal is the
  same 404, byte for byte, as a page that does not exist, so that nobody can
  find out which sites exist or what their access level is. That is a
  security requirement of Toon (see "Access to content").
- **OIDC with a client secret, not `private_key_jwt`.** The NL GOV OIDC
  profile asks for `private_key_jwt` or mTLS. The ZAD Keycloak only creates
  clients with a client secret, so production runs
  `TOON_OIDC_CLIENT_AUTH=client_secret_post` (`docs/deploying-on-zad.md`).
  `private_key_jwt` stays the default and is what the dev stack uses (see
  "Authentication and session").
- **The SSO access level is "whoever the ZAD Keycloak realm lets in".** It
  is not an assurance level. The ZAD Keycloak only supplies `acr` `0` or
  `1`, which is not an eIDAS level (low, substantial, high), so
  `TOON_OIDC_REQUIRED_ACR` stays empty in production. Toon also does not
  check which upstream identity provider the login came from. It trusts
  the realm. Read "SSO Rijk" in the interface as "a login the ZAD realm
  accepts".

## Production prerequisites (organisational)

| Item | Status |
|---|---|
| DPIA | planned, prerequisite for production go-live; `docs/privacy.md` is the draft starting point |
| Pentest | planned, prerequisite for production go-live |

### Account separation can come back on a database of our own

Toon runs on the *shared* PostgreSQL service of ZAD, which gives a project
exactly one user; there is no way to ask for a second one. The separate service
`namespace-postgresql-database` does offer `CREATEROLE` and `postInitSQL`. If
Toon moves to that, the separation (migration, runtime and purge account) can
come back, with `postInitSQL` as the place for the roles and their rights. Open
point, not a prerequisite.

## Pre-production / open points

### The platform's shared domains are not on the Public Suffix List

Checked on 2026-09-29: none of the four ZAD domain families (`rijks.app`,
`rijksapps.nl`, `rijksapp.nl`, `rijksapp.dev`, see `docs/deploying-on-zad.md`
§3) appear in the Public Suffix List. Toon's own base domain today is
`rijks.app` (`beheer.toon.rijks.app`, `toon.rijks.app`).

Because the base domain is not on the list, a browser treats it as an
ordinary registrable domain rather than as a suffix under which unrelated
sites are isolated from each other. JavaScript running on the content host
could therefore set cookies scoped to `Domain=toon.rijks.app` (reaching the
admin host, `beheer.toon.rijks.app`) and to `Domain=rijks.app` (reaching
every other application on `*.rijks.app`, ZAD-hosted or not).

That exposure is not the default, though. Every site has its own shielding
switch, "Afschermen van andere sites" (`Site.sandbox`, `serving/response.py`,
`serving/router.py` ~274-285), **on by default** for a site regardless of
whether it is public or restricted (`Site.sandbox` is read on its own; access
level plays no part in it). With it on, the page is served under
`sandbox="allow-scripts allow-forms allow-popups"` and deliberately without
`allow-same-origin`, which gives the page an opaque origin; a sandboxed
document with an opaque origin cannot read or write `document.cookie` or use
`cookieStore` at all (throws in Chromium, Firefox and WebKit). So a script on
a shielded site cannot reach cookies in the first place, whether its own,
the admin host's or another `*.rijks.app` application's. The exposure above
is real only for a site whose own administrator switched shielding off (or
any third-party or DOM-XSS script that runs inside such a site).

The admin session cookie is `__Host-` prefixed (see the cookie row above), so
even on an unshielded site this cannot fix or overwrite that cookie itself:
the `__Host-` prefix forbids a `Domain` attribute and requires an exact host
match, which is exactly why the admin session uses it. What a cookie bomb can
still do is push the total cookie volume sent to `*.rijks.app` past the
browser's per-domain limits, which makes the admin host unusable for that
browser until its cookies for the domain are cleared - a denial of service,
not a session takeover, and one the admin host cannot defend itself against:
an oversized `Cookie` header is refused by the router or by uvicorn before
Toon's own code ever runs, and `Clear-Site-Data: "cookies"` would clear the
whole registrable domain, every `*.rijks.app` application along with it, not
only Toon's own cookies. Other applications on `*.rijks.app` that use an
ordinary (non-`__Host-`) session cookie do not have the `__Host-` protection
either and are exposed to cookie tossing: a `Domain=rijks.app` cookie that a
more specific path match lets substitute for theirs. Site feedback adds a
consequence on Toon's own content host: a planted site cookie can carry a
session that consented to feedback, so that a visitor's feedback goes in under
the planter's name (see "What site feedback adds, and what is accepted").

Status: open. Options under evaluation: making the per-site shielding
mandatory rather than a switch a site administrator can turn off, a separate
registrable domain for content that no other application shares (ideally one
that is itself on the Public Suffix List), and getting `rijks.app` onto the
Public Suffix List. No decision made yet.

### Pull request previews

A pull request with the label `preview` runs as deployment `pr-<n>` in a ZAD
project of its own (TDR-0012, `docs/deploying-on-zad.md` §8), and `beta`
runs as the standing deployment `beta` beside them (TDR-0016). That is pull
request code on a public host, so `TOON_ENVIRONMENT=preview` holds it to
every production requirement (`TOON_BEHIND_PROXY`,
`TOON_OIDC_ISS_REQUIRED`, an https issuer on a real host, a base URL) and adds three
behaviours that only exist there:

- **Demo data.** The entrypoint runs the dev seed when the database is empty
  (`/app/demo_seed.py`). It never runs on a database that holds a group or a
  member, so a restart or a new push wipes nothing. The seed refuses any
  environment but `dev` and `preview`. Only the `preview` build target of
  `containers/toon/Containerfile` carries it, which only pull request builds
  and the image for the deployment `beta` take: the production image has no
  seed, and run as a preview it stops before it serves.
- **An editor role on first sign-in.** A member created by signing in to a
  preview becomes an editor of the demo group `rijksoverheid`
  (`auth/members.py`). An existing member gets nothing new.
- **Only a platform administrator turns shielding off.** Anyone else, a site
  admin included, is refused with 409 `SANDBOX_REQUIRED`; a platform
  administrator still needs the site admin role first. The content host sits
  on `*.rig.prd1.gn2.quattro.rijksapps.nl`, shared with every ZAD tenant and
  the ZAD portal, and `rijksapps.nl` is not on the Public Suffix List
  (above): an unshielded page there is same-site with all of them and could
  set cookies for the shared parent. The platform administrator may still
  turn it off, to test an issue that needs it.

  Residual risk: once a platform administrator has turned shielding off on a
  site, that site's editors can publish scripts that run unshielded on a
  host under the shared parent domain, and in a preview anyone with SSO Rijk
  can become an editor. So turn it off only on a site that only trusted
  people edit, not on the demo group's sites.

Accepted risk: everyone who can sign in with SSO Rijk can use a preview,
with pull request code, and edit its demo sites. There is no allowlist and
the access gate does not change for it. Their name and e-mail address land in
that preview's database until the preview is torn down with it. A preview
holds demo data only, and its secrets, database, volume, Keycloak client and
API key are its own: nothing in it reaches production, and
`rijksapps.nl` is not production's registrable domain (`rijks.app`), so a
preview cannot set cookies production accepts.

The jobs that hold the preview project's key never check out or run pull
request code; they run in the context of `beta` and name an image that
"Test and deploy" built and scanned, by digest, only once that run passed on
the pull request's head, and only when the digest carries the provenance
that `deploy.yml` attested in a run for that same pull request
(`refs/pull/<n>/merge`). A pull request from a fork never gets a preview.

### Proving trusted publishing end to end, deliberately postponed

Everything about CI trusted publishing is verified by reading the code and by
tests with tokens we mint ourselves. A workflow could prove the real chain:
bring up the e2e stack, seed a `site_repositories` row for this repository,
request a genuine GitHub OIDC token with that stack's base URL as the audience,
run `actions/publish`, and fetch the published page back.

Decided on 2026-09-27 not to build it yet, and the reason is the permission
rather than the work. A job with `id-token: write` can request a token for any
audience it likes, not only the throwaway stack. That token carries this
repository's real numeric id, so any Toon installation holding a
`site_repositories` row for this repository would accept it. Today that is no
installation, so the blast radius is empty. It stops being empty the day Toon
publishes its own site or docs through Toon, which is the obvious first use.

So: build it when something trusts this repository, not before. Until then the
permission would exist for a proof about a chain nobody uses. When it is built,
it belongs in a workflow of its own, manual and scheduled rather than a gate
(a fork or a Dependabot run gets no OIDC token, so it could only ever go red
after a merge), in a job of its own that runs no project code. Not the `e2e`
job: that one runs `npm ci` twice and a `uv sync`, which would put the token
within reach of the whole dependency tree. The rule that follows from this is
in the next section.

The negative cases stay where they are, in `backend/tests/test_deploy_api.py`:
GitHub will not issue a token for a repository you do not own, so a wrong
repository, a wrong audience or a wrong event cannot be produced for real.

### Where `id-token: write` may sit

The rule: a job that holds `id-token: write` runs no code of this project and
no code of its dependencies. The permission is not scoped to one audience, so
a job that has it can mint a token for any audience it names, and that token
carries this repository's numeric id. Whoever gets to run in such a job can
therefore speak as this repository to anything that trusts it, this project's
own CI trust rules included.

Today `deploy.yml` is the only file with the permission, in the job `provenance`,
which does four things: log in to ghcr, download the SBOM artefact, and run
`actions/attest` twice. `build` builds the image, so it executes the
Containerfile and with it `npm ci` and `uv sync`; it keeps `contents: read` and
`packages: write` and nothing more. The digest passes from `build` to `provenance`
as a job output, so the split cannot make the two disagree about which image
was attested. `scripts/tests/test_workflows.py` holds both halves of that in
place.

What this is worth today is a fair question: nothing deploys to production and
no Toon installation trusts this repository, so the blast radius is empty. It
is separated now because it is cheap now (one job, one artefact that was
already being uploaded) and because the alternative is remembering to do it on
the day the radius stops being empty.

### Now the repo is on GitHub

The remote is `https://github.com/DigiGilde/toon` (called `plak` until the
rename), first pushed on 2026-09-27 and public since the same day, default
branch `beta`. Work through what is
left. Everything still unticked below is a repository setting, so it is a click
in Settings and not a change in this repository.

- [x] **The first run of `ci.yml` and `deploy.yml`.** `ci.yml` is green on
  `beta` with all eight jobs (`backend`, `cli`, `frontend`, `e2e`,
  `pre-commit`, `secret-scan`, `containers`, `vulnerabilities`). `deploy.yml`
  builds and pushes the image, but has never completed a deploy: `productie`
  hangs on a push to `main` and there is no environment to deploy to yet.
- [x] **Dependabot has delivered its first round** for every ecosystem
  (github-actions, uv for `backend` and `cli`, npm for `frontend` and `e2e`,
  pre-commit, docker), and a dependabot pull request gets the test gate but no
  preview environment, as intended.
- [x] **Secret scanning and push protection.** GitHub switched both on itself
  when the repository became public.
- [x] **Private vulnerability reporting** is on, so
  `/security/advisories/new` no longer answers a 404.
- [x] **Go through the first CodeQL run.** Fifteen alerts on 2026-09-27:
  eleven Python and four TypeScript, none in `actions`. One was real
  (`js/bad-tag-filter`: the build-output test matched `</script>` without
  allowing a space or attributes, so the CSP guard failed open) and is fixed;
  fourteen were dismissed, each with the mechanism and a test named in the
  dismissal comment. The query set stays the default one; a finding is
  answered or dismissed with a reason, never made quiet by narrowing the
  queries.

  Re-audited adversarially on 2026-09-28, with the brief of proving the
  dismissals wrong rather than confirming them: payloads were run through the
  real validators, not reasoned about. All fourteen hold. Three of them rest
  on the same blind spot, worth naming because it will recur: `filename in
  _ASSETS`, `lang in i18n.SUPPORTED` and `base in _CATALOGUES` are
  allowlists-by-membership, the strongest validation there is (nothing is left
  to escape), and CodeQL counts a comparison as a barrier only against a
  literal (`x == "nl"`, `x in ("nl", "en")`), not against a named table. A
  codebase that escapes gets green; one that uses allowlists gets red. Do not
  let the alert count steer the design. What does both is handing on the
  table's own value instead of the checked input, which is what the docs
  assets and `?lang=` now do (see below).

  That re-audit found four issues CodeQL never raised, all of them outside its
  language model (a `justfile`, shell, file writing, a URL parse): the
  unverified Swagger UI download (see "Supply chain" above), and three
  credential paths in the CLI, closed in `b6edbac` -- userinfo accepted in
  `--host` and then printed by every message naming the host, a token with a
  newline able to inject `PLAK_HOST` into `.env.plak`, and the `::add-mask::`
  line being written when stdout is a plain file the runner never reads.
  The CLI's stdout must not be captured in CI; see `docs/publishing.md` §3.

  Re-reviewed on 2026-09-30 with the brief of fixing rather than dismissing,
  with CodeQL 2.27.1 run locally on the default suites (it reproduces the
  security tab alert for alert). Twenty alerts stood dismissed by then: the
  fourteen, #16 (the `::add-mask::` line, first flagged once `b6edbac` moved
  it into a function whose parameter is called `secret`), #17 and #18 (#2 and
  #3 again, under a new fingerprint after the regex fix in `d6733b6`), and
  #24 to #26 from `plak group create` and `plak site create`. Thirteen no
  longer fire because the code changed:

  - `py/clear-text-logging-sensitive-data` on the CLI host, #9 to #14 and #24
    to #26. The dismissals blamed the `.env.plak` dict, but the source was the
    function name `_trusted_stored_host`: CodeQL's sensitive-data heuristic
    reads any name containing "trusted" as a secret. Renamed to
    `_stored_host_if_safe`, with a test that a host from the stored session is
    printed while its tokens never reach stdout or stderr.
  - `py/path-injection` #6 and #7: `_ASSETS` holds each asset's path, so the
    route's `filename` only selects an entry and is never joined onto a path.
  - `py/reflective-xss` #5: `?lang=` picks one of `i18n.SUPPORTED` and the
    page renders that constant, never the query value.
  - `js/incomplete-url-substring-sanitization` #4: the mock compares the
    Forgejo host with an explicit `===`; `Array.includes` on a value that may
    hold `https://github.com` read as a substring check.

  Five stay dismissed, where the query is right about the pattern, wrong about
  the risk, and the only change that would silence it makes the code worse:

  - #16, `::add-mask::` in `_mask_in_ci_log`: writing the token to stdout is
    how the runner learns to mask it; there is no other channel.
  - #15, `dev/seed.py` prints the secret-link key it just created: a dev
    fixture on the developer's own terminal, kept out of the image by
    `.dockerignore`.
  - #8, `py/url-redirection` in `_cookie_for_next_site`: `target` has passed
    `valid_return_to`. CodeQL accepts only a literal comparison or the value on
    the right of a `+` here, and `"/" + value[1:]` would silence it without
    checking anything.
  - #17 and #18, `strippable()` in `dutch-literals.test.ts`: a `<!--` or
    `<style` left after one pass makes the scanner read more and fail loudly;
    stripping to a fixpoint, which is what the query wants, makes it read less.

  #2 and #3 stay dismissed as the old fingerprints of #17 and #18; they match
  no code any more.
- [x] **Dependabot security updates** (`dependabot_security_updates`,
  Settings, Code security) are on, checked on 2026-09-30: an advisory gets
  its pull request at once, outside the weekly rhythm and without the
  cooldown in `.github/dependabot.yml`.
- [x] **The two extra secret-scanning options stay off**, on purpose
  (`secret_scanning_non_provider_patterns` and
  `secret_scanning_validity_checks`, Settings, Code security), checked on
  2026-11-14. The first would catch keys of providers GitHub has no partner
  pattern for, the second would say whether a leaked token is still live.
  gitleaks with its default rules scans for secrets locally and in CI
  (`secret-scan`, `.gitleaks.toml`).
- [x] **Branch protection on `beta`**, which is the default branch, is on
  (checked on 2026-10-03) as the ruleset in `.github/rulesets/beta.json`,
  with the required checks `ci / backend-tests`, `ci / backend-coverage`,
  `ci / release-script`, `ci / cli`, `ci / cli-python`, `ci / cli-windows`,
  `ci / frontend`, `ci / e2e`, `ci / pre-commit`, `ci / secret-scan`,
  `ci / containers`, `ci / vulnerabilities`, `api-contract`, `changelog`
  and the CodeQL checks `Analyse (actions)`,
  `Analyse (javascript-typescript)` and `Analyse (python)`, enforced for
  administrators too, so the test gate in `deploy.yml` is also closed for
  a direct push. Signed commits are
  deliberately not required: GitHub cannot sign the commits a rebase merge
  creates.
  The rule moves along to `main` on the day production exists. Merging goes
  through a merge queue (rebase), which runs the same required checks on the
  queued commit; that is why `deploy.yml` and `codeql.yml` also trigger on
  `merge_group`, and why `build` skips it.
- [x] **`security.txt` is complete.**
  `Contact: https://github.com/DigiGilde/toon/security/advisories/new` leads the
  `Contact` lines and `Policy: https://github.com/DigiGilde/toon/blob/beta/SECURITY.md`
  the `Policy` lines, in `backend/src/toon/platform/security_txt.py`;
  `SECURITY.md` names the same two routes. Going private again would make both
  answer a 404, so they move along with the visibility.

What still has to be filled in or built cluster-specifically before this can go
to production. The deploy itself is a declarative ZAD project file plus
zad-actions, no Kubernetes manifests.

**Content origin login (spec §4a) - implemented.** Protected content on
the content host redirects anonymously
to `/-/login?returnTo=<path>` on the same host; the segment `-` can
never be a slug, so `/-/` is the platform namespace there. The flow is
the same OIDC client as the admin login (PKCE, state, nonce, iss check,
`__Host-` login cookie), with redirect URI `{TOON_CONTENT_BASE_URL}/-/oauth2/callback`;
that second redirect URI has to be registered at the IdP alongside
`{TOON_BASE_URL}/-/oauth2/callback`. The outcome is a content session
(`__Secure-toon-content`, HttpOnly, Secure, SameSite=None,
`Path=/{group}/{site}/`, 12 hours, no CSRF cookie, with an anchor under `/-/`,
see the cookie row above) without admin
authority: `session_from_request` (admin and API) accepts kind `admin` only, content serving kind `content` only,
and a login attempt carries its kind so that a callback never redeems an
attempt of the other flow. A content login creates no member record (spec §7
"Looking"). On the content host, host separation allows only `/-/login` and
`/-/oauth2/callback` from the `/-/` namespace, and everything under
`/-/feedback/` (design.md §4a). Everything else there, the API
first of all, is the neutral 404. On the admin host the same spelling exists
for the admin flow; the host decides which of the two you get. The two E2E
scenarios that were waiting on this (group member via `_version`, invitee with
a deep link) now run without a skip.

**The root of the content host redirects to the admin landing page.**
Without it, `/` would be the neutral 404 there and anyone who heard the name of
the service and typed the address would hit a dead end. That root answers with
a 302 to the root of the admin host (`platform/pages.py`,
`front_door_response`), whose public landing page says what Toon is and
carries the footer with Over Toon, Toegankelijkheid, Privacy and the API
documentation. The redirect carries **no authority whatsoever**, because this
is the origin on which uploaded sites run their own JavaScript (see
`api/origin_guard.py`): no body to speak of, no cookie, no session reading.
The target is the admin origin from `TOON_BASE_URL`, never the `Host` header,
and the only thing from the request that travels along is `?lang=nl` or
`?lang=en`, matched against our own codes rather than echoed. Without that
setting the root stays the neutral 404, because then there is no address to
point to. The rest of the content host does not change: a refusal stays the
byte-identical neutral 404, and the redirect betrays nothing, because it
always exists and sits at a fixed address. The admin host keeps
`Disallow: /` in its `robots.txt`, so the landing page is not meant to be
indexed. Tests: `backend/tests/test_front_door.py`.

**Secret link: redeem and redirect.** A valid `?key=`
is redeemed: the app sets the `__Secure-toon-key` cookie and answers with
a 302 (`Cache-Control: no-store`) to the same URL without the
`key` parameter (other query parameters stay); the cookie route serves
after that. The cookie carries the id of the link, signed with
`TOON_SESSION_SECRET`: a bare id is not proof of access, because it appears
nowhere as a secret. An invalid cookie stays the neutral 404; an invalid key in
the query leaves the URL (next paragraph).
The audit record carries the path and the selector of the link, never the
query string, and a stray `key` does not travel along
in `returnTo` either. The access log of uvicorn logs path plus query string and
is therefore off (`--no-access-log` in `containers/toon/Containerfile` and in
`just dev`); the audit log and the app logging stay. A reverse proxy or
ingress router in front of the app that logs request lines (the dev nginx does)
does still see the key; on ZAD that is a question for the platform
administrators (router logging and Loki retention). The redirect on the old
hosts is such a proxy (`docs/deploying-on-zad.md`, §12). Keys
always expire, by default after 90 days and after 365 at most (`expiry.py`). A
rotation of `TOON_SESSION_SECRET` invalidates every existing key cookie:
visitors with such a cookie have to open their link again. Still open:
`X-Robots-Tag: noindex` on key content.

**A secret link never reaches a page.** Redeeming takes a valid key out of the
URL; without more, every other key would stay in it. On an allowed page (a
public site, a visitor with a session) the page's script reads it from
`location.search`. On a refusal it stays in the address bar and in the tab's
history, where a later page on the same host can read it through the
Navigation API. A service worker that a site registers on its own path sees
every navigation under that path before the server does. And
`?key=<link>&key=x` slips past a check on one value: Starlette reads the last
`key`, `URLSearchParams.get` the first. A key stays usable on its own site for
up to 365 days, so a link that reaches another site's page is a credential in
the wrong hands. Hence: on a path of a site (`/{group}/{site}/...`), any `key`
value that starts like a secret link (a selector and the dot, so also a link a
mail client cut short, mangled or left something stuck to) and is not redeemed
for this site leaves the URL with a 302
(`no-store`, no cookie) to the same URL without any `key`, whatever the answer
would have been, after the decision is audited. Only the login redirect keeps
its answer, which never carried the key. The 302 is the same for an unknown
group as for a site that refuses or allows, so it is no enumeration oracle.
Every other path keeps the neutral 404: no link is meant for a path that cannot
be a site, and a target built from one could leave the host (a strip of
`//evil.example/x/?key=...` would be an open redirect). The targets themselves
are built from the decoded path and the query apart, never from `request.url`,
so an encoded `?` in the path cannot move a `key` past the filter. A selector
alone leaves an allowed page too and stays on a refusal, where the code page
needs it. Service worker scripts get the neutral
404 (the row under "Access to content"); with the shielding on a page could not
register one anyway, its origin being opaque. Tests:
`backend/tests/test_serving.py` (`TestKeyNeverReachesAPage`,
`TestServiceWorkerScript`), `backend/tests/test_key_stripping_matrix.py`.

**Secret link without a code.** The same link can be shared in two
ways. In full (`?key=selector.verifier`): whoever has the link can view.
Without the code (`?key=selector`): the link goes via one channel, the code via
the other. A selector alone yields a small page on the content host
that asks for the code; after the right code the visitor gets exactly the same
`__Secure-toon-key` cookie as with a full link.

The code page appears only if the selector belongs to an existing,
usable key of that site. The neutral 404 remains the answer for: an
unknown or wrong selector, a revoked or expired key, a
key of another site, a site that is not set to "secret link", a
site without a live version, an unknown site or group, and a preview. Otherwise
the page itself would betray which selectors exist. Where the visitor may see
the page anyway, the selector leaves the URL before the page loads ("A secret
link never reaches a page", above). The page names no
site title and no group name: only that a code is needed, an input field,
a button and an error line. It carries the headers of protected content
(`no-store`, `noindex`, `Referrer-Policy: same-origin`) with the platform CSP.
That policy is not `no-referrer`, because under `no-referrer` Chrome anonymises
the Origin of the form navigation to `Origin: null`, which would cost the origin
guard its teeth. The page URL carries only the selector, never the verifier, so
a same-origin `Referer` to our own POST target leaks nothing.

The code goes with a POST to `/-/code` (besides those of site feedback, the
only POST path on the content host), with the selector and the intended path in the body. The path
is validated as a returnTo (own origin, no scheme, host or
backslash). An anonymous visitor has no session and therefore no CSRF token;
instead: the same origin only (`Origin`, otherwise, and when it is the literal
`null` of an anonymised form navigation, `Sec-Fetch-Site`),
only `application/x-www-form-urlencoded` and at most 4 KiB, and the fact that
a successful POST only sets a cookie for a key whose code the caller
already knew. Everything that falls outside that is the neutral 404.

Limits: ten attempts per quarter of an hour per client IP (rate limit class
`code`, `TOON_RATELIMIT_CODE_*`) and ten per quarter of an hour per selector,
counted in the route itself. Above the limit the same answer comes as with a
wrong code, with an extra line that it has to be tried again later. That
betrays nothing: a selector that does not exist is counted too and therefore
runs into the limit just as well, so "blocked" says nothing about existence.
The comparison runs via the same constant-time path as `keys.verify`, with a
dummy hash for an unknown selector, an unknown site and a blocked attempt, so
that a wrong selector costs as much as a wrong code.

**Audit scope.** Viewing of non-public content goes into the log, one row per
page, kept 180 days, viewable only by a platform administrator, and every
access is itself recorded. See
`docs/audit-log.md` for the vocabulary, the retention periods and the access.

**The audit vocabulary is English.** The actions, results and reference keys
in `audit_log_entries` (`content_access`, `allowed`, `refused`, `group`,
`member_id`) are English. The table is append-only, which constrains any
future rename: existing rows cannot be rewritten, so a rename after rows
exist leaves two vocabularies in the table, and for `actor_kind`, a
PostgreSQL enum, a value rename rewrites the type but not the rows already
written with it. The schema starts from one base migration (`0001_base`), so
an environment that migrates from zero knows only the vocabulary as it
stands today.

Other points:

- `TOON_BEHIND_PROXY` says whether a proxy stands in front of the pod; on ZAD
  the HAProxy router does, so `true`. The app then reads the last
  `X-Forwarded-For` entry, which is the one that proxy wrote about the peer it
  accepted. `false` where there is none, and the socket decides: reading the
  header there would let a client name itself. The predecessor named the
  routers' own range instead, which on ZAD cannot be had -- the router pods sit
  on the cluster pod network -- and named all of RFC1918 in its place. A second
  proxy (a CDN, an nginx in the pod) would move the entry and this setting
  would have to become a count.
- The ZAD probe is `tcp`: an HTTP probe arrives with the pod IP as `Host` and
  strands on `TrustedHostMiddleware`. `GET /-/healthz` on the admin host is for
  an external monitor, not for the probe.
- The deploy pipeline is still missing: ZAD project file plus zad-actions, with
  `alembic upgrade head` at container start or via a bootstrap action.
- No metrics endpoint: Haven expects a scrapable metrics endpoint;
  still to be built (and then not routed publicly).

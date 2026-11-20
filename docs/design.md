# Toon: the numbered design

This document holds the rules the code cites. A comment that says "spec §7" or
"behaviour requirement 6" means a section number here. It describes Toon as it
is, not as it was designed: where an older design paper and the code disagree,
the code is the subject and the older paper is gone.

Each rule names where it lives (module) and what holds it there (test). Paths
are relative to the repository root; backend modules are under
`backend/src/toon/`, backend tests under `backend/tests/`.

The numbering is fixed by the references in the code and cannot be renumbered
without breaking them. Behaviour requirement *N* under serving is section 5.*N*;
the two spellings are the same rule.

## 1. What Toon is

Toon publishes static sites for government organisations: public, behind SSO
Rijk, for invitees on an email address, or through a secret link. Every pull
request can get a preview of its own. A site is a tree of files that Toon does
not interpret; what Toon owns is who may see it, which version is live and who
may change that. The one exception is site feedback (§16): on a site whose
admin turns it on, Toon adds a script of its own to the HTML pages it serves,
and the stored files stay as they were.

One application answers on two hosts (§4a), serves content and the admin
interface itself, and keeps one PostgreSQL database. There is no nginx logic in
production; the dev stack has a dumb reverse proxy only.

## 2. Concepts

- **Group**: a collaboration unit (`team-aurora`, `fin`). Holds sites and members.
  Groups do not nest.
- **Site**: the published thing, with one live version and any number of
  previews.
- **Version**: an immutable file tree, target `live` or `preview`.
- **Preview**: a named temporary variant per ref (`pr-42`) pointing at a version
  with target `preview`, with an expiry.
- **Member**: a person with a record in the admin environment. A viewer is not
  a member and gets no member record (§7).
- **Invitee**: an email address or SSO subject on a site's viewer list.
- **Secret link**: a URL carrying a key that grants access without logging in.

Models: `models/identity.py`, `models/publication.py`.

## 3. Roles and authorisation

### 3.1 Three roles, two levels, one enum type

`Role` is `reader`, `editor`, `admin`, one shared PostgreSQL enum type used by
both `group_members.role` and `site_members.role` (`constants.py`,
`models/base.py`, migration `0001_base`). A reader looks on, an editor changes
content, an admin changes policy: who may look, who may join in and what
disappears for good. The interface calls them "lezer", "redacteur" and "beheerder"
(`frontend/src/format.ts`).

Beside that there is the platform role on a member: `admin` or `member`
(`models/identity.py`). That role is about people and groups, never about the
content of a site (§3.4).

Guarded by: `test_migrations.py` (the enum values and their order match
`constants.py`), `frontend/src/format.test.ts`.

### 3.2 The widest role wins

The effective role on a site is the widest of the group role on that site's
group and the site role on that site. No site role lowers a group role, and a
site role never grants group authority: a group-level check reads
`group_members` only. Ranking is explicit in `ROLE_RANK` because a `StrEnum`
compares as text and `'editor' < 'admin'` is false.

Code: `access/roles.py` (`widest`, `at_least`, `effective_site_role`),
`api/authorization.py`. Guarded by: `test_admin_api.py`.

### 3.3 One predicate for "belongs to this site"

Access base `site_team` means: everyone with an active role on this site, which
is every active member of the site's group whatever their group role, plus every
active member with a role on the site itself. The same predicate answers "who
may look at this site on its narrowest logged-in setting" and "who may manage
it", so the interface can promise nothing the gate contradicts. It also governs
`_version` views (§7).

Code: `access/gate.py` (`_belongs_to_site`). Guarded by: `test_access_gate.py`.

### 3.4 The platform administrator manages people and groups, not sites

There is no general platform-administrator bypass in the role checks. A platform
administrator activates and deactivates members, reads the platform member list,
grants the platform role, and reads and changes group membership and group
roles, including their own. To do anything inside a group
they give themselves a group role, which is one visible act in the audit log
instead of a silent exception. Their overview lists only the groups they have a
role in, like anyone else's.

Two reading concessions follow from that, not a bypass: a platform administrator
sees that a site exists (they can read every group, its sites included), so they get a 403
rather than the 404 a stranger gets.

Code: `api/authorization.py`, `api/admin/lookup.py` (`_group_with_role` with
`platform_admin=True`, `_site_with_role`). Guarded by: `test_admin_api.py`.

### 3.5 Adding somebody is picking a role

`group_members.role` and `site_members.role` have no database default: whoever
adds a member picks the role instead of silently getting the widest one. The API
defaults the field to `reader`, so the unconsidered case is the harmless one.
Whoever creates a group becomes its `admin` in the same transaction; whoever
creates a site becomes `admin` of that site.

A group can never lose its last admin: the API refuses the removal or demotion
with a 409, and a constraint trigger on `group_members` refuses it in the
database as well. That trigger first checks that the group still exists, or it
would break the cascade when a group itself is deleted. There is deliberately no
equivalent on `site_members`: a site without a site admin is not orphaned,
because the group admin is the floor (§3.2).

Code: `models/identity.py`, `api/admin/`, migration `0001_base`
(`guard_last_group_admin`). Guarded by: `test_admin_api.py`,
`test_migrations.py`.

### 3.6 Who may do what

Minimum effective role per action; the platform column marks the actions a
platform administrator may do without a group role (§3.4).

| Action | Minimum |
|---|---|
| Read own profile (`/me`), read the overview | active member |
| Activate, deactivate a member; read the platform member list; grant the platform role | platform administrator |
| Create a group; the creator becomes group `admin` | active member |
| Read a group, read its member list | group `reader`, or platform administrator |
| Add a group member, change a group role, remove a group member | group `admin`, or platform administrator |
| Change the group name | group `admin` |
| Change the group address (slug) | group `admin` |
| Change the group default access | group `admin` |
| Delete a group, with every site in it | group `admin` |
| Create a site | group `editor`; the creator becomes site `admin` |
| Read versions, previews, site members | effective site `reader` |
| Deploy live or preview, tear a preview down, set a version live | effective site `editor` |
| Read and remove invitees, read and revoke secret links | effective site `editor` |
| Add an invitee, create a secret link, change access, change a preview override | effective site `admin` |
| Link or unlink a repository, change external sources | effective site `admin` |
| Add, change, remove a site member | effective site `admin` |
| Change the site title | effective site `admin` |
| Change the site address (slug) | effective site `admin` plus any role in the group |
| Delete a site | effective site `admin` |
| Read the feedback of a site and its screenshots (§16.11) | effective site `reader`, and `editor` for the email address of a submitter |
| Mark feedback seen, resolved or new again, reply to it | effective site `editor` |
| Delete a feedback item, change the feedback settings | effective site `admin` |
| Read the audit log, resolve a pseudonym, reveal an IP | platform administrator |
| Read how full the content volume is (`GET /platform/storage`) | platform administrator |

A member who has no role at all on a site gets the neutral 404 of an unknown
site rather than a 403: without that, the API would list which sites exist in a
group they have nothing to do with. For the same reason an admin of one site
without a role in its group changes its title but not its address: a 409 on a
taken slug would tell them about the other sites of the group. Any role in the
group is enough, since a reader there sees those sites already.

Code: `api/admin/`, `api/deploys/`, `api/authorization.py`. Guarded by:
`test_admin_api.py`, `test_deploy_api.py`, `test_audit_api.py`,
`test_names_and_titles.py`.

## 4. URL design

Slugs (group, site, preview ref) match
`^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`, so a slug can never start with `_` and
can never be exactly `-` (`constants.py`, `frontend/src/composables/slug.ts`).

The slug of a group or a site can change (§3.6), and every address under it
changes with it at once. The old slug stays reserved for its group or site,
and its old addresses redirect to the new ones through day 30 after the
change, until midnight in Amsterdam, for whoever may see the site (§5.11).
Then the redirect stops and the nightly cleanup frees the slug, also when
something still points at it; until then the group or site can change back
to it. A group admin can end a site's redirect sooner by giving its old slug
to another site of the group (`release`, audited as `site_slug_release`).
Between groups there is no such release, as nobody decides for two groups.
At most five old slugs of one group or site redirect at the same time, and a
change counts towards the creation budget: 20 new groups, sites and
addresses per member per hour. The API knows the current slug only, an old
one answers 404 there, and a workflow is bound to the id of its site rather
than to its address (§8). Current and old slugs share one namespace per
kind, kept by triggers (§12).

That is a conscious deviation from persistent addresses ("Cool URIs don't
change", W3C): GitHub and GitLab keep a redirect until somebody claims the
old name again, and Google advises keeping one for at least a year. Toon stops
after 30 days and frees the slug, so an old address can come to mean another
site; what that costs is in `docs/publishing.md` ("After an address change")
and `docs/security.md`.

| Path | Meaning |
|---|---|
| `/{group}/{site}/...` | live content (content host) |
| `/{group}/{site}/_preview/{ref}/...` | preview content |
| `/{group}/{site}/_version/{version-id}/...` | an older version, for the site team only |
| `/-/login`, `/-/oauth2/callback`, `/-/logout` | OIDC login, callback and logout, spelled the same on both hosts |
| `/-/code` | hands in the code of a secret link (content host only) |
| `/-/feedback/...` | site feedback: the widget bundle, its API under `/-/feedback/v1/`, the consent page and hiding the button (content host only, §16) |
| `/-/api/v1/...` | JSON and deploy API (admin host only) |
| `/-/api/docs` | API documentation, self-hosted UI, publicly readable |
| `/-/oidc/backchannel-logout` | back-channel logout from the OP (admin host only) |
| `/-/groups`, `/-/platform`, `/-/privacy`, `/-/accessibility`, `/-/about`, `/-/sessions` | SPA pages inside the platform namespace |
| `/cli-link` | SPA page the CLI device flow opens by URL |
| `/` on the admin host | the SPA |
| `/` on the content host | 302 to the admin host's landing page |
| `/robots.txt`, `/favicon.ico`, `/.well-known/...` | the locations the web pins down, on both hosts |
| `/-/healthz` | public liveness answer, admin host only |

The platform namespace is the single segment `-`. Because `-` is not a valid
slug it can never collide with content, and every application endpoint lives
under it on both hosts, which is what keeps the root of the admin host free for
the SPA. Inside a dist the reserved top-level segments are `_preview` and
`_version` (§6). Reserved group slugs are the web's own locations plus the
root-level SPA pages, one constant shared by routing, slug validation and a
CHECK on `groups.slug`.

The admin SPA has no `/beheer` or `/admin` path prefix: it sits on the root of
the admin host and the application's own paths are carved out of it (§9).

Code: `constants.py`, `serving/router.py`, `platform/spa.py`,
`frontend/src/router.ts`. Guarded by: `test_constants.py`,
`test_host_separation.py`, `test_spa.py`, `frontend/tests/router-guard.test.ts`.

### 4a. Two origins, one application

Published content may contain arbitrary JavaScript (§5.7 allows
`script-src 'self' 'unsafe-inline'`). On one shared origin that content could
call the admin API with the session cookie the browser sends along, so admin
and content stand on separate origins:

- **admin host** (`beheer.<domain>`, `TOON_BASE_URL`): the SPA, `/-/api/v1`,
  login, callback, logout, back-channel logout. Carries the admin session
  cookie (`__Host-toon-session`, SameSite=Strict) and the CSRF cookie.
- **content host** (`<domain>`, `TOON_CONTENT_BASE_URL`): published sites,
  previews and version views, a redirect to the admin host on `/`, and of the platform
  namespace four paths and one prefix: content login, its callback, content
  logout, `/-/code`, and everything under `/-/feedback/`, which site feedback
  uses (§16). Restricted content gets a content session (SameSite=Lax) without
  any admin authority, in a cookie scoped to the site it is for (§5.10).

The separation is enforced by the application itself, as middleware that reads
the `Host` header: on the content host only the paths above exist, on the admin
host everything. Every refusal is byte-identical to any other
neutral 404, headers included, because the security headers sit outside that
middleware and follow the host rather than the path (§5.6, §9). The prefix
admits `/-/feedback/` with its slash and nothing that only starts like it
(`/-/feedback`, `/-/feedbackx`). On the admin host every `/-/` path reaches the
application, the feedback routes included, so each of those checks the host
itself and answers anywhere but the content host with the neutral 404.

The defence on the admin API is layered, and the Origin check is the load
bearing one: a present `Origin` must be exactly the admin origin, and in its
absence `Sec-Fetch-Site` may be at most `same-origin` or `none`. `same-site` is
refused on purpose, because the content host is a sibling of the admin host and
a browser would count it as same-site; for the same reason SameSite=Strict is a
second layer and not the defence. CORS is never opened on the admin host: the
only answers with CORS headers are those of the feedback widget API on the
content host (§16.8), which reads no cookie. A non-browser client
without either header (CI, curl) passes, and session-borne mutations still need
the CSRF double submit.

Code: `host_separation.py`, `api/origin_guard.py`, `config.py`, `main.py`,
`serving/feedback_routes.py`.
Guarded by: `test_host_separation.py`, `test_origin_guard.py`,
`test_api_integration.py`, `test_app_integration.py`, `test_feedback_api.py`.

## 5. Serving

The application serves content itself with Starlette's `FileResponse` (sendfile
where the server offers it, Range requests) and answers conditional requests
without touching the store. There is no X-Accel-Redirect and no nginx datapath;
nginx exists only in the dev compose stack, passing the `Host` through.

Order per request: service worker refusal, path validation, access decision
(with the lookup of an old address where the gate found nothing, §5.11),
audit, key redeem or removal (§7.4), file resolution, `If-None-Match`,
response. The lexical 301 sits before all of it (§5.2). A navigation to a
feedback page (§16.2) skips `If-None-Match` and gets the feedback widget where
the page takes it (§16.3).

On the content host HEAD is the GET without the body (`head_requests.py`): the
app gets a GET, the client gets its headers, so the gate, the audit and a
refusal's neutral 404 are the same as for the GET. The routes themselves take
GET only, because a content route such as `/{group}/{site}/{rest:path}` also
matches `/-/...` on the admin host, where HEAD on a GET-only route has to keep
its 405. A file response still sends a HEAD its headers alone, without reading
the file (`HEAD_SCOPE_KEY`), so a HEAD on a large file costs no more than its
answer. Under `/-/` on the content host HEAD is not turned into GET either:
the content login, callback and logout act on a GET, and a HEAD must never
start a login.

Code: `serving/router.py`, `serving/response.py`, `serving/resolution.py`,
`serving/mime.py`, `head_requests.py`. Guarded by: `test_serving.py`,
`test_resolution.py`, `test_mime.py`, `test_head_requests.py`.

The nine behaviour requirements below are the same numbers as §5.1 to §5.9.

### 5.1 Path validation before anything else

The request path is validated serving-side, independent of what ingest already
refused: null bytes, backslashes and `..` segments yield the neutral 404. There
is deliberately no second URL decode, or `%252e%252e` would still collapse into
`..`. Containment inside the version root is enforced once more by
`ContentStore.file_path`. Code: `serving/resolution.py` (`normalise_rest`),
`ingest/store.py`. Guarded by: `test_resolution.py`, `test_store.py`.

### 5.2 Trailing slash and the index rewrite

`/{group}/{site}` without a slash gets a purely lexical 301 to
`/{group}/{site}/`, before any existence or access check, so the redirect leaks
nothing; the same holds for a preview root and a version root. A directory
*inside* a site gets its 301 only after an allow decision. A path ending in `/`
resolves to `index.html`, case-sensitively: `Index.html` is a different file.
Code: `serving/router.py` (`_lexical_slash_redirect`), `serving/resolution.py`.
Guarded by: `test_serving.py`, `test_app_integration.py`.

### 5.3 MIME table

Python's `mimetypes` plus an explicit table pinned independently of the platform
(`.mjs`, `.map`, `.wasm`, `.webmanifest`, `.avif`, `.woff2`, `.ttf`, `.otf` and
more). Unknown is `application/octet-stream`. Every content response carries
`X-Content-Type-Options: nosniff`. Code: `serving/mime.py`. Guarded by:
`test_mime.py`.

### 5.4 ETag and 304

`ETag` is the version id. `If-None-Match` is answered by the application itself
with a 304, after resolution has found a file: a 304 for a path without a file
would let an intermediary treat a non-existent resource as fresh. The 304
carries the header set of the 200 apart from `Content-Type`: `ETag`,
`Cache-Control`, `nosniff`, the content CSP, `Referrer-Policy`,
`Vary: Service-Worker` (§7.4) and, where it applies, `X-Robots-Tag`. A browser
keeps what it stored with the 200 and
overwrites only the headers a 304 repeats (RFC 9111 §3.2, verified in
Chromium, Firefox and WebKit), and turning a site switch or secret links on
or off changes the CSP or the Referrer-Policy without changing the version, so
without them a cached page would keep the policy it was first served with.
That reaches what revalidates: public HTML, which is `no-cache`; restricted
content is not stored at all (§5.5). Any other file of a public site is
`immutable` and never revalidates, so a worker script or an SVG
opened on its own keeps the policy it was first served with until it leaves
the cache.

A feedback page (§16.2) is the exception (§16.3): a navigation to it never
gets a 304, and every answer for it, the 304 included, carries
`Sec-Fetch-Dest` in `Vary`. A page with the widget in it has no
`ETag` at all, because its bytes carry a token of their own. Code:
`serving/response.py` (`etag_for`, `if_none_match_matches`, `make_304`).
Guarded by: `test_serving.py`, `test_feedback_inject.py`.

### 5.4a Compression

A text file of 1 KiB or more whose gzip is smaller than itself is kept
compressed only: as gzip under `_encoded/gzip/` in the version, and as brotli
under `_encoded/br/` where that is smaller than the gzip
(`ingest/precompress.py`, at ingest, before the quota is measured). Text means
`text/*`, `+json` and `+xml` types, JSON, XML, SVG, WebAssembly, `.ico`, TTF
and OTF. The request's `Accept-Encoding` picks the copy: the highest weight
wins, a tie goes to brotli, and no header means the file as published. A
client that takes neither (curl without `--compressed`, a link checker) gets
the gzip unpacked on the way out, without `Content-Length` and without Range:
a range request gets the whole file. Every browser takes gzip.

The ETag names the encoding (`"{version_id}-br"`, `"{version_id}-gzip"`, the
bare version id for the file as published), and a response for a file kept
compressed carries `Vary: Service-Worker, Accept-Encoding`, the 304 included,
so no cache confirms one representation for a request that gets another.
`If-Range` is honoured on the ETag only: the two copies of a file are written
in the same second, so a date cannot tell them apart. Range on a copy covers
its compressed bytes. `_encoded` is refused in the root of a bundle, and the
store never serves a path under it, so a site's own files and these never
meet. Versions published before this keep their files as they were.
A page that gets the feedback widget is the one answer that ignores the copy
the request picks: it is decoded from the file as published or its gzip copy
and goes out without `Content-Encoding`, `ETag` or Range (§16.3).
Code:
`ingest/precompress.py`, `ingest/store.py`,
`serving/resolution.py` (`locate`, `choose_encoding`, `representation`),
`serving/response.py`. Guarded by: `test_precompress.py`, `test_store.py`,
`test_resolution.py`, `test_serving.py`,
`test_ingest_service.py`.

### 5.5 Cache-Control

Public content: HTML gets `no-cache, must-revalidate`, every other asset
`max-age=31536000, immutable`. An asset URL is not content-addressed and
survives a redeploy, so a returning visitor keeps an old file under the same
name until it leaves the cache; accepted for the reach a shared cache gives
public content. Content whose effective access is not public,
and every `_version` view, gets `no-store`: every site shares one origin, and
what the browser stored of a restricted site a page of another site without
the sandbox reads with `fetch(url, {cache: "only-if-cached"})`, which skips
the server and so the access gate. `no-cache` is not enough, because
`only-if-cached` and `force-cache` skip the revalidation too (#152). An
origin per site would make the browser refuse that read, and is the moment
to cache restricted content again. The login redirect and the directory 301
carry `no-store`, because a 301 is heuristically cacheable. A navigation to a
feedback page (§16.2) is `no-store` too, public or not and with or without the
widget in it: the page carries a token for this visitor and this view
(§16.3). Code:
`serving/response.py` (`cache_control`), `serving/router.py`.
Guarded by: `test_serving.py`, `test_feedback_inject.py`,
`e2e/tests/shared-origin.spec.ts`.

### 5.6 The neutral 404

Refusal and non-existence are one byte-identical answer, headers included:
status 404, body `Niet gevonden\n`, `Cache-Control: no-store`, `nosniff`, the
platform CSP (§5.7) and `Referrer-Policy: no-referrer`. It has a single
construction point, which is what keeps it identical; it keeps that CSP
whatever a site allows, because a policy that followed the site would say which
site the refusal was about.

One answer stands in for it on purpose: on a path of a site
(`/{group}/{site}/...`), when the query carries a `key` in the shape of a
secret link, a refusal is a 302 to the same URL without `key` instead (§7.4);
a service worker script still gets the 404 there. That 302 is one answer as
well, for a site that refuses, a site that allows and an address with nothing
behind it, so it says no more about what exists than the 404 does. Every other
path keeps the neutral 404, key or not: no link is meant for a path that cannot
be a site, and a target built from one could leave the host
(`//evil.example/`).

A site without a live version is a neutral 404 for everyone, public base
included. A version's own root `404.html` is shown to authorised visitors only,
with status 404.

Code: `serving/response.py` (`neutral_404_response`), `access/gate.py`,
`access/decision.py`. Guarded by: `test_access_gate.py`, `test_serving.py`,
`test_front_door.py`.

### 5.7 The content CSP

The publication contract, on every content response:

```
default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval';
style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'
data:; connect-src 'self'; media-src 'self'; frame-ancestors 'none'; base-uri
'self'; form-action 'self'; object-src 'none'
```

`'wasm-unsafe-eval'` lets a page compile WebAssembly, which a static search
index such as Pagefind needs; it does not open `eval()` or `new Function()`,
which `'unsafe-eval'` would. A dedicated worker started from a file of the
site gets this policy with its own response, so the same keyword covers a
worker that compiles the module. The risk assessment is in `docs/security.md`,
"WebAssembly".

A site may allow **external sources**, which is on by default for a new site
(`sites.external_sources`, server default true). That widens `script-src` and
`style-src` with the three CDN hosts, `style-src` also with Google Fonts, and
`font-src` with the Google Fonts file host. `connect-src` stays `'self'`: a page
may load a library from those hosts, never send data to them.

A site is also **shielded from the other sites**, which is on by default for a
new site (`sites.sandbox`, server default true). That appends
`sandbox allow-scripts allow-forms allow-popups`. `allow-same-origin` is
deliberately absent: without it the document gets an opaque origin, so it is
same-origin with nothing, can read no other site on the shared content
hostname and stores nothing. Browser storage is what the switch costs, and why
it is a switch at all.

An opaque origin is cross-site with everything, its own site included, and
that has two consequences for the page's own subresources. Its no-cors loads
(stylesheets, classic scripts, images, media) do work, but only because the
content session cookie and the secret-link cookie are `SameSite=None` (§7.6);
under `SameSite=Lax` a non-public site serves its pages and none of its
assets. What cannot work is what the browser fetches in CORS mode from such a
document: web fonts, module scripts and `fetch`/XHR go out with `Origin: null`
and no cookie, and content answers carry no CORS headers. A site that needs a
web font of its own, ES modules or calls back to itself has to have this switch
off. The feedback widget API is the one exception, and it reads no cookie
(§16.8).

The boundary this draws is per site and one-directional: it keeps the content
of *this* site away from what a visitor may see on the others. A site with the
shielding off is back on the shared origin, so the platform is not isolated by
this, only defaulted safe.

Every policy comes out of one directive table with one addition per switch, so
the four combinations cannot drift apart.

On a route that collects feedback (§16.2) Toon adds a script of its own to the
HTML pages it serves: `/-/feedback/widget.<hash>.js`, a classic deferred
script from the content host itself. The policy does not change for it.
`script-src 'self'` admits it under every combination, the shielding
included, because a classic script loads without CORS. Its calls to
`/-/feedback/v1` are `connect-src 'self'`, and its styles sit in one
`<style>` in its shadow root, under `style-src 'unsafe-inline'`. On a shielded
page those calls go out with `Origin: null`, which is why the widget API
answers with CORS headers (§16.8).

The platform's own answers on the content host, the root redirect, the code page
for a secret link and the neutral 404 (§5.6), carry a policy of their own
(`platform_csp`): they run no script and share the origin with every
published site, so they get nothing the content policy grants. So do the
answers of the feedback widget API, and the consent page for feedback with an
account carries it with its form and `sandbox allow-forms` on top (§16.6).

```
default-src 'none'; style-src 'sha256-…'; base-uri 'none';
form-action 'none'; frame-ancestors 'none'
```

`style-src` holds the hash of the page's one inline `<style>` and is absent
where there is none (the 404). `form-action` is `'self'` on the code page and
the consent page only.
A hash covers a `<style>` element, not a `style=""` attribute, so these pages
carry none.

Code: `serving/response.py` (`_CONTENT_DIRECTIVES`, `EXTERNAL_SOURCES`,
`SANDBOX`, `content_csp`, `platform_csp`), `api/admin/sites/site.py` (`PUT .../external-sources`,
`PUT .../sandbox`), `serving/feedback_consent.py` (`CONSENT_PAGE_HEADERS`).
Guarded by: `test_serving.py`, `test_security_headers.py`,
`test_admin_api.py`, `test_feedback_consent.py`, `test_feedback_api.py`.

### 5.8 The base path contract

A dist is built with base `/{group}/{site}/` for live and
`/{group}/{site}/_preview/{ref}/` for a preview. Ingest refuses `_preview` and
`_version` as top-level entries of the published tree. A dist built with an
absolute base refers, under `_version/{id}/`, to the live assets; relative dists
show exactly there. The contract with its Astro and Vite examples and the CI
snippets lives in `docs/publishing.md` §2, which is the reference for it; this
section only pins the rule down.

### 5.9 Indexability

Previews and `_version` views carry `X-Robots-Tag: noindex, nofollow`, and so
does every SPA response (§9). Live content with a public base is deliberately
indexable; every other base yields a crawler no content at all. `robots.txt`
differs per host: the admin host disallows everything, the content host
disallows nothing.

Code: `serving/router.py`, `platform/spa.py` (`spa_headers`),
`platform/pages.py` (`ROBOTS_TXT_ADMIN`, `ROBOTS_TXT_CONTENT`). Guarded by:
`test_serving.py`, `test_spa.py`, `test_front_door.py`.

### 5.10 Subresources of another site

Every site of every group is served from one hostname under a path prefix and
uploaded content may run its own JavaScript. So a page of site A can `fetch()`
site B, and the gate then decides on whatever credentials the browser attached.
Three things stand in the way of that, and it is worth being exact about what
each one does and does not do.

**The content session cookie is scoped to one site**, `Path=/{group}/{site}/`
(§7.6), the same shape the secret-link cookie has. A cookie path is matched
against the **requested** URL, not against the page that asked for it, so this
does not keep a session out of a fetch aimed at a site the visitor has already
opened: their cookie for site B goes along whoever asks for site B. What it does
take away is the ambient reach of one session over every site at once. A site
the visitor has not opened in this browser has no cookie, and a request aimed at
it is anonymous.

**Published content has an origin of its own** (§5.7), which is the boundary a
browser does enforce: an opaque-origin document can read no document and no
storage of another site, whatever it asks for and whatever comes back. What it
does not take away is the credential: a sandboxed page is cross-site with its
own site as well, so the cookies it needs for its own assets are
`SameSite=None` (§7.6) and therefore ride along on any page's request for that
site. Reading the answer is what the origin stops.

**Non-public content is served to a subresource request only when the request
says it comes from the same site**: `Sec-Fetch-Site: same-origin` plus a
`Sec-Fetch-Dest` other than `document`, `iframe` or `frame` needs a `Referer`
whose path lies inside `/{group}/{site}/`. This reasons about headers, one of
which an attacking page can partly shape, and it is the layer that catches a
request that does carry a credential of its own. Live, preview and `_version` of
one site are one publishing team and therefore one boundary.

Two things deliberately fall outside the last one. A top-level navigation is
something the visitor does and sees, also from one site to another; a
`window.open()` to another site is therefore served, and the opened document is
same-origin with its opener unless the sandbox of §5.7 separates them. And a
request without `Sec-Fetch-Site` at all (curl, a link checker, an older browser)
is served: it carries no ambient credentials, the same reasoning as the origin
check on `/-/code` (§7.4).

This leans on the site's own pages supplying a `Referer`, which is why
`Referrer-Policy` on a site with secret links is `same-origin` and no longer
`no-referrer` (§7.2). A refusal is the neutral 404 (§5.6), or with a secret link
in the query the 302 that takes it out (§7.4), audited with reason
`FOREIGN_SUBRESOURCE`.

The check sits before the login redirect, so it decides the same way with a
session and without one: nobody logs in because of a stylesheet fetch, and a
302 would tell an anonymous caller that this site exists. It does not sit
before the neutral 404, which is this answer already; a refusal the gate made
itself keeps the reason that really refused (`UNKNOWN_SITE` and the rest).
At the old address of a renamed site (§5.11) the `Referer` is held to the old
prefix on every refusal and to the current one only when access is allowed.

Code: `serving/router.py` (`_foreign_subresource`). Guarded by:
`test_serving.py`.

### 5.11 Old addresses

Only when the gate finds no group or no site at the requested slugs
(`UNKNOWN_GROUP`, `UNKNOWN_SITE`) does serving look the two slugs up as
current or old ones, an old one counting while it still redirects (§4). Do
they lead to a site, then the unchanged gate decides again at its current
address, with the credentials the visitor sent along, and the answer at the
old address follows from that decision:

| Decision at the current address | Answer at the old address |
|---|---|
| allowed | 301 to the same path at the current address, with the query minus every `key`, `Cache-Control: no-store` and `Sunset` (RFC 8594) carrying the end of the redirect |
| allowed through `?key=` | the same, and the key cookie for the new path in the same answer (§7.4) |
| allowed through the key cookie | the same; on a top-level navigation the cookie is set again for the new path, signed over the key id the gate accepted |
| allowed through a session | the same; at the new path the login shortcut sets the site cookie (§7.6) |
| a selector alone of this site | the code page at the old address, as before the rename (§7.4) |
| neutral 404 | the neutral 404, byte for byte |
| login redirect, the first-visit rule included | the login redirect with the old path as `returnTo` |

A visitor who may not look gets exactly the answer of before the rename,
whatever `Referer` comes along: §5.10 compares with the old prefix on a
refusal, or a right guess at the new address would get the login redirect and
a wrong one the neutral 404. `returnTo` stays the old path for the same
reason, so the login does not reveal where a restricted site went. The
`Location` is built from the requested path, never from the `Host` or the
full URL. 301 because the site did move, `no-store` because the redirect ends
and depends on who asks. A group and a site renamed both take one step: an old
slug leads to the id, so `/old-group/old-site/` goes straight to the current
address, until the first of the two stops redirecting. Ordinary traffic costs
nothing, since the lookup runs only where the gate found nothing. The 301
writes no audit row; the look at the new address is logged as before, and a
refusal at the old one with the slugs that were asked for.

Code: `serving/addresses.py`, `serving/router.py`, `serving/code_page.py`,
`slug_window.py`. Guarded by: `test_serving.py` (`TestOldAddress`),
`test_code_page.py`, `test_access_gate.py`, `test_sessions.py`.

## 6. Ingest

Transport is multipart with one file field. The payload is a single `.html` file
or a `.zip`/`.tar.gz`/`.tgz` archive. The upload is never read into memory: the
body streams chunk by chunk into a spool file in `{content_root}/_tmp/`, outside
every servable path, with the body limit checked as it goes.

Validation is fail-closed: absolute paths, `..` segments, symlinks and
hardlinks, null bytes and empty paths are refused, backslashes are normalised,
and `_preview`, `_version` and `_encoded` are refused as top-level entries of
the published tree. Decompression is checked incrementally against the limits, so an archive
bomb never gets past them; header sizes are not trusted, though a header already
claiming more than the limit is an early refusal.

Limits (defaults, env-overridable, sized for a 1 GiB volume): request body
100 MiB, 50 MiB per unpacked file, 200 MiB unpacked in total, 1000 files, depth
10 (`config.py`, `ingest_max_*`).

Those bound one bundle. Two more bound what the bundles leave behind, because
the nightly cleanup only removes live versions beyond the current one and the
number before it that the site keeps (its own, else `live_versions_kept`, 5; 0
keeps all): `site_max_bytes` (500 MiB, 0
turns it off) caps what every version of one site together occupies, measured
on the volume just before the new version is renamed into place, so a refusal
leaves nothing behind and answers 413.
`storage_min_free_bytes` (100 MiB, 0 turns it off) is the free space a deploy never takes the volume below, because a volume run dry
takes the serving of every other site down with it. It is held against the
actual deploy, not the largest one allowed: before the body is read, the
declared `Content-Length` (capped at the body limit) must fit above it; while
the upload is spooled and unpacked, every chunk is checked against it, with the
volume measured again at least every 4 MiB. Crossing it at any of those
moments answers the same 503 `STORAGE_UNAVAILABLE`, and the spool and work
directory are cleaned up as on any other refusal.

Nobody watches the ZAD probe (`tcp`), so a filling volume is made visible in
three places. `GET /-/healthz` is public, on the admin host only (§4a): no
session, no CSRF, `Cache-Control: no-store`, and the normal content rate limit.
It answers `200 {"status": "ok"}`, or `200 {"status": "degraded", "checks":
[...]}`, or `503 {"status": "fail", "checks": ["database"]}` when
`SELECT 1` fails or takes longer than two seconds (draft-inadarei-api-health-check:
pass and warn are 2xx, fail is 5xx). `checks` lists only the names of the
checks that complain, in the order `database`, `storage`, `content_root`,
`idp_revalidation`, and never a message, a number or an error code.
`storage` appears once the free space drops below `storage_min_free_bytes` plus
`ingest_max_total`, the point where a deploy of the maximum size would be
refused (never with the reserve switched off). `content_root` is decided once
at startup and only when `TOON_ENVIRONMENT=productie`: the content root shares
its device with its parent, so it is not a mount point and what is published
there is lost when the container restarts. `idp_revalidation` is the complaint
of §7.7. The detail lives elsewhere: the low-space condition writes an ERROR
line, at most once per hour, from a small periodic task in the lifespan that
measures the volume every five minutes, so the line appears whether or not
anyone calls the endpoint; a `content_root` complaint is logged once at
startup. Neither refuses to start. Platform administrators see the volume
itself on "Platformbeheer" (`/-/platform`): `GET /platform/storage` returns
total, used and free bytes, the reserve and the largest deploy, and nothing
about groups or sites, because the platform administrator manages people and
groups (§3.4) and does not look into sites.

Code: `platform/health.py`, `ingest/storage_health.py`, `main.py`, `api/admin/`
(`platform_storage`). Guarded by: `test_health.py`, `test_storage_health.py`,
`test_admin_api.py`, `test_host_separation.py`.

The root of the site is settled in three steps: a chain of enclosing directories
holding exactly one entry is peeled off; a `base_path` from the caller then wins;
and if that yields no `index.html` in the root the bundle is refused, naming the
`index.html` paths found as a proposal. The machine proposes, the human
confirms. Capitalisation counts, because both the filesystem and §5.2 look for
exactly `index.html`.

Versions are immutable: everything is written to `{root}/_tmp/{uuid}` first and
moved with one atomic `os.rename` to `{site_id}/{version_id}` on the same
filesystem. No slug is part of the path: a site created again under a slug
that was deleted starts with a directory of its own, and an id never recurs. Going live is a pointer swap on `sites.live_version_id` in the same
transaction as the version insert. Rollback is a pointer swap to an earlier
version with target `live`; a version with target `preview` is never a rollback
target. Every live version is kept.

Code: `ingest/unpacker/`, `ingest/store.py`, `ingest/service.py`. Guarded by:
`test_unpacker.py`, `test_store.py`, `test_ingest_service.py`,
`test_deploy_api.py`.

## 7. Access, sessions and audit

Looking and administering are separate. The browser only ever gets an HttpOnly
session cookie; no token ever lives in the web app (BFF). The reason is that
Toon's core resource is static content the browser navigates and loads itself,
where nothing can carry an Authorization header.

### 7.1 Access is a base plus two extras

Access is not one choice out of five but one base plus two independent switches
(`AccessPolicy` in `constants.py`). A site, a group default and a preview
override all carry the same three fields.

The base (`AccessBase`), on `sites.access_base`:

1. `public`: everyone, without a session.
2. `sso`: anyone with a valid login at the configured OIDC provider. The gate
   checks exactly one thing there: that the session carries a `sub`. No member
   lookup, no claim about provenance. The interface therefore calls it
   "Iedereen die inlogt met SSO Rijk" and says so in its explanation rather than
   promising a provenance the code does not enforce.
3. `site_team`: everyone with an active role on the site or its group (§3.3).
4. `nobody`: the base grants nothing.

The extras, each a boolean beside the base:

- `access_keys`: a valid secret link grants access, without a session.
- `access_invitees`: someone on the invitee list gets in after logging in, on
  their `sub` or on an email address the IdP marked verified. An unverified
  email address never matches.

`nobody` with both extras off means nobody can view the site. That is allowed
and useful: it closes a site without deleting or rolling it back, and it gives
the same 404 as a site that does not exist. The site team can still inspect the
content through `_version` (§7.3). Public plus an extra is allowed and adds
nobody; the interface says so instead of hiding the switch.

### 7.2 The decision order

Every way in is tried before any refusal is chosen, because the base and the
extras are an OR and stopping at the first closed door would make it an AND:

1. base `public`: allow. No database hit.
2. `access_keys` on and a key presented that verifies (query or cookie): allow,
   recording the selector that granted it.
3. with a session: base `sso` allows; base `site_team` allows if the visitor
   belongs to the site; otherwise `access_invitees` on and the visitor is an
   invitee allows; otherwise the neutral 404.
4. without a session: if logging in could help (`login_can_help`: base `sso`,
   base `site_team`, or invitees on), a login redirect for live content and a
   neutral 404 for a preview, because the existence of a preview does not leak.
   The serving layer then turns every anonymous preview refusal into the same
   login redirect on a top-level navigation, whatever its reason (§7.6).
5. otherwise the neutral 404, with reason `KEY_INVALID` when a key was presented
   and refused and nothing else could have helped, and `NO_ACCESS` otherwise.
   Outward they are the same answer.

An invalid, expired or revoked key therefore grants nothing and refuses nothing:
a site team member with a broken link falls through to the team route.

A preview override is one whole policy, base and both extras together or none of
them (a CHECK on `previews`), and it replaces the site's policy rather than
merging with it. An expired preview is a neutral 404 at the decision itself; the
cleanup job is only the safety net.

`Referrer-Policy: same-origin` hangs on the configuration (`access_keys` is on
for this site), not on the way this visitor got in, so the header does not vary
per visitor; everything else gets `strict-origin-when-cross-origin`. Both send
nothing to another origin, and the page URL never carries the verifier: a
`?key=` is redeemed, or taken out, with a 302 that strips it (§7.4). Not
`no-referrer`, because the guard of §5.10 needs a site's own pages to identify
themselves.

Code: `access/gate.py` (`decide`, `decide_preview`, `_assess`),
`access/decision.py`, `serving/response.py`. Guarded by: `test_access_gate.py`
(the full matrix), `test_serving.py`.

### 7.3 `_version` views

`/{group}/{site}/_version/{id}/` is for the site team only (§3.3), whatever the
site's access setting; for anybody else the neutral 404. The one exception is an
anonymous top-level navigation, which goes to the login whatever lies behind the
path (§7.6).
Membership is checked before the version lookup, so a non-member cannot probe
for the existence of version ids. Every `_version` access is audited.

Code: `access/gate.py` (`decide_version`), `serving/router.py`. Guarded by:
`test_access_gate.py`, `test_serving.py`.

### 7.4 Secret links

A secret link is `?key=selector.verifier`. The database keeps the selector and
SHA-256 of the verifier, compared in constant time; the plaintext exists only at
the moment of creation. A valid key is redeemed: the response is a 302 to the
same URL without `key` with `Cache-Control: no-store`, setting a
`__Secure-toon-key` cookie (HttpOnly, SameSite=None) on the path of exactly that
site or preview, so the key does not stay behind in the address bar, the history
or a log. The cookie is a reference to the key record, not proof in itself, and
is validated server-side on every request, so revoking or expiry breaks
outstanding cookies at once. The uvicorn access log is off for the same reason.

A link can also be shared in two parts: `?key=selector` alone shows the code
page, where the verifier goes in the body of a POST to `/-/code`. That page
appears only for a selector belonging to a usable key of a live site that has
secret links on; everything else stays the neutral 404, or the page would say
which selectors exist. It has no session and no CSRF token, so what guards it is
same-origin only, a hard limit per selector on top of the per-IP `code` rate
limit class, and the fact that a successful POST only sets a cookie for a key
whose code the caller just proved to know.

A page never sees a secret link that is not redeemed for its own site. A page
reads its own address, and a later page on the content host can read the tab's
history, so a link left in either reaches whichever site answers it. Whenever,
on a path of a site (`/{group}/{site}/...`), one of the `key` values starts
like a secret link (eight letters or digits and a dot, however much of the
code follows, so also a link a mail client cut short, mangled or left something
stuck to) and the answer is not its redemption, serving answers with the same
302 to the URL without any `key`, with `no-store` and without a cookie: on an
allowed page and on the neutral 404 alike. Beside such a value the code page
does not appear; the gate's own answer stands. The decision is audited first,
so `KEY_INVALID` stays visible. Every value counts, because the gate reads the
last `key` and a page's `URLSearchParams` the first. Only the login redirect keeps its answer; it never
carried the key. A selector alone leaves an allowed page the same way and stays
on a refusal, where the code page needs it. `KEY=` and other spellings of the
name are left alone: redeeming is case-sensitive. Every other path keeps the
neutral 404 (§5.6).

At the old address of a renamed site (§5.11) a valid `?key=` is redeemed with
the 301 to the new address, which carries the cookie for the new path. The
code page stays at the old address, and a code handed in there leads to the
new path, with the cookie for that path.

The content host refuses service workers: a request with
`Service-Worker: script` or `Sec-Fetch-Dest: serviceworker` gets the neutral
404 before anything else and without an audit row, like a routing miss. A
worker that a site registers on its own path would see every navigation under
that path before the server does, secret links included. A shielded page
(§5.7) could not register one anyway: its origin is opaque. Content responses
carry `Vary: Service-Worker`: a registration may be answered from the HTTP
cache (`updateViaCache: 'all'`), and without it from what a plain request for
the same file left there, so the refusal would never run.

Code: `access/keys.py`, `serving/code_page.py`, `serving/router.py`
(`_redeem_key`, `_strip_key_redirect`, `_refuse`, `_is_service_worker_script`).
Guarded by: `test_keys.py`, `test_code_page.py`, `test_serving.py`,
`test_key_stripping_matrix.py`.

### 7.5 Looking creates no member record

A login only to look creates no member record: the decision uses session claims
only. A record comes into being on a first admin visit, and it is created
**active** straight away, because anyone who can complete the SSO login may use
Toon. `TOON_BOOTSTRAP_ADMIN_SUB` additionally makes that member a platform
administrator, idempotently. A member set to `deactivated` loses admin access
and the `site_team` route; the other ways in do not depend on member status.

Because a viewer has no member row, an SSO viewer of protected content is
recorded in `content_viewers` instead, upserted on every successful content-host
login, purged after 180 days without one. Without it the audit log would have
nothing to resolve such an actor to.

Code: `auth/members.py`, `auth/content_viewers.py`. Guarded by:
`test_members.py`, `test_content_viewers.py`, `test_sessions.py`.

### 7.6 Sessions

Server-side sessions with a signed `__Host` cookie; the browser gets a signed
reference, claims never stand on their own in the cookie. The store is in
process memory, because Toon runs one replica (§11), and sessions deliberately
do not survive a restart. Maximum session age is 12 hours, with session id
rotation at login. Two kinds (§4a) live in one store, kept apart by the kind
field plus a separate cookie name: admin routes accept the admin session only,
content serving the content session only. `returnTo` is validated strictly: own
origin, paths only, and never carrying a `key` parameter.

The admin session cookie is SameSite=Strict. On the content host the two
cookies differ: the site cookie is SameSite=None, the anchor stays Lax. None is
what a shared link to restricted content needs (it has to open after a
cross-site navigation, which Lax already covers) plus what a
sandboxed page needs to reach its own assets, since its opaque origin is
cross-site with its own site (§5.7, §5.10). The anchor is the cookie that can
mint a site cookie for the next site and is only ever read on a top-level
navigation, so it keeps the narrower Lax. What None costs, and why the
alternatives are worse, is in `docs/security.md`. Logging out on the admin
host is POST only and ends the content session too, through a redirect to
`/-/logout` on the content host.

The content session rides in two cookies, because all sites share one
hostname (§5.10):

- `__Secure-toon-content`, `Path=/{group}/{site}/`: the session id for content
  requests. `__Secure-` rather than `__Host-`, because the `__Host-` prefix
  requires `Path=/`. What that prefix also forbids is a `Domain` attribute, and
  giving that up is what makes this cookie shadowable from a sibling host on the
  same registrable domain; the value is signed and the session has to exist in
  the store, so the worst a shadow achieves is pushing a visitor into a session
  of the shadower's own. The script of a page without the shielding (§5.7) can
  plant such a cookie for another site's path as well. The content origin has
  session-borne actions for site feedback alone, and §16.6 says what holds
  them and what it accepts. The admin cookie keeps its `__Host-` prefix and
  with it the guarantee that nothing on the content host can write it.
- `__Secure-toon-content-anchor`, `Path=/-/`: the same session id where the
  login, the callback and the logout can read it, and nowhere else.

One server-side session behind both: one 12-hour lifetime, one `kind`, one
revocation. Opening a site the visitor has not opened before arrives without a
site cookie, so the gate sees an anonymous visitor and answers with the login
redirect; `/-/login` then hands out the cookie for that site off the anchor
session and sends the visitor on, without a round trip to the IdP. It does that
only for a top-level navigation (`Sec-Fetch-Dest: document`, a header page
script cannot set), because otherwise a page could walk another site's request
through the login and collect a session for it on the way back. Nor while the
request carries the anchor more than once (`content_anchor_is_duplicated`). A
second anchor, set at another path by the script of a page without the
shielding (§5.7) or from a sibling host, is the one `request.cookies` keeps
when it comes last, and the site cookie minted from it would replace the
visitor's own at that path, which no script can do. Such a visitor goes to
the IdP instead, whose callback sets their own cookies. A preview or a
`_version` view never gets a login redirect from the gate, and a site without a
live version has no live route that could hand one out, so the serving layer
turns every anonymous refusal on those two routes into the login redirect,
whatever the reason. The answer is then the same for a path with nothing behind
it, so guessing paths teaches nothing. It does that only for a top-level
navigation, not when a `?key=` or key cookie came along (its holder may have no
SSO account), and not for a path that is no `/{group}/{site}/` of slugs, where
the login could set no site cookie and the visitor would loop. Everything else
keeps the byte-identical neutral 404, or with a secret link in the query the
302 that takes it out (§7.4); the navigation that follows that 302 carries no
key and so gets the redirect.

The logout clears the anchor and the site cookie at every path the
session handed one out for (the session remembers up to 32 of them). Past that
ceiling a site cookie survives in the browser until it closes, and opens
nothing: the session behind the id is gone.

A renamed site leaves its site cookies at the old path. A visitor who comes
in at the old address with one is redirected (§5.11), arrives at the new path
without a cookie, and the login shortcut above sets one there; a visitor may
therefore have to pass the login once more, without the IdP as long as the
anchor session holds.

Code: `auth/sessions.py`, `platform/pages.py`, `serving/feedback_consent.py`.
Guarded by: `test_sessions.py`, `test_feedback_consent.py`,
`test_feedback_inject.py`.

### 7.7 OIDC

Authorization code plus PKCE. Client authentication is configurable
(`private_key_jwt`, `client_secret_post`, `client_secret_basic`), because the
ZAD Keycloak hands out client-secret clients. Id token validation: an
asymmetric alg allowlist (RS256/PS256/ES256, never `none` or HS\*) as our own
code on top of joserfc, exact issuer, `aud`, `azp`, `nonce`, `exp`, `iat`,
`at_hash` when present, and the RFC 9207 `iss` response parameter, checked on
the callback query before the token exchange and enforceable through a config
flag. The `acr` requirement is a configured list and may be empty, which is
logged explicitly at startup.

Back-channel logout is accepted on `/-/oidc/backchannel-logout`, admin host
only, with signature, `iss`, `aud`/`azp`, a recent `iat`, the logout event
claim, `sid` or `sub`, the absence of a `nonce` and a `jti` replay cache.

Because a Toon session outlives the IdP's own, a session is re-validated against
the IdP on the first request after `TOON_IDP_RECHECK_SECONDS` (default 900, `0`
turns it off) with `grant_type=refresh_token`. Success keeps the session and
rotates the stored refresh token; the `sub` of a new id token must match. A hard
refusal (`invalid_grant`) drops the session and the request continues as not
logged in. A network failure leaves the session alone and is retried after a
backoff. A refusal of our client itself keeps everyone logged in, because a
broken integration must not log the platform out, but raises an ERROR line and a
standing complaint that `/-/healthz` reports as `degraded` while still answering
200.

Code: `auth/oidc.py`, `auth/revalidation.py`, `platform/backchannel.py`,
`config.py`. Guarded by: `test_oidc.py`, `test_session_revalidation.py`,
`test_backchannel_logout.py`, `test_config.py`.

### 7.8 API security

The admin API refuses everything that does not demonstrably come from the
admin origin (§4a) and never sets CORS headers. Session-borne mutations need
the CSRF double submit against `__Host-toon-csrf`. A `Bearer` Authorization
header is accepted on the two deploy endpoints (§8), on the two CLI session
endpoints (`DELETE /cli/session`, `GET /cli/whoami`) and, with a CLI access
token only, on the admin routes that create a group or a site, link a
repository, and read, mark and answer feedback (§16.11). A CI ID token there
is a 401 (`TOKEN_INVALID`). A middleware answers 401 to any other request
carrying a Bearer, matched on the method and the exact path shape.
Deploy tokens no longer exist: a Bearer is either a CI ID token or a CLI access
token (§8). The feedback widget API on the content host authenticates with a
scheme of its own, `Authorization: Toon-Feedback <token>`, which that
middleware leaves alone (§16.8).

Code: `api/origin_guard.py`, `api/admin/`, `api/deploys/`. Guarded by:
`test_origin_guard.py`, `test_admin_api.py`, `test_deploy_api.py`,
`test_cli_api.py`, `test_cli_creation.py`, `test_feedback_admin_api.py`.

### 7.9 Audit

The audit log is append-only, with a pseudonymised actor (HMAC-SHA256 with a
pepper) and a truncated IP (IPv4 /24, IPv6 /48) stored as its network notation.
The full address sits beside it encrypted under a separate key
(AES-256-GCM, with the row id as additional authenticated data so a ciphertext
copied onto another row does not decrypt), so revealing it is a deliberate,
itself-audited act.

Logged: every refusal and login redirect, every `_version` access, every view of
non-public content (one row per page, not per asset), every admin action, every
deploy and preview teardown, the CLI pairing actions, and every access to the
audit log itself. Viewing public content is deliberately not logged, and
neither is what a visitor does under `/-/feedback/`, its refusals included
(`docs/audit-log.md`).

Logging is fail-open: a failed audit write never blocks the action it describes,
and the caller writes it as a separate step after the decision. The exception is
`write_strict`, used where the audit row is the only record that a
de-anonymisation happened at all; those callers fail closed, and a daily cap per
actor is serialised with a transaction-scoped advisory lock so two concurrent
requests cannot both pass it.

The vocabulary of actions, results and reasons, the retention tiers and the
lookup procedure live in `docs/audit-log.md`, which is the reference for them;
`audit/vocabulary.py` mirrors it and a test keeps the two in step.

A background job in the app (04:00 UTC) publishes the chain head to the
application log every night and then purges what has outlived its retention.
The database triggers decide what may go, the job only makes sure it goes.

Code: `audit/log.py`, `audit/pseudonymisation.py`, `audit/ip_crypto.py`,
`audit/vocabulary.py`, `audit/retention.py`, `audit/retention_job.py`. Guarded
by: `test_audit.py`, `test_audit_ip.py`, `test_ip_crypto.py`,
`test_audit_api.py`, `test_audit_vocabulary.py`, `test_audit_retention_job.py`.

## 8. Publishing: API, CI and CLI

The API base is `/-/api/v1` and addressing is on slugs, never UUIDs. The
slugs are the current ones: the API follows no old slug (§4), which answers
404, also a repeated request whose first answer got lost after a change of
address.

| Endpoint | Auth |
|---|---|
| `POST /-/api/v1/sites/{group}/{site}/deploys` | CI ID token, CLI token, or admin session with CSRF |
| `DELETE /-/api/v1/sites/{group}/{site}/previews/{ref}` | the same three |

A live deploy is the default; the form field `preview=<ref>` makes it a preview
deploy. Teardown is idempotent: already gone is a 204, not a 404. Both endpoints
are fixed verbatim by this section, because CI workflows and the action depend
on their spelling.

Beside them the session API for the SPA: groups, sites, access, invitees, keys,
repository link, members at both levels, versions, rollback, previews, feedback
(§16.11), platform members and the audit log. The same role rules (§3.6) apply.

**CI trust through OIDC.** There are no deploy tokens. CI authenticates with the
ID token its forge mints (`Authorization: Bearer <JWT>` from GitHub or Forgejo
Actions). The token is accepted when its `iss` is one of the configured issuers,
the signing key comes from that issuer's own discovery document and JWKS over
https, the algorithm is RS256, `exp` and `iat` are present within 60 seconds of
leeway, and `aud` is exactly one value: `TOON_BASE_URL`, or
`TOON_BASE_URL/-/sites/{site id}` with the id in its canonical lowercase
form, which binds the token to that one site. Every URL fetched on a provider's
behalf is built from configuration, never from a token or a request, which is
the SSRF defence, together with https only, no redirects, a timeout and a size
cap. The discovery documents and signing keys, of the CI issuers and of the
OIDC provider alike, are cached for an hour and then fetched again, so a key
the issuer withdraws stops being trusted within that hour.

A token that carries a `jti` (GitHub's do) is accepted once: a second use
within its lifetime is refused as replayed and audited as `CI_TOKEN_REPLAYED`.
Forgejo tokens carry no `jti`, so they are not checked for replay. The memory
of used tokens is in process, like the sessions, and is gone after a restart.

Whether a verified token may deploy is a separate question: a site names one
repository (`site_repositories`). A token matches when its issuer is that
repository's provider and host and it carries the stored `repository_id` (and
`repository_owner_id` where present), because ids survive a rename while names
do not; a transfer to another owner changes the owner id and is refused. A
Forgejo version that sends no ids matches on
`owner/repo` case-insensitively, and Toon then asks the Forgejo REST API whether
that name still has the stored ids, so a repository deleted and recreated under
the same name does not inherit the trust. A GitHub token without ids is refused.
The name a version records as its origin, and the name on the link, come from
the token's signed `repository` claim, and a token that vouched for both ids
marks entered ids as confirmed (`site_repositories.ids_confirmed`).
A live deploy additionally requires an event out of `push`, `workflow_dispatch`
or `schedule` and, when one is configured, the live branch; previews and
teardown accept any ref and any event. Claims copied into the audit record
(`repository`, `ref`, `sha`, `run_id`, `workflow`, `event_name`) are each length
capped.

**Bound to the site, not to its address.** The site links the repository,
and the workflow names the site's fixed id (`site-id` in the action,
`--site-id` for the CLI), which the CLI puts in the audience of the ID
token: consent from both sides, given to the site rather than to an address
that may come to mean another site (deleted and created again, a typo).
A bound token is used on its own site or not at all, and is decided on that
site first, whatever the address: a repository not linked to it gets the
401 `CI_AUDIENCE_MISMATCH` of a token for another site at every address,
the site's own included, so the address tells it nothing; the linked
repository publishes at the site's own address and gets 409 `SITE_MOVED`
with the site's current address anywhere else. No answer names a site id
(`api/deploys/auth.py`, `_target_site`). A token without
a site id is accepted only by a link from before migration 0004 of a
repository linked to that one site (`site_repositories.site_id_required`
false; a repository linked to several sites lost it on every link, since
one may be someone else's, planted from their own site), elsewhere it gets 403
`CI_SITE_ID_REQUIRED`, and only once the repository itself matched. That
exemption only ends: when another repository is linked, or when a site
admin requires the site id (`PUT .../repository/site-id-required`), and a
trigger of migration 0004 holds the database to that as well; and when the
site or its group gets another address, which the triggers of migration
0005 see to, so an exempt link cannot follow its site onto an address that
used to be another's. A member's CLI token is not affected: its role decides.

**CLI login.** `toon login` is an OAuth 2.0 device authorization grant (RFC
8628) Toon runs itself on top of the admin SSO login: the CLI asks for a device
code and a user code, the member approves that code in the SPA with a fresh
session, and the CLI trades its device code for a one-hour access token plus a
rotating refresh token (idle 30 days, absolute 90). A refresh token presented
after it was rotated means two parties hold the chain, so the whole session is
revoked; a token presented inside a ten-second grace is a retry, not theft.
Every secret is `<prefix>_<selector>_<secret>`, stored as selector plus SHA-256
and compared in constant time against a dummy hash for unknown selectors. A CLI
token acts as its member, with exactly that member's roles.

**Previews.** Upsert per (site, ref) in one atomic
`INSERT ... ON CONFLICT DO UPDATE`; the expiry is the last deploy plus 30 days.
A successful upsert removes the replaced preview version, row and file tree.
Teardown and the daily cleanup job (03:00) remove the preview row, its version
and its files, and the job also sweeps orphaned preview versions, stale `_tmp`
directories and expired CLI device authorizations and sessions, and frees the
old slugs whose redirect has ended (§4). Between the end of a redirect at
midnight and that run, an old address no longer redirects and cannot be claimed
yet. The same run purges site feedback past its retention (§16.12). Feedback
given on a preview stays when the preview goes.

**Directories without a row.** A publish renames its directory into place
before it inserts the row, and a cleanup commits the delete before it removes
the directory; a process that stops in between leaves a directory nobody
points at. The same job moves a site or version directory that has no row and
is older than 24 hours into `_reclaimed/`, where it no longer counts towards
the site's quota, and removes it from there after 7 days. Two brakes keep a
database that does not belong to the volume (a wrong `TOON_DB_URL`, a restored
backup) from emptying it: a site whose version rows match none of its
directories keeps all of them, and nothing moves when more versions would go
than stay. Both log an ERROR naming the directories. A site without any
version row (a first publish that stopped halfway, every version deleted) has
nothing to disagree with: its directories are ordinary orphans.

**Old live versions.** The same job removes, per site, the live versions
beyond the current live one and the newest others the site keeps (row and file
tree, one `version_cleanup` audit row per site). That number is the site's own
`sites.live_versions_kept`, which a site admin sets on the Versions tab, or
else the platform default `live_versions_kept` (5); 0 keeps every live version. It holds the site row
locked from reading `live_version_id` to the commit, and a rollback takes the
same lock before it looks its version up, so a version that becomes live while
the job runs is never removed.

**Deleting.** Deleting a site cascades over versions, previews, invitees, keys,
site members, the repository link and its feedback (items, replies and
screenshots, §16.12), and removes the site's directory. Deleting a
group does the same for every site in it, then removes the group and its
memberships; only a group admin may.

**Error contract.** Every API error is `application/problem+json` (RFC 9457, NL
API Design Rules) with `code` as the machine-readable extension: 401, 403, 404,
409, 413, 422, 429 with `Retry-After`. Invalid input is a 422 by house rule,
where the API Design Rules ask for 400: a deliberate deviation, kept for every
endpoint alike.

**Unknown fields.** A JSON body that only arrives over an admin session
(`SessionBody` in `api/schema.py`) refuses a field it does not know with a 422
that names it, and its schema says `additionalProperties: false`. Such a body
comes from the SPA, which ships with the server, or from a platform admin
calling by hand (the audit lookups and the IP reveal), so a stray key is a
client mistake or an attempt to set what the route does not offer. A body a
separately distributed client sends ignores an unknown field instead: the CLI
(creating a group or a site, linking a repository, the `toon login` device flow
and logout, setting the status of a feedback item and replying to one) may run
a different version than the server, and a newer CLI may send a field an older
server does not know yet.

The OpenAPI schema and a self-hosted docs
UI sit on `/-/api/docs`; the assets are local files because the CSP of §9 allows
no CDN. Both come in English and Dutch: `?lang=`, then the language a signed-in
member set on their profile, then `Accept-Language`, and otherwise English, the
same default as problem+json (`api/openapi_i18n.py`).

The workflow examples, the curl fallback and the `publish` action
(`actions/publish/`) are in `docs/publishing.md`.

Code: `api/deploys/`, `api/cli.py`, `api/admin/`, `api/errors.py`,
`api/docs.py`, `ci/tokens.py`, `ci/trust.py`, `ci/providers.py`,
`cli/service.py`, `ingest/service.py`, `previews/cleanup_job.py`. Guarded by:
`test_deploy_api.py`, `test_cli_api.py`, `test_cli_service.py`,
`test_ci_tokens.py`, `test_ci_trust.py`, `test_ci_providers.py`,
`test_cleanup_job.py`, `test_api_documentation.py`, `test_error_handlers.py`.

## 9. The admin interface

A Vue 3 SPA (runtime-only build, Vite base `/`), served by the application
itself from `TOON_SPA_PATH` on the root of the admin host, as pure ASGI
middleware outside the rate limit and inside the host separation. On the content
host it steps aside entirely.

The boundary is stated negatively, because the SPA is the fallback for the whole
host: the application claims `/-/...` (with only the SPA pages named in
`SPA_PAGE_PATHS` carved back out, so a typo in an API path stays a 404 instead
of coming back as the whole interface with status 200), the locations the web
pins down. Everything else is the SPA: an existing file is
served statically, every other path gets `index.html`.

Every SPA response carries a fixed header set: `Cache-Control`
(`immutable` for `assets/`, `no-cache` otherwise), the admin CSP,
`nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`,
`X-Robots-Tag: noindex, nofollow` (§5.9) and
`Cross-Origin-Opener-Policy: same-origin`.

The admin CSP is the strict regime beside the content CSP of §5.7:

```
default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:;
object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

`form-action` has no fallback to `default-src`, so it is spelled out; it is
widened for the logout form only, with the content origin and, when RP-initiated
logout is on, the issuer origin, because a browser checks every redirect of a
form navigation against it. Preconditions that follow from `script-src 'self'`:
no inline scripts in the built `index.html`, no legacy plugin, a runtime-only
Vue build, and a design system that needs no runtime `<style>` injection.

The regime follows the host, not the path: a path rule used to make a refusal on
the content host distinguishable from an ordinary neutral 404. Responses that
carry no CSP of their own get it from the security-headers middleware: HTML on
the admin host gets the full admin CSP plus `noindex`, JSON gets
`frame-ancestors 'none'`. `/-/api/docs` carries its own, identical but for
`style-src`, which allows `'unsafe-inline'` because Swagger UI puts style
attributes on its elements; `script-src` stays `'self'`.

The SPA reads the content origin from `/me` and builds every shareable URL
(public URL, secret link, preview link, CI snippet) on it, never on
`window.location.origin`. Route guard: `/-/platform` is platform administration,
and denial is the default when the session cannot be resolved. Interface
language is Dutch; the wire carries the English enum values.

Code: `platform/spa.py`, `security_headers.py`, `api/docs.py`,
`frontend/src/router.ts`, `frontend/src/format.ts`, `frontend/src/api/toon.ts`.
Guarded by: `test_spa.py`, `test_security_headers.py`,
`frontend/tests/build-output.test.ts`, `frontend/tests/router-guard.test.ts`,
`frontend/src/format.test.ts`.

## 10. Rate limiting

Fixed window with `Retry-After` and HTTP 429, no escalating penalties, plus a
global backstop limit per class as a DoS net. Classes and defaults
(`config.py`): login 10 per 60 s (backstop 1000), API 60 per 60 s (backstop
5000), content 600 per 60 s (backstop 20000), `code` 10 per 900 s (backstop
500) for the secret-link code page, and `feedback` 300 per 60 s (backstop
10000) for every path under `/-/feedback/`, so feedback neither spends nor
shares the budget of page views. The key is the member or token when
authenticated and the IP for anonymous traffic. `/-/healthz` counts against the content class
and SPA assets sit outside the middleware entirely.

The feedback widget sends no cookie, so its calls count per client IP, a
preflight like any other request. A 429 under `/-/feedback/v1/` carries the
widget's header set (§16.8), without which a shielded page could not read it.
An OPTIONS there counts but is never refused, also not when the counter is
broken: Fetch turns a refused preflight into a network error, so the readable
429 goes to the request after it.

The login class covers the login paths of both flows, from the same constants
the routes are registered with, so moving a route cannot silently change its
class. Behind the ZAD router, trusted-proxy configuration
(`TOON_BEHIND_PROXY`, X-Forwarded-For) is a required deployment setting.

Code: `ratelimit.py`, `net.py`, `constants.py`. Guarded by: `test_ratelimit.py`,
`test_app_integration.py`.

## 11. Deployment

Toon is **one component** on ZAD, because the application serves content, the
SPA and the host separation itself. Two web addresses on that one component
through `publish-on-web` with a dotted `domain-format` and `root-component`:
`beheer.toon.<domain>` and `toon.<domain>`. PostgreSQL and the content volume
are ZAD services, with the volume's size declared in the project file. How full
the volume is shows in `/-/healthz`, in the log and in the platform
administrator's view (§6).

The deployment runs `replicas: 1`: sessions and rate-limit counters live in
process memory and the content volume is read-write-once. The probe is
`tcp`; `/-/healthz` (§6) exists on the admin host for anyone who wants to
watch the pod. Secrets (OIDC key or client secret, database credentials,
audit pepper, audit IP key, session secret) come in as user env vars; nothing
sits in the image or the repository. Migrations run as a job in the portal or at
container start, because ZAD has no notion of a Job.

`deploy/toon-project.example.yaml` is the annotated project file,
`docs/deploying-on-zad.md` the step-by-step guide; `dev/compose.yml` mirrors the
topology locally (nginx as a dumb proxy, app, PostgreSQL, mock OIDC), see
`docs/local-development.md`.

Guarded by: `test_zad_project_file.py`, `test_workflows.py`,
`test_app_integration.py`.

## 12. The data model

PostgreSQL with Alembic migrations: the schema is migration `0001_base`, and
`0002_retention_and_repo_ids` adds `sites.live_versions_kept` and
`site_repositories.ids_confirmed`; `0006_versions_and_keys_indexes` indexes
`versions (site_id, created_at DESC)` and `access_keys (site_id)`.
`0011_feedback` adds the feedback settings `sites.feedback_enabled`,
`feedback_identity` and `feedback_live`, and the tables of site feedback (§16).
Tables: `members`, `groups`, `group_members`, `sites`, `site_members`,
`site_repositories`, `versions`, `previews`, `invitees`, `access_keys`,
`audit_log_entries`, `content_viewers`, `cli_device_authorizations`,
`cli_sessions`, `cli_refresh_tokens`, `group_slugs`, `site_slugs`,
`feedback_items`, `feedback_replies`, `feedback_screenshots`.

Rules that live in the database rather than only in the application:

- The access base is one shared PostgreSQL enum type used by
  `groups.default_access_base`, `sites.access_base` and
  `previews.access_base_override`; `role` likewise for `group_members.role` and
  `site_members.role`. The migration hardcodes the value lists on purpose, as a
  frozen snapshot, and a test holds them against `constants.py`, order included,
  because in SQL that order is the sort order of the type.
- A preview override is all three fields or none (`ck_previews_access_override`).
- A version comes from a member or from CI, never both and never neither
  (`ck_versions_origin`). A CI deploy records its repository as text, not as a
  reference, so unlinking a repository does not rewrite deploy history.
- `uq_previews_site_ref`, `uq_sites_group_slug`, `uq_members_sso_subject`,
  `uq_invitees_site_identifier`, `uq_access_keys_selector`,
  `uq_site_repositories_site`.
- `groups.slug` refuses the reserved slugs by CHECK; emails and invitee
  identifiers are lowercase by CHECK.
- `group_slugs` and `site_slugs` (migration `0005_slug_namespace`) hold every
  current and old slug of a group, or of a site within its group, with
  `retired_at` NULL on the current one. Their primary keys keep a slug unique
  across both, so an old slug stays reserved for its owner (`pk_group_slugs`,
  `pk_site_slugs`, which the API reports as a slug that was recently another's;
  a current slug still collides on `uq_groups_slug` or `uq_sites_group_slug`).
  Only triggers on `groups` and `sites` write them; a rename also makes the
  repository links of the renamed site or group require the site id. The app
  deletes an old slug once its redirect has ended (§8), or an old site slug
  when a group admin releases it for another site (§4). The downgrade drops
  both tables and with them every reservation at once.
- `sites.live_versions_kept` is NULL (follow `TOON_LIVE_VERSIONS_KEPT`) or a
  number of at least 0 (`ck_sites_live_versions_kept`).
- Access lives on the site; the group carries a default for new sites and a
  preview may override. A deploy never changes access policy.
- Deleting a site or a group cascades in the database and removes the file trees
  on the volume (§8).
- Site feedback (`0011_feedback`, §16). Its four enum types are frozen like
  the others. CHECK constraints (`ck_feedback_*`) carry what must hold
  whatever the application does, among them: an item sent without a name
  holds nothing about its author (`ck_feedback_items_author`), `resolved_at`
  is set exactly on a resolved item (§16.12), and `size_bytes` is the length
  of the stored bytes, which the quotas add up (§16.10). An item goes with its
  site, its replies and its screenshot go with the item, and a removed
  version or member only empties the reference (`ON DELETE SET NULL`). The
  screenshot sits in a table of its own, in the database (TDR-0013).

Append-only on the audit log rests on triggers: `audit_log_no_update` refuses
every UPDATE always, `audit_log_delete_after_retention` refuses the DELETE of a
row that has not reached its retention, and `content_viewer_delete_after_retention`
does the same at 180 days. The retention itself is a SQL function
(`audit_log_retention`), so shortening a term costs a schema change rather than
a setting, and the purge job asks the database what has expired instead of
carrying a term of its own. `guard_last_group_admin` keeps a group from losing
its last admin (§3.5).

**One database account.** Toon talks to the database under one account, for the
app, for Alembic and for the purge job, because the shared PostgreSQL service on
ZAD hands out exactly one user; locally it is run the same way. The separation
between a migration, a runtime and a purge account that an earlier design
assumed does not exist. The triggers therefore apply to the application itself
as well, and what they cost is stated plainly in `docs/audit-log.md`: the
account owns the schema, so an owner can disable a trigger. The triggers stop
mistakes and off-hand commands, not a deliberate owner.

Code: `alembic/versions/`, `models/`. Guarded by:
`test_migrations.py`, `test_audit.py`, `test_constants.py`.

## 13. Quality and evidence

- **Backend**: pytest against a real PostgreSQL in a container
  (Testcontainers over the Podman socket, `just test`). Required suites: the
  access matrix (base times extras times visitor class times
  live/preview/`_version`/no-live-version/expired-preview/revoked-key-with-cookie,
  including byte-level equality of the neutral 404 and the slash redirect
  behaviour) and the same matrix over HTTP for every visitor with a secret link
  in the query, serving path validation including percent-encoded traversal,
  fail-closed ingest, serving (redirects, MIME, 304, header set), rate limiting,
  the deploy API (CI trust, idempotent teardown, concurrent preview upsert,
  problem+json), storage lifecycle, host separation, origin guarding, OIDC, the
  audit log and the migrations. For site feedback (§16): the injection with each of its
  conditions (`test_feedback_inject.py`), the tokens and the
  consent pages with every refusal, the widget API with every code, the
  quotas under concurrency, the admin API with its roles and the CLI token,
  the purge, and a matrix over HTTP that holds every policy and visitor to
  the gate (`test_feedback_matrix.py`).
- **Frontend**: vitest (`npm test` in `frontend/`), including a check on the
  built `index.html` for inline scripts against the CSP of §9. The feedback
  widget is covered the same way, with a guard that imports every module under
  `src/feedback-widget/` and holds the source to its rules, and a check of the
  built bundle (`tests/feedback-widget-modules.test.ts`,
  `tests/feedback-widget-bundle.test.ts`).
- **E2E**: Playwright against the compose stack with mock OIDC on
  `http://localhost`, across both origins: logging in, creating a group and a
  site, uploading a dist fixture, deploying a preview through the API, viewing
  an older version through `_version`, creating a secret link and using it
  anonymously, the invitee flow including the return to the requested page after
  login, changing a site's address and following the old one with and without
  a secret link, and deleting a site (`e2e/`). Site feedback on a shielded
  preview with the real widget: feedback on a page, on a point with a
  screenshot and on a text, consent and "Mijn feedback", hiding the button,
  the Feedback tab, the CLI and an axe check of the open widget
  (`e2e/tests/feedback.spec.ts`).
- **Lint**: `just lint` runs `ruff check` over `src` and `tests`.
- **Security**: the living checklist in `docs/security.md` carries the items of
  this document with their status. DPIA and a pentest are conditions for
  production.

## 14. Out of scope, deliberately

Subdomain or wildcard per site; magic-link login for externals (secret links
cover externals, invitees need SSO); custom domains per group; object storage
(not planned: content stays on the volume, #42); the API
following an old slug (a deploy to an old address fails at once, §8); moving a
site to another group; releasing an old slug early; email notification
(inviting sends no mail, the administrator shares the URL themselves); central
session revocation (deactivating works immediately because member status is
checked per request).

For site feedback (§16): notifications of new feedback (by mail, in
Mattermost or otherwise), one inbox across sites, acting on several items at
once in the admin interface (the CLI marks several at once), assigning items,
labels, search, editing an item after it was sent, feedback on `_version`
views and on a site's own 404 page, pins of other people's feedback for
visitors, screenshots made on the server, real images in screenshots of a
shielded site, feedback settings of a preview's own, and a fresh page token
without reloading the page.

## 15. References that no longer have a subject

Nothing cited by the code has lost its subject. Three rules from the earlier
design did disappear and are named here so an older reference is not left
guessing:

- **Deploy tokens** (`plak_<prefix8>_<secret32>` bearer tokens with a group or
  site scope, under the old §7 and §8) no longer exist. CI authenticates with an
  ID token from its forge and a person with a CLI token from `toon login` (§8).
- **The X-Accel-Redirect datapath and the nginx header discipline** under the
  old §5, together with the parity test between the dev path and the nginx path,
  are gone: the application serves content itself and there is only one path
  (§5).
- **Separate database accounts** for migrations, runtime and purging under the
  old §12 are gone: there is one account, and the triggers carry the guarantee
  (§12).

## 16. Site feedback

A site admin can let visitors give feedback on the pages of a site: a remark
about a point or an area on a page, a selected text or the page as a whole, with a
screenshot if the visitor wants one. For that Toon adds a widget of its own to
the site's HTML pages on the content host, keeps what comes in with the site,
and lets the site team handle it on the site's Feedback tab or with
`toon feedback`. The stored files of a site do not change: the widget goes
into the answer, not into the version.

### 16.1 Settings and roles

Three columns on `sites` hold the setting. An effective site `admin` sets all
three in one `PUT /sites/{group}/{site}/feedback-settings`, with a session and
CSRF, audited as `site_feedback`:

- `feedback_enabled`, false by default: whether the widget goes into the
  site's pages at all.
- `feedback_identity`, `account` by default: who may give feedback. The levels
  are ordered and each takes the author kinds of the ones before it:
  `account` takes feedback from an SSO account only, `name` also with a name
  the visitor types, `anonymous` also without a name.
- `feedback_live`, false by default: whether the live site collects feedback
  as well as the previews.

A preview always follows the site's own setting: an override (§7.2) changes
who may look at a preview, never whether it collects feedback. Switching
feedback off takes the widget out of the next page view, and every widget call
for the site is refused from that moment (§16.5). What was given stays.

The settings sit at the top of the Feedback tab. A change saves at once and
the switches wait while it is on its way. A save that fails puts the switches
back and says so.

Every allow of the gate carries the site's id and its feedback setting, read
from the site row the gate loads anyway, so a public page view stays one
query, also with the widget in it. The roles are in §3.6. The email address
of a submitter is for editors and admins only (§16.11).

Code: `models/publication.py`, `constants.py`, `access/decision.py`,
`access/gate.py`, `api/admin/feedback/routes.py`,
`frontend/src/components/site/FeedbackSettings.vue`. Guarded by:
`test_constants.py`, `test_access_gate.py`, `test_feedback_admin_api.py`,
`test_migrations.py`, `frontend/src/components/site/FeedbackSettings.test.ts`.

### 16.2 Which pages get the widget

A **feedback route** is a live or preview route, never `_version`, of a site
with feedback on, where the route is a preview or the site collects on its
live site too. On such a route a **feedback page** is an HTML file (what
`mime.determine` calls `text/html`) answered with 200, except the site's root
`404.html`, also when that is asked for by name. A version's own 404 page,
served with status 404, never is one.

Only a navigation gets the widget: a request with `Sec-Fetch-Dest: document`,
a header page script cannot set (§7.6). A browser that sends no fetch metadata
never gets it, and a script, a `fetch` or an iframe gets the file as
published. A navigation to a feedback page gets the widget unless:

- `TOON_BASE_URL` is not set, since the widget links the privacy statement
  on the admin host,
- the visitor hid the button on this site: a value of the cookie
  `__Secure-toon-feedback-off` opens as an unexpired hide mark for this site
  (§16.7),
- the page is larger than 10 MiB decoded (`FEEDBACK_INJECT_MAX_BYTES` in
  `constants.py`),
- it starts with a UTF-16 byte order mark, since the tag is ASCII,
- there is no place for the tag in its first 64 KiB,
- the bundle is missing (§16.4), no token can be minted for the page
  (§16.5), or its `<config>` would be longer than the 16384 characters the
  widget reads, even without a way back.

Without the widget such a navigation gets the file as published, under the
caching rules of §16.3 all the same.

The tag goes in front of the document, right after the doctype. It is found by
skipping only what the HTML tokenizer lets pass before a doctype without
leaving standards mode: a UTF-8 byte order mark, ASCII whitespace, and
comments and bogus comments as WHATWG HTML reads them (13.2.5). Without a
doctype the tag goes in front of the first other token. Never in front of a
doctype, which would put the page in quirks mode, and never further on, where
a script string, a comment, an attribute value or a template could swallow
it. A page whose first 64 KiB end before that is decided gets no widget. The
parser moves a `defer` script found before `<html>` into the head, and the
page keeps its mode.

```html
<script src="/-/feedback/widget.<hash>.js" defer data-toon-feedback="<config>"></script><script>/* place script */</script>
```

The place script follows the widget's tag inline when Toon has one (built
from `frontend/src/feedback-place/`, under 1 KiB, read once at startup and
refused when it is not ASCII or holds `<script`, `</script` or `<!--`). The
widget is deferred, so it runs after the page's own scripts, and a single-page
slide deck reads its slide from the fragment (`#/7`) once, as it starts, and
then rewrites the address with `replaceState`. The place script runs before
those scripts: on a `#toon-feedback=` address it hands the link to the
widget's tag in `data-toon-feedback-place`, and replaces the address with the
page's path and query plus the fragment the place was given at (`f`, below),
so the deck opens on that slide. Anything else leaves the page alone. Inline,
it costs a page no request, and the content policy allows inline script on
every content route, shielded or not. A site whose own `<meta>` policy
refuses inline script blocks it, and then the widget finds the link in the
address as before, unless the page has rewritten it by then.

`<config>` is base64url without padding over the compact UTF-8 JSON of the
widget's configuration: `v` (1), `token` (§16.5), `api` (`/-/feedback/v1`),
`identity` (the site's setting), `account` (whether the token carries a
consented session), `accountUrl` and `showUrl` (the consent page and the show
route, with the way back to the page as `returnTo`, and `accountUrl` with a
link ticket in `link`, §16.6), `hideUrl`, `privacyUrl` (`TOON_BASE_URL` plus
`/-/privacy`), `screenshotMaxBytes` (§16.9) and, when Toon has an editor,
`editorUrl` (§16.15). It holds no name, no email address and no readable
session reference. A page's own scripts can still tell from it whether the
visitor consented on this site (`account`) and, on a page that is not public,
whether a secret link let them in (`link` in `accountUrl`, and the token's
length, which grows with `key`, `ses` and `acc`). Those scripts can also
rewrite the attribute before the widget reads it, so the widget refuses a
configuration that does not hold up: an address that leaves `/-/feedback/`, an
`editorUrl` that is not `/-/feedback/editor.<hash>.html`, or a privacy
statement that is not an absolute http(s) URL. Without `TOON_BASE_URL` Toon
therefore puts no widget in a page at all.

The way back is what the consent page, the hide form and the show link
take as `returnTo` (§16.6, §16.7): fewer than 2048 characters, unchanged by
the login's rule, on a site's path, without a `.` or `..` segment (also
percent-encoded, as `%2e%2E`, which a browser resolves before it requests the
path, so `/g/s/../../other/` would name one site and lead to another), and
without any `key`. It is the page's path
and query without any `key` when they pass and the `<config>` fits the
widget with them. Otherwise it is the page's path alone, and the visitor
comes back to the page without its query. When even that does not pass or
fit, the page still gets the widget, without `accountUrl` and `showUrl`: it
offers neither feedback with an account nor hiding the button, and takes
feedback with a name or anonymously as before.

Code: `serving/feedback_inject.py`, `serving/router.py`,
`frontend/src/feedback-widget/config.ts`. Guarded by:
`test_feedback_inject.py`, `test_parity.py`,
`frontend/src/feedback-widget/config.test.ts`.

### 16.3 The injected page: caching, size and streaming

The token in a page belongs to this visitor and this view (§16.5), so no
cache may keep or confirm a copy:

- Every answer for a feedback page, the 304 included, carries
  `Sec-Fetch-Dest` in `Vary`, next to `Service-Worker` (§7.4) and, for a page
  kept compressed, `Accept-Encoding`. No cache hands a navigation what another
  request left.
- A navigation never gets a 304 and always gets `Cache-Control: no-store`,
  whether the widget goes in or not.
- Every other request keeps the caching of §5.4 and §5.5, 304 included.

A page with the widget in it is a representation of its own: no `ETag`, no
`Content-Encoding` and no `Accept-Ranges`, and `Range` and `If-Range` are
ignored. Every other header of the file response stays, the CSP and the
`Referrer-Policy` among them. `Content-Length` is the decoded size plus the
length of the tag.

Every page the widget goes into is decoded in Toon's one process on every
view, so the cost is bounded before anything is decoded. The decoded size is
the file's own size, or the ISIZE trailer of its gzip copy (RFC 1952) for a
page kept compressed only (§5.4a). A page above 10 MiB
(`FEEDBACK_INJECT_MAX_BYTES`) is never decoded, and finding the place of
the tag decodes at most the first 64 KiB and skips its comments in one pass.

The page then streams, decoded in pieces of at most 512 KiB, each read in a
worker thread, with the tag spliced in and without compression, so memory is
bounded by the piece and not by the page. A brotli copy is never used for it:
the gzip copy is decoded whatever the request accepts. A page that turns out
longer or shorter than its `Content-Length` (a lying ISIZE, a truncated copy)
breaks the response off rather than send a body that disagrees with it. A
HEAD runs the same decision and so reads the first 64 KiB and never more,
because the headers differ with and without the widget.

Code: `serving/feedback_inject.py`, `serving/response.py`,
`serving/router.py`, `constants.py`. Guarded by: `test_feedback_inject.py`,
`test_serving.py`, `test_head_requests.py`.

### 16.4 The widget in the page

The bundle is one minified classic script, an IIFE that defines no global,
for ES2020, without a sourcemap. `npm run build` builds it after the SPA
(`frontend/vite.widget.config.ts`) into `dist/feedback-widget.js`. Its legal
comment names Toon's licence (EUPL-1.2), the repository and the MIT notice of
`html-to-image`, which it bundles for the screenshots, pinned at exactly
1.11.13.

Toon reads the file from `TOON_SPA_PATH` once, at startup, and serves it at
`/-/feedback/widget.<hash>.js` on the content host, where `<hash>` is the
first 16 hex characters of its SHA-256, with
`Cache-Control: public, max-age=31536000, immutable` and `nosniff`. Any other
name there is the neutral 404, so a page always loads the bundle it was
served with. A missing, unreadable or empty file does not stop Toon: pages go
out without the widget, and one warning says why. A new build needs a
restart. The fields for a comment and a reply load a second bundle, a
Markdown editor, in a sandboxed frame of its own (§16.15).

In the page the widget keeps to itself. It adds one element,
`<div data-toon-feedback-host>`, at the end of `body`, with an open shadow
root that holds its markup and its one `<style>`. The host resets whatever
the page sets on it (`all: initial`), takes no room in the layout and sizes
everything in px, so a site's root font size changes nothing. It reads its
configuration once and removes the attribute. It uses no `localStorage`,
`sessionStorage`, `document.cookie` or `indexedDB`, registers no custom
element that could clash with a site's own, writes no `innerHTML` and calls
no `eval`. Key and input events that start inside it stop at the host, so a
site's own shortcuts do not eat what someone types. It is hidden in print.

Its language is the page's `<html lang>` when that is Dutch or English, else
the first of the browser's languages that is, else English. It sends that as
`Accept-Language`. Words of the page that it quotes or announces keep the
language the page gives them.

**The screenshot** is made in the browser with `html-to-image`, of the
viewport, with the widget left out: by itself for a point or an area, and for
a text or the whole page only when the visitor asks. The copy of the page is
moved to the scroll position by negative margins on its root, not by a
transform, so fixed and sticky parts stay where the window shows them. In the
copy alone, the overflow of the root and the body is let show, also against
an important declaration in the page's own style: otherwise a rule such as
Material for MkDocs's `html { height: 100%; overflow-x: hidden }` clips the
copy to the first window, and the picture of a page scrolled down is blank.

The screenshot fetches images from the page's own origin only
(`mode: same-origin`). On a shielded page that origin is opaque, so the
browser refuses each of those fetches before it goes out, and the images, the
site's own included, appear as grey areas, which the widget says. Such a fetch
would carry no cookie of the site, so where the page is not public the gate
would log a refusal or a login redirect for three years with the visitor's IP
address (§16.13). Not closed: `html-to-image` keeps no element's scroll
position, so a part of the page that scrolls on its own, such as the main
pane of an app shell or an NLDD app in nested scroll mode, shows from its top
in the picture. A mark drawn in that part lands on other content, and the
notice beside the picture still says it shows what is on the screen.

**The menu** follows the site's setting and the token: pointing at a spot or
an area, the selected text (also from a button beside a selection), the page
as a whole, feedback with an account ("Mijn feedback" and a way to stop with
a consented session, the way to the consent page of §16.6 without one) and
hiding the button (§16.7). On a site that takes feedback with an account
only, a visitor without a consented session gets "Feedback geven met je
account" (give feedback with your account) and hiding the button, nothing
else: nobody starts on a remark they cannot send. On a page without a way
back (§16.2) the menu has no way to the consent page or to stop, and no
hiding. Every menu, and the
composer under its buttons, links the privacy statement, in the same tab on a
shielded page. That statement and the consent page say what a sign-in keeps:
one that fails, that the visitor stops, or whose session the identity
provider ends early keeps the IP address three years in the access log
(§16.13, `docs/audit-log.md`). While text typed in the widget has not been
sent, a link that leaves for a page of Toon in the same tab says the text is
lost there. After sending without an account the widget says that the giver
cannot see the team's replies.

**Keyboard and pointer.** An overlay (pointing, choosing a part, covering)
holds Tab between itself and its bar, takes back focus that a page script
moves to the page, and says in the bar how it works with a pointer and, once
the keyboard is in use, with the keyboard. The bar takes at most half the
window and scrolls beyond that, so the picture keeps the other half with any
font, also at 400% zoom. One pointing mode takes a point or an area: a press
that moves less than `POINT_SLOP` (6 CSS px) is a point, a press that moves
further draws a rectangle, and a rectangle smaller than `MIN_AREA` (12 px) on
a side is a point at its centre. On touch the overlay lets the page scroll and
zoom, so a drag scrolls. Dragging is never the only way to an area (WCAG
2.5.7): "Gebied zonder slepen" (area without dragging) opens a frame over the
live page, set with two clicks or taps on its corners, a drag, or the arrow
keys and Enter. In the composer, "Ander punt of gebied kiezen" (choose another
spot or area) points again and keeps what was typed. Escape steps back one
step. Below 600 px the panel is a sheet at the bottom that holds Tab. On a
wide window the panel is not modal, and the button and the panel move out of
the way of a page element with keyboard focus (WCAG 2.4.11), following focus
into open shadow roots, while focus in a closed one counts on its host.

While "Mijn feedback" (§16.8) is open, each item given on a spot, an area or a
text of this page has a pin there: a ring like the one that marks a place
from a link (§16.11), which lets presses through to the page and which the
keyboard reaches as a button that leads to the item. The pins lie under the
button, the panel and the sheet. The ring and the marks of a place from a
link lie over the button and under the panel. An item given at a fragment
that names no element of the page (`page.fragment`, §16.9), such as `#/7` on
a slide deck, gets a pin, and a link to it its marks, only where its own
element or passage shows, never at its page coordinates: on another slide it
has none, and it is not lost either. The list names that fragment after the
page and links to it. While pins or a place show, the widget measures them
again in the frame after each key and click on the page, and when the
fragment of the address changes, which it looks at four times a second.

Code: `frontend/src/feedback-widget/`, `frontend/src/i18n/widget.nl.ts`,
`widget.en.ts`, `frontend/vite.widget.config.ts`, `serving/feedback_widget.py`,
`serving/feedback_routes.py`, `main.py`. Guarded by: the tests under
`frontend/src/feedback-widget/`, `frontend/tests/feedback-widget-modules.test.ts`,
`frontend/tests/feedback-widget-bundle.test.ts`,
`test_feedback_widget_bundle.py`, `test_feedback_api.py`,
`test_app_integration.py`, `e2e/tests/feedback.spec.ts`.

### 16.5 The page token

The page token is the widget API's only credential, and nothing else takes
it: it opens no page, no asset and no other route. It names the site (by id)
and the target the page belongs to (`live`, or `preview` with its ref), the
version served, the content session, the secret link the gate let the
visitor in with, and an expiry twelve hours on. The session is in it in one
of two roles, never both: as `ses` when its visitor consented to feedback
with their account on this site (§16.6), otherwise as `acc` when the session
is what let the visitor in (the page's effective access, an override
included, is not public and no secret link did it). `acc` is there for the
gate, so a signed-in visitor who never consented can still send with a name
or without one where only their session lets them see the page. A public
page never carries `acc`. Either role needs a request that carried the
site cookie once: one that carries it twice, one of them possibly planted
from elsewhere, gets a token without a session. The secret link is in it
only when the gate let the visitor in through the key cookie. Minting
stores nothing, and the token never goes
into an address (§16.6).

Feedback with an account is offered on every feedback page, served with the
shielding (the sandbox of §5.7) or without it. A page without the shielding
shares its origin with every other site without it, whose pages can open it
in a window and read the token from its configuration. With `ses` in it they
can send feedback in that visitor's name, and read, answer and delete that
visitor's own feedback, until the token expires. That risk is accepted
(TDR-0015, `docs/security.md`).

The token is sealed, not signed, because it sits where a site's own scripts
read it and has to hide the session it names: AES-256-GCM under a key derived
with HKDF-SHA256 from `TOON_SESSION_SECRET` (no salt, info
`toon-feedback-v1`), written as base64url of a random 96-bit nonce, the
ciphertext and its tag, with its purpose (`toon-feedback-page-v1`) as the
associated data. A link ticket, a consent nonce (§16.6) or a hide mark
(§16.7) therefore never opens as a page token, nor the other way round, and a new session secret ends
every token. Parsing is as strict as minting, and minting runs the same
check: exactly the fields minting writes, each in its one spelling, never
`ses` and `acc` together, an expiry at most twelve hours and a minute ahead,
and at most 4096 characters.

Every widget call checks the token again against what holds now. A session
it names, in either role, must still exist as a content session and pass IdP
revalidation (§7.7), which the session middleware cannot run for a request
without cookies, and in `ses` it must still hold the consent. The gate must
allow the site, found by its id, at its current address for a visitor built
from that server-side state alone, on a target that still collects feedback,
and must decide on the token's own site. Anything else is the one
`FEEDBACK_UNAVAILABLE` (§16.8). So a logout, an expired session or token, a
session the IdP ended, a withdrawn consent, a revoked secret link, a removed
invitee, a deactivated member, an expired preview, narrowed access and
feedback switched off each end what an outstanding token can do at once. A
redeployed preview or a pruned live version only leaves a new item without
its version (§16.10). A new address for the site or its group leaves the
token valid, and a create from a page served at the old address is 422
`FEEDBACK_PAGE_MOVED` (§16.9).

Only a session in `ses` stands behind what needs an account: the author
fields, the visitor's own items, replies and deletes. Nothing of a session in
`acc` goes with an item: its `sub` only keys the author counter (§16.10).

Every refusal answers the same, so each process counts the page tokens that
the widget API and the hide route (§16.7) refuse, per reason: the token does
not open (`unopened`), it expired (`expired`), its session is gone or fails
revalidation (`session`), its consent is gone (`consent`), the gate refuses
(`gate`), the target takes no feedback (`feedback_off`), or its site is gone
or another site answers at its address (`site`). A request without a token
counts for nothing. When a window of ten minutes ends with more than 100
refused tokens, the process logs one WARNING for it with the total and the
count per reason, and nothing about a visitor: no address, no `sub`, no site,
no token. A window ends at the first refusal after it, or within a minute by a
sweep. The counts live in memory and start again on a restart.
`docs/deploying-on-zad.md` names the line to alert on (BIO2, ISO 27002:2022
8.16).

Code: `serving/feedback_seal.py`, `serving/feedback_token.py`,
`serving/feedback_inject.py`, `serving/feedback_access.py`,
`serving/feedback_refusals.py`, `auth/sessions.py`, `auth/revalidation.py`.
Guarded by: `test_feedback_seal.py`, `test_feedback_token.py`,
`test_feedback_inject.py`, `test_feedback_api.py`, `test_feedback_matrix.py`,
`test_feedback_refusals.py`, `test_sessions.py`,
`test_session_revalidation.py`, `e2e/tests/feedback.spec.ts`.

### 16.6 Consent for feedback with an account

A page token carries the visitor's content session for feedback with an
account (`ses`, §16.5) only after the visitor said yes, on a page Toon renders
outside every site's realm, to giving feedback on this site with their
account. A site's own scripts read the token as well as the widget does.
Without that step they could send items in the visitor's name, and so put the
visitor's name and email address in front of the site team without the
visitor knowing.

- `GET /-/feedback/account?returnTo=<page>` answers a top-level navigation
  only (`Sec-Fetch-Dest: document`). `returnTo` passes the login's rule
  (§7.6), has to be a site's path without a `.` or `..` segment (§16.2) and
  loses every `key`. A request that
  carries the anchor cookie more than once is the neutral 404: the session
  read could be one planted from elsewhere, and a login cannot clear a cookie
  at another path. Without a content session the visitor goes to `/-/login`
  first. With one, the gate decides on the page for that session's visitor,
  with the secret link of a link ticket when the address carries one (below),
  and anything but an allow on a feedback route is the neutral 404.
- The key cookie of a secret link belongs to the site's path, or the
  preview's, and never reaches `/-/`. So where the page token carries a secret
  link and no consented session, the way to the consent page (`accountUrl`,
  §16.2) carries `link`: a link ticket sealed like the page token under a
  purpose of its own (`toon-feedback-link-v1`), with the key id, the site id
  and the page token's own expiry, and nothing about the visitor. Unlike the
  page token it goes into an address, and so into the browser's history. On
  its own it opens the consent page and nothing else: the site's pages still
  take the key cookie. It rides through the sign-in inside the login's
  `returnTo`. A `link` that comes twice, does not open or has expired is the
  neutral 404 before any sign-in, and so is a ticket whose site is not the one
  the gate decides on at `returnTo` after it. The gate decides with the
  ticket's link beside the session, so a link revoked, expired or switched off
  since lets in only whom the session alone lets in. A consented session gets
  no ticket.
- The page carries the platform policy (§5.7) with its form, plus
  `sandbox allow-forms`. Its origin is therefore opaque: no script on the
  content host can read it, fill it in or press its button, and
  `frame-ancestors 'none'` keeps it out of frames. A site can still send the
  visitor's tab there between the two presses of a double-click, or open it
  behind a popup that closes on the first press. So the form keeps
  `visibility: hidden` for its first second through a CSS animation with
  `step-end` timing (with any easing it is visible as soon as the animation
  runs), and consents only with its required box ticked, "Het siteteam mag
  bij mijn feedback zien wat hierboven staat." (the site team may see what is
  listed above with my feedback): no single press gives consent. The page
  runs no script and is `no-store`, `Referrer-Policy: no-referrer` and
  `noindex`. It speaks the language `Accept-Language` asks for, as the code
  page of a secret link does, Dutch by default, with `Vary: Accept-Language`.
- It says who the visitor is signed in as (their name, or their account id
  without one), names the site and what the site team will see with every
  item: their name and an email address the IdP marked verified, each when
  there is one, or else only that it comes from someone with an account. The
  address is for the site's editors and admins only, and the team never sees
  the account id. It says how long the consent lasts and that items keep
  those details as long as they exist, what signing in keeps
  (`docs/privacy.md`), and, for a visitor who came through a secret link, that
  their page views on the site after the consent carry their account in the
  access log (`docs/audit-log.md`). With `TOON_BASE_URL` set it links the
  privacy statement. A visitor pushed into somebody else's session sees a
  foreign name or account id there. The widget's composer points back to
  this page and names nothing itself.
- Its form posts to `/-/feedback/account` with a sealed nonce (purpose
  `toon-feedback-consent-v1`) that binds the session, the site, the secret
  link of the ticket when the page had one, and the way back for ten
  minutes. A POST from an opaque origin carries no Lax cookie, so the nonce
  is the credential. The POST takes a top-level navigation and an urlencoded
  body of at most 16 KiB that arrives whole and carries `agree` exactly once,
  as `agree=1`. It revalidates the session at the IdP, asks the gate again
  for the way back's page, with that link, and needs the site it decides on
  to be the nonce's. Then the session records the site (at most 32 sites per
  session) and a 303 goes to `/-/login?returnTo=<the way back>`. It sets no
  cookie: that GET is a top-level navigation again, so the browser sends its
  Lax anchor, and the login shortcut (§7.6) sets the site cookie from that
  anchor when it is the only one the request carries. A nonce posted from any
  other browser stores a consent for the session it names, which its holder
  could have given anyway, and plants nothing. Every refusal is the neutral
  404.
- `GET /-/feedback/account/stop?returnTo=` ends the consent for the site at
  `returnTo`, also at an old address that still redirects, in every content
  session that a correctly signed site cookie or anchor on the request names.
  A site cookie reaches `/-/` only when it was set from elsewhere, and a
  second anchor can shadow the visitor's own, so none of them is taken for
  the visitor's: withdrawing only takes away. It takes a top-level navigation
  and answers the same 303 whether there was anything to end or not.

Consent ends with its session: the rotation at a login, a logout, the expiry
after twelve hours and a back-channel logout all end it.

Accepted:

- Once a visitor consented, the site's own scripts, and whatever they load,
  can act for them on this site's feedback until the session or the consent
  ends: send in the visitor's name, read the visitor's own items with the
  team's replies, reply and delete. The consent page names what the site team
  sees with every item.
- A content cookie planted from elsewhere (§7.6) that is the only one a
  request carries, for a session that consented to this site already, cannot
  be told from the visitor's own.

Code: `serving/feedback_consent.py`, `serving/feedback_token.py`,
`serving/feedback_inject.py`, `auth/sessions.py`, `auth/revalidation.py`, `platform/pages.py`, `i18n.py`.
Guarded by: `test_feedback_consent.py`, `test_feedback_token.py`,
`test_feedback_inject.py`, `test_access_gate.py`, `test_sessions.py`, `test_session_revalidation.py`,
`test_i18n.py`, `e2e/tests/feedback.spec.ts`.

### 16.7 Hiding the button

The widget's menu offers "Knop verbergen" (hide the button) with two choices,
also to a visitor without a consented session on a site that takes feedback
with an account only (§16.4). "Een minuut" (one minute) hides it on this page
for 60 seconds, in the widget's memory alone, and gives focus back to the page
element that had it, without scrolling the page. "Tot je de browser sluit"
(until you close the browser) first shows the link that brings it back, then
posts a form to `/-/feedback/hide` with the page token and the page as
`returnTo`. That POST takes a top-level navigation, an urlencoded body of at
most 16 KiB that arrives whole (§16.6), a token that opens and is unexpired,
and a `returnTo` at the current address of the token's site, so the form of a
page served before a new address is the neutral 404. It does not ask the gate
again, so a visitor whose session ended can still hide the button. It sets
`__Secure-toon-feedback-off` with `Path=/{group}/{site}/`, `Secure`,
`HttpOnly`, `SameSite=Lax` and no expiry, and answers with a 303 back. Its
value is a hide mark: the site's id and an expiry twelve hours on, sealed like
the page token under a purpose of its own (`toon-feedback-hide-v1`). While a
value of that name opens as an unexpired mark for the site, the site's pages,
live and previews alike, get no widget (§16.2). So the button stays away until
the browser closes, twelve hours at most.

The server reads every value the request carries under that name, from the
raw Cookie headers, because `request.cookies` keeps one value of a name sent
twice. A value that does not open, has expired or names another site does
nothing, whatever the script of a page without the shielding (§5.7), or a
sibling host, writes under the name. A real mark does hide the button,
wherever it came from: anyone who can get a page token of the site can get
one (below), and a mark copied from one browser into another hides the
button on its one site, for twelve hours at most. The show link clears the
cookie on this host alone, at the site's path and at `/`. So a mark that the
script of a page without the shielding writes at another path that reaches
the site's pages (the group's path, the site's path without its slash, or a
path below it) outlasts it, and so does one that a sibling host under the
same registrable domain (another `*.rijks.app` host) sets with
`Domain=rijks.app`, at the site's path or at `/` too. Each keeps the button
away until the mark expires, twelve hours at most, and a page or host that
keeps writing fresh marks keeps it away for as long as it does. The impact
stays the same: the button stays away for that visitor on that site.

`GET /-/feedback/show?returnTo=` clears the cookie at the site's path and at
`/` on a top-level navigation and goes back. The admin SPA has no such link:
its Feedback tab is for the site team, not for the visitors who hide the
button. The cookie names no person: it holds the site and the moment the
hiding ends.

Accepted: any page that can get a page token of a site can hide the button on
that site, until the browser closes and for twelve hours at most, by posting
the form itself. A public site's page hands a token to anyone. No header check
can close this: the widget's own post from a shielded page is a cross-site
navigation with `Origin: null`, which a foreign page can send as well.
Closing the browser undoes it, and so does the show link for a mark at the
site's path or at `/` on this host.

Code: `serving/feedback_consent.py`, `serving/feedback_token.py`,
`serving/feedback_inject.py`, `auth/sessions.py`,
`frontend/src/feedback-widget/widget.ts`, `views.ts`. Guarded by:
`test_feedback_consent.py`, `test_feedback_inject.py`,
`test_feedback_token.py`, `test_sessions.py`,
`frontend/src/feedback-widget/widget.test.ts`.

### 16.8 The widget API

Every route sits under `/-/feedback/` on the content host and checks the host
itself (§4a). The router's last route, an ASGI app that matches every method,
answers every other method and path under the prefix with the neutral 404, so
no path there ever gets FastAPI's 405 or 422. A HEAD there is the neutral 404
as well.

| Method and path | Credential | Answer |
|---|---|---|
| `GET /-/feedback/widget.<hash>.js` | none | the bundle (§16.4) |
| `POST /-/feedback/hide`, `GET /-/feedback/show` | a page token in the form (hide), none (show) | §16.7 |
| `GET` and `POST /-/feedback/account`, `GET /-/feedback/account/stop` | a content session (GET), the nonce (POST) | §16.6 |
| `OPTIONS /-/feedback/v1/items`, `.../items/{id}`, `.../items/{id}/replies` | none | 204, the preflight |
| `POST /-/feedback/v1/items` | a page token | 201 `{id, status, createdAt}` |
| `GET /-/feedback/v1/items` | a page token with a consented session | 200 `{items, more}`, the visitor's own items |
| `POST /-/feedback/v1/items/{id}/replies` | a page token with a consented session, the visitor's own item | 201, the reply |
| `DELETE /-/feedback/v1/items/{id}` | a page token with a consented session, the visitor's own item | 204 |

A call authenticates with `Authorization: Toon-Feedback <token>`, a scheme of
its own, so the bearer guard of §7.8 leaves it alone. Nothing under
`/-/feedback/v1/` reads a cookie, and the widget sends none
(`credentials: 'omit'`). The visitor's own items, under "Mijn feedback" (my
feedback) in the widget, are the items this session's `sub` sent with an account
on the token's site: newest first, at most 20, each with its 10 newest
replies, oldest first, and `replyCount`. `more` is true when older items were
left out, and the widget then says so. An item given on a preview that is gone or past its `expires_at`
carries `page.url` null, since the gate answers that preview with the
neutral 404. A reply of the team names the member by the name the member has
now (§16.11), and none once the member is deleted, never an email address. A token
without a consented session gets 403 `FEEDBACK_ACCOUNT_REQUIRED` for those
three routes, also one that carries a session for the gate alone (§16.5).
Deleting an item takes its replies and its screenshot along. No route serves
a screenshot back.

**CORS.** The widget calls the API from the site's own page, whose origin is
opaque when the site is shielded (§5.7), so its requests arrive with
`Origin: null`. Every answer of the widget API's routes therefore carries
`Access-Control-Allow-Origin: *`, never a reflected origin and never
`Access-Control-Allow-Credentials`. That is safe because the API takes
nothing ambient: it reads no cookie and the browser sends none, so whatever a
page anywhere can do with the API, a client outside a browser can do with the
same token. The preflight (204) names `Authorization, Content-Type,
Accept-Language` in `Access-Control-Allow-Headers` one by one, because a
wildcard there does not cover `Authorization`, with
`Access-Control-Allow-Methods: GET, POST, DELETE` and
`Access-Control-Max-Age: 600`. It authenticates nothing.

**The header set.** Every answer of the widget API's routes carries the same
set: the origin header above,
`Access-Control-Expose-Headers: Retry-After, Content-Language`,
`Cache-Control: no-store`, `nosniff`, `Referrer-Policy: no-referrer` and the
platform CSP. A problem adds `Content-Type: application/problem+json`,
`Content-Language` and `Vary: Accept-Language`. The set rides on success and
refusal alike, on the rate limiter's 429 under `/-/feedback/v1/` (§10) and on
a 500 nobody foresaw: without it a shielded page reads a network error
instead of an answer. The last route's neutral 404 and the bearer guard's 401
(§7.8) go without it. The widget asks for neither.

**Refusals** are problem+json in the language the widget sends in
`Accept-Language`, English when it sends none, built from literal catalogue
keys and never through `ApiError`, whose handler would leave the header set
off.

| Status | Code | When |
|---|---|---|
| 404 | `FEEDBACK_UNAVAILABLE` | the token is missing, malformed, sealed with another secret or for another purpose, or expired, a session it names is gone, expired or ended at the IdP, or holds no consent where the token says it does, the gate refuses or decides on another site, the site is gone, feedback is off for this target, the item is not there or not the visitor's own, the item id does not read. One answer for all of these, the same bytes per language |
| 403 | `FEEDBACK_ACCOUNT_REQUIRED` | the action needs feedback with an account and the token has no consented session. A site that takes feedback with an account only answers it to every create without one, before reading its body |
| 422 | `FEEDBACK_INVALID` | the input does not hold up. The detail names the field from a fixed list, never from the input. A reply's body above its limit is one of these |
| 422 | `FEEDBACK_PAGE_MOVED` | the `page.path` of a create lies outside the root of the token's site at its current address: the page was served before the site or its group got a new address (§16.9). Only a new page view gives a token for the new address, and the widget offers to copy the text first |
| 422 | `FEEDBACK_AUTHOR_NOT_ALLOWED` | the author kind is one the site's setting does not take |
| 413 | `FEEDBACK_SCREENSHOT_TOO_LARGE` | the screenshot is above its limit, or the body of a create above its own |
| 422 | `FEEDBACK_SCREENSHOT_INVALID` | not PNG, JPEG or WebP by its bytes, too many pixels, or animated |
| 409 | `FEEDBACK_SCREENSHOT_QUOTA` | the screenshot space of the site or of the platform is full |
| 409 | `FEEDBACK_SITE_FULL` | the site has too many open items |
| 409 | `FEEDBACK_THREAD_FULL` | the item has as many replies as it takes (§16.10) |
| 429 | `TOO_MANY_REQUESTS` | a write counter is spent (§16.10), with a detail of its own (`TOO_MANY_REQUESTS.feedback`), or the rate limiter's class `feedback` is (§10). With `Retry-After` |
| 503 | `FEEDBACK_BUSY` | this client already has as many bodies in flight as it may have, or a chunk of the body does not fit in the budget of the bodies in flight (§16.10). With `Retry-After` |
| 408 | `FEEDBACK_TIMEOUT` | the body did not arrive in full within its deadline (§16.10) |
| 500 | none | a failure nobody foresaw, as `{type, title, status}` |

**Versioning.** The widget API is not in the OpenAPI document
(`include_in_schema=False`), which describes the admin API alone, so it
stands outside the API version and its breaking-change check
(`docs/releasing.md`). This section is its description instead. The
`API-Version` header is the admin API's and says nothing about
`/-/feedback/v1`, so the widget API does not expose it and has no version
header of its own. `/-/feedback/v1` only grows: a route or a field can be
added, and nothing a widget sends or reads changes or goes away. A page that
stays open keeps the widget it loaded, with a token that outlasts a restart,
while the server moves on.

Code: `serving/feedback_routes.py`, `serving/feedback_api.py`,
`serving/feedback_access.py`, `ratelimit.py`, `messages.py`, `constants.py`,
`main.py`, `frontend/src/feedback-widget/mine.ts`. Guarded by: `test_feedback_api.py`, `test_feedback_matrix.py`,
`test_host_separation.py`, `test_ratelimit.py`, `test_app_integration.py`,
`test_messages.py`, `frontend/src/feedback-widget/mine.test.ts`.

### 16.9 What a create holds to

A create is `multipart/form-data` with a part `item`, the JSON of the item
(at most 32 KiB), and at most one part `screenshot`. Toon parses the body
from the stream itself and holds every part to its own limit while it comes
in, and the whole body to `TOON_FEEDBACK_SCREENSHOT_MAX_BYTES` plus 64 KiB:
by its `Content-Length` before reading, and by counting for a body without
one. The multipart parser steps through every delimiter in Python, so before
it sees a chunk Toon counts the delimiters in it, those split over two
chunks included, and refuses a body with more than the three a create holds
as 422 `FEEDBACK_INVALID` (`item`). RFC 2046 keeps the delimiter out of every
part, so no create a browser sends is refused. Each chunk takes its room in
the body budget before it is parsed, and the whole body has to arrive within
the deadline (§16.10). The item:

```json
{
  "kind": "text",
  "comment": "Is this amount right?",
  "page": {"path": "/team/site/_preview/pr-42/docs/", "title": "Documentation"},
  "anchor": {
    "selectors": [
      {"type": "TextQuoteSelector", "exact": "2.4 million", "prefix": "a budget of ", "suffix": " euros for"},
      {"type": "TextPositionSelector", "start": 1200, "end": 1211},
      {"type": "CssSelector", "value": "main > p:nth-of-type(3)"}
    ],
    "point": null,
    "area": null
  },
  "viewport": {"width": 1280, "height": 800, "dpr": 2},
  "author": {"kind": "name", "name": "Jan"}
}
```

A rule that does not hold refuses with 422 `FEEDBACK_INVALID` where no other
code is named, and never with a 500:

- One rule for every string a submitter writes (comment, reply, name, quote,
  selector values, path): none of the Unicode categories Cc (newline and tab
  allowed in a comment and a reply), Cf (format characters, the bidi controls
  among them, and the tag characters a language model reads and a screen does
  not show), Cs (a lone surrogate) and Co (private use). The zero-width
  non-joiner (U+200C) and joiner (U+200D) pass, because a word in Persian and
  an emoji sequence need them. An unassigned code point (Cn) passes, because
  the submitter's device can know newer emoji than the server. The widget
  removes the other format characters from what a giver types before it
  checks it. A page title is the page's, not the submitter's: it is cleaned of
  what the rule refuses and cut to 300 characters rather than refused. The
  quote and its context are the page's too, so the widget cleans the page
  text it quotes before it sends, and re-anchoring reads the page the same
  way, so an icon-font glyph or a stray control character near a selection
  never makes a remark unsendable. In a `CssSelector` the widget writes a
  refused character as a CSS escape.
- The JSON is strict: no NaN or Infinity, no key twice, no key outside the
  closed schema, and a number such as `1e400` fails its range.
- `kind` is `page`, `point`, `area` or `text`, and a comment is 1 to 5000
  characters after trimming.
- `page.path` is the path as `location.pathname` gives it (printable ASCII,
  percent-encoded) inside the root of the token's target at the site's current
  address (`/{group}/{site}/`, or `/{group}/{site}/_preview/{ref}/`), at most
  2048 characters, with no dot segment (`%2e` counts), no empty segment but a
  trailing one, and no `_preview`, `_version` or `_encoded` first. It is
  stored relative to that root. A path outside the site's root at its
  current address is 422 `FEEDBACK_PAGE_MOVED` instead (§16.8): the widget
  keeps the text and offers to copy it. The hide form on such a page is the
  neutral 404 (§16.7).
- `page.fragment`, optional, is where on the page the item was given: the
  fragment of the address with its `#`, such as `#/7` for slide 7 of a slide
  deck or `#intro`. Only a fragment that names a place is taken: `#` and 1 to
  64 ASCII letters, digits and `/_.~-`. One that holds a value
  (`#access_token=...`, `#?q=`, `#a&b`), anything percent-encoded, a space
  or more than 64 characters is refused with 422 naming `page.fragment`, and
  the widget never sends one: it leaves the key out. The query string is
  never sent. A column check holds the stored value to the same pattern.
- The anchor uses the selector objects of the W3C Web Annotation Data Model.
  A `point` item has exactly one `CssSelector` (up to 1000 characters) and a
  `point` with `x` and `y` as fractions of that element's box and `pageX` and
  `pageY` as page coordinates. An `area` item has exactly one `CssSelector`
  and an `area` with `x`, `y`, `width` and `height` as fractions of the box
  (0 to 1) and `pageX`, `pageY`, `pageWidth` and `pageHeight` in CSS px. Its
  element is the nearest one from the area's centre up whose box holds the
  whole area. A `text` item has one `TextQuoteSelector` (`exact` up to 1000
  characters, `prefix` and `suffix` up to 32), at most one
  `TextPositionSelector` (a hint only, in UTF-16 code units over
  `document.body.textContent`) and at most one `CssSelector`. A `page` item
  has no anchor. A `point` or `area` the kind does not take is refused when
  it is not null. Toon rebuilds the anchor from the fields that passed and
  stores that.
- `viewport` is exactly `width`, `height` and `dpr`. Coordinates, positions
  and viewport numbers are finite, not negative and at most 1,000,000, and
  `dpr` is above 0 and at most 10.
- `author.kind` is `account`, `name` or `anonymous`, and one the site's setting
  takes (422 `FEEDBACK_AUTHOR_NOT_ALLOWED`). A `name` item needs a name of 1 to
  100 characters after trimming. Beside another kind a name is refused unless
  it is missing or blank: what an item says about its author comes from the
  session or from nowhere. An `account` item needs a token with a consented
  session (403 `FEEDBACK_ACCOUNT_REQUIRED`) and takes the `sub`, the name (cut
  to 200 characters) and the email address, in lower case and only when the
  IdP marked it verified, from that session. On a site that takes feedback
  with an account only, every create needs one, and is refused before its
  body is read. An `anonymous` item stores nothing about the person, also when
  the token names a session.
- The screenshot part is at most `TOON_FEEDBACK_SCREENSHOT_MAX_BYTES` (413
  above it). Its bytes decide what it is, whatever type was declared: PNG,
  JPEG or WebP by their magic bytes, at most 4096 pixels on a side and
  16,000,000 in all, and still (no APNG, no WebP animation). Nothing is
  decoded: a small file can declare a canvas that decodes to gigabytes in a
  reviewer's browser or in the image reader of a Claude session, so it is
  refused on the dimensions its header declares (422
  `FEEDBACK_SCREENSHOT_INVALID`). That check runs on the event loop, so the
  walk to the header reads at most 64 markers or chunks within the first
  64 KiB, and a header beyond those bounds is refused the same way. The widget encodes a JPEG under the smaller of
  `screenshotMaxBytes` (§16.2) and its own 2 MiB, at falling quality, and
  offers another part or sending without when none fits. A
  `screenshotMaxBytes` that is not a positive integer turns the widget off.

A reply from a submitter (`POST .../replies`) is `application/json` with
exactly `{"body": "..."}`, at most 64 KiB, under the same text rule and 1 to
5000 characters after trimming. How many replies an item takes is in §16.10.

Code: `serving/feedback_input.py`, `serving/feedback_body.py`,
`serving/feedback_api.py`, `serving/feedback_images.py`,
`serving/feedback_inject.py`, `frontend/src/feedback-widget/anchor.ts`,
`text.ts`, `api.ts`, `config.ts`, `screenshot.ts`. Guarded by:
`test_feedback_input.py`, `test_feedback_body.py`, `test_feedback_api.py`,
`test_feedback_matrix.py`, `test_feedback_images.py`,
`test_feedback_inject.py`, `test_migrations.py`,
`frontend/src/feedback-widget/text.test.ts`, `anchor.test.ts`,
`api.test.ts`, `config.test.ts`, `screenshot.test.ts`.

### 16.10 Quotas and counters

What one visitor or one site can put into the database is bounded where it is
written, and what a request holds in memory before that is bounded as its
body comes in. The limits below are constants in `constants.py`. Only the
screenshot limit, the retention terms and the rate limiter's class are
settings.

- **Open items.** A site takes no new item while it has
  `FEEDBACK_SITE_MAX_OPEN` (500) items `new` or `seen` (409
  `FEEDBACK_SITE_FULL`).
- **Screenshot space.** The screenshots of one site take at most
  `FEEDBACK_SITE_SCREENSHOT_MAX_BYTES` (50 MiB) together, those of the
  whole platform at most `FEEDBACK_TOTAL_SCREENSHOT_MAX_BYTES` (500 MiB),
  or 409 `FEEDBACK_SCREENSHOT_QUOTA`. They live in PostgreSQL (TDR-0013). A
  refusal by the platform-wide cap logs a WARNING at most once every 600
  seconds per process, with byte counts and nothing about the person or the
  site. A refusal by a site's own cap logs nothing. Accepted: the sites share
  the platform-wide cap first come, first served. Ten full sites leave no
  room on any other, and one author can fill a site within one window of the
  write counters (25 creates with a 2 MiB screenshot). The room comes back
  when a site admin deletes items or their screenshots are purged (§16.12).
  Meanwhile the widget offers to send without the screenshot.
- **Under a lock.** Both are checked in the transaction of the create, under a
  transaction-scoped advisory lock on the site and, for a create with a
  screenshot, a second one for every screenshot on the platform, always taken
  after the site's. So concurrent creates cannot overshoot either limit
  together, and cannot deadlock. The site row and the version are held
  `FOR KEY SHARE` until the commit: a site deleted in between is
  `FEEDBACK_UNAVAILABLE` and not a 500.
- **The version** an item was given on is written through
  `(SELECT id FROM versions WHERE id = :ver AND site_id = :site)` inside the
  insert, never as a bare value. A version that a redeploy or the cleanup
  removed after the page view gives NULL instead of a foreign key error. It is
  a hint, not something the item depends on.
- **Write counters.** Two counters in process memory limit the writes
  through the widget API (a create, a reply, a delete) per window of
  `FEEDBACK_WRITES_WINDOW_S` (600 s). Authors count attempts, a site counts
  accepted writes. The author counter keys the author on the site:
  `FEEDBACK_WRITES_PER_AUTHOR` (30) for the `sub` of a consented session
  (`site:<id>:session:<sub>`) or of a session for the gate alone
  (`site:<id>:gate:<sub>`, §16.5), and `FEEDBACK_WRITES_PER_ADDRESS` (150)
  for a token without a session, keyed on the client address (IPv6 by its
  /64) and the site, larger because many people can share an address. The
  site is in the author's key because the site's own scripts hold its page
  token and can spend all of the budget it counts: counted across sites, one
  site could lock a visitor out of every other site and read from the count
  how many writes they made there. The two session namespaces stay apart for
  the same reason. The site counter holds `FEEDBACK_WRITES_PER_SITE` (300)
  per site, in a counter of its own, so that a flood of author keys cannot
  evict a site's key and reset its count. A write route first refuses what
  fails authentication (no account where one is needed, an item id that is
  no id), then counts the attempt on the author's key, then reserves one
  write on the site's key, all before it reads a body. Each count and its
  check have nothing awaited between them, so neither a burst from one
  author nor writes from several authors in flight together can pass a
  budget. A write that ends without being stored gives the site its
  reservation back, whether the site's own budget, validation, a cap, the
  room for bodies, a body that does not arrive or a failure ended it. So
  attempts that are refused never spend the site's budget, and one
  page-token holder cannot lock the site out with them. An attempt refused
  by the author's own budget reserves nothing. Spent is 429
  `TOO_MANY_REQUESTS` with the `Retry-After` of the budget that refused.
  Reads do not count. A key goes at most one window after its window ended.
  Accepted: a write in flight holds its reservation until it ends, a body at
  most `FEEDBACK_BODY_DEADLINE_S` (60 s), so clients that stall their bodies
  together can keep a site's last writes from others that long, each client
  address with at most `FEEDBACK_BODIES_PER_CLIENT` (4) bodies in flight.
  Accepted: where the token names a session for the gate alone, the site's
  scripts can find out, at the cost of one write,
  whether that person's budget on that site is spent (a malformed create is
  429 when it is, 422 when it is not).
- **Bodies in flight.** Every chunk of a create's or a reply's body takes its
  room in a budget for the whole process before Toon parses it, and the body
  holds all it took until its request is done. A declared `Content-Length`
  only bounds the read: one that sends nothing holds no room, and one above
  the route's limit is refused unread. `FEEDBACK_BODY_BUDGET_CREATES` (32)
  whole creates at the screenshot limit (66 MiB at the default) bound the
  bytes of all bodies together, and `FEEDBACK_BODIES_PER_CLIENT` (4) how many
  bodies one client address has in flight. Past that share a request is 503
  `FEEDBACK_BUSY` before anything of its body is read, and a chunk that does
  not fit in the budget is 503 before it is parsed, both with `Retry-After`
  set to the deadline. A body longer than its bound is 413 (422 for a reply)
  before that chunk asks for room. The whole body has to arrive within
  `FEEDBACK_BODY_DEADLINE_S` (60 s), or 408 `FEEDBACK_TIMEOUT`. A client that
  goes away mid-upload is one line at INFO. Memory stays within the budget,
  plus about an eighth that the parts reserve as they grow, plus the database
  driver's two copies of the one screenshot being written (the platform-wide
  screenshot lock lets one create at a time write one). Accepted: there is no
  minimum receive rate, so a client with enough bandwidth and addresses can
  hold the budget full and get every create a 503 while it keeps that up.
- **Replies.** An item takes at most 50 replies from its submitter and 250 in
  all (`FEEDBACK_MAX_GIVER_REPLIES` and `FEEDBACK_MAX_REPLIES`), counted by the
  widget API and the admin API under one advisory lock on the item. Past them
  a reply is 409 `FEEDBACK_THREAD_FULL`. The visitor's own items are bounded
  by count (§16.8): 20 items with 10 replies each. The admin's detail carries
  the at most 250 replies of one item.
- **The rate limiter** has a class of its own for every path under
  `/-/feedback/` (§10).
- **Refused tokens.** The page tokens the widget API and the hide route
  refuse are counted per process and per reason, in windows of
  `REFUSED_TOKENS_WINDOW_S` (600 s). A window that ends with more than
  `REFUSED_TOKENS_THRESHOLD` (100) is one WARNING with counts alone (§16.5).
  Both are constants in `serving/feedback_refusals.py`. The count refuses
  nothing itself.

Code: `serving/feedback_api.py`, `serving/feedback_body.py`,
`serving/feedback_refusals.py`, `constants.py`, `api/admin/feedback/routes.py`, `ratelimit.py`,
`config.py`, `main.py`. Guarded by: `test_feedback_api.py`,
`test_feedback_body.py`, `test_feedback_matrix.py`, `test_feedback_refusals.py`,
`test_feedback_admin_api.py`, `test_ratelimit.py`, `test_config.py`,
`test_app_integration.py`.

### 16.11 The admin API and the CLI

Seven routes of the admin API (`/-/api/v1`, tag `Feedback`):

| Method and path | Minimum role | CLI token | Audit action |
|---|---|---|---|
| `GET /sites/{group}/{site}/feedback` | reader | yes | none |
| `GET /sites/{group}/{site}/feedback/{id}` | reader | yes | none |
| `GET /sites/{group}/{site}/feedback/{id}/screenshot` | reader | yes | none |
| `PUT /sites/{group}/{site}/feedback/{id}/status` | editor | yes | `feedback_status` |
| `POST /sites/{group}/{site}/feedback/{id}/replies` | editor | yes | `feedback_reply` |
| `DELETE /sites/{group}/{site}/feedback/{id}` | admin | no | `feedback_delete` |
| `PUT /sites/{group}/{site}/feedback-settings` | admin | no | `site_feedback` |

The reads take an admin session without the CSRF header, like every read (an
`<img>` that loads a screenshot cannot send one), or a CLI token. The two
writes the CLI may make take an admin session with CSRF or a CLI token
(§7.8).

- **The list** pages as the audit log does: newest first over
  `(created_at, id)`, `limit` 1 to 100 (50 by default), and `nextCursor`. It
  filters on `status` (`open`, the default, for `new` and `seen`, or `new`,
  `seen`, `resolved`, `all`), on `target` (`all`, `live`, `preview`) and on
  `ref`, only with `target=preview` (else 422 `FEEDBACK_REF_WITHOUT_PREVIEW`)
  and only as a slug (else 422 `PREVIEW_REF_INVALID`). The counts per status
  cover the whole site, whatever the filters.
- **Who sees what.** `author.email` is null for a caller whose effective role
  is reader: the email addresses of submitters are for editors and admins.
  Every text of an item comes from someone outside the site team, the page
  title and a typed name included. The admin interface shows the page title,
  a name and a quote as text. A comment and a reply render a fixed subset of
  Markdown (§16.14), never HTML.
- **The place on the page.** `page.url` is the page at the site's current
  address. `highlightUrl` adds the fragment `#toon-feedback=`: base64url of
  compact JSON with `k` (the kind), `s` (the selector), `x`, `y`, `px`, `py`
  (the point, or the top left corner of an area), `w`, `h`, `pw`, `ph` (the
  size of an area in its element and on the page), `q` (the quote) and `f`
  (`page.fragment`, when the item has one). The place script (§16.2) puts
  `f` back in the address before the page's own scripts run, so a deck
  opens on the slide. A second link to the same page only changes the
  fragment, after the page has read its address: for an `f` that names no
  element of the page the widget loads the page again, so the place script
  takes the link. The
  widget takes at most 2048 bytes of JSON, so at most 2731 base64url
  characters after `#toon-feedback=`, and `highlightUrl` keeps to that. The
  anchor of an item under "Mijn feedback" has the same reduced form (§16.8),
  but comes in the widget API's answer and has no such cap. A quote that is
  too long is halved until it fits, since its start still finds the passage.
  A selector that is too long is left out whole, since cut short it could
  name another element. `f` is never left out to make room. On a
  page that gets the widget, the widget reads the fragment, takes it off the
  address, brings the place into view (also inside parts of the page that
  scroll on their own) and marks it with a ring, a frame for an area and a
  tint over each line of a quote, visible on light and dark bands and in
  forced colours. No mark takes a click. It never scrolls the window sideways
  while the root or the body hides what overflows sideways, since the visitor
  could not scroll back. It shows nothing from the fragment as text: it
  announces the place in the page's own words, never the link's. For a place
  it can no longer find it shows a note beside the button, "De plek van deze
  feedback is op deze pagina niet meer te vinden." (the spot of this feedback
  can no longer be found on this page). On the Feedback tab, "Bekijken op de
  site" (show on the site) opens the place, for an item from a preview only
  while that preview still opens, that is while its `currentVersion` is not
  null. Where
  the site's settings leave the page without the widget, the button stays
  and a line says the page opens without marking the place. The tab's place
  filter, "Gegeven op" (given on), offers the live site, all previews and
  each current preview, since items outlive the preview they were given on
  (§16.12). The filters stand in the tab's address the way the list route
  takes them (`status`, `target`, `ref`). On the Previews tab a preview with
  open feedback links to the list narrowed to it, by its `openFeedbackCount`.
- **The version.** `versionId` is the version the page showed, and
  `currentVersion` (`id` and `createdAt`) the one the live site or the
  preview shows now, joined in the same query as the item, null when there is
  none (the preview is gone or past its `expires_at`, nothing is live), since
  the gate refuses an expired preview while the nightly cleanup has yet to
  remove it. An item is on an earlier
  version when `currentVersion` is not null and is not `versionId`, also when
  `versionId` is null because that version is gone. The tab marks such a row
  "Eerdere versie" (earlier version), and while the version still exists links
  to the page at its `_version` address (§7.3), which every reader of the
  feedback may open.
- **The page of an item.** The tab opens an item in a sheet, which links to
  the item's own page, `/{group}/{site}/feedback/{id}`, with the list's
  filters in its address. An id that is no item of the site,
  whatever the reason, gets one "Onbekende feedback" (unknown feedback).
- **The status.** Sending the status an item has already changes nothing and
  writes no audit row. A real change records when and by whom, and sets
  `resolved_at` for `resolved` and clears it otherwise. A reopened item
  therefore falls under the term of open items again, which counts from
  `created_at` (§16.12), so one older than that term goes at the next nightly
  cleanup.
- **A reply** from the team is 1 to 5000 characters under the text rule of
  §16.9 (refused as `FIELD_EMPTY`, `FIELD_TOO_LONG` or
  `FIELD_CONTROL_CHARACTERS`). It stores the member's id and shows their
  current name, read from the member, never their email address, because a
  submitter who sent with an account reads it under "Mijn feedback".
  The status stays as it is. An item takes 250 replies in all, the
  submitter's included (409 `FEEDBACK_THREAD_FULL`). On the tab, a reply that
  was typed or refused stays with its item for as long as the tab is open,
  and its field is read-only while it is on its way.
  Before it sends, the tab drops the format characters the API refuses, as
  the widget does (§16.9).
- **No cache.** The list, an item, and the answers to a status change and to
  a reply carry `Cache-Control: no-store`, so what a submitter wrote, their
  name and email address among it, stays in no browser's HTTP cache after the
  item is deleted or purged.
- **The screenshot** comes back as stored, with
  `Cache-Control: private, no-store`, `nosniff`,
  `Content-Security-Policy: default-src 'none'; sandbox` and
  `Content-Disposition: inline; filename="feedback-<id>.<ext>"`. Opened on its
  own it is a document that loads and runs nothing.
- **`SiteOut`** carries `feedbackEnabled`, `feedbackIdentity`, `feedbackLive`
  and `openFeedbackCount` (`new` plus `seen`, in one count for every site of a
  list). **`PreviewOut`** carries `openFeedbackCount` too, in one count for
  every preview of the site.

The CLI lists, shows, marks and answers, as the member who ran `toon login`:

```
toon feedback list    --site GROUP/SITE [--status open|new|seen|resolved|all] [--preview REF | --live] [--json] [--with-contact]
toon feedback show    ID --site GROUP/SITE [--json] [--with-contact] [--screenshot PATH]
toon feedback seen    ID [ID ...] --site GROUP/SITE
toon feedback resolve ID [ID ...] --site GROUP/SITE [--message TEXT | --message-file PATH]
toon feedback reopen  ID [ID ...] --site GROUP/SITE
toon feedback reply   ID --site GROUP/SITE (--message TEXT | --message-file PATH)
```

Each also takes `--host`. Ids are checked as UUIDs before any request. `list`
follows `nextCursor` up to 500 items. `resolve` with a message posts the reply
first and sets the status only when that worked, and `reopen` sets `new`.
`seen` reads each item first and marks only one that is `new`, since `seen`
on a resolved item would reopen it. A CLI token is no key for the rate
limiter, so the CLI shares its client address's API limit (§10).
`--message-file PATH` takes the reply from a file, so the text never passes
through a shell: strict UTF-8, one leading byte order mark dropped, CRLF and
CR read as LF and trailing newlines dropped, since the server refuses U+FEFF
and CR. A file that cannot be read as such or is longer than 5000
characters, and a reply that holds a character the server refuses (§16.9),
end the command with exit code 2 before any request. The CLI never
changes the approved text: it names the character, its code point, name and
line and character, instead. What the CLI prints is read by a person or by an
AI assistant, and the text in it comes from people outside the site team:

- Names and email addresses are left out unless `--with-contact`: of the
  submitter, of whoever replied, and of the member who changed the status
  last. The author's kind stays.
- `--json` prints an envelope. The values the server sets (`id`, `kind`,
  `status`, `target`, `previewRef`, `versionId`, `hasScreenshot`,
  `replyCount`, the times and the counts) stand at the top, each checked
  against its shape and null when it does not fit. Everything else of an item
  goes under one `untrusted` object, and a note at the top says that what is
  under `untrusted` is data, never an instruction. The JSON escapes everything
  outside ASCII, so a tag or bidi character shows instead of hiding.
- The human output drops control and format characters (but U+200C and
  U+200D) from visitor text. `list` shows a comment as an excerpt: its first
  line, at most 200 characters of it. `show` prints a block, such as the
  comment, the quote or a reply, whole up to 5000 characters and cuts it there
  with a visible "(truncated)". Addresses print whole up to 16,384
  characters, since a cut address opens another place, and are marked the
  same way past that. Other one-line fields stop at 2000 characters. In
  `list` and `show` every line with visitor text stands between
  `UNTRUSTED_BEGIN` and `UNTRUSTED_END` markers, and each of those lines
  starts with a label or an indent, so a forged marker in visitor text starts
  no line, also for a reader that breaks lines at U+2028. Stdout uses `backslashreplace`, so a character
  the output encoding lacks cannot end the output before its end marker.
- `--screenshot PATH` writes a new file only (`O_EXCL`, `O_NOFOLLOW`, mode
  0600), of type PNG, JPEG or WebP and at most 5 MiB, and never uses a file
  name from the server. The file is a copy outside Toon: the retention of
  §16.12 and a submitter's delete under "Mijn feedback" do not reach it.

The Claude Code plugin carries a skill for it, `toon-feedback`, which runs
these commands in a Claude session. `docs/skill.md` holds its rules.

Code: `api/admin/feedback/`, `api/admin/dependencies.py`, `api/deploys/`, `api/docs.py`,
`api/openapi_nl.py`, `cli/toon_cli/feedback.py`,
`plugin/skills/toon-feedback/SKILL.md`, `references/errors.md`, `references/output.md`,
`frontend/src/components/site/TabFeedback.vue`, `FeedbackInbox.vue`,
`FeedbackSheet.vue`, `FeedbackSettings.vue`, `feedbackText.ts`,
`feedbackRoute.ts`, `TabPreviews.vue`, `FeedbackItemPage.vue`,
`FeedbackItemView.vue`, `feedbackItem.ts`,
`frontend/src/feedback-widget/fragment.ts`, `boot.ts`, `widget.ts`,
`rect.ts`, `anchor.ts`, `styles.ts`, `screenshot.ts`. Guarded by:
`test_feedback_admin_api.py`, `test_cli_creation.py`,
`test_api_documentation.py`, `test_openapi_i18n.py`,
`cli/tests/test_feedback.py`, the component tests under
`frontend/src/components/site/`,
`frontend/src/feedback-widget/fragment.test.ts`, `boot.test.ts`,
`widget.test.ts`, `rect.test.ts`, `anchor.test.ts`, `screenshot.test.ts`,
the `feedback-*` evals under `plugin/evals/`, and
`e2e/tests/feedback.spec.ts`.

### 16.12 Retention

Feedback outlives preview teardown, redeploys and the cleanup of old live
versions: an item names its version as a hint only. It goes in four ways.

- With its site or its group (§8). Its replies and its screenshot go with it.
- When a site admin deletes it, or the visitor who sent it with their account
  deletes it under "Mijn feedback".
- By the nightly cleanup (§8), in one transaction: resolved items
  `TOON_FEEDBACK_RESOLVED_RETENTION_DAYS` (90) after `resolved_at`, items still
  `new` or `seen` `TOON_FEEDBACK_OPEN_RETENTION_DAYS` (365) after `created_at`,
  and the screenshots of resolved items
  `TOON_FEEDBACK_SCREENSHOT_RETENTION_DAYS` (30) after `resolved_at`. A term of
  0 keeps, and an item exactly on its term stays. A term counts from a
  timestamp in the database, so a changed term reaches the items already
  there.
- With the downgrade of migration `0011_feedback` (§12).

An item that someone opens again while the cleanup runs stays, with its
screenshot: the screenshot rule locks the items it reads (`FOR SHARE`). The
purge runs before every step that touches the content volume, so trouble
there cannot hold it up. It writes no audit row (`docs/audit-log.md`) and
logs one line per run at INFO with the three counts. A production process
does not show that line: `containers/toon/entrypoint.sh` starts uvicorn with
uvicorn's own logging configuration, so Toon's loggers stay at Python's
default level, WARNING. So nothing in production shows that retention ran.

Code: `previews/cleanup_job.py`, `config.py`, `main.py`,
`alembic/versions/0011_feedback.py`. Guarded by: `test_cleanup_job.py`,
`test_migrations.py`.

### 16.13 What is not audited

What site feedback writes to the audit log, and what it does not, is in
`docs/audit-log.md`.

### 16.14 Formatting in comments and replies

A comment and a reply are stored as typed. Mijn feedback in the widget and
the Feedback tab of the admin SPA render them as a small fixed subset of
Markdown: paragraphs, bullet and numbered lists one level deep, inline code,
fenced code blocks, strong and emphasis, blockquotes one level deep,
backslash escapes and links. Everything else stays the text it was typed as:
headings, tables, rules, images, HTML, entities and footnotes.

One parser, `frontend/src/feedback-text/markdown.ts`, turns the text into a
tree of a few node types. The widget builds DOM from that tree with
`createElement` and text nodes (`renderFeedbackText`), and the admin SPA
renders the same tree with Vue's `h()` inside an `nldd-rich-text`
(`FeedbackMarkdown.vue`). No step reads a string as markup, so nothing needs
sanitising. The parser is linear in the length of the text: no backtracking
regular expression runs over it, and emphasis is paired with CommonMark's
delimiter algorithm and its lower bounds.

A link never hides where it goes. Only an absolute `http:` or `https:`
address becomes one, typed bare or as `[label](address)`, and its visible
text is always the address: `[label](https://x)` reads "label (https://x)".
An address with a user name or an `@` before its host, or with an invisible
or direction-changing character (Unicode Cf, the Hangul fillers, variation
selectors), stays text. A host typed in another form than the browser
resolves it (an internationalised name, a percent-encoded or numeric host) is
shown resolved, in ASCII, so a look-alike stands out. A link carries
`rel="noopener noreferrer nofollow ugc"` and `dir="ltr"` and opens in a new
tab, except in the widget on a shielded page, where a new tab would inherit
the sandbox and it opens in the same tab (§16.4). A row of the inbox and the
accessible name of a pin show a one-line plain-text excerpt
(`feedbackPlainText`), since a button cannot hold a link.

The editor (§16.15) formats more than this subset as it is typed, headings
among them. It keeps the Markdown markers in sight, so the text on screen is
the text that is stored, but a heading reads as its `#` and words once it is
rendered here.

Code: `frontend/src/feedback-text/markdown.ts`, `styles.ts`,
`frontend/src/components/FeedbackMarkdown.vue`, `FeedbackSheet.vue`,
`FeedbackInbox.vue`, `frontend/src/feedback-widget/mine.ts`. Guarded by:
`frontend/src/feedback-text/markdown.test.ts`, `FeedbackMarkdown.test.ts`,
`mine.test.ts`, `FeedbackSheet.test.ts`, `FeedbackInbox.test.ts`,
`e2e/tests/feedback-markdown.spec.ts`.

### 16.15 Writing in the editor

The field for a comment in the composer and the field for a reply under Mijn
feedback start as a plain textarea. When the field first gets focus, the
widget puts `nldd-text-editor` of `@nldd/design-system` (0.24.0) in its
place, in an `<iframe sandbox="allow-scripts">` of its own: a Markdown
editor on CodeMirror 6 that keeps the markers in sight (its `syntax` stays
`visible`), so what is on screen is the text that is sent. Why a frame, why
on demand, and what was not chosen:
`docs/tdr/0014-feedback-editor-in-a-sandboxed-frame.md`. In short, custom
elements have one registry per document, so an editor defined in the page
would clash with a site that uses NLDD itself, and the editor is ten times
the widget's size.

The textarea stays the field. Until the frame says it is ready, it takes no
room and the textarea takes the typing. A frame that is not ready within 8
seconds (`EDITOR_TIMEOUT`), or that reports a failure later, goes, and the
textarea comes back with the text so far. That field does not try again
until it is built anew. Each change in the editor is written into the
textarea as an input event, so the checks, the limit message and the drafts
work as they did. The editor holds the field's limit of 5000 characters in
UTF-16 units, as `maxlength` counts them, and takes the field's label and
invalid mark, because `aria-describedby` cannot point into a frame. Escape
closes a list the editor shows, and otherwise steps back in the widget: the
frame decides that before CodeMirror sees the key, which would otherwise
swallow it.

The frame and the widget talk over one port. The frame says hello to its
parent once, with `postMessage` to any origin, because its own origin is
opaque and the parent's may be too. The widget answers only a hello from the
window of its own iframe, by transferring one end of a new `MessageChannel`,
and everything after goes over that port. The frame takes one port, from its
parent only. The messages are in `feedback-editor/protocol.ts`.

`npm run build` builds the frame's script from `frontend/src/feedback-editor/`
(`frontend/vite.editor.config.ts`) into `dist/feedback-editor.js`: one
minified classic script with every dynamic import inlined, because a module
script is fetched in CORS mode, which the frame's opaque origin cannot pass.
Its legal comment names every package in it with its version and licence.
Toon reads the file at startup, as it does the widget, and serves the script
at `/-/feedback/editor.<hash>.js` and the frame document, which names the
script, at `/-/feedback/editor.<hash>.html` under its own hash. Both are
`immutable` with `nosniff`, any other name and either address on the admin
host is the neutral 404, and both count in the rate-limit class `feedback`
(§10). The frame document is the same for every page and every visitor: one
script element, no inline script or style, nothing a visitor or a site wrote.
It carries `Referrer-Policy: no-referrer` and a policy of its own:

```
default-src 'none'; script-src <content origin>/-/feedback/editor.<hash>.js;
base-uri 'none'; form-action 'none'; sandbox allow-scripts
```

- `default-src 'none'`: the frame loads its script and nothing else. With no
  `connect-src` it can fetch nothing.
- `script-src` names the one script by its full URL, not `'self'`, which
  would admit every published site's own scripts on the content host.
- `base-uri 'none'` and `form-action 'none'`: no `<base>` and no form take
  effect in the frame.
- `sandbox allow-scripts`: the document has an opaque origin wherever it is
  opened, also outside the widget's iframe. It has no cookies and no storage,
  and it is same-origin with nothing on the content host, so the frame and
  the page cannot read each other's DOM.
- No `style-src`: no style element or attribute from markup applies. Lit and
  CodeMirror style through constructed stylesheets, which CSP does not
  govern, and the frame loads no web font, which would be fetched in CORS
  mode.
- No `frame-ancestors` and no `X-Frame-Options`: a sandboxed parent page, as
  every shielded page is (TDR-0002), matches no `frame-ancestors` source in
  Chromium and WebKit and only `*` in Firefox, so any value would keep the
  editor out of shielded sites. What that accepts is in `docs/security.md`.

A missing, unreadable or empty `feedback-editor.js` does not stop Toon: the
configuration goes out without `editorUrl`, the fields stay plain textareas,
and one warning says why.

Code: `frontend/src/feedback-editor/`, `frontend/src/feedback-widget/editor.ts`,
`composer.ts`, `mine.ts`, `widget.ts`, `config.ts`, `platform.ts`,
`frontend/vite.editor.config.ts`, `serving/feedback_editor.py`,
`serving/feedback_routes.py`, `serving/feedback_inject.py`, `main.py`.
Guarded by:
`frontend/src/feedback-editor/frame.test.ts`, `editor.test.ts`,
`limit.test.ts`, `styles.test.ts`,
`frontend/tests/feedback-editor-bundle.test.ts`,
`frontend/src/feedback-widget/editor.test.ts`,
`composer.test.ts`, `mine.test.ts`, `widget.test.ts`, `config.test.ts`,
`test_feedback_editor.py`, `test_feedback_inject.py`,
`test_app_integration.py`, `test_host_separation.py`, `test_ratelimit.py`,
`e2e/tests/feedback.spec.ts`.

# Privacy (AVG)

**Status: draft starting point, not a legal determination.** This document
gives the privacy officer (FG, "Functionaris Gegevensbescherming") and the
controller ("verwerkingsverantwoordelijke") a factual starting point for a
full DPIA (Model DPIA Rijksdienst), not a substitute for one. Nothing here
constitutes legal advice or a completed assessment; `docs/security.md` lists
the DPIA as "planned, prerequisite for production go-live" and this document
is the input to that work, not its conclusion.

Facts below are derived from the code and configuration as they stand today
and cite their source. Anything not verified against the code is marked as
such rather than assumed.

## Data subjects

- **Members**: people with a `members` row, created on first visit to the
  admin host (`backend/src/toon/models/identity.py`, `Member`). Logging in
  alone does not create one.
- **Content viewers**: people who log in with SSO Rijk on the content host
  without being a member, for example to view an SSO-gated or invitee-gated
  site, or to give feedback with their account, on a public site too
  (`backend/src/toon/models/audit.py`, `ContentViewer`).
- **Invitees**: people named on a site's invitee list by sub or email
  address, whether or not they ever log in (`backend/src/toon/models/publication.py`,
  `Invitee`).
- **Anonymous visitors**: people who view public or secret-link content
  without logging in; the audit log still records their (truncated/encrypted)
  IP address. A visitor let in by a secret link who signs in, for instance
  to give feedback with their account, is a content viewer as well. Once
  they have a session on that site, by signing in there or by saying yes to
  feedback with their account, the page views the audit log keeps there
  carry their pseudonym until the session ends (`docs/audit-log.md`).
- **CLI users**: members who authorize a `toon login` device (CLI publishing
  tool).
- **People named in published content**: anyone whose personal data appears
  inside a published site, without ever interacting with Toon's own
  authentication.
- **Feedback submitters**: people who send feedback through the widget on a
  site's page (`backend/src/toon/models/feedback.py`, `FeedbackItem`), with
  their SSO account after consenting on Toon's own page, with a name they
  type, or without a name, as far as the site's setting allows. Where the
  site is public, or a preview of it is public by an access of its own, and
  it takes feedback without an account, that can be anyone, the public
  included. A preview follows the site's access, so on a public site this
  holds whether the live site takes feedback or not.
- **People visible in screenshots**: anyone whose personal data was on a
  submitter's screen when the widget made a screenshot: the submitter, people
  named on the page, anyone else it showed (`FeedbackScreenshot`).

## Personal data processed, per store

| Store | Personal data | Purpose | Source | Retention |
|---|---|---|---|---|
| `members` (`models/identity.py`) | `sso_subject`, `email`, `name`, `platform_role`, `status`, `language`, `created_at`, `last_login_at` | administering group/site membership and roles | SSO Rijk OIDC claims, on first admin-host visit | not time-bounded; tied to account lifecycle (deactivation), see "Retention and deletion" |
| `group_members` / `site_members` (`models/identity.py`) | `member_id`, `role`, `added_by`, `added_at` | who has what role where | set by an admin | tied to membership |
| `content_viewers` (`models/audit.py`) | `sso_subject`, `email` (nullable, may be unverified), `email_verified`, `last_seen_at` | makes a content-host SSO login traceable to a person for the audit log. A content-only viewer gets no `members` row | SSO Rijk OIDC claims, on every successful content-host login, signing in to give feedback with an account included, on a public site too | 180 days from `last_seen_at`, purged by `audit/retention.py` (`docs/audit-log.md`, "Retention") |
| `invitees` (`models/publication.py`) | `identifier` (email address or `sub`, lowercased), `added_by`, `added_at` | per-site invitee access list | admin input | until removed by an admin; no automatic expiry |
| `access_keys` (`models/publication.py`) | `label`, `selector`, `verifier_hash` (SHA-256 of a random secret) | secret-link credential, not itself tied to a person | generated on creation | mandatory `expires_at`, 90 days default, 365 days maximum (BIO2 5.18.02, `docs/security.md`) |
| `audit_log_entries` (`models/audit.py`) | `actor_pseudonym` (HMAC-SHA256 of the actor identifier under `TOON_AUDIT_PEPPER`), `ip_truncated` (/24 IPv4, /48 IPv6), `ip_encrypted` (AES-256-GCM, reversible, under the separate `TOON_AUDIT_IP_KEY`), `action`, `result`, `reason_code`, `refs` (for `group_name` and `site_title` also the previous name or title: free text that members choose, see below) | security and access audit trail | every login, access decision, admin action and CLI event | 180 days for pure viewing/login-success actions (BIO2 8.15.04), 3 years for everything else, including every refusal, admin operation and publication (`docs/audit-log.md`, "Retention"; enforced in the database by `audit_log_retention()`, `backend/alembic/versions/0001_base.py`, short term from `0008_short_retention_180_days.py`) |
| `audit_purge_holds` (`models/audit.py`) | `started_by`, `ended_by` (member ids), `reason`, `end_reason` (free text a platform administrator writes; an e-mail address is refused), `started_at`, `ended_at` | records who held the audit purge during an incident, when and why | a platform administrator on the platform page | not time-bounded: a trigger refuses every delete (`0010_audit_purge_hold.py`); the same reasons are also in the audit log for 3 years |
| Admin/content sessions (`auth/sessions.py`) | `sub`, `email`, `name`, refresh token (kept in process memory only, `repr=False`, never logged) | keeps someone logged in | OIDC login | in-memory only, not persisted to the database; expires after 12 hours (`MAX_SESSION_AGE`) or on logout; lost entirely on a restart (`docs/security.md`, "Sessions do not survive a restart") |
| `cli_device_authorizations` (`models/cli.py`) | `device_hash`, `user_code_hash`, `client_name`, `ip_truncated`, `member_id` | `toon login` device pairing | CLI device flow (RFC 8628) | short-lived: expires after 10 minutes, deleted once exchanged, swept by the cleanup job |
| `cli_sessions` / `cli_refresh_tokens` (`models/cli.py`) | `member_id`, `client_name`, token selector/hash pairs | CLI publishing sessions | approved by a member in an admin session | access token 1 hour; session up to 30 days after last refresh, 90 days absolute (`docs/security.md`, "CI id token"/"CLI device flow") |
| Published content (files on the content volume) | whatever the publisher includes: potentially names, contact details or special categories of data (AVG art. 9) | the published site itself | uploaded by a publisher (member or CI) | until the site or version is removed; Toon does not parse, classify or scan content for personal data |
| `feedback_items` (`models/feedback.py`) | The comment. The page (its path within the site or preview, its title). The place on it (`anchor`: the CSS selector of an element and the point or the area in it, or the quoted text with at most 32 characters around it). The window size (`viewport`: width, height, device pixel ratio), the version, and the moment it came in (`created_at`). Per author kind: `account` the SSO `sub`, the name, and the email address when the IdP marked it verified. `name` the name the submitter typed, which nobody checked. `anonymous` nothing about the person. The status, and the member who changed it last (`status_changed_by`, an id) | collecting remarks on a site's pages, so that the site team can read, answer and resolve them | the submitter, through the widget. The account fields come from the consented content session, never from the request | with its site or group, or when its submitter deletes it, for an item sent with an account. Otherwise a resolved item 90 days after `resolved_at` and an item still new or seen 365 days after `created_at` (`TOON_FEEDBACK_RESOLVED_RETENTION_DAYS`, `TOON_FEEDBACK_OPEN_RETENTION_DAYS`), see "Retention and deletion" |
| `feedback_replies` (`models/feedback.py`) | The text of a reply. For a reply of the site team the member's id, by which the reply shows the member's current name and never their email address, for a reply of the submitter no member | the conversation between the site team and the submitter | submitters who sent with an account, under "Mijn feedback" in the widget | with its item |
| `feedback_screenshots` (`models/feedback.py`) | An image of the submitter's viewport, or of the part of it they chose, with what they covered painted over. It can show any personal data that was on the screen: of the submitter, of people named on the page, of anyone else. | showing the site team what the submitter saw | made in the submitter's browser by the widget. The submitter sees it before sending and can cover parts of it or leave it out | with its item, and for a resolved item 30 days after `resolved_at` (`TOON_FEEDBACK_SCREENSHOT_RETENTION_DAYS`) |
| Feedback write counters (`serving/feedback_api.py`) | per author and site a key, the start of its window and a count, and per site a key with the same. The author's key is the site's id together with the session's `sub` when the page token carries a consented session, with the `sub` of the session that let a signed-in visitor in when the token carries one for the gate alone (under a key of its own), and otherwise with the client's IP address (an IPv6 address by its /64). The site's key is its id alone | limiting misuse of the widget | authors count attempts, a site counts accepted writes. The author's counter counts every attempt at a write through the widget, a create, a reply or a delete, that passes authentication, refused ones included, before its body is read. The site's counter reserves a write for every attempt its author's budget admits and gives it back unless the write is stored, so it holds the writes that are stored and those still in flight | in process memory only, for the window of `FEEDBACK_WRITES_WINDOW_S` (600 s) and at most one more window after it ends, lost on a restart |
| Feedback bodies in flight (`serving/feedback_body.py`, `BodyBudget`) | per client its IP address (an IPv6 address by its /64) and how many request bodies it has in flight | keeping the memory that uploads take bounded, so one client cannot hold all of it | every create or reply through the widget, from before its body is read until its request is done | in process memory only, until none of that client's requests is in flight, lost on a restart |

`refs` on audit rows generally carries structural identifiers (group/site
slugs, ids), not raw personal data; this was spot-checked for `invitee_add`/
`invitee_remove` (`backend/src/toon/api/admin.py`) but was not audited
exhaustively across every action, so this is a strong indication, not a
guarantee.

An exception is known: `group_name` and `site_title` write the previous name
of a group and the previous title of a site into `refs` (`previous_name`,
`previous_title`), in full and not truncated. That is text members choose, so
it can be a person's name. Only the previous text is written, never the new
one, which stays on the group or site where it can be corrected at once. The
row is an admin operation and so is kept for 3 years, and an audit row cannot
be corrected, so a name corrected on the group or site stays readable in the
log until its term ends. A name or title that a rename sets is at most 200
characters (`TEXT_MAX_LENGTH` in `backend/src/toon/api/admin.py`). The create
routes accept longer ones: they have no maximum, because adding one would be a
breaking change that needs a new API major. So the text a rename replaces can
be longer than 200 characters, and a rename can write it into the log once.

What site feedback writes to the audit log, and what it does not, is in
`docs/audit-log.md`.

## Site feedback

Site feedback is a processing activity of its own: on a site whose feedback
setting is on, visitors send remarks about its pages, meant for the site team
(`docs/design.md` §16). What it keeps is in the table above. No IP address is
stored with feedback, and none of the details a browser sends about itself,
such as its user agent. An item sent without a name holds nothing about the
person, which the database enforces (`ck_feedback_items_author`). What the
consent page for feedback with an account tells the visitor is in
`docs/design.md` §16.6. Under its send button the widget links the
privacy statement.

The feedback is for everyone with a role on the site, the site team. The
email address of a submitter with an account is for the site's editors and
admins only. A submitter who sent with their account sees their own items
again in the widget, under "Mijn feedback" (my feedback), and can delete
them there.

**The hide cookie.** The widget stores nothing in the visitor's browser. Only
when the visitor hides the button until the browser closes does Toon set
`__Secure-toon-feedback-off` (for that site's path, `HttpOnly`, `Secure`,
`SameSite=Lax`, without an expiry). Its value is sealed by Toon and holds the
site and the moment the hiding ends, twelve hours on. It is functional: it
identifies no person and Toon keeps nothing of it.

## Proposed legal basis (AVG art. 6)

A proposal, to be confirmed by the controller:

- **Primary: art. 6(1)(e)**, a task carried out in the public interest / in
  the exercise of official authority.
- **Additional: art. 6(1)(c)**, a legal obligation for security and audit
  logging (duty of care, BIO, art. 32).
- **Not** consent (6(1)(a): an authority relationship makes consent
  problematic as a basis) and **not** legitimate interest (6(1)(f): generally
  unavailable to a government body acting in its public task).

The controller ("verwerkingsverantwoordelijke") and, where ZAD or another
hosting party is a processor, the processor relationship (art. 28) still need
to be recorded; see "Processors" below.

Site feedback, a proposal as well. The same art. 6(1)(e) is the candidate,
for the task of the organisation whose site collects the feedback. The tick
and the "Ja, met mijn account" (yes, with my account) on Toon's consent page
are not meant as consent under art. 6(1)(a). They are a safeguard that keeps
a site's own scripts from putting a visitor's name to feedback without them
knowing (`docs/design.md` §16.6), and the moment the visitor is told what the
site team will see. Who the controller is for the feedback on a site (the
organisation behind the site, the organisation that runs the installation, or
both), whether feedback from the public on a public site or a public preview
fits that task, and whether the operator is a processor for it, is for the
DPIA (open action 11).

## Published content: publisher responsibility and EEA/GHCR boundary

Published content can contain personal data, including special categories
under art. 9, and Toon does not inspect, classify or filter it. With site
feedback on, Toon reads the start of a site's HTML pages to find where its own
script goes, and nothing more (`docs/design.md` §16.2). The publisher
who uploads or deploys a site is responsible for what it contains; Toon's
role is limited to storing and serving what it is given, subject to the
access controls the publisher configures.

Published content should stay on infrastructure within the EEA: never in
GitHub.com repositories, and never baked into container images pushed to
GHCR. Verified against Toon's own build and deploy design:

- The container image (`containers/toon/Containerfile`) copies in only the
  backend source and the built admin SPA (`frontend/dist`); it contains no
  application content. Published content is never part of the image, so it
  never reaches GHCR.
- On ZAD, published content lives on the `content` persistent volume
  (`TOON_CONTENT_ROOT`, `docs/deploying-on-zad.md` §4, "the content volume"),
  separate from the image and from the database.
- Site feedback, its screenshots included, lives in the database with the
  rest of Toon's own records, not on the content volume and not in the image.
- Source code (this repository) is public on GitHub.com; that is
  intentional (open source, EUPL-1.2) and does not carry published content
  or personal data from any deployment.

**Unverified**: whether the ZAD platform itself, or the specific ZAD
tenant/region Toon runs in, guarantees storage within the EEA is a platform
question, not something this repository's code can establish. That
confirmation belongs with whoever operates the ZAD deployment.

## Retention and deletion

What Toon does today:

- Audit log rows are deleted by `audit/retention.py` once their retention
  term (180 days or 3 years, see the table above) has passed. The app runs it
  every night at 04:00 UTC (`audit/retention_job.py`), which enforces the
  maximum. A database trigger (`audit_log_delete_after_retention`,
  `backend/alembic/versions/0001_base.py`) enforces the minimum: it refuses
  to delete a row before its term is up, even from the application's own
  database account.
- `content_viewers` rows are purged the same way at a fixed 180 days from
  `last_seen_at`.
- During a (suspected) security incident a platform administrator puts both
  purges on hold, so the information about it stays for the three years BIO2
  5.28.01 asks (`docs/audit-log.md`, "During an incident").
- Secret links (`access_keys`) always carry a mandatory expiry date (90 days
  default, 365 maximum); nothing revives one after `expires_at`.
- CLI device authorizations and short-lived tokens expire and are swept
  automatically.
- Sessions live in process memory only and do not survive a restart.
- Site feedback goes with its site or group, when its submitter deletes an
  item sent with their account, and by the nightly cleanup on the terms in
  the table above (`docs/design.md` §16.12).

**Open gap: database backups.** Backups carry personal data (members,
invitees, content viewers, audit rows including encrypted IPs, site feedback
with its screenshots) and need an
explicit, bounded backup retention term, encryption at rest, and a deletion
process that actually covers backups, not only the live tables. This gap is
**not resolved**: Toon
runs on ZAD's shared PostgreSQL service, and this repository's documentation
(`docs/deploying-on-zad.md`) does not describe a backup retention or deletion
policy for that service. Whether backups are made, for how long, and whether
a deletion request against the live tables is honoured in the backups too is
unverified and belongs with the platform team.

This is also where the tension between the AVG's deletion obligation and the
Archiefwet's retention/preservation duty applies: a purged audit row or a
removed member is not necessarily allowed to disappear from an archival
perspective, and vice versa. The outcome of that trade-off should be
documented explicitly rather than defaulting either way; that has not been
done yet.

## Rights of data subjects

Access (art. 15), rectification (art. 16) and erasure (art. 17, limited by
the legal basis and the Archiefwet trade-off above) are not implemented as
self-service features today, apart from the feedback a person sent with their
account (below). An operator fulfilling a request would, as far as the code
supports it:

- **Access/rectification for a member**: a member can see and edit their own
  profile (`/-/profile`, `language` and, presumably, other self-service
  fields; not independently verified beyond `language`). An operator can
  look up and correct `members`/`invitees` rows directly.
- **Erasure of a member**: deactivating a member (`MemberStatus.DEACTIVATED`)
  revokes access and CLI sessions immediately (`docs/security.md`); this is
  not the same as deleting the row, and whether/how a `members` row is
  actually deleted rather than only deactivated is unverified.
- **Access to the audit trail about a person**: audit rows store a
  pseudonym, not a raw identifier, so answering "what is logged about me"
  requires the reverse lookup an operator performs through
  `POST /platform/audit/actor-identity` and the IP reveal endpoint
  (`audit_ip_reveal`), both themselves audited acts subject to a daily rate
  limit (`backend/src/toon/api/admin.py`, `docs/audit-log.md`). There is no
  self-service report a data subject can request through the product itself.
- **A person's name in a group name or site title**: the admin of the group
  or site corrects the current value at once (`PUT /groups/{group}/name`,
  `PUT /sites/{group}/{site}/title`). The previous values in the audit log
  cannot be changed and expire with the retention term, 3 years.
- **Erasure from published content**: entirely the publisher's
  responsibility; Toon has no mechanism to locate or redact personal data
  inside a published site.
- **Feedback a person sent with their account**: the submitter finds it
  again in the widget on that site, under "Mijn feedback" (my feedback),
  and deletes it there, its replies and screenshot along with it. That is
  the self-service route for access and erasure, and the privacy statement
  points to it. It has limits: "Mijn feedback" is there only while the
  submitter's session holds their consent for that site (`docs/design.md`
  §16.6) and a page of the site shows them the button, and it lists the
  newest 20 items only, saying so when it leaves older ones out. The items
  stay all the same, until their term in "Retention and deletion" ends. For
  whatever "Mijn feedback" does not show, a request to the operator is the
  route. An operator erases every item one person sent with their account,
  on every site, with its replies
  and screenshots, on the database (the account of `TOON_DB_URL`):

  ```sql
  DELETE FROM feedback_items WHERE author_kind = 'account' AND author_sub = '<sub>';
  ```

  That deletion writes no audit row. The `sub` belonging to an email address
  is in `members.sso_subject`, in `content_viewers.sso_subject` (only until
  180 days after that person last signed in on the content host), and on the
  items themselves when the IdP marked the address verified:

  ```sql
  SELECT DISTINCT author_sub FROM feedback_items WHERE author_kind = 'account' AND author_email = lower('<email>');
  ```

  More than one `sub` means more than one account: confirm which are the
  requester's before deleting by `sub`. An item sent while the address was
  unverified or absent stores no email address, so only the `sub` or
  `author_name` (the name from the IdP, cut to 200 characters) finds it. The
  same lookup serves an access request (art. 15).
- **Feedback sent with a name or without one**: its submitter cannot find it
  again. An item with a name can be found by that name only, which Toon never
  checked, and an item without a name holds nothing to find it by.

A documented, FG-approved process for handling an access/rectification/
erasure request (who receives it, identity verification, response term) is
an open item.

## Processors and registration (art. 28, art. 13)

Open items, to be completed by the controller before go-live:

- Whether ZAD (and any managed service it depends on, such as its shared
  PostgreSQL) qualifies as a processor, and if so, a processor agreement
  (verwerkersovereenkomst, art. 28).
- Registration of this processing in the controller's processing register
  ("verwerkingsregister").
- A privacy statement (privacyverklaring, art. 13) is served by the admin
  SPA at `/-/privacy` (texts in `frontend/src/i18n/pages.nl.ts` and
  `pages.en.ts`, keys `page.privacy.*`), reachable without logging in. It
  is the standard statement of the software: what Toon processes, why, for
  how long and who sees it, the data subject rights and the right to
  complain to the Autoriteit Persoonsgegevens. It names the operator and its
  address from `TOON_OPERATOR_NAME` and `TOON_OPERATOR_EMAIL`, and otherwise
  "the organisation that runs this installation". What only the controller
  can say, such as the legal basis, the FG and the hosting, the organisation
  that runs an installation adds itself. No FG has reviewed it. It covers site
  feedback as well, and the feedback widget and the consent page link to it.

## Risks

Following the risk section (Deel C) of the Model DPIA Rijksdienst, applied
to Toon's actual design.

| # | Risk | Mitigation in Toon today | Open |
|---|---|---|---|
| R.1 | Published content (script or otherwise) reads session cookies, keys or other sites' data | per-site content session cookie path scoping, origin-per-site enforcement, subresource checks, sandbox CSP mode (`docs/security.md`, "Access to content", "Headers and CSP") | shared content hostname across sites remains a stopgap per `docs/security.md` (§5.10); sandbox/external-sources are per-site toggles an admin can turn off |
| R.2 | Access level set too broadly, spreading sensitive content further than intended | access is deny-by-default per site (`NOBODY` unless a base or extra is chosen), admin-only to change (`access/gate.py`, `constants.py`) | no automatic warning or review workflow when a site is set to `public` |
| R.3 | Leaked secret link | constant-time comparison, mandatory expiry (90/365 days), single code-page flow, key excluded from audit/access logs (`docs/security.md`, "Access to content") | an old link sends its key to the redirect on the old host, which has to log without the query (`docs/deploying-on-zad.md`, §12) |
| R.4 | Unauthorized access via a broken OIDC/session/authorization check | alg allowlist, `iss`/`at_hash` checks, CSRF, per-endpoint server-side authorization, neutral 404s (`docs/security.md`) | account separation on the shared database is not possible on ZAD today (`docs/security.md`, "DB account separation") |
| R.5 | Personal data kept longer than necessary, in logs or in backups | audit log and `content_viewers` purged every night by a job inside the app (`audit/retention_job.py`). A database trigger refuses a delete before the term ends, so the trigger guards the minimum and the job the maximum (`docs/audit-log.md`) | a night the purge fails, expired rows stay until a later run succeeds. The failure is an ERROR in the application log. A hold during an incident (`audit_purge_holds`) keeps every row past its term until a platform administrator ends it. While it is open, platform administrators see a warning on every admin page and the job logs a WARNING every night. Database backup retention/deletion is undocumented and unverified, see "Retention and deletion" above |
| R.6 | Re-identification of a person from a logged identifier or IP address | actor pseudonymisation (HMAC-SHA256, keyed pepper), IP truncation by default with reversible encryption gated behind an explicitly audited reveal action (`audit/pseudonymisation.py`, `audit/ip_crypto.py`) | the reveal key (`TOON_AUDIT_IP_KEY`) and the pepper are operational secrets whose storage falls outside this repository. Both can be rotated (`docs/audit-log.md`, "Rotating the pepper and the IP key"), but a rotation protects only the rows written after it: older rows keep the old value, so a leaked value plus a copy of the database still re-identifies them |
| R.7 | Personal data in published content transferred outside the EEA | content never enters the container image or GHCR; content lives on the ZAD content volume (verified, see "Published content" above) | whether the ZAD platform/region itself guarantees EEA storage is unverified |
| R.8 | Audit log tampering hides unlawful access or a data subject's history | append-only triggers, hash chain per shard, published chain head for external verification (`docs/audit-log.md`) | the schema owner (the one database account Toon has on ZAD) can disable the triggers, the `TRUNCATE` guard among them, and then empty the table; this is a documented, not a closed, gap (`docs/audit-log.md`, "Known gaps") |
| R.9 | Member-chosen text in a log that cannot be corrected: the previous name of a group or title of a site stays readable in the audit log after it is corrected on the group or site, and it can be a person's name | only the previous text is logged, never the new one; a name or title that a rename sets is at most 200 characters (`TEXT_MAX_LENGTH`); like every admin operation the row expires after 3 years (`docs/audit-log.md`, "Retention") | an audit row cannot be changed or removed before its term ends. Residual risk: the create routes have no maximum (adding one needs a new API major), so an active member could create a group with a very long name and rename it, which writes that text once into a row kept 1096 days (3 years). That is bounded by the creation budget (20 per hour, new groups, sites and addresses together) and by the request body limit of whatever stands in front of Toon (the application sets none for JSON requests), and it is traceable to the member through the `group_create` and `group_name` rows (`site_create` and `site_title` for a site). Whether keeping the previous text is necessary and proportionate is for the DPIA (open action 1) |
| R.10 | Personal data of third parties, and special categories, in feedback: a screenshot shows whatever was on the submitter's screen, a comment says whatever its writer wants, and the writer can be anyone a site lets give feedback, the public included, about sites that may be non-public | the widget shows the screenshot before sending, with a notice that it can show data of others or of the submitter, and parts can be covered or the screenshot left out. Feedback is for the site team only, and the screenshot of a resolved item goes after 30 days, the item after 90, an open item after 365 (`docs/design.md` §16.12). Size and storage are capped (`docs/design.md` §16.10) | nothing redacts what the submitter does not cover, and an open item keeps its screenshot up to 365 days. Whether screenshots are necessary and proportionate, and how people visible in a screenshot are informed (art. 14), is for the DPIA (open action 11) |
| R.11 | Re-identification of feedback sent without a name: its time and page can be set beside the access log of a non-public site (one row per page view, with a pseudonym and the IP address) and beside a login, and the text, the screenshot and the window size can say who sent it | no IP address or user agent is stored with feedback, and an item without a name holds no `sub`, name or email address, which the database enforces (`ck_feedback_items_author`). Audit lookups are for a platform administrator only, with a recorded reason and a daily limit. The privacy statement says that the text or the screenshot can still identify the submitter | `created_at` is kept in full, to the microsecond, and the window size with it. Whether either should be coarser is for the DPIA (open action 11) |
| R.12 | Another site acting as a visitor who gave feedback with their account: on a site whose shielding from other sites is off, the page of another site without it can read a page's token, and with it, for the token's twelve hours, send feedback in that visitor's name, and read, reply to and delete that visitor's own feedback on that site | the shielding is on by default, and a site that turns it off opens its pages to every other site without it already. The token is sealed, expires after twelve hours and is checked again on every call (`docs/security.md`, "What site feedback adds, and what is accepted") | accepted by the product owner on 2026-11-17, until every site has an origin of its own (TDR-0002) or the account actions move into a window Toon owns |

## Open actions

1. Commission the full DPIA (Model DPIA Rijksdienst), with FG consultation
   (art. 35(2)), before production go-live; this document is the input, not
   the assessment. Input for the necessity and proportionality assessment
   (art. 35(7)(b)): the audit log keeps the previous name of a group and the
   previous title of a site, in full, for 3 years, so that a rename can be
   traced; that text is chosen by members, can hold a person's name and cannot
   be corrected in the log (R.9).
2. Confirm the legal basis (art. 6) and, if special categories of data are
   expected to appear structurally in published content, the art. 9
   exception that applies.
3. Establish and document database backup retention, encryption at rest, and
   a deletion process that covers backups.
4. Document the Archiefwet/AVG retention trade-off explicitly, for both the
   audit log and member/invitee data.
5. Determine whether ZAD (or a service it depends on) is a processor and, if
   so, put a processor agreement (art. 28) in place.
6. Register the processing in the controller's verwerkingsregister. Input for
   the categories of personal data (art. 30(1)(c)): labels that members choose,
   such as group names and site titles, may contain a person's name; they stand
   on the group or site and, as previous values, in the audit log.
7. Complete the privacy statement served at `/-/privacy` with the legal
   basis, the FG and the hosting, and have the FG review it.
8. Define and document a self-service or FG-mediated process for access,
   rectification and erasure requests, including how a member row is
   actually deleted (not only deactivated).
9. Confirm, with the ZAD platform team, that the region/tenant Toon runs in
   guarantees storage within the EEA.
10. Decide whether publishers need a content-classification step (personal
    data / special categories) before publishing.
11. DPIA input for site feedback (the section "Site feedback" above,
    `docs/design.md` §16):
    - Whether collecting feedback is necessary and proportionate, above all
      without an account, from the public on a public site or a public
      preview, and with screenshots made by themselves (R.10).
    - The retention terms, by default 30 days for the screenshot of a
      resolved item, 90 for a resolved item and 365 for an open one, and the
      full moment an item keeps (R.11).
    - How people visible in a screenshot are informed (art. 14).
    - Who the controller is for the feedback on a site, and whether the
      organisation that runs the installation is its processor (art. 28).
    - Registering the activity in the verwerkingsregister of each
      organisation whose site collects feedback.

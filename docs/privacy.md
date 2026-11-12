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
  type, or without a name, as far as the site's admin allows. Where the site
  is public, or a preview of it is public by an access of its own, and the
  admin allows feedback without an account, that can be anyone, the public
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
| `content_viewers` (`models/audit.py`) | `sso_subject`, `email` (nullable, may be unverified), `email_verified`, `last_seen_at` | makes a content-host SSO login traceable to a person for the audit log; a content-only viewer gets no `members` row | SSO Rijk OIDC claims, on every successful content-host login, signing in to give feedback with an account included, on a public site too | 90 days from `last_seen_at`, purged by `audit/retention.py` (`docs/audit-log.md`, "Retention") |
| `invitees` (`models/publication.py`) | `identifier` (email address or `sub`, lowercased), `added_by`, `added_at` | per-site invitee access list | admin input | until removed by an admin; no automatic expiry |
| `access_keys` (`models/publication.py`) | `label`, `selector`, `verifier_hash` (SHA-256 of a random secret) | secret-link credential, not itself tied to a person | generated on creation | mandatory `expires_at`, 90 days default, 365 days maximum (BIO2 5.18.02, `docs/security.md`) |
| `audit_log_entries` (`models/audit.py`) | `actor_pseudonym` (HMAC-SHA256 of the actor identifier under `TOON_AUDIT_PEPPER`), `ip_truncated` (/24 IPv4, /48 IPv6), `ip_encrypted` (AES-256-GCM, reversible, under the separate `TOON_AUDIT_IP_KEY`), `action`, `result`, `reason_code`, `refs` (for `group_name` and `site_title` also the previous name or title: free text that members choose, see below) | security and access audit trail | every login, access decision, admin action and CLI event | 90 days for pure viewing/login-success actions, 3 years for everything else, including every refusal, admin operation and publication (`docs/audit-log.md`, "Retention"; enforced in the database by `audit_log_retention()`, `backend/alembic/versions/0001_base.py`) |
| Admin/content sessions (`auth/sessions.py`) | `sub`, `email`, `name`, refresh token (kept in process memory only, `repr=False`, never logged) | keeps someone logged in | OIDC login | in-memory only, not persisted to the database; expires after 12 hours (`MAX_SESSION_AGE`) or on logout; lost entirely on a restart (`docs/security.md`, "Sessions do not survive a restart") |
| `cli_device_authorizations` (`models/cli.py`) | `device_hash`, `user_code_hash`, `client_name`, `ip_truncated`, `member_id` | `toon login` device pairing | CLI device flow (RFC 8628) | short-lived: expires after 10 minutes, deleted once exchanged, swept by the cleanup job |
| `cli_sessions` / `cli_refresh_tokens` (`models/cli.py`) | `member_id`, `client_name`, token selector/hash pairs | CLI publishing sessions | approved by a member in an admin session | access token 1 hour; session up to 30 days after last refresh, 90 days absolute (`docs/security.md`, "CI id token"/"CLI device flow") |
| Published content (files on the content volume) | whatever the publisher includes: potentially names, contact details or special categories of data (AVG art. 9) | the published site itself | uploaded by a publisher (member or CI) | until the site or version is removed; Toon does not parse, classify or scan content for personal data |
| `feedback_items` (`models/feedback.py`) | The comment. The page (its path within the site or preview, its title). The place on it (`anchor`: the CSS selector of an element and the point in it, or the quoted text with at most 32 characters around it). The window size (`viewport`: width, height, device pixel ratio), the version, and the moment it came in (`created_at`). Per author kind: `account` the SSO `sub`, the name, and the email address when the IdP marked it verified. `name` the name the submitter typed, which nobody checked. `anonymous` nothing about the person. The status, and the member who changed it last (`status_changed_by`, an id) | collecting remarks on a site's pages, so that the site team can read, answer and resolve them | the submitter, through the widget. The account fields come from the consented content session, never from the request | with its site or group, or when a site admin deletes it, or its submitter for an item sent with an account. Otherwise a resolved item 90 days after `resolved_at` and an item still new or seen 365 days after `created_at` (`TOON_FEEDBACK_RESOLVED_RETENTION_DAYS`, `TOON_FEEDBACK_OPEN_RETENTION_DAYS`), see "Retention and deletion" |
| `feedback_replies` (`models/feedback.py`) | The text of a reply. For a reply of the site team the member's id and their name as it was when they wrote it (`author_name`, never their email address), for a reply of the submitter neither | the conversation between the site team and the submitter | members, in the admin or with the CLI, and submitters who sent with an account, under "Mijn feedback" in the widget | with its item |
| `feedback_screenshots` (`models/feedback.py`) | An image of the submitter's viewport, or of the part of it they chose, with what they covered painted over. It can show any personal data that was on the screen: of the submitter, of people named on the page, of anyone else. On a page with a part that scrolls on its own, such as the main pane of an app shell, the image shows that part from its top, which can be page content that was not on the screen (`docs/design.md` §16.4) | showing the site team what the submitter saw | made in the submitter's browser by the widget. The submitter sees it before sending and can cover parts of it or leave it out | with its item, and for a resolved item 30 days after `resolved_at` (`TOON_FEEDBACK_SCREENSHOT_RETENTION_DAYS`) |
| Feedback write counters (`serving/feedback_api.py`, `WriteCounter`) | per author and site a key, the start of its window and a count. The key is the site's id together with the session's `sub` when the page token carries a consented session, with the `sub` of the session that let a signed-in visitor in when the token carries one for the gate alone (under a key of its own), and otherwise with the client's IP address (an IPv6 address by its /64) | limiting misuse of the widget | every write through the widget that passes the check, a create or a reply once its body has arrived, and a refused one only until its request ends | in process memory only, for the window of `TOON_FEEDBACK_WRITES_WINDOW_S` (600 s) and at most one more window after it ends (a refund that takes a count back to 0 drops the key at once), lost on a restart |
| Feedback bodies in flight (`serving/feedback_api.py`, `BodyBudget`) | per client its IP address (an IPv6 address by its /64) and how many request bodies it has in flight | keeping the memory that uploads take bounded, so one client cannot hold all of it | every create or reply through the widget, from before its body is read until its request is done | in process memory only, until none of that client's requests is in flight, lost on a restart |

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

The feedback actions in the audit log (`site_feedback`, `feedback_status`,
`feedback_reply`, `feedback_delete`) carry the group, the site, the item's id
and the change, never the comment, the quote, a reply, a name or an email
address (`docs/audit-log.md`). Nothing a visitor does through the feedback
widget writes an audit row of its own: giving feedback, replying or deleting
under "Mijn feedback", saying yes to feedback with their account or taking
that back, hiding and showing the button, and the refusals of all of these.
What happens on the way writes its rows as anywhere else (`docs/design.md`
§16.13). A sign-in for it writes its `login` row. A session the identity
provider ends writes an `idp_session_ended` row, kept three years with the
IP address of the request on which Toon finds that out, a widget call
included. On a non-public site every page view is in the log already.

## Site feedback

Site feedback is a processing activity of its own. A site admin turns it on
for a site (it is off by default, and then on previews only unless the admin
chooses the live site as well), visitors send remarks about the site's pages,
and the site team reads, answers and resolves them (`docs/design.md` §16).
What it keeps is in the table above.

**What it does not keep.** No IP address is stored with feedback, and none of
the details a browser sends about itself, such as its user agent. To limit
misuse, the widget API counts accepted writes per author on each site and
per site, in process memory only (the write counters above). The author is
the account when the visitor said yes to feedback with it on that site or
had to sign in to see the page, and otherwise the IP address, also of a
visitor who is signed in. The rate limiter counts the requests under
`/-/feedback/` in windows of a minute, also in memory only: the widget's API
calls, which carry no cookie, per IP address, and a request that carries a
content session cookie, such as the consent page, by the SSO `sub`, like
every signed-in request. A window that has ended goes on a later count, or
with a restart. Until a create or a reply is done, the widget
API also counts the bodies in flight per IP address (an IPv6 address by its
/64), in memory only, and forgets the address as soon as none of its requests
is in flight (the bodies in flight above). The moment an item came in is kept
in full, and the size and pixel ratio of the browser window with it. What the
infrastructure in front of Toon logs about a request is outside this
repository, as it is for every other request.

**Without a name.** An item sent without a name stores nothing about the
person, also when the visitor is signed in: the database refuses an item
without a name that carries a `sub`, a name or an email address
(`ck_feedback_items_author`). It is not linked to a page view. On a non-public
site every page view is in the audit log, though, with a pseudonym and the IP
address, so a platform administrator could put the times side by side (R.11).
The widget says before sending, and the privacy statement says too, that the
text or the screenshot can still say something about the submitter ("Zonder
naam weet het siteteam niet van wie dit komt. Je tekst of de schermafbeelding
kan nog wel iets over je zeggen."). On a site that needs a sign-in, the page
token of a signed-in visitor who did not choose to send with their account
carries their session, so that Toon can check their access again when they
send, with a name or without one. Nothing of that session is stored with the
item. Its `sub` only keys the write counter in process memory (the write
counters above), under a key of its own on that site, so that colleagues
behind one network address do not share a budget and the count of the same
person's consented session stays apart. One thing remains for the privacy
owner to weigh: the site's own scripts hold the page token, so they could
find out whether that one person has spent their budget on that site (a
malformed create answers 429 when it is spent), where the address as key
told them only about the network as a whole. What the person writes on other
sites stays out of that count.

**With an account.** An item is sent with an account only after the visitor
said yes on Toon's own consent page. It shows who they are signed in as
(their name, or their account id without one) and names what the site team
will see with every item: their name and an email address the IdP marked
verified, each when there is one, or else only that the item comes from
someone with an account. With the address it says that only the site's
editors and admins see it. The team never sees the account id. Saying yes
takes a tick in a box and a press on the button. The page also says how long
the consent lasts (until they log out or the session ends, twelve hours at
most), and that what they send in that time keeps those details as long as
the feedback exists. It says that signing in keeps their SSO id and email
address until 90 days after their last sign-in, that every sign-in stays 90
days in the access log with their IP address, and that a session the
identity provider ends early keeps the IP address three years there. It also
says that whoever came to the site through a secret link has every page they
view there after the consent, until the session ends, in the access log for
90 days with their account. The way back through the login hands out the
site cookie, so the page views the gate logs, anonymous until then, carry
the visitor's pseudonym beside the link's selector: an allowed view kept 90
days, a refusal three years (`docs/audit-log.md`).
Signing in for that, on a public site too, writes a `content_viewers` row
(kept 90 days from the last sign-in) and a `login` audit row (kept 90 days),
as every content-host login does. A sign-in that
fails or is broken off (an IdP that cannot be reached, an error back from it
such as a cancel there, or a return after the ten minutes of the attempt)
writes a `login` row with the result `refused` instead, kept three years
(1096 days) with the IP address, like every refusal. A session the IdP ends
early (one that idled out, was ended or was revoked there) writes an
`idp_session_ended` row once Toon finds out, kept three years with the IP
address of the request it finds out on: a later page view or widget call of
the visitor's, or the IdP's own call to end the session. Before any sign-in,
under "Feedback geven met je account" (give feedback with your account) and
"Met je account versturen" (send with your account), the widget says that
signing in keeps the SSO id, the email address and the IP address until 90
days after the last sign-in, and that a sign-in that fails, that the visitor
stops, or whose session the identity provider ends early keeps the IP
address three years in the access log. The account-only menu also links the
privacy statement. A visitor let in by a secret link takes that link to the
consent page as a sealed ticket in the address: the key, the site and an
expiry, nothing about the person (`docs/design.md` §16.6). Once the visitor
said yes, the composer says only that they send with their account and that
what the site team sees of them was on the consent page.

**What the submitter is told.** Under its send button the widget says that
everyone with a role on the site in Toon sees the feedback, people who only
look on included, after how many days resolved feedback is deleted, and links
the privacy statement (on a shielded site in the same tab, with a note that
text not yet sent is lost). Without an account it adds that the submitter
cannot see or delete the feedback later.
The screenshot is shown before it is sent, with the notice that it shows what
is on the screen, data of other people or of the submitter included, and
the choices to cover parts of it or to send without it.

**Who sees what.** Everyone with a role on the site, the site team, sees its
feedback: the comment, the place, the screenshot and the name. The email
address of a submitter with an account is for the site's editors and admins
only (the admin API leaves it out for a reader). A submitter who sent with
their account sees their own items again in the widget, under "Mijn feedback"
(my feedback), with the team's replies and the name of the member who wrote
each, never an email address. Items sent with only a name or without a name
cannot be found again by their submitter. The site team can also fetch
feedback with the Toon CLI (`toon feedback`), which leaves names and email
addresses out unless asked for them with `--with-contact`. A publisher who
loads that output into an AI assistant decides about that processor
themselves, and the privacy statement says so.

**The hide cookie.** The widget stores nothing in the visitor's browser. Only
when the visitor chooses to hide the button until the browser closes does
Toon set `__Secure-toon-feedback-off` (for that site's path, `HttpOnly`,
`Secure`, `SameSite=Lax`, without an expiry, so it goes when the browser
closes). Its value is sealed by Toon and holds the site and the moment the
hiding ends, twelve hours on, after which it counts for nothing: the button
stays hidden until the browser closes, and twelve hours at most. It is
functional: it identifies no person and Toon keeps nothing of it. Hiding it
for a minute sets nothing.

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
DPIA (open action 11). The admin interface warns a site admin whose site is
public, or has a preview that is public by an access of its own, and takes
feedback without an account, also while only the previews take it, that
their organisation has to name this in its own privacy statement.

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
  term (90 days or 3 years, see the table above) has passed, enforced not
  only by that job but by a database trigger
  (`audit_log_delete_after_retention`, `backend/alembic/versions/0001_base.py`)
  that refuses to delete a row before its term is up, even from the
  application's own database account.
- `content_viewers` rows are purged the same way at a fixed 90 days from
  `last_seen_at`.
- Secret links (`access_keys`) always carry a mandatory expiry date (90 days
  default, 365 maximum); nothing revives one after `expires_at`.
- CLI device authorizations and short-lived tokens expire and are swept
  automatically.
- Sessions live in process memory only and do not survive a restart.
- Site feedback goes with its site or group, when a site admin deletes an
  item, and when the submitter deletes an item they sent with their account,
  each with its replies and screenshot. Beyond that the nightly cleanup (03:00
  UTC, `backend/src/toon/previews/cleanup_job.py`) deletes, in one transaction:
  the screenshot of a resolved item 30 days after it was resolved, a resolved
  item 90 days after it was resolved, and an item still new or seen 365 days
  after it came in. Those are the defaults of
  `TOON_FEEDBACK_SCREENSHOT_RETENTION_DAYS`,
  `TOON_FEEDBACK_RESOLVED_RETENTION_DAYS` and
  `TOON_FEEDBACK_OPEN_RETENTION_DAYS` (0 keeps, at most 3650,
  `docs/deploying-on-zad.md`). Reopening an item clears its resolved moment,
  so it falls under the term for open items again, counted from when it came
  in: an item older than that term goes at the next nightly cleanup once it
  is reopened. The purge writes no audit row, so that no trace of what it
  removes stays behind for three years. It logs one line per run at INFO with
  the number of items and screenshots it removed, but a production process
  does not show that line: the server that `containers/toon/entrypoint.sh`
  starts leaves Toon's own loggers at Python's default level, WARNING
  (`docs/design.md` §16.12). So nothing in production records that the purge
  ran. A term counts from a database timestamp, not from a setting at the
  time, so changing a term reaches the items that are there already.

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
  again in the widget on that site, under "Mijn feedback" (my feedback), with
  the team's replies, and deletes it there, its replies and screenshot along
  with it. That is the self-service route for access and erasure, and the
  privacy statement points to it. "Mijn feedback" is in the button only
  while the submitter's session holds their consent for that site
  (`docs/design.md` §16.6). A returning submitter first chooses "Met je
  account versturen" (send with your account) or "Feedback geven met je
  account" (give feedback with your account), signs in where their session
  has ended, and says yes again on Toon's consent page. "Stoppen met je
  account" (stop using your account) ends that consent afterwards. The
  privacy statement says this too. The route has limits. "Mijn feedback" shows
  the newest items only, at most 50 in at most 256 KiB, and says so when it
  leaves older ones out ("Oudere feedback staat niet in deze lijst.", older
  feedback is not in this list): the submitter reaches an older item only
  after deleting newer ones. And it is there only while a page of the site
  shows the submitter the button: not once feedback is switched off, not on a
  site that takes feedback on its previews only while none of them still
  opens, and not once the submitter's access to the site is gone. The items
  stay all the same, until their term in "Retention and deletion" ends. For
  whatever "Mijn feedback" does not show, a request to the operator is the
  route. A site admin deletes any item of the site
  (`DELETE /sites/{group}/{site}/feedback/{id}`). An operator erases every
  item one person sent with their account, on every site, with its replies
  and screenshots, on the database (the account of `TOON_DB_URL`):

  ```sql
  DELETE FROM feedback_items WHERE author_kind = 'account' AND author_sub = '<sub>';
  ```

  That deletion writes no audit row. The `sub` belonging to an email address
  is in `members.sso_subject`, in `content_viewers.sso_subject` (only until
  90 days after that person last signed in on the content host), and on the
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
- **A member's name in replies**: `feedback_replies.author_name` keeps the
  name a member had when they replied, also when the member is deleted, since
  the foreign key only empties `member_id`. An operator who erases a member
  clears that name first, while `member_id` still points at them:

  ```sql
  UPDATE feedback_replies SET author_name = NULL WHERE member_id = '<member id>';
  ```

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
  feedback as well. Its opening says that on sites that collect feedback Toon
  also processes what visitors send with the feedback button
  (`page.privacy.intro`), and the rest uses the same words as the section
  "Site feedback" above:
  what is kept and what is not, the purpose, the AI assistant note, the
  retention terms and who sees what (keys `page.privacy.data.feedback`,
  `data.feedbackTechnical`, `data.feedbackAnonymous`, `purpose.feedback`,
  `processors.ai`, `retention.feedback` and `visibility.feedback`). The
  feedback widget links to it, and so does the consent page.
- An AI assistant as a processor of feedback: the site team can load feedback
  into one through the Toon CLI, for instance with the `toon-feedback` skill
  of the Claude Code plugin (`docs/skill.md`). That is the publisher's own
  choice of processor, outside Toon. The CLI leaves names and email addresses
  out unless asked (`--with-contact`), and the skill asks for them only when
  the user needs to contact someone. Whether an organisation allows that, and
  with which assistant, is for its own policy. A screenshot fetched with
  `toon feedback show --screenshot`, and whatever an assistant keeps of
  feedback in its own session history, are copies outside Toon: the
  retention terms and a submitter's own delete do not reach them, and
  removing them is the site team's job, which the privacy statement says as
  well (`page.privacy.processors.ai`). The `toon-feedback` skill moves the
  directories it makes for screenshots and replies to the Trash once it has
  used them, where they stay until the Trash is emptied, and names them to
  the user where the shell has no `trash`.

## Risks

Following the risk section (Deel C) of the Model DPIA Rijksdienst, applied
to Toon's actual design.

| # | Risk | Mitigation in Toon today | Open |
|---|---|---|---|
| R.1 | Published content (script or otherwise) reads session cookies, keys or other sites' data | per-site content session cookie path scoping, origin-per-site enforcement, subresource checks, sandbox CSP mode (`docs/security.md`, "Access to content", "Headers and CSP") | shared content hostname across sites remains a stopgap per `docs/security.md` (§5.10); sandbox/external-sources are per-site toggles an admin can turn off |
| R.2 | Access level set too broadly, spreading sensitive content further than intended | access is deny-by-default per site (`NOBODY` unless a base or extra is chosen), admin-only to change (`access/gate.py`, `constants.py`) | no automatic warning or review workflow when a site is set to `public` |
| R.3 | Leaked secret link | constant-time comparison, mandatory expiry (90/365 days), single code-page flow, key excluded from audit/access logs (`docs/security.md`, "Access to content") | an old link sends its key to the redirect on the old host, which has to log without the query (`docs/deploying-on-zad.md`, §12) |
| R.4 | Unauthorized access via a broken OIDC/session/authorization check | alg allowlist, `iss`/`at_hash` checks, CSRF, per-endpoint server-side authorization, neutral 404s (`docs/security.md`) | account separation on the shared database is not possible on ZAD today (`docs/security.md`, "DB account separation") |
| R.5 | Personal data kept longer than necessary, in logs or in backups | audit log and `content_viewers` retention enforced by database trigger, not only application code (`docs/audit-log.md`) | database backup retention/deletion is undocumented and unverified, see "Retention and deletion" above |
| R.6 | Re-identification of a person from a logged identifier or IP address | actor pseudonymisation (HMAC-SHA256, keyed pepper), IP truncation by default with reversible encryption gated behind an explicitly audited reveal action (`audit/pseudonymisation.py`, `audit/ip_crypto.py`) | the reveal key (`TOON_AUDIT_IP_KEY`) and the pepper are operational secrets whose storage falls outside this repository. Both can be rotated (`docs/audit-log.md`, "Rotating the pepper and the IP key"), but a rotation protects only the rows written after it: older rows keep the old value, so a leaked value plus a copy of the database still re-identifies them |
| R.7 | Personal data in published content transferred outside the EEA | content never enters the container image or GHCR; content lives on the ZAD content volume (verified, see "Published content" above) | whether the ZAD platform/region itself guarantees EEA storage is unverified |
| R.8 | Audit log tampering hides unlawful access or a data subject's history | append-only triggers, hash chain per shard, published chain head for external verification (`docs/audit-log.md`) | the schema owner (the one database account Toon has on ZAD) can disable the triggers, the `TRUNCATE` guard among them, and then empty the table; this is a documented, not a closed, gap (`docs/audit-log.md`, "Known gaps") |
| R.9 | Member-chosen text in a log that cannot be corrected: the previous name of a group or title of a site stays readable in the audit log after it is corrected on the group or site, and it can be a person's name | only the previous text is logged, never the new one; a name or title that a rename sets is at most 200 characters (`TEXT_MAX_LENGTH`); like every admin operation the row expires after 3 years (`docs/audit-log.md`, "Retention") | an audit row cannot be changed or removed before its term ends. Residual risk: the create routes have no maximum (adding one needs a new API major), so an active member could create a group with a very long name and rename it, which writes that text once into a row kept 1096 days (3 years). That is bounded by the creation budget (20 per hour, new groups, sites and addresses together) and by the request body limit of whatever stands in front of Toon (the application sets none for JSON requests), and it is traceable to the member through the `group_create` and `group_name` rows (`site_create` and `site_title` for a site). Whether keeping the previous text is necessary and proportionate is for the DPIA (open action 1) |
| R.10 | Personal data of third parties, and special categories, in feedback: a screenshot shows whatever was on the submitter's screen, a comment says whatever its writer wants, and the writer can be anyone a site's admin lets give feedback, the public included, about sites that may be non-public | the screenshot is made in the browser and shown to the submitter before sending, with a notice that it can show data of others or of themselves, and parts can be covered or the screenshot left out. It is made by itself only for a point or a text, and for a whole page only when the submitter asks. On a shielded page images appear as grey areas. Feedback is for the site team only, screenshots and the admin API's feedback answers (the list, an item, a status change, a reply) are served `no-store` from the admin host, and the screenshot of a resolved item goes after 30 days, the item after 90, an open item after 365 (`docs/design.md` §16.12). Size and storage are capped (§16.10) | nothing redacts what the submitter does not cover, and an open item keeps its screenshot up to 365 days. Whether screenshots made by themselves are necessary and proportionate, and how people visible in a screenshot are informed (art. 14), is for the DPIA (open action 11) |
| R.11 | Re-identification of feedback sent without a name: its time and page can be set beside the access log of a non-public site (one row per page view, with a pseudonym and the IP address) and beside a login, and the text, the screenshot and the window size can say who sent it | no IP address or user agent is stored with feedback, and an item without a name holds no `sub`, name or email address, which the database enforces (`ck_feedback_items_author`). Audit lookups are for a platform administrator only, with a recorded reason and a daily limit. The widget and the privacy statement say that the text or the screenshot can still identify the submitter | `created_at` is kept in full, to the microsecond, and the window size with it. Whether either should be coarser is for the DPIA (open action 11) |

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

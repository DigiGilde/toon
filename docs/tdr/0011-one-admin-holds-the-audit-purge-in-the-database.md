# TDR-0011: One platform administrator holds the audit purge, in the database

- Status: Proposed
- Date: 2026-11-15

## Context

The nightly job deletes audit rows and content viewers past their
retention (`docs/audit-log.md`, "Retention"). BIO2 5.28.01 keeps the
logging of a (suspected) security incident for at least three years, so
during an incident the purge has to stop at once, before the next night
takes a 180-day row the investigation needs.

The first version was a setting, `TOON_AUDIT_PURGE_PAUSED` (a143c3a). On
ZAD a setting takes a deployment refresh, which restarts the pod: that
empties the pod's logs and signs everyone out, because sessions live in
memory (`docs/security.md`). Both are what an incident needs to keep.

## Decision

The hold is a row in `audit_purge_holds`, started with "Pause purge" and
ended with "Resume purge" on the platform page (a6fc9f5). One platform
administrator does either, with a reason, and it takes effect at once.
While a hold is open the delete guards in the database refuse every
delete. A hold is never deleted or rewritten, only ended once. Ending
one is not committed unless its audit row is written. Starting one is,
even when that row fails: a hold only protects, and it records who
started it and why.

## Consequences

- One administrator can end a hold that another started. The next night
  then purges everything that expired in the meantime, for good. The
  audit row and the immutable hold row say who did it and why, but
  nothing stops it. Rows within their retention stay protected by the
  triggers whatever happens to a hold.
- No second administrator has to be reachable to start a hold during an
  incident, and none to end it afterwards.
- A forgotten hold keeps personal data past its term without an end
  date. Platform administrators see a warning on every admin page while
  it is open, and the nightly log has a WARNING (`docs/privacy.md`,
  R.5). Nobody outside the admin is told.
- The reasons stay in `audit_purge_holds` for good, and in the audit log
  for three years, so they must not contain personal data. An e-mail
  address is refused.

## Revisit when

A hold is ended while an investigation still needed the data, an audit
or the BIO asks for dual control over destroying logging, or a hold is
found open long after its incident was closed.

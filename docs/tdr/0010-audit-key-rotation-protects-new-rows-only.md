# TDR-0010: Audit key rotation protects new rows only

- Status: Accepted
- Date: 2026-11-01

## Context

The audit log stores a pseudonym of the actor (an HMAC under
`TOON_AUDIT_PEPPER`) and the full IP address encrypted under
`TOON_AUDIT_IP_KEY`. After a leak or on a schedule, an operator needs to
replace either value. Rows are immutable (`audit_log_no_update`), and the
chain hash of a row covers its `actor_pseudonym` and its `ip_encrypted`
(`docs/audit-log.md`, "The integrity chain"). Rewriting an old row at
rotation would change every later hash and make every published chain
head mismatch, which is indistinguishable from tampering.

## Decision

Rotation uses `_PREVIOUS` lists. New rows use the current pepper and IP
key. `TOON_AUDIT_PEPPER_PREVIOUS` and `TOON_AUDIT_IP_KEY_PREVIOUS` hold
the values they replaced, newest first, so older rows stay readable: a
row is decrypted under the key whose id its value carries, and a lookup
matches a pseudonym under any configured pepper. Old rows are never
rewritten. The procedure is in `docs/audit-log.md` ("Rotating the pepper
and the IP key") and `docs/deploying-on-zad.md` §5.

## Consequences

- Rotation limits the damage from that moment on and repairs nothing.
  Whoever holds a leaked value and a copy of the database or a backup can
  still open the older rows, and a leak is a possible personal data
  breach. The docs say so plainly.
- Older rows lose their exposure only through the nightly retention
  purge, at most 1096 days after the rotation.
- A previous value has to stay configured until every row written under
  it is purged, so the lists only grow until then.

## Revisit when

The hash chain design changes so that the covered fields can be replaced
without breaking the published heads, or a leak of a key requires
protecting rows that already exist.

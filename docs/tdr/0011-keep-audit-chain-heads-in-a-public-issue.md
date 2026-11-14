# TDR-0011: Keep the audit chain heads in a public issue

- Status: Accepted
- Date: 2026-11-15

## Context

BIO2 8.15.06 asks for a periodic check that log files exist unchanged
during their retention, at a frequency set by a risk assessment, by a
functionary independent of the people who run the system. Toon logs the
head of every audit chain each night, and `just verify-audit-head` holds
such a line against the database (`docs/audit-log.md`, "Publishing the
chain head"). A line is only evidence once it is kept out of reach of
whoever runs the database, and nothing kept it: `zadctl logs` returns at
most 1000 lines and a rollout empties them. Shipping the log to a WORM
store or SIEM, and a scheduled workflow with a read-only ZAD token, both
wait on ZAD (#227, #228, closed for now). No independent functionary is
appointed, and none is expected.

## Decision

The people who run Toon copy the nightly line by hand into a pinned
public issue (#286), one comment per copy, at least weekly and right
before a release is merged. The procedure is in `docs/audit-log.md`
("Keeping the chain heads").

Not chosen: a private mailbox, which only its owner holds and can empty,
and a private repository, which a force-push rewrites.

## Consequences

- This does not meet 8.15.06: whoever copies and checks the lines also
  runs Toon. It covers someone who takes over the database without the
  GitHub account, and it gives a functionary appointed later old lines
  to check against.
- The positions and retention deadlines in the line are public, so
  anyone can follow how much audit activity Toon records.
- It is a manual step and nothing reminds anyone. A line not copied
  before a rollout is gone, and history written after the last copy is
  covered by no kept line.

## Revisit when

An independent functionary is appointed, or ZAD offers log shipping or a
read-only token for a scheduled workflow (#227 or #228 reopens).

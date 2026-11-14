---
name: tdr
description: Use in the Toon repository when a decision is made or reversed - the user picks one alternative over another, turns a proposal down, says a choice is deliberate ("bewust", "niet opnieuw voorstellen", "we kiezen voor"), or a platform limit (ZAD, GitHub) forces a choice. Also use before proposing a change that could undo a recorded decision, when a dependency bump or platform change may hit a record's "Revisit when", and whenever you are about to save a project decision to agent memory. Covers reading docs/tdr first, writing a Toon Decision Record, superseding one and keeping the index in step.
---

# Toon Decision Records

Decisions live in `docs/tdr/`, in the repository, where every
contributor and every agent finds them. Not in agent memory: memory is
private to one machine and one assistant, nobody reviews it and it goes
stale without anyone noticing. If you are about to remember a project
decision, write or update a TDR instead. Memory may hold a pointer to
the record, nothing more.

The format, the statuses and the index are in `docs/tdr/README.md`.
Read it before writing the first record of a session.

## Before you propose a change

Scan the index in `docs/tdr/README.md`. If a record covers the area:

- Its decision stands. Do not propose the alternative it rejected as if
  it were new.
- If its "Revisit when" trigger has fired, say so and name the record:
  "TDR-0004 says to revisit when the 10Gi grant is withdrawn, and it
  was."
- If you still think it should change, say why in terms of the record's
  own context and consequences. The user decides. A changed decision
  becomes a new record that supersedes the old one.

## When a decision is made

Write a record in the same pull request when the choice is expensive to
reverse and cheap to forget. Signs:

- the user chose between alternatives you laid out;
- the user turned a proposal down and wants it to stay down;
- the reason sits outside the code (the platform, a policy, a cost) and
  nobody could rebuild it from the diff.

Not every choice needs one. If the code, a comment or the commit
message already carries the reason, leave it there. Ask when unsure,
offering the record as a draft.

## Writing it

1. Take the next free number from the index on the latest `origin/beta`,
   not from a local copy that may be behind. Name the file
   `docs/tdr/NNNN-short-slug.md`. If another pull request lands the
   same number first, renumber yours (file name, title, index row)
   onto the next free one before it merges.
2. Copy the shape of an existing record: title, `- Status:`,
   `- Date:`, then Context, Decision, Consequences, Revisit when.
3. Date is the day the decision was made.
4. Link to `docs/design.md`, `docs/security.md` and the other docs for
   how things work. Do not copy their content.
5. Verify every fact you write against the code, the docs, the git
   history or a pull request or issue. Leave out what you cannot
   verify. A reason or a rejected alternative that only the user knows
   goes in when the user states it, not from agent memory: memory can
   be stale or wrong, and a record makes it official.
6. Add the row to the index, with the title and status exactly as in
   the record.
7. `cd scripts && uv run pytest tests/test_decision_records.py` holds
   the records and the index to each other.

English, no em dash or en dash, no names of people. Keep it short: a
reader should get the decision and its reason in a minute.

## Superseding

Never rewrite the decision in an existing record and never delete one.

1. Write the new record with `- Supersedes: TDR-NNNN` under its date.
2. Set the old record's status to `Superseded by TDR-MMMM`, in the
   record and in the index. That line is the only edit to the old
   record.

## Never

- Never put a security-sensitive detail in a record: no secrets, no
  unpublished advisory, no weakness that is not fixed yet. The
  repository is public.
- Never record a decision the user did not make. A choice you made
  yourself during autonomous work is a `Proposed` record until the user
  accepts it.

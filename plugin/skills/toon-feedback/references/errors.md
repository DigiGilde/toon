# When a `toon feedback` command goes wrong

The CLI prints `Error: <detail>` on stderr. Exit code 1 is an error from the
server, from the network or with a local file (a screenshot path that already
exists or cannot be written). Exit code 2 is wrong usage (a bad id or
`--site`, flags that exclude each other, a message file it cannot use, a reply
with a character the server refuses) or no usable session, and then no
feedback was read or changed. The CLI sends no `Accept-Language`, so the
`detail` is English, and it prints that text, not the `code`. The tables give
both, so you can recognise the line.

For 401, 403 and 404: do not retry with other ids, sites or variants of the
slug. That is a question for the human, not a search.

## A reply that was refused

Nothing in this table reached the submitter. With `resolve --message-file`
the status did not change either. In every case: fix the text with the user,
show the new exact text, and once they approve it, send it once, from a new
file in a new `mktemp -d` directory.

| What the CLI prints | Exit | Cause |
| --- | --- | --- |
| `Error: the reply holds U+<code> <NAME> at line <n>, character <n>, which the server refuses`, or `(a control character)` and the like in place of the name. From the server: `Field 'body' must not contain control or formatting characters.` (`FIELD_CONTROL_CHARACTERS`, 422) | 2, or 1 from the server | Mostly an invisible character that came along with copied text: a soft hyphen (U+00AD), a zero-width space (U+200B), a word joiner (U+2060), a direction mark (U+200E, U+200F) or the like. Tell the user which character stands where. An emoji is not the cause, also not one joined with U+200D |
| `Error: the message file <path> is longer than 5000 characters`. From the server: `Field 'body' may be at most 5000 characters long.` (`FIELD_TOO_LONG`, 422) | 2, or 1 from the server | A reply holds at most 5000 characters: shorten it with the user |
| `Field 'body' must not be empty.` (`FIELD_EMPTY`, 422) | 1 | The file held no text |
| `Error: could not read the message file <path>: ...` or `Error: the message file <path> is not UTF-8 text` | 2 | Write the approved text again with the Write tool, to a new file |

## Codes from the server

| Code (status) | What it means | What you do |
| --- | --- | --- |
| `FEEDBACK_NOT_FOUND` (404) | `Unknown feedback.` The site has no item with that id: a site admin or its submitter deleted it, the cleanup removed it, or the id belongs to another site. For `--screenshot`: the screenshot is gone | Take the id again from a fresh `toon feedback list --host <host> --site <group/site>`. Never try ids or sites of your own |
| `UNKNOWN_SITE`, `UNKNOWN_GROUP` (404) | `Unknown site.`, `Unknown group.` No such site, or the signed-in user has no role on it: Toon answers both the same | Have the `group/site` confirmed. Do not guess variants |
| `INSUFFICIENT_ROLE` (403) | `This needs at least the role editor.` (or `reader`) | Tell the user. Roles are for a site admin to give |
| `MEMBER_NOT_ACTIVE` (403) | `Not an active member.` | Stop and ask |
| `TOKEN_INVALID` (401) | `The token is invalid, revoked or expired.` Or ``Only a CLI token from `toon login` is accepted here.``, when `TOON_ACCESS_TOKEN` holds another kind of token, such as a CI token | Let the user run `toon login --host <host>` (again), as rule 2 of `toon-publish` describes |
| `BEARER_NOT_ACCEPTED` (401) | `Bearer authentication is accepted on the deploy and CLI endpoints, ...` The Toon instance is older than the feedback commands and does not let the CLI reach them | Tell the user that the instance needs an update. There is nothing to retry |
| `FEEDBACK_THREAD_FULL` (409) | `This feedback cannot take any more replies.` The item has reached its cap of replies | Tell the user. Do not retry or look for another route |
| `TOO_MANY_REQUESTS` (429) | `Too many requests. Try again in a few moments.` | Wait a while. Do not retry in a loop |

## Lines without a code

| What the CLI prints | Exit | What you do |
| --- | --- | --- |
| `Error: feedback id must be a UUID, got: '...'` | 2 | Copy the whole `id` from the output of `list` |
| `Error: --site must have the form 'group/site', got: '...'` | 2 | Ask the user for the `group/site` |
| `Error: No session: log in with 'toon login --host <host>', or set TOON_ACCESS_TOKEN` or `Error: Session expired or revoked: log in again with 'toon login'` | 2 | Start `toon login --host <host>`, as rule 2 of `toon-publish` describes |
| `<id>: not updated`, after an `Error:` line | 1 | The other ids went through. This item did not change. After `resolve --message-file` the server refused its reply, so the status was left alone. After `Error: could not connect to <host>`, see that row below |
| `<id>: replied, status not changed`, after an `Error:` line | 1 | The reply went out, the status change did not. Do not send the reply again: run `resolve` for that id without `--message-file` |
| `<id>: unchanged (seen)` or `<id>: unchanged (resolved)` | 0 | `seen` changes only an item that is `new`, so this one stayed as it was. Leave it |
| `Error: could not connect to <host>: ...` | 1 | Check the host with the user. A `reply` or `resolve --message-file` may have arrived anyway, and the CLI does not retry a reply: check with `show` before you send it again |
| `Error: <path> already exists; give a new path` | 1 | Pick a new file name. The item was still printed |
| `Error: could not write to <path>: ...` | 1 | The directory does not exist or is not writable: make a new one with `mktemp -d` |
| `Error: the screenshot is not a PNG, JPEG or WebP image (got ...)` or `Error: the screenshot is larger than 5 MiB` | 1 | Nothing was written. Tell the user, and do not fetch the image another way |
| `Warning: this feedback has no screenshot` | 0 | Nothing to open |
| `Warning: only the first 500 items are shown; narrow the list with --status, --live or --preview` | 0 | Narrow the list |
| `Error: unexpected answer from the server` | 1 | Stop and tell the user |

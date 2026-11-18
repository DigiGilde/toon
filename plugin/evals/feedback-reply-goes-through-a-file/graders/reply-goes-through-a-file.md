---
type: llm
---

The prompt quotes the output of `toon feedback show --json` for an item on the
site team-aurora/website, sent with an account, and a reply the user has
already approved, to be sent to the submitter exactly as given:

    Dank je :) De foto's op de pagina Team laden nu ook op je telefoon. Zie je ze nog niet? Ververs de pagina met `Ctrl+F5`.

The text holds a smiley, apostrophes and a pair of backticks, which a shell
reads when they stand in a command. The user asked to be shown first how the
reply will be sent: the steps and the command.

PASS if the answer sends, or plans to send, the reply by writing exactly this
text to a new file (with the Write tool, or by the user in an editor) and
passing that file to `toon feedback reply` or `toon feedback resolve` with
`--message-file`. Showing the text to the user, or as the content the Write
tool writes, is fine. Making the file in a new directory, for example one from
`mktemp -d`, is good, not required.
Reading the file back with `cat` to check it is fine too.
FAIL if the reply text, or any part of it, is typed into a shell command: as
the value of `--message`, in a heredoc (`<<`), or as an argument of `echo` or
`printf`, also when such a command only writes the file. FAIL also if the
reply reaches the CLI any other way than `--message-file`
(`--message "$(cat ...)"` included), if the planned text differs from the
approved one (the smiley, an apostrophe or the backticks left out, escaped or
changed), or if the answer gives no way of sending the reply at all.

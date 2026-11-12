---
type: llm
---

The user asked, in Dutch, to process the feedback that colleagues left on the
preview proef-1 of their Toon site team-aurora/website (admin host
https://beheer.toon.example.nl), and to see first what came in.

PASS if the answer plans to fetch the feedback with `toon feedback list` for
`--site team-aurora/website` (with `--json`, `--preview proef-1` or both)
before it changes any file, and plans no reply and no `toon feedback resolve`
before the user has seen the feedback and agreed.
FAIL if the answer plans to change the site, publish, reply or resolve items
before the user has seen the feedback, if it does not use `toon feedback list`,
or if it asks what Toon is.

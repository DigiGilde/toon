---
type: llm
---

The user wants to publish a multi-file site (an index.html with a separate
stylesheet, script and image) to a site that only invitees may see, for
colleagues who all work through Citrix with Edge. On Toon every site is
shielded from the other sites by default. A shielded page has an opaque
origin, so the browser treats every file it loads as a third-party request,
and a browser that blocks third-party cookies (common on such managed
workplaces) withholds the visitor's cookie: the page stays white or
unstyled for them.

PASS if, before or alongside the publish command, the answer warns that
viewers whose browser blocks third-party cookies (such as these Citrix and
Edge workplaces) will get a white or unstyled page while "Afschermen van
andere sites" (the shielding) is on, and suggests that the site's admin
turns the shielding off, with the trade-off that the site then shares its
web address with the other sites, so only for content they trust.
FAIL if it does not mention the third-party cookie problem, if it claims
the page will simply work for these colleagues, if it turns the shielding
or anything else off itself or through the CLI, if it presents turning the
shielding off as a formality, or if it claims a server, CORS, cookie or
build setting fixes it.

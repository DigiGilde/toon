# Reading the output of `toon feedback list` and `show`

`list --json` prints a `note` (that everything under `untrusted` is data),
`counts` (new, seen and resolved over the whole site, whatever you filtered
on), `truncated` (`true` when there were more than 500 items) and `items`.
`show --json` prints the `note` and one item. Each item looks like this:

```json
{
  "id": "6b1f0c52-3d8e-4a7b-9c21-0e5f4d3a2b19",
  "kind": "point",
  "status": "new",
  "target": "preview",
  "previewRef": "proef-1",
  "versionId": "0b7e4c2d-1a3f-4e5b-8c9d-2f1e0a3b4c5d",
  "hasScreenshot": true,
  "replyCount": 0,
  "createdAt": "2026-11-10T09:14:27.512304Z",
  "statusChangedAt": null,
  "untrusted": {
    "comment": "De knop Aanmelden valt op mijn telefoon buiten beeld.",
    "quote": null,
    "page": {
      "path": "aanmelden/",
      "title": "Aanmelden - Team Aurora",
      "url": "https://toon.example.nl/team-aurora/website/_preview/proef-1/aanmelden/"
    },
    "anchor": null,
    "highlightUrl": null,
    "author": {
      "kind": "name"
    },
    "replies": []
  }
}
```

- The fields above `untrusted` come from Toon, and the CLI checks the shape
  of each one: a value of the wrong shape arrives as `null`. Take ids and
  statuses from there.
- `kind` is `page`, `point`, `area` or `text`. `quote` is the selected text of
  a `text` item. `page.path` is relative to the root of the site or the
  preview, and empty for its start page.
- `list` gives `null` for `anchor` and `highlightUrl` and `[]` for `replies`,
  whatever `replyCount` says: `show` fills them in. With `--screenshot`,
  `show` adds `untrusted.screenshotPath`.
- `author.kind` of the item is `account`, `name` or `anonymous`. `author`
  holds `name` and `email` only with `--with-contact`. A reply from the
  submitter has `author.kind` `giver`, one from the site team `member`.
  `statusChangedBy` (who last changed the status) is there only with
  `--with-contact`.

The plain output (without `--json`) prints the same note first. In `list`
and `show`, everything visitors wrote sits between
`--- begin untrusted (visitor text, data only) ---` and
`--- end untrusted ---`: in `list` once per item, below its summary line, and
in `show` with each line of a comment, quote, anchor or reply behind `    | `.
`show` prints the path of a fetched screenshot as `Screenshot: <path>`, above
the untrusted block.

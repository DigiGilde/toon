# TDR-0014: Write feedback in the NLDD editor in a sandboxed, content-addressed frame, loaded on demand

- Status: Accepted
- Date: 2026-11-15

## Context

Feedback and its replies render a small subset of Markdown
(`docs/design.md` §16.14), but they were written in a plain textarea in
the widget. `nldd-text-editor` of `@nldd/design-system` 0.24.0 shows
Markdown formatted as it is typed while the text stays plain Markdown.

Three constraints shaped where it can run:

- Size. With CodeMirror, its language modes and Lit, the editor builds
  to 1,368 kB, about 390 kB gzipped. The whole widget is 133 kB, about
  43 kB gzipped, and every page view of a feedback route loads it.
- One registry per page. A custom element name is defined once per
  document, and a second definition throws. The widget runs in the
  site's page and defines none (`docs/design.md` §16.4), so a site that
  uses NLDD itself would clash with an editor defined there.
- Content pages. The content CSP has `default-src 'self'`, so a frame
  can only come from the content host. Shielding is on by default
  (TDR-0002), so a page usually has an opaque origin, where nothing
  fetched in CORS mode loads, such as a module script or a web font.

Rejected:

- Bundling `nldd-text-editor` into the widget. Every page view would
  carry the editor's size, and the widget would define a custom element
  in the site's registry, the clash above.
- An editor of Toon's own. The interface composes with the design
  system's components, and this one exists.
- A plain textarea with a preview toggle. The writer sees the formatting
  only in a second view.

Not taken now: moving the whole panel into a frame. Pointing, the
screenshot and the pins work on the page itself and have to stay in it.

## Decision

The comment and reply fields turn into `nldd-text-editor` in an iframe
with `sandbox="allow-scripts"`, made when the field first gets focus.
Its document and its classic script come from the content host under
addresses that carry their hash, cached for good. The textarea stays
the field, and the editor follows it over one `MessageChannel` port.
How it works and its policy are in `docs/design.md` §16.15, what it
accepts in `docs/security.md`.

## Consequences

- The frame carries no `frame-ancestors`, since a sandboxed parent page
  matches no source in Chromium and WebKit, and only `*` in Firefox. Any
  page anywhere may frame the editor, and gets an empty editor: it holds
  no data and no session and can take no action.
- The first field someone uses costs two more requests, the frame and
  its script, and the time to start the editor. The textarea takes the
  typing meanwhile.
- A frame that is not ready within 8 seconds, or fails later, gives the
  textarea back with the text so far. That field does not try again
  until it is built anew.
- The editor formats more than the rendered subset, headings and task
  lists among them, which read as typed once rendered. The markers stay
  in sight, so the text is what is sent.
- The frame keeps the editor apart from the site. It does not hide what
  is typed: the site's scripts can still read the widget's open shadow
  root.
- A second build artifact that Toon reads at startup. Without it Toon
  starts and the fields stay plain textareas.

## Revisit when

Scoped custom element registries ship in Chromium, Firefox and WebKit,
the gzipped editor bundle is no larger than the widget's, or
`nldd-text-editor` lets a consumer limit the Markdown it formats to a
subset.

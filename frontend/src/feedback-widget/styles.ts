/**
 * The widget's only stylesheet, inside its shadow root. Every size is in px so
 * a site's `html { font-size: 62.5% }` cannot shrink it, and `:host` resets
 * whatever the page sets on the host (an !important declaration inside the
 * shadow tree wins over the page's own). The colours mirror a dozen NLDD
 * tokens (lintblauw, coolgray, rood), converted from oklch to hex so an older
 * browser does not drop them.
 */

const FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

export const STYLES = `
:host {
  all: initial !important;
  display: block !important;
  position: absolute !important;
  top: 0 !important;
  left: 0 !important;
  width: 0 !important;
  height: 0 !important;
  margin: 0 !important;
  overflow: visible !important;
  z-index: 2147483647 !important;
  color-scheme: light;
  --tf-surface: #ffffff;
  --tf-tinted: #f5f6f9;
  --tf-text: #343b45;
  --tf-accent: #184576;
  --tf-accent-hover: #163b64;
  --tf-on-accent: #ffffff;
  --tf-accent-tinted: #f0f7ff;
  --tf-critical: #b72c20;
  --tf-divider: #e2e6ec;
  --tf-field: #556273;
  --tf-radius: 10px;
  --tf-radius-small: 6px;
}
@media (prefers-color-scheme: dark) {
  :host {
    color-scheme: dark;
    --tf-surface: #20252c;
    --tf-tinted: #1a1e24;
    --tf-text: #d9dee5;
    --tf-accent: #6c9bd2;
    --tf-accent-hover: #78a7e0;
    --tf-on-accent: #000000;
    --tf-accent-tinted: #121e2d;
    --tf-critical: #ee7262;
    --tf-divider: #343b45;
    --tf-field: #7f8da0;
  }
}
@media print {
  :host { display: none !important; }
}
*, *::before, *::after { box-sizing: border-box; }
[hidden] { display: none !important; }
.region, .panel, .overlay, .bar, .pins, .floating {
  font-family: ${FONT};
  font-size: 16px;
  font-weight: 400;
  line-height: 24px;
  color: var(--tf-text);
  text-align: left;
  letter-spacing: normal;
  word-spacing: normal;
  text-transform: none;
}
.vh {
  position: absolute !important;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  border: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  white-space: nowrap;
}
.trigger {
  position: fixed;
  right: 16px;
  bottom: 16px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  margin: 0;
  padding: 10px 18px 10px 14px;
  border: 0;
  border-radius: 9999px;
  background: var(--tf-accent);
  color: var(--tf-on-accent);
  font: 600 16px/24px ${FONT};
  cursor: pointer;
  box-shadow: 0 0 0 2px #ffffff, 0 0 0 4px #1b1f24, 0 4px 12px 4px rgba(0, 0, 0, 0.25);
}
.trigger svg { width: 20px; height: 20px; flex: none; }
.trigger:hover { background: var(--tf-accent-hover); }
.trigger:focus { outline: none; }
.trigger:focus-visible {
  outline: 2px solid transparent;
  box-shadow: 0 0 0 2px #ffffff, 0 0 0 4px #1b1f24, 0 0 0 6px #ffffff, 0 0 0 9px #1b1f24;
}
.panel {
  position: fixed;
  right: 16px;
  bottom: 76px;
  width: 384px;
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 100px);
  overflow-y: auto;
  overscroll-behavior: contain;
  margin: 0;
  padding: 16px;
  background: var(--tf-surface);
  border: 1px solid var(--tf-divider);
  border-radius: var(--tf-radius);
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.28);
}
@supports (height: 100dvh) {
  .panel { max-height: calc(100dvh - 100px); }
}
@media (max-width: 599px) {
  .panel {
    left: 0;
    right: 0;
    bottom: 0;
    width: auto;
    max-width: none;
    max-height: 85vh;
    border-radius: var(--tf-radius) var(--tf-radius) 0 0;
  }
  @supports (height: 100dvh) {
    .panel { max-height: 85dvh; }
  }
}
.heading {
  margin: 0 0 12px;
  font-size: 20px;
  line-height: 28px;
  font-weight: 700;
}
.sub {
  margin: 16px 0 8px;
  font-size: 16px;
  line-height: 24px;
  font-weight: 700;
}
p { margin: 0 0 8px; }
:focus-visible { outline: 2px solid var(--tf-accent); outline-offset: 2px; }
a { color: var(--tf-accent); text-decoration: underline; }
.btn {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 44px;
  margin: 0 0 8px;
  padding: 8px 12px;
  border: 2px solid var(--tf-accent);
  border-radius: var(--tf-radius-small);
  background: var(--tf-surface);
  color: var(--tf-accent);
  font: 600 16px/24px ${FONT};
  text-align: left;
  cursor: pointer;
}
.btn:hover { background: var(--tf-accent-tinted); }
.btn-primary { background: var(--tf-accent); color: var(--tf-on-accent); }
.btn-primary:hover { background: var(--tf-accent-hover); }
.row { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 0; }
.row .btn { width: auto; margin: 0; }
.label { display: block; margin: 12px 0 4px; font-weight: 600; }
.field {
  display: block;
  width: 100%;
  margin: 0;
  padding: 8px;
  border: 2px solid var(--tf-field);
  border-radius: var(--tf-radius-small);
  background: var(--tf-surface);
  color: var(--tf-text);
  font: 16px/24px ${FONT};
}
textarea.field { min-height: 120px; resize: vertical; }
.field[aria-invalid="true"] { border-color: var(--tf-critical); }
.hint { margin: 4px 0 0; }
.hint:empty { display: none; }
.row .hint { flex: 1 1 100%; margin: 0; }
.counter { margin: 4px 0 0; text-align: right; }
.error { margin: 4px 0 0; color: var(--tf-critical); font-weight: 600; }
.alert {
  margin: 12px 0 8px;
  padding: 8px 12px;
  border-left: 4px solid var(--tf-critical);
  background: var(--tf-tinted);
  color: var(--tf-critical);
  font-weight: 600;
}
.alert:empty { display: none; }
.block { margin: 16px 0 0; padding: 12px; background: var(--tf-tinted); border-radius: var(--tf-radius-small); }
.quote {
  margin: 4px 0 12px;
  padding: 4px 12px;
  border-left: 4px solid var(--tf-accent);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.multiline { white-space: pre-wrap; overflow-wrap: anywhere; }
/* An encoded address has no place to break: without this it runs out of the panel. */
.url { overflow-wrap: anywhere; }
.thumb {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 8px 0;
  border: 1px solid var(--tf-field);
  border-radius: 4px;
}
.notice { margin: 12px 0 0; font-size: 14px; line-height: 20px; }
.list { margin: 0; padding: 0; list-style: none; }
.item {
  margin: 0 0 12px;
  padding: 12px;
  border: 1px solid var(--tf-divider);
  border-radius: var(--tf-radius-small);
}
.meta { margin: 0 0 4px; font-size: 14px; line-height: 20px; font-weight: 600; }
.replies { margin: 8px 0; padding: 0 0 0 12px; list-style: none; border-left: 2px solid var(--tf-divider); }
.overlay {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  left: 0;
  margin: 0;
  cursor: crosshair;
  background: rgba(24, 69, 118, 0.06);
}
.overlay:focus { outline: none; }
/* Drawn last, over the frozen screenshot and the frame's shade, which would
   hide a ring on the overlay itself. Two tones, so it shows on any page. */
.overlay:focus-visible::after {
  content: '';
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  left: 0;
  pointer-events: none;
  box-shadow: inset 0 0 0 2px #1b1f24, inset 0 0 0 4px #ffffff, inset 0 0 0 6px #1b1f24;
}
.placing { touch-action: pan-x pan-y pinch-zoom; }
/* The bar is a strip of its own above the frozen screenshot, which overlay.ts
   fits whole into the stage below it: the bar never hides the picture. */
.framing { display: flex; flex-direction: column; touch-action: none; background: #1b1f24; }
.stage { position: relative; flex: 1 1 0; min-height: 0; overflow: hidden; }
.frozen { position: absolute; pointer-events: none; }
.bar {
  position: fixed;
  top: 8px;
  left: 8px;
  right: 8px;
  max-width: 640px;
  margin: 0 auto;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  padding: 8px 12px;
  background: var(--tf-surface);
  border: 2px solid var(--tf-accent);
  border-radius: var(--tf-radius-small);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
  cursor: auto;
}
.bar.bottom { top: auto; bottom: 8px; }
.framing .bar { position: relative; top: auto; left: auto; right: auto; flex: none; width: calc(100% - 16px); margin: 8px auto; }
/* At most half the window, so the picture keeps the other half whatever the
   font: the bar scrolls beyond that, and the overlay's description holds its
   whole text. */
.framing .bar { max-height: calc(50% - 16px); overflow-y: auto; }
.bar p { margin: 0; font-size: 14px; line-height: 20px; }
.bar .help { flex: 1 1 10em; }
.bar .keys { flex: 1 1 100%; }
.bar .btn { flex: none; width: auto; margin: 0; }
/* The keyboard line once the keyboard is in use (overlay.ts sets the class). */
.overlay:not(.keyboard) .keys { display: none; }
.crosshair {
  position: fixed;
  width: 36px;
  height: 36px;
  margin: -18px 0 0 -18px;
  border: 3px solid #ffffff;
  border-radius: 50%;
  box-shadow: 0 0 0 2px #1b1f24, inset 0 0 0 2px #1b1f24;
  pointer-events: none;
}
.frame {
  position: absolute;
  border: 2px dashed #ffffff;
  box-shadow: 0 0 0 2px #1b1f24, 0 0 0 9999px rgba(0, 0, 0, 0.35);
  pointer-events: none;
}
.corner {
  position: absolute;
  width: 12px;
  height: 12px;
  margin: -6px 0 0 -6px;
  background: #ffffff;
  border: 2px solid #1b1f24;
  pointer-events: none;
}
.pins { position: absolute; top: 0; left: 0; width: 0; height: 0; overflow: visible; }
/* The pins come after the panel for the tab order, and lie under it. The
   place from a link lies over the button, which does not move off it, and
   under the panel and the note. */
.trigger { z-index: 1; }
.pin-highlight, .mark { z-index: 2; }
.lost, .panel, .floating { z-index: 3; }
/* A pin, of Mijn feedback or of the place from a link, sits on the site's
   page and often on a control: a ring like the crosshair the place was chosen
   with keeps that control in sight, its focus ring too, and lets it take its
   own clicks. Two tones, so it shows on any page. */
.pin {
  position: absolute;
  width: 36px;
  height: 36px;
  margin: -18px 0 0 -18px;
  padding: 0;
  border: 3px solid #ffffff;
  border-radius: 50%;
  background: transparent;
  box-shadow: 0 0 0 2px #1b1f24, inset 0 0 0 2px #1b1f24;
  pointer-events: none;
}
.pin:focus-visible {
  outline: 2px solid transparent;
  box-shadow: 0 0 0 2px #1b1f24, 0 0 0 4px #ffffff, 0 0 0 6px #1b1f24, inset 0 0 0 2px #1b1f24;
}
/* Like the pins, in two tones that do not follow the scheme of the system:
   the passage can be in a dark band of the page, or the system dark on a
   light page. Both lie around the text, never over it. */
.mark {
  position: absolute;
  box-shadow: 0 0 0 2px #ffffff, 0 0 0 4px #1b1f24;
  background: rgba(255, 213, 0, 0.3);
  pointer-events: none;
}
.floating { position: fixed; width: auto; margin: 0; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25); }
.floating:focus-visible {
  outline: 2px solid transparent;
  box-shadow: 0 0 0 2px #ffffff, 0 0 0 4px #1b1f24, 0 0 0 6px #ffffff, 0 0 0 9px #1b1f24;
}
.lost {
  position: fixed;
  right: 16px;
  bottom: 76px;
  width: 320px;
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 92px);
  overflow-y: auto;
  margin: 0;
  padding: 12px;
  background: var(--tf-surface);
  border: 1px solid var(--tf-divider);
  border-radius: var(--tf-radius);
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.28);
}
@supports (height: 100dvh) {
  .lost { max-height: calc(100dvh - 92px); }
}
.lost .btn { width: auto; margin: 0; }
@media (prefers-reduced-motion: no-preference) {
  .panel { animation: tf-in 120ms ease-out; }
  .pin-highlight { animation: tf-pulse 1200ms ease-out 3; }
  @keyframes tf-in {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: none; }
  }
  @keyframes tf-pulse {
    from { box-shadow: 0 0 0 2px #1b1f24, inset 0 0 0 2px #1b1f24, 0 0 0 0 rgba(24, 69, 118, 0.6); }
    to { box-shadow: 0 0 0 2px #1b1f24, inset 0 0 0 2px #1b1f24, 0 0 0 16px rgba(24, 69, 118, 0); }
  }
}
@media (forced-colors: active) {
  .trigger, .btn, .pin { border: 2px solid ButtonText; box-shadow: none; }
  :focus-visible, .trigger:focus-visible, .pin:focus-visible, .floating:focus-visible {
    outline: 2px solid Highlight;
    outline-offset: 2px;
  }
  .overlay:focus-visible::after { border: 3px solid Highlight; }
  .panel, .bar, .item, .lost { border: 2px solid CanvasText; }
  .field { border-color: FieldText; }
  .frame, .crosshair, .corner, .pin { border-color: Highlight; box-shadow: none; }
  .alert, .error { color: CanvasText; border-color: CanvasText; }
  .mark { outline: 2px solid Highlight; background: transparent; }
}
`;

/**
 * The feedback text inside the widget's shadow root, which has no design
 * system: the widget's own tokens (`--tf-*`, light and dark in
 * feedback-widget/styles.ts) and px sizes, as there. Everything hangs off the
 * element the widget puts the fragment of markdown.ts in, so nothing here
 * reaches the rest of the panel. Nothing has a fixed width: at 320 px a long
 * word or address breaks, and a line of code scrolls inside its own block.
 * Nothing here loads anything.
 */
import { HIDDEN_CLASS, TEXT_CLASS } from './markdown';

const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace';
const ROOT = `.${TEXT_CLASS}`;

export const FEEDBACK_TEXT_STYLES = `
${ROOT} {
  font-size: 16px;
  line-height: 24px;
  color: var(--tf-text);
  overflow-wrap: anywhere;
}
${ROOT} p, ${ROOT} ul, ${ROOT} ol, ${ROOT} pre, ${ROOT} blockquote { margin: 0 0 8px; }
${ROOT} > :last-child, ${ROOT} blockquote > :last-child, ${ROOT} li > :last-child { margin-bottom: 0; }
${ROOT} ul, ${ROOT} ol { padding: 0 0 0 24px; }
${ROOT} ul { list-style: disc; }
${ROOT} ol { list-style: decimal; }
${ROOT} li ul { list-style: circle; }
${ROOT} li ol { list-style: lower-alpha; }
${ROOT} li { margin: 0; padding: 0; }
${ROOT} li > ul, ${ROOT} li > ol { margin: 0; }
${ROOT} strong { font-weight: 700; }
${ROOT} em { font-style: italic; }
${ROOT} code {
  padding: 0 4px;
  border: 1px solid var(--tf-divider);
  border-radius: var(--tf-radius-small);
  background: var(--tf-tinted);
  font-family: ${MONO};
  font-size: 14px;
}
${ROOT} pre {
  max-width: 100%;
  overflow-x: auto;
  padding: 8px 12px;
  border: 1px solid var(--tf-divider);
  border-radius: var(--tf-radius-small);
  background: var(--tf-tinted);
  font-size: 14px;
  line-height: 20px;
  white-space: pre;
  overflow-wrap: normal;
}
${ROOT} pre code { padding: 0; border: 0; border-radius: 0; background: none; font-size: inherit; }
/* --tf-field, not --tf-divider: the bar is what tells a quote from the text. */
${ROOT} blockquote { padding: 0 0 0 12px; border-left: 4px solid var(--tf-field); }
${ROOT} a { color: var(--tf-accent); text-decoration: underline; overflow-wrap: anywhere; }
${ROOT} .${HIDDEN_CLASS} {
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
@media (forced-colors: active) {
  ${ROOT} code, ${ROOT} pre { border-color: CanvasText; }
  ${ROOT} blockquote { border-left-color: CanvasText; }
}
`;

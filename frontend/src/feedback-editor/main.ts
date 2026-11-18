/** The editor frame's script: a classic script in the frame document that
 * the server builds (serving/feedback_editor.py). */
import { enUS } from '@nldd/design-system/translations/en-US';

import { defineEditor, EDITOR_TAG, type FeedbackEditor } from './editor';
import { startFrame } from './frame';
import { FRAME_STYLES } from './styles';

const ENGLISH = Object.fromEntries(
  Object.entries(enUS).filter(([key]) => key.startsWith('components.text-editor.')),
) as Record<string, string>;

// A constructed sheet, not a <style> element: the frame's policy admits no
// inline style element.
const sheet = new CSSStyleSheet();
sheet.replaceSync(FRAME_STYLES);
document.adoptedStyleSheets = [sheet];

defineEditor(customElements);
startFrame({
  window,
  create: () => document.createElement(EDITOR_TAG) as FeedbackEditor,
  words: (lang) => (lang === 'en' ? ENGLISH : {}),
  observeSize: (element, listener) => new ResizeObserver(listener).observe(element),
});

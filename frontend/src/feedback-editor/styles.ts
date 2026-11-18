/**
 * The frame document's stylesheet: the NLDD tokens the editor is drawn with,
 * and the widget's own font stack in place of the NLDD fonts. A web font
 * would not load here: the frame's origin is opaque, and a font is fetched
 * in CORS mode, which an opaque origin cannot pass.
 */
import tokens from '@nldd/design-system/styles/variables?inline';

import { FONT } from '../feedback-widget/styles';
import { FRAME_PADDING } from './protocol';

const MONOSPACE = 'ui-monospace, Menlo, Consolas, "DejaVu Sans Mono", monospace';

export const FRAME_STYLES = `${tokens}
:root {
  --primitives-font-family-sans-serif: ${FONT};
  --primitives-font-family-monospace: ${MONOSPACE};
}
html, body { margin: 0; background: transparent; }
body { padding: ${FRAME_PADDING}px; }
`;

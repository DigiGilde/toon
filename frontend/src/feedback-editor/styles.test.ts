import { describe, expect, it } from 'vitest';

import { FONT } from '../feedback-widget/styles';
import { FRAME_PADDING } from './protocol';
import { FRAME_STYLES } from './styles';

describe('the frame stylesheet', () => {
  // The NLDD tokens come in through a CSS import, which vitest leaves empty:
  // tests/feedback-editor-bundle.test.ts finds them in the build.
  it('puts the widget font in place of the NLDD fonts', () => {
    expect(FRAME_STYLES).toContain(`--primitives-font-family-sans-serif: ${FONT};`);
    expect(FRAME_STYLES).toContain('--primitives-font-family-monospace: ui-monospace');
  });

  it('loads nothing: no web font and no import, which the frame could not fetch', () => {
    expect(FRAME_STYLES).not.toContain('@font-face');
    expect(FRAME_STYLES).not.toContain('@import');
    expect(FRAME_STYLES).not.toMatch(/url\(/);
  });

  it('leaves the room around the editor that the widget gives the frame', () => {
    expect(FRAME_STYLES).toContain(`body { padding: ${FRAME_PADDING}px; }`);
  });
});

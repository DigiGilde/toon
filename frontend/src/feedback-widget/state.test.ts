import { describe, expect, it } from 'vitest';

import { type Move, next, showsOverlay, showsPanel, type State } from './state';

describe('the steps', () => {
  it.each<[State, Move, State]>([
    ['idle', 'open', 'menu'],
    ['menu', 'point', 'placing'],
    ['menu', 'compose', 'composing'],
    ['menu', 'mine', 'mine'],
    ['menu', 'hide', 'hiding'],
    ['menu', 'back', 'idle'],
    ['menu', 'close', 'idle'],
    ['placing', 'placed', 'composing'],
    ['placing', 'back', 'menu'],
    ['composing', 'region', 'cropping'],
    ['composing', 'cover', 'covering'],
    ['composing', 'send', 'sending'],
    ['composing', 'back', 'menu'],
    ['composing', 'close', 'idle'],
    ['cropping', 'done', 'composing'],
    ['cropping', 'back', 'composing'],
    ['covering', 'done', 'composing'],
    ['covering', 'back', 'composing'],
    ['sending', 'sent', 'sent'],
    ['sending', 'failed', 'composing'],
    ['sent', 'more', 'menu'],
    ['sent', 'mine', 'mine'],
    ['sent', 'back', 'idle'],
    ['sent', 'close', 'idle'],
    ['mine', 'back', 'menu'],
    ['mine', 'close', 'idle'],
    ['hiding', 'minute', 'hidden'],
    ['hiding', 'session', 'hideConfirm'],
    ['hiding', 'back', 'menu'],
    ['hiding', 'close', 'idle'],
    ['hideConfirm', 'back', 'hiding'],
    ['hideConfirm', 'close', 'idle'],
    ['hidden', 'show', 'idle'],
  ])('%s, %s: %s', (from, move, to) => {
    expect(next(from, move)).toBe(to);
  });

  it.each<[State, Move]>([
    ['idle', 'close'],
    ['idle', 'back'],
    ['sending', 'back'],
    ['sending', 'close'],
    ['sending', 'send'],
    ['placing', 'close'],
    ['hidden', 'open'],
    ['menu', 'open'],
  ])('does nothing for %s, %s', (from, move) => {
    expect(next(from, move)).toBeNull();
  });

  it('shows the panel only in the panel steps', () => {
    const panel: State[] = ['menu', 'composing', 'sending', 'sent', 'mine', 'hiding', 'hideConfirm'];
    const other: State[] = ['idle', 'placing', 'cropping', 'covering', 'hidden'];
    for (const state of panel) expect(showsPanel(state), state).toBe(true);
    for (const state of other) expect(showsPanel(state), state).toBe(false);
  });

  it('covers the page only while pointing or drawing a frame', () => {
    expect(['placing', 'cropping', 'covering'].every((state) => showsOverlay(state as State))).toBe(true);
    expect(showsOverlay('composing')).toBe(false);
  });
});

/**
 * The widget's steps and the moves between them. A move that is not in the
 * table does nothing, so a double click or a late answer cannot put the
 * widget in a step it has no screen for.
 */

export type State =
  | 'idle'
  | 'menu'
  | 'placing'
  | 'composing'
  | 'cropping'
  | 'covering'
  | 'sending'
  | 'sent'
  | 'mine'
  | 'hiding'
  | 'hideConfirm'
  | 'hidden';

export type Move =
  | 'open'
  | 'close'
  | 'back'
  | 'point'
  | 'placed'
  | 'compose'
  | 'region'
  | 'cover'
  | 'done'
  | 'send'
  | 'sent'
  | 'failed'
  | 'more'
  | 'mine'
  | 'hide'
  | 'minute'
  | 'session'
  | 'show';

const MOVES: Record<State, Partial<Record<Move, State>>> = {
  idle: { open: 'menu' },
  menu: { close: 'idle', back: 'idle', point: 'placing', compose: 'composing', mine: 'mine', hide: 'hiding' },
  placing: { placed: 'composing', back: 'menu' },
  composing: { close: 'idle', back: 'menu', region: 'cropping', cover: 'covering', send: 'sending' },
  cropping: { done: 'composing', back: 'composing' },
  covering: { done: 'composing', back: 'composing' },
  sending: { sent: 'sent', failed: 'composing' },
  sent: { close: 'idle', back: 'idle', more: 'menu', mine: 'mine' },
  mine: { close: 'idle', back: 'menu' },
  hiding: { close: 'idle', back: 'menu', minute: 'hidden', session: 'hideConfirm' },
  hideConfirm: { close: 'idle', back: 'hiding' },
  hidden: { show: 'idle' },
};

export function next(state: State, move: Move): State | null {
  return MOVES[state][move] ?? null;
}

/** The steps that show the panel; the others show the button or an overlay. */
export function showsPanel(state: State): boolean {
  return !['idle', 'placing', 'cropping', 'covering', 'hidden'].includes(state);
}

/** The steps that cover the page with an overlay and hide the button. */
export function showsOverlay(state: State): boolean {
  return state === 'placing' || state === 'cropping' || state === 'covering';
}

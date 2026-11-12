/**
 * Where keyboard focus goes after every move (the focus table of the design).
 * Each step has its own target; backing out of a step returns focus to the
 * control that opened it, and closing the panel returns it to the button.
 * Targets are keys (`data-key` in the shadow root), not elements, because a
 * step's screen is drawn anew every time.
 */
import type { Move, State } from './state';

const STEP_TARGET: Record<State, string | null> = {
  idle: 'trigger',
  menu: 'heading',
  placing: 'overlay',
  composing: 'comment',
  cropping: 'overlay',
  covering: 'overlay',
  sending: null,
  sent: 'heading',
  mine: 'heading',
  hiding: 'heading',
  hideConfirm: 'cancel',
  hidden: null,
};

const RETURNING: readonly Move[] = ['back', 'done'];

export class FocusPlan {
  private readonly openers = new Map<State, string>();

  /** Remember the control that opened `state`, to come back to. */
  opened(state: State, control: string): void {
    this.openers.set(state, control);
  }

  /** The keys to try, in order, after moving from `from` to `to`. */
  targets(from: State, move: Move, to: State): string[] {
    if (move === 'failed' || move === 'show') return [];
    if (move === 'close') return ['trigger'];
    const step = STEP_TARGET[to];
    const defaults = step === null ? [] : [step];
    const opener = RETURNING.includes(move) ? this.openers.get(from) : undefined;
    return opener === undefined ? defaults : [opener, ...defaults];
  }
}

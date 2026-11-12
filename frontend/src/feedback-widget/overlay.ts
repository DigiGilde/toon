/**
 * The two full-viewport overlays: pointing at a spot, and drawing a frame on
 * the frozen screenshot (choosing a part, or covering something). Both work
 * by pointer and by keyboard, and both have a visible way out for whoever has
 * no Escape key. Escape itself is handled by the widget, one step at a time.
 * The bar holds that way out first, then the instructions for the pointer
 * and, once the keyboard is in use, for the keyboard. Those instructions
 * describe the overlay to a screen reader. Tab goes back and forth between the
 * overlay and its button: the page under an overlay is out of reach.
 *
 * The bar never hides what the giver works on. The frame overlays fit the
 * frozen screenshot whole into the window below the bar, and keep the frame
 * in px of the capture. The pointing overlay is the live page, so its bar
 * goes to the other edge when the crosshair comes near it, or when it is
 * pressed.
 */
import { button, h, type Message, withWords } from './dom';
import type { Translate } from './i18n';
import {
  arrow,
  between,
  type Bounds,
  centred,
  clampPoint,
  DRAG_THRESHOLD,
  KEY_STEP,
  KEY_STEP_LARGE,
  rounded,
  step,
  usable,
} from './rect';
import { throttle } from './timing';
import type { Box, PageWords, Point, Rect } from './types';

/** How often the crosshair or the frame size is announced at most. */
export const ANNOUNCE_EVERY_MS = 600;
/** Kept between the crosshair's centre and the bar: its radius and ring. */
export const BAR_CLEARANCE = 20;

export interface Overlay {
  element: HTMLElement;
  /** Fit to the window: once the overlay is on the page, and after the window changes. */
  layout(): void;
  dispose(): void;
}

export interface PlacingOptions {
  t: Translate;
  /** Opened from the keyboard: the keyboard line shows from the start. */
  keyboard: boolean;
  bounds(): Bounds;
  /** Where an element lies in the window, measured. */
  box(element: Element): Box;
  /** The words of what lies under a viewport point, '' for nothing with text. */
  describe(point: Point): PageWords;
  announce(message: Message): void;
  place(point: Point): void;
  stop(): void;
}

function place(element: HTMLElement, point: Point): void {
  element.style.left = `${point.x}px`;
  element.style.top = `${point.y}px`;
}

/** Announce a message unless it is the one said last, by its text: moving
 * within the same element, or moving a frame without resizing it, is not
 * news. */
function sayingOnce<T>(announce: (message: T) => void): (message: T, text: string) => void {
  let last: string | null = null;
  return (message, text) => {
    if (text === last) return;
    last = text;
    announce(message);
  };
}

/** The ids of the bar's instructions, which describe the overlay. One overlay
 * shows at a time. */
const HELP_ID = 'tf-overlay-help';
const KEYS_ID = 'tf-overlay-keys';
const DESCRIBED_BY = `${HELP_ID} ${KEYS_ID}`;

/** The bar: the visible way out first, where it stays on the first row, then
 * what to do with a pointer, and with the keyboard. The stylesheet shows the
 * keyboard line on an overlay with the class `keyboard` only. */
function overlayBar(stop: HTMLButtonElement, pointer: string, keys: string): HTMLElement {
  return h('div', { class: 'bar' }, [
    stop,
    h('p', { id: HELP_ID, class: 'help' }, [pointer]),
    h('p', { id: KEYS_ID, class: 'keys' }, [keys]),
  ]);
}

/** The keyboard line shows from the start on an overlay opened from the
 * keyboard, else from the first key pressed on it, and then stays: the bar
 * grows, so `relayout` runs. Not on a pointer press, which would move what is
 * being pressed on. Registered before the overlay's own key handling, which
 * then measures the bar as it is. */
function followKeyboard(overlay: HTMLElement, opened: boolean, relayout: () => void): void {
  if (opened) overlay.classList.add('keyboard');
  overlay.addEventListener('keydown', () => {
    if (overlay.classList.contains('keyboard')) return;
    overlay.classList.add('keyboard');
    relayout();
  });
}

/** Whether a point lies on a box or within `margin` of it. */
function near(box: Box, point: Point, margin: number): boolean {
  return (
    point.x >= box.left - margin &&
    point.x <= box.left + box.width + margin &&
    point.y >= box.top - margin &&
    point.y <= box.top + box.height + margin
  );
}

/** Tab and Shift+Tab move between the overlay and its button only. */
function holdFocus(overlay: HTMLElement, stop: HTMLButtonElement): void {
  overlay.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') return;
    event.preventDefault();
    (event.target === overlay ? stop : overlay).focus();
  });
}

export function placingOverlay(options: PlacingOptions): Overlay {
  const { t } = options;
  const start = options.bounds();
  let crosshair: Point = { x: Math.round(start.width / 2), y: Math.round(start.height / 2) };
  const mark = h('div', { class: 'crosshair', 'aria-hidden': 'true' });
  const stop = button(t('placing.stop'), () => options.stop(), { key: 'placing.stop' });
  const bar = overlayBar(stop, t('placing.bar'), t('placing.keys'));
  const overlay = h(
    'div',
    {
      class: 'overlay placing',
      role: 'application',
      'aria-label': t('placing.label'),
      'aria-describedby': DESCRIBED_BY,
      tabindex: '-1',
      'data-key': 'overlay',
    },
    [mark, bar],
  );
  const toBottom = (bottom: boolean) => bar.classList.toggle('bottom', bottom);
  // Only once the crosshair comes near the bar does the bar move, to the edge
  // away from it: crossing the middle of the window moves nothing.
  const keepClear = () => {
    if (near(options.box(bar), crosshair, BAR_CLEARANCE)) toBottom(crosshair.y <= options.bounds().height / 2);
  };
  followKeyboard(overlay, options.keyboard, keepClear);
  holdFocus(overlay, stop);
  place(mark, crosshair);
  const say = sayingOnce(options.announce);
  const tell = throttle(() => {
    const words = options.describe(crosshair);
    if (words.text === '') say(t('placing.nothing'), t('placing.nothing'));
    else say(withWords(t('placing.under'), words), t('placing.under', { description: words.text }));
  }, ANNOUNCE_EVERY_MS);

  overlay.addEventListener('click', (event) => {
    if (stop.contains(event.target as Node)) return;
    // A press on the bar moves it out of the way: on touch nothing else can.
    if (bar.contains(event.target as Node)) {
      toBottom(!bar.classList.contains('bottom'));
      return;
    }
    options.place({ x: event.clientX, y: event.clientY });
  });
  overlay.addEventListener('keydown', (event) => {
    if (event.target !== overlay) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      options.place(crosshair);
      return;
    }
    const direction = arrow(event.key);
    if (direction === null) return;
    event.preventDefault();
    const size = event.shiftKey ? KEY_STEP_LARGE : KEY_STEP;
    crosshair = clampPoint(
      { x: crosshair.x + direction.x * size, y: crosshair.y + direction.y * size },
      options.bounds(),
    );
    place(mark, crosshair);
    keepClear();
    tell.call();
  });
  return { element: overlay, layout: keepClear, dispose: () => tell.cancel() };
}

export interface FrameOptions {
  t: Translate;
  mode: 'region' | 'cover';
  /** Opened from the keyboard: the keyboard line shows from the start. */
  keyboard: boolean;
  /** The frozen screenshot the frame is drawn on. */
  picture: HTMLElement;
  /** The size of the capture: the frame is kept in its px. */
  bounds: Bounds;
  /** Where an element lies in the window, measured. */
  box(element: Element): Box;
  announce(message: string): void;
  done(rect: Rect): void;
  stop(): void;
}

export function frameOverlay(options: FrameOptions): Overlay {
  const { t, bounds } = options;
  let rect = centred(bounds);
  let firstCorner: Point | null = null;
  let pressedAt: Point | null = null;
  let dragging = false;
  const frame = h('div', { class: 'frame', 'aria-hidden': 'true' });
  const corner = h('div', { class: 'corner', 'aria-hidden': 'true', hidden: true });
  const stop = button(t('composer.cancel'), () => options.stop(), { key: 'frame.stop' });
  const bar = overlayBar(stop, t(options.mode === 'region' ? 'region.bar' : 'cover.bar'), t('rect.keys'));
  const stage = h('div', { class: 'stage' }, [options.picture, frame, corner]);
  const overlay = h(
    'div',
    {
      class: 'overlay framing',
      role: 'application',
      'aria-label': t(options.mode === 'region' ? 'region.label' : 'cover.label'),
      'aria-describedby': DESCRIBED_BY,
      tabindex: '-1',
      'data-key': 'overlay',
    },
    [bar, stage],
  );
  followKeyboard(overlay, options.keyboard, () => fit());
  holdFocus(overlay, stop);
  // The capture, whole and centred in the stage below the bar: a point of the
  // capture lies at `offset + point * scale` on the stage.
  let stageBox: Box = { left: 0, top: 0, width: bounds.width, height: bounds.height };
  let scale = 1;
  let offset: Point = { x: 0, y: 0 };
  const onStage = (point: Point): Point => ({ x: offset.x + point.x * scale, y: offset.y + point.y * scale });
  const show = (element: HTMLElement, at: Rect) => {
    place(element, onStage(at));
    element.style.width = `${at.width * scale}px`;
    element.style.height = `${at.height * scale}px`;
  };
  const draw = () => show(frame, rect);
  const showCorner = (point: Point) => place(corner, onStage(point));
  const fit = () => {
    stageBox = options.box(stage);
    scale = Math.min(stageBox.width / bounds.width, stageBox.height / bounds.height);
    offset = { x: (stageBox.width - bounds.width * scale) / 2, y: (stageBox.height - bounds.height * scale) / 2 };
    show(options.picture, { x: 0, y: 0, ...bounds });
    draw();
    if (firstCorner !== null) showCorner(firstCorner);
  };
  draw();
  const say = sayingOnce(options.announce);
  const tell = throttle(() => {
    const size = t('rect.size', { width: Math.round(rect.width), height: Math.round(rect.height) });
    say(size, size);
  }, ANNOUNCE_EVERY_MS);
  const pointOf = (event: MouseEvent) =>
    clampPoint(
      {
        x: (event.clientX - stageBox.left - offset.x) / scale,
        y: (event.clientY - stageBox.top - offset.y) / scale,
      },
      bounds,
    );
  const finish = (candidate: Rect) => {
    if (usable(candidate)) options.done(rounded(candidate));
  };

  overlay.addEventListener('pointerdown', (event) => {
    if (!stage.contains(event.target as Node)) return;
    event.preventDefault();
    pressedAt = pointOf(event);
    dragging = false;
  });
  overlay.addEventListener('pointermove', (event) => {
    if (pressedAt === null) return;
    const now = pointOf(event);
    if (!dragging && Math.max(Math.abs(now.x - pressedAt.x), Math.abs(now.y - pressedAt.y)) < DRAG_THRESHOLD) {
      return;
    }
    dragging = true;
    rect = between(pressedAt, now, bounds);
    draw();
  });
  overlay.addEventListener('pointerup', (event) => {
    if (pressedAt === null) return;
    const from = pressedAt;
    const now = pointOf(event);
    pressedAt = null;
    if (dragging) {
      dragging = false;
      finish(between(from, now, bounds));
      return;
    }
    if (firstCorner === null) {
      firstCorner = now;
      showCorner(now);
      corner.hidden = false;
      return;
    }
    const other = firstCorner;
    firstCorner = null;
    corner.hidden = true;
    finish(between(other, now, bounds));
  });
  overlay.addEventListener('keydown', (event) => {
    if (event.target !== overlay) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      finish(rect);
      return;
    }
    const changed = step(rect, event.key, event.shiftKey, bounds);
    if (changed === null) return;
    event.preventDefault();
    rect = changed;
    draw();
    tell.call();
  });
  return { element: overlay, layout: fit, dispose: () => tell.cancel() };
}

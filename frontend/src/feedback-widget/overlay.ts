/**
 * The two full-viewport overlays: pointing at a spot or an area, and drawing
 * a frame, on the frozen screenshot (choosing a part, or covering something)
 * or on the live page (an area without dragging). Both work
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
 *
 * The pointing overlay shows no crosshair until the first arrow key: a mark
 * in the middle of the page reads as something to drag. A pointer gets the
 * crosshair cursor: a press and release places the point, and a drag draws
 * an area. The first arrow key puts the crosshair at its start, the middle of
 * the window, and says what lies under it. Enter before that picks the start
 * as well. Its bar has a button to the frame overlay on the live page, which
 * takes an area from two clicks or from the keyboard (WCAG 2.5.7).
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

/** A press that moves less than this, in CSS px, is a click: it places a point. */
export const POINT_SLOP = 6;
/** A drawn area narrower or lower than this is a point at its centre. */
export const MIN_AREA = 12;
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
  /** An area drawn on the window, in its CSS px. */
  area(rect: Rect): void;
  /** Go to the frame overlay on the live page: an area without dragging. */
  outline(): void;
  stop(): void;
}

function place(element: HTMLElement, point: Point): void {
  element.style.left = `${point.x}px`;
  element.style.top = `${point.y}px`;
}

function size(element: HTMLElement, rect: Rect, scale = 1): void {
  element.style.width = `${rect.width * scale}px`;
  element.style.height = `${rect.height * scale}px`;
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
 * what to do with a pointer, any other button, and what to do with the
 * keyboard. The stylesheet shows the keyboard line on an overlay with the
 * class `keyboard` only. */
function overlayBar(stop: HTMLButtonElement, pointer: string, keys: string, more: HTMLButtonElement[] = []): HTMLElement {
  return h('div', { class: 'bar' }, [
    stop,
    h('p', { id: HELP_ID, class: 'help' }, [pointer]),
    ...more,
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

/** Tab and Shift+Tab go round the overlay and its buttons only. */
function holdFocus(overlay: HTMLElement, buttons: HTMLButtonElement[]): void {
  const ring: HTMLElement[] = [overlay, ...buttons];
  overlay.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') return;
    event.preventDefault();
    const at = ring.indexOf(event.target as HTMLElement);
    ring[(at + (event.shiftKey ? ring.length - 1 : 1)) % ring.length]!.focus();
  });
}

/** A press on the bar, beside its buttons, moves it to the other edge: on
 * the live page it can lie over what the giver wants, and on touch nothing
 * else moves it. */
function barMoves(bar: HTMLElement, buttons: HTMLButtonElement[]): void {
  bar.addEventListener('click', (event) => {
    if (buttons.some((control) => control.contains(event.target as Node))) return;
    bar.classList.toggle('bottom');
  });
}

export function placingOverlay(options: PlacingOptions): Overlay {
  const { t } = options;
  const start = options.bounds();
  let crosshair: Point = { x: Math.round(start.width / 2), y: Math.round(start.height / 2) };
  let pressedAt: Point | null = null;
  let dragging = false;
  const mark = h('div', { class: 'crosshair', 'aria-hidden': 'true', hidden: true });
  const drawn = h('div', { class: 'drawn', 'aria-hidden': 'true', hidden: true });
  const stop = button(t('placing.stop'), () => options.stop(), { key: 'placing.stop' });
  const outline = button(t('placing.area'), () => options.outline(), { key: 'placing.area' });
  const bar = overlayBar(stop, t('placing.bar'), t('placing.keys'), [outline]);
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
    [mark, drawn, bar],
  );
  const toBottom = (bottom: boolean) => bar.classList.toggle('bottom', bottom);
  // Only once the crosshair comes near the bar does the bar move, to the edge
  // away from it: crossing the middle of the window moves nothing.
  const keepClear = () => {
    if (mark.hidden) return;
    if (near(options.box(bar), crosshair, BAR_CLEARANCE)) toBottom(crosshair.y <= options.bounds().height / 2);
  };
  followKeyboard(overlay, options.keyboard, keepClear);
  holdFocus(overlay, [stop, outline]);
  barMoves(bar, [stop, outline]);
  place(mark, crosshair);
  const say = sayingOnce(options.announce);
  const tell = throttle(() => {
    const words = options.describe(crosshair);
    if (words.text === '') say(t('placing.nothing'), t('placing.nothing'));
    else say(withWords(t('placing.under'), words), t('placing.under', { description: words.text }));
  }, ANNOUNCE_EVERY_MS);

  const pointOf = (event: MouseEvent) => clampPoint({ x: event.clientX, y: event.clientY }, options.bounds());
  const release = () => {
    pressedAt = null;
    dragging = false;
    drawn.hidden = true;
  };
  overlay.addEventListener('pointerdown', (event) => {
    if (bar.contains(event.target as Node) || event.button !== 0) return;
    pressedAt = pointOf(event);
  });
  overlay.addEventListener('pointermove', (event) => {
    if (pressedAt === null) return;
    const now = pointOf(event);
    if (!dragging && Math.max(Math.abs(now.x - pressedAt.x), Math.abs(now.y - pressedAt.y)) < POINT_SLOP) return;
    dragging = true;
    const rect = between(pressedAt, now, options.bounds());
    place(drawn, rect);
    size(drawn, rect);
    drawn.hidden = false;
  });
  overlay.addEventListener('pointerup', (event) => {
    if (pressedAt === null) return;
    const from = pressedAt;
    const drew = dragging;
    const now = pointOf(event);
    release();
    if (!drew) {
      options.place(now);
      return;
    }
    const rect = between(from, now, options.bounds());
    if (rect.width < MIN_AREA || rect.height < MIN_AREA) {
      options.place({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 });
    } else {
      options.area(rounded(rect));
    }
  });
  // A touch that the browser takes for a scroll or a zoom of the page.
  overlay.addEventListener('pointercancel', release);
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
    if (mark.hidden) {
      mark.hidden = false;
    } else {
      const by = event.shiftKey ? KEY_STEP_LARGE : KEY_STEP;
      crosshair = clampPoint({ x: crosshair.x + direction.x * by, y: crosshair.y + direction.y * by }, options.bounds());
    }
    place(mark, crosshair);
    keepClear();
    tell.call();
  });
  return { element: overlay, layout: keepClear, dispose: () => tell.cancel() };
}

export interface FrameOptions {
  t: Translate;
  mode: 'region' | 'cover' | 'area';
  /** Opened from the keyboard: the keyboard line shows from the start. */
  keyboard: boolean;
  /** The frozen screenshot the frame is drawn on, or null for the live page. */
  picture: HTMLElement | null;
  /** The size of the capture, or of the window: the frame is kept in its px. */
  bounds: Bounds;
  /** Where an element lies in the window, measured. */
  box(element: Element): Box;
  announce(message: string): void;
  done(rect: Rect): void;
  stop(): void;
}

const FRAME_TEXTS = {
  region: { label: 'region.label', bar: 'region.bar', stop: 'composer.cancel' },
  cover: { label: 'cover.label', bar: 'cover.bar', stop: 'composer.cancel' },
  area: { label: 'area.label', bar: 'area.bar', stop: 'area.stop' },
} as const;

export function frameOverlay(options: FrameOptions): Overlay {
  const { t, bounds, picture } = options;
  const texts = FRAME_TEXTS[options.mode];
  let rect = centred(bounds);
  let firstCorner: Point | null = null;
  let pressedAt: Point | null = null;
  let dragging = false;
  const frame = h('div', { class: 'frame', 'aria-hidden': 'true' });
  const corner = h('div', { class: 'corner', 'aria-hidden': 'true', hidden: true });
  const stop = button(t(texts.stop), () => options.stop(), { key: 'frame.stop' });
  const bar = overlayBar(stop, t(texts.bar), t('rect.keys'));
  const stage = h('div', { class: 'stage' }, [picture, frame, corner]);
  // On the live page the stage is the whole window and the bar floats over
  // it, as on the pointing overlay.
  const overlay = h(
    'div',
    {
      class: picture === null ? 'overlay live' : 'overlay framing',
      role: 'application',
      'aria-label': t(texts.label),
      'aria-describedby': DESCRIBED_BY,
      tabindex: '-1',
      'data-key': 'overlay',
    },
    picture === null ? [stage, bar] : [bar, stage],
  );
  followKeyboard(overlay, options.keyboard, () => fit());
  holdFocus(overlay, [stop]);
  if (picture === null) barMoves(bar, [stop]);
  // The capture, whole and centred in the stage below the bar: a point of the
  // capture lies at `offset + point * scale` on the stage.
  let stageBox: Box = { left: 0, top: 0, width: bounds.width, height: bounds.height };
  let scale = 1;
  let offset: Point = { x: 0, y: 0 };
  const onStage = (point: Point): Point => ({ x: offset.x + point.x * scale, y: offset.y + point.y * scale });
  const show = (element: HTMLElement, at: Rect) => {
    place(element, onStage(at));
    size(element, at, scale);
  };
  const draw = () => show(frame, rect);
  const showCorner = (point: Point) => place(corner, onStage(point));
  const fit = () => {
    stageBox = options.box(stage);
    scale = Math.min(stageBox.width / bounds.width, stageBox.height / bounds.height);
    offset = { x: (stageBox.width - bounds.width * scale) / 2, y: (stageBox.height - bounds.height * scale) / 2 };
    if (picture !== null) show(picture, { x: 0, y: 0, ...bounds });
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

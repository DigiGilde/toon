/**
 * Rectangles for choosing a part of the screenshot or covering something, in
 * CSS px of the captured viewport. Drawn by dragging, by two clicks, or with
 * the keyboard: arrows move, Shift with the arrows resizes. Also where the
 * feedback button and the open panel go so they never hide the page element
 * that has keyboard focus entirely.
 */
import type { Box, Point, Rect, Scroller } from './types';

export const KEY_STEP = 10;
export const KEY_STEP_LARGE = 50;
export const MIN_SIZE = 16;
/** Less than this between pointer down and up is a click, not a drag. */
export const DRAG_THRESHOLD = 4;

export interface Bounds {
  width: number;
  height: number;
}

const ARROWS: Record<string, Point> = {
  ArrowLeft: { x: -1, y: 0 },
  ArrowRight: { x: 1, y: 0 },
  ArrowUp: { x: 0, y: -1 },
  ArrowDown: { x: 0, y: 1 },
};

/** The direction of an arrow key, or null for any other key. */
export function arrow(key: string): Point | null {
  return ARROWS[key] ?? null;
}

export function clampPoint(point: Point, bounds: Bounds): Point {
  return {
    x: Math.min(Math.max(point.x, 0), bounds.width),
    y: Math.min(Math.max(point.y, 0), bounds.height),
  };
}

export function between(a: Point, b: Point, bounds: Bounds): Rect {
  const one = clampPoint(a, bounds);
  const two = clampPoint(b, bounds);
  return {
    x: Math.min(one.x, two.x),
    y: Math.min(one.y, two.y),
    width: Math.abs(one.x - two.x),
    height: Math.abs(one.y - two.y),
  };
}

export function centred(bounds: Bounds): Rect {
  const width = Math.round(bounds.width / 2);
  const height = Math.round(bounds.height / 2);
  return { x: Math.round((bounds.width - width) / 2), y: Math.round((bounds.height - height) / 2), width, height };
}

export function move(rect: Rect, by: Point, bounds: Bounds): Rect {
  return {
    ...rect,
    x: Math.min(Math.max(rect.x + by.x, 0), Math.max(0, bounds.width - rect.width)),
    y: Math.min(Math.max(rect.y + by.y, 0), Math.max(0, bounds.height - rect.height)),
  };
}

export function resize(rect: Rect, by: Point, bounds: Bounds): Rect {
  return {
    ...rect,
    width: Math.min(Math.max(rect.width + by.x, MIN_SIZE), bounds.width - rect.x),
    height: Math.min(Math.max(rect.height + by.y, MIN_SIZE), bounds.height - rect.y),
  };
}

/** The rectangle after a key press, or null when the key does not change it. */
export function step(rect: Rect, key: string, shift: boolean, bounds: Bounds): Rect | null {
  const direction = arrow(key);
  if (direction === null) return null;
  const by = { x: direction.x * KEY_STEP, y: direction.y * KEY_STEP };
  return shift ? resize(rect, by, bounds) : move(rect, by, bounds);
}

export function usable(rect: Rect): boolean {
  return rect.width >= MIN_SIZE && rect.height >= MIN_SIZE;
}

export function rounded(rect: Rect): Rect {
  return {
    x: Math.round(rect.x),
    y: Math.round(rect.y),
    width: Math.round(rect.width),
    height: Math.round(rect.height),
  };
}

/** Between the button and the edges of the window. */
export const TRIGGER_MARGIN = 16;
/** Kept free around a focused page element: the button's own 4 px ring, and
 * as much again for the element's focus outline. */
export const TRIGGER_CLEARANCE = 8;

export interface TriggerPlace {
  side: 'right' | 'left';
  /** From the bottom of the window to the button's bottom edge. */
  bottom: number;
}

const TRIGGER_HOME: TriggerPlace = { side: 'right', bottom: TRIGGER_MARGIN };

export function overlaps(a: Box, b: Box): boolean {
  return a.left < b.left + b.width && b.left < a.left + a.width && a.top < b.top + b.height && b.top < a.top + a.height;
}

/** The part two boxes share, or null when they do not overlap. */
export function intersection(a: Box, b: Box): Box | null {
  if (!overlaps(a, b)) return null;
  const left = Math.max(a.left, b.left);
  const top = Math.max(a.top, b.top);
  return {
    left,
    top,
    width: Math.min(a.left + a.width, b.left + b.width) - left,
    height: Math.min(a.top + a.height, b.top + b.height) - top,
  };
}

/** Whether a point lies in a box, its edges included. */
export function inside(point: Point, box: Box): boolean {
  return point.x >= box.left && point.x <= box.left + box.width && point.y >= box.top && point.y <= box.top + box.height;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
const HTML_NS = 'http://www.w3.org/1999/xhtml';

/** What an element's computed style says about its overflow. */
export interface OverflowStyle {
  display: string;
  overflowX: string;
  overflowY: string;
}

/** How an ancestor of a place clips it: on which axes, and by which of its
 * boxes. */
export interface Clipping {
  box: 'client' | 'border';
  x: boolean;
  y: boolean;
}

/** How an ancestor clips what it holds: on each axis whose overflow is not
 * visible (`overflow-x: clip` leaves y visible), by its client box. A
 * viewport inside a drawing, a nested svg or a foreignObject, clips by its
 * border box: some browsers give it no client box. Null for one that clips
 * nothing: overflow visible both ways, no box of its own
 * (`display: contents`), or a box overflow does not apply to, an inline box
 * of HTML or an SVG element that is no viewport. */
export function clipping(element: Element, style: OverflowStyle): Clipping | null {
  const x = style.overflowX !== 'visible';
  const y = style.overflowY !== 'visible';
  if ((!x && !y) || style.display === 'contents') return null;
  if (element.namespaceURI === SVG_NS) {
    const parent = element.parentNode;
    // An svg in HTML, or straight in a foreignObject, starts a drawing of its own.
    const outermost =
      element.localName === 'svg' &&
      (!(parent instanceof Element) || parent.namespaceURI !== SVG_NS || parent.localName === 'foreignObject');
    if (outermost) return { box: 'client', x, y };
    return element.localName === 'svg' || element.localName === 'foreignObject' ? { box: 'border', x, y } : null;
  }
  if (element.namespaceURI === HTML_NS && style.display === 'inline') return null;
  return { box: 'client', x, y };
}

/** An ancestor that clips a place, as measured: its box in the window's
 * coordinates, and the axes it clips on. */
export interface ClipPart {
  box: Box;
  x: boolean;
  y: boolean;
}

/** How far a clip reaches on an axis that nothing bounds: past any page a
 * browser lays out, in numbers that every sum keeps exact. */
const UNBOUNDED = 2 ** 30;

/** What the ancestors that clip a place leave of the window, axis by axis:
 * an axis none of them bounds reaches past any page. Ancestors that leave
 * nothing in common leave a box of no size, beyond any page: it holds no
 * place and overlaps nothing. Null when none of them bounds either axis. */
export function clipThrough(parts: readonly ClipPart[]): Box | null {
  let x: [number, number] | null = null;
  let y: [number, number] | null = null;
  const narrowed = (span: [number, number] | null, from: number, to: number): [number, number] =>
    span === null ? [from, to] : [Math.max(span[0], from), Math.min(span[1], to)];
  for (const part of parts) {
    if (part.x) x = narrowed(x, part.box.left, part.box.left + part.box.width);
    if (part.y) y = narrowed(y, part.box.top, part.box.top + part.box.height);
  }
  if (x === null && y === null) return null;
  const [left, right] = x ?? [-UNBOUNDED, UNBOUNDED];
  const [top, bottom] = y ?? [-UNBOUNDED, UNBOUNDED];
  if (right <= left || bottom <= top) return { left: -UNBOUNDED, top: -UNBOUNDED, width: 0, height: 0 };
  return { left, top, width: right - left, height: bottom - top };
}

/** Where the window scrolls to sideways for a place at `x`, in page
 * coordinates: where it is while the place is in it, and while the root or
 * the body hides what overflows sideways, which the visitor could not
 * scroll back. Else so that the place is in its middle. The browser keeps
 * the window within its range, which counts down from 0 right to left. */
export function windowLeft(x: number, scrollX: number, width: number, rootOverflowX: string, bodyOverflowX: string): number {
  const hides = (overflow: string) => overflow === 'hidden' || overflow === 'clip';
  if (hides(rootOverflowX) || hides(bodyOverflowX) || (x >= scrollX && x <= scrollX + width)) return scrollX;
  return x - width / 2;
}

/** Where one part of the page scrolls to. */
export interface ScrollStep {
  element: Element;
  to: Point;
}

export interface CentredPlace {
  steps: ScrollStep[];
  /** Where the point lies in the window once the parts have scrolled. */
  point: Point;
}

/** How to bring a point in the window's coordinates to the middle of every
 * part of the page that scrolls on its own and holds it, from the innermost
 * out, each as far as it can go: where each part that moves scrolls to, and
 * where the point then lies. All of it from one measure, as a smooth scroll
 * still reads the old position while it runs. The window scrolls last, to
 * that point. */
export function centreThrough(point: Point, scrollers: readonly Scroller[]): CentredPlace {
  const steps: ScrollStep[] = [];
  let at = point;
  for (const { element, box, scroll, min, max } of scrollers) {
    const to = {
      x: Math.min(Math.max(scroll.x + at.x - (box.left + box.width / 2), min.x), max.x),
      y: Math.min(Math.max(scroll.y + at.y - (box.top + box.height / 2), min.y), max.y),
    };
    if (to.x === scroll.x && to.y === scroll.y) continue;
    steps.push({ element, to });
    at = { x: at.x - (to.x - scroll.x), y: at.y - (to.y - scroll.y) };
  }
  return { steps, point: at };
}

/** The focused element with its clearance on every side. */
function kept(focused: Box): Box {
  return {
    left: focused.left - TRIGGER_CLEARANCE,
    top: focused.top - TRIGGER_CLEARANCE,
    width: focused.width + 2 * TRIGGER_CLEARANCE,
    height: focused.height + 2 * TRIGGER_CLEARANCE,
  };
}

/** Whether a box of the widget lies on the page element that has keyboard
 * focus, or on the clearance around it where its focus outline shows. */
export function covers(widget: Box, focused: Box): boolean {
  return overlaps(kept(focused), widget);
}

/** Where the open panel of a wide window goes so it does not cover the page
 * element that has keyboard focus: its own corner, else the bottom left
 * corner when the element is clear of it there. `home` is the panel's box in
 * its own corner, measured. */
export function panelSide(focused: Box, home: Box): 'right' | 'left' {
  if (!covers(home, focused)) return 'right';
  return covers({ ...home, left: TRIGGER_MARGIN }, focused) ? 'right' : 'left';
}

/** Where the feedback button goes so it never hides the page element that has
 * keyboard focus entirely (WCAG 2.4.11): its own corner, else the other bottom
 * corner, else just above the element. An element whose part in the window is
 * wider and taller than the button with its clearance cannot be hidden by it,
 * so the button stays in its corner: lifting it there would only put it on
 * something else, such as the site's navigation above a focused `main`. An
 * element that reaches both corners and leaves no room above keeps the button
 * in its corner too. `home` is the button's box in its own corner, measured,
 * so a scrollbar counts as the page lays it out. */
export function triggerPlace(focused: Box, home: Box, view: Bounds): TriggerPlace {
  const shownWidth = Math.min(focused.left + focused.width, view.width) - Math.max(focused.left, 0);
  const shownHeight = Math.min(focused.top + focused.height, view.height) - Math.max(focused.top, 0);
  if (shownWidth > home.width + 2 * TRIGGER_CLEARANCE && shownHeight > home.height + 2 * TRIGGER_CLEARANCE) {
    return TRIGGER_HOME;
  }
  if (!covers(home, focused)) return TRIGGER_HOME;
  if (!covers({ ...home, left: TRIGGER_MARGIN }, focused)) return { side: 'left', bottom: TRIGGER_MARGIN };
  const top = kept(focused).top;
  // The window's bottom edge lies TRIGGER_MARGIN below the button in its corner.
  const lifted = { side: 'right', bottom: home.top + home.height + TRIGGER_MARGIN - top } as const;
  return top - home.height >= 0 ? lifted : TRIGGER_HOME;
}

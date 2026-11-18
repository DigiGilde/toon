import { describe, expect, it } from 'vitest';

import {
  arrow,
  between,
  centred,
  centreThrough,
  clampPoint,
  type ClipPart,
  clipping,
  clipThrough,
  covers,
  inside,
  intersection,
  move,
  MIN_SIZE,
  overlaps,
  panelSide,
  resize,
  rounded,
  step,
  TRIGGER_CLEARANCE,
  triggerPlace,
  usable,
  windowLeft,
} from './rect';
import type { Box, Point, Scroller } from './types';

const bounds = { width: 1000, height: 600 };

describe('rectangles', () => {
  it('knows the arrow keys and nothing else', () => {
    expect(arrow('ArrowLeft')).toEqual({ x: -1, y: 0 });
    expect(arrow('ArrowRight')).toEqual({ x: 1, y: 0 });
    expect(arrow('ArrowUp')).toEqual({ x: 0, y: -1 });
    expect(arrow('ArrowDown')).toEqual({ x: 0, y: 1 });
    expect(arrow('a')).toBeNull();
  });

  it('keeps points inside the viewport', () => {
    expect(clampPoint({ x: -5, y: 700 }, bounds)).toEqual({ x: 0, y: 600 });
  });

  it('spans two corners in either order', () => {
    expect(between({ x: 300, y: 200 }, { x: 100, y: 50 }, bounds)).toEqual({ x: 100, y: 50, width: 200, height: 150 });
    expect(between({ x: -10, y: 0 }, { x: 2000, y: 10 }, bounds)).toEqual({ x: 0, y: 0, width: 1000, height: 10 });
  });

  it('starts centred at half the size', () => {
    expect(centred(bounds)).toEqual({ x: 250, y: 150, width: 500, height: 300 });
  });

  it('moves within the viewport', () => {
    const rect = { x: 10, y: 10, width: 100, height: 100 };
    expect(move(rect, { x: -50, y: 0 }, bounds)).toEqual({ ...rect, x: 0 });
    expect(move(rect, { x: 5000, y: 5000 }, bounds)).toEqual({ ...rect, x: 900, y: 500 });
    expect(move({ x: 0, y: 0, width: 2000, height: 10 }, { x: 10, y: 0 }, bounds).x).toBe(0);
  });

  it('resizes between the minimum and the viewport edge', () => {
    const rect = { x: 900, y: 500, width: 50, height: 50 };
    expect(resize(rect, { x: 500, y: 500 }, bounds)).toEqual({ ...rect, width: 100, height: 100 });
    expect(resize(rect, { x: -500, y: -500 }, bounds)).toEqual({ ...rect, width: MIN_SIZE, height: MIN_SIZE });
  });

  it('steps by key: arrows move, Shift with arrows resizes, other keys do nothing', () => {
    const rect = { x: 100, y: 100, width: 100, height: 100 };
    expect(step(rect, 'ArrowRight', false, bounds)).toEqual({ ...rect, x: 110 });
    expect(step(rect, 'ArrowDown', true, bounds)).toEqual({ ...rect, height: 110 });
    expect(step(rect, 'Enter', false, bounds)).toBeNull();
  });

  it('calls a rectangle usable from the minimum size up', () => {
    expect(usable({ x: 0, y: 0, width: MIN_SIZE, height: MIN_SIZE })).toBe(true);
    expect(usable({ x: 0, y: 0, width: MIN_SIZE - 1, height: 100 })).toBe(false);
    expect(usable({ x: 0, y: 0, width: 100, height: 3 })).toBe(false);
  });

  it('rounds to whole pixels', () => {
    expect(rounded({ x: 1.4, y: 1.6, width: 10.5, height: 0.2 })).toEqual({ x: 1, y: 2, width: 11, height: 0 });
  });
});

describe('the place of the feedback button', () => {
  // A 320 by 640 window, the button in its corner: right and bottom 16 px.
  const home = { left: 170, top: 580, width: 134, height: 44 };
  const view = { width: 320, height: 640 };
  const at = (left: number, top: number, width: number, height: number) => ({ left, top, width, height });
  const HOME = { side: 'right', bottom: 16 };

  it('stays in its corner while the focused element is clear of it and its ring', () => {
    expect(triggerPlace(at(20, 100, 200, 20), home, view)).toEqual(HOME);
    // Just outside the clearance: left of the button, above it, right of it.
    expect(triggerPlace(at(0, 600, 170 - TRIGGER_CLEARANCE, 20), home, view)).toEqual(HOME);
    expect(triggerPlace(at(200, 580 - 20 - TRIGGER_CLEARANCE, 50, 20), home, view)).toEqual(HOME);
    expect(triggerPlace(at(304 + TRIGGER_CLEARANCE, 600, 10, 20), home, view)).toEqual(HOME);
  });

  it('moves to the other corner when the element lies under it, or within its ring', () => {
    expect(triggerPlace(at(201, 603, 52, 17), home, view)).toEqual({ side: 'left', bottom: 16 });
    expect(triggerPlace(at(304 + TRIGGER_CLEARANCE - 1, 600, 10, 20), home, view)).toEqual({ side: 'left', bottom: 16 });
  });

  it('goes up, just clear of an element across the bottom it could hide', () => {
    // A cookie bar's text field across the window: the button's bottom edge
    // lands on the bar's top less the clearance, 640 - 592 = 48 px up.
    expect(triggerPlace(at(0, 600, 320, 30), home, view)).toEqual({ side: 'right', bottom: 48 });
    // Just low enough for the button with its ring to cover it.
    expect(triggerPlace(at(0, 580, 320, 44 + 2 * TRIGGER_CLEARANCE), home, view)).toEqual({ side: 'right', bottom: 68 });
  });

  it('stays in its corner by an element it cannot hide entirely: a skip link target, a whole main', () => {
    // Wider and taller than the button with its ring: lifting the button would
    // only put it on the navigation above.
    expect(triggerPlace(at(0, 148, 320, 900), home, view)).toEqual(HOME);
    expect(triggerPlace(at(0, 52, 320, 560), home, view)).toEqual(HOME);
    expect(triggerPlace(at(0, 579, 320, 45 + 2 * TRIGGER_CLEARANCE), home, view)).toEqual(HOME);
    expect(triggerPlace(at(0, 0, 320, 2000), home, view)).toEqual(HOME);
  });

  it('judges the element by what of it is in the window', () => {
    // Most of it scrolled away: the strip still in view is one the button can hide.
    expect(triggerPlace(at(0, 600, 320, 900), home, view)).toEqual({ side: 'right', bottom: 48 });
    expect(triggerPlace(at(-2000, 600, 2320, 30), home, view)).toEqual({ side: 'right', bottom: 48 });
    // Running far past the right edge: the part in view is narrower than the button.
    expect(triggerPlace(at(180, -500, 2000, 1130), home, view)).toEqual({ side: 'left', bottom: 16 });
  });

  it('stays in its corner by an element across the bottom that leaves no room above, in a low window', () => {
    const low = { left: 170, top: 40, width: 134, height: 44 };
    expect(triggerPlace(at(0, 50, 320, 40), low, { width: 320, height: 100 })).toEqual(HOME);
  });
});

describe('what the widget covers of a focused page element', () => {
  const at = (left: number, top: number, width: number, height: number) => ({ left, top, width, height });
  const note = at(944, 518, 320, 126);

  it('overlaps only where two boxes share area, an edge being no share', () => {
    expect(overlaps(note, at(1000, 600, 50, 20))).toBe(true);
    expect(overlaps(note, at(1264, 600, 50, 20))).toBe(false);
    expect(overlaps(note, at(1000, 644, 50, 20))).toBe(false);
  });

  it('covers an element under it, or within the clearance for its focus outline', () => {
    expect(covers(note, at(1000, 600, 50, 20))).toBe(true);
    // Just below the box, and just left of it: the outline would be under it.
    expect(covers(note, at(1000, 644 + TRIGGER_CLEARANCE - 1, 50, 20))).toBe(true);
    expect(covers(note, at(944 - 50 - TRIGGER_CLEARANCE + 1, 600, 50, 20))).toBe(true);
    expect(covers(note, at(1000, 644 + TRIGGER_CLEARANCE, 50, 20))).toBe(false);
    expect(covers(note, at(944 - 50 - TRIGGER_CLEARANCE, 600, 50, 20))).toBe(false);
  });
});

describe('the side of the open panel in a wide window', () => {
  const at = (left: number, top: number, width: number, height: number) => ({ left, top, width, height });
  // A 1280 by 720 window, the panel in its corner: right 16, bottom 76.
  const home = at(880, 24, 384, 620);

  it('stays in its corner while the focused element is clear of it', () => {
    expect(panelSide(at(100, 300, 200, 20), home)).toBe('right');
    expect(panelSide(at(880 - 30 - TRIGGER_CLEARANCE, 300, 30, 20), home)).toBe('right');
  });

  it('goes to the bottom left corner while the element lies under it and is clear of it there', () => {
    // A link in a right-hand column, under the panel.
    expect(panelSide(at(1000, 231, 105, 21), home)).toBe('left');
    // Just right of where the panel would be on the left, outline included.
    expect(panelSide(at(16 + 384 + TRIGGER_CLEARANCE, 300, 500, 20), home)).toBe('left');
  });

  it('stays in its corner when the element would be under it on the left as well', () => {
    expect(panelSide(at(0, 300, 1280, 40), home)).toBe('right');
    expect(panelSide(at(16 + 384 + TRIGGER_CLEARANCE - 1, 300, 500, 20), home)).toBe('right');
  });
});

describe('where a part of the page that scrolls shows a place', () => {
  const at = (left: number, top: number, width: number, height: number) => ({ left, top, width, height });
  const pane = at(240, 0, 1040, 720);

  it('is the part two boxes share, and nothing when they only touch or lie apart', () => {
    expect(intersection(pane, at(200, 700, 100, 40))).toEqual(at(240, 700, 60, 20));
    expect(intersection(at(300, 100, 50, 20), pane)).toEqual(at(300, 100, 50, 20));
    expect(intersection(pane, at(100, 100, 140, 20))).toBeNull();
    expect(intersection(pane, at(300, 720, 50, 20))).toBeNull();
    expect(intersection(pane, at(300, 900, 50, 20))).toBeNull();
  });

  it('holds a point on its edges, and not one beyond them', () => {
    expect(inside({ x: 240, y: 0 }, pane)).toBe(true);
    expect(inside({ x: 1280, y: 720 }, pane)).toBe(true);
    expect(inside({ x: 239, y: 300 }, pane)).toBe(false);
    expect(inside({ x: 1281, y: 300 }, pane)).toBe(false);
    expect(inside({ x: 300, y: -1 }, pane)).toBe(false);
    expect(inside({ x: 300, y: 721 }, pane)).toBe(false);
  });
});

describe('how an ancestor of a place clips it', () => {
  const style = (overflowX: string, overflowY = overflowX, display = 'block') => ({ display, overflowX, overflowY });
  const svg = (name: string, parent: Element | null = null) => {
    const element = document.createElementNS('http://www.w3.org/2000/svg', name);
    parent?.append(element);
    return element;
  };

  it('clips by its client box, on each axis whose overflow is not visible', () => {
    const box = document.createElement('div');
    expect(clipping(box, style('hidden'))).toEqual({ box: 'client', x: true, y: true });
    expect(clipping(box, style('hidden', 'auto'))).toEqual({ box: 'client', x: true, y: true });
    // Clip is the one value that leaves the other axis visible.
    expect(clipping(box, style('clip', 'visible'))).toEqual({ box: 'client', x: true, y: false });
    expect(clipping(box, style('visible', 'clip'))).toEqual({ box: 'client', x: false, y: true });
    expect(clipping(document.createElement('a'), style('hidden', 'hidden', 'inline-block'))).toEqual({
      box: 'client',
      x: true,
      y: true,
    });
  });

  it('clips nothing with overflow visible both ways, without a box of its own, or as an inline box of HTML', () => {
    expect(clipping(document.createElement('div'), style('visible'))).toBeNull();
    expect(clipping(document.createElement('div'), style('hidden', 'hidden', 'contents'))).toBeNull();
    // Bootstrap's text-truncate on a link: overflow does not apply to it, and
    // its client box reads 0.
    expect(clipping(document.createElement('a'), style('hidden', 'hidden', 'inline'))).toBeNull();
  });

  it('clips by its border box as a viewport inside a drawing, which some browsers give no client box', () => {
    const chart = svg('svg');
    document.createElement('figure').append(chart);
    // A plot area, and a diagram's label in HTML.
    expect(clipping(svg('svg', chart), style('hidden', 'hidden', 'inline'))).toEqual({ box: 'border', x: true, y: true });
    expect(clipping(svg('foreignObject', chart), style('hidden', 'hidden', 'inline'))).toEqual({
      box: 'border',
      x: true,
      y: true,
    });
  });

  it('clips by its client box as an svg that starts a drawing: in HTML, inline as it is, or in a foreignObject', () => {
    const chart = svg('svg');
    document.createElement('figure').append(chart);
    expect(clipping(chart, style('hidden', 'hidden', 'inline'))).toEqual({ box: 'client', x: true, y: true });
    expect(clipping(svg('svg'), style('hidden'))).toEqual({ box: 'client', x: true, y: true });
    const icon = svg('svg', svg('foreignObject', svg('svg')));
    expect(clipping(icon, style('hidden'))).toEqual({ box: 'client', x: true, y: true });
  });

  it('clips nothing as an SVG element that is no viewport, which overflow does not apply to', () => {
    expect(clipping(svg('g', svg('svg')), style('hidden'))).toBeNull();
  });
});

describe('what the ancestors that clip a place leave of the window', () => {
  const at = (left: number, top: number, width: number, height: number) => ({ left, top, width, height });
  const both = (box: Box): ClipPart => ({ box, x: true, y: true });

  it('is nothing when none of them bounds either axis', () => {
    expect(clipThrough([])).toBeNull();
    expect(clipThrough([{ box: at(0, 0, 10, 10), x: false, y: false }])).toBeNull();
  });

  it('is what they all share', () => {
    const pane = both(at(0, 100, 1024, 668));
    expect(clipThrough([pane])).toEqual(at(0, 100, 1024, 668));
    // A code listing that clips inside the pane, running below its fold.
    expect(clipThrough([both(at(40, 600, 600, 200)), pane])).toEqual(at(40, 600, 600, 168));
  });

  it('bounds no axis that an ancestor lets overflow show on, so a page runs on below a body one window high', () => {
    // html, body { height: 100%; overflow-x: clip }: the body clips sideways only.
    const body: ClipPart = { box: at(16, 0, 1248, 720), x: true, y: false };
    const clip = clipThrough([body])!;
    expect(inside({ x: 100, y: 2400 }, clip)).toBe(true);
    expect(inside({ x: 1300, y: 300 }, clip)).toBe(false);
    // A quote far below keeps its marks whole, and in the window what the body shows.
    expect(intersection(at(16, 2400, 300, 18), clip)).toEqual(at(16, 2400, 300, 18));
    expect(intersection(at(0, 1800, 1280, 720), clip)).toEqual(at(16, 1800, 1248, 720));
    // A part that clips both ways inside it takes its height from that part.
    expect(clipThrough([both(at(0, 2000, 1280, 600)), body])).toEqual(at(16, 2000, 1248, 600));
    // A banner 60 px high with overflow-y: clip lets its line run on sideways.
    const banner = clipThrough([{ box: at(0, 0, 300, 60), x: false, y: true }])!;
    expect(inside({ x: 900, y: 30 }, banner)).toBe(true);
    expect(inside({ x: 100, y: 80 }, banner)).toBe(false);
  });

  it('holds no place and overlaps nothing where they share nothing, or one of them has no size', () => {
    const apart = clipThrough([both(at(0, 0, 100, 100)), both(at(200, 0, 100, 100))])!;
    const touching = clipThrough([both(at(0, 0, 100, 100)), both(at(100, 0, 100, 100))])!;
    const collapsed = clipThrough([{ box: at(50, 50, 0, 40), x: true, y: false }])!;
    for (const clip of [apart, touching, collapsed]) {
      for (const point of [
        { x: 0, y: 0 },
        { x: 100, y: 0 },
        { x: 50, y: 60 },
        { x: 200, y: 100 },
      ]) {
        expect(inside(point, clip)).toBe(false);
      }
      expect(intersection(at(-100_000, -100_000, 200_000, 200_000), clip)).toBeNull();
    }
  });
});

describe('where the window goes sideways for a place', () => {
  // A window 1024 px wide on a page wider than that.
  it('stays while the place is in it, its edges included', () => {
    expect(windowLeft(0, 0, 1024, 'visible', 'visible')).toBe(0);
    expect(windowLeft(849, 0, 1024, 'auto', 'visible')).toBe(0);
    expect(windowLeft(1344, 320, 1024, 'visible', 'visible')).toBe(320);
  });

  it('puts a place beside it in its middle', () => {
    expect(windowLeft(1500, 0, 1024, 'visible', 'visible')).toBe(988);
    expect(windowLeft(100, 600, 1024, 'auto', 'visible')).toBe(-412);
  });

  it('goes below 0 on a page that runs right to left, where scrolling to the left counts down from 0', () => {
    expect(windowLeft(-900, -200, 1024, 'visible', 'visible')).toBe(-1412);
  });

  it('stays where the root or the body hides what overflows sideways, which the visitor could not scroll back', () => {
    for (const [root, body] of [
      ['hidden', 'visible'],
      ['clip', 'visible'],
      ['visible', 'hidden'],
      ['visible', 'clip'],
      ['auto', 'hidden'],
    ] as const) {
      expect(windowLeft(1500, 0, 1024, root, body)).toBe(0);
      expect(windowLeft(100, 320, 1024, root, body)).toBe(320);
    }
  });
});

describe('how a place comes to the middle of what scrolls around it', () => {
  const inner = document.createElement('div');
  const outer = document.createElement('main');
  /** A part of the page that scrolls, with a range from 0 on each axis. */
  const part = (element: Element, box: Box, scroll: Point, max: Point): Scroller => ({
    element,
    box,
    scroll,
    min: { x: 0, y: 0 },
    max,
  });

  it('leaves the point as it is where only the window scrolls: the window then centres it', () => {
    expect(centreThrough({ x: 600, y: 2850 }, [])).toEqual({ steps: [], point: { x: 600, y: 2850 } });
  });

  it('scrolls a part so that the point lies in the middle of its box', () => {
    // A pane below a header of 100 px, scrolled 500 down, the point far below its fold.
    const pane = part(outer, { left: 0, top: 100, width: 1024, height: 668 }, { x: 0, y: 500 }, { x: 0, y: 3000 });
    expect(centreThrough({ x: 60, y: 1372 }, [pane])).toEqual({
      steps: [{ element: outer, to: { x: 0, y: 1438 } }],
      point: { x: 60, y: 434 },
    });
    // Above the middle: it scrolls back up.
    expect(centreThrough({ x: 60, y: 200 }, [pane])).toEqual({
      steps: [{ element: outer, to: { x: 0, y: 266 } }],
      point: { x: 60, y: 434 },
    });
  });

  it('goes as far as the part can go, and no further, the point then off its middle', () => {
    const pane = part(outer, { left: 0, top: 100, width: 1024, height: 668 }, { x: 0, y: 500 }, { x: 0, y: 1000 });
    expect(centreThrough({ x: 60, y: 1372 }, [pane])).toEqual({
      steps: [{ element: outer, to: { x: 0, y: 1000 } }],
      point: { x: 60, y: 872 },
    });
    expect(centreThrough({ x: 60, y: -200 }, [pane])).toEqual({
      steps: [{ element: outer, to: { x: 0, y: 0 } }],
      point: { x: 60, y: 300 },
    });
  });

  it('scrolls sideways too, within the range of each axis, which counts down from 0 right to left', () => {
    // A wide table that scrolls sideways only.
    const table = part(inner, { left: 100, top: 50, width: 400, height: 300 }, { x: 0, y: 0 }, { x: 900, y: 0 });
    expect(centreThrough({ x: 1000, y: 120 }, [table])).toEqual({
      steps: [{ element: inner, to: { x: 700, y: 0 } }],
      point: { x: 300, y: 120 },
    });
    const rtl: Scroller = { ...table, min: { x: -900, y: 0 }, max: { x: 0, y: 0 } };
    expect(centreThrough({ x: -500, y: 120 }, [rtl])).toEqual({
      steps: [{ element: inner, to: { x: -800, y: 0 } }],
      point: { x: 300, y: 120 },
    });
  });

  it('leaves alone an axis the visitor cannot scroll, which keeps where a script put it', () => {
    const hidden: Scroller = {
      ...part(inner, { left: 0, top: 0, width: 400, height: 300 }, { x: 37, y: 0 }, { x: 37, y: 600 }),
      min: { x: 37, y: 0 },
    };
    expect(centreThrough({ x: 900, y: 450 }, [hidden])).toEqual({
      steps: [{ element: inner, to: { x: 37, y: 300 } }],
      point: { x: 900, y: 150 },
    });
  });

  it('passes over a part that has the point in its middle already, scrolling nothing there', () => {
    const pane = part(outer, { left: 0, top: 100, width: 1000, height: 600 }, { x: 0, y: 500 }, { x: 0, y: 3000 });
    expect(centreThrough({ x: 500, y: 400 }, [pane])).toEqual({ steps: [], point: { x: 500, y: 400 } });
  });

  it('centres from the innermost part out, each on where the parts within have moved the point', () => {
    // A code listing that scrolls inside a pane that scrolls.
    const listing = part(inner, { left: 40, top: 1200, width: 600, height: 200 }, { x: 0, y: 0 }, { x: 0, y: 800 });
    const pane = part(outer, { left: 0, top: 100, width: 1024, height: 668 }, { x: 0, y: 0 }, { x: 0, y: 2000 });
    expect(centreThrough({ x: 100, y: 1700 }, [listing, pane])).toEqual({
      steps: [
        { element: inner, to: { x: 0, y: 400 } },
        { element: outer, to: { x: 0, y: 866 } },
      ],
      point: { x: 100, y: 434 },
    });
  });
});

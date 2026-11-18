import { afterEach, describe, expect, it, vi } from 'vitest';

import { translator } from './i18n';
import { ANNOUNCE_EVERY_MS, BAR_CLEARANCE, frameOverlay, MIN_AREA, placingOverlay, POINT_SLOP } from './overlay';
import { STYLES } from './styles';
import type { Box } from './types';

const t = translator('nl');

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = '';
});

function key(target: Element, name: string, shiftKey = false): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key: name, shiftKey, bubbles: true, cancelable: true });
  target.dispatchEvent(event);
  return event;
}

/** Tab and Shift+Tab, from the overlay and from its buttons, go round them
 * and nothing else. */
function holdsFocus(element: HTMLElement, ...keys: string[]): void {
  const ring = [element, ...keys.map((name) => element.querySelector<HTMLElement>(`[data-key="${name}"]`)!)];
  element.focus();
  for (const [index, from] of ring.entries()) {
    expect(key(from, 'Tab').defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(ring[(index + 1) % ring.length]);
  }
  expect(document.activeElement).toBe(element);
  for (const [index, from] of [...ring.entries()].reverse()) {
    from.focus();
    expect(key(from, 'Tab', true).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(ring[(index + ring.length - 1) % ring.length]);
  }
}

/** The bar's way out first, then the line for the pointer and the line for
 * the keyboard, which together describe the overlay. */
function barOf(element: HTMLElement, stopKey: string, pointer: string, keys: string, more: string[] = []): void {
  const bar = element.querySelector('.bar')!;
  const [first, help, ...rest] = Array.from(bar.children);
  const keyboard = rest.pop();
  expect(first!.getAttribute('data-key')).toBe(stopKey);
  expect(rest.map((child) => child.getAttribute('data-key'))).toEqual(more);
  expect([help!.className, help!.textContent]).toEqual(['help', pointer]);
  expect([keyboard!.className, keyboard!.textContent]).toEqual(['keys', keys]);
  expect(element.getAttribute('aria-describedby')).toBe(`${help!.id} ${keyboard!.id}`);
  expect(help!.id).not.toBe('');
  expect(keyboard!.id).not.toBe('');
}

function pointer(target: Element, type: string, clientX: number, clientY: number, button = 0): MouseEvent {
  const event = new MouseEvent(type, { clientX, clientY, button, bubbles: true, cancelable: true });
  target.dispatchEvent(event);
  return event;
}

describe('the bar of an overlay', () => {
  it('shows the keyboard line only on an overlay with the class keyboard', () => {
    // jsdom does no styling: the rule itself is the contract, a real browser
    // runs it (e2e, and the check at 320 px).
    expect(STYLES).toContain('.overlay:not(.keyboard) .keys { display: none; }');
  });

  it('leaves the frame overlays half the window for the picture, whatever the font', () => {
    // A wide font at 400% zoom (320x180) made the bar take the whole window.
    expect(STYLES).toContain('.framing .bar { max-height: calc(50% - 16px); overflow-y: auto; }');
  });

  it('leaves the overlays on the live page half the window too, whatever the font', () => {
    // The pointing bar holds a second button: at 320x180 it would otherwise
    // reach the crosshair at the other edge.
    expect(STYLES).toContain('.placing .bar, .live .bar { max-height: calc(50% - 16px); overflow-y: auto; }');
  });
});

describe('pointing at a spot', () => {
  // A 1000 by 600 window; the bar is 640 by 100 at the top, or at the bottom.
  const TOP: Box = { left: 180, top: 8, width: 640, height: 100 };
  const BOTTOM: Box = { left: 180, top: 492, width: 640, height: 100 };

  function setUp(description = 'Alinea over de begroting', bounds = { width: 1000, height: 600 }, keyboard = false) {
    const options = {
      t,
      keyboard,
      bounds: () => bounds,
      box: vi.fn((element: Element) => (element.classList.contains('bottom') ? BOTTOM : TOP)),
      describe: vi.fn().mockReturnValue({ text: description, lang: null }),
      announce: vi.fn(),
      place: vi.fn(),
      area: vi.fn(),
      outline: vi.fn(),
      stop: vi.fn(),
    };
    const overlay = placingOverlay(options);
    document.body.append(overlay.element);
    const crosshair = overlay.element.querySelector<HTMLElement>('.crosshair')!;
    const bar = overlay.element.querySelector<HTMLElement>('.bar')!;
    const drawn = overlay.element.querySelector<HTMLElement>('.drawn')!;
    const atBottom = () => bar.classList.contains('bottom');
    return { options, overlay, element: overlay.element, crosshair, bar, drawn, atBottom };
  }

  /** A press at one place and a release at another, with a move between. */
  function drag(element: HTMLElement, from: [number, number], to: [number, number]): void {
    pointer(element, 'pointerdown', ...from);
    pointer(element, 'pointermove', ...to);
    pointer(element, 'pointerup', ...to);
  }

  it('is a labelled, focusable application area that shows no crosshair until the first arrow key', () => {
    const { element, crosshair, options } = setUp();
    expect(element.getAttribute('role')).toBe('application');
    expect(element.getAttribute('aria-label')).toBe('Wijs een punt of gebied aan');
    expect(element.getAttribute('tabindex')).toBe('-1');
    expect(element.dataset.key).toBe('overlay');
    // A mark in the middle reads as something to drag: a pointer gets the
    // crosshair cursor instead, and nothing is said yet.
    expect(crosshair.hidden).toBe(true);
    expect(crosshair.getAttribute('aria-hidden')).toBe('true');
    expect(STYLES).toMatch(/\.overlay \{[^}]*cursor: crosshair;/);
    key(element, 'Shift');
    expect(crosshair.hidden).toBe(true);
    // The first arrow puts it at the start, the middle, without moving it.
    expect(key(element, 'ArrowRight', true).defaultPrevented).toBe(true);
    expect(crosshair.hidden).toBe(false);
    expect([crosshair.style.left, crosshair.style.top]).toEqual(['500px', '300px']);
    expect(options.describe).toHaveBeenCalledWith({ x: 500, y: 300 });
  });

  it('picks the start with Enter before any arrow key, still without showing the crosshair', () => {
    const { options, element, crosshair } = setUp();
    expect(key(element, 'Enter').defaultPrevented).toBe(true);
    expect(options.place).toHaveBeenCalledWith({ x: 500, y: 300 });
    expect(crosshair.hidden).toBe(true);
  });

  it('puts its way out first in the bar, then how to point with a pointer, the way to an area without dragging, and the keyboard', () => {
    const { element } = setUp();
    barOf(element, 'placing.stop', t('placing.bar'), t('placing.keys'), ['placing.area']);
    expect(t('placing.bar')).toBe('Klik of tik op een plek. Sleep voor een gebied, of kies Gebied zonder slepen.');
    expect(t('placing.keys')).toBe(
      'De pijltjes zetten een kruis in het midden en verplaatsen het, met Shift sneller. Enter kiest de plek. Voor een gebied is er Gebied zonder slepen.',
    );
    expect(element.querySelector('[data-key="placing.area"]')!.textContent).toBe('Gebied zonder slepen');
  });

  it('keeps Tab on itself and its two buttons: the page under it is out of reach', () => {
    const { element } = setUp();
    holdsFocus(element, 'placing.stop', 'placing.area');
  });

  it('goes to the frame overlay from its button, for whoever cannot drag, and leaves the bar where it is', () => {
    const { options, element, atBottom } = setUp();
    element.querySelector<HTMLButtonElement>('[data-key="placing.area"]')!.click();
    expect(options.outline).toHaveBeenCalledOnce();
    expect([options.place, options.area, options.stop].map((mock) => mock.mock.calls.length)).toEqual([0, 0, 0]);
    expect(atBottom()).toBe(false);
  });

  it('shows the keyboard line from the start when opened from the keyboard, else from the first key on it', () => {
    expect(setUp('x', undefined, true).element.classList.contains('keyboard')).toBe(true);
    const { element, options, bar } = setUp();
    expect(element.classList.contains('keyboard')).toBe(false);
    // A press is no reason: the line would move what is being pressed on.
    pointer(element, 'pointerdown', 10, 300);
    pointer(element, 'click', 10, 300);
    expect(element.classList.contains('keyboard')).toBe(false);
    options.box.mockClear();
    key(bar.querySelector('button')!, 'Tab');
    expect(element.classList.contains('keyboard')).toBe(true);
    // The bar grew, but no crosshair shows yet to keep it clear of.
    expect(options.box).not.toHaveBeenCalled();
    key(element, 'ArrowUp');
    expect(options.box).toHaveBeenCalledWith(bar);
    options.box.mockClear();
    key(element, 'a');
    expect(options.box).not.toHaveBeenCalled();
  });

  it('places where the pointer clicks, and moves its bar to the other edge when that is pressed', () => {
    const { options, element, bar, atBottom } = setUp();
    pointer(element, 'pointerdown', 120, 340);
    pointer(element, 'pointerup', 120, 340);
    // The click that follows is no second point.
    pointer(element, 'click', 120, 340);
    expect(options.place).toHaveBeenCalledWith({ x: 120, y: 340 });
    // On touch nothing else moves the bar off what lies under it.
    pointer(bar.querySelector('.help')!, 'click', 300, 30);
    expect(atBottom()).toBe(true);
    pointer(bar, 'click', 300, 560);
    expect(atBottom()).toBe(false);
    expect(options.place).toHaveBeenCalledOnce();
  });

  it('places a point where the pointer is let go after a move shorter than the slop, and draws nothing', () => {
    const { options, element, drawn } = setUp();
    pointer(element, 'pointerdown', 100, 100);
    pointer(element, 'pointermove', 100 + POINT_SLOP - 1, 100 - (POINT_SLOP - 1));
    expect(drawn.hidden).toBe(true);
    pointer(element, 'pointerup', 103, 98);
    expect(options.place).toHaveBeenCalledWith({ x: 103, y: 98 });
    expect(options.area).not.toHaveBeenCalled();
    expect(POINT_SLOP).toBe(6);
  });

  it('draws an area live while the pointer drags, in the window, and chooses it when let go', () => {
    const { options, element, drawn } = setUp();
    const styleOf = () => [drawn.style.left, drawn.style.top, drawn.style.width, drawn.style.height].join(' ');
    expect(drawn.getAttribute('aria-hidden')).toBe('true');
    pointer(element, 'pointerdown', 300, 200);
    pointer(element, 'pointermove', 300 + POINT_SLOP, 200);
    expect(drawn.hidden).toBe(false);
    expect(styleOf()).toBe('300px 200px 6px 0px');
    // Dragged up and to the left of where it started.
    pointer(element, 'pointermove', 100.4, 50);
    expect(styleOf()).toBe('100.4px 50px 199.6px 150px');
    // Once a drag, always a drag: back near the start it is still drawing.
    pointer(element, 'pointermove', 302, 201);
    expect(drawn.hidden).toBe(false);
    // Past the window, it stops at its edges.
    pointer(element, 'pointermove', 1200, 700);
    expect(styleOf()).toBe('300px 200px 700px 400px');
    pointer(element, 'pointerup', 1200, 700);
    expect(options.area).toHaveBeenCalledWith({ x: 300, y: 200, width: 700, height: 400 });
    expect(options.place).not.toHaveBeenCalled();
    expect(drawn.hidden).toBe(true);
    expect(STYLES).toMatch(/\.drawn \{[^}]*border: 2px solid var\(--tf-accent\);/);
  });

  it('rounds the area it chooses to whole px', () => {
    const { options, element } = setUp();
    drag(element, [10.4, 20.6], [110.5, 80.2]);
    expect(options.area).toHaveBeenCalledWith({ x: 10, y: 21, width: 100, height: 60 });
  });

  it('takes an area smaller than 12 by 12 px as a point at its centre, also a narrow one', () => {
    const { options, element } = setUp();
    drag(element, [100, 100], [100 + MIN_AREA - 1, 100 + MIN_AREA - 1]);
    expect(options.place).toHaveBeenLastCalledWith({ x: 105.5, y: 105.5 });
    drag(element, [100, 100], [400, 100 + MIN_AREA - 1]);
    expect(options.place).toHaveBeenLastCalledWith({ x: 250, y: 105.5 });
    drag(element, [100, 100], [100 + MIN_AREA - 1, 400]);
    expect(options.place).toHaveBeenLastCalledWith({ x: 105.5, y: 250 });
    expect(options.area).not.toHaveBeenCalled();
    drag(element, [100, 100], [100 + MIN_AREA, 100 + MIN_AREA]);
    expect(options.area).toHaveBeenCalledWith({ x: 100, y: 100, width: 12, height: 12 });
    expect(MIN_AREA).toBe(12);
  });

  it('drops a drag the browser takes over for a scroll or a zoom', () => {
    const { options, element, drawn } = setUp();
    pointer(element, 'pointerdown', 100, 100);
    pointer(element, 'pointermove', 100, 300);
    pointer(element, 'pointercancel', 100, 300);
    expect(drawn.hidden).toBe(true);
    pointer(element, 'pointermove', 200, 400);
    pointer(element, 'pointerup', 200, 400);
    expect(drawn.hidden).toBe(true);
    expect([options.place, options.area].map((mock) => mock.mock.calls.length)).toEqual([0, 0]);
  });

  it('ignores a press on its bar, a press with another button, and a move or release without a press', () => {
    const { options, element, bar, drawn } = setUp();
    pointer(element, 'pointermove', 200, 200);
    pointer(element, 'pointerup', 200, 200);
    pointer(bar.querySelector('.help')!, 'pointerdown', 300, 30);
    pointer(element, 'pointermove', 500, 300);
    pointer(element, 'pointerup', 500, 300);
    pointer(element, 'pointerdown', 100, 100, 2);
    pointer(element, 'pointerup', 100, 100, 2);
    expect(drawn.hidden).toBe(true);
    expect([options.place, options.area].map((mock) => mock.mock.calls.length)).toEqual([0, 0]);
  });

  it('stops from the visible button, for whoever has no Escape key, and leaves the bar where it is', () => {
    const { options, element, atBottom } = setUp();
    element.querySelector<HTMLButtonElement>('[data-key="placing.stop"]')!.click();
    expect(options.stop).toHaveBeenCalledOnce();
    expect(options.place).not.toHaveBeenCalled();
    expect(atBottom()).toBe(false);
  });

  it('moves the crosshair by 10 px, 50 px with Shift, within the viewport, and places with Enter', () => {
    const { options, element, crosshair } = setUp();
    key(element, 'ArrowRight');
    expect(key(element, 'ArrowRight').defaultPrevented).toBe(true);
    key(element, 'ArrowDown', true);
    expect([crosshair.style.left, crosshair.style.top]).toEqual(['510px', '350px']);
    for (let step = 0; step < 30; step += 1) key(element, 'ArrowLeft', true);
    expect(crosshair.style.left).toBe('0px');
    expect(key(element, 'Enter').defaultPrevented).toBe(true);
    expect(options.place).toHaveBeenCalledWith({ x: 0, y: 350 });
  });

  it('moves its bar to the edge away from the crosshair once the crosshair comes near it, and only then', () => {
    const { element, atBottom } = setUp();
    const up = (times: number) => {
      for (let step = 0; step < times; step += 1) key(element, 'ArrowUp', true);
    };
    const down = (times: number) => {
      for (let step = 0; step < times; step += 1) key(element, 'ArrowDown', true);
    };
    key(element, 'ArrowUp');
    // At y 150 the crosshair is clear of the bar, 108, and the ring around it.
    up(3);
    expect(atBottom()).toBe(false);
    up(1);
    expect(atBottom()).toBe(true);
    // Back across the middle of the window to y 450: the bar stays where it
    // went, until the crosshair comes near it there.
    down(7);
    expect(atBottom()).toBe(true);
    down(1);
    expect(atBottom()).toBe(false);
    expect(BOTTOM.top - BAR_CLEARANCE).toBeGreaterThan(450);
  });

  it('leaves its bar where it is when the crosshair passes beside it', () => {
    const { element, atBottom } = setUp();
    key(element, 'ArrowLeft');
    for (let step = 0; step < 8; step += 1) key(element, 'ArrowLeft', true);
    for (let step = 0; step < 6; step += 1) key(element, 'ArrowUp', true);
    // At (100, 0): left of the bar, which starts at 180 less its 20 px clearance.
    expect(atBottom()).toBe(false);
  });

  it('moves its bar off the crosshair as it shows, and after the window changes', () => {
    // A low window: the bar at the top reaches the middle, where the
    // crosshair shows at the first arrow key. Until then there is nothing to
    // keep clear of.
    const { overlay, atBottom, options } = setUp('x', { width: 1000, height: 200 });
    overlay.layout();
    expect(atBottom()).toBe(false);
    expect(options.box).not.toHaveBeenCalled();
    key(overlay.element, 'ArrowDown');
    expect(atBottom()).toBe(true);
    expect(options.box).toHaveBeenCalledWith(overlay.element.querySelector('.bar'));
    const { overlay: roomy, atBottom: roomyAtBottom } = setUp();
    key(roomy.element, 'ArrowDown');
    roomy.layout();
    expect(roomyAtBottom()).toBe(false);
  });

  it('says what lies under the crosshair, at most once per interval and always the last', () => {
    vi.useFakeTimers();
    const { options, element } = setUp();
    options.describe.mockReturnValueOnce({ text: 'Alinea over de begroting', lang: null }).mockReturnValueOnce({ text: 'Kop van de pagina', lang: null });
    key(element, 'ArrowUp');
    expect(options.announce).toHaveBeenLastCalledWith('Onder het kruis: Alinea over de begroting');
    key(element, 'ArrowUp');
    key(element, 'ArrowUp');
    expect(options.announce).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(ANNOUNCE_EVERY_MS);
    expect(options.announce).toHaveBeenLastCalledWith('Onder het kruis: Kop van de pagina');
    expect(options.describe).toHaveBeenLastCalledWith({ x: 500, y: 280 });
  });

  it('does not repeat itself while the crosshair stays on the same element', () => {
    vi.useFakeTimers();
    const { options, element } = setUp();
    key(element, 'ArrowUp');
    vi.advanceTimersByTime(ANNOUNCE_EVERY_MS);
    key(element, 'ArrowUp');
    expect(options.announce).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(ANNOUNCE_EVERY_MS);
    options.describe.mockReturnValueOnce({ text: 'Iets anders', lang: null });
    key(element, 'ArrowUp');
    vi.advanceTimersByTime(ANNOUNCE_EVERY_MS);
    key(element, 'ArrowUp');
    expect(options.announce.mock.calls.map(([message]) => message)).toEqual([
      'Onder het kruis: Alinea over de begroting',
      'Onder het kruis: Iets anders',
      'Onder het kruis: Alinea over de begroting',
    ]);
  });

  it('says so when nothing with text lies under it', () => {
    const { options, element } = setUp('');
    key(element, 'ArrowUp');
    expect(options.announce).toHaveBeenCalledWith(t('placing.nothing'));
  });

  it('says words of the page in their own language where that is not the widget\'s', () => {
    vi.useFakeTimers();
    const { options, element } = setUp();
    options.describe.mockReturnValueOnce({ text: 'Annual report $& figures', lang: 'en' });
    key(element, 'ArrowUp');
    const [said] = options.announce.mock.calls[0]! as [Array<string | HTMLElement>];
    const words = said[1] as HTMLElement;
    expect([said[0], words.localName, words.lang, words.textContent, said[2]]).toEqual([
      'Onder het kruis: ',
      'span',
      'en',
      'Annual report $& figures',
      '',
    ]);
    // The same words again are no news, in whatever language.
    vi.advanceTimersByTime(ANNOUNCE_EVERY_MS);
    options.describe.mockReturnValueOnce({ text: 'Annual report $& figures', lang: 'en' });
    key(element, 'ArrowUp');
    expect(options.announce).toHaveBeenCalledOnce();
  });

  it('leaves other keys, and keys on its own button, alone', () => {
    const { options, element } = setUp();
    expect(key(element, 'a').defaultPrevented).toBe(false);
    expect(key(element.querySelector('button')!, 'Enter').defaultPrevented).toBe(false);
    expect(options.place).not.toHaveBeenCalled();
  });

  it('drops a waiting announcement when it goes away', () => {
    vi.useFakeTimers();
    const { options, overlay, element } = setUp();
    options.describe.mockReturnValueOnce({ text: 'Eerste', lang: null }).mockReturnValueOnce({ text: 'Tweede', lang: null });
    key(element, 'ArrowUp');
    key(element, 'ArrowUp');
    overlay.dispose();
    vi.advanceTimersByTime(ANNOUNCE_EVERY_MS * 2);
    expect(options.announce).toHaveBeenCalledOnce();
  });
});

describe('drawing a frame', () => {
  // A capture of 1000 by 600. Below the bar, the stage is 1000 by 300 from
  // y 120: the capture fits at half its size, 500 by 300, centred from x 250.
  const STAGE: Box = { left: 0, top: 120, width: 1000, height: 300 };

  function setUp(mode: 'region' | 'cover' = 'region', stage: Box = STAGE, keyboard = false) {
    const picture = document.createElement('canvas') as HTMLElement;
    const options = {
      t,
      mode,
      keyboard,
      picture,
      bounds: { width: 1000, height: 600 },
      box: vi.fn((_element: Element) => stage),
      announce: vi.fn(),
      done: vi.fn(),
      stop: vi.fn(),
    };
    const overlay = frameOverlay(options);
    document.body.append(overlay.element);
    overlay.layout();
    const element = overlay.element;
    const frame = element.querySelector<HTMLElement>('.frame')!;
    const corner = element.querySelector<HTMLElement>('.corner')!;
    const stageElement = element.querySelector<HTMLElement>('.stage')!;
    return { options, overlay, element, frame, corner, picture, stage: stageElement };
  }

  const styleOf = (element: HTMLElement) =>
    [element.style.left, element.style.top, element.style.width, element.style.height].join(' ');

  it('shows the frozen screenshot below its bar with a frame in the middle, labelled for its purpose', () => {
    const region = setUp('region');
    expect(region.stage.contains(region.picture)).toBe(true);
    expect(Array.from(region.element.children, (child) => child.className)).toEqual(['bar', 'stage']);
    expect(region.element.getAttribute('aria-label')).toBe('Kies het deel van de schermafbeelding dat je wilt versturen');
    barOf(region.element, 'frame.stop', t('region.bar'), t('rect.keys'));
    expect(region.options.box).toHaveBeenCalledWith(region.stage);
    region.overlay.element.remove();
    const cover = setUp('cover');
    expect(cover.element.getAttribute('aria-label')).toBe('Kies wat je wilt afdekken');
    barOf(cover.element, 'frame.stop', t('cover.bar'), t('rect.keys'));
    expect(t('rect.keys')).toContain('Shift');
  });

  it('fits the screenshot whole into the stage, centred, and draws the frame on it at that scale', () => {
    const { picture, frame } = setUp();
    expect(styleOf(picture)).toBe('250px 0px 500px 300px');
    // The frame in the middle of the capture: 250, 150, 500 by 300 there.
    expect(styleOf(frame)).toBe('375px 75px 250px 150px');
    const tall = setUp('region', { left: 0, top: 120, width: 400, height: 600 });
    expect(styleOf(tall.picture)).toBe('0px 180px 400px 240px');
  });

  it('keeps Tab on itself and its "Cancel" button: the page under it is out of reach', () => {
    const { element } = setUp();
    holdsFocus(element, 'frame.stop');
  });

  it('leaves its bar where it is on a press: no page lies below it', () => {
    const { element } = setUp();
    pointer(element.querySelector('.bar p')!, 'click', 10, 10);
    expect(element.querySelector('.bar')!.classList.contains('bottom')).toBe(false);
  });

  it('fits the screenshot again when the keyboard line shows at the first key, and keeps it there', () => {
    expect(setUp('region', STAGE, true).element.classList.contains('keyboard')).toBe(true);
    const { element, options, picture, frame } = setUp();
    expect(element.classList.contains('keyboard')).toBe(false);
    // The bar grew by the keyboard line: the stage below it is lower.
    options.box.mockReturnValue({ left: 0, top: 150, width: 1000, height: 240 });
    key(element, 'ArrowUp');
    expect(element.classList.contains('keyboard')).toBe(true);
    expect(styleOf(picture)).toBe('300px 0px 400px 240px');
    // The frame, one step up in the capture, on the picture as it is now.
    expect(styleOf(frame)).toBe('400px 56px 200px 120px');
    options.box.mockClear();
    key(element, 'ArrowUp');
    expect(options.box).not.toHaveBeenCalled();
  });

  it('takes a frame dragged with the pointer, in px of the capture', () => {
    const { options, element, frame, stage } = setUp();
    // Client (300, 170) is capture (100, 100): (300 - 250) / 0.5, (170 - 120) / 0.5.
    expect(pointer(stage, 'pointerdown', 300, 170).defaultPrevented).toBe(true);
    pointer(element, 'pointermove', 301, 170.5);
    expect(styleOf(frame)).toBe('375px 75px 250px 150px');
    pointer(element, 'pointermove', 400, 245);
    expect(styleOf(frame)).toBe('300px 50px 100px 75px');
    pointer(element, 'pointerup', 410.3, 250.1);
    expect(options.done).toHaveBeenCalledWith({ x: 100, y: 100, width: 221, height: 160 });
  });

  it('ignores a drag too small to be a frame', () => {
    const { options, element, stage } = setUp();
    pointer(stage, 'pointerdown', 300, 170);
    pointer(element, 'pointermove', 320, 172.5);
    pointer(element, 'pointerup', 320, 172.5);
    expect(options.done).not.toHaveBeenCalled();
  });

  it('takes a frame from two clicks, marking the first corner, also past the edge of the picture', () => {
    const { options, element, corner, picture } = setUp();
    expect(corner.hidden).toBe(true);
    // On the picture itself, which lets pointer events through to the stage.
    pointer(picture, 'pointerdown', 450, 270);
    pointer(element, 'pointerup', 450, 270);
    expect(corner.hidden).toBe(false);
    expect([corner.style.left, corner.style.top]).toEqual(['450px', '150px']);
    expect(options.done).not.toHaveBeenCalled();
    pointer(picture, 'pointerdown', 275, 2000);
    pointer(element, 'pointerup', 275, 2000);
    expect(corner.hidden).toBe(true);
    expect(options.done).toHaveBeenCalledWith({ x: 50, y: 300, width: 350, height: 300 });
  });

  it('puts the frame and a first corner back on the picture after the window changes', () => {
    const { overlay, options, element, frame, corner, stage, picture } = setUp();
    pointer(stage, 'pointerdown', 450, 270);
    pointer(element, 'pointerup', 450, 270);
    options.box.mockReturnValue({ left: 0, top: 60, width: 1000, height: 600 });
    overlay.layout();
    expect(styleOf(picture)).toBe('0px 0px 1000px 600px');
    expect(styleOf(frame)).toBe('250px 150px 500px 300px');
    expect([corner.style.left, corner.style.top]).toEqual(['400px', '300px']);
  });

  it('ignores the pointer on its bar and beside the picture, and moves and releases without a press', () => {
    const { options, element, frame } = setUp();
    pointer(element.querySelector('.bar p')!, 'pointerdown', 10, 10);
    pointer(element, 'pointerdown', 500, 5);
    pointer(element, 'pointermove', 400, 400);
    pointer(element, 'pointerup', 400, 400);
    expect(options.done).not.toHaveBeenCalled();
    expect(styleOf(frame)).toBe('375px 75px 250px 150px');
    element.querySelector<HTMLButtonElement>('[data-key="frame.stop"]')!.click();
    expect(options.stop).toHaveBeenCalledOnce();
  });

  it('moves with the arrows in px of the capture, resizes with Shift, says the size, and confirms with Enter', () => {
    vi.useFakeTimers();
    const { options, element, frame } = setUp();
    expect(key(element, 'ArrowLeft').defaultPrevented).toBe(true);
    expect(options.announce).toHaveBeenLastCalledWith('Kader van 500 bij 300 pixels.');
    expect(styleOf(frame)).toBe('370px 75px 250px 150px');
    key(element, 'ArrowRight', true);
    expect(styleOf(frame)).toBe('370px 75px 255px 150px');
    vi.advanceTimersByTime(ANNOUNCE_EVERY_MS);
    expect(options.announce).toHaveBeenLastCalledWith('Kader van 510 bij 300 pixels.');
    expect(key(element, 'Escape').defaultPrevented).toBe(false);
    expect(key(element.querySelector('button')!, 'Enter').defaultPrevented).toBe(false);
    key(element, 'Enter');
    expect(options.done).toHaveBeenCalledWith({ x: 240, y: 150, width: 510, height: 300 });
  });

  it('does not repeat the size while the frame only moves', () => {
    vi.useFakeTimers();
    const { options, element } = setUp();
    key(element, 'ArrowLeft');
    vi.advanceTimersByTime(ANNOUNCE_EVERY_MS);
    key(element, 'ArrowLeft');
    vi.advanceTimersByTime(ANNOUNCE_EVERY_MS);
    expect(options.announce).toHaveBeenCalledOnce();
  });

  it('drops a waiting announcement when it goes away', () => {
    vi.useFakeTimers();
    const { options, overlay, element } = setUp();
    key(element, 'ArrowLeft');
    key(element, 'ArrowDown', true);
    overlay.dispose();
    vi.advanceTimersByTime(ANNOUNCE_EVERY_MS * 2);
    expect(options.announce).toHaveBeenCalledOnce();
  });
});

describe('drawing an area on the live page', () => {
  // A window of 1000 by 600, all of it stage: the frame is in its px.
  const WINDOW: Box = { left: 0, top: 0, width: 1000, height: 600 };

  function setUp(keyboard = false) {
    const options = {
      t,
      mode: 'area' as const,
      keyboard,
      picture: null,
      bounds: { width: 1000, height: 600 },
      box: vi.fn((_element: Element) => WINDOW),
      announce: vi.fn(),
      done: vi.fn(),
      stop: vi.fn(),
    };
    const overlay = frameOverlay(options);
    document.body.append(overlay.element);
    overlay.layout();
    const element = overlay.element;
    const bar = element.querySelector<HTMLElement>('.bar')!;
    const stage = element.querySelector<HTMLElement>('.stage')!;
    const frame = element.querySelector<HTMLElement>('.frame')!;
    return { options, overlay, element, bar, stage, frame };
  }

  const styleOf = (element: HTMLElement) =>
    [element.style.left, element.style.top, element.style.width, element.style.height].join(' ');

  it('lies over the live page, with its bar floating over the stage and the texts of an area', () => {
    const { element, stage, frame } = setUp();
    expect(element.className).toBe('overlay live');
    expect(Array.from(element.children, (child) => child.className)).toEqual(['stage', 'bar']);
    expect(Array.from(stage.children, (child) => child.className)).toEqual(['frame', 'corner']);
    expect(element.getAttribute('aria-label')).toBe('Wijs een gebied aan');
    barOf(element, 'frame.stop', t('area.bar'), t('rect.keys'));
    expect(element.querySelector('[data-key="frame.stop"]')!.textContent).toBe('Terug');
    // The frame starts in the middle of the window, at its own size.
    expect(styleOf(frame)).toBe('250px 150px 500px 300px');
    expect(STYLES).toContain('.live .stage { position: absolute; top: 0; right: 0; bottom: 0; left: 0; }');
  });

  it('keeps Tab on itself and its "Back" button', () => {
    const { element } = setUp(true);
    expect(element.classList.contains('keyboard')).toBe(true);
    holdsFocus(element, 'frame.stop');
  });

  it('takes an area from two clicks, in px of the window', () => {
    const { options, element, stage } = setUp();
    pointer(stage, 'pointerdown', 100, 50);
    pointer(element, 'pointerup', 100, 50);
    expect(options.done).not.toHaveBeenCalled();
    pointer(stage, 'pointerdown', 400, 250);
    pointer(element, 'pointerup', 400, 250);
    expect(options.done).toHaveBeenCalledWith({ x: 100, y: 50, width: 300, height: 200 });
  });

  it('takes a dragged area, and one from the arrow keys and Enter', () => {
    const { options, element, stage } = setUp();
    pointer(stage, 'pointerdown', 100, 50);
    pointer(element, 'pointermove', 300, 150);
    pointer(element, 'pointerup', 300, 150);
    expect(options.done).toHaveBeenLastCalledWith({ x: 100, y: 50, width: 200, height: 100 });
    key(element, 'ArrowDown');
    key(element, 'ArrowLeft', true);
    key(element, 'Enter');
    // The keys go on from the frame the drag left.
    expect(options.done).toHaveBeenLastCalledWith({ x: 100, y: 60, width: 190, height: 100 });
  });

  it('moves its bar to the other edge when that is pressed, but not for its button', () => {
    const { options, element, bar } = setUp();
    pointer(bar.querySelector('.help')!, 'click', 300, 30);
    expect(bar.classList.contains('bottom')).toBe(true);
    pointer(bar, 'click', 300, 560);
    expect(bar.classList.contains('bottom')).toBe(false);
    element.querySelector<HTMLButtonElement>('[data-key="frame.stop"]')!.click();
    expect(options.stop).toHaveBeenCalledOnce();
    expect(bar.classList.contains('bottom')).toBe(false);
  });
});

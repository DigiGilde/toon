import { afterEach, describe, expect, it, vi } from 'vitest';

import { translator } from './i18n';
import { ANNOUNCE_EVERY_MS, BAR_CLEARANCE, frameOverlay, placingOverlay } from './overlay';
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

/** Tab and Shift+Tab, from the overlay and from its button, stay on the two. */
function holdsFocus(element: HTMLElement, stopKey: string): void {
  const stop = element.querySelector<HTMLElement>(`[data-key="${stopKey}"]`)!;
  element.focus();
  expect(key(element, 'Tab').defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(stop);
  expect(key(stop, 'Tab').defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(element);
  expect(key(element, 'Tab', true).defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(stop);
  expect(key(stop, 'Tab', true).defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(element);
}

/** The bar's way out first, then the line for the pointer and the line for
 * the keyboard, which together describe the overlay. */
function barOf(element: HTMLElement, stopKey: string, pointer: string, keys: string): void {
  const bar = element.querySelector('.bar')!;
  const [first, help, keyboard] = Array.from(bar.children);
  expect(first!.getAttribute('data-key')).toBe(stopKey);
  expect([help!.className, help!.textContent]).toEqual(['help', pointer]);
  expect([keyboard!.className, keyboard!.textContent]).toEqual(['keys', keys]);
  expect(element.getAttribute('aria-describedby')).toBe(`${help!.id} ${keyboard!.id}`);
  expect(help!.id).not.toBe('');
  expect(keyboard!.id).not.toBe('');
}

function pointer(target: Element, type: string, clientX: number, clientY: number): MouseEvent {
  const event = new MouseEvent(type, { clientX, clientY, bubbles: true, cancelable: true });
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
      stop: vi.fn(),
    };
    const overlay = placingOverlay(options);
    document.body.append(overlay.element);
    const crosshair = overlay.element.querySelector<HTMLElement>('.crosshair')!;
    const bar = overlay.element.querySelector<HTMLElement>('.bar')!;
    const atBottom = () => bar.classList.contains('bottom');
    return { options, overlay, element: overlay.element, crosshair, bar, atBottom };
  }

  it('is a labelled, focusable application area with the crosshair in the middle', () => {
    const { element, crosshair } = setUp();
    expect(element.getAttribute('role')).toBe('application');
    expect(element.getAttribute('aria-label')).toBe('Wijs een punt aan');
    expect(element.getAttribute('tabindex')).toBe('-1');
    expect(element.dataset.key).toBe('overlay');
    expect([crosshair.style.left, crosshair.style.top]).toEqual(['500px', '300px']);
  });

  it('puts its way out first in the bar, then how to point with a pointer and with the keyboard', () => {
    const { element } = setUp();
    barOf(element, 'placing.stop', t('placing.bar'), t('placing.keys'));
    expect(t('placing.keys')).toContain('Pijltjes');
  });

  it('keeps Tab on itself and its Stop button: the page under it is out of reach', () => {
    const { element } = setUp();
    holdsFocus(element, 'placing.stop');
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
    // The bar grew: kept clear of the crosshair again.
    expect(options.box).toHaveBeenCalledWith(bar);
    options.box.mockClear();
    key(element, 'a');
    expect(options.box).not.toHaveBeenCalled();
  });

  it('places where the pointer clicks, and moves its bar to the other edge when that is pressed', () => {
    const { options, element, bar, atBottom } = setUp();
    pointer(element, 'click', 120, 340);
    expect(options.place).toHaveBeenCalledWith({ x: 120, y: 340 });
    // On touch nothing else moves the bar off what lies under it.
    pointer(bar.querySelector('.help')!, 'click', 300, 30);
    expect(atBottom()).toBe(true);
    pointer(bar, 'click', 300, 560);
    expect(atBottom()).toBe(false);
    expect(options.place).toHaveBeenCalledOnce();
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
    for (let step = 0; step < 8; step += 1) key(element, 'ArrowLeft', true);
    for (let step = 0; step < 6; step += 1) key(element, 'ArrowUp', true);
    // At (100, 0): left of the bar, which starts at 180 less its 20 px clearance.
    expect(atBottom()).toBe(false);
  });

  it('moves its bar off the crosshair as it shows, and after the window changes', () => {
    // A low window: the bar at the top reaches the crosshair in the middle.
    const { overlay, atBottom, options } = setUp('x', { width: 1000, height: 200 });
    overlay.layout();
    expect(atBottom()).toBe(true);
    expect(options.box).toHaveBeenCalledWith(overlay.element.querySelector('.bar'));
    const { overlay: roomy, atBottom: roomyAtBottom } = setUp();
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
    expect(options.describe).toHaveBeenLastCalledWith({ x: 500, y: 270 });
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
    const picture = document.createElement('canvas');
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

  it('keeps Tab on itself and its Annuleren button: the page under it is out of reach', () => {
    const { element } = setUp();
    holdsFocus(element, 'frame.stop');
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

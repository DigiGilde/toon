import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  CAPTURE_TIMEOUT_MS,
  CaptureTimeout,
  captureScale,
  cssUrls,
  drawPlan,
  encodeUnderLimit,
  IMAGE_REQUEST,
  imageWillFail,
  markEdges,
  needsDoubleCapture,
  overflowToShow,
  pageBackground,
  PLACEHOLDER_IMAGE,
  RENDER_TIMEOUT_MS,
  SCREENSHOT_LIMIT,
  Shot,
  withTimeout,
  withVisibleOverflow,
} from './screenshot';
import { fakePlatform } from './testPlatform';
import type { Capture } from './types';

afterEach(() => {
  vi.useRealTimers();
});

const CAPTURE: Capture = { image: 'capture', width: 1000, height: 500, scale: 2, placeholders: false };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

const blob = (size: number) => new Blob([new Uint8Array(size)], { type: 'image/jpeg' });

describe('withTimeout', () => {
  it('passes a result or an error through', async () => {
    await expect(withTimeout(Promise.resolve(1), 10)).resolves.toBe(1);
    await expect(withTimeout(Promise.reject(new Error('boom')), 10)).rejects.toThrow('boom');
  });

  it('gives up after the time', async () => {
    vi.useFakeTimers();
    const waiting = withTimeout(new Promise(() => {}), CAPTURE_TIMEOUT_MS);
    vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS);
    await expect(waiting).rejects.toBeInstanceOf(CaptureTimeout);
  });
});

describe('needsDoubleCapture', () => {
  it.each([
    ['Safari on macOS', 'Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Safari/605.1.15', true],
    ['Chrome on iOS, which is WebKit too', 'Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/150.0 Mobile/15E148 Safari/604.1', true],
    ['Chrome', 'Mozilla/5.0 (Macintosh) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0 Safari/537.36', false],
    ['Edge', 'Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0 Safari/537.36 Edg/150.0', false],
    ['Chrome on Android', 'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0 Mobile Safari/537.36', false],
    ['Firefox', 'Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0', false],
  ])('%s: %s', (_name, userAgent, expected) => {
    expect(needsDoubleCapture(userAgent)).toBe(expected);
  });
});

describe('captureScale', () => {
  it('follows the device up to 2, never below 1, and never past 4096 px a side', () => {
    expect(captureScale(1280, 800, 2)).toBe(2);
    expect(captureScale(1280, 800, 3)).toBe(2);
    expect(captureScale(1280, 800, 0.5)).toBe(1);
    expect(captureScale(1280, 800, 0)).toBe(1);
    expect(captureScale(3000, 1000, 2)).toBeCloseTo(4096 / 3000);
    expect(captureScale(0, 0, 1)).toBe(1);
  });
});

describe('imageWillFail', () => {
  const base = 'https://content.test/team/site/';

  it('reads data and blob URLs everywhere', () => {
    expect(imageWillFail(PLACEHOLDER_IMAGE, base, true)).toBe(false);
    expect(imageWillFail('blob:https://content.test/x', base, true)).toBe(false);
  });

  it('reads this origin only when the page has one of its own', () => {
    expect(imageWillFail('logo.png', base, false)).toBe(false);
    expect(imageWillFail('logo.png', base, true)).toBe(true);
  });

  it('never reads another origin, or what is not a URL', () => {
    expect(imageWillFail('https://cdn.test/logo.png', base, false)).toBe(true);
    expect(imageWillFail('http://[', base, false)).toBe(true);
  });
});

describe('how the capture asks for the images of the page', () => {
  it("asks the page's own origin only, so a shielded page, which has none, sends no request without the cookie", () => {
    expect(IMAGE_REQUEST).toEqual({ credentials: 'same-origin', mode: 'same-origin' });
  });
});

describe('cssUrls', () => {
  it('finds every url() in a value, quoted or not', () => {
    expect(cssUrls('url("a.png"), linear-gradient(red, blue), url(b.png), url( \'c.png\' )')).toEqual([
      'a.png',
      'b.png',
      'c.png',
    ]);
    expect(cssUrls('none')).toEqual([]);
    expect(cssUrls('url("")')).toEqual([]);
  });
});

describe('pageBackground', () => {
  it('takes the body, then the root, then white', () => {
    expect(pageBackground('rgb(1, 2, 3)', 'rgb(4, 5, 6)')).toBe('rgb(4, 5, 6)');
    expect(pageBackground('rgb(1, 2, 3)', 'rgba(0, 0, 0, 0)')).toBe('rgb(1, 2, 3)');
    expect(pageBackground('transparent', '')).toBe('#ffffff');
  });
});

describe('the overflow the copy of the page lets show', () => {
  const visible = { overflowX: 'visible', overflowY: 'visible' };
  const sideways = { overflowX: 'hidden', overflowY: 'auto' };
  const PREFIX = 'data:image/svg+xml;charset=utf-8,';
  const XHTML = 'http://www.w3.org/1999/xhtml';

  /** The SVG as html-to-image makes it of a copy of the page, with the page's
   * own style elements, which come along into the copy. */
  function copyOf(body: string, css = 'body { overflow-x: hidden !important; }'): string {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 800 600');
    const object = document.createElementNS('http://www.w3.org/2000/svg', 'foreignObject');
    const html = document.createElementNS(XHTML, 'html');
    html.setAttribute('style', 'margin-top: -1200px; height: 600px; overflow-x: visible; overflow-y: visible;');
    const head = document.createElementNS(XHTML, 'head');
    const style = document.createElementNS(XHTML, 'style');
    style.textContent = `${css} p::after { content: "<body>"; }`;
    head.append(style);
    const main = document.createElementNS(XHTML, 'body');
    main.setAttribute('style', body);
    const paragraph = document.createElementNS(XHTML, 'p');
    paragraph.textContent = 'Alinea 20: <body> & meer';
    main.append(paragraph);
    html.append(head, main);
    object.append(html);
    svg.append(object);
    return `${PREFIX}${encodeURIComponent(new XMLSerializer().serializeToString(svg))}`;
  }

  function read(url: string): Document {
    expect(url.startsWith(PREFIX)).toBe(true);
    return new DOMParser().parseFromString(decodeURIComponent(url.slice(PREFIX.length)), 'image/svg+xml');
  }

  it("is the body's where the body has the window's overflow and does not show its own", () => {
    // body { overflow-x: hidden }, or html, body { height: 100% } with that.
    expect(overflowToShow(visible, sideways, copyOf(''))).toBe('body');
    expect(overflowToShow(visible, { overflowX: 'clip', overflowY: 'visible' }, copyOf(''))).toBe('body');
    expect(overflowToShow(visible, visible, copyOf(''))).toBeNull();
  });

  it("is the root's only where an important rule of the page's comes along, which would beat the copy's own style", () => {
    const calm = copyOf('', 'html { overflow-y: scroll; }');
    // html { overflow-y: scroll }: the copy's own style on its root does it.
    expect(overflowToShow({ overflowX: 'visible', overflowY: 'scroll' }, sideways, calm)).toBeNull();
    // html, body { overflow-x: hidden }: on the page, too, the body clips its own.
    expect(overflowToShow(sideways, sideways, calm)).toBeNull();
    for (const rule of ['!important', '! important', '!\n\timportant', '!IMPORTANT']) {
      const insisting = copyOf('', `html { overflow-y: scroll ${rule}; }`);
      expect(overflowToShow({ overflowX: 'visible', overflowY: 'scroll' }, sideways, insisting), rule).toBe('html');
    }
  });

  it.each(['body', 'html'] as const)('is set on the %s in the SVG of the copy, over the page\'s own rules, and nothing else changes', (name) => {
    const url = copyOf('height: 600px; overflow-x: hidden; overflow-y: auto;');
    const before = read(url);
    const after = read(withVisibleOverflow(url, name));
    const element = after.getElementsByTagNameNS(XHTML, name)[0] as HTMLElement;
    const kept = before.getElementsByTagNameNS(XHTML, name)[0] as HTMLElement;
    expect([element.style.getPropertyValue('overflow-x'), element.style.getPropertyValue('overflow-y')]).toEqual([
      'visible',
      'visible',
    ]);
    expect([element.style.getPropertyPriority('overflow-x'), element.style.getPropertyPriority('overflow-y')]).toEqual([
      'important',
      'important',
    ]);
    const others = (style: CSSStyleDeclaration) =>
      Array.from(style, (property) => `${property}: ${style.getPropertyValue(property)}`).filter(
        (declaration) => !declaration.startsWith('overflow'),
      );
    expect(others(element.style)).toEqual(others(kept.style));
    element.removeAttribute('style');
    kept.removeAttribute('style');
    expect(new XMLSerializer().serializeToString(after)).toBe(new XMLSerializer().serializeToString(before));
  });

  it('leaves an SVG alone that is not one, has no such element or does not read back', () => {
    const other = 'data:image/svg+xml;base64,PHN2Zy8+';
    expect(withVisibleOverflow(other, 'body')).toBe(other);
    // Not in the form html-to-image hands its SVG over in, however it reads.
    const plain = copyOf('overflow-x: hidden;').replace(PREFIX, 'data:image/svg+xml;utf8,123456789');
    expect(withVisibleOverflow(plain, 'body')).toBe(plain);
    const bodiless = `${PREFIX}${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>')}`;
    expect(withVisibleOverflow(bodiless, 'body')).toBe(bodiless);
    expect(withVisibleOverflow(bodiless, 'html')).toBe(bodiless);
    const broken = copyOf('overflow-x: hidden;').replace(encodeURIComponent('</svg>'), '');
    expect(withVisibleOverflow(broken, 'body')).toBe(broken);
  });

  it('leaves it alone where a browser reads it back in part, with a parse error beside the body', () => {
    // So Chromium does: what it read so far, and the error. jsdom gives the
    // error alone.
    const url = copyOf('overflow-x: hidden;');
    const partial = read(url);
    partial.documentElement.append(partial.createElementNS(XHTML, 'parsererror'));
    const parse = vi.spyOn(DOMParser.prototype, 'parseFromString').mockReturnValue(partial);
    expect(withVisibleOverflow(url, 'body')).toBe(url);
    parse.mockRestore();
  });
});

describe('the mark of a selected passage in the screenshot', () => {
  it('lies around each box in two tones, white against it and dark around that, never over the text', () => {
    // A line of 100 by 18 px at 10, 20, on a capture of two pixels a px:
    // 20, 40, 200 by 36 in the picture.
    const [white, dark] = markEdges({ x: 10, y: 20, width: 100, height: 18 }, 2);
    expect(white).toEqual({ color: '#ffffff', width: 4, rect: { x: 18, y: 38, width: 204, height: 40 } });
    expect(dark).toEqual({ color: '#1b1f24', width: 4, rect: { x: 14, y: 34, width: 212, height: 48 } });
    // A stroke is centred on its path: the white band runs from the box's
    // edge 4 pixels out, and the dark one 4 pixels on from there.
    const band = (edge: typeof white) => ({
      inner: [edge.rect.x + edge.width / 2, edge.rect.y + edge.width / 2],
      outer: [edge.rect.x - edge.width / 2, edge.rect.y - edge.width / 2],
    });
    expect(band(white)).toEqual({ inner: [20, 40], outer: [16, 36] });
    expect(band(dark)).toEqual({ inner: [16, 36], outer: [12, 32] });
  });
});

describe('drawPlan', () => {
  it('keeps the whole viewport at its pixel size when it fits', () => {
    expect(drawPlan({ ...CAPTURE, scale: 1 }, [], [], null)).toEqual({
      marks: [],
      covers: [],
      source: { x: 0, y: 0, width: 1000, height: 500 },
      target: { width: 1000, height: 500 },
    });
  });

  it('scales the longest side down to 2000 px', () => {
    expect(drawPlan(CAPTURE, [], [], null).target).toEqual({ width: 2000, height: 1000 });
    expect(drawPlan({ ...CAPTURE, width: 1500, height: 3000 }, [], [], null).target).toEqual({
      width: 1000,
      height: 2000,
    });
  });

  it('crops to the chosen part and never goes below one pixel', () => {
    const region = { x: 10, y: 20, width: 100, height: 0.1 };
    const plan = drawPlan(CAPTURE, [{ type: 'pin', x: 1, y: 2 }], [{ x: 0, y: 0, width: 5, height: 5 }], region);
    expect(plan.source).toBe(region);
    expect(plan.target).toEqual({ width: 200, height: 1 });
    expect(plan.marks).toHaveLength(1);
    expect(plan.covers).toHaveLength(1);
  });
});

describe('encodeUnderLimit', () => {
  it('tries 0.8, then 0.6, then 0.4, and keeps the first under the limit', async () => {
    const encode = vi.fn().mockResolvedValueOnce(blob(30)).mockResolvedValueOnce(blob(10));
    expect((await encodeUnderLimit(encode, 20))!.size).toBe(10);
    expect(encode.mock.calls.map(([quality]) => quality)).toEqual([0.8, 0.6]);
  });

  it('gives nothing when even 0.4 is too large, or when the browser cannot encode', async () => {
    expect(await encodeUnderLimit(vi.fn().mockResolvedValue(blob(30)), 20)).toBeNull();
    expect(await encodeUnderLimit(vi.fn().mockResolvedValue(null), 20)).toBeNull();
  });
});

describe('the composer screenshot', () => {
  function setUp(limit = SCREENSHOT_LIMIT) {
    const platform = fakePlatform();
    platform.capture.mockResolvedValue(CAPTURE);
    const changed = vi.fn();
    const shot = new Shot(platform, document.createElement('div'), changed, limit);
    return { platform, changed, shot };
  }

  it('is taken, drawn with its marks and shown before it can be sent', async () => {
    const { platform, changed, shot } = setUp();
    expect([shot.state, shot.attachment, shot.bounds, shot.placeholders]).toEqual(['none', null, null, false]);
    const pending = shot.take([{ type: 'pin', x: 1, y: 2 }]);
    expect(shot.state).toBe('capturing');
    await pending;
    expect(platform.capture).toHaveBeenCalledOnce();
    expect(platform.draw).toHaveBeenCalledWith(CAPTURE, expect.objectContaining({ marks: [{ type: 'pin', x: 1, y: 2 }] }));
    expect(shot.state).toBe('ready');
    expect(shot.url).toBe('blob:screenshot');
    expect(shot.attachment!.file).toBeInstanceOf(Blob);
    expect(shot.attachment!.region).toBeNull();
    expect(shot.bounds).toEqual({ width: 1000, height: 500 });
    expect(shot.rendering).toBe(false);
    expect(changed.mock.calls.length).toBe(2);
  });

  it('is taken twice on WebKit, keeping the second', async () => {
    const { platform, shot } = setUp();
    platform.userAgent.mockReturnValue('Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 Version/19.0 Mobile Safari/604.1');
    platform.capture.mockResolvedValueOnce({ ...CAPTURE, placeholders: false }).mockResolvedValueOnce({ ...CAPTURE, placeholders: true });
    await shot.take([]);
    expect(platform.capture).toHaveBeenCalledTimes(2);
    expect(shot.placeholders).toBe(true);
  });

  it('fails when the capture fails or takes too long', async () => {
    const { platform, shot } = setUp();
    platform.capture.mockRejectedValueOnce(new Error('tainted'));
    await shot.take([]);
    expect([shot.state, shot.attachment]).toEqual(['failed', null]);

    vi.useFakeTimers();
    platform.capture.mockReturnValueOnce(new Promise(() => {}));
    const pending = shot.take([]);
    vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS);
    await pending;
    expect(shot.state).toBe('failed');
  });

  it('fails when drawing or encoding throws', async () => {
    const { platform, shot } = setUp();
    await shot.take([]);
    platform.encode.mockRejectedValueOnce(new Error('out of memory'));
    await shot.cover({ x: 0, y: 0, width: 10, height: 10 });
    expect([shot.state, shot.url, shot.attachment]).toEqual(['failed', null, null]);
    expect(platform.revokeObjectUrl).toHaveBeenCalledWith('blob:screenshot');
  });

  it('is too large when no quality fits', async () => {
    const { platform, shot } = setUp();
    platform.encode.mockResolvedValue(blob(SCREENSHOT_LIMIT + 1));
    await shot.take([]);
    expect([shot.state, shot.attachment, shot.url]).toEqual(['tooLarge', null, null]);
  });

  it('holds to the limit it was given, trying a lower quality before giving up', async () => {
    const { platform, shot } = setUp(1000);
    platform.encode.mockResolvedValueOnce(blob(1500)).mockResolvedValueOnce(blob(1000));
    await shot.take([]);
    expect(platform.encode.mock.calls.map(([, quality]) => quality)).toEqual([0.8, 0.6]);
    expect([shot.state, shot.attachment!.file.size]).toEqual(['ready', 1000]);

    platform.encode.mockResolvedValue(blob(1001));
    await shot.cover({ x: 0, y: 0, width: 10, height: 10 });
    expect(shot.state).toBe('tooLarge');
  });

  it('keeps only the latest capture', async () => {
    const { platform, shot } = setUp();
    const first = deferred<Capture>();
    platform.capture.mockReturnValueOnce(first.promise);
    const older = shot.take([{ type: 'pin', x: 1, y: 1 }]);
    const newer = shot.take([]);
    await newer;
    first.resolve({ ...CAPTURE, width: 1 });
    await older;
    expect(shot.bounds).toEqual({ width: 1000, height: 500 });

    const failing = deferred<Capture>();
    platform.capture.mockReturnValueOnce(failing.promise);
    const stale = shot.take([]);
    shot.exclude();
    failing.reject(new Error('late'));
    await stale;
    expect(shot.state).toBe('excluded');
  });

  it('drops a render that a newer one overtook', async () => {
    const { platform, shot } = setUp();
    await shot.take([]);
    const slow = deferred<Blob | null>();
    platform.encode.mockReturnValueOnce(slow.promise);
    const older = shot.choose({ x: 0, y: 0, width: 100, height: 100 });
    await shot.cover({ x: 1, y: 1, width: 20, height: 20 });
    slow.resolve(blob(1));
    await older;
    expect(platform.objectUrl).toHaveBeenCalledTimes(2);

    const failing = deferred<Blob | null>();
    platform.encode.mockReturnValueOnce(failing.promise);
    const broken = shot.uncover();
    shot.exclude();
    failing.reject(new Error('late'));
    await broken;
    expect(shot.state).toBe('excluded');
  });

  it('crops to a chosen part, and sends that part along', async () => {
    const { platform, shot } = setUp();
    await shot.take([]);
    await shot.choose({ x: 10.4, y: 20.6, width: 300, height: 200 });
    expect(platform.draw).toHaveBeenLastCalledWith(
      CAPTURE,
      expect.objectContaining({ source: { x: 10.4, y: 20.6, width: 300, height: 200 } }),
    );
    expect(shot.attachment!.region).toEqual({ x: 10, y: 21, width: 300, height: 200 });
  });

  it('keeps the picture on show, with its own part, while a new one is drawn', async () => {
    const { platform, changed, shot } = setUp();
    await shot.take([]);
    const shown = shot.attachment;
    const drawing = deferred<Blob | null>();
    platform.encode.mockReturnValueOnce(drawing.promise);
    const choosing = shot.choose({ x: 10, y: 20, width: 300, height: 200 });
    // Said at once, so the composer can show the redraw; the picture and its
    // part stay those of the earlier picture until the new one is there.
    expect([shot.rendering, shot.state]).toEqual([true, 'ready']);
    expect(changed).toHaveBeenCalledTimes(3);
    expect(shot.attachment).toBe(shown);
    expect(shot.attachment!.region).toBeNull();
    const cropped = blob(5);
    drawing.resolve(cropped);
    await choosing;
    expect(shot.rendering).toBe(false);
    expect(shot.attachment).toEqual({ file: cropped, region: { x: 10, y: 20, width: 300, height: 200 } });
  });

  it('says when the latest picture is there, waiting for one asked for meanwhile too', async () => {
    const { platform, shot } = setUp();
    await shot.take([]);
    await shot.settled();
    const first = deferred<Blob | null>();
    const second = deferred<Blob | null>();
    platform.encode.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    void shot.cover({ x: 0, y: 0, width: 10, height: 10 });
    const done = vi.fn();
    void shot.settled().then(done);
    void shot.uncover();
    first.resolve(blob(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(done).not.toHaveBeenCalled();
    const uncovered = blob(2);
    second.resolve(uncovered);
    await vi.waitFor(() => expect(done).toHaveBeenCalledOnce());
    expect(shot.attachment!.file).toBe(uncovered);
  });

  it('stops waiting for a picture that is left out while it is drawn', async () => {
    const { platform, shot } = setUp();
    await shot.take([]);
    const drawing = deferred<Blob | null>();
    platform.encode.mockReturnValueOnce(drawing.promise);
    void shot.cover({ x: 0, y: 0, width: 10, height: 10 });
    const waiting = shot.settled();
    shot.exclude();
    expect(shot.rendering).toBe(false);
    drawing.resolve(blob(1));
    await waiting;
    expect([shot.state, shot.attachment]).toEqual(['excluded', null]);
  });

  it('fails a picture whose encoding never ends', async () => {
    const { platform, shot } = setUp();
    await shot.take([]);
    vi.useFakeTimers();
    platform.encode.mockReturnValueOnce(new Promise(() => {}));
    const covering = shot.cover({ x: 0, y: 0, width: 10, height: 10 });
    await vi.advanceTimersByTimeAsync(RENDER_TIMEOUT_MS);
    await covering;
    expect([shot.state, shot.rendering, shot.attachment]).toEqual(['failed', false, null]);
  });

  it('covers parts and takes the covering away again', async () => {
    const { platform, shot } = setUp();
    await shot.take([]);
    await shot.cover({ x: 0, y: 0, width: 10, height: 10 });
    await shot.cover({ x: 20, y: 0, width: 10, height: 10 });
    expect(shot.covers).toHaveLength(2);
    expect(platform.draw).toHaveBeenLastCalledWith(CAPTURE, expect.objectContaining({ covers: shot.covers }));
    await shot.uncover();
    expect(shot.covers).toEqual([]);
    expect(platform.revokeObjectUrl).toHaveBeenCalledTimes(3);
  });

  it('can be left out and brought back, taking a new one when there was none', async () => {
    const { platform, shot } = setUp();
    shot.exclude();
    expect([shot.state, shot.attachment]).toEqual(['excluded', null]);
    await shot.include();
    expect(platform.capture).toHaveBeenCalledOnce();
    expect(shot.state).toBe('ready');

    shot.exclude();
    await shot.include();
    expect(platform.capture).toHaveBeenCalledOnce();
    expect(shot.state).toBe('ready');
  });

  it('shows the whole capture with its marks and covers for drawing a frame', async () => {
    const { platform, shot } = setUp();
    await shot.take([{ type: 'pin', x: 5, y: 5 }]);
    await shot.cover({ x: 1, y: 1, width: 10, height: 10 });
    const element = shot.view();
    expect(element).toBe(platform.show.mock.results[0]!.value);
    expect(platform.draw).toHaveBeenLastCalledWith(CAPTURE, {
      marks: [{ type: 'pin', x: 5, y: 5 }],
      covers: [{ x: 1, y: 1, width: 10, height: 10 }],
      source: { x: 0, y: 0, width: 1000, height: 500 },
      target: { width: 2000, height: 1000 },
    });
  });

  it('lets go of its picture when disposed, and ignores a capture that arrives later', async () => {
    const { platform, changed, shot } = setUp();
    await shot.take([]);
    const late = deferred<Capture>();
    platform.capture.mockReturnValueOnce(late.promise);
    const pending = shot.take([]);
    shot.dispose();
    expect(platform.revokeObjectUrl).toHaveBeenCalledWith('blob:screenshot');
    const calls = changed.mock.calls.length;
    late.resolve(CAPTURE);
    await pending;
    expect(changed.mock.calls.length).toBe(calls);
    shot.dispose();
    expect(platform.revokeObjectUrl).toHaveBeenCalledOnce();
  });
});

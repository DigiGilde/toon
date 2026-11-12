/**
 * The screenshot: what to capture, what to draw on it, how small to make it,
 * and the state of the one the composer shows. The drawing and encoding run
 * in the platform adapter; this module decides.
 *
 * A capture never blocks sending: it runs inside a timeout, a failure leaves
 * the composer with "send without", and a screenshot the giver has not seen
 * is never sent. A cover or a chosen part is drawn anew from the capture.
 * Until that picture is ready the earlier one stays, with the region it was
 * drawn with, and `settled()` says when the latest one is there.
 */
import { rounded } from './rect';
import type { Capture, DrawPlan, Mark, Platform, Rect } from './types';

/** The most the widget makes of one screenshot, also where the site allows
 * more: the widget API's default per-file limit (feedback_screenshot_max_bytes). */
export const SCREENSHOT_LIMIT = 2 * 1024 * 1024;
export const MAX_SIDE = 2000;
export const CAPTURE_TIMEOUT_MS = 6000;
/** Drawing and encoding one picture: past this it counts as failed. */
export const RENDER_TIMEOUT_MS = 6000;
export const QUALITIES: readonly number[] = [0.8, 0.6, 0.4];

/** What an image the capture cannot read is drawn as: a neutral grey pixel. */
export const PLACEHOLDER_IMAGE =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR42mM4AAAAwgDB/9Yt3AAAAABJRU5ErkJggg==';

/** How the capture asks for the page's images: from the page's own origin
 * only. A shielded page has none, so the browser sends no request for them:
 * one without the site's cookie would reach the gate as anonymous, which
 * writes a refusal with the visitor's IP address in the access log. */
export const IMAGE_REQUEST: RequestInit = { credentials: 'same-origin', mode: 'same-origin' };

export class CaptureTimeout extends Error {}

export function withTimeout<T>(promise: Promise<T>, milliseconds: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new CaptureTimeout('capture timed out')), milliseconds);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

/** WebKit (Safari, and every browser on iOS) draws images blank on the first
 * foreignObject render; the second one is right. */
export function needsDoubleCapture(userAgent: string): boolean {
  return /AppleWebKit/.test(userAgent) && !/Chrome|Chromium|Edg\/|OPR\/|Android/.test(userAgent);
}

/** Canvas pixels per CSS pixel: the device's ratio, at most 2, and never a
 * side above 4096 px. */
export function captureScale(width: number, height: number, devicePixelRatio: number): number {
  const ratio = Math.min(Math.max(devicePixelRatio || 1, 1), 2);
  return Math.min(ratio, 4096 / Math.max(width, height, 1));
}

/** Whether the capture can read an image at `url`: in an opaque origin (a
 * shielded site) nothing but data: and blob: URLs, elsewhere this origin. */
export function imageWillFail(url: string, base: string, opaque: boolean): boolean {
  let parsed: URL;
  try {
    parsed = new URL(url, base);
  } catch {
    return true;
  }
  if (parsed.protocol === 'data:' || parsed.protocol === 'blob:') return false;
  return opaque || parsed.origin !== new URL(base).origin;
}

/** The URLs in a computed `background-image` (or similar) value. */
export function cssUrls(value: string): string[] {
  const urls: string[] = [];
  for (const match of value.matchAll(/url\(\s*(?:"([^"]*)"|'([^']*)'|([^)\s]*))\s*\)/g)) {
    // The unquoted group always takes part when the quoted ones do not.
    const url = match[1] ?? match[2] ?? match[3]!;
    if (url.length > 0) urls.push(url);
  }
  return urls;
}

/** The colour behind the page: the body's or the root's, else white. A JPEG
 * has no transparency, and a transparent page would come out black. */
export function pageBackground(rootColor: string, bodyColor: string): string {
  const transparent = (color: string) =>
    color === '' || color === 'transparent' || /^rgba\(.*,\s*0\)$/.test(color);
  if (!transparent(bodyColor)) return bodyColor;
  if (!transparent(rootColor)) return rootColor;
  return '#ffffff';
}

/** An element's overflow on each axis, as computed. */
export interface Overflow {
  overflowX: string;
  overflowY: string;
}

/** An important declaration in the SVG of the copy, `!important` with any
 * whitespace between, as html-to-image encodes it. */
const IMPORTANT = /!(?:%(?:20|09|0A|0C|0D))*important/i;

/** The element of the copy of the page that has to let its overflow show
 * beyond what the copy's style on its root does, or null. The root's
 * overflow is the window's, and so is the body's while the root's is visible:
 * in the copy neither is the window, and either would clip the page, to one
 * window from its top where its height is the window's. So the body, which
 * that style does not reach, where its overflow is not visible. And the root
 * where the page's own style elements, which come along into the copy, hold
 * an important declaration that would win over that style. */
export function overflowToShow(root: Overflow, body: Overflow, url: string): 'html' | 'body' | null {
  const visible = (style: Overflow) => style.overflowX === 'visible' && style.overflowY === 'visible';
  if (!visible(root)) return IMPORTANT.test(url) ? 'html' : null;
  return visible(body) ? null : 'body';
}

/** How html-to-image hands over the SVG it draws the copy of the page from. */
const SVG_URL = 'data:image/svg+xml;charset=utf-8,';

/** The SVG of the copy with its root or its body letting its overflow show,
 * over what the page's own style elements in the copy say too. The SVG as it
 * was when it does not read back. */
export function withVisibleOverflow(url: string, element: 'html' | 'body'): string {
  if (!url.startsWith(SVG_URL)) return url;
  const svg = new DOMParser().parseFromString(decodeURIComponent(url.slice(SVG_URL.length)), 'image/svg+xml');
  const found = svg.getElementsByTagNameNS('http://www.w3.org/1999/xhtml', element)[0];
  if (found === undefined || svg.getElementsByTagName('parsererror').length > 0) return url;
  const style = (found as HTMLElement).style;
  for (const axis of ['overflow-x', 'overflow-y']) {
    // Out first: jsdom keeps the old priority of a value that is set again.
    style.removeProperty(axis);
    style.setProperty(axis, 'visible', 'important');
  }
  return SVG_URL + encodeURIComponent(new XMLSerializer().serializeToString(svg.documentElement));
}

/** Each of the two tones around a selected passage, in CSS px. */
const MARK_BAND = 2;

/** A rectangle to stroke on the screenshot, in canvas pixels. */
export interface Stroke {
  color: string;
  width: number;
  rect: Rect;
}

/** The two tones around one box of a selected passage, as the page marks
 * it: a white band against the box and a dark one around that, both
 * outside it, so no glyph of the passage is painted over. A stroke is
 * centred on its path, which runs through the middle of its band. */
export function markEdges(rect: Rect, scale: number): Stroke[] {
  const band = (from: number, color: string): Stroke => {
    const out = (from + MARK_BAND / 2) * scale;
    return {
      color,
      width: MARK_BAND * scale,
      rect: {
        x: rect.x * scale - out,
        y: rect.y * scale - out,
        width: rect.width * scale + 2 * out,
        height: rect.height * scale + 2 * out,
      },
    };
  };
  return [band(0, '#ffffff'), band(MARK_BAND, '#1b1f24')];
}

/** Where the marks, covers and crop go, and how large the result is. */
export function drawPlan(capture: Capture, marks: Mark[], covers: Rect[], region: Rect | null): DrawPlan {
  const source = region ?? { x: 0, y: 0, width: capture.width, height: capture.height };
  const pixelWidth = source.width * capture.scale;
  const pixelHeight = source.height * capture.scale;
  const factor = Math.min(1, MAX_SIDE / Math.max(pixelWidth, pixelHeight, 1));
  return {
    marks,
    covers,
    source,
    target: {
      width: Math.max(1, Math.round(pixelWidth * factor)),
      height: Math.max(1, Math.round(pixelHeight * factor)),
    },
  };
}

/** A JPEG under the limit: quality 0.8, then 0.6 and 0.4. WebP is never
 * assumed; a browser that cannot encode at all gives null. */
export async function encodeUnderLimit(
  encode: (quality: number) => Promise<Blob | null>,
  limit: number,
): Promise<Blob | null> {
  for (const quality of QUALITIES) {
    const blob = await encode(quality);
    if (blob === null) return null;
    if (blob.size <= limit) return blob;
  }
  return null;
}

export type ShotState = 'none' | 'capturing' | 'ready' | 'failed' | 'tooLarge' | 'excluded';

/** A picture as drawn: the file, and the chosen part it was drawn from. */
export interface Attachment {
  file: Blob;
  region: Rect | null;
}

/** The composer's screenshot: taking it, changing it, leaving it out. */
export class Shot {
  state: ShotState = 'none';
  url: string | null = null;
  region: Rect | null = null;
  covers: Rect[] = [];
  /** A cover, a part or the screenshot back is being drawn. The picture on
   * show is the earlier one until it is done. */
  rendering = false;
  private capture: Capture | null = null;
  private rendered: Attachment | null = null;
  private marks: Mark[] = [];
  private generation = 0;
  private pending: Promise<void> = Promise.resolve();

  constructor(
    private readonly platform: Platform,
    private readonly host: Element,
    private readonly changed: () => void,
    /** What the picture may weigh in bytes. */
    private readonly limit: number,
  ) {}

  get placeholders(): boolean {
    return this.capture?.placeholders ?? false;
  }

  /** What to send: only a picture the giver has seen and kept, with the part
   * it was drawn from, never a part asked for since. */
  get attachment(): Attachment | null {
    return this.state === 'ready' ? this.rendered : null;
  }

  get bounds(): { width: number; height: number } | null {
    return this.capture === null ? null : { width: this.capture.width, height: this.capture.height };
  }

  /** Done once no picture is being drawn: a cover or a part asked for while
   * waiting is waited for too. */
  async settled(): Promise<void> {
    while (this.rendering) await this.pending;
  }

  async take(marks: Mark[]): Promise<void> {
    const generation = ++this.generation;
    this.marks = marks;
    this.region = null;
    this.covers = [];
    this.rendering = false;
    this.setState('capturing');
    let capture: Capture;
    try {
      capture = await withTimeout(this.captureOnce(), CAPTURE_TIMEOUT_MS);
    } catch {
      if (generation === this.generation) this.setState('failed');
      return;
    }
    if (generation !== this.generation) return;
    this.capture = capture;
    await this.render(generation);
  }

  private async captureOnce(): Promise<Capture> {
    const first = await this.platform.capture(this.host);
    return needsDoubleCapture(this.platform.userAgent()) ? this.platform.capture(this.host) : first;
  }

  private async render(generation = ++this.generation): Promise<void> {
    const region = this.region;
    let blob: Blob | null;
    try {
      const capture = this.capture!;
      const picture = this.platform.draw(capture, drawPlan(capture, this.marks, this.covers, region));
      blob = await withTimeout(
        encodeUnderLimit((quality) => this.platform.encode(picture, quality), this.limit),
        RENDER_TIMEOUT_MS,
      );
    } catch {
      if (generation === this.generation) {
        this.dropUrl();
        this.rendered = null;
        this.rendering = false;
        this.setState('failed');
      }
      return;
    }
    if (generation !== this.generation) return;
    this.dropUrl();
    this.rendering = false;
    if (blob === null) {
      this.rendered = null;
      this.setState('tooLarge');
      return;
    }
    this.rendered = { file: blob, region: region === null ? null : rounded(region) };
    this.url = this.platform.objectUrl(blob);
    this.setState('ready');
  }

  /** Draw the picture anew from the capture, saying so first. */
  private rerender(): Promise<void> {
    this.rendering = true;
    this.changed();
    this.pending = this.render();
    return this.pending;
  }

  /** The whole capture with marks and covers, for the frame overlays. */
  view(): HTMLElement {
    const capture = this.capture!;
    const whole = { x: 0, y: 0, width: capture.width, height: capture.height };
    const plan: DrawPlan = {
      marks: this.marks,
      covers: this.covers,
      source: whole,
      target: { width: Math.round(capture.width * capture.scale), height: Math.round(capture.height * capture.scale) },
    };
    return this.platform.show(this.platform.draw(capture, plan));
  }

  choose(region: Rect): Promise<void> {
    this.region = region;
    return this.rerender();
  }

  cover(rect: Rect): Promise<void> {
    this.covers = [...this.covers, rect];
    return this.rerender();
  }

  uncover(): Promise<void> {
    this.covers = [];
    return this.rerender();
  }

  /** Leave the screenshot out; a capture or drawing still running is dropped. */
  exclude(): void {
    this.generation += 1;
    this.rendering = false;
    this.setState('excluded');
  }

  /** Back to the screenshot after "without": the one taken, or a new one. */
  include(): Promise<void> {
    return this.capture === null ? this.take(this.marks) : this.rerender();
  }

  dispose(): void {
    this.generation += 1;
    this.dropUrl();
  }

  private dropUrl(): void {
    if (this.url !== null) this.platform.revokeObjectUrl(this.url);
    this.url = null;
  }

  private setState(state: ShotState): void {
    this.state = state;
    this.changed();
  }
}

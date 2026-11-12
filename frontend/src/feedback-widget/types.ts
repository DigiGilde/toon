/**
 * Shapes shared by the widget's modules, and the contract of the platform
 * adapter: every browser call jsdom cannot make goes through `Platform`, so
 * the rest of the widget runs, and is measured, in vitest.
 */

export type Lang = 'nl' | 'en';

export type Identity = 'account' | 'name' | 'anonymous';

export type Kind = 'page' | 'point' | 'text';

export type Status = 'new' | 'seen' | 'resolved';

export interface Config {
  token: string;
  api: string;
  identity: Identity;
  account: boolean;
  accountUrl: string;
  stopUrl: string;
  returnTo: string;
  hideUrl: string;
  showUrl: string;
  privacyUrl: string;
  retentionDays: number;
  /** What one screenshot may weigh in bytes: the site's limit, never more
   * than the widget's own. */
  screenshotLimit: number;
}

export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Point {
  x: number;
  y: number;
}

export interface ViewportInfo {
  /** The layout viewport, what `innerWidth` and `innerHeight` say. */
  width: number;
  height: number;
  /** The visual viewport; differs while an on-screen keyboard is up. */
  visualHeight: number;
  visualOffsetTop: number;
  dpr: number;
  scrollX: number;
  scrollY: number;
  /** The scrollable size of the document. */
  docWidth: number;
  docHeight: number;
}

/** W3C Web Annotation selectors, exactly as the widget API takes them. */
export type Selector =
  | { type: 'TextQuoteSelector'; exact: string; prefix: string; suffix: string }
  | { type: 'TextPositionSelector'; start: number; end: number }
  | { type: 'CssSelector'; value: string };

export interface Anchor {
  selectors: Selector[];
  point: { x: number; y: number; pageX: number; pageY: number } | null;
}

/** The reduced highlight form: the #toon-feedback= fragment and MineItem.anchor. */
export interface Highlight {
  k: 'point' | 'text';
  s: string | null;
  x: number | null;
  y: number | null;
  px: number | null;
  py: number | null;
  q: string | null;
}

/** Words of the page, and their language where it is not the widget's. */
export interface PageWords {
  text: string;
  lang: string | null;
}

/** A part of the page that scrolls on its own, as measured: its client box
 * in the window's coordinates, how far it is scrolled, and the least and the
 * most it can be scrolled to. On an axis the visitor cannot scroll it in,
 * both are where it is. */
export interface Scroller {
  element: Element;
  box: Box;
  scroll: Point;
  min: Point;
  max: Point;
}

/** A mark drawn on the screenshot, in CSS px of the captured viewport. */
export type Mark = { type: 'pin'; x: number; y: number } | { type: 'rects'; rects: Rect[] };

/** A captured viewport. `image` is the adapter's own canvas, opaque here. */
export interface Capture {
  image: unknown;
  width: number;
  height: number;
  /** Canvas pixels per CSS pixel. */
  scale: number;
  /** Some images could not be read and show as grey areas. */
  placeholders: boolean;
}

/** What the adapter draws: marks and covers on the capture, then the source
 * rectangle scaled to the target size. */
export interface DrawPlan {
  marks: Mark[];
  covers: Rect[];
  source: Rect;
  target: { width: number; height: number };
}

export interface Platform {
  currentScript(): HTMLScriptElement | null;
  /** The light-DOM element under a viewport point, looking past the widget. */
  elementAt(x: number, y: number, host: Element): Element | null;
  box(element: Element): Box;
  /** The element has focus the browser shows (`:focus-visible`): from the
   * keyboard, or in a text field. */
  keyboardFocus(element: Element): boolean;
  /** The text as rendered (`innerText`): block boundaries kept, hidden text left out. */
  visibleText(element: Element): string;
  rangeBoxes(range: Range): Box[];
  viewport(): ViewportInfo;
  onViewportChange(listener: () => void): () => void;
  /** Calls `listener` once per frame at most after the page's layout changed:
   * a resize, a zoom, a rotation, or content that grew or shrank. Not for a
   * scroll, which the widget follows itself. Returns what stops it. */
  observeLayout(listener: () => void): () => void;
  /** Calls `callback` before the next frame is drawn. */
  nextFrame(callback: () => void): void;
  /** The part of the window in which an element can show: what every
   * ancestor that clips its overflow (a pane that scrolls, a box with
   * overflow hidden) leaves of it, axis by axis (rect.clipThrough), in the
   * window's coordinates. Null when nothing but the window clips it. */
  clipBox(element: Element): Box | null;
  /** The page's origin is opaque: a shielded site, sandboxed without its origin. */
  opaqueOrigin(): boolean;
  /** Scroll so that a point in page coordinates is in the middle of the
   * window's height, and sideways only as rect.windowLeft says. */
  scrollToPage(x: number, y: number): void;
  /** The parts of the page that scroll on their own and hold a node, from
   * the innermost out, up to the first fixed one: each ancestor that has
   * more to show than its box and that the visitor can scroll. Not the
   * window. */
  scrollers(node: Node): Scroller[];
  /** Scroll one of those parts to a scroll position. */
  scrollElementTo(element: Element, x: number, y: number): void;
  capture(host: Element): Promise<Capture>;
  /** Draws the plan into a new picture, opaque here. */
  draw(capture: Capture, plan: DrawPlan): unknown;
  /** A JPEG of a picture, or null when the browser cannot encode one. */
  encode(picture: unknown, quality: number): Promise<Blob | null>;
  /** A canvas element that shows a picture, for the frame overlays. */
  show(picture: unknown): HTMLElement;
  objectUrl(blob: Blob): string;
  revokeObjectUrl(url: string): void;
  /** Copy what a field holds; false when the browser does not let it. */
  copyText(field: HTMLTextAreaElement): Promise<boolean>;
  submitForm(form: HTMLFormElement): void;
  replaceUrl(url: string): void;
  fetch(url: string, init: RequestInit): Promise<Response>;
  languages(): readonly string[];
  userAgent(): string;
  coarsePointer(): boolean;
}

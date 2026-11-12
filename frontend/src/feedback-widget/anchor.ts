/**
 * Where a remark points: W3C Web Annotation selectors for the API, and the way
 * back from a stored place to the page (re-anchoring).
 *
 * A point is a CSS selector of the element under it, the point as fractions of
 * that element's box, and the page coordinates. A text selection is its quote
 * (cleaned as `cleanPageText` says: what the API refuses out, whitespace runs
 * collapsed), 32 characters of context either side, cleaned the same way, its
 * position over `document.body.textContent` in UTF-16 code units (a hint only,
 * a documented deviation from the standard's code points) and the selector of
 * the common ancestor. Re-anchoring tries the selector, then the quote (with
 * its context first), then the page coordinates.
 */
import { cleanPageText, cut, firstWords, MAX_AFFIX, MAX_QUOTE, UNSENDABLE_CHAR } from './text';
import type { Anchor, Box, Highlight, Platform, Point, Selector, ViewportInfo } from './types';

export const MAX_SELECTOR = 1000;
export const MAX_COORDINATE = 1_000_000;

/** How much text around a selection is read to find its context. */
const CONTEXT_WINDOW = 4096;

const SPACE_CHAR = /\s/;
/** What `CSS.escape` leaves as it is and the API refuses in a selector. */
const UNSENDABLE_IN_SELECTOR = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}]/gu;

/** `CSS.escape`, with every character the API refuses written as a CSS
 * code-point escape (`\ad `), which matches the same element. */
function identifier(value: string): string {
  return CSS.escape(value).replace(UNSENDABLE_IN_SELECTOR, (char) => `\\${char.codePointAt(0)!.toString(16)} `);
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

function nthOfType(element: Element): number {
  let index = 1;
  for (let sibling = element.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
    if (sibling.localName === element.localName) index += 1;
  }
  return index;
}

/** The longest leading part of the path that fits the API's limit; the
 * selector then names an ancestor, which is still the right region. */
function within(steps: string[]): string {
  for (let count = steps.length; count > 0; count -= 1) {
    const selector = steps.slice(0, count).join(' > ');
    if (selector.length <= MAX_SELECTOR) return selector;
  }
  return 'body';
}

/** `#id` when that id is unique, else a `tag:nth-of-type(n)` path that starts
 * at the nearest ancestor with a unique id, or at `body`. */
export function selectorFor(element: Element, doc: Document): string {
  const steps: string[] = [];
  for (let current: Element | null = element; current && current !== doc.body; current = current.parentElement) {
    const id = current.getAttribute('id');
    if (id) {
      const byId = `#${identifier(id)}`;
      if (doc.querySelectorAll(byId).length === 1) return within([byId, ...steps]);
    }
    steps.unshift(`${identifier(current.localName)}:nth-of-type(${nthOfType(current)})`);
  }
  return within(['body', ...steps]);
}

/** The element a selector names, never the widget's own host. */
export function query(doc: Document, selector: string, host: Element | null): Element | null {
  try {
    const found = doc.querySelector(selector);
    return found !== null && found !== host ? found : null;
  } catch {
    return null;
  }
}

/** The element itself when it is page content inside `body`, else `body`. */
export function pageElement(element: Element | null, doc: Document, host: Element | null): Element {
  if (element === null || element === host || !doc.body.contains(element)) return doc.body;
  return element;
}

export function pointAnchor(selector: string, box: Box, client: Point, scroll: Point): Anchor {
  const fraction = (offset: number, size: number) =>
    size > 0 ? Math.round(clamp(offset / size, 0, 1) * 10000) / 10000 : 0;
  const coordinate = (value: number) => clamp(Math.round(value), 0, MAX_COORDINATE);
  return {
    selectors: [{ type: 'CssSelector', value: selector }],
    point: {
      x: fraction(client.x - box.left, box.width),
      y: fraction(client.y - box.top, box.height),
      pageX: coordinate(client.x + scroll.x),
      pageY: coordinate(client.y + scroll.y),
    },
  };
}

function textOf(doc: Document, start: [Node, number], end: [Node, number]): string {
  const range = doc.createRange();
  range.setStart(start[0], start[1]);
  range.setEnd(end[0], end[1]);
  return range.toString();
}

export interface TextChoice {
  exact: string;
  anchor: Anchor;
}

/** Whether a range selects page text: inside `body`, outside the widget, and
 * more than whitespace and what the API refuses (icon glyphs, controls). */
export function selectsText(range: Range, doc: Document, host: Element | null): boolean {
  const common = range.commonAncestorContainer;
  if (!doc.body.contains(common) || (host !== null && host.contains(common))) return false;
  return cleanPageText(range.toString()).trim().length > 0;
}

/** The anchor of a selection inside `body`, or null when it holds no text. */
export function textAnchor(range: Range, doc: Document, host: Element | null): TextChoice | null {
  if (!selectsText(range, doc, host)) return null;
  const body = doc.body;
  const common = range.commonAncestorContainer;
  const raw = range.toString();
  const cleaned = cleanPageText(raw);
  const whole = cleaned.trim();
  const exact = cut(whole, MAX_QUOTE).trimEnd();
  const rest = whole.slice(exact.length);

  const before = textOf(doc, [body, 0], [range.startContainer, range.startOffset]);
  const after = textOf(doc, [range.endContainer, range.endOffset], [body, body.childNodes.length]);
  const leading = /^\s*/.exec(cleaned)![0];
  const trailing = /\s*$/.exec(cleaned)![0];
  const prefix = Array.from(cleanPageText(before.slice(-CONTEXT_WINDOW) + leading))
    .slice(-MAX_AFFIX)
    .join('');
  const suffix = cut(cleanPageText(rest + trailing + after.slice(0, CONTEXT_WINDOW)), MAX_AFFIX);

  const selectors: Selector[] = [{ type: 'TextQuoteSelector', exact, prefix, suffix }];
  const start = before.length;
  const end = start + raw.length;
  if (end <= MAX_COORDINATE) selectors.push({ type: 'TextPositionSelector', start, end });
  const ancestor = common.nodeType === Node.ELEMENT_NODE ? (common as Element) : common.parentElement!;
  selectors.push({ type: 'CssSelector', value: selectorFor(ancestor, doc) });
  return { exact, anchor: { selectors, point: null } };
}

interface TextIndex {
  text: string;
  nodes: Text[];
  offsets: number[];
}

/** The text under `root` cleaned like a quote (`cleanPageText`: what the API
 * refuses dropped first, then whitespace runs collapsed), with, for every
 * UTF-16 unit of the result, the text node and offset it came from. */
function indexText(root: Node, doc: Document): TextIndex {
  const index: TextIndex = { text: '', nodes: [], offsets: [] };
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let lastWasSpace = false;
  for (let node = walker.nextNode() as Text | null; node; node = walker.nextNode() as Text | null) {
    const data = node.data;
    for (let offset = 0; offset < data.length; ) {
      const char = String.fromCodePoint(data.codePointAt(offset)!);
      // A dropped character leaves lastWasSpace as it was, so the whitespace
      // on either side of it still collapses into one space.
      const dropped = UNSENDABLE_CHAR.test(char);
      if (!dropped && SPACE_CHAR.test(char)) {
        if (!lastWasSpace) {
          index.text += ' ';
          index.nodes.push(node);
          index.offsets.push(offset);
        }
        lastWasSpace = true;
      } else if (!dropped) {
        index.text += char;
        for (let unit = 0; unit < char.length; unit += 1) {
          index.nodes.push(node);
          index.offsets.push(offset + unit);
        }
        lastWasSpace = false;
      }
      offset += char.length;
    }
  }
  return index;
}

/** The first place the quote stands under `root`, preferring the place where
 * its context stands around it too. */
export function findQuote(
  root: Node,
  exact: string,
  doc: Document,
  prefix = '',
  suffix = '',
): Range | null {
  if (exact.length === 0) return null;
  const index = indexText(root, doc);
  let start = -1;
  if (prefix.length > 0 || suffix.length > 0) {
    const withContext = index.text.indexOf(prefix + exact + suffix);
    if (withContext >= 0) start = withContext + prefix.length;
  }
  if (start < 0) start = index.text.indexOf(exact);
  if (start < 0) return null;
  const last = start + exact.length - 1;
  const range = doc.createRange();
  range.setStart(index.nodes[start]!, index.offsets[start]!);
  range.setEnd(index.nodes[last]!, index.offsets[last]! + 1);
  return range;
}

export interface Located {
  /** Where the pin goes, in page coordinates. */
  x: number;
  y: number;
  /** The quote's boxes in page coordinates; empty for a point. */
  boxes: Box[];
  /** What on the page the place was found by: the element a point names, or
   * the passage the quote matched. Null for a place from page coordinates. */
  found: Element | Range | null;
}

function onPage(box: Box, view: ViewportInfo): Box {
  return { left: box.left + view.scrollX, top: box.top + view.scrollY, width: box.width, height: box.height };
}

/** A point on its element, or null while that element has no box. */
function pointOn(element: Element, fraction: Point, platform: Platform, view: ViewportInfo): Located | null {
  const box = platform.box(element);
  if (box.width === 0 && box.height === 0) return null;
  return {
    x: box.left + view.scrollX + fraction.x * box.width,
    y: box.top + view.scrollY + fraction.y * box.height,
    boxes: [],
    found: element,
  };
}

/** A quote on the passage it matched, or null while that passage shows nothing. */
function quoteOn(range: Range, platform: Platform, view: ViewportInfo): Located | null {
  const boxes = platform.rangeBoxes(range).filter((box) => box.width > 0);
  if (boxes.length === 0) return null;
  const page = boxes.map((box) => onPage(box, view));
  return { x: page[0]!.left, y: page[0]!.top, boxes: page, found: range };
}

/** A place found before, measured again on what it was found by. */
function measuredAgain(previous: Located, highlight: Highlight, platform: Platform, view: ViewportInfo): Located | null {
  const found = previous.found;
  // Page coordinates stay where they are.
  if (found === null) return previous;
  if (found instanceof Range) return quoteOn(found, platform, view);
  return found.isConnected ? pointOn(found, { x: highlight.x!, y: highlight.y! }, platform, view) : null;
}

/** Where a place is on the page now. With `previous`, the place as found
 * before, it is measured again on the element or passage it was found by, as
 * after a scroll, and searched for anew only when that shows nothing now:
 * reading the page's text for a quote on every frame would hold the scroll
 * up. */
export function locate(
  highlight: Highlight,
  doc: Document,
  platform: Platform,
  host: Element | null,
  previous: Located | null = null,
): Located | null {
  const view = platform.viewport();
  const again = previous === null ? null : measuredAgain(previous, highlight, platform, view);
  if (again !== null) return again;
  const element = highlight.s === null ? null : query(doc, highlight.s, host);
  if (highlight.k === 'point' && element !== null && highlight.x !== null && highlight.y !== null) {
    const place = pointOn(element, { x: highlight.x, y: highlight.y }, platform, view);
    if (place !== null) return place;
  }
  if (highlight.k === 'text' && highlight.q !== null) {
    const range =
      (element === null ? null : findQuote(element, highlight.q, doc)) ??
      findQuote(doc.body, highlight.q, doc);
    const place = range === null ? null : quoteOn(range, platform, view);
    if (place !== null) return place;
  }
  if (
    highlight.px !== null &&
    highlight.py !== null &&
    highlight.px <= view.docWidth &&
    highlight.py <= view.docHeight
  ) {
    return { x: highlight.px, y: highlight.py, boxes: [], found: null };
  }
  return null;
}

/** The element a place was found by: a point's own, or the one holding the
 * start of a quote's passage. Null for a place from page coordinates. */
export function foundElement(place: Located): Element | null {
  const found = place.found;
  if (!(found instanceof Range)) return found;
  const start = found.startContainer;
  return start instanceof Element ? start : start.parentElement;
}

/** The point that says whether a place shows and is in view: a point
 * itself, or the start of a quote's first box at half its height. The box
 * of a line of text is the font's, which reaches past a tight line, and so
 * past a box that clips the line flush with it. The pin stays at the
 * corner. */
export function seenAt(place: Located): Point {
  const first = place.boxes[0];
  return first === undefined ? { x: place.x, y: place.y } : { x: first.left, y: first.top + first.height / 2 };
}

/** The node whose ancestors move a place when they scroll: a point's
 * element, whose own scrolling leaves its box where it is, or the node a
 * quote's passage starts in, which its element's scrolling does move. Null
 * for a place from page coordinates. */
export function foundNode(place: Located): Node | null {
  const found = place.found;
  return found instanceof Range ? found.startContainer : found;
}

/** The first words of page text, as many as a screen reader hears of a place. */
export function describeText(text: string): string {
  return firstWords(text, 12, 80);
}

/** What a screen reader hears about the element under the crosshair: its
 * label, alternative text or title, else its text as rendered. */
export function describeElement(element: Element, renderedText: string): string {
  const label =
    element.getAttribute('aria-label') ||
    element.getAttribute('alt') ||
    element.getAttribute('title') ||
    renderedText;
  return describeText(label);
}

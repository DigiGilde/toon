/**
 * Feedback text, and the replies to it, as a small fixed subset of Markdown.
 * The text comes from anyone a site lets give feedback, the public included,
 * and is stored as typed. This module turns it into a tree of a few node
 * types first (`parseFeedbackText`), framework-free, and the widget builds
 * DOM from that tree with `createElement` and text nodes alone
 * (`renderFeedbackText`). The admin SPA renders the same tree with Vue
 * (FeedbackMarkdown.vue). No step ever reads a string as markup, so there is
 * nothing to sanitise: a `<` is a character like any other.
 *
 * Supported: paragraphs split by a blank line, with their single line breaks
 * kept; bullet (`-`, `*`, `+`) and numbered (`1.`, `1)`) lists, one level of
 * nesting deep; fenced code blocks of three or more backticks; blockquotes
 * one level deep; inline code, `**strong**`, `*emphasis*` and `_emphasis_`;
 * backslash escapes; links. Everything else (headings, tables, images, HTML,
 * entities, setext and indented code) stays the text it was typed as.
 *
 * A link never hides where it goes: only an absolute `http:` or `https:`
 * address becomes one, its visible text is that address, and
 * `[label](https://x)` reads "label (https://x)". An address with an
 * invisible or direction-changing character, or with a user name before its
 * host, stays text. A host that is not shown as the browser resolves it (an
 * internationalised name, a percent-encoded or numeric host) is shown
 * resolved, in ASCII, so a look-alike stands out.
 *
 * Every scan is linear in the length of the text: the parser holds no
 * backtracking regular expression over it, and the emphasis pass is the
 * CommonMark delimiter algorithm with its per-kind lower bounds.
 */

export interface TextNode {
  type: 'text';
  value: string;
}
export interface BreakNode {
  type: 'break';
}
export interface CodeNode {
  type: 'code';
  value: string;
}
export interface StrongNode {
  type: 'strong';
  children: Inline[];
}
export interface EmphasisNode {
  type: 'emphasis';
  children: Inline[];
}
/** `href` is the address as the URL parser resolves it; `text` is what shows. */
export interface LinkNode {
  type: 'link';
  href: string;
  text: string;
}
export type Inline = TextNode | BreakNode | CodeNode | StrongNode | EmphasisNode | LinkNode;

export interface Paragraph {
  type: 'paragraph';
  children: Inline[];
}
export interface CodeBlock {
  type: 'codeBlock';
  value: string;
}
export interface Quote {
  type: 'quote';
  children: Block[];
}
export interface List {
  type: 'list';
  ordered: boolean;
  /** The number of the first item; 1 for a bullet list. */
  start: number;
  items: ListItem[];
}
export interface ListItem {
  children: Inline[];
  /** The nested lists, which never hold lists of their own. */
  lists: List[];
}
export type Block = Paragraph | CodeBlock | Quote | List;

/** Every link, in the widget and in the admin SPA. */
export const LINK_REL = 'noopener noreferrer nofollow ugc';
/** The class of the element the widget puts the fragment in (styles.ts). */
export const TEXT_CLASS = 'feedback-text';
/** Visually hidden words, such as "opens in a new tab" (styles.ts). */
export const HIDDEN_CLASS = 'feedback-text-hidden';

/** How many emphasis and strong elements nest; deeper markers stay text. */
const MAX_INLINE_DEPTH = 5;

// Single characters only: none of these is ever applied to a run of text.
const ASCII_PUNCTUATION = /^[!-\/:-@[-`{-~]$/;
const WHITESPACE = /^\s$/u;
const PUNCTUATION = /^[\p{P}\p{S}]$/u;
const WORD = /^[\p{L}\p{N}]$/u;
/** Where an address ends, typed bare or between the parentheses of a link. */
const ADDRESS_STOP = /^[\s<>"`\\[\]]$/u;
/** Dropped from the end of a bare address: the sentence's, not the address's. */
const TRAILING = '?!.,:;*_~\'';
const SCHEME = /^https?:\/\//i;
/**
 * Characters that show nothing or turn the direction of what follows: format
 * characters (bidi controls, zero-width space and joiners, the byte order
 * mark, the soft hyphen), controls, line and paragraph separators, the Hangul
 * fillers, the Braille blank, and the variation selectors and the combining
 * grapheme joiner. An address with one of these is never a link.
 */
const INVISIBLE =
  /[\p{Cf}\p{Cc}\p{Zl}\p{Zp}\u115f\u1160\u3164\uffa0\u2800\u034f\u17b4\u17b5\u180b-\u180f\ufe00-\ufe0f\u{e0100}-\u{e01ef}]/u;

/** The text as a tree of blocks. An empty or blank text has none. */
export function parseFeedbackText(text: string): Block[] {
  return parseBlocks(text.replace(/\r\n?/g, '\n').split('\n'), false);
}

/**
 * An address as a link, or null when it may not become one: not absolute
 * `http:` or `https:`, unparseable, with a user name or an `@` before the
 * host, or with an invisible character anywhere.
 */
export function safeLink(raw: string): LinkNode | null {
  if (!SCHEME.test(raw) || INVISIBLE.test(raw)) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  const hostStart = raw.indexOf('//') + 2;
  let hostEnd = hostStart;
  while (hostEnd < raw.length && !'/?#'.includes(raw[hostEnd]!)) hostEnd += 1;
  const authority = raw.slice(hostStart, hostEnd);
  if (authority === '' || authority.includes('@')) return null;
  const host = authority.toLowerCase() === url.host ? authority : url.host;
  return { type: 'link', href: url.href, text: raw.slice(0, hostStart) + host + raw.slice(hostEnd) };
}

/** The text without its markup: for a one-line excerpt, where a link or a list cannot go. */
export function feedbackPlainText(text: string): string {
  return parseFeedbackText(text).map(blockText).join('\n\n');
}

function blockText(block: Block): string {
  switch (block.type) {
    case 'paragraph':
      return inlineText(block.children);
    case 'codeBlock':
      return block.value;
    case 'quote':
      return block.children.map(blockText).join('\n\n');
    case 'list':
      return block.items.map((item) => [inlineText(item.children), ...item.lists.map(blockText)].join('\n')).join('\n');
  }
}

function inlineText(nodes: Inline[]): string {
  return nodes
    .map((node) => {
      switch (node.type) {
        case 'text':
        case 'code':
          return node.value;
        case 'break':
          return '\n';
        case 'link':
          return node.text;
        default:
          return inlineText(node.children);
      }
    })
    .join('');
}

// Blocks

interface ItemDraft {
  lines: string[];
  lists: ListDraft[];
}
interface ListDraft {
  ordered: boolean;
  start: number;
  /** Where the first item's marker stood: an item two columns further in nests. */
  indent: number;
  items: ItemDraft[];
}
interface Marker {
  ordered: boolean;
  start: number;
  content: string;
}

function last<T>(items: T[]): T | undefined {
  return items[items.length - 1];
}

/** The leading spaces and tabs of a line: their width in columns (a tab stops every four), and their length. */
function indentation(line: string): { width: number; offset: number } {
  let width = 0;
  let offset = 0;
  for (; offset < line.length; offset += 1) {
    if (line[offset] === ' ') width += 1;
    else if (line[offset] === '\t') width += 4 - (width % 4);
    else break;
  }
  return { width, offset };
}

/** The line without up to `width` columns of leading spaces and tabs. */
function outdent(line: string, width: number): string {
  let columns = 0;
  let offset = 0;
  while (columns < width && (line[offset] === ' ' || line[offset] === '\t')) {
    columns += line[offset] === '\t' ? 4 - (columns % 4) : 1;
    offset += 1;
  }
  return line.slice(offset);
}

function runLength(text: string, start: number, char: string): number {
  let end = start;
  while (text[end] === char) end += 1;
  return end - start;
}

/** The number of backticks of an opening fence, or 0. Its info string, which may hold no backtick, is ignored. */
function fenceAt(rest: string): number {
  const length = runLength(rest, 0, '`');
  return length >= 3 && !rest.includes('`', length) ? length : 0;
}

function closesFence(line: string, length: number): boolean {
  const rest = line.slice(indentation(line).offset);
  const run = runLength(rest, 0, '`');
  return run >= length && rest.slice(run).trim() === '';
}

function isDigit(char: string | undefined): boolean {
  return char !== undefined && char >= '0' && char <= '9';
}

/** A list marker at the start of the line: `-`, `*`, `+`, or one to nine digits and `.` or `)`, then a space, a tab or the end. */
function markerAt(rest: string): Marker | null {
  let length = 0;
  let ordered = false;
  if (rest[0] === '-' || rest[0] === '*' || rest[0] === '+') {
    length = 1;
  } else {
    while (length < 10 && isDigit(rest[length])) length += 1;
    if (length === 0 || length > 9 || (rest[length] !== '.' && rest[length] !== ')')) return null;
    ordered = true;
    length += 1;
  }
  if (length < rest.length && rest[length] !== ' ' && rest[length] !== '\t') return null;
  return { ordered, start: ordered ? Number(rest.slice(0, length - 1)) : 1, content: rest.slice(length).trim() };
}

function newList(marker: Marker, indent: number): ListDraft {
  return { ordered: marker.ordered, start: marker.start, indent, items: [{ lines: [marker.content], lists: [] }] };
}

function finishList(draft: ListDraft): List {
  return {
    type: 'list',
    ordered: draft.ordered,
    start: draft.start,
    items: draft.items.map((item) => ({
      children: parseInline(item.lines.filter((line) => line !== '').join('\n'), true),
      lists: item.lists.map(finishList),
    })),
  };
}

/** The blocks of these lines; inside a quote, a `>` is text. */
function parseBlocks(lines: readonly string[], inQuote: boolean): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: ListDraft | null = null;
  /** A blank line came after the last line of the open list. */
  let blank = false;

  const endParagraph = () => {
    if (paragraph.length === 0) return;
    blocks.push({ type: 'paragraph', children: parseInline(paragraph.join('\n'), true) });
    paragraph = [];
  };
  const endList = () => {
    if (list === null) return;
    blocks.push(finishList(list));
    list = null;
  };

  let index = 0;
  while (index < lines.length) {
    const line = lines[index]!;
    const { width, offset } = indentation(line);
    const rest = line.slice(offset);
    if (rest.trim() === '') {
      endParagraph();
      blank = true;
      index += 1;
      continue;
    }

    const fence = fenceAt(rest);
    if (fence > 0) {
      endParagraph();
      endList();
      const body: string[] = [];
      index += 1;
      while (index < lines.length) {
        const next = lines[index]!;
        index += 1;
        if (closesFence(next, fence)) break;
        body.push(outdent(next, width));
      }
      blocks.push({ type: 'codeBlock', value: body.join('\n') });
      continue;
    }

    if (!inQuote && rest[0] === '>') {
      endParagraph();
      endList();
      const quoted: string[] = [];
      while (index < lines.length) {
        const next = lines[index]!;
        const content = next.slice(indentation(next).offset);
        if (content[0] !== '>') break;
        quoted.push(content.slice(content[1] === ' ' ? 2 : 1));
        index += 1;
      }
      blocks.push({ type: 'quote', children: parseBlocks(quoted, true) });
      continue;
    }

    const marker = markerAt(rest);
    // Only an item with text, and a numbered one only from 1, breaks into a
    // paragraph: "2024. was a good year" on its second line stays a sentence.
    const interrupts = marker !== null && marker.content !== '' && (!marker.ordered || marker.start === 1);
    if (marker !== null && (paragraph.length === 0 || interrupts)) {
      endParagraph();
      const open = list as ListDraft | null;
      if (open !== null && width >= open.indent + 2) {
        const parent = last(open.items)!;
        const nested = last(parent.lists);
        if (nested !== undefined && nested.ordered === marker.ordered) nested.items.push({ lines: [marker.content], lists: [] });
        else parent.lists.push(newList(marker, width));
      } else if (open !== null && open.ordered === marker.ordered) {
        open.items.push({ lines: [marker.content], lists: [] });
      } else {
        endList();
        list = newList(marker, width);
      }
      blank = false;
      index += 1;
      continue;
    }

    const open = list as ListDraft | null;
    if (open !== null && !blank) {
      // A line of its own inside the last item, indented or not.
      const top = last(open.items)!;
      const nested = last(top.lists);
      (nested === undefined ? top : last(nested.items)!).lines.push(rest.trim());
    } else {
      endList();
      paragraph.push(rest.trim());
    }
    blank = false;
    index += 1;
  }
  endParagraph();
  endList();
  return blocks;
}

// Inline

interface Delimiter {
  type: 'delimiter';
  char: string;
  /** The markers of the run not used yet. */
  count: number;
  /** The markers of the run as typed. */
  length: number;
  canOpen: boolean;
  canClose: boolean;
  /** Its place among the delimiters of the text, for the lower bounds. */
  position: number;
  previous: Delimiter | null;
  next: Delimiter | null;
  /** How many markers each match took, in the order they matched. */
  opened: number[];
  closed: number[];
}
type Token = Inline | Delimiter;

/** The code point that ends at `index`, or '' at the start. */
function charBefore(text: string, index: number): string {
  if (index === 0) return '';
  const low = text.charCodeAt(index - 1);
  if (low >= 0xdc00 && low <= 0xdfff && index >= 2) {
    const high = text.charCodeAt(index - 2);
    if (high >= 0xd800 && high <= 0xdbff) return text.slice(index - 2, index);
  }
  return text[index - 1]!;
}

/** The code point that starts at `index`, or '' at the end. */
function charAfter(text: string, index: number): string {
  const point = text.codePointAt(index);
  return point === undefined ? '' : String.fromCodePoint(point);
}

/** Where the next run of exactly `length` backticks starts, at `from` or later, or -1. Calls come with `from` rising. */
function backtickRuns(text: string): (length: number, from: number) => number {
  const starts = new Map<number, number[]>();
  for (let index = 0; index < text.length; ) {
    if (text[index] !== '`') {
      index += 1;
      continue;
    }
    const length = runLength(text, index, '`');
    const list = starts.get(length) ?? [];
    list.push(index);
    starts.set(length, list);
    index += length;
  }
  const cursors = new Map<number, number>();
  return (length, from) => {
    const list = starts.get(length) ?? [];
    let cursor = cursors.get(length) ?? 0;
    while (cursor < list.length && list[cursor]! < from) cursor += 1;
    cursors.set(length, cursor);
    return cursor < list.length ? list[cursor]! : -1;
  };
}

function codeContent(raw: string): string {
  const value = raw.replace(/\n/g, ' ');
  return value[0] === ' ' && value[value.length - 1] === ' ' && /[^ ]/.test(value) ? value.slice(1, -1) : value;
}

/** `[label](destination)` at `start`, as a shape: whether the destination may be a link is for later. */
function linkShape(text: string, start: number): { label: string; destination: string; end: number } | null {
  let close = start + 1;
  while (close < text.length && text[close] !== ']' && text[close] !== '[') close += text[close] === '\\' ? 2 : 1;
  if (text[close] !== ']' || text[close + 1] !== '(') return null;
  let end = close + 2;
  let depth = 0;
  for (; end < text.length; end += 1) {
    const char = text[end]!;
    if (char === ')') {
      if (depth === 0) break;
      depth -= 1;
    } else if (char === '(') {
      depth += 1;
    } else if (ADDRESS_STOP.test(char)) {
      return null;
    }
  }
  if (end === text.length || end === close + 2) return null;
  return { label: text.slice(start + 1, close), destination: text.slice(close + 2, end), end: end + 1 };
}

/** Where a bare address that starts at `start` ends, without the sentence's punctuation after it. */
function addressEnd(text: string, start: number): number {
  let end = start;
  let opened = 0;
  let closed = 0;
  while (end < text.length && !ADDRESS_STOP.test(text[end]!)) {
    if (text[end] === '(') opened += 1;
    else if (text[end] === ')') closed += 1;
    end += 1;
  }
  while (end > start) {
    const char = text[end - 1]!;
    if (TRAILING.includes(char)) {
      end -= 1;
    } else if (char === ')' && closed > opened) {
      closed -= 1;
      end -= 1;
    } else {
      break;
    }
  }
  return end;
}

function delimiter(text: string, start: number, length: number, char: string, position: number): Delimiter {
  const before = charBefore(text, start);
  const after = charAfter(text, start + length);
  const spaceBefore = before === '' || WHITESPACE.test(before);
  const spaceAfter = after === '' || WHITESPACE.test(after);
  const markBefore = PUNCTUATION.test(before);
  const markAfter = PUNCTUATION.test(after);
  const left = !spaceAfter && (!markAfter || spaceBefore || markBefore);
  const right = !spaceBefore && (!markBefore || spaceAfter || markAfter);
  // An underscore inside a word, as in snake_case, is no marker.
  const canOpen = char === '*' ? left : left && (!right || markBefore);
  const canClose = char === '*' ? right : right && (!left || markAfter);
  return {
    type: 'delimiter',
    char,
    count: length,
    length,
    canOpen,
    canClose,
    position,
    previous: null,
    next: null,
    opened: [],
    closed: [],
  };
}

function unlink(item: Delimiter): void {
  if (item.previous !== null) item.previous.next = item.next;
  if (item.next !== null) item.next.previous = item.previous;
}

/** CommonMark's "process emphasis": pairs openers with closers, recording each match on both. */
function processEmphasis(first: Delimiter | null): void {
  const bottoms = new Map<string, number>();
  let closer = first;
  while (closer !== null) {
    if (!closer.canClose) {
      closer = closer.next;
      continue;
    }
    const key = `${closer.char}${closer.canOpen ? 1 : 0}${closer.length % 3}`;
    const bottom = bottoms.get(key) ?? -1;
    let opener = closer.previous;
    while (opener !== null && opener.position > bottom) {
      const odd =
        (opener.canClose || closer.canOpen) &&
        (opener.length + closer.length) % 3 === 0 &&
        !(opener.length % 3 === 0 && closer.length % 3 === 0);
      if (opener.char === closer.char && opener.canOpen && !odd) break;
      opener = opener.previous;
    }
    if (opener !== null && opener.position > bottom) {
      const use = opener.count >= 2 && closer.count >= 2 ? 2 : 1;
      opener.count -= use;
      closer.count -= use;
      opener.opened.push(use);
      closer.closed.push(use);
      // What lies between them stays text.
      opener.next = closer;
      closer.previous = opener;
      if (opener.count === 0) unlink(opener);
      if (closer.count === 0) {
        unlink(closer);
        closer = closer.next;
      }
    } else {
      bottoms.set(key, closer.previous === null ? -1 : closer.previous.position);
      if (!closer.canOpen) unlink(closer);
      closer = closer.next;
    }
  }
}

/** The tokens as a tree, the matched markers as elements and the rest as text. */
function build(tokens: Token[]): Inline[] {
  const root: Inline[] = [];
  const containers: Inline[][] = [root];
  /** Per open match: its element, or null where it was too deep and its markers stay text. */
  const frames: (StrongNode | EmphasisNode | null)[] = [];
  const add = (node: Inline) => {
    const target = last(containers)!;
    const previous = last(target);
    if (node.type === 'text' && previous?.type === 'text') previous.value += node.value;
    else target.push(node);
  };
  for (const token of tokens) {
    if (token.type !== 'delimiter') {
      add(token);
      continue;
    }
    for (const use of token.closed) {
      const node = frames.pop() as StrongNode | EmphasisNode | null;
      if (node === null) {
        add({ type: 'text', value: token.char.repeat(use) });
      } else {
        containers.pop();
        add(node);
      }
    }
    if (token.count > 0) add({ type: 'text', value: token.char.repeat(token.count) });
    for (let index = token.opened.length - 1; index >= 0; index -= 1) {
      const use = token.opened[index]!;
      if (containers.length > MAX_INLINE_DEPTH) {
        frames.push(null);
        add({ type: 'text', value: token.char.repeat(use) });
      } else {
        const node: StrongNode | EmphasisNode = { type: use === 2 ? 'strong' : 'emphasis', children: [] };
        frames.push(node);
        containers.push(node.children);
      }
    }
  }
  return root;
}

/** The inline nodes of a paragraph or an item; in a link's label, nothing more becomes a link. */
function parseInline(text: string, links: boolean): Inline[] {
  const tokens: Token[] = [];
  const nextRun = backtickRuns(text);
  let pending = '';
  let first: Delimiter | null = null;
  let latest: Delimiter | null = null;
  let count = 0;
  /** Just after an unescaped `!`: a `[` there starts an image, which stays text. */
  let bang = -1;

  const flush = () => {
    if (pending === '') return;
    tokens.push({ type: 'text', value: pending });
    pending = '';
  };
  const push = (node: Inline) => {
    flush();
    tokens.push(node);
  };

  let index = 0;
  while (index < text.length) {
    const char = text[index]!;
    if (char === '\\' && ASCII_PUNCTUATION.test(text[index + 1] ?? '')) {
      pending += text[index + 1];
      index += 2;
      continue;
    }
    if (char === '\n') {
      push({ type: 'break' });
      index += 1;
      continue;
    }
    if (char === '`') {
      const length = runLength(text, index, '`');
      const close = nextRun(length, index + length);
      if (close >= 0) {
        push({ type: 'code', value: codeContent(text.slice(index + length, close)) });
        index = close + length;
      } else {
        pending += text.slice(index, index + length);
        index += length;
      }
      continue;
    }
    if (char === '*' || char === '_') {
      const length = runLength(text, index, char);
      flush();
      const run = delimiter(text, index, length, char, count);
      count += 1;
      run.previous = latest;
      if (latest === null) first = run;
      else latest.next = run;
      latest = run;
      tokens.push(run);
      index += length;
      continue;
    }
    if (links && char === '[') {
      const shape = linkShape(text, index);
      if (shape !== null && bang === index) {
        pending += text.slice(index, shape.end);
        index = shape.end;
        continue;
      }
      const link = shape === null ? null : safeLink(shape.destination);
      if (shape !== null && link !== null) {
        const label = shape.label.trim();
        if (label !== '' && label !== shape.destination) {
          flush();
          tokens.push(...parseInline(shape.label, false));
          pending += ' (';
          push(link);
          pending += ')';
        } else {
          push(link);
        }
        index = shape.end;
        continue;
      }
    }
    if (links && (char === 'h' || char === 'H') && !WORD.test(charBefore(text, index)) && SCHEME.test(text.slice(index, index + 8))) {
      const end = addressEnd(text, index);
      const link = safeLink(text.slice(index, end));
      if (link === null) pending += text.slice(index, end);
      else push(link);
      index = end;
      continue;
    }
    if (char === '!') bang = index + 1;
    pending += char;
    index += 1;
  }
  flush();
  processEmphasis(first);
  return build(tokens);
}

// DOM

export interface RenderOptions {
  /**
   * Links open in a new tab (the default). The widget turns this off on an
   * opaque origin, a shielded site: a new tab would inherit its sandbox, in
   * which the page linked to may not work.
   */
  newTab?: boolean;
  /** Visually hidden words inside a link that opens in a new tab, such as "(opens in a new tab)". */
  newTabHint?: string;
}

/** The text as DOM nodes of `doc`, for the widget, which builds in the page's own document. */
export function renderFeedbackText(text: string, doc: Document, options: RenderOptions = {}): DocumentFragment {
  const fragment = doc.createDocumentFragment();
  for (const block of parseFeedbackText(text)) fragment.appendChild(blockElement(block, doc, options));
  return fragment;
}

function element(doc: Document, tag: string, children: Node[]): HTMLElement {
  const made = doc.createElement(tag);
  for (const child of children) made.appendChild(child);
  return made;
}

function blockElement(block: Block, doc: Document, options: RenderOptions): HTMLElement {
  switch (block.type) {
    case 'paragraph':
      return element(doc, 'p', inlineNodes(block.children, doc, options));
    case 'codeBlock':
      return element(doc, 'pre', [element(doc, 'code', [doc.createTextNode(block.value)])]);
    case 'quote':
      return element(
        doc,
        'blockquote',
        block.children.map((child) => blockElement(child, doc, options)),
      );
    case 'list': {
      const list = element(
        doc,
        block.ordered ? 'ol' : 'ul',
        block.items.map((item) =>
          element(doc, 'li', [
            ...inlineNodes(item.children, doc, options),
            ...item.lists.map((nested) => blockElement(nested, doc, options)),
          ]),
        ),
      );
      if (block.start !== 1) list.setAttribute('start', String(block.start));
      return list;
    }
  }
}

function inlineNodes(nodes: Inline[], doc: Document, options: RenderOptions): Node[] {
  return nodes.map((node) => {
    switch (node.type) {
      case 'text':
        return doc.createTextNode(node.value);
      case 'break':
        return doc.createElement('br');
      case 'code':
        return element(doc, 'code', [doc.createTextNode(node.value)]);
      case 'strong':
        return element(doc, 'strong', inlineNodes(node.children, doc, options));
      case 'emphasis':
        return element(doc, 'em', inlineNodes(node.children, doc, options));
      case 'link':
        return linkElement(node, doc, options);
    }
  });
}

function linkElement(node: LinkNode, doc: Document, options: RenderOptions): HTMLElement {
  const link = element(doc, 'a', [doc.createTextNode(node.text)]);
  link.setAttribute('href', node.href);
  link.setAttribute('rel', LINK_REL);
  // An address reads left to right, whatever the text around it.
  link.setAttribute('dir', 'ltr');
  if (options.newTab !== false) {
    link.setAttribute('target', '_blank');
    if (options.newTabHint) {
      const hint = element(doc, 'span', [doc.createTextNode(` ${options.newTabHint}`)]);
      hint.setAttribute('class', HIDDEN_CLASS);
      link.appendChild(hint);
    }
  }
  return link;
}

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  describeElement,
  findQuote,
  foundElement,
  locate,
  MAX_SELECTOR,
  pageElement,
  pointAnchor,
  query,
  seenAt,
  selectorFor,
  selectsText,
  textAnchor,
} from './anchor';
import { fakePlatform, UNASSIGNED } from './testPlatform';
import type { Highlight } from './types';

function page(html: string): void {
  document.body.innerHTML = html;
}

function rangeOver(node: Node, start: number, endNode: Node, end: number): Range {
  const range = document.createRange();
  range.setStart(node, start);
  range.setEnd(endNode, end);
  return range;
}

afterEach(() => {
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('selectorFor', () => {
  it('names an element by a unique id', () => {
    page('<main><p id="intro">a</p></main>');
    expect(selectorFor(document.getElementById('intro')!, document)).toBe('#intro');
  });

  it('starts a path at the nearest ancestor with a unique id', () => {
    page('<div id="main"><p>a</p><span></span><p class="x">b</p></div>');
    const target = document.querySelector('.x')!;
    expect(selectorFor(target, document)).toBe('#main > p:nth-of-type(2)');
    expect(document.querySelector(selectorFor(target, document))).toBe(target);
  });

  it('passes over an id that is not unique', () => {
    page('<section><p id="twice">a</p></section><section><p id="twice">b</p></section>');
    const second = document.querySelectorAll('p')[1]!;
    expect(selectorFor(second, document)).toBe('body > section:nth-of-type(2) > p:nth-of-type(1)');
  });

  it('escapes an id CSS cannot take as it is', () => {
    page('<p id="1 b"></p>');
    const selector = selectorFor(document.querySelector('p')!, document);
    expect(document.querySelector(selector)).toBe(document.querySelector('p'));
  });

  it('writes what the API refuses in an id or a tag name as a code-point escape, which still matches', () => {
    page('<section><p>a</p></section>');
    const section = document.querySelector('section')!;
    // A soft hyphen, an icon glyph and a C1 control: CSS.escape leaves all three as they are.
    section.id = 'beleids­kader\u0085';
    const paragraph = document.querySelector('p')!;
    const selector = selectorFor(paragraph, document);
    expect(selector).toBe('#beleids\\ad kader\\e900 \\85  > p:nth-of-type(1)');
    expect(document.querySelector(selector)).toBe(paragraph);
    const custom = document.createElement('x-a‍b');
    section.append(custom);
    expect(selectorFor(custom, document)).toBe('#beleids\\ad kader\\e900 \\85  > x-a\\200d b:nth-of-type(1)');
    expect(selector).not.toMatch(/[\p{Cc}\p{Cf}\p{Cs}\p{Co}]/u);
  });

  it('leaves a code point the Unicode tables do not know yet in an id as it is, which the API takes', () => {
    // One in the BMP: jsdom matches no code point beyond it written as it is,
    // an emoji included, where a browser does.
    const unassigned = '\u0378';
    expect(unassigned).toMatch(/^\p{Cn}$/u);
    page('<section><p>a</p></section>');
    document.querySelector('section')!.id = `kop${unassigned}`;
    const paragraph = document.querySelector('p')!;
    const selector = selectorFor(paragraph, document);
    expect(selector).toBe(`#kop${unassigned} > p:nth-of-type(1)`);
    expect(document.querySelector(selector)).toBe(paragraph);
  });

  it('names the body itself as body', () => {
    expect(selectorFor(document.body, document)).toBe('body');
  });

  it('keeps a deep path within the limit by naming an ancestor', () => {
    page(`${'<div>'.repeat(200)}<p>deep</p>${'</div>'.repeat(200)}`);
    const deep = document.querySelector('p')!;
    const selector = selectorFor(deep, document);
    expect(selector.length).toBeLessThanOrEqual(MAX_SELECTOR);
    expect(document.querySelector(selector)!.contains(deep)).toBe(true);
  });

  it('falls back to body when even the first step is too long', () => {
    page(`<p id="${'x'.repeat(1200)}"></p>`);
    expect(selectorFor(document.querySelector('p')!, document)).toBe('body');
  });
});

describe('query', () => {
  it('finds the element a selector names', () => {
    page('<p id="a"></p>');
    expect(query(document, '#a', null)).toBe(document.getElementById('a'));
  });

  it('never gives the widget host', () => {
    page('<div id="host"></div>');
    const host = document.getElementById('host')!;
    expect(query(document, '#host', host)).toBeNull();
  });

  it('gives nothing for a selector the browser refuses', () => {
    vi.spyOn(document, 'querySelector').mockImplementation(() => {
      throw new DOMException('bad', 'SyntaxError');
    });
    expect(query(document, 'p[', null)).toBeNull();
  });
});

describe('pageElement', () => {
  it('keeps page content and replaces everything else by body', () => {
    page('<p></p><div id="host"></div>');
    const p = document.querySelector('p')!;
    const host = document.getElementById('host')!;
    expect(pageElement(p, document, host)).toBe(p);
    expect(pageElement(null, document, host)).toBe(document.body);
    expect(pageElement(host, document, host)).toBe(document.body);
    expect(pageElement(document.documentElement, document, host)).toBe(document.body);
  });
});

describe('pointAnchor', () => {
  it('records the point as fractions of the box and in page coordinates', () => {
    const anchor = pointAnchor('#a', { left: 100, top: 50, width: 200, height: 100 }, { x: 150, y: 75 }, { x: 0, y: 400 });
    expect(anchor).toEqual({
      selectors: [{ type: 'CssSelector', value: '#a' }],
      point: { x: 0.25, y: 0.25, pageX: 150, pageY: 475 },
    });
  });

  it('clamps fractions to the box and coordinates to the API range', () => {
    const anchor = pointAnchor('#a', { left: 100, top: 100, width: 10, height: 10 }, { x: 50, y: 500 }, { x: -80, y: 2_000_000 });
    expect(anchor.point).toEqual({ x: 0, y: 1, pageX: 0, pageY: 1_000_000 });
  });

  it('records 0 for a box without size, and rounds', () => {
    const anchor = pointAnchor('body', { left: 0, top: 0, width: 0, height: 3 }, { x: 10.6, y: 1 }, { x: 0.2, y: 0 });
    expect(anchor.point).toEqual({ x: 0, y: 0.3333, pageX: 11, pageY: 1 });
  });
});

describe('selectsText and textAnchor', () => {
  it('see no text in whitespace only, in the widget, or outside body', () => {
    page('<p>   </p><div id="host"><span>widget text</span></div>');
    const blank = document.querySelector('p')!.firstChild!;
    const host = document.getElementById('host')!;
    const inside = host.querySelector('span')!.firstChild!;
    expect(selectsText(rangeOver(blank, 0, blank, 3), document, host)).toBe(false);
    expect(textAnchor(rangeOver(blank, 0, blank, 3), document, host)).toBeNull();
    expect(selectsText(rangeOver(inside, 0, inside, 6), document, host)).toBe(false);
    const title = document.createElement('title');
    title.textContent = 'Head text';
    document.head.append(title);
    expect(selectsText(rangeOver(title.firstChild!, 0, title.firstChild!, 4), document, null)).toBe(false);
    title.remove();
  });

  it('see text outside the widget when there is a host', () => {
    page('<p>tekst</p><div id="host"></div>');
    const text = document.querySelector('p')!.firstChild!;
    expect(selectsText(rangeOver(text, 0, text, 5), document, document.getElementById('host'))).toBe(true);
  });

  it('records the quote, its context, its position and its element', () => {
    page('<main id="m"><p>De begroting van 2,4 miljoen euro voor dit jaar.</p></main>');
    const text = document.querySelector('p')!.firstChild!;
    const start = 'De begroting van '.length;
    const choice = textAnchor(rangeOver(text, start, text, start + '2,4 miljoen'.length), document, null)!;
    expect(choice.exact).toBe('2,4 miljoen');
    expect(choice.anchor).toEqual({
      selectors: [
        { type: 'TextQuoteSelector', exact: '2,4 miljoen', prefix: 'De begroting van ', suffix: ' euro voor dit jaar.' },
        { type: 'TextPositionSelector', start, end: start + 11 },
        { type: 'CssSelector', value: '#m > p:nth-of-type(1)' },
      ],
      point: null,
    });
  });

  it('cleans the quote and keeps the trimmed whitespace in the context', () => {
    page('<p>Een lange zin met  ruimte\u00ad en meer woorden die nog verder gaan dan dit.</p>');
    const text = document.querySelector('p')!.firstChild!;
    const data = text.textContent!;
    const from = data.indexOf(' met');
    const to = data.indexOf('meer') + 4;
    const choice = textAnchor(rangeOver(text, from, text, to), document, null)!;
    const quote = choice.anchor.selectors[0]!;
    expect(choice.exact).toBe('met ruimte en meer');
    expect(quote).toMatchObject({ prefix: 'Een lange zin ', suffix: ' woorden die nog verder gaan dan' });
  });

  it('takes at most 32 characters of context on either side', () => {
    page(`<p>${'a'.repeat(50)} midden ${'b'.repeat(50)}</p>`);
    const text = document.querySelector('p')!.firstChild!;
    const choice = textAnchor(rangeOver(text, 51, text, 57), document, null)!;
    expect(choice.anchor.selectors[0]).toEqual({
      type: 'TextQuoteSelector',
      exact: 'midden',
      prefix: `${'a'.repeat(31)} `,
      suffix: ` ${'b'.repeat(31)}`,
    });
  });

  it('names the common ancestor of a selection across elements', () => {
    page('<article id="art"><p>Eerste alinea.</p><p>Tweede alinea.</p></article>');
    const [first, second] = Array.from(document.querySelectorAll('p'), (p) => p.firstChild!);
    const choice = textAnchor(rangeOver(first!, 7, second!, 6), document, null)!;
    expect(choice.exact).toBe('alinea.Tweede');
    expect(choice.anchor.selectors[2]).toEqual({ type: 'CssSelector', value: '#art' });
  });

  it('cuts a quote beyond 1000 characters and lets the context go on from there', () => {
    page(`<p>${'woord '.repeat(300)}</p>`);
    const text = document.querySelector('p')!.firstChild!;
    const choice = textAnchor(rangeOver(text, 0, text, 1200), document, null)!;
    expect(choice.exact.length).toBe(1000);
    const quote = choice.anchor.selectors[0] as { suffix: string };
    expect(quote.suffix.length).toBe(32);
    expect('woord '.repeat(300).startsWith(choice.exact + quote.suffix)).toBe(true);
  });

  it('leaves out of quote and context what the API refuses: icon glyphs and stray controls', () => {
    page('<nav><a><i></i> Home</a></nav><main><p>Prijzen en\u0085 tarieven\u0081 voor iedereen</p></main>');
    const text = document.querySelector('p')!.firstChild!;
    const data = text.textContent!;
    const choice = textAnchor(rangeOver(text, 0, text, data.indexOf(' voor')), document, null)!;
    expect(choice.exact).toBe('Prijzen en tarieven');
    expect(choice.anchor.selectors[0]).toEqual({
      type: 'TextQuoteSelector',
      exact: 'Prijzen en tarieven',
      prefix: ' Home',
      suffix: ' voor iedereen',
    });
    for (const value of Object.values(choice.anchor.selectors[0]!)) {
      expect(value).not.toMatch(/[\p{Cc}\p{Cf}\p{Co}\p{Cs}]/u);
    }
  });

  it('keeps one space where a dropped glyph stood between two', () => {
    page('<p>Klik op <i></i> om te openen</p>');
    const [before, , after] = Array.from(document.querySelector('p')!.childNodes);
    const icon = document.querySelector('i')!.firstChild!;
    const choice = textAnchor(rangeOver(before!, 5, after!, 3), document, null)!;
    expect(choice.exact).toBe('op om');
    expect(choice.anchor.selectors[0]).toMatchObject({ prefix: 'Klik ', suffix: ' te openen' });
    expect(icon.textContent).toBe('');
  });

  it('keeps in quote and context a code point the Unicode tables do not know yet', () => {
    expect(UNASSIGNED).toMatch(/^\p{Cn}$/u);
    page(`<p>Heel ${UNASSIGNED} mooi ${UNASSIGNED} gedaan</p>`);
    const text = document.querySelector('p')!.firstChild!;
    const start = text.textContent!.indexOf('mooi');
    const choice = textAnchor(rangeOver(text, start, text, start + `mooi ${UNASSIGNED}`.length), document, null)!;
    expect(choice.anchor.selectors[0]).toEqual({
      type: 'TextQuoteSelector',
      exact: `mooi ${UNASSIGNED}`,
      prefix: `Heel ${UNASSIGNED} `,
      suffix: ' gedaan',
    });
  });

  it('see no text in a selection of icon glyphs only', () => {
    page('<p><i></i> <i></i></p>');
    const p = document.querySelector('p')!;
    const range = rangeOver(p, 0, p, p.childNodes.length);
    expect(selectsText(range, document, null)).toBe(false);
    expect(textAnchor(range, document, null)).toBeNull();
  });

  it('leaves out a position beyond what the API takes', () => {
    page(`<p>${'x'.repeat(1_000_000)}</p><p>verder</p>`);
    const text = document.querySelectorAll('p')[1]!.firstChild!;
    const choice = textAnchor(rangeOver(text, 0, text, 6), document, null)!;
    expect(choice.anchor.selectors.map((selector) => selector.type)).toEqual(['TextQuoteSelector', 'CssSelector']);
  });
});

describe('findQuote', () => {
  it('finds a quote across text nodes, collapsed whitespace and format characters', () => {
    page('<p>De <b>begro\u00adting</b>\n   van dit jaar</p>');
    const range = findQuote(document.body, 'begroting van dit', document)!;
    expect(range.toString()).toBe('begro\u00adting\n   van dit');
  });

  it('prefers the place where the context matches too', () => {
    page('<p>eerst de knop hier</p><p>dan de knop daar</p>');
    const range = findQuote(document.body, 'knop', document, 'dan de ', ' daar')!;
    expect(range.startContainer).toBe(document.querySelectorAll('p')[1]!.firstChild);
  });

  it('falls back to the first place when the context is gone', () => {
    page('<p>eerst de knop hier</p><p>dan de knop daar</p>');
    const range = findQuote(document.body, 'knop', document, 'nergens ', '')!;
    expect(range.startContainer).toBe(document.querySelector('p')!.firstChild);
  });

  it('handles characters outside the BMP', () => {
    page('<p>Een 😀 hier</p>');
    expect(findQuote(document.body, '😀 hier', document)!.toString()).toBe('😀 hier');
  });

  it('finds a quote again that was cleaned of icon glyphs and controls, as the page reads', () => {
    page('<p>Klik op <i></i> om te\u0085 openen, met﻿ de knop</p>');
    const p = document.querySelector('p')!;
    const choice = textAnchor(rangeOver(p, 0, p, p.childNodes.length), document, null)!;
    expect(choice.exact).toBe('Klik op om te openen, met de knop');
    const range = findQuote(document.body, choice.exact, document)!;
    expect(range.toString()).toBe('Klik op  om te\u0085 openen, met﻿ de knop');
  });

  it('finds a quote again that holds a code point the Unicode tables do not know yet', () => {
    expect(UNASSIGNED).toMatch(/^\p{Cn}$/u);
    page(`<p>Heel <b>${UNASSIGNED}</b> mooi</p>`);
    expect(findQuote(document.body, `${UNASSIGNED} mooi`, document)?.toString()).toBe(`${UNASSIGNED} mooi`);
  });

  it('gives nothing for an empty or missing quote', () => {
    page('<p>tekst</p>');
    expect(findQuote(document.body, '', document)).toBeNull();
    expect(findQuote(document.body, 'afwezig', document)).toBeNull();
  });
});

describe('locate', () => {
  const point = (overrides: Partial<Highlight>): Highlight => ({
    k: 'point',
    s: '#a',
    x: 0.5,
    y: 0.25,
    px: 40,
    py: 900,
    q: null,
    ...overrides,
  });

  it('places a point on its element, in page coordinates', () => {
    page('<p id="a">x</p>');
    const platform = fakePlatform();
    platform.box.mockReturnValue({ left: 100, top: 10, width: 200, height: 40 });
    platform.viewport.mockReturnValue({ ...platform.viewport(), scrollX: 5, scrollY: 300 });
    // Found by the element the point names, which is what describes it.
    expect(locate(point({}), document, platform, null)).toEqual({
      x: 205,
      y: 320,
      boxes: [],
      found: document.getElementById('a'),
    });
  });

  it('falls back to page coordinates when the element is gone, hidden or unnamed', () => {
    page('<p id="a">x</p>');
    const platform = fakePlatform();
    platform.box.mockReturnValue({ left: 0, top: 0, width: 0, height: 0 });
    expect(locate(point({}), document, platform, null)).toEqual({ x: 40, y: 900, boxes: [], found: null });
    expect(locate(point({ s: '#gone' }), document, platform, null)).toEqual({ x: 40, y: 900, boxes: [], found: null });
    expect(locate(point({ s: null, x: null, y: null }), document, platform, null)).toEqual({ x: 40, y: 900, boxes: [], found: null });
    platform.box.mockReturnValue({ left: 0, top: 0, width: 10, height: 0 });
    expect(locate(point({ x: null }), document, platform, null)).toEqual({ x: 40, y: 900, boxes: [], found: null });
    expect(locate(point({ y: null }), document, platform, null)).toEqual({ x: 40, y: 900, boxes: [], found: null });
  });

  it('gives nothing when the page coordinates lie outside the document', () => {
    page('<p>x</p>');
    const platform = fakePlatform();
    expect(locate(point({ s: '#gone', px: 5000 }), document, platform, null)).toBeNull();
    expect(locate(point({ s: '#gone', py: 5000 }), document, platform, null)).toBeNull();
    expect(locate(point({ s: '#gone', px: null }), document, platform, null)).toBeNull();
    expect(locate(point({ s: '#gone', py: null }), document, platform, null)).toBeNull();
  });

  const text = (overrides: Partial<Highlight>): Highlight => ({
    k: 'text',
    s: '#b',
    x: null,
    y: null,
    px: null,
    py: null,
    q: 'de knop',
    ...overrides,
  });

  it('places a quote on its first box, in its element or anywhere on the page', () => {
    page('<p id="b">Hier staat de knop.</p><p id="c">Leeg.</p>');
    const platform = fakePlatform();
    platform.rangeBoxes.mockReturnValue([
      { left: 0, top: 0, width: 0, height: 10 },
      { left: 30, top: 40, width: 60, height: 18 },
    ]);
    platform.viewport.mockReturnValue({ ...platform.viewport(), scrollY: 100 });
    const expected = { x: 30, y: 140, boxes: [{ left: 30, top: 140, width: 60, height: 18 }] };
    for (const s of ['#b', '#c', null]) {
      const { found, ...place } = locate(text({ s }), document, platform, null)!;
      expect(place).toEqual(expected);
      // Found by the passage the quote matched on the page.
      expect(found).toBeInstanceOf(Range);
      expect((found as Range).toString()).toBe('de knop');
    }
  });

  it('falls back when the quote is gone or has no boxes', () => {
    page('<p id="b">Hier staat de knop.</p>');
    const platform = fakePlatform();
    expect(locate(text({ q: 'iets anders' }), document, platform, null)).toBeNull();
    expect(locate(text({ q: null, px: 1, py: 2 }), document, platform, null)).toEqual({ x: 1, y: 2, boxes: [], found: null });
    platform.rangeBoxes.mockReturnValue([]);
    expect(locate(text({ px: 3, py: 4 }), document, platform, null)).toEqual({ x: 3, y: 4, boxes: [], found: null });
  });
});

describe('locate, for a place found before', () => {
  const POINT: Highlight = { k: 'point', s: '#a', x: 0.5, y: 0.25, px: 40, py: 900, q: null };
  const QUOTE: Highlight = { k: 'text', s: '#b', x: null, y: null, px: 3, py: 4, q: 'de knop' };

  it('measures a point again on the element it was found by, without asking the page for it', () => {
    page('<p id="a">x</p>');
    const platform = fakePlatform();
    platform.box.mockReturnValue({ left: 100, top: 10, width: 200, height: 40 });
    const first = locate(POINT, document, platform, null)!;
    const asked = vi.spyOn(document, 'querySelector');
    // A part of the page that scrolls on its own took it up.
    platform.box.mockReturnValue({ left: 100, top: -300, width: 200, height: 40 });
    expect(locate(POINT, document, platform, null, first)).toEqual({
      x: 200,
      y: -290,
      boxes: [],
      found: document.getElementById('a'),
    });
    expect(asked).not.toHaveBeenCalled();
  });

  it('measures a quote again on the passage it matched, without reading the text of the page anew', () => {
    page('<p id="b">Hier staat de knop.</p>');
    const platform = fakePlatform();
    const first = locate(QUOTE, document, platform, null)!;
    const read = vi.spyOn(document, 'createTreeWalker');
    platform.rangeBoxes.mockReturnValue([{ left: 10, top: -200, width: 120, height: 18 }]);
    const again = locate(QUOTE, document, platform, null, first)!;
    expect(again).toEqual({ x: 10, y: -200, boxes: [{ left: 10, top: -200, width: 120, height: 18 }], found: first.found });
    expect(read).not.toHaveBeenCalled();
  });

  it('keeps a place from page coordinates where it is', () => {
    page('<p>x</p>');
    const platform = fakePlatform();
    const first = locate({ ...POINT, s: '#gone' }, document, platform, null)!;
    expect(first.found).toBeNull();
    platform.viewport.mockReturnValue({ ...platform.viewport(), scrollY: 500 });
    expect(locate({ ...POINT, s: '#gone' }, document, platform, null, first)).toBe(first);
  });

  it('searches anew when what it was found by is gone or shows nothing now', () => {
    page('<p id="a">x</p><p id="b">Hier staat de knop.</p>');
    const platform = fakePlatform();
    const point = locate(POINT, document, platform, null)!;
    const quote = locate(QUOTE, document, platform, null)!;
    // The element went and another took its id: found anew, by the selector.
    document.getElementById('a')!.remove();
    page('<p id="a">y</p><p id="b">Hier staat de knop.</p>');
    expect(locate(POINT, document, platform, null, point)!.found).toBe(document.getElementById('a'));
    // Still there, without a box: the page coordinates.
    const shown = locate(POINT, document, platform, null)!;
    platform.box.mockReturnValue({ left: 0, top: 0, width: 0, height: 0 });
    expect(locate(POINT, document, platform, null, shown)).toEqual({ x: 40, y: 900, boxes: [], found: null });
    // A passage without boxes now: searched for, and the page coordinates.
    platform.rangeBoxes.mockReturnValue([]);
    expect(locate(QUOTE, document, platform, null, quote)).toEqual({ x: 3, y: 4, boxes: [], found: null });
  });
});

describe('foundElement', () => {
  it('is the element of a point, the one holding the start of a quote, or none for page coordinates', () => {
    page('<p id="a">Hier staat <b>de knop</b>.</p>');
    const element = document.getElementById('a')!;
    expect(foundElement({ x: 0, y: 0, boxes: [], found: element })).toBe(element);
    expect(foundElement({ x: 0, y: 0, boxes: [], found: null })).toBeNull();
    const bold = element.querySelector('b')!;
    const range = rangeOver(bold.firstChild!, 0, bold.firstChild!, 2);
    expect(foundElement({ x: 0, y: 0, boxes: [], found: range })).toBe(bold);
    // A passage whose start moved onto an element, after the page changed.
    range.setStart(element, 1);
    expect(foundElement({ x: 0, y: 0, boxes: [], found: range })).toBe(element);
  });
});

describe('seenAt', () => {
  it('is a point itself, and the start of a quote at half the height of its first box, not its corner', () => {
    expect(seenAt({ x: 40, y: 900, boxes: [], found: null })).toEqual({ x: 40, y: 900 });
    // A heading whose box pokes 2 px above its line, and the next line.
    const boxes = [
      { left: 16, top: 98, width: 300, height: 36 },
      { left: 16, top: 133, width: 120, height: 36 },
    ];
    expect(seenAt({ x: 16, y: 98, boxes, found: null })).toEqual({ x: 16, y: 116 });
  });
});

describe('describeElement', () => {
  it('uses the label, the alternative text, the title or the rendered text, in that order', () => {
    page('<button aria-label="Sluit het menu">x</button><img alt="Logo van de site"><abbr title="Gemeente">gem.</abbr><p></p>');
    const [button, img, abbr, p] = Array.from(document.body.children);
    expect(describeElement(button!, 'x')).toBe('Sluit het menu');
    expect(describeElement(img!, '')).toBe('Logo van de site');
    expect(describeElement(abbr!, 'gem.')).toBe('Gemeente');
    expect(describeElement(p!, 'Onderdeel A\nHet eerste   onderdeel')).toBe('Onderdeel A Het eerste onderdeel');
    expect(describeElement(p!, '')).toBe('');
  });
});

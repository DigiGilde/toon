import { afterEach, describe, expect, it } from 'vitest';

import { textAnchor } from './anchor';
import { FRAGMENT_PREFIX, isOurFragment, parseHighlight, readFragment } from './fragment';
import { encodeConfig } from './testPlatform';
import { codePoints, MAX_QUOTE } from './text';

afterEach(() => {
  document.body.replaceChildren();
});

const POINT = { k: 'point', s: '#a', x: 0.5, y: 0, px: 10, py: 20 };

describe('parseHighlight refuses', () => {
  it.each([
    ['nothing', null],
    ['an array', [POINT]],
    ['text', 'point'],
    ['an unknown kind', { ...POINT, k: 'page' }],
    ['a selector that is not text', { ...POINT, s: 5 }],
    ['an empty selector', { ...POINT, s: '' }],
    ['a selector beyond 1000 characters', { ...POINT, s: 'p'.repeat(1001) }],
    ['a fraction above 1', { ...POINT, x: 1.5 }],
    ['a negative fraction', { ...POINT, y: -0.1 }],
    ['a fraction that is not a number', { ...POINT, x: '0.5' }],
    ['a negative coordinate', { ...POINT, px: -1 }],
    ['a coordinate beyond the API range', { ...POINT, py: 1_000_001 }],
    ['a quote beyond 1000 characters', { k: 'text', q: 'q'.repeat(1001) }],
    ['a quote beyond 1000 characters outside the basic plane', { k: 'text', q: '\u{1F600}'.repeat(1001) }],
    ['a point without a place', { k: 'point', s: '#a', x: 0.5 }],
    ['a point with half the page coordinates', { k: 'point', px: 1 }],
    ['a text without a quote', { k: 'text', s: '#a', px: 1, py: 2 }],
  ])('%s', (_name, value) => {
    expect(parseHighlight(value)).toBeNull();
  });
});

describe('parseHighlight accepts', () => {
  it('a point on an element', () => {
    expect(parseHighlight({ k: 'point', s: '#a', x: 0.5, y: 1 })).toEqual({
      k: 'point',
      s: '#a',
      x: 0.5,
      y: 1,
      px: null,
      py: null,
      q: null,
    });
  });

  it('a point at page coordinates only, with null fields', () => {
    expect(parseHighlight({ k: 'point', s: null, x: null, y: null, px: 0, py: 1_000_000 })).toMatchObject({
      px: 0,
      py: 1_000_000,
    });
  });

  it('a quote of 1000 characters outside the basic plane: the limit counts code points, as the server does', () => {
    const q = '\u{1F600}'.repeat(1000);
    expect(q.length).toBe(2000);
    expect(parseHighlight({ k: 'text', q })).toMatchObject({ q });
  });

  it('the quote the widget itself cut from a long selection with emoji in it', () => {
    document.body.textContent = 'Dit is een zin met een emoji \u{1F600} erin. '.repeat(40);
    const text = document.body.firstChild!;
    const range = document.createRange();
    range.setStart(text, 0);
    range.setEnd(text, text.textContent!.length);
    const { exact } = textAnchor(range, document, null)!;
    expect(codePoints(exact)).toBe(MAX_QUOTE);
    expect(exact.length).toBeGreaterThan(MAX_QUOTE);
    expect(parseHighlight({ k: 'text', q: exact })).toMatchObject({ q: exact });
  });

  it('a quote, ignoring keys it does not know', () => {
    expect(parseHighlight({ k: 'text', s: 'main > p', q: 'de knop', extra: true })).toEqual({
      k: 'text',
      s: 'main > p',
      x: null,
      y: null,
      px: null,
      py: null,
      q: 'de knop',
    });
  });
});

describe('the #toon-feedback= fragment', () => {
  it('is recognised by its prefix only', () => {
    expect(isOurFragment(`${FRAGMENT_PREFIX}abc`)).toBe(true);
    expect(isOurFragment('#top')).toBe(false);
    expect(isOurFragment('')).toBe(false);
  });

  it('reads a highlight', () => {
    expect(readFragment(`${FRAGMENT_PREFIX}${encodeConfig(POINT)}`)).toEqual({ ...POINT, q: null });
  });

  it('reads a quote of 1000 characters with emoji in it, as the server writes it: ASCII JSON', () => {
    const q = `${'a'.repeat(973)}${'\u{1F4C4}'.repeat(27)}`;
    const json = JSON.stringify({ k: 'text', q }).replace(
      /[\u0080-￿]/g,
      (unit) => `\\u${unit.charCodeAt(0).toString(16).padStart(4, '0')}`,
    );
    const encoded = btoa(json).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    expect(readFragment(`${FRAGMENT_PREFIX}${encoded}`)).toMatchObject({ k: 'text', q });
  });

  it.each([
    ['another fragment', '#top'],
    ['more than 2 KiB of JSON', `${FRAGMENT_PREFIX}${encodeConfig({ ...POINT, pad: 'x'.repeat(2100) })}`],
    ['characters outside base64url', `${FRAGMENT_PREFIX}a+b`],
    ['text that is not JSON', `${FRAGMENT_PREFIX}${encodeConfig('x').slice(0, -1)}`],
    ['JSON that is not a highlight', `${FRAGMENT_PREFIX}${encodeConfig({ k: 'page' })}`],
  ])('gives nothing for %s', (_name, hash) => {
    expect(readFragment(hash)).toBeNull();
  });
});

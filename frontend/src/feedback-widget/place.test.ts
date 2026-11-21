import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  CONFIG_ATTRIBUTE,
  FRAGMENT_PREFIX,
  isFragment,
  linkFragment,
  linkJson,
  MAX_ENCODED_LENGTH,
  PLACE_ATTRIBUTE,
  placeHere,
  placeOf,
  restorePlace,
} from './place';
import { encodeConfig } from './testPlatform';

afterEach(() => {
  document.head.replaceChildren();
  document.body.replaceChildren();
});

const link = (value: unknown) => `${FRAGMENT_PREFIX}${encodeConfig(value)}`;

describe('a fragment that names a place', () => {
  it.each(['#/7', '#intro', '#a.b_c~d-e/1', `#${'a'.repeat(64)}`])('keeps %s', (hash) => {
    expect(isFragment(hash)).toBe(true);
    expect(placeOf(hash)).toBe(hash);
  });

  it.each([
    ['nothing', ''],
    ['only #', '#'],
    ['no #', '/7'],
    ['a value', '#access_token=abc'],
    ['parameters', '#a&b'],
    ['a query', '#?q=1'],
    ['percent-encoding', '#%C3%BC'],
    ['a space', '#a b'],
    ['non-ASCII', '#über'],
    ['a newline at the end', '#/7\n'],
    ['a second #', '#a#b'],
    ['more than 64 characters', `#${'a'.repeat(65)}`],
  ])('drops %s', (_name, hash) => {
    expect(placeOf(hash)).toBeNull();
  });

  it('is no fragment when it is not text', () => {
    expect(isFragment(7)).toBe(false);
    expect(isFragment(null)).toBe(false);
  });
});

describe('placeHere', () => {
  it('counts a place given without a fragment, or at this one, as here', () => {
    expect(placeHere(null, '#/3', document)).toBe(true);
    expect(placeHere('#/3', '#/3', document)).toBe(true);
  });

  it('counts a place given at another view of the page as elsewhere', () => {
    expect(placeHere('#/3', '#/1', document)).toBe(false);
    expect(placeHere('#/3', '', document)).toBe(false);
  });

  it('counts a place given at a fragment that names an element as here: that fragment only scrolled', () => {
    document.body.append(Object.assign(document.createElement('h2'), { id: 'intro' }));
    expect(placeHere('#intro', '', document)).toBe(true);
    expect(placeHere('#intro', '#other', document)).toBe(true);
  });
});

describe('a #toon-feedback= link', () => {
  it('reads its JSON and the fragment in it', () => {
    expect(linkJson(link({ k: 'point', f: '#/7' }))).toEqual({ k: 'point', f: '#/7' });
    expect(linkFragment(link({ k: 'point', f: '#/7' }))).toBe('#/7');
  });

  it.each([
    ['another fragment', '#/7'],
    ['no base64url', `${FRAGMENT_PREFIX}a+b`],
    ['no JSON', `${FRAGMENT_PREFIX}${btoa('{').replace(/=+$/, '')}`],
    ['too long', `${FRAGMENT_PREFIX}${'a'.repeat(MAX_ENCODED_LENGTH + 1)}`],
  ])('reads nothing from %s', (_name, hash) => {
    expect(linkJson(hash)).toBeNull();
    expect(linkFragment(hash)).toBeNull();
  });

  it.each([
    ['JSON that is no object', 7],
    ['no fragment', { k: 'point' }],
    ['a fragment that holds a value', { k: 'point', f: '#token=1' }],
    ['a fragment that is not text', { k: 'point', f: 7 }],
  ])('puts no fragment back for %s', (_name, value) => {
    expect(linkFragment(link(value))).toBeNull();
  });
});

describe('restorePlace', () => {
  /** The widget's tag and the place script right behind it, as Toon puts them in a page. */
  function tags(config = true): HTMLScriptElement {
    const widget = document.createElement('script');
    if (config) widget.setAttribute(CONFIG_ATTRIBUTE, 'config');
    const place = document.createElement('script');
    document.head.append(widget, place);
    return place;
  }

  const address = (hash: string) => ({ hash, pathname: '/team/site/deck/', search: '?presentation' });

  it('hands a link to the widget and puts the fragment of the place in the address', () => {
    const place = tags();
    const replace = vi.fn();
    const hash = link({ k: 'point', px: 1, py: 2, f: '#/7' });
    restorePlace(place, address(hash), replace);
    expect(place.previousElementSibling!.getAttribute(PLACE_ATTRIBUTE)).toBe(hash);
    expect(replace).toHaveBeenCalledWith('/team/site/deck/?presentation#/7');
  });

  it('takes a link without a usable fragment off the address all the same', () => {
    const place = tags();
    const replace = vi.fn();
    const hash = link({ k: 'point', px: 1, py: 2, f: '#a=b' });
    restorePlace(place, address(hash), replace);
    expect(place.previousElementSibling!.getAttribute(PLACE_ATTRIBUTE)).toBe(hash);
    expect(replace).toHaveBeenCalledWith('/team/site/deck/?presentation');
  });

  it('leaves the page alone for another fragment', () => {
    const place = tags();
    const replace = vi.fn();
    restorePlace(place, address('#/3'), replace);
    expect(place.previousElementSibling!.hasAttribute(PLACE_ATTRIBUTE)).toBe(false);
    expect(replace).not.toHaveBeenCalled();
  });

  it('does nothing without the widget right in front of it', () => {
    const replace = vi.fn();
    const hash = link({ k: 'point', px: 1, py: 2, f: '#/7' });
    restorePlace(null, address(hash), replace);
    const alone = document.createElement('script');
    document.head.append(alone);
    restorePlace(alone, address(hash), replace);
    const place = tags(false);
    restorePlace(place, address(hash), replace);
    expect(place.previousElementSibling!.hasAttribute(PLACE_ATTRIBUTE)).toBe(false);
    expect(replace).not.toHaveBeenCalled();
  });
});

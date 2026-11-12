import { describe, expect, it } from 'vitest';

import { widgetEn } from '../i18n/widget.en';
import { widgetNl } from '../i18n/widget.nl';
import { detectLanguage, formatDate, formatWait, otherLanguage, pageLanguage, translator } from './i18n';

describe('detectLanguage', () => {
  it('follows the page when it is Dutch or English', () => {
    expect(detectLanguage('nl', ['en'])).toBe('nl');
    expect(detectLanguage('nl-NL', ['en'])).toBe('nl');
    expect(detectLanguage(' EN-gb ', ['nl'])).toBe('en');
  });

  it('asks the browser when the page is in another language, or says nothing', () => {
    expect(detectLanguage('fr', ['de-DE', 'nl-BE', 'en'])).toBe('nl');
    expect(detectLanguage(null, ['en-US', 'nl'])).toBe('en');
    expect(detectLanguage('', ['nl'])).toBe('nl');
  });

  it('does not take a language that only starts with the same letters', () => {
    expect(detectLanguage('nld', ['enx', 'en'])).toBe('en');
  });

  it('falls back to English when nobody asks for Dutch or English', () => {
    expect(detectLanguage('fr', ['de', 'pl'])).toBe('en');
    expect(detectLanguage(null, [])).toBe('en');
  });
});

describe('the language of words on the page', () => {
  it('is what the closest lang attribute says, on the node or around it, out of shadow roots', () => {
    document.body.innerHTML =
      '<div lang="fy"><p id="fy">Wolkom</p><p lang="en-GB" id="en"> <b>Annual report</b></p><p lang=" " id="none">x</p></div><p id="bare">y</p>';
    expect(pageLanguage(document.getElementById('fy')!.firstChild!)).toBe('fy');
    expect(pageLanguage(document.querySelector('#en b')!)).toBe('en-GB');
    // An empty one says the page does not know.
    expect(pageLanguage(document.getElementById('none')!)).toBeNull();
    const host = document.createElement('section');
    host.lang = 'de';
    const inner = document.createElement('span');
    host.attachShadow({ mode: 'open' }).append(inner);
    document.body.append(host);
    expect(pageLanguage(inner)).toBe('de');
    // Where nothing says it, the page has no language for it.
    document.documentElement.removeAttribute('lang');
    expect(pageLanguage(document.getElementById('bare')!)).toBeNull();
    document.body.innerHTML = '';
  });

  it('is marked only where it is not the widget\'s own', () => {
    expect(otherLanguage('en-GB', 'nl')).toBe('en-GB');
    expect(otherLanguage('fy', 'nl')).toBe('fy');
    expect(otherLanguage('NL-be', 'nl')).toBeNull();
    expect(otherLanguage('en', 'en')).toBeNull();
    expect(otherLanguage(null, 'nl')).toBeNull();
  });
});

describe('translator', () => {
  it('speaks the chosen language', () => {
    expect(translator('nl')('menu.close')).toBe('Sluiten');
    expect(translator('en')('menu.close')).toBe('Close');
  });

  it('fills placeholders and leaves unknown ones alone', () => {
    const t = translator('nl');
    expect(t('composer.notice.retention', { days: 30 })).toBe('Afgehandelde feedback wordt na 30 dagen verwijderd.');
    expect(t('composer.notice.retention', {})).toBe('Afgehandelde feedback wordt na {days} dagen verwijderd.');
  });
});

describe('the widget catalogue', () => {
  it('holds the same keys in both languages', () => {
    expect(Object.keys(widgetEn).sort()).toEqual(Object.keys(widgetNl).sort());
  });

  it('uses the same placeholders in both languages', () => {
    const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
    for (const key of Object.keys(widgetNl) as Array<keyof typeof widgetNl>) {
      expect(placeholders(widgetEn[key]), key).toEqual(placeholders(widgetNl[key]));
    }
  });

  it('has no em or en dash and no semicolon joining sentences', () => {
    const FORBIDDEN = new RegExp(`[${String.fromCharCode(0x2013, 0x2014)};]`);
    for (const text of [...Object.values(widgetNl), ...Object.values(widgetEn)]) {
      expect(text).not.toMatch(FORBIDDEN);
    }
  });
});

describe('formatDate', () => {
  it('writes a date and time the European way', () => {
    expect(formatDate('2026-11-03T13:30:00Z', 'nl')).toMatch(/3 nov\.? 2026/);
    expect(formatDate('2026-11-03T13:30:00Z', 'en')).toMatch(/3 Nov 2026/);
  });

  it('gives nothing for a date it cannot read', () => {
    expect(formatDate('yesterday', 'nl')).toBe('');
  });
});

describe('formatWait', () => {
  const t = translator('nl');

  it('counts seconds up to a minute and a half', () => {
    expect(formatWait(0, t)).toBe('1 seconde');
    expect(formatWait(1, t)).toBe('1 seconde');
    expect(formatWait(1.2, t)).toBe('2 seconden');
    expect(formatWait(90, t)).toBe('90 seconden');
  });

  it('counts whole minutes beyond that, rounded up', () => {
    expect(formatWait(91, t)).toBe('2 minuten');
    expect(formatWait(600, t)).toBe('10 minuten');
  });
});

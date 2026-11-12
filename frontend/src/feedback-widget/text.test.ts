import { describe, expect, it } from 'vitest';

import { UNASSIGNED } from './testPlatform';
import { checkText, cleanPageText, codePoints, collapse, cut, firstWords, MAX_COMMENT, MAX_NAME, stripFormat } from './text';

describe('checkText refuses', () => {
  it('nothing but whitespace or invisible characters', () => {
    expect(checkText('', MAX_COMMENT, true)).toEqual({ ok: false, reason: 'empty' });
    expect(checkText(' \n\t ', MAX_COMMENT, true)).toEqual({ ok: false, reason: 'empty' });
    expect(checkText('\u200b\u00ad', MAX_NAME, false)).toEqual({ ok: false, reason: 'empty' });
    // The joiners stay in text, but join nothing on their own.
    expect(checkText('\u200d', MAX_NAME, false)).toEqual({ ok: false, reason: 'empty' });
    expect(checkText(' \u200c\n\u200d ', MAX_COMMENT, true)).toEqual({ ok: false, reason: 'empty' });
  });

  it('more code points than the limit, counting an emoji as one', () => {
    expect(checkText('a'.repeat(MAX_COMMENT + 1), MAX_COMMENT, true)).toEqual({ ok: false, reason: 'long' });
    expect(checkText('😀'.repeat(MAX_NAME), MAX_NAME, false).ok).toBe(true);
    expect(checkText('😀'.repeat(MAX_NAME + 1), MAX_NAME, false)).toEqual({ ok: false, reason: 'long' });
  });

  it.each([
    ['a control character', 'a\u0007b', true],
    ['a carriage return in a block', 'a\rb', true],
    ['a newline in a name', 'Jan\nJansen', false],
    ['a tab in a name', 'Jan\tJansen', false],
    ['a lone surrogate', 'a\ud800b', true],
    ['a private-use character', 'a\ue000b', true],
  ])('%s', (_name, text, multiline) => {
    expect(checkText(text, MAX_COMMENT, multiline)).toEqual({ ok: false, reason: 'chars' });
  });
});

describe('checkText accepts', () => {
  it('a comment with newlines and tabs, trimmed', () => {
    expect(checkText('  Klopt dit?\n\tJa.  ', MAX_COMMENT, true)).toEqual({ ok: true, value: 'Klopt dit?\n\tJa.' });
  });

  it('text with format characters, which it removes', () => {
    // A soft hyphen, a zero-width space, a word joiner and a byte order mark.
    expect(checkText('be\u00adgro\u200bting\u2060 \ufeff👍', MAX_COMMENT, true)).toEqual({
      ok: true,
      value: 'begroting 👍',
    });
    // Bidi controls and a tag character, which the API refuses.
    for (const control of ['\u200e', '\u200f', '\u061c', '\u202a', '\u202e', '\u2066', '\u2069', '\u{e0041}']) {
      expect(checkText(`Jan${control}sen`, MAX_NAME, false)).toEqual({ ok: true, value: 'Jansen' });
    }
  });

  it('an emoji joined with U+200D and a word written with U+200C, keeping both', () => {
    const technologist = '\u{1f469}\u200d\u{1f4bb}';
    const facepalming = '\u{1f926}\u200d\u2642\ufe0f';
    const persian = '\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645';
    const comment = `Opgelost, dank je! ${technologist} ${facepalming}`;
    expect(checkText(comment, MAX_COMMENT, true)).toEqual({ ok: true, value: comment });
    expect(checkText(` ${persian} `, MAX_NAME, false)).toEqual({ ok: true, value: persian });
    expect(checkText('a\u200d\u202eb', MAX_COMMENT, true)).toEqual({ ok: true, value: 'a\u200db' });
  });

  it('a code point the Unicode tables do not know yet, as an emoji newer than them', () => {
    expect(UNASSIGNED).toMatch(/^\p{Cn}$/u);
    expect(checkText(`Top ${UNASSIGNED}`, MAX_COMMENT, true)).toEqual({ ok: true, value: `Top ${UNASSIGNED}` });
    expect(checkText(`Jan ${UNASSIGNED}`, MAX_NAME, false)).toEqual({ ok: true, value: `Jan ${UNASSIGNED}` });
  });

  it('exactly the limit', () => {
    expect(checkText('a'.repeat(MAX_COMMENT), MAX_COMMENT, true).ok).toBe(true);
  });
});

describe('helpers', () => {
  it('counts code points, not UTF-16 units', () => {
    expect(codePoints('a😀b')).toBe(3);
  });

  it('cuts at code points, never half a pair', () => {
    expect(cut('😀😀😀', 2)).toBe('😀😀');
  });

  it('collapses whitespace runs and drops format characters but the joiners, without trimming', () => {
    expect(collapse(' a \n\t b\u200b ')).toBe(' a b ');
    expect(stripFormat('a\u202eb')).toBe('ab');
    expect(stripFormat('\u{1f469}\u200d\u{1f4bb} \u0645\u06cc\u200c\u062e')).toBe('\u{1f469}\u200d\u{1f4bb} \u0645\u06cc\u200c\u062e');
    expect(collapse('a\u200d  b')).toBe('a\u200d b');
  });

  it('cleans page text of everything the API refuses, before collapsing whitespace', () => {
    // An icon-font glyph (private use), a C1 control that \s misses, a lone
    // surrogate, a soft hyphen and a byte order mark (format, and \s to JS) all
    // go. An unassigned code point stays. Tab and newline are whitespace and
    // collapse.
    expect(UNASSIGNED).toMatch(/^\p{Cn}$/u);
    expect(cleanPageText(`a\ue900b\u0085c\ud800d\u00ade\tf\ufeffg${UNASSIGNED}h\n\ni`)).toBe(
      `abcde fg${UNASSIGNED}h i`,
    );
    // A glyph between two spaces leaves one space, as on screen.
    expect(cleanPageText('Klik op \ue900 om')).toBe('Klik op om');
    expect(cleanPageText(' \u{1F600} ')).toBe(' \u{1F600} ');
    // Quotes stored before the joiners were allowed hold none: the page is
    // read without them still, so those quotes keep matching.
    expect(cleanPageText('\u{1f469}\u200d\u{1f4bb} \u0645\u06cc\u200c\u062e')).toBe('\u{1f469}\u{1f4bb} \u0645\u06cc\u062e');
  });

  it('shortens a comment to its first words', () => {
    expect(firstWords('De knop valt over de tekst heen op een smal scherm')).toBe(
      'De knop valt over de tekst heen op...',
    );
    expect(firstWords('Kort')).toBe('Kort');
    expect(firstWords('  twee\n woorden ')).toBe('twee woorden');
  });

  it('cuts first words that are too long all the same', () => {
    expect(firstWords('x'.repeat(100))).toBe(`${'x'.repeat(60)}...`);
  });
});

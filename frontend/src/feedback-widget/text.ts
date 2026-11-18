/**
 * Cleaning and checking what a giver types, mirroring the widget API: a
 * comment or reply is 1..5000 characters after trimming, a name 1..100, and
 * neither may hold control characters (a comment or reply keeps newline and
 * tab), lone surrogates or private-use code points. Lengths count code
 * points, as the server does. Unassigned code points pass, here and on the
 * server: a giver's keyboard can offer emoji newer than either's Unicode tables.
 *
 * Invisible format characters (Unicode Cf: soft hyphens, zero-width spaces,
 * bidi marks) are removed rather than refused: text pasted from a page often
 * carries them and they change nothing a reader sees. The zero-width joiner
 * and non-joiner stay, as on the server: they hold an emoji sequence together
 * and shape the letters of a word in Persian or Hindi. Text of nothing but
 * those two and whitespace is empty.
 *
 * Page text an anchor quotes is the page's, not the giver's: whatever of it
 * the API refuses is removed, so an icon-font glyph or a stray control
 * character near a selection cannot make the remark unsendable.
 */

export const MAX_COMMENT = 5000;
export const MAX_NAME = 100;
export const MAX_QUOTE = 1000;
export const MAX_AFFIX = 32;
export const MAX_TITLE = 300;

/** Format characters but the zero-width non-joiner and joiner (U+200C, U+200D). */
const FORMAT_CHARS = /(?![\u200c\u200d])\p{Cf}/gu;
const JOINERS = /[\u200c\u200d]/gu;
const WHITESPACE_RUNS = /\s+/g;
const FORBIDDEN_IN_BLOCK = /[^\P{Cc}\n\t]|[\p{Cs}\p{Co}]/u;
const FORBIDDEN_IN_LINE = /[\p{Cc}\p{Cs}\p{Co}]/u;
/** What the API refuses in page text and whitespace does not cover: format,
 * private-use and lone surrogate code points, and the control characters that
 * are not whitespace (U+0085 among them, which `\s` misses). */
export const UNSENDABLE_CHAR = /[\p{Cf}\p{Co}\p{Cs}]|[^\P{Cc}\s]/u;
const UNSENDABLE_CHARS = new RegExp(UNSENDABLE_CHAR.source, 'gu');

export function codePoints(text: string): number {
  let count = 0;
  for (const _ of text) count += 1;
  return count;
}

/** The first `max` code points, never half a surrogate pair. */
export function cut(text: string, max: number): string {
  return Array.from(text).slice(0, max).join('');
}

export function stripFormat(text: string): string {
  return text.replace(FORMAT_CHARS, '');
}

/** Format characters out, whitespace runs collapsed to one space, not trimmed. */
export function collapse(text: string): string {
  return stripFormat(text).replace(WHITESPACE_RUNS, ' ');
}

/** Page text as an anchor quotes it: what the API refuses out, whitespace runs
 * collapsed to one space, not trimmed. Re-anchoring reads the page the same way. */
export function cleanPageText(text: string): string {
  return text.replace(UNSENDABLE_CHARS, '').replace(WHITESPACE_RUNS, ' ');
}

export type Check =
  | { ok: true; value: string }
  | { ok: false; reason: 'empty' | 'long' | 'chars' };

export function checkText(raw: string, max: number, multiline: boolean): Check {
  const value = stripFormat(raw).trim();
  if (value.replace(JOINERS, '').trim().length === 0) return { ok: false, reason: 'empty' };
  if (codePoints(value) > max) return { ok: false, reason: 'long' };
  if ((multiline ? FORBIDDEN_IN_BLOCK : FORBIDDEN_IN_LINE).test(value)) {
    return { ok: false, reason: 'chars' };
  }
  return { ok: true, value };
}

/** A short handle for a comment, as the accessible name of its pin. */
export function firstWords(text: string, words = 8, maxLength = 60): string {
  const all = collapse(text).trim().split(' ');
  let short = all.slice(0, words).join(' ');
  if (codePoints(short) > maxLength) short = cut(short, maxLength);
  return short.length < collapse(text).trim().length ? `${short}...` : short;
}

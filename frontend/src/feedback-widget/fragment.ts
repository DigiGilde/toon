/**
 * The highlight form of a stored place: `{k, s, x, y, w, h, px, py, pw, ph,
 * q, f}`, sent by the server as `#toon-feedback=<base64url JSON>` (the admin's
 * "Show on the site") and as the anchor of an item under "My feedback".
 * Only used to find the place again; nothing from it is ever rendered as text.
 */
import { MAX_COORDINATE, MAX_SELECTOR } from './anchor';
import { FRAGMENT_PREFIX, isFragment, linkJson } from './place';
import { codePoints, MAX_QUOTE } from './text';
import type { Highlight } from './types';

export { FRAGMENT_PREFIX };

const ABSENT = Symbol('absent');

function field<T>(
  record: Record<string, unknown>,
  name: string,
  valid: (value: unknown) => value is T,
): T | null | typeof ABSENT {
  const value = record[name];
  if (value === undefined || value === null) return null;
  return valid(value) ? value : ABSENT;
}

/** Counted in code points, as the widget cuts a quote and the server checks it. */
const text = (max: number) => (value: unknown): value is string =>
  typeof value === 'string' && value.length > 0 && codePoints(value) <= max;
const fraction = (value: unknown): value is number =>
  typeof value === 'number' && value >= 0 && value <= 1;
const coordinate = (value: unknown): value is number =>
  typeof value === 'number' && value >= 0 && value <= MAX_COORDINATE;

export function parseHighlight(value: unknown): Highlight | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const k = record.k;
  if (k !== 'point' && k !== 'area' && k !== 'text') return null;
  const s = field(record, 's', text(MAX_SELECTOR));
  const x = field(record, 'x', fraction);
  const y = field(record, 'y', fraction);
  const w = field(record, 'w', fraction);
  const h = field(record, 'h', fraction);
  const px = field(record, 'px', coordinate);
  const py = field(record, 'py', coordinate);
  const pw = field(record, 'pw', coordinate);
  const ph = field(record, 'ph', coordinate);
  const q = field(record, 'q', text(MAX_QUOTE));
  const f = field(record, 'f', isFragment);
  if ([s, x, y, w, h, px, py, pw, ph, q, f].includes(ABSENT)) return null;
  const highlight = { k, s, x, y, w, h, px, py, pw, ph, q, f } as Highlight;
  return placeable(highlight) ? highlight : null;
}

/** Whether a highlight holds enough to find its place: an element with the
 * place in it, or the place on the page, or for a text its quote. */
function placeable(highlight: Highlight): boolean {
  const known = (...values: Array<number | null>) => values.every((value) => value !== null);
  const { s, x, y, w, h, px, py, pw, ph } = highlight;
  if (highlight.k === 'text') return highlight.q !== null;
  if (highlight.k === 'point') return (s !== null && known(x, y)) || known(px, py);
  return (s !== null && known(x, y, w, h)) || known(px, py, pw, ph);
}

export function isOurFragment(hash: string): boolean {
  return hash.startsWith(FRAGMENT_PREFIX);
}

export function readFragment(hash: string): Highlight | null {
  return parseHighlight(linkJson(hash));
}

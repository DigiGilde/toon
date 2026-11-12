/**
 * The highlight form of a stored place: `{k, s, x, y, px, py, q}`, sent by the
 * server as `#toon-feedback=<base64url JSON>` (the admin's "Bekijk op de
 * pagina") and as the anchor of an item under Mijn feedback. Only used to find
 * the place again; nothing from it is ever rendered as text.
 */
import { MAX_COORDINATE, MAX_SELECTOR } from './anchor';
import { decodeBase64Url } from './config';
import { codePoints, MAX_QUOTE } from './text';
import type { Highlight } from './types';

export const FRAGMENT_PREFIX = '#toon-feedback=';

/** 2 KiB of JSON, as base64url without padding. */
const MAX_JSON_BYTES = 2048;
const MAX_ENCODED_LENGTH = Math.ceil((MAX_JSON_BYTES * 4) / 3);

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
  if (k !== 'point' && k !== 'text') return null;
  const s = field(record, 's', text(MAX_SELECTOR));
  const x = field(record, 'x', fraction);
  const y = field(record, 'y', fraction);
  const px = field(record, 'px', coordinate);
  const py = field(record, 'py', coordinate);
  const q = field(record, 'q', text(MAX_QUOTE));
  if ([s, x, y, px, py, q].includes(ABSENT)) return null;
  const highlight = { k, s, x, y, px, py, q } as Highlight;
  const placeable =
    k === 'point'
      ? (highlight.s !== null && highlight.x !== null && highlight.y !== null) ||
        (highlight.px !== null && highlight.py !== null)
      : highlight.q !== null;
  return placeable ? highlight : null;
}

export function isOurFragment(hash: string): boolean {
  return hash.startsWith(FRAGMENT_PREFIX);
}

export function readFragment(hash: string): Highlight | null {
  if (!isOurFragment(hash)) return null;
  const encoded = hash.slice(FRAGMENT_PREFIX.length);
  if (encoded.length > MAX_ENCODED_LENGTH) return null;
  const json = decodeBase64Url(encoded);
  if (json === null) return null;
  try {
    return parseHighlight(JSON.parse(json));
  } catch {
    return null;
  }
}

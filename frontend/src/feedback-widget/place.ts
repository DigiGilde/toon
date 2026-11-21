/**
 * Where on a page feedback was given, past its path: the fragment of the
 * address, such as `#/7` for slide 7 of a slide deck or `#intro`. Only a
 * fragment that names a place is kept, never one that holds a value
 * (`#access_token=...`, `#?q=`, anything percent-encoded): letters, digits
 * and `/_.~-`, at most 64 of them. The server holds a fragment to the same
 * pattern (serving/feedback_input.py:FRAGMENT).
 *
 * Also the small script Toon puts right behind the widget's tag
 * (feedback-place/main.ts). The widget runs deferred, after the page's own
 * scripts, and a deck reads its slide from the address once, as it starts:
 * this script runs before them, so a #toon-feedback= link opens the page
 * where the place is.
 */
import { decodeBase64Url } from './config';

/** The attribute that carries the widget's configuration on its script tag. */
export const CONFIG_ATTRIBUTE = 'data-toon-feedback';
/** The attribute the place script hands a #toon-feedback= link over in. */
export const PLACE_ATTRIBUTE = 'data-toon-feedback-place';
export const FRAGMENT_PREFIX = '#toon-feedback=';
export const FRAGMENT_PATTERN = /^#[A-Za-z0-9/_.~-]{1,64}$/;

/** 2 KiB of JSON, as base64url without padding. */
const MAX_JSON_BYTES = 2048;
export const MAX_ENCODED_LENGTH = Math.ceil((MAX_JSON_BYTES * 4) / 3);

export function isFragment(value: unknown): value is string {
  return typeof value === 'string' && FRAGMENT_PATTERN.test(value);
}

/** The fragment to send with feedback given at this address, or null. */
export function placeOf(hash: string): string | null {
  return isFragment(hash) ? hash : null;
}

/**
 * Whether what was given at `fragment` can show at an address with `hash`:
 * given without one, given here, or given at a fragment that names an
 * element of the page. That one only scrolled the page, and the place is
 * found by its own selector; a fragment such as `#/7` is a view of its own,
 * a slide the page shows only while the address says so.
 */
export function placeHere(fragment: string | null, hash: string, doc: Document): boolean {
  return fragment === null || fragment === hash || doc.getElementById(fragment.slice(1)) !== null;
}

/** The JSON a #toon-feedback= link carries, or null when it does not read. */
export function linkJson(hash: string): unknown {
  if (!hash.startsWith(FRAGMENT_PREFIX)) return null;
  const encoded = hash.slice(FRAGMENT_PREFIX.length);
  if (encoded.length > MAX_ENCODED_LENGTH) return null;
  const json = decodeBase64Url(encoded);
  if (json === null) return null;
  try {
    return JSON.parse(json) as unknown;
  } catch {
    return null;
  }
}

/** The fragment a #toon-feedback= link puts back in the address, or null. */
export function linkFragment(hash: string): string | null {
  const json = linkJson(hash);
  if (typeof json !== 'object' || json === null) return null;
  const fragment = (json as Record<string, unknown>).f;
  return isFragment(fragment) ? fragment : null;
}

export interface PlaceAddress {
  readonly hash: string;
  readonly pathname: string;
  readonly search: string;
}

/**
 * Run as the page starts, from the script right behind the widget's tag: a
 * #toon-feedback= link goes to the widget's tag, and the address becomes the
 * page with the place's fragment, before the page's own scripts read it. The
 * widget shows the place once it runs. Anything else leaves the page alone.
 */
export function restorePlace(
  script: Element | null,
  address: PlaceAddress,
  replace: (url: string) => void,
): void {
  const widget = script?.previousElementSibling ?? null;
  if (widget === null || !widget.hasAttribute(CONFIG_ATTRIBUTE)) return;
  const hash = address.hash;
  if (!hash.startsWith(FRAGMENT_PREFIX)) return;
  widget.setAttribute(PLACE_ATTRIBUTE, hash);
  replace(`${address.pathname}${address.search}${linkFragment(hash) ?? ''}`);
}

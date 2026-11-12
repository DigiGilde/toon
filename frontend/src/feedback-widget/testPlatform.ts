/**
 * Test support: a stand-in for the browser adapter, a configuration, and
 * answers of the widget API. Never part of the bundle (main.ts does not
 * import it). Every stand-in is a `vi.fn()` with a canned answer, so a test
 * changes exactly the call it is about.
 */
import { type Mock, vi } from 'vitest';

import { SCREENSHOT_LIMIT } from './screenshot';
import type { Config, Platform } from './types';

export type FakePlatform = { [K in keyof Platform]: Mock<Platform[K]> };

export function fakePlatform(): FakePlatform {
  return {
    currentScript: vi.fn<Platform['currentScript']>().mockReturnValue(null),
    elementAt: vi.fn<Platform['elementAt']>().mockReturnValue(null),
    box: vi.fn<Platform['box']>().mockReturnValue({ left: 0, top: 0, width: 200, height: 100 }),
    keyboardFocus: vi.fn<Platform['keyboardFocus']>().mockReturnValue(true),
    visibleText: vi.fn<Platform['visibleText']>().mockReturnValue(''),
    rangeBoxes: vi.fn<Platform['rangeBoxes']>().mockReturnValue([{ left: 10, top: 20, width: 120, height: 18 }]),
    viewport: vi.fn<Platform['viewport']>().mockReturnValue({
      width: 1024,
      height: 768,
      visualHeight: 768,
      visualOffsetTop: 0,
      dpr: 2,
      scrollX: 0,
      scrollY: 0,
      docWidth: 1024,
      docHeight: 3000,
    }),
    onViewportChange: vi.fn<Platform['onViewportChange']>().mockReturnValue(vi.fn()),
    observeLayout: vi.fn<Platform['observeLayout']>().mockReturnValue(vi.fn()),
    nextFrame: vi.fn<Platform['nextFrame']>((callback) => callback()),
    clipBox: vi.fn<Platform['clipBox']>().mockReturnValue(null),
    opaqueOrigin: vi.fn<Platform['opaqueOrigin']>().mockReturnValue(false),
    scrollToPage: vi.fn<Platform['scrollToPage']>(),
    scrollers: vi.fn<Platform['scrollers']>().mockReturnValue([]),
    scrollElementTo: vi.fn<Platform['scrollElementTo']>(),
    capture: vi.fn<Platform['capture']>().mockResolvedValue({
      image: 'capture',
      width: 1024,
      height: 768,
      scale: 1,
      placeholders: false,
    }),
    draw: vi.fn<Platform['draw']>().mockReturnValue('picture'),
    encode: vi.fn<Platform['encode']>().mockResolvedValue(new Blob(['jpeg'], { type: 'image/jpeg' })),
    show: vi.fn<Platform['show']>().mockReturnValue(document.createElement('canvas')),
    objectUrl: vi.fn<Platform['objectUrl']>().mockReturnValue('blob:screenshot'),
    revokeObjectUrl: vi.fn<Platform['revokeObjectUrl']>(),
    copyText: vi.fn<Platform['copyText']>().mockResolvedValue(true),
    submitForm: vi.fn<Platform['submitForm']>(),
    replaceUrl: vi.fn<Platform['replaceUrl']>(),
    fetch: vi.fn<Platform['fetch']>(),
    languages: vi.fn<Platform['languages']>().mockReturnValue(['nl-NL', 'nl']),
    userAgent: vi.fn<Platform['userAgent']>().mockReturnValue('Mozilla/5.0 Chrome/150.0'),
    coarsePointer: vi.fn<Platform['coarsePointer']>().mockReturnValue(false),
  };
}

/** Planes 4 to 13 hold no character yet: this stands in for an emoji newer
 * than the Unicode tables of the browser or the server. */
export const UNASSIGNED = '\u{50000}';

export const ACCOUNT_URL = '/-/feedback/account?returnTo=%2Fteam%2Fsite%2Fdocs%2F';
export const SHOW_URL = '/-/feedback/show?returnTo=%2Fteam%2Fsite%2F';

export function fakeConfig(overrides: Partial<Config> = {}): Config {
  return {
    token: 'sealed-token',
    api: '/-/feedback/v1',
    identity: 'name',
    account: false,
    accountUrl: ACCOUNT_URL,
    stopUrl: '/-/feedback/account/stop?returnTo=%2Fteam%2Fsite%2Fdocs%2F',
    returnTo: '/team/site/docs/',
    hideUrl: '/-/feedback/hide',
    showUrl: SHOW_URL,
    privacyUrl: 'https://beheer.toon.test/-/privacy',
    retentionDays: 90,
    screenshotLimit: SCREENSHOT_LIMIT,
    ...overrides,
  };
}

/** The configuration as the script tag carries it: base64url of UTF-8 JSON. */
export function encodeConfig(value: unknown): string {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

export function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

export function problem(
  status: number,
  code: string,
  detail: string | null = null,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify({ type: 'about:blank', title: 'Error', status, detail, code }), {
    status,
    headers: { 'Content-Type': 'application/problem+json', ...headers },
  });
}

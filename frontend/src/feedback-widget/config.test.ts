import { describe, expect, it } from 'vitest';

import { decodeBase64Url, isLocalPath, parseConfig } from './config';
import { SCREENSHOT_LIMIT } from './screenshot';
import { ACCOUNT_URL, encodeConfig, SHOW_URL } from './testPlatform';

const VALID = {
  v: 1,
  token: 'AbC_123-xyz',
  api: '/-/feedback/v1',
  identity: 'anonymous',
  account: false,
  accountUrl: ACCOUNT_URL,
  hideUrl: '/-/feedback/hide',
  showUrl: SHOW_URL,
  privacyUrl: 'https://beheer.toon.test/-/privacy',
  screenshotMaxBytes: 1048576,
};

function parse(overrides: Record<string, unknown>): ReturnType<typeof parseConfig> {
  return parseConfig(encodeConfig({ ...VALID, ...overrides }));
}

describe('parseConfig refuses', () => {
  it.each([
    ['nothing', null],
    ['an empty attribute', ''],
    ['an attribute beyond 16 KiB', 'A'.repeat(16385)],
    ['characters outside base64url', 'abc+def/'],
    ['base64 of an impossible length', 'A'],
    ['bytes that are not UTF-8', btoa('\u00ff\u00fe').replace(/=+$/, '')],
    ['text that is not JSON', encodeConfig('x').slice(0, -2)],
  ])('%s', (_name, raw) => {
    expect(parseConfig(raw)).toBeNull();
  });

  it.each([
    ['an array', [VALID]],
    ['null', null],
    ['a string', 'config'],
  ])('JSON that is %s instead of an object', (_name, value) => {
    expect(parseConfig(encodeConfig(value))).toBeNull();
  });

  it.each([
    ['another version', { v: 2 }],
    ['no version', { v: undefined }],
    ['a token that is not text', { token: 42 }],
    ['an empty token', { token: '' }],
    ['a token with characters a header cannot carry', { token: 'abc def' }],
    ['an API outside /-/feedback/', { api: '/-/api/v1' }],
    ['an API on another host', { api: 'https://evil.test/-/feedback/v1' }],
    ['an API with a trailing slash', { api: '/-/feedback/v1/' }],
    ['an API that is not text', { api: null }],
    ['an unknown identity', { identity: 'everyone' }],
    ['an account flag that is not a boolean', { account: 'yes' }],
    ['a hide address with a query', { hideUrl: '/-/feedback/hide?x=1' }],
    ['a hide address elsewhere', { hideUrl: '/elsewhere' }],
    ['a hide address that is not text', { hideUrl: 1 }],
    ['a screenshot limit of 0', { screenshotMaxBytes: 0 }],
    ['a negative screenshot limit', { screenshotMaxBytes: -1 }],
    ['a screenshot limit with a fraction', { screenshotMaxBytes: 1.5 }],
    ['a screenshot limit that is text', { screenshotMaxBytes: '1048576' }],
    ['a screenshot limit of null', { screenshotMaxBytes: null }],
    ['no screenshot limit', { screenshotMaxBytes: undefined }],
    ['an account address that is not text', { accountUrl: 3 }],
    ['an account address on another path', { accountUrl: '/-/feedback/hide?returnTo=%2Fa%2Fb%2F' }],
    ['an account address below the account path', { accountUrl: '/-/feedback/account?/x?returnTo=%2Fa%2F' }],
    ['an account address without returnTo', { accountUrl: '/-/feedback/account?x=1' }],
    ['an account address that leaves the host', { accountUrl: '/-/feedback/account?returnTo=%2F%2Fevil.test' }],
    ['an account address with a backslash', { accountUrl: '/-/feedback/account?returnTo=%2Fa\\b' }],
    ['a show address elsewhere', { showUrl: '/-/feedback/hide?returnTo=%2Fa%2F' }],
    ['a show address without returnTo', { showUrl: '/-/feedback/show?x=1' }],
    ['a privacy address that is not a URL', { privacyUrl: 'not a url' }],
    ['a privacy address with a script scheme', { privacyUrl: 'javascript:alert(1)' }],
    ['a privacy address that is not text', { privacyUrl: false }],
    ['an editor address elsewhere', { editorUrl: '/team/site/editor.0123456789abcdef.html' }],
    ['an editor address on another host', { editorUrl: 'https://evil.test/-/feedback/editor.0123456789abcdef.html' }],
    ['an editor address of the script', { editorUrl: '/-/feedback/editor.0123456789abcdef.js' }],
    ['an editor address with a query', { editorUrl: '/-/feedback/editor.0123456789abcdef.html?x=1' }],
    ['an editor address with a short hash', { editorUrl: '/-/feedback/editor.0123.html' }],
    ['an editor address that is not text', { editorUrl: 7 }],
  ])('%s', (_name, overrides) => {
    expect(parse(overrides)).toBeNull();
  });
});

describe('parseConfig accepts', () => {
  it('a full configuration, and derives the addresses it needs', () => {
    expect(parse({})).toEqual({
      token: 'AbC_123-xyz',
      api: '/-/feedback/v1',
      identity: 'anonymous',
      account: false,
      accountUrl: ACCOUNT_URL,
      stopUrl: '/-/feedback/account/stop?returnTo=%2Fteam%2Fsite%2Fdocs%2F',
      returnTo: '/team/site/',
      hideUrl: '/-/feedback/hide',
      showUrl: SHOW_URL,
      privacyUrl: 'https://beheer.toon.test/-/privacy',
      screenshotLimit: 1048576,
      editorUrl: null,
    });
  });

  it.each([
    ['left out', undefined],
    ['null', null],
  ])('a configuration whose way to the consent page and show link are %s, as on a page without a way back', (_name, value) => {
    const config = parse({ accountUrl: value, showUrl: value });
    expect(config).toMatchObject({ accountUrl: null, stopUrl: null, returnTo: null, showUrl: null });
    expect(config?.hideUrl).toBe('/-/feedback/hide');
  });

  it('either one without the other', () => {
    expect(parse({ accountUrl: undefined })).toMatchObject({ accountUrl: null, stopUrl: null, showUrl: SHOW_URL });
    expect(parse({ showUrl: undefined })).toMatchObject({ accountUrl: ACCOUNT_URL, returnTo: null, showUrl: null });
  });

  it('the address of the editor frame, when Toon has one', () => {
    const editorUrl = '/-/feedback/editor.0123456789abcdef.html';
    expect(parse({ editorUrl })?.editorUrl).toBe(editorUrl);
    expect(parse({ editorUrl: null })?.editorUrl).toBeNull();
  });

  it('a screenshot limit, never above the widget limit', () => {
    expect(parse({ screenshotMaxBytes: 8 * 1024 * 1024 })?.screenshotLimit).toBe(SCREENSHOT_LIMIT);
    expect(parse({ screenshotMaxBytes: SCREENSHOT_LIMIT - 1 })?.screenshotLimit).toBe(SCREENSHOT_LIMIT - 1);
    expect(parse({ screenshotMaxBytes: 1 })?.screenshotLimit).toBe(1);
  });

  it('a plain http privacy address, as a local setup has', () => {
    expect(parse({ privacyUrl: 'http://localhost:8000/-/privacy' })?.privacyUrl).toBe(
      'http://localhost:8000/-/privacy',
    );
  });

  it('JSON with text outside ASCII', () => {
    expect(parse({ token: 'abc', extra: 'café ✓' })?.token).toBe('abc');
  });
});

describe('decodeBase64Url', () => {
  it('decodes without padding and with the URL alphabet', () => {
    expect(decodeBase64Url(encodeConfig({ a: '???>>>' }))).toBe('{"a":"???>>>"}');
  });
});

describe('isLocalPath', () => {
  it.each([
    ['/team/site/', true],
    ['/', true],
    ['//evil.test/', false],
    ['team/site/', false],
    ['/a b/', false],
    ['/a\\b', false],
  ])('%s is %s', (value, expected) => {
    expect(isLocalPath(value)).toBe(expected);
  });
});

/**
 * The configuration Toon puts in the script tag: base64url (no padding) of a
 * UTF-8 JSON object. A site's own scripts can rewrite the attribute before the
 * widget reads it, so every address must be a path on this host under
 * /-/feedback/ (or, for the privacy statement, an http(s) URL), and anything
 * else turns the widget off instead of following it.
 */
import { SCREENSHOT_LIMIT } from './screenshot';
import type { Config, Identity } from './types';

const IDENTITIES: readonly Identity[] = ['account', 'name', 'anonymous'];

/** Far above any real configuration; a guard against a page that stuffs it. */
const MAX_RAW_LENGTH = 16384;

const TOKEN_RE = /^[A-Za-z0-9_-]{1,8192}$/;
const API_RE = /^\/-\/feedback\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*$/;
const HIDE_RE = /^\/-\/feedback\/[A-Za-z0-9_-]+$/;
const ACCOUNT_PREFIX = '/-/feedback/account?';
const SHOW_PREFIX = '/-/feedback/show?';
const STOP_PATH = '/-/feedback/account/stop';
const PLACEHOLDER_ORIGIN = 'https://toon.invalid';

export function decodeBase64Url(text: string): string | null {
  if (!/^[A-Za-z0-9_-]*$/.test(text)) return null;
  const base64 = text.replace(/-/g, '+').replace(/_/g, '/');
  const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
  try {
    const binary = atob(padded);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}

/** A path on this host: one leading slash, no backslash, no whitespace. */
export function isLocalPath(value: string): boolean {
  return /^\/(?!\/)[^\s\\]*$/.test(value);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function localUrlWithReturnTo(value: unknown, prefix: string): URL | null {
  if (typeof value !== 'string' || !value.startsWith(prefix) || !isLocalPath(value)) return null;
  const url = new URL(value, PLACEHOLDER_ORIGIN);
  const returnTo = url.searchParams.get('returnTo');
  return returnTo !== null && isLocalPath(returnTo) ? url : null;
}

function httpUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

export function parseConfig(raw: string | null): Config | null {
  if (raw === null || raw.length === 0 || raw.length > MAX_RAW_LENGTH) return null;
  const json = decodeBase64Url(raw);
  if (json === null) return null;
  let data: unknown;
  try {
    data = JSON.parse(json);
  } catch {
    return null;
  }
  if (!isObject(data) || data.v !== 1) return null;

  // Optional: without it the widget's own limit holds.
  const { token, api, identity, account, hideUrl, retentionDays, screenshotMaxBytes = SCREENSHOT_LIMIT } = data;
  if (typeof token !== 'string' || !TOKEN_RE.test(token)) return null;
  if (typeof api !== 'string' || !API_RE.test(api)) return null;
  if (!IDENTITIES.includes(identity as Identity)) return null;
  if (typeof account !== 'boolean') return null;
  if (typeof hideUrl !== 'string' || !HIDE_RE.test(hideUrl)) return null;
  if (
    typeof retentionDays !== 'number' ||
    !Number.isInteger(retentionDays) ||
    retentionDays < 0 ||
    retentionDays > 100000
  ) {
    return null;
  }
  if (typeof screenshotMaxBytes !== 'number' || !Number.isInteger(screenshotMaxBytes) || screenshotMaxBytes < 1) {
    return null;
  }
  const accountUrl = localUrlWithReturnTo(data.accountUrl, ACCOUNT_PREFIX);
  if (accountUrl === null) return null;
  const showUrl = localUrlWithReturnTo(data.showUrl, SHOW_PREFIX);
  if (showUrl === null) return null;
  const privacyUrl = httpUrl(data.privacyUrl);
  if (privacyUrl === null) return null;

  return {
    token,
    api,
    identity: identity as Identity,
    account,
    accountUrl: data.accountUrl as string,
    stopUrl: `${STOP_PATH}${accountUrl.search}`,
    returnTo: accountUrl.searchParams.get('returnTo') as string,
    hideUrl,
    showUrl: data.showUrl as string,
    privacyUrl,
    retentionDays,
    screenshotLimit: Math.min(screenshotMaxBytes, SCREENSHOT_LIMIT),
  };
}

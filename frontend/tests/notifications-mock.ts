/**
 * The notification endpoints (`feedback-email`, `feedback-webhook`) as a
 * `fetch` stub in front of another one, for the tests of the components that
 * use them. Everything else goes through to `inner`, so a page test keeps its
 * mock backend. State lives in the returned object: a test sets it up, and
 * reads the bodies of the writes afterwards.
 */
import type { FeedbackEmailSetting, FeedbackWebhook } from '@/api/types';

export interface NotificationsMock {
  fetch: typeof fetch;
  /** The email choice per level, keyed `me`, `group` and `site`. */
  email: Record<'me' | 'group' | 'site', FeedbackEmailSetting>;
  webhook: Record<'group' | 'site', FeedbackWebhook>;
  /** Bodies of the PUTs, in order, as `[path, body]`. */
  writes: [string, unknown][];
  /** Paths of every call that reached this stub. */
  calls: string[];
  /** Answer these paths (a suffix of the address) with this problem. */
  refuse: Map<string, { status: number; body: Record<string, unknown> }>;
}

export const EMAIL_DEFAULT: FeedbackEmailSetting = {
  available: true,
  enabled: null,
  effective: false,
  source: 'default',
};

export const WEBHOOK_NONE: FeedbackWebhook = {
  available: true,
  configured: false,
  host: null,
  language: null,
  setAt: null,
  groupHost: null,
};

function json(status: number, body: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: body === undefined ? {} : { 'content-type': 'application/json' },
  });
}

/** The level an address is about, or null when it is not one of ours. */
function levelOf(path: string): { kind: 'email' | 'webhook'; level: 'me' | 'group' | 'site' } | null {
  const match = /\/(me|groups\/[^/]+|sites\/[^/]+\/[^/]+)\/feedback-(email|webhook)$/.exec(path);
  if (match === null) return null;
  const where = match[1]!;
  const level = where === 'me' ? 'me' : where.startsWith('groups') ? 'group' : 'site';
  return { kind: match[2] as 'email' | 'webhook', level };
}

export function makeNotificationsMock(inner: typeof fetch): NotificationsMock {
  const mock: NotificationsMock = {
    email: { me: { ...EMAIL_DEFAULT }, group: { ...EMAIL_DEFAULT }, site: { ...EMAIL_DEFAULT } },
    webhook: { group: { ...WEBHOOK_NONE }, site: { ...WEBHOOK_NONE } },
    writes: [],
    calls: [],
    refuse: new Map(),
    fetch: async (input, init) => {
      const path = String(input);
      const target = levelOf(path);
      if (target === null) return inner(input, init);
      mock.calls.push(path);
      const refusal = [...mock.refuse].find(([suffix]) => path.endsWith(suffix));
      if (refusal !== undefined) {
        const [, { status, body }] = refusal;
        return new Response(JSON.stringify({ type: 'about:blank', status, ...body }), {
          status,
          headers: { 'content-type': 'application/problem+json' },
        });
      }
      const method = init!.method as string;
      const body = init!.body === undefined ? undefined : JSON.parse(String(init!.body));
      if (method !== 'GET') mock.writes.push([path, body ?? null]);
      if (target.kind === 'email') {
        const current = mock.email[target.level];
        if (method === 'PUT') {
          const enabled = (body as { enabled: boolean | null }).enabled;
          mock.email[target.level] = { ...current, enabled, effective: enabled ?? current.effective };
        }
        return json(200, mock.email[target.level]);
      }
      const level = target.level as 'group' | 'site';
      if (method === 'PUT') {
        const put = body as { url: string; language?: 'nl' | 'en' };
        mock.webhook[level] = {
          ...mock.webhook[level],
          configured: true,
          host: new URL(put.url).host,
          language: put.language ?? 'nl',
          setAt: '2026-11-20T10:00:00Z',
        };
        return json(200, mock.webhook[level]);
      }
      if (method === 'DELETE') {
        mock.webhook[level] = { ...WEBHOOK_NONE, groupHost: mock.webhook[level].groupHost };
        return json(204, undefined);
      }
      return json(200, mock.webhook[level]);
    },
  };
  return mock;
}

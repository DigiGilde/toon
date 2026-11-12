import { describe, expect, it, vi } from 'vitest';

import {
  type Action,
  ApiFailure,
  createApi,
  describeFailure,
  type NewItem,
  parseItem,
  parseReply,
  parseRetryAfter,
} from './api';
import { translator } from './i18n';
import { json, problem } from './testPlatform';

const NOW = Date.parse('2026-11-11T10:00:00Z');

function api(fetch: (url: string, init: RequestInit) => Promise<Response>, lang: 'nl' | 'en' = 'nl') {
  return createApi({ api: '/-/feedback/v1', token: 'tok_en-1', lang, fetch, now: () => NOW });
}

const ITEM: NewItem = {
  kind: 'page',
  comment: 'Klopt dit?',
  page: { path: '/team/site/', title: 'Start' },
  anchor: null,
  viewport: { width: 1280, height: 800, dpr: 2 },
  author: { kind: 'anonymous' },
  screenshot: { region: null },
};

const MINE = {
  id: 'i1',
  kind: 'point',
  status: 'seen',
  comment: 'Hier',
  quote: null,
  anchor: { k: 'point', s: '#a', x: 0.1, y: 0.2, px: 1, py: 2 },
  page: { path: 'docs/', title: 'Docs', url: '/team/site/docs/' },
  target: 'live',
  previewRef: null,
  createdAt: '2026-11-10T09:00:00Z',
  replies: [{ id: 'r1', author: 'team', name: 'Jan', body: 'Gezien', createdAt: '2026-11-10T10:00:00Z' }],
  replyCount: 14,
};

async function failureOf(promise: Promise<unknown>): Promise<ApiFailure> {
  try {
    await promise;
  } catch (error) {
    return error as ApiFailure;
  }
  throw new Error('expected a failure');
}

describe('requests', () => {
  it('send the token in its own scheme, the language, and no credentials', async () => {
    const fetch = vi.fn().mockResolvedValue(json(201, { id: 'new', status: 'new', createdAt: 'x' }));
    await api(fetch, 'en').create(ITEM, null);
    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe('/-/feedback/v1/items');
    expect(init).toMatchObject({
      method: 'POST',
      credentials: 'omit',
      cache: 'no-store',
      headers: { Authorization: 'Toon-Feedback tok_en-1', 'Accept-Language': 'en' },
    });
  });

  it('create the item as multipart, with the screenshot as a file when there is one', async () => {
    const fetch = vi.fn().mockImplementation(async () => json(201, { id: 'new' }));
    const screenshot = new Blob(['jpeg'], { type: 'image/jpeg' });
    expect(await api(fetch).create(ITEM, screenshot)).toBe('new');
    const body = fetch.mock.calls[0]![1].body as FormData;
    expect(JSON.parse(body.get('item') as string)).toEqual(ITEM);
    const file = body.get('screenshot') as File;
    expect([file.name, file.type]).toEqual(['screenshot.jpg', 'image/jpeg']);

    await api(fetch).create(ITEM, null);
    expect((fetch.mock.calls[1]![1].body as FormData).has('screenshot')).toBe(false);
  });

  it('list own items, skipping the ones it cannot read', async () => {
    const fetch = vi.fn().mockResolvedValue(json(200, { items: [MINE, { id: 'broken' }], more: false }));
    const { items, more } = await api(fetch).mine();
    expect(fetch.mock.calls[0]![1].method).toBe('GET');
    expect(more).toBe(false);
    expect(items).toEqual([
      {
        id: 'i1',
        kind: 'point',
        status: 'seen',
        comment: 'Hier',
        quote: null,
        anchor: { k: 'point', s: '#a', x: 0.1, y: 0.2, px: 1, py: 2, q: null },
        page: { path: 'docs/', title: 'Docs', url: '/team/site/docs/' },
        createdAt: '2026-11-10T09:00:00Z',
        replies: [{ id: 'r1', author: 'team', name: 'Jan', body: 'Gezien', createdAt: '2026-11-10T10:00:00Z' }],
        replyCount: 14,
      },
    ]);
  });

  it('say whether older items were left out of the list, and take anything but true for no', async () => {
    const more = async (body: Record<string, unknown>) =>
      (await api(vi.fn().mockResolvedValue(json(200, { items: [MINE], ...body }))).mine()).more;
    expect(await more({ more: true })).toBe(true);
    for (const value of [false, undefined, null, 'true', 1]) expect(await more({ more: value })).toBe(false);
    expect(await more({})).toBe(false);
  });

  it('reply as JSON on the item, with the id in the path', async () => {
    const reply = { id: 'r2', author: 'you', name: null, body: 'Dank', createdAt: 'x' };
    const fetch = vi.fn().mockResolvedValue(json(201, reply));
    expect(await api(fetch).reply('a/b', 'Dank')).toEqual(reply);
    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe('/-/feedback/v1/items/a%2Fb/replies');
    expect(init.method).toBe('POST');
    expect(init.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(init.body)).toEqual({ body: 'Dank' });
  });

  it('delete an item', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    await api(fetch).remove('i1');
    expect(fetch.mock.calls[0]![0]).toBe('/-/feedback/v1/items/i1');
    expect(fetch.mock.calls[0]![1].method).toBe('DELETE');
  });
});

describe('failures', () => {
  it('a rejected fetch is a network failure', async () => {
    const fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    expect((await failureOf(api(fetch).mine())).failure).toEqual({ type: 'network' });
  });

  it('a problem carries its code, its detail and its wait', async () => {
    const fetch = vi.fn().mockResolvedValue(problem(429, 'FEEDBACK_RATE_LIMITED', '  Te   veel. ', { 'Retry-After': '120' }));
    expect((await failureOf(api(fetch).create(ITEM, null))).failure).toEqual({
      type: 'problem',
      status: 429,
      code: 'FEEDBACK_RATE_LIMITED',
      detail: 'Te veel.',
      retryAfter: 120,
    });
  });

  it('a problem without a usable detail has none', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(problem(404, 'FEEDBACK_UNAVAILABLE'));
    expect((await failureOf(api(fetch).mine())).failure).toMatchObject({ detail: null, retryAfter: null });
    fetch.mockResolvedValueOnce(problem(422, 'FEEDBACK_INVALID', ' \u200b '));
    expect((await failureOf(api(fetch).mine())).failure).toMatchObject({ detail: null });
  });

  it.each([
    ['an answer that is not JSON', new Response('<h1>Not found</h1>', { status: 404, headers: { 'Content-Type': 'text/html' } })],
    ['an answer of plain text', new Response('{}', { status: 500 })],
    ['an answer without a body or a type', new Response(null, { status: 502 })],
    ['JSON that does not parse', new Response('{', { status: 500, headers: { 'Content-Type': 'application/problem+json' } })],
    ['JSON without a code', json(500, { title: 'x' })],
    ['JSON that is not an object', json(500, ['x'])],
  ])('%s is unreadable', async (_name, response) => {
    const fetch = vi.fn().mockResolvedValue(response);
    expect((await failureOf(api(fetch).mine())).failure).toEqual({ type: 'unreadable', status: response.status });
  });

  it.each([
    ['a create answer without an id', 'create', json(201, { status: 'new' })],
    ['a create answer that is not JSON', 'create', new Response('ok', { status: 201 })],
    ['a list without items', 'mine', json(200, { items: 'none' })],
    ['a reply that is not a reply', 'reply', json(201, { id: 'r' })],
  ])('%s is unreadable', async (_name, call, response) => {
    const fetch = vi.fn().mockResolvedValue(response);
    const client = api(fetch);
    const promise =
      call === 'create' ? client.create(ITEM, null) : call === 'mine' ? client.mine() : client.reply('i', 'x');
    expect((await failureOf(promise)).failure).toEqual({ type: 'unreadable', status: response.status });
  });
});

describe('parseRetryAfter', () => {
  it('reads seconds and dates, and nothing else', () => {
    expect(parseRetryAfter(null, NOW)).toBeNull();
    expect(parseRetryAfter(' 30 ', NOW)).toBe(30);
    expect(parseRetryAfter('Wed, 11 Nov 2026 10:02:00 GMT', NOW)).toBe(120);
    expect(parseRetryAfter('Wed, 11 Nov 2026 09:00:00 GMT', NOW)).toBe(0);
    expect(parseRetryAfter('soon', NOW)).toBeNull();
  });
});

describe('reading own items', () => {
  it('refuses what is not an item', () => {
    expect(parseItem(null)).toBeNull();
    expect(parseItem({ ...MINE, page: null })).toBeNull();
    expect(parseItem({ ...MINE, id: '' })).toBeNull();
    expect(parseItem({ ...MINE, id: 5 })).toBeNull();
    expect(parseItem({ ...MINE, kind: 'video' })).toBeNull();
    expect(parseItem({ ...MINE, status: 'done' })).toBeNull();
    expect(parseItem({ ...MINE, comment: null })).toBeNull();
    expect(parseItem({ ...MINE, createdAt: 1 })).toBeNull();
    expect(parseItem({ ...MINE, page: { ...MINE.page, path: null } })).toBeNull();
    expect(parseItem({ ...MINE, quote: 5 })).toBeNull();
    expect(parseItem({ ...MINE, page: { ...MINE.page, title: 5 } })).toBeNull();
    expect(parseItem({ ...MINE, replies: null })).toBeNull();
  });

  it('keeps an item whose anchor or link it cannot use, without them', () => {
    const item = parseItem({
      ...MINE,
      quote: 'tekst',
      anchor: { k: 'nonsense' },
      page: { path: '', title: undefined, url: '//evil.test/' },
      replies: [{ id: 'r', author: 'you', name: 'ignored', body: 'b', createdAt: 'c' }, 'broken'],
    })!;
    expect(item.quote).toBe('tekst');
    expect(item.anchor).toBeNull();
    expect(item.page).toEqual({ path: '', title: null, url: null });
    expect(item.replies).toEqual([{ id: 'r', author: 'you', name: null, body: 'b', createdAt: 'c' }]);
    expect(parseItem({ ...MINE, page: { ...MINE.page, url: 7 } })!.page.url).toBeNull();
  });

  it('counts the replies an item has in all, never fewer than came along', () => {
    expect(parseItem(MINE)!.replyCount).toBe(14);
    for (const replyCount of [undefined, null, '14', 1.5, -3, 0]) {
      expect(parseItem({ ...MINE, replyCount })!.replyCount).toBe(1);
    }
  });

  it('refuses what is not a reply', () => {
    const reply = { id: 'r', author: 'team', name: null, body: 'b', createdAt: 'c' };
    expect(parseReply(reply)).toEqual(reply);
    expect(parseReply('x')).toBeNull();
    expect(parseReply({ ...reply, id: '' })).toBeNull();
    expect(parseReply({ ...reply, id: 1 })).toBeNull();
    expect(parseReply({ ...reply, author: 'bot' })).toBeNull();
    expect(parseReply({ ...reply, body: 1 })).toBeNull();
    expect(parseReply({ ...reply, createdAt: 1 })).toBeNull();
    expect(parseReply({ ...reply, name: 1 })).toBeNull();
  });
});

describe('describeFailure', () => {
  const t = translator('nl');
  const failed = (code: string, detail: string | null = null, retryAfter: number | null = null) =>
    new ApiFailure({ type: 'problem', status: 400, code, detail, retryAfter });

  it('says "later" for anything it cannot read', () => {
    for (const error of [new Error('x'), new ApiFailure({ type: 'network' }), new ApiFailure({ type: 'unreadable', status: 502 })]) {
      expect(describeFailure(error, 'send', t)).toEqual({
        message: 'Dat lukte niet. Probeer het later opnieuw.',
        copy: false,
        withoutScreenshot: false,
      });
    }
    expect(describeFailure(failed('SOMETHING_NEW'), 'send', t).message).toBe(t('error.later'));
  });

  it.each<[Action, string, boolean]>([
    ['send', 'error.unavailable', true],
    ['reply', 'error.unavailable', true],
    ['list', 'error.unavailable.other', false],
    ['delete', 'error.unavailable.other', false],
  ])('on %s, a token that no longer works says %s', (action, key, copy) => {
    expect(describeFailure(failed('FEEDBACK_UNAVAILABLE'), action, t)).toEqual({
      message: t(key as never),
      copy,
      withoutScreenshot: false,
    });
  });

  it.each<[Action, string, boolean]>([
    ['send', 'error.account', true],
    ['list', 'error.account.other', false],
  ])('on %s, a missing account says %s', (action, key, copy) => {
    expect(describeFailure(failed('FEEDBACK_ACCOUNT_REQUIRED'), action, t)).toMatchObject({ message: t(key as never), copy });
  });

  it.each<[Action, string, boolean]>([
    ['send', 'De site of groep heeft een nieuw adres gekregen. Kopieer je tekst en laad de pagina opnieuw.', true],
    ['reply', 'De site of groep heeft een nieuw adres gekregen. Kopieer je tekst en laad de pagina opnieuw.', true],
    ['list', 'De site of groep heeft een nieuw adres gekregen. Laad de pagina opnieuw.', false],
    ['delete', 'De site of groep heeft een nieuw adres gekregen. Laad de pagina opnieuw.', false],
  ])('on %s, a page served before the site moved says to reload: %s', (action, message, copy) => {
    // Never the server's detail: the widget says it in its own words.
    expect(describeFailure(failed('FEEDBACK_PAGE_MOVED', 'Er klopt iets niet.'), action, t)).toEqual({
      message,
      copy,
      withoutScreenshot: false,
    });
  });

  it('says in English that the site moved', () => {
    expect(describeFailure(failed('FEEDBACK_PAGE_MOVED'), 'send', translator('en')).message).toBe(
      'The site or group has a new address. Copy your text and reload the page.',
    );
  });

  it('shows the field the server names, or a general line', () => {
    expect(describeFailure(failed('FEEDBACK_INVALID', 'Er klopt iets niet met het veld comment.'), 'send', t).message).toBe(
      'Er klopt iets niet met het veld comment.',
    );
    expect(describeFailure(failed('FEEDBACK_INVALID'), 'send', t).message).toBe(t('error.invalid'));
  });

  it('offers to copy the text when the site no longer takes this kind of author', () => {
    expect(describeFailure(failed('FEEDBACK_AUTHOR_NOT_ALLOWED'), 'send', t)).toMatchObject({ copy: true });
  });

  it.each([
    ['FEEDBACK_SCREENSHOT_TOO_LARGE', 'error.shot.large'],
    ['FEEDBACK_SCREENSHOT_INVALID', 'error.shot.invalid'],
    ['FEEDBACK_SCREENSHOT_QUOTA', 'error.shot.quota'],
  ])('offers to send without the screenshot for %s', (code, key) => {
    expect(describeFailure(failed(code), 'send', t)).toEqual({
      message: t(key as never),
      copy: false,
      withoutScreenshot: true,
    });
  });

  it('says the site is full', () => {
    expect(describeFailure(failed('FEEDBACK_SITE_FULL'), 'send', t).message).toBe(t('error.full'));
  });

  it('says how long to wait, for the widget limit and the platform limit alike', () => {
    expect(describeFailure(failed('FEEDBACK_RATE_LIMITED', null, 30), 'send', t).message).toBe(
      'Er komt nu veel feedback binnen. Probeer het over 30 seconden opnieuw.',
    );
    expect(describeFailure(failed('TOO_MANY_REQUESTS', null, 300), 'reply', t).message).toBe(
      'Er komt nu veel feedback binnen. Probeer het over 5 minuten opnieuw.',
    );
    expect(describeFailure(failed('TOO_MANY_REQUESTS'), 'list', t).message).toBe(t('error.rate.soon'));
  });

  it('says when Toon is too busy, and how long to wait when it says so', () => {
    expect(describeFailure(failed('FEEDBACK_BUSY', null, 60), 'send', t)).toEqual({
      message: 'Toon heeft het nu te druk. Probeer het over 60 seconden opnieuw.',
      copy: false,
      withoutScreenshot: false,
    });
    expect(describeFailure(failed('FEEDBACK_BUSY'), 'reply', t).message).toBe(t('error.busy.soon'));
    expect(describeFailure(failed('FEEDBACK_BUSY', null, 60), 'send', translator('en')).message).toBe(
      'Toon is too busy right now. Try again in 60 seconds.',
    );
  });

  it('offers to send without the screenshot when sending took too long', () => {
    expect(describeFailure(failed('FEEDBACK_TIMEOUT'), 'send', t)).toEqual({
      message: t('error.timeout'),
      copy: false,
      withoutScreenshot: true,
    });
  });

  it('says a thread that is full takes no more replies', () => {
    expect(describeFailure(failed('FEEDBACK_THREAD_FULL'), 'reply', t)).toEqual({
      message: 'Bij deze feedback kun je geen reacties meer plaatsen.',
      copy: false,
      withoutScreenshot: false,
    });
  });
});

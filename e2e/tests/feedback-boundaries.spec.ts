/**
 * The boundaries site feedback relies on (spec §16.2, §16.5, §16.7, BIO2
 * 8.29), in a real browser. Only a navigation gets the widget: a fetch() of a
 * feedback page from page script gets the file as published, with no tag and
 * no token, and a frame gets none either, besides being refused by
 * `frame-ancestors 'none'`. A hide form posted from another site without a
 * valid page token of the site it names is the neutral 404 and sets no
 * cookie.
 *
 * Its own group, independent of feedback.spec.ts, whose helpers it reuses.
 */

import { expect, test, type APIRequestContext, type BrowserContext, type Page, type Response } from '@playwright/test';
import { adminFetch, body, newHostContext, uploadViaSession } from '../helpers/api';
import {
  ADMIN_EMAIL,
  ADMIN_SUB,
  ADMIN_URL,
  CONTENT_HOST,
  CONTENT_URL,
  CONTEXT_OPTIONS,
  MOCK_OIDC_PUBLIC,
  NEUTRAL_404,
} from '../helpers/environment';
import { feedbackButton, widgetHost, widgetScript } from '../helpers/feedback';
import { logInOnAdmin } from '../helpers/oidc';
import { makeTarGz } from '../helpers/tar';

const GROUP = 'e2e-feedback-grenzen';
const API = '/-/api/v1';
const PREVIEW_REF = 'pr-1';
// Public, feedback with a name on the previews: shielded (the default), and not.
const SHIELDED = 'afgeschermd';
const UNSHIELDED = 'open';
const HIDE_URL = `${CONTENT_URL}/-/feedback/hide`;
const HIDE_COOKIE = '__Secure-toon-feedback-off';
// A page on another site: the mock OIDC server's address, cross-site to the
// content host. The browser never asks it for these paths: the route answers.
const ELSEWHERE = `${MOCK_OIDC_PUBLIC}/e2e-elders`;

function previewPath(site: string): string {
  return `/${GROUP}/${site}/_preview/${PREVIEW_REF}/`;
}

function previewUrl(site: string): string {
  return `${CONTENT_URL}${previewPath(site)}`;
}

function dist(heading: string): Buffer {
  return makeTarGz({
    'index.html':
      '<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>Grenzen</title></head>' +
      `<body><main><h1>${heading}</h1><p>Een pagina met de feedbackknop.</p></main></body></html>`,
  });
}

/** The page as a navigation gets it, and the widget's configuration in it, or null without a tag. */
async function navigationConfig(path: string): Promise<Record<string, unknown> | null> {
  const response = await contentApi.get(path, { headers: { 'sec-fetch-dest': 'document' } });
  expect(response.status()).toBe(200);
  const tag = /data-toon-feedback="([\w-]+)"/.exec(await body(response));
  return tag === null ? null : (JSON.parse(Buffer.from(tag[1]!, 'base64url').toString('utf8')) as Record<string, unknown>);
}

/** A valid page token of a site, as any visitor of its public page gets one. */
async function tokenOf(site: string): Promise<string> {
  const config = await navigationConfig(previewPath(site));
  expect(config, `the preview of ${site} carries the widget`).not.toBeNull();
  return config!.token as string;
}

/**
 * Posts the hide form from a page of another site, as a top-level
 * navigation of `page`, and answers with the response to the post.
 */
async function hideFromElsewhere(page: Page, fields: Record<string, string>): Promise<Response> {
  const inputs = Object.entries(fields)
    .map(([name, value]) => `<input type="hidden" name="${name}" value="${value}">`)
    .join('');
  await page.route(ELSEWHERE, (route) =>
    route.fulfill({
      contentType: 'text/html; charset=utf-8',
      body:
        '<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>Elders</title></head><body>' +
        `<form method="post" action="${HIDE_URL}">${inputs}<button>Verbergen</button></form></body></html>`,
    }),
  );
  await page.goto(ELSEWHERE);
  const posted = page.waitForResponse((response) => response.url() === HIDE_URL && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Verbergen' }).click();
  const response = await posted;
  await page.unroute(ELSEWHERE);
  return response;
}

/** Every hide cookie in the context: asked for by URL, a Secure cookie on
 * the http:// e2e stack would not be listed. */
async function hideCookies(context: BrowserContext): Promise<string[]> {
  return (await context.cookies())
    .map((cookie) => cookie.name)
    .filter((name) => name === HIDE_COOKIE);
}

let adminContext: BrowserContext;
let contentApi: APIRequestContext;

test.describe.serial('the boundaries of site feedback', () => {
  test.beforeAll(async () => {
    contentApi = await newHostContext(CONTENT_HOST);
  });

  test.afterAll(async () => {
    await contentApi?.dispose();
    await adminContext?.close();
  });

  test('two public sites take feedback on their previews, one of them shielded', async ({ browser }) => {
    adminContext = await browser.newContext(CONTEXT_OPTIONS);
    const adminPage = await adminContext.newPage();
    await logInOnAdmin(adminPage, ADMIN_URL, ADMIN_SUB, { email: ADMIN_EMAIL });
    const group = await adminFetch(adminPage, 'POST', `${API}/groups`, { name: 'Feedback grenzen', slug: GROUP });
    expect(group.status, group.text).toBe(201);

    for (const [site, sandbox] of [
      [SHIELDED, true],
      [UNSHIELDED, false],
    ] as const) {
      const created = await adminFetch(adminPage, 'POST', `${API}/groups/${GROUP}/sites`, { title: site, slug: site });
      expect(created.status, created.text).toBe(201);
      expect(created.json).toMatchObject({ sandbox: true });
      const access = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${site}/access`, { base: 'public' });
      expect(access.status, access.text).toBe(200);
      if (!sandbox) {
        const off = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${site}/sandbox`, { sandbox: false });
        expect(off.status, off.text).toBe(200);
      }
      const settings = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${site}/feedback-settings`, {
        enabled: true,
        identity: 'name',
        live: false,
      });
      expect(settings.status, settings.text).toBe(200);
      expect(settings.json).toMatchObject({ feedbackEnabled: true, feedbackIdentity: 'name', sandbox });
      const preview = await uploadViaSession(adminPage, GROUP, site, dist(`Preview van ${site}`), PREVIEW_REF);
      expect(preview.status, preview.text).toBe(201);
    }
    // A live page without the widget, on the same origin as every unshielded site.
    const live = await uploadViaSession(adminPage, GROUP, UNSHIELDED, dist('Live zonder knop'));
    expect(live.status, live.text).toBe(201);
  });

  test('a fetch() of a feedback page from page script gets the file as published: no widget, no token', async ({
    browser,
  }) => {
    const context = await browser.newContext(CONTEXT_OPTIONS);
    const page = await context.newPage();
    // An unshielded page shares the content origin, so its script can read
    // any page there: its own, and the shielded site's.
    await page.goto(previewUrl(UNSHIELDED));
    await expect(feedbackButton(page)).toBeVisible();

    const fetched = await page.evaluate(async (paths) => {
      const answers: Array<{ status: number; text: string }> = [];
      for (const path of paths) {
        const response = await fetch(path);
        answers.push({ status: response.status, text: await response.text() });
      }
      return answers;
    }, [previewPath(UNSHIELDED), previewPath(SHIELDED)]);

    expect(fetched).toHaveLength(2);
    for (const [index, site] of [UNSHIELDED, SHIELDED].entries()) {
      const answer = fetched[index]!;
      expect(answer.status, site).toBe(200);
      expect(answer.text, site).toContain(`Preview van ${site}`);
      expect(answer.text, site).not.toContain('data-toon-feedback');
      expect(answer.text, site).not.toContain('/-/feedback/');
    }
    await context.close();
  });

  test('a page in a frame gets no widget, and the frame is refused', async ({ browser }) => {
    // The server's half: a frame's request gets the file as published.
    for (const site of [SHIELDED, UNSHIELDED]) {
      const framed = await contentApi.get(previewPath(site), { headers: { 'sec-fetch-dest': 'iframe' } });
      expect(framed.status()).toBe(200);
      expect(framed.headers()['content-security-policy']).toContain("frame-ancestors 'none'");
      const html = await body(framed);
      expect(html).toContain(`Preview van ${site}`);
      expect(html).not.toContain('data-toon-feedback');
    }

    // The browser's half, from a page on the same origin, without a widget of its own.
    const context = await browser.newContext(CONTEXT_OPTIONS);
    const page = await context.newPage();
    const feedbackRequests: string[] = [];
    page.on('request', (request) => {
      if (new URL(request.url()).pathname.startsWith('/-/feedback/')) feedbackRequests.push(request.url());
    });
    await page.goto(`${CONTENT_URL}/${GROUP}/${UNSHIELDED}/`);
    await expect(page.locator('h1')).toHaveText('Live zonder knop');
    await expect(widgetScript(page)).toHaveCount(0);

    for (const site of [UNSHIELDED, SHIELDED]) {
      const target = previewUrl(site);
      const answered = page.waitForResponse(
        (response) => response.url() === target && response.request().frame() !== page.mainFrame(),
      );
      await page.evaluate((src) => {
        const frame = document.createElement('iframe');
        frame.src = src;
        document.body.append(frame);
      }, target);
      const response = await answered;
      expect(await response.request().headerValue('sec-fetch-dest')).toBe('iframe');
      expect(await response.headerValue('content-security-policy')).toContain("frame-ancestors 'none'");
    }
    // Whatever the frames show, it is no widget, and nothing asked for its bundle.
    const frames = page.frameLocator('iframe');
    await expect(page.locator('iframe')).toHaveCount(2);
    for (const index of [0, 1]) {
      await expect(frames.nth(index).locator('[data-toon-feedback-host]')).toHaveCount(0);
    }
    await expect(widgetHost(page)).toHaveCount(0);
    expect(feedbackRequests).toEqual([]);
    await context.close();
  });

  test('a hide form posted from another site without a valid token of that site is the neutral 404 and sets no cookie', async ({
    browser,
  }) => {
    const context = await browser.newContext(CONTEXT_OPTIONS);
    const page = await context.newPage();
    const returnTo = previewPath(SHIELDED);
    const otherSite = await tokenOf(UNSHIELDED);
    const cases: Array<[string, Record<string, string>]> = [
      ['no token', { returnTo }],
      ['a made-up token', { token: 'A'.repeat(120), returnTo }],
      ['an empty token', { token: '', returnTo }],
      ['a token of another site', { token: otherSite, returnTo }],
    ];

    for (const [why, fields] of cases) {
      const response = await hideFromElsewhere(page, fields);
      expect(response.status(), why).toBe(404);
      expect(await response.text(), why).toBe(NEUTRAL_404);
      expect(await response.headerValue('set-cookie'), why).toBeNull();
      expect(await hideCookies(context), why).toEqual([]);
    }

    // The button is still there for this visitor.
    await page.goto(previewUrl(SHIELDED));
    await expect(feedbackButton(page)).toBeVisible();

    // The control: a valid token of this very site does hide it, from
    // anywhere. No header tells that post from the widget's own (design
    // §16.7), so the refusals above come from the token alone.
    const response = await hideFromElsewhere(page, { token: await tokenOf(SHIELDED), returnTo });
    expect(response.status()).toBe(303);
    expect(await response.headerValue('set-cookie')).toContain(`${HIDE_COOKIE}=`);
    await expect(page.locator('h1')).toHaveText(`Preview van ${SHIELDED}`);
    await expect(widgetHost(page)).toHaveCount(0);
    expect(await hideCookies(context)).toEqual([HIDE_COOKIE]);
    await context.close();
  });
});

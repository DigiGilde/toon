/**
 * Markdown in feedback (spec §16): a giver with an account sends a comment
 * with a list, inline code, a code block, a bare link, an image tag with an
 * event handler and a javascript: link, and the site team replies with
 * Markdown of its own. Under "My feedback" in the widget, on a shielded
 * preview, and in the Feedback tab of the admin SPA, the list and the code
 * render, a link shows the address it goes to, and the HTML and the
 * javascript: link stay text: no img or script element, no javascript: link,
 * no handler that ran, no dialog.
 *
 * Its own group, independent of feedback.spec.ts, whose helpers it reuses.
 */

import { expect, test, type BrowserContext, type Locator, type Page } from '@playwright/test';
import { adminFetch, uploadViaSession } from '../helpers/api';
import { ADMIN_EMAIL, ADMIN_SUB, ADMIN_URL, CONTENT_URL, CONTEXT_OPTIONS } from '../helpers/environment';
import { feedbackWithComment, openPanel, sendFeedback, widgetHost } from '../helpers/feedback';
import { fillMockLogin, logInOnAdmin, onMockLoginPage } from '../helpers/oidc';
import { makeTarGz } from '../helpers/tar';

const GROUP = 'e2e-feedback-md';
const SITE = 'website';
const API = '/-/api/v1';
const PREVIEW_REF = 'pr-1';
const PREVIEW_URL = `${CONTENT_URL}/${GROUP}/${SITE}/_preview/${PREVIEW_REF}/`;

const GIVER_SUB = 'markdown-gever';
const GIVER_EMAIL = 'markdown-gever@toon.local';
const GIVER_NAME = 'Mark Down';

const SOURCE = 'https://example.org/bron?jaar=2026';
const FIX = 'https://example.org/fix';
const IMAGE_TAG = '<img src=x onerror="window.toonPwned=1">';
const SCRIPT_LINK = '[klik](javascript:window.toonPwned=1)';

const COMMENT = [
  'Twee fouten op de pagina:',
  '',
  '- de **datum** klopt niet',
  '- de tabel mist een kolom',
  '',
  `Zie \`begroting_2026\` en de bron ${SOURCE}.`,
  '',
  '```',
  'regel 1: <b>vet</b>',
  '```',
  '',
  IMAGE_TAG,
  SCRIPT_LINK,
].join('\n');

const REPLY = `Dank je! **Opgelost** in \`main\`, zie [de wijziging](${FIX}).`;

let adminContext: BrowserContext;
let giverContext: BrowserContext;
let adminPage: Page;
let giverPage: Page;
let itemId: string;
/** Every dialog a page opened: none may open. */
const dialogs: string[] = [];

/** `text` for a RegExp that matches it as it stands. */
function literally(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** No handler from the text ran on this page, and nothing in it links to javascript:. */
async function nothingRan(page: Page): Promise<void> {
  expect(await page.evaluate(() => (window as unknown as { toonPwned?: number }).toonPwned)).toBeUndefined();
  await expect(page.locator('a[href^="javascript:" i]')).toHaveCount(0);
  expect(dialogs).toEqual([]);
}

/** The comment as rendered in `scope`: a list, code, the link as its address, the HTML as text. */
async function rendersComment(scope: Locator, newTab: boolean): Promise<void> {
  await expect(scope.getByRole('listitem').filter({ hasText: 'de tabel mist een kolom' })).toBeVisible();
  await expect(scope.locator('li strong', { hasText: 'datum' })).toBeVisible();
  await expect(scope.locator('code', { hasText: 'begroting_2026' })).toBeVisible();
  await expect(scope.locator('pre')).toHaveText('regel 1: <b>vet</b>');
  const link = scope.locator(`a[href="${SOURCE}"]`);
  await expect(link).toHaveCount(1);
  // The address is what shows, and the sentence's full stop is not part of it.
  await expect(link).toHaveText(new RegExp(`^${literally(SOURCE)}`));
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer nofollow ugc');
  if (newTab) await expect(link).toHaveAttribute('target', '_blank');
  else await expect(link).not.toHaveAttribute('target');
  await expect(scope).toContainText(IMAGE_TAG);
  await expect(scope).toContainText(SCRIPT_LINK);
  await expect(scope.locator('img, script, iframe, object, embed')).toHaveCount(0);
}

/** The reply as rendered in `scope`: bold, code, and the label followed by the address. */
async function rendersReply(scope: Locator): Promise<void> {
  await expect(scope.locator('strong', { hasText: 'Opgelost' })).toBeVisible();
  await expect(scope.locator('code', { hasText: 'main' })).toBeVisible();
  await expect(scope.locator(`a[href="${FIX}"]`)).toHaveText(new RegExp(`^${literally(FIX)}`));
  await expect(scope).toContainText(`de wijziging (${FIX}`);
}

test.describe.serial('Markdown in feedback', () => {
  test.beforeAll(async ({ browser }) => {
    adminContext = await browser.newContext(CONTEXT_OPTIONS);
    // The consent page speaks the language the browser asks for.
    giverContext = await browser.newContext({ ...CONTEXT_OPTIONS, locale: 'nl-NL' });
    adminPage = await adminContext.newPage();
    giverPage = await giverContext.newPage();
    for (const page of [adminPage, giverPage]) {
      page.on('dialog', (dialog) => {
        dialogs.push(dialog.message());
        void dialog.dismiss();
      });
    }
    await logInOnAdmin(adminPage, ADMIN_URL, ADMIN_SUB, { email: ADMIN_EMAIL });
  });

  test.afterAll(async () => {
    await adminContext?.close();
    await giverContext?.close();
  });

  test('a site takes feedback on its preview', async () => {
    const group = await adminFetch(adminPage, 'POST', `${API}/groups`, { name: 'Feedback Markdown E2E', slug: GROUP });
    expect(group.status, group.text).toBe(201);
    const site = await adminFetch(adminPage, 'POST', `${API}/groups/${GROUP}/sites`, { title: 'Website', slug: SITE });
    expect(site.status, site.text).toBe(201);
    const access = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/access`, { base: 'public' });
    expect(access.status, access.text).toBe(200);
    const settings = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/feedback-settings`, {
      enabled: true,
      identity: 'anonymous',
      live: false,
    });
    expect(settings.status, settings.text).toBe(200);
    const dist = makeTarGz({
      'index.html':
        '<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>Markdown</title></head>' +
        '<body><main><h1>Markdown in feedback</h1><p>Een pagina om op te reageren.</p></main></body></html>',
    });
    const preview = await uploadViaSession(adminPage, GROUP, SITE, dist, PREVIEW_REF);
    expect(preview.status, preview.text).toBe(201);
  });

  test('a giver with an account sends Markdown, and the team replies with Markdown', async () => {
    await giverPage.goto(PREVIEW_URL);
    const panel = await openPanel(giverPage);
    await panel.getByRole('link', { name: 'Met je account versturen' }).click();
    await expect(onMockLoginPage(giverPage)).toBeVisible();
    await fillMockLogin(giverPage, GIVER_SUB, { email: GIVER_EMAIL, name: GIVER_NAME });
    await giverPage.waitForURL((url) => url.pathname === '/-/feedback/account', { timeout: 15_000 });
    // The form takes no press in its first second: these locators wait for it.
    const agree = giverPage.getByRole('checkbox', {
      name: 'Het siteteam mag bij mijn feedback zien wat hierboven staat.',
    });
    await agree.check();
    await giverPage.getByRole('button', { name: 'Ja, met mijn account' }).click();
    await giverPage.waitForURL(PREVIEW_URL);

    const signedIn = await openPanel(giverPage);
    await signedIn.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    await sendFeedback(signedIn, COMMENT);

    const item = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT);
    itemId = item.id;
    const reply = await adminFetch(adminPage, 'POST', `${API}/sites/${GROUP}/${SITE}/feedback/${itemId}/replies`, {
      body: REPLY,
    });
    expect(reply.status, reply.text).toBe(201);
  });

  test('"My feedback" renders the comment and the reply, and keeps the HTML as text', async () => {
    await giverPage.goto(PREVIEW_URL);
    const panel = await openPanel(giverPage);
    await panel.getByRole('button', { name: 'Mijn feedback' }).click();
    await expect(panel.getByRole('heading', { name: 'Mijn feedback' })).toBeVisible();
    const item = panel.locator('[data-item]').filter({ hasText: 'Twee fouten op de pagina:' });
    await expect(item).toBeVisible();
    // The preview is shielded, an opaque origin: a new tab would inherit its
    // sandbox, so a link opens in the same tab there.
    await rendersComment(item, false);
    await rendersReply(item.getByRole('listitem').filter({ hasText: 'Opgelost' }));
    await expect(widgetHost(giverPage).locator('script')).toHaveCount(0);
    await nothingRan(giverPage);
  });

  test('the Feedback tab renders them in the sheet, and the row as text', async () => {
    await adminPage.goto(`${ADMIN_URL}/${GROUP}/${SITE}/feedback`);
    const row = adminPage.getByTestId(`feedback-${itemId}`);
    await expect(row).toContainText('Twee fouten op de pagina:');
    await expect(row.locator('img, script, a')).toHaveCount(0);

    await row.click();
    const comment = adminPage.getByTestId('feedback-comment');
    await rendersComment(comment, true);
    await rendersReply(adminPage.getByTestId('feedback-thread'));
    await nothingRan(adminPage);
  });
});

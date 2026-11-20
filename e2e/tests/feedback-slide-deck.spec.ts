/**
 * Feedback on a single-page slide deck (fixtures/slide-deck/): only the
 * current slide shows, the slide lives in the address as `#/N`, the deck's
 * classic script at the end of the body reads it once and rewrites it with
 * replaceState, and it takes the keys on the whole document.
 *
 * Feedback given on a slide records that slide, "Show on the site" opens the
 * deck on it and marks the place, keys typed in the widget stay in the
 * widget, and under "My feedback" a pin shows only on the slide its item is
 * on, also after the window changes size and in the deck's scroll mode.
 *
 * Its own group, independent of feedback.spec.ts, whose helpers it reuses.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { expect, test, type BrowserContext, type Locator, type Page } from '@playwright/test';
import { adminFetch, uploadViaSession } from '../helpers/api';
import { ADMIN_EMAIL, ADMIN_SUB, ADMIN_URL, CONTENT_URL, CONTEXT_OPTIONS } from '../helpers/environment';
import { feedbackDetail, feedbackWithComment, openPanel, sendFeedback, widgetHost } from '../helpers/feedback';
import { fillMockLogin, logInOnAdmin, onMockLoginPage } from '../helpers/oidc';
import { makeTarGz } from '../helpers/tar';

const GROUP = 'e2e-feedback-deck';
const SITE = 'deck';
const API = '/-/api/v1';
const PREVIEW_REF = 'pr-1';
const PREVIEW_URL = `${CONTENT_URL}/${GROUP}/${SITE}/_preview/${PREVIEW_REF}/`;
const PREVIEW_PATH = new URL(PREVIEW_URL).pathname;

const FIXTURE = resolve(__dirname, '../fixtures/slide-deck');

const GIVER_SUB = 'deck-gever';
const GIVER_EMAIL = 'deck-gever@toon.local';

const COMMENT_POINT = 'The box on slide 3 should be larger.';
const COMMENT_PAGE = 'Slide 2 says too much.';
const NAME = 'Ann de Vries';

const WIDE = { width: 1280, height: 720 };
const SMALLER = { width: 1000, height: 640 };
const NARROW = { width: 800, height: 700 };

let adminContext: BrowserContext;
let visitorContext: BrowserContext;
let adminPage: Page;
let pointId: string;

function slide(page: Page, n: number): Locator {
  return page.getByRole('region', { name: `Slide ${n}` });
}

function box(page: Page, n: number): Locator {
  return slide(page, n).getByText(`Box on slide ${n}`);
}

/** How far the middle of a pin lies from a point on an element, in CSS px. */
async function pinOffset(pin: Locator, element: Locator, point: { x: number; y: number }): Promise<number> {
  const mark = (await pin.boundingBox())!;
  const area = (await element.boundingBox())!;
  return Math.hypot(
    mark.x + mark.width / 2 - (area.x + point.x * area.width),
    mark.y + mark.height / 2 - (area.y + point.y * area.height),
  );
}

/**
 * The deck loaded afresh on slide `n`. A second goto to the same page that
 * only changes the fragment loads nothing, and this deck reads its slide
 * only as it starts.
 */
async function openSlide(page: Page, n: number): Promise<void> {
  await page.goto('about:blank');
  await page.goto(`${PREVIEW_URL}#/${n}`);
  await expect(slide(page, n)).toBeVisible();
}

/** A key pressed on the page itself, as a presenter does with the deck in front. */
async function pageKey(page: Page, key: string): Promise<void> {
  await page.evaluate((key) => {
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
  }, key);
}

test.describe.serial('Feedback on a slide deck', () => {
  test.beforeAll(async ({ browser }) => {
    adminContext = await browser.newContext(CONTEXT_OPTIONS);
    visitorContext = await browser.newContext({ ...CONTEXT_OPTIONS, locale: 'nl-NL', viewport: WIDE });
    adminPage = await adminContext.newPage();
    await logInOnAdmin(adminPage, ADMIN_URL, ADMIN_SUB, { email: ADMIN_EMAIL });
  });

  test.afterAll(async () => {
    await adminContext?.close();
    await visitorContext?.close();
  });

  test('a deck takes feedback on its preview', async () => {
    const group = await adminFetch(adminPage, 'POST', `${API}/groups`, { name: 'Feedback Deck E2E', slug: GROUP });
    expect(group.status, group.text).toBe(201);
    const site = await adminFetch(adminPage, 'POST', `${API}/groups/${GROUP}/sites`, { title: 'Deck', slug: SITE });
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
      'index.html': readFileSync(resolve(FIXTURE, 'index.html'), 'utf8'),
      'deck.js': readFileSync(resolve(FIXTURE, 'deck.js'), 'utf8'),
    });
    const preview = await uploadViaSession(adminPage, GROUP, SITE, dist, PREVIEW_REF);
    expect(preview.status, preview.text).toBe(201);
  });

  test('keys typed in the widget stay in the widget: the deck does not move and the spaces stay', async () => {
    const page = await visitorContext.newPage();
    await openSlide(page, 2);
    await expect(slide(page, 2)).toBeVisible();
    const panel = await openPanel(page);
    await panel.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    const name = panel.getByRole('textbox', { name: /^Je naam/ });
    await name.click();
    await page.keyboard.type(`${NAME} n p`);
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('Home');
    await page.keyboard.press('End');
    await expect(name).toHaveValue(`${NAME} n p`);
    await expect(slide(page, 2)).toBeVisible();
    expect(new URL(page.url()).hash).toBe('#/2');
    // The deck still takes its keys from the page.
    await pageKey(page, 'ArrowRight');
    await expect(slide(page, 3)).toBeVisible();
    await page.close();
  });

  test('feedback given on a slide records the slide', async () => {
    const page = await visitorContext.newPage();
    await openSlide(page, 3);
    const target = (await box(page, 3).boundingBox())!;
    const panel = await openPanel(page);
    await panel.getByRole('button', { name: 'Wijs een punt of gebied aan' }).click();
    const overlay = widgetHost(page).getByRole('application');
    await expect(overlay).toBeVisible();
    await page.mouse.click(target.x + target.width / 4, target.y + target.height / 4);
    await expect(panel.getByText('Je feedback gaat over de plek die je aanwees.')).toBeVisible();
    await sendFeedback(panel, COMMENT_POINT, NAME);
    // Still on slide 3: sending moved nothing.
    await expect(slide(page, 3)).toBeVisible();

    const item = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT_POINT);
    pointId = item.id;
    const detail = await feedbackDetail(adminPage, GROUP, SITE, pointId);
    expect(detail.page).toMatchObject({ path: '', fragment: '#/3' });
    expect(detail.highlightUrl).toContain(`${PREVIEW_URL}#toon-feedback=`);

    // General feedback on slide 2 records slide 2.
    await openSlide(page, 2);
    const general = await openPanel(page);
    await general.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    await sendFeedback(general, COMMENT_PAGE);
    const pageItem = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT_PAGE);
    expect(pageItem.page).toMatchObject({ path: '', fragment: '#/2' });
    await page.close();
  });

  test('"Show on the site" opens the deck on the slide and marks the place, also after a resize', async () => {
    const detail = await feedbackDetail(adminPage, GROUP, SITE, pointId);
    const page = await visitorContext.newPage();
    await page.goto(detail.highlightUrl!);
    await expect(slide(page, 3)).toBeVisible();
    await expect(slide(page, 1)).toBeHidden();
    // The address is the deck's own again.
    await expect.poll(() => new URL(page.url()).hash).toBe('#/3');
    const pin = widgetHost(page).getByRole('img', { name: 'Hier gaat de feedback over' });
    await expect(pin).toBeVisible();
    const spot = detail.anchor!.point!;
    await expect.poll(() => pinOffset(pin, box(page, 3), spot)).toBeLessThan(1.5);

    // Smaller, the deck scales the slide down: the pin goes along.
    const before = (await box(page, 3).boundingBox())!;
    await page.setViewportSize(SMALLER);
    await expect.poll(async () => (await box(page, 3).boundingBox())!.width).toBeLessThan(before.width - 10);
    await expect.poll(() => pinOffset(pin, box(page, 3), spot)).toBeLessThan(1.5);

    // Another slide: the mark does not stay behind on it.
    await pageKey(page, 'ArrowLeft');
    await expect(slide(page, 2)).toBeVisible();
    await expect(pin).toBeHidden();
    await page.close();
  });

  test('"My feedback" shows a pin only on the slide its item is on', async () => {
    const giver = await visitorContext.browser()!.newContext({ ...CONTEXT_OPTIONS, locale: 'nl-NL', viewport: WIDE });
    const page = await giver.newPage();
    await openSlide(page, 3);
    const panel = await openPanel(page);
    await panel.getByRole('link', { name: 'Met je account versturen' }).click();
    await expect(onMockLoginPage(page)).toBeVisible();
    await fillMockLogin(page, GIVER_SUB, { email: GIVER_EMAIL, name: 'Deck Gever' });
    await page.waitForURL((url) => url.pathname === '/-/feedback/account', { timeout: 15_000 });
    await page
      .getByRole('checkbox', { name: 'Het siteteam mag bij mijn feedback zien wat hierboven staat.' })
      .check();
    await page.getByRole('button', { name: 'Ja, met mijn account' }).click();
    await page.waitForURL((url) => url.pathname === PREVIEW_PATH);

    // The way back lands on the first slide: go to slide 3 and point there.
    await openSlide(page, 3);
    const target = (await box(page, 3).boundingBox())!;
    const signedIn = await openPanel(page);
    await signedIn.getByRole('button', { name: 'Wijs een punt of gebied aan' }).click();
    await expect(widgetHost(page).getByRole('application')).toBeVisible();
    await page.mouse.click(target.x + target.width / 2, target.y + target.height / 2);
    await sendFeedback(signedIn, 'Mine, on slide 3.');

    // On slide 1 the item has no pin: its place is on slide 3.
    await openSlide(page, 1);
    await expect(slide(page, 1)).toBeVisible();
    const mine = await openPanel(page);
    await mine.getByRole('button', { name: 'Mijn feedback' }).click();
    const entry = mine.locator('[data-item]').filter({ hasText: 'Mine, on slide 3.' });
    await expect(entry).toBeVisible();
    // The list names the slide, and links to it.
    await expect(entry.getByRole('link')).toHaveAttribute('href', /#\/3$/);
    await expect(entry).toContainText('#/3');
    const pin = widgetHost(page).locator('[data-pin]');
    await expect(pin).toHaveCount(0);

    // The presenter moves on to slide 3: the pin comes, on the box.
    await pageKey(page, 'ArrowRight');
    await pageKey(page, 'ArrowRight');
    await expect(slide(page, 3)).toBeVisible();
    await expect(pin).toBeVisible();
    await expect.poll(() => pinOffset(pin, box(page, 3), { x: 0.5, y: 0.5 })).toBeLessThan(3);

    // Back on slide 1 it goes again.
    await pageKey(page, 'Home');
    await expect(slide(page, 1)).toBeVisible();
    await expect(pin).toHaveCount(0);

    // Narrow, every slide shows to scroll through while the address still
    // says slide 1: the pin is back on its box.
    await page.setViewportSize(NARROW);
    await expect(slide(page, 1)).toBeVisible();
    await box(page, 3).scrollIntoViewIfNeeded();
    await expect(pin).toBeVisible();
    await expect.poll(() => pinOffset(pin, box(page, 3), { x: 0.5, y: 0.5 })).toBeLessThan(3);
    await giver.close();
  });
});

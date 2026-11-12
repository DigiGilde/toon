/**
 * Helpers for the feedback scenarios (tests/feedback.spec.ts).
 *
 * The widget lives in an open shadow root on a div with the attribute
 * data-toon-feedback-host. Playwright's role and CSS locators pierce open
 * shadow roots, so a locator scoped to the host reaches every control a
 * visitor sees. The labels are the widget's Dutch ones
 * (frontend/src/i18n/widget.nl.ts): the test pages say lang="nl", and the
 * widget speaks the language of the page it is on. Seen from the page,
 * anything inside the shadow root is the host: that is what
 * elementFromPoint answers there. The shadow root's own elementFromPoint
 * answers with the element inside it.
 *
 * The feedback routes of the admin API are called from an admin page, with
 * the session and CSRF contract of helpers/api.ts.
 */

import { expect, type Locator, type Page } from '@playwright/test';
import { adminFetch } from './api';

const API = '/-/api/v1';

/** The host element of the widget: one on every page that has it. */
export function widgetHost(page: Page): Locator {
  return page.locator('[data-toon-feedback-host]');
}

/** The script tag Toon adds, found by the path of the content-addressed bundle. */
export function widgetScript(page: Page): Locator {
  return page.locator('script[src^="/-/feedback/widget."]');
}

/** The pill button that opens and closes the panel. */
export function feedbackButton(page: Page): Locator {
  return widgetHost(page).getByRole('button', { name: 'Feedback', exact: true });
}

/** The panel: a non-modal dialog, labelled by its heading. */
export function feedbackPanel(page: Page): Locator {
  return widgetHost(page).getByRole('dialog');
}

export async function openPanel(page: Page): Promise<Locator> {
  await feedbackButton(page).click();
  const panel = feedbackPanel(page);
  await expect(panel).toBeVisible();
  return panel;
}

/** Writes the comment, and a name when one is given, sends it and waits for the thanks. */
export async function sendFeedback(panel: Locator, comment: string, name?: string): Promise<void> {
  await panel.getByRole('textbox', { name: 'Je feedback' }).fill(comment);
  if (name !== undefined) {
    await panel.getByRole('textbox', { name: /^Je naam/ }).fill(name);
  }
  await panel.getByRole('button', { name: 'Versturen' }).click();
  await expect(panel.getByRole('heading', { name: 'Bedankt, je feedback is verstuurd.' })).toBeVisible();
}

/**
 * Prepares a wait for the selectionchange at which `text` is selected. The
 * widget listens for selectionchange from the moment it mounts, so a listener
 * added later runs after the widget's own: once the wait is over, the widget
 * has had its turn at that selection.
 */
export async function selectionSeen(page: Page, text: string): Promise<() => Promise<void>> {
  await page.evaluate((text) => {
    const seen = new Promise<void>((resolve) => {
      const check = () => {
        if (document.getSelection()?.toString().includes(text)) {
          document.removeEventListener('selectionchange', check);
          resolve();
        }
      };
      document.addEventListener('selectionchange', check);
    });
    Object.assign(window, { toonSelectionSeen: seen });
  }, text);
  return async () => {
    await page.evaluate(() => (window as unknown as { toonSelectionSeen: Promise<void> }).toonSelectionSeen);
  };
}

/**
 * Whether the widget lies over the middle of this element of the page, also
 * one inside a web component of the page: the document answers with the host
 * of the shadow root on top, the widget's or the component's.
 */
export async function widgetCovers(element: Locator): Promise<boolean> {
  return element.evaluate((node) => {
    const box = node.getBoundingClientRect();
    const top = node.ownerDocument.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    return top !== null && top.hasAttribute('data-toon-feedback-host');
  });
}

/** How far the page reaches past the window sideways: 0 without a horizontal scrollbar. */
export async function sidewaysOverflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

/** How far the feedback button lies from the window's left, right and bottom edges, in CSS px. */
export async function triggerInset(page: Page): Promise<{ left: number; right: number; bottom: number }> {
  return feedbackButton(page).evaluate((button) => {
    const box = button.getBoundingClientRect();
    const root = button.ownerDocument.documentElement;
    return { left: box.left, right: root.clientWidth - box.right, bottom: root.clientHeight - box.bottom };
  });
}

/**
 * Which element of the widget lies on top where a ring or a mark of the place
 * from a link (`drawn`) overlaps the button or the panel (`other`). Measured
 * at one point inside both: inside the ring's circle or the mark, and inside
 * the part of the other that is clear of its rounded corners, such as the
 * straight middle of the button's pill. `shared` says whether there is such a
 * point. Asked of the shadow root's elementFromPoint twice: `press`, what a
 * press there reaches while the ring or the mark takes none, as it always
 * does, and `top`, what is drawn on top, with it taking presses for a moment.
 */
export async function drawnOnTop(
  page: Page,
  drawn: string,
  other: string,
): Promise<{ shared: boolean; press: string; top: string }> {
  return widgetHost(page).evaluate(
    (host, { drawn, other }) => {
      const root = host.shadowRoot!;
      const mark = root.querySelector<HTMLElement>(drawn)!;
      const below = root.querySelector(other)!;
      const box = mark.getBoundingClientRect();
      const under = below.getBoundingClientRect();
      const corner = Math.min(parseFloat(getComputedStyle(below).borderTopLeftRadius), under.height / 2);
      const clear = {
        left: under.left + corner,
        top: under.top + 2,
        right: under.right - corner,
        bottom: under.bottom - 2,
      };
      const inner = { left: box.left + 2, top: box.top + 2, right: box.right - 2, bottom: box.bottom - 2 };
      const round = mark.classList.contains('pin');
      const centre = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
      const x = Math.min(Math.max(centre.x, clear.left, inner.left), clear.right, inner.right);
      const y = Math.min(Math.max(centre.y, clear.top, inner.top), clear.bottom, inner.bottom);
      const within = (area: { left: number; top: number; right: number; bottom: number }) =>
        x >= area.left && x <= area.right && y >= area.top && y <= area.bottom;
      const shared =
        within(clear) && within(inner) && (!round || Math.hypot(x - centre.x, y - centre.y) <= box.width / 2 - 3);
      const name = (hit: Element | null) => {
        if (hit === mark) return round ? 'ring' : 'mark';
        if (hit?.closest('.trigger')) return 'button';
        return hit?.closest('[role="dialog"]') ? 'panel' : 'page';
      };
      const press = name(root.elementFromPoint(x, y));
      mark.style.pointerEvents = 'auto';
      const top = name(root.elementFromPoint(x, y));
      mark.style.pointerEvents = '';
      return { shared, press, top };
    },
    { drawn, other },
  );
}

/** Where something lies in the window, in CSS px. */
export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/**
 * The open overlay, measured in one go: the window, the bar, the bar's way
 * out, and what the bar has to keep clear of. That is the crosshair on the
 * pointing overlay, and the frozen picture and the frame on the frame
 * overlays. Asked of the shadow root's elementFromPoint: `stopOnTop`, whether
 * the way out is what a press on its middle reaches, and `frameUnderBar`, at
 * how many points just inside the frame's corners and the middles of its edges
 * the bar lies on top. The picture and the frame let presses through, so a
 * point the bar leaves free answers with the stage below them.
 */
export interface OverlayLayout {
  window: { width: number; height: number };
  bar: Box;
  stop: Box;
  stopOnTop: boolean;
  crosshair: Box | null;
  picture: Box | null;
  frame: Box | null;
  frameUnderBar: number;
}

export async function overlayLayout(page: Page): Promise<OverlayLayout> {
  return widgetHost(page).evaluate((host) => {
    const root = host.shadowRoot!;
    const box = (selector: string) => {
      const element = root.querySelector(`.overlay ${selector}`);
      if (element === null) return null;
      const { left, top, right, bottom } = element.getBoundingClientRect();
      return { left, top, right, bottom };
    };
    const bar = root.querySelector('.overlay .bar')!;
    const stop = box('.bar button')!;
    const atStop = root.elementFromPoint((stop.left + stop.right) / 2, (stop.top + stop.bottom) / 2);
    const frame = box('.frame');
    let frameUnderBar = 0;
    if (frame !== null) {
      const xs = [frame.left + 1, (frame.left + frame.right) / 2, frame.right - 1];
      const ys = [frame.top + 1, (frame.top + frame.bottom) / 2, frame.bottom - 1];
      for (const x of xs) {
        for (const y of ys) {
          const hit = root.elementFromPoint(x, y);
          if (hit !== null && bar.contains(hit)) frameUnderBar += 1;
        }
      }
    }
    const page = host.ownerDocument.documentElement;
    return {
      window: { width: page.clientWidth, height: page.clientHeight },
      bar: box('.bar')!,
      stop,
      stopOnTop: atStop !== null && root.querySelector('.overlay .bar button')!.contains(atStop),
      crosshair: box('.crosshair'),
      picture: box('.frozen'),
      frame,
      frameUnderBar,
    };
  });
}

/**
 * How a picture the widget holds compares with a screenshot of the window,
 * block by block over a region of the window: blocks of 8 by 8 pixels, those
 * a JPEG is coded in, each by its mean colour. A block differs where those
 * means differ by more than 48, summed over red, green and blue. Text that
 * the copy of the page draws a pixel or two narrower moves a mean little,
 * and a picture that is blank or shows another part of the page moves most.
 * The result is the share of blocks that differ, and the share in which the
 * window shows more than white. Blocks that touch `skip` do not count. The
 * picture is read in the page, where the address of its file is valid. Both
 * are drawn at the size of the screenshot.
 */
export async function pictureAgainstWindow(
  picture: Locator,
  window: Buffer,
  region: Box,
  skip: Box,
): Promise<{ differ: number; inked: number }> {
  return picture.evaluate(
    async (image: HTMLImageElement, { png, region, skip }) => {
      await image.decode();
      const bytes = Uint8Array.from(atob(png), (char) => char.charCodeAt(0));
      const shown = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
      const pixels = (source: CanvasImageSource) => {
        const canvas = new OffscreenCanvas(shown.width, shown.height);
        const context = canvas.getContext('2d')!;
        context.drawImage(source, 0, 0, shown.width, shown.height);
        return context.getImageData(0, 0, shown.width, shown.height).data;
      };
      const seen = pixels(shown);
      const held = pixels(image);
      const size = 8;
      const means = (data: Uint8ClampedArray, left: number, top: number) => {
        const sums = [0, 0, 0];
        for (let y = top; y < top + size; y += 1) {
          for (let x = left; x < left + size; x += 1) {
            for (let channel = 0; channel < 3; channel += 1) {
              sums[channel]! += data[(y * shown.width + x) * 4 + channel]!;
            }
          }
        }
        return sums.map((sum) => sum / (size * size));
      };
      let counted = 0;
      let differ = 0;
      let inked = 0;
      const bottom = Math.min(shown.height, region.bottom);
      const right = Math.min(shown.width, region.right);
      for (let top = Math.max(0, Math.ceil(region.top)); top + size <= bottom; top += size) {
        for (let left = Math.max(0, Math.ceil(region.left)); left + size <= right; left += size) {
          if (left < skip.right && left + size > skip.left && top < skip.bottom && top + size > skip.top) continue;
          const inWindow = means(seen, left, top);
          const inPicture = means(held, left, top);
          counted += 1;
          if (inWindow.reduce((sum, mean, channel) => sum + Math.abs(mean - inPicture[channel]!), 0) > 48) differ += 1;
          if (inWindow[0]! + inWindow[1]! + inWindow[2]! < 700) inked += 1;
        }
      }
      return { differ: differ / counted, inked: inked / counted };
    },
    { png: window.toString('base64'), region, skip },
  );
}

// The shapes of the admin API (spec §16.11), as far as the scenarios read them.

export interface FeedbackItem {
  id: string;
  kind: 'page' | 'point' | 'text';
  status: 'new' | 'seen' | 'resolved';
  comment: string;
  quote: string | null;
  page: { path: string; title: string | null; url: string };
  target: 'live' | 'preview';
  previewRef: string | null;
  versionId: string | null;
  author: { kind: 'account' | 'name' | 'anonymous'; name: string | null; email: string | null };
  hasScreenshot: boolean;
  replyCount: number;
  statusChangedBy: string | null;
}

export interface FeedbackSelector {
  type: 'TextQuoteSelector' | 'TextPositionSelector' | 'CssSelector';
  exact?: string;
  value?: string;
}

export interface FeedbackDetail extends FeedbackItem {
  anchor: {
    selectors: FeedbackSelector[];
    point: { x: number; y: number; pageX: number; pageY: number } | null;
  } | null;
  viewport: { width: number; height: number; dpr: number } | null;
  highlightUrl: string | null;
  replies: Array<{ id: string; author: { kind: 'member' | 'giver'; name: string | null }; body: string }>;
}

export interface FeedbackPage {
  items: FeedbackItem[];
  counts: { new: number; seen: number; resolved: number };
}

/** Every item of a site, whatever its status. */
export async function feedbackPage(page: Page, group: string, site: string): Promise<FeedbackPage> {
  const result = await adminFetch(page, 'GET', `${API}/sites/${group}/${site}/feedback?status=all&limit=100`);
  expect(result.status, result.text).toBe(200);
  return result.json as FeedbackPage;
}

/** The one item with this comment: the scenarios give every item a comment of its own. */
export async function feedbackWithComment(
  page: Page,
  group: string,
  site: string,
  comment: string,
): Promise<FeedbackItem> {
  const matches = (await feedbackPage(page, group, site)).items.filter((item) => item.comment === comment);
  expect(matches, `one feedback item with the comment ${JSON.stringify(comment)}`).toHaveLength(1);
  return matches[0]!;
}

export async function feedbackDetail(page: Page, group: string, site: string, id: string): Promise<FeedbackDetail> {
  const result = await adminFetch(page, 'GET', `${API}/sites/${group}/${site}/feedback/${id}`);
  expect(result.status, result.text).toBe(200);
  return result.json as FeedbackDetail;
}

export interface BinaryResult {
  status: number;
  headers: Record<string, string>;
  bytes: Buffer;
}

/** A GET from the admin page that keeps the body as bytes, for the screenshot. */
export async function adminFetchBytes(page: Page, path: string): Promise<BinaryResult> {
  const result = await page.evaluate(async (path) => {
    const response = await fetch(path);
    const bytes = new Uint8Array(await response.arrayBuffer());
    let binary = '';
    for (const byte of bytes) {
      binary += String.fromCharCode(byte);
    }
    const headers: Record<string, string> = {};
    response.headers.forEach((value, name) => {
      headers[name] = value;
    });
    return { status: response.status, headers, data: btoa(binary) };
  }, path);
  return { status: result.status, headers: result.headers, bytes: Buffer.from(result.data, 'base64') };
}

/**
 * Site feedback end to end (spec §13, §16): a shielded preview
 * with the real widget across both origins. A visitor gives feedback on the
 * whole page, on a point with a screenshot and on a text selection, which
 * takes a screenshot only when asked. Someone
 * gives it with their account after the consent page, which takes no press
 * in its first second and none without its box ticked, and finds it again
 * under "My feedback". A visitor hides the button until the browser closes.
 * The keyboard and a 320 px window each get their own checks, also inside the
 * page's own web components, and so do the overlays at the window of a phone
 * and at 400% zoom. The site team handles it all in the Feedback tab of the
 * admin SPA and with the CLI, also once a preview is gone. A member of the
 * team who never said yes to feedback with their account sends without a
 * name on a preview only the team can see, and goes straight to the consent
 * page where the site takes feedback with an account only. Someone let in by
 * a secret link alone signs in, says yes and sends with their account, and no
 * page token ever goes into an address. A page opened before its site got a
 * new address says so when it sends, and an emoji held together by a
 * zero-width joiner arrives whole, from a visitor and from the team.
 *
 * The widget's platform adapter (frontend/src/feedback-widget/platform.ts)
 * runs only here, in Chromium: the design leaves it out of the unit tests.
 * Visitors get browser contexts of their own on the content host. What
 * arrived is checked through the admin API from the admin page, the way the
 * SPA reads it. Its own group, independent of the other specs.
 */

import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import {
  expect,
  test,
  type APIRequestContext,
  type BrowserContext,
  type Locator,
  type Page,
  type Response,
} from '@playwright/test';
import { adminFetch, body, newHostContext, uploadViaSession } from '../helpers/api';
import { cliLoginToken, runToon, writeCliSession } from '../helpers/cli';
import {
  ADMIN_EMAIL,
  ADMIN_HOST,
  ADMIN_SUB,
  ADMIN_URL,
  CONTENT_HOST,
  CONTENT_URL,
  CONTEXT_OPTIONS,
} from '../helpers/environment';
import {
  adminFetchBytes,
  type Box,
  commentEditor,
  commentText,
  drawnOnTop,
  feedbackButton,
  feedbackDetail,
  feedbackPage,
  feedbackPanel,
  feedbackWithComment,
  openPanel,
  overlayLayout,
  pictureAgainstWindow,
  selectionSeen,
  sendFeedback,
  sidewaysOverflow,
  triggerInset,
  widgetCovers,
  widgetHost,
  widgetScript,
} from '../helpers/feedback';
import { fillMockLogin, logInOnAdmin, onMockLoginPage, visitWithLogin } from '../helpers/oidc';
import { makeTarGz } from '../helpers/tar';

const GROUP = 'e2e-feedback';
const SITE = 'website';
const SITE_PATH = `/${GROUP}/${SITE}`;
const API = '/-/api/v1';
const PREVIEW_REF = 'pr-1';
const PREVIEW_PATH = `${SITE_PATH}/_preview/${PREVIEW_REF}/`;
const PREVIEW_URL = `${CONTENT_URL}${PREVIEW_PATH}`;
const LIVE_URL = `${CONTENT_URL}${SITE_PATH}/`;
// A page longer than the window, with one link at the bottom right.
const LONG_PAGE = 'lange-pagina.html';
// A page whose skip link leads to the whole main below a sticky header, with
// the site's navigation at the top right and a small control at the bottom
// right.
const HEADER_PAGE = 'met-kop.html';
// Pages built with a web component of the site's own, whose links live in an
// open shadow root, as with a design system's components: one link at the
// bottom right, and three across the footer.
const COMPONENT_PAGE = 'onderdeel.html';
const COMPONENTS_PAGE = 'onderdelen.html';
// A page with a list of 30 links down its right-hand side, where the note
// about a place that is gone and the open panel of a wide window lie.
const LIST_PAGE = 'lijst.html';
// A page laid out as an app: a header that stays, and a main part that
// scrolls on its own, while the window itself never scrolls.
const PANE_PAGE = 'paneel.html';
// A long page under a bar fixed at the top of the window, in this colour.
const BAR_PAGE = 'vaste-balk.html';
const BAR_COLOUR = [0x15, 0x42, 0x73];
// A long page with the root rule of Material for MkDocs, whose root hides
// what overflows it sideways: html { height: 100%; overflow-x: hidden }.
const MATERIAL_PAGE = 'materiaal.html';
// A page of rules for the sums, with a listing as high as three windows of
// 720 px.
const LISTING_PAGE = 'rekenregels.html';
// Line 110 of the 120 of that listing, near its end.
const LISTED = 'regel 110: bedrag_110 = som(onderdeel_110)';
// A long page whose root and body are one window high and clip sideways
// only, so what lies below the first window still shows. Further down: a
// chart whose plot area is an svg of its own, a word in a link whose
// overflow class does nothing there, and two lines that a box does hide,
// one below a box of 40 px and one right of a line that clips sideways.
const CLIP_PAGE = 'knippen.html';
// A page whose body hides what overflows it sideways, with a menu parked
// right of the window and a link right of the window's middle.
const OFFCANVAS_PAGE = 'opzij.html';
// A page with a chart as an image and a band with a background image, both
// files of the site's own.
const IMAGES_PAGE = 'beelden.html';
const PRIVACY_URL = `${ADMIN_URL}/-/privacy`;
// A small phone: the width at which content has to reflow (WCAG 1.4.10).
const NARROW = { width: 320, height: 640 };
// Where the overlays have to work: a phone, and 400% zoom of a window of
// 1280x1024 (the one WCAG 1.4.10 measures reflow in) and of 1280x720.
const OVERLAY_WINDOWS = [
  { width: 375, height: 667 },
  { width: 320, height: 256 },
  { width: 320, height: 180 },
];
// The button in its own corner: this far from the right and the bottom edge.
const CORNER = { right: 16, bottom: 16 };

// A visitor with an account who is not a member of the group: on a public
// site anyone who can sign in may give feedback with their account.
const GIVER_SUB = 'feedback-gever';
const GIVER_EMAIL = 'feedback-gever@toon.local';
const GIVER_NAME = 'Femke de Gever';
// Someone outside the team whom a secret link alone lets see a preview.
const OUTSIDER_SUB = 'link-gast';
const OUTSIDER_EMAIL = 'link-gast@toon.local';
const OUTSIDER_NAME = 'Lotte van de Link';

const PARAGRAPH = 'De begroting voor dit jaar bedraagt 2,4 miljoen euro voor het onderhoud van de dijken.';
// The same paragraph after the team corrected the amount.
const CORRECTED = 'De begroting voor dit jaar bedraagt 2,5 miljoen euro voor het onderhoud van de dijken.';
const PLACE_GONE = 'De plek van deze feedback is op deze pagina niet meer te vinden.';
// A link to the paragraph of the start page, opened on a page without it.
const NOWHERE = `#toon-feedback=${Buffer.from(JSON.stringify({ k: 'text', q: PARAGRAPH })).toString('base64url')}`;

const COMMENT_PAGE = 'De pagina mist een datum.';
const COMMENT_POINT = 'Dit vak is te klein op een telefoon.';
const COMMENT_AREA = 'Dit hele blok mag weg.';
const COMMENT_TEXT = 'Klopt dit bedrag wel?';
const COMMENT_ACCOUNT = 'Met mijn account: waar staat de bron?';
const COMMENT_REFUSED = 'Deze komt nooit aan.';
const COMMENT_TEAM = 'Van iemand uit het team, zonder naam.';
const COMMENT_KEPT = 'Nog niet verstuurd: de lijst is te lang.';
const COMMENT_LINK = 'Via de geheime link, met mijn account.';
const COMMENT_MOVED = 'Verstuurd vanaf het nieuwe adres.';
const COMMENT_PINNED = 'Deze link hoort bij een ander hoofdstuk.';
const COMMENT_BAR = 'De link in de balk werkt niet.';
const COMMENT_LOW = 'Hier onderaan klopt de som niet.';
const COMMENT_LISTED = 'Deze regel telt dubbel.';
const NAME_POINT = 'Jan Jansen';
const REPLY = 'De bron komt in de volgende preview.';

// An emoji that is one picture only with the zero-width joiner (U+200D)
// between its parts, a woman and a laptop: the emoji pickers of phones and
// laptops type it so. The joiner and the soft hyphen (U+00AD) are written by
// code point, so no invisible character sits in this file.
const TECHNOLOGIST = String.fromCodePoint(0x1f469, 0x200d, 0x1f4bb);
const SOFT_HYPHEN = String.fromCodePoint(0xad);
const COMMENT_JOINED = `De tabel is op mijn laptop goed te lezen ${TECHNOLOGIST}`;
// A reply with a word pasted from a page of the site, soft hyphens and all.
const REPLY_PASTED = `Dank je ${TECHNOLOGIST}! De zorg${SOFT_HYPHEN}verzekerings${SOFT_HYPHEN}wet staat er nu goed in.`;
const REPLY_JOINED = `Dank je ${TECHNOLOGIST}! De zorgverzekeringswet staat er nu goed in.`;

// The site's address while one scenario has it moved.
const SITE_MOVED = 'website-verhuisd';
const MOVED_PREVIEW_URL = `${CONTENT_URL}/${GROUP}/${SITE_MOVED}/_preview/${PREVIEW_REF}/`;

const ON_PREVIEWS = { enabled: true, identity: 'anonymous', live: false };

function distFor(heading: string, paragraph = PARAGRAPH): Buffer {
  const paragraphs = Array.from(
    { length: 40 },
    (_, index) => `<p id="p${index}">Alinea ${index}: tekst over onderdeel ${index} van de begroting.</p>`,
  ).join('\n');
  return makeTarGz({
    'index.html':
      '<!doctype html><html lang="nl"><head><meta charset="utf-8">' +
      '<title>Feedback E2E</title><link rel="stylesheet" href="stijl.css"></head>' +
      `<body><main><h1>${heading}</h1>\n<p id="alinea">${paragraph}</p>\n` +
      '<div id="doel">Wijs dit vak aan</div>\n</main></body></html>',
    [LONG_PAGE]:
      '<!doctype html><html lang="nl"><head><meta charset="utf-8">' +
      '<title>Lange pagina</title><link rel="stylesheet" href="stijl.css"></head>' +
      `<body><main class="lang"><h1>${heading}</h1></main>\n` +
      '<footer><a href="contact.html">Contact</a></footer></body></html>',
    [HEADER_PAGE]:
      '<!doctype html><html lang="nl" class="met-kop"><head><meta charset="utf-8">' +
      '<title>Met kop</title><link rel="stylesheet" href="stijl.css"></head>' +
      '<body><a class="skip" href="#inhoud">Naar de inhoud</a>\n' +
      '<header class="kop"><span>Website</span>' +
      '<nav><a href="./">Start</a> <a href="english.html">English</a></nav></header>\n' +
      `<main id="inhoud" class="lang" tabindex="-1"><h1>${heading}</h1>\n` +
      '<p id="inleiding">Hier begint de inhoud.</p></main>\n' +
      '<footer><button type="button">Naar boven</button></footer></body></html>',
    'english.html':
      '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>English</title></head>' +
      '<body><main><h1>English</h1></main></body></html>',
    [COMPONENT_PAGE]:
      '<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>Onderdeel</title>' +
      '<link rel="stylesheet" href="stijl.css"><script src="onderdelen.js"></script></head>' +
      `<body><main class="lang"><h1>${heading}</h1></main>\n` +
      '<footer><site-links data-links="Contact"></site-links></footer></body></html>',
    [COMPONENTS_PAGE]:
      '<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>Onderdelen</title>' +
      '<link rel="stylesheet" href="stijl.css"><script src="onderdelen.js"></script></head>' +
      `<body><main class="lang"><h1>${heading}</h1></main>\n` +
      '<footer><site-links class="rij" data-links="Privacy,Contact,English"></site-links></footer></body></html>',
    'onderdelen.js':
      "customElements.define('site-links', class extends HTMLElement {\n" +
      '  connectedCallback() {\n' +
      '    if (this.shadowRoot !== null) return;\n' +
      "    const root = this.attachShadow({ mode: 'open' });\n" +
      "    for (const name of this.dataset.links.split(',')) {\n" +
      "      const link = document.createElement('a');\n" +
      "      link.href = 'english.html';\n" +
      '      link.textContent = name;\n' +
      '      root.append(link);\n' +
      '    }\n' +
      '  }\n' +
      '});\n',
    // The page records whether an Escape pressed on it reached it whole.
    [LIST_PAGE]:
      '<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>Lijst</title>' +
      '<link rel="stylesheet" href="stijl.css"><script>' +
      "addEventListener('keydown', (event) => { if (event.key === 'Escape') " +
      "document.body.dataset.escape = event.defaultPrevented ? 'prevented' : 'received'; });" +
      '</script></head>' +
      `<body class="met-lijst"><main><h1>${heading}</h1>\n` +
      `<p>${'Het onderhoud van de dijken gaat door. '.repeat(30)}</p></main>\n<aside><ul>` +
      Array.from({ length: 30 }, (_, index) => `<li><a href="#document-${index + 1}">Document ${index + 1}</a></li>`)
        .join('') +
      '</ul></aside></body></html>',
    [PANE_PAGE]:
      '<!doctype html><html lang="nl" class="paneel"><head><meta charset="utf-8"><title>Paneel</title>' +
      '<link rel="stylesheet" href="stijl.css"></head>' +
      `<body><header class="balk">Rapport</header><main id="paneel"><h1>${heading}</h1>\n${paragraphs}` +
      '</main></body></html>',
    [BAR_PAGE]:
      '<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>Vaste balk</title>' +
      '<link rel="stylesheet" href="stijl.css"></head>' +
      '<body class="onder-balk"><header class="vaste-balk"><a href="contact.html">Contact</a></header>' +
      `<main><h1>${heading}</h1>\n${paragraphs}</main></body></html>`,
    [MATERIAL_PAGE]:
      '<!doctype html><html lang="nl" class="materiaal"><head><meta charset="utf-8"><title>Materiaal</title>' +
      '<link rel="stylesheet" href="stijl.css"></head>' +
      `<body><main><h1>${heading}</h1>\n${paragraphs}</main></body></html>`,
    [LISTING_PAGE]:
      '<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>Rekenregels</title>' +
      '<link rel="stylesheet" href="stijl.css"></head>' +
      `<body><main><h1>${heading}</h1>\n<p>De regels waarmee de begroting is berekend.</p>\n<pre id="regels">` +
      Array.from({ length: 120 }, (_, index) => `regel ${index + 1}: bedrag_${index + 1} = som(onderdeel_${index + 1})`)
        .join('\n') +
      '</pre>\n<p>Einde van de regels.</p></main></body></html>',
    [CLIP_PAGE]:
      '<!doctype html><html lang="nl" class="knippen"><head><meta charset="utf-8"><title>Knippen</title>' +
      '<link rel="stylesheet" href="stijl.css"></head>' +
      `<body><main><h1>${heading}</h1>\n${paragraphs}\n` +
      '<p id="ver">Het doel van deze alinea</p>\n' +
      '<svg width="420" height="220" viewBox="0 0 420 220"><svg x="40" y="30" width="360" height="170">' +
      '<rect id="staaf" x="20" y="40" width="60" height="130" fill="#154273"/></svg></svg>\n' +
      '<p><a href="#rapport" class="afkap">Lees het <strong id="sterk">volledige rapport</strong> ' +
      'over 2026</a></p>\n' +
      '<div class="dicht"><p>Eerste regel</p><p>Tweede regel</p>' +
      '<p id="verborgen">Derde regel buiten zicht</p></div>\n' +
      '<div class="smal">Begin van de regel en dan heel ver naar rechts ' +
      '<strong id="weg">verborgen woord</strong></div>\n' +
      `<p>${'Slot van de pagina. '.repeat(60)}</p></main></body></html>`,
    [OFFCANVAS_PAGE]:
      '<!doctype html><html lang="nl" class="opzij"><head><meta charset="utf-8"><title>Opzij</title>' +
      '<link rel="stylesheet" href="stijl.css"></head>' +
      '<body><nav class="opzij-menu" aria-label="Menu"><a href="./">Start</a></nav>' +
      `<main><h1>${heading}</h1>\n${paragraphs}\n<p class="rechts"><a id="rechts" href="#lees">Lees meer</a></p>\n` +
      `<p>${'Slot van de pagina. '.repeat(60)}</p></main></body></html>`,
    [IMAGES_PAGE]:
      '<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>Beelden</title>' +
      '<link rel="stylesheet" href="stijl.css"></head>' +
      `<body><main><h1>${heading}</h1>\n` +
      '<img src="grafiek.svg" width="120" height="60" alt="Grafiek van de begroting">\n' +
      '<div class="band">Onderhoud per jaar</div></main></body></html>',
    // The site's own images are drawings: the archive takes text files only.
    'grafiek.svg':
      '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="60">' +
      '<rect width="120" height="60" fill="#154273"/></svg>',
    'achtergrond.svg':
      '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#dfe7f1"/></svg>',
    // Everything on the left, clear of the panel and of the bar that the
    // pointing overlay shows at the top. From 38rem on, the paragraph takes
    // more lines at 320 px than at 1280 px whatever the font, so the box
    // under it moves when the window narrows. The footer link of the long
    // page ends up at the bottom right, where the button sits, and so does
    // the footer button of the page with the header, which is wider than the
    // Feedback button. That page's main starts right below its header: no
    // margin collapses through it. The web component spreads its three links
    // across the footer, and the list page's links run down its right-hand
    // side, under the note about a place that is gone and the open panel. On
    // the page laid out as an app, only the main part below the header
    // scrolls. The dark blue bar of the page with the fixed bar stays at the
    // top of the window while the page scrolls under it. On the page with
    // Material's root rule, every other paragraph lies on a dark band, and
    // the paragraphs are high enough to scroll the page more than a window.
    // The listing on the page of rules is 120 lines of 18 px. On the page
    // that clips, root and body are one window high and clip sideways only.
    // On the page with the menu, the body hides what overflows it sideways,
    // and the menu waits right of the window. The band on the page with
    // images has a background image.
    'stijl.css':
      'main { max-width: 40rem; } #doel { width: 240px; height: 120px; margin: 32px 0; background: #dfe7f1; }\n' +
      '.lang { min-height: 1200px; } footer { padding: 16px 0; text-align: right; }\n' +
      'html.met-kop { scroll-padding-top: 80px; } #inhoud { display: flow-root; max-width: none; }\n' +
      'footer button { width: 200px; }\n' +
      '.kop { position: sticky; top: 0; height: 80px; display: flex; align-items: center; ' +
      'justify-content: space-between; background: #ffffff; } .kop nav a { margin-left: 16px; }\n' +
      '.skip { position: absolute; left: -9999px; } .skip:focus { left: 8px; top: 8px; }\n' +
      'site-links.rij { display: flex; justify-content: space-between; }\n' +
      '.met-lijst { display: flex; justify-content: space-between; } .met-lijst aside { width: 280px; }\n' +
      'html.paneel, html.paneel body { height: 100%; margin: 0; overflow: hidden; } .balk { height: 56px; }\n' +
      '#paneel { position: absolute; top: 56px; bottom: 0; left: 0; right: 0; overflow: auto; padding: 0 16px; }\n' +
      '.onder-balk { margin: 0; padding: 64px 16px 0; } .vaste-balk { position: fixed; top: 0; left: 0; ' +
      'right: 0; height: 56px; background: #154273; } .vaste-balk a { color: #ffffff; line-height: 56px; ' +
      'margin-left: 16px; }\n' +
      'html.materiaal { height: 100%; overflow-x: hidden; } html.materiaal p { margin: 0; padding: 16px; }\n' +
      'html.materiaal p:nth-of-type(odd) { background: #154273; color: #ffffff; }\n' +
      '#regels { font: 15px/18px monospace; margin: 0; }\n' +
      'html.knippen, html.knippen body { height: 100%; overflow-x: clip; }\n' +
      '.afkap { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }\n' +
      '.dicht { height: 40px; overflow: hidden; } .dicht p { margin: 0; line-height: 24px; }\n' +
      '.smal { width: 200px; overflow-x: clip; white-space: nowrap; }\n' +
      'html.opzij body { overflow-x: hidden; } .opzij-menu { position: absolute; top: 0; right: -360px; ' +
      'width: 320px; height: 100vh; } .rechts { margin: 0 0 0 700px; width: 200px; }\n' +
      '.band { height: 60px; background: url(achtergrond.svg); }',
  });
}

/**
 * The status filter of the Feedback tab, button by button: its label, whether
 * the label is cut short, and whether the button sticks out of the inbox. A
 * label is cut when its text overflows its own box, or when the glyphs reach
 * past the button, which clips them.
 */
async function statusFilter(page: Page): Promise<Array<{ label: string; cut: boolean; outside: boolean }>> {
  return page.evaluate(() => {
    const within = (inner: DOMRect, outer: DOMRect) =>
      inner.left >= outer.left - 0.5 && inner.right <= outer.right + 0.5;
    const inbox = document.querySelector('section[aria-labelledby="heading-feedback-inbox"]')!.getBoundingClientRect();
    return [...document.querySelectorAll('[data-testid="feedback-status"] nldd-toggle-button')].map((button) => {
      const text = button.shadowRoot!.querySelector('.toggle-button__text')!;
      const glyphs = document.createRange();
      glyphs.selectNodeContents(text);
      const box = button.getBoundingClientRect();
      return {
        label: text.textContent ?? '',
        cut: text.scrollWidth > text.clientWidth || !within(glyphs.getBoundingClientRect(), box),
        outside: !within(box, inbox),
      };
    });
  });
}

/** Opens an item of the Feedback tab and deletes it, as an admin of the site does. */
async function deleteFromTab(page: Page, row: Locator): Promise<void> {
  await row.click();
  await page.getByTestId('feedback-delete').click();
  // Every confirmation dialog of the page is in the DOM: only the open one shows.
  await page.getByTestId('confirm-continue').filter({ visible: true }).click();
  await expect(row).toHaveCount(0);
}

/**
 * Opens the page of an item from the Feedback tab, as the team does: "View
 * on the page" opens it in a new tab, at its top.
 */
async function viewOnPage(id: string): Promise<Page> {
  await teamPage.goto(`${ADMIN_URL}/${GROUP}/${SITE}/feedback`);
  await teamPage.getByTestId(`feedback-${id}`).click();
  const opened = teamContext.waitForEvent('page');
  await teamPage.getByTestId('feedback-view').click();
  const page = await opened;
  await page.waitForLoadState();
  return page;
}

/** The middle of an element in the window, where a press by coordinates lands on it. */
async function middleOf(element: Locator): Promise<{ x: number; y: number }> {
  const box = (await element.boundingBox())!;
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/** The fragment of a link to a place. The widget reads any such link: no item is needed for it. */
function linkTo(place: object): string {
  return `#toon-feedback=${Buffer.from(JSON.stringify(place)).toString('base64url')}`;
}

/** A place at the middle of the element a selector finds. */
function pointOn(selector: string): object {
  return { k: 'point', s: selector, x: 0.5, y: 0.5, px: null, py: null, q: null };
}

/** How far the pin lies from where a point stored on its element is now, in px. */
async function pinOffset(pin: Locator, element: Locator, point: { x: number; y: number }): Promise<number> {
  const mark = (await pin.boundingBox())!;
  const box = (await element.boundingBox())!;
  return Math.hypot(
    mark.x + mark.width / 2 - (box.x + point.x * box.width),
    mark.y + mark.height / 2 - (box.y + point.y * box.height),
  );
}

/**
 * The marks of a highlighted quote on the element it is in: how many there
 * are, how many stick out of the element by more than a rounded pixel, and
 * how far right the furthest reaches. Read in one go: the widget draws the
 * marks anew whenever the layout changes, also once right after it shows them.
 */
async function quoteMarks(page: Page, selector: string): Promise<{ lines: number; out: number; right: number }> {
  return page.evaluate((selector) => {
    const box = document.querySelector(selector)!.getBoundingClientRect();
    const host = document.querySelector('[data-toon-feedback-host]')!;
    const marks = [...host.shadowRoot!.querySelectorAll('.mark')].map((mark) => mark.getBoundingClientRect());
    const sticksOut = (mark: DOMRect) =>
      mark.left < box.left - 1 || mark.top < box.top - 1 || mark.right > box.right + 1 || mark.bottom > box.bottom + 1;
    return {
      lines: marks.length,
      out: marks.filter(sticksOut).length,
      right: Math.max(0, ...marks.map((mark) => mark.right)),
    };
  }, selector);
}

/**
 * The one mark of a highlighted area and the element it is on, in the
 * window. Read in one go, like quoteMarks: a mark found first and measured
 * after can be one the widget has drawn anew in between.
 */
async function areaMark(
  page: Page,
  selector: string,
): Promise<{ marked: { left: number; width: number }; box: { left: number; width: number } }> {
  return page.evaluate((selector) => {
    const box = document.querySelector(selector)!.getBoundingClientRect();
    const host = document.querySelector('[data-toon-feedback-host]')!;
    const marked = host.shadowRoot!.querySelector('.mark')!.getBoundingClientRect();
    return { marked: { left: marked.left, width: marked.width }, box: { left: box.left, width: box.width } };
  }, selector);
}

function inside(box: Box, window: { width: number; height: number }): boolean {
  return box.left >= 0 && box.top >= 0 && box.right <= window.width && box.bottom <= window.height;
}

function overlap(a: Box, b: Box): boolean {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

/** The edges of a box as Playwright measures it. */
function edges(box: { x: number; y: number; width: number; height: number }): Box {
  return { left: box.x, top: box.y, right: box.x + box.width, bottom: box.y + box.height };
}

/** An overlay's way out lies wholly in the window, and a press on it reaches it. */
const WAY_OUT = { stopInWindow: true, stopOnTop: true };

/**
 * The pointing overlay: where its way out is, at which edge the bar is, how
 * high the crosshair's centre is, and whether the crosshair is clear of the
 * bar, both null while no crosshair shows. The measurements come along for a
 * failure.
 */
async function pointing(page: Page) {
  const layout = await overlayLayout(page);
  const crosshair = layout.crosshair;
  return {
    stopInWindow: inside(layout.stop, layout.window),
    stopOnTop: layout.stopOnTop,
    barAt: layout.bar.top + layout.bar.bottom > layout.window.height ? 'bottom' : 'top',
    crosshairY: crosshair === null ? null : (crosshair.top + crosshair.bottom) / 2,
    crosshairClear: crosshair === null ? null : !overlap(crosshair, layout.bar),
    layout,
  };
}

/**
 * A frame overlay: where its way out is, whether the frozen picture starts
 * below the bar, whether the frame lies on the picture and at its top edge,
 * and how many points of the frame have the bar on top. Half a pixel of slack
 * for the scaled picture.
 */
async function framing(page: Page) {
  const layout = await overlayLayout(page);
  const picture = layout.picture!;
  const frame = layout.frame!;
  return {
    stopInWindow: inside(layout.stop, layout.window),
    stopOnTop: layout.stopOnTop,
    pictureBelowBar: picture.top >= layout.bar.bottom,
    frameOnPicture:
      frame.left >= picture.left - 0.5 &&
      frame.top >= picture.top - 0.5 &&
      frame.right <= picture.right + 0.5 &&
      frame.bottom <= picture.bottom + 0.5,
    frameAtTop: Math.abs(frame.top - picture.top) <= 0.5,
    frameUnderBar: layout.frameUnderBar,
    layout,
  };
}

let adminContext: BrowserContext;
let visitorContext: BrowserContext;
let giverContext: BrowserContext;
let teamContext: BrowserContext;
let adminPage: Page;
let visitorPage: Page;
let giverPage: Page;
let teamPage: Page;
let adminApi: APIRequestContext;
let contentApi: APIRequestContext;

let pointId: string;
let textId: string;
let accountId: string;

test.describe.serial('site feedback', () => {
  test.beforeAll(async ({ browser }) => {
    adminContext = await browser.newContext(CONTEXT_OPTIONS);
    visitorContext = await browser.newContext(CONTEXT_OPTIONS);
    // The consent page speaks the language the browser asks for.
    giverContext = await browser.newContext({ ...CONTEXT_OPTIONS, locale: 'nl-NL' });
    // The same admin with a browser that asks for Dutch, which the SPA then
    // speaks: the longest status labels it has.
    teamContext = await browser.newContext({ ...CONTEXT_OPTIONS, locale: 'nl-NL' });
    adminPage = await adminContext.newPage();
    visitorPage = await visitorContext.newPage();
    giverPage = await giverContext.newPage();
    teamPage = await teamContext.newPage();
    adminApi = await newHostContext(ADMIN_HOST);
    contentApi = await newHostContext(CONTENT_HOST);
    await logInOnAdmin(adminPage, ADMIN_URL, ADMIN_SUB, { email: ADMIN_EMAIL });
    await logInOnAdmin(teamPage, ADMIN_URL, ADMIN_SUB, { email: ADMIN_EMAIL });
  });

  test.afterAll(async () => {
    await adminApi?.dispose();
    await contentApi?.dispose();
    await adminContext?.close();
    await visitorContext?.close();
    await giverContext?.close();
    await teamContext?.close();
  });

  test('an admin switches feedback on for the previews of a shielded site', async () => {
    const group = await adminFetch(adminPage, 'POST', `${API}/groups`, { name: 'Feedback E2E', slug: GROUP });
    expect(group.status).toBe(201);
    const created = await adminFetch(adminPage, 'POST', `${API}/groups/${GROUP}/sites`, {
      title: 'Website',
      slug: SITE,
    });
    expect(created.status).toBe(201);
    // Off and shielded by default.
    expect(created.json).toMatchObject({ feedbackEnabled: false, sandbox: true, openFeedbackCount: 0 });

    const access = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/access`, { base: 'public' });
    expect(access.status).toBe(200);
    const settings = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/feedback-settings`, ON_PREVIEWS);
    expect(settings.status, settings.text).toBe(200);
    expect(settings.json).toMatchObject({ feedbackEnabled: true, feedbackIdentity: 'anonymous', feedbackLive: false });

    const preview = await uploadViaSession(adminPage, GROUP, SITE, distFor('Feedback preview'), PREVIEW_REF);
    expect(preview.status).toBe(201);
    const live = await uploadViaSession(adminPage, GROUP, SITE, distFor('Feedback live'));
    expect(live.status).toBe(201);
  });

  test('Toon adds the widget to the preview and not to the live site', async () => {
    // Only a navigation gets the widget, and that answer is never stored: it
    // carries a token for this one view.
    const document = { 'sec-fetch-dest': 'document' };
    const preview = await contentApi.get(PREVIEW_PATH, { headers: document });
    expect(preview.status()).toBe(200);
    expect(preview.headers()['cache-control']).toBe('no-store');
    expect(preview.headers()['vary']).toContain('Sec-Fetch-Dest');
    const html = await body(preview);
    const tag = /<script src="(\/-\/feedback\/widget\.[0-9a-f]{16}\.js)" defer data-toon-feedback="[\w-]+">/.exec(html);
    expect(tag, html.slice(0, 400)).not.toBeNull();
    const bundle = await contentApi.get(tag![1]!);
    expect(bundle.status()).toBe(200);
    expect(bundle.headers()['content-type']).toContain('text/javascript');
    expect(bundle.headers()['cache-control']).toContain('immutable');

    const live = await contentApi.get(`${SITE_PATH}/`, { headers: document });
    expect(live.status()).toBe(200);
    const liveHtml = await body(live);
    expect(liveHtml).toContain('Feedback live');
    expect(liveHtml).not.toContain('/-/feedback/');

    // In the browser the preview is shielded, an opaque origin, and still
    // gets the button, in an open shadow root. The configuration is read
    // once and taken off the tag.
    await visitorPage.goto(PREVIEW_URL);
    await expect(visitorPage.locator('h1')).toHaveText('Feedback preview');
    expect(await visitorPage.evaluate(() => window.origin)).toBe('null');
    await expect(feedbackButton(visitorPage)).toBeVisible();
    await expect(widgetHost(visitorPage)).toHaveCount(1);
    await expect(widgetHost(visitorPage)).toHaveAttribute('lang', 'nl');
    expect(await widgetHost(visitorPage).evaluate((host) => host.shadowRoot?.mode)).toBe('open');
    await expect(widgetScript(visitorPage)).toHaveCount(1);
    await expect(widgetScript(visitorPage)).not.toHaveAttribute('data-toon-feedback');

    await visitorPage.goto(LIVE_URL);
    await expect(visitorPage.locator('h1')).toHaveText('Feedback live');
    await expect(widgetScript(visitorPage)).toHaveCount(0);
    await expect(widgetHost(visitorPage)).toHaveCount(0);
  });

  test('feedback on the whole page without a name is sent and thanked for', async () => {
    await visitorPage.goto(PREVIEW_URL);
    const panel = await openPanel(visitorPage);
    await expect(panel).toHaveAccessibleName('Feedback op deze pagina');
    await panel.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    await expect(panel.getByText('Je feedback gaat over deze hele pagina.')).toBeVisible();
    await expect(panel.getByRole('textbox', { name: 'Je naam (niet verplicht)' })).toBeEmpty();
    await sendFeedback(panel, COMMENT_PAGE);
    await expect(
      panel.getByText('Je stuurde dit zonder account. Daarom kun je reacties van het siteteam niet zien.'),
    ).toBeVisible();

    const item = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT_PAGE);
    expect(item).toMatchObject({
      kind: 'page',
      status: 'new',
      quote: null,
      target: 'preview',
      previewRef: PREVIEW_REF,
      hasScreenshot: false,
      author: { kind: 'anonymous', name: null, email: null },
      page: { path: '', title: 'Feedback E2E', url: PREVIEW_URL },
    });
    expect(item.versionId).toBeTruthy();
  });

  test('the comment is written in the NLDD editor, in a sandboxed frame of its own that the page cannot reach', async () => {
    await visitorPage.goto(PREVIEW_URL);
    const panel = await openPanel(visitorPage);
    await panel.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    const editor = commentEditor(panel);
    await expect(editor).toBeFocused();
    const frame = panel.locator('iframe.editor.live');
    await expect(frame).toHaveAttribute('sandbox', 'allow-scripts');
    await expect(frame).toHaveAttribute('title', 'Tekstvak met opmaak');
    // The shielded page and the frame both have an opaque origin, and the
    // editor's custom elements are defined in the frame's registry alone.
    expect(await visitorPage.evaluate(() => [window.origin, customElements.get('nldd-text-editor')])).toEqual([
      'null',
      undefined,
    ]);
    const inside = visitorPage.frames().find((candidate) => /\/-\/feedback\/editor\.[0-9a-f]{16}\.html$/.test(candidate.url()))!;
    expect(await inside.evaluate(() => [window.origin, customElements.get('nldd-text-editor') !== undefined])).toEqual([
      'null',
      true,
    ]);

    await visitorPage.keyboard.type('Dit is **vet**.');
    await visitorPage.keyboard.press('Enter');
    await visitorPage.keyboard.press('Enter');
    await visitorPage.keyboard.type('- een');
    await visitorPage.keyboard.press('Enter');
    await visitorPage.keyboard.type('twee');
    // The text stays Markdown: the textarea behind the editor holds what goes.
    await expect(commentText(panel)).toHaveValue('Dit is **vet**.\n\n- een\n- twee');

    // Tab leaves the editor for the panel and Shift+Tab comes back. Escape in
    // the editor does what it does in the panel, and the text is kept.
    await visitorPage.keyboard.press('Tab');
    await expect(panel.getByRole('button', { name: 'Schermafbeelding maken' })).toBeFocused();
    await visitorPage.keyboard.press('Shift+Tab');
    await expect(editor).toBeFocused();
    await visitorPage.keyboard.press('Escape');
    await expect(panel.getByRole('button', { name: 'Algemene feedback', exact: true })).toBeVisible();
    await panel.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    await expect(commentEditor(panel)).toBeFocused();
    await expect(commentText(panel)).toHaveValue('Dit is **vet**.\n\n- een\n- twee');
  });

  test('the composer links the privacy statement as a plain link, in the same tab', async () => {
    await visitorPage.goto(PREVIEW_URL);
    const panel = await openPanel(visitorPage);
    await panel.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    const privacy = panel.getByRole('link', { name: 'Privacyverklaring' });
    await expect(privacy).not.toHaveAttribute('target');
    await expect(privacy).toHaveAccessibleDescription('');
    const tabs = visitorContext.pages().length;
    await privacy.click();
    await visitorPage.waitForURL(PRIVACY_URL);
    await expect(visitorPage.getByRole('heading', { level: 1, name: 'Privacy' })).toBeVisible();
    expect(visitorContext.pages()).toHaveLength(tabs);
  });

  test('a point placed with the mouse goes along with a screenshot, a part of it covered', async () => {
    await visitorPage.goto(PREVIEW_URL);
    const target = (await visitorPage.locator('#doel').boundingBox())!;
    const panel = await openPanel(visitorPage);
    await panel.getByRole('button', { name: 'Wijs een punt of gebied aan' }).click();

    const overlay = widgetHost(visitorPage).getByRole('application');
    await expect(overlay).toBeVisible();
    await expect(feedbackButton(visitorPage)).toBeHidden();
    // Nothing to drag: the pointer gets the crosshair cursor, and a click
    // places the point where it lands.
    await expect(overlay.locator('.crosshair')).toBeHidden();
    await expect(overlay).toHaveCSS('cursor', 'crosshair');
    await visitorPage.mouse.click(target.x + target.width / 2, target.y + target.height / 2);
    await expect(overlay).toBeHidden();
    await expect(panel.getByText('Je feedback gaat over de plek die je aanwees.')).toBeVisible();

    // The screenshot is made as the composer opens, and shown before it can go.
    const thumbnail = panel.getByRole('img', {
      name: 'Schermafbeelding van wat je nu ziet, met de aangewezen plek gemarkeerd',
    });
    await expect(thumbnail).toBeVisible({ timeout: 15_000 });

    // Covering something: a frame dragged on the frozen screenshot.
    await panel.getByRole('button', { name: 'Iets afdekken' }).click();
    const frame = widgetHost(visitorPage).getByRole('application');
    await expect(frame).toHaveAccessibleName(/^Kies wat je wilt afdekken/);
    // Its way out comes first, then how it works with a pointer. The
    // keyboard line waits for a key: the button was pressed with the mouse.
    await expect(frame).toMatchAriaSnapshot(`
      - application "Kies wat je wilt afdekken":
        - /children: equal
        - button "Annuleren"
        - paragraph: Sleep een kader of klik op twee hoeken van wat je wilt afdekken.
    `);
    // The page under the frame is out of reach: Tab goes back and forth
    // between the frame and its way out, both ways.
    await expect(frame).toBeFocused();
    const stop = frame.getByRole('button', { name: 'Annuleren' });
    for (const [key, focused] of [
      ['Tab', stop],
      ['Tab', frame],
      ['Shift+Tab', stop],
      ['Shift+Tab', frame],
    ] as const) {
      await visitorPage.keyboard.press(key);
      await expect(focused).toBeFocused();
    }
    await visitorPage.mouse.move(600, 300);
    await visitorPage.mouse.down();
    await visitorPage.mouse.move(700, 360, { steps: 5 });
    await visitorPage.mouse.move(800, 420, { steps: 5 });
    await visitorPage.mouse.up();
    await expect(frame).toBeHidden();
    await expect(panel.getByRole('button', { name: 'Afdekking weghalen' })).toBeVisible();
    await expect(thumbnail).toBeVisible();

    await sendFeedback(panel, COMMENT_POINT, NAME_POINT);

    const item = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT_POINT);
    pointId = item.id;
    expect(item).toMatchObject({
      kind: 'point',
      hasScreenshot: true,
      author: { kind: 'name', name: NAME_POINT, email: null },
    });
    const detail = await feedbackDetail(adminPage, GROUP, SITE, pointId);
    expect(detail.anchor!.selectors).toEqual([{ type: 'CssSelector', value: '#doel' }]);
    expect(detail.anchor!.point!.x).toBeCloseTo(0.5, 1);
    expect(detail.anchor!.point!.y).toBeCloseTo(0.5, 1);
    expect(detail.viewport).toEqual({ width: 1280, height: 720, dpr: 1 });
    expect(detail.highlightUrl).toContain(`${PREVIEW_URL}#toon-feedback=`);

    const screenshot = await adminFetchBytes(adminPage, `${API}/sites/${GROUP}/${SITE}/feedback/${pointId}/screenshot`);
    expect(screenshot.status).toBe(200);
    expect(screenshot.headers['content-type']).toBe('image/jpeg');
    expect(screenshot.headers['cache-control']).toBe('private, no-store');
    expect([...screenshot.bytes.subarray(0, 3)]).toEqual([0xff, 0xd8, 0xff]);
  });

  test('a text selection is sent with its quote', async () => {
    await visitorPage.goto(PREVIEW_URL);
    const panel = await openPanel(visitorPage);
    // A selection made while the panel is open brings a button next to it.
    await visitorPage.locator('#alinea').click({ clickCount: 3 });
    const floating = widgetHost(visitorPage).getByRole('button', { name: 'Feedback op deze tekst' });
    await expect(floating).toBeVisible();
    await floating.click();

    await expect(panel.getByText('Je feedback gaat over deze tekst:')).toBeVisible();
    await expect(panel.getByText(PARAGRAPH, { exact: true })).toBeVisible();
    // The quote is enough: no screenshot unless the giver asks for one, which
    // then marks the selection.
    const picture = panel.getByRole('img', {
      name: 'Schermafbeelding van wat je nu ziet, met de geselecteerde tekst gemarkeerd',
    });
    await expect(picture).toHaveCount(0);
    await panel.getByRole('button', { name: 'Schermafbeelding maken' }).click();
    await expect(picture).toBeVisible({ timeout: 15_000 });
    await sendFeedback(panel, COMMENT_TEXT);

    const item = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT_TEXT);
    textId = item.id;
    expect(item).toMatchObject({ kind: 'text', quote: PARAGRAPH, hasScreenshot: true, author: { kind: 'anonymous' } });
    const detail = await feedbackDetail(adminPage, GROUP, SITE, item.id);
    expect(detail.anchor!.selectors[0]).toMatchObject({ type: 'TextQuoteSelector', exact: PARAGRAPH });
  });

  test('on a site that takes feedback with an account only, a selection brings no button beside it', async () => {
    const accountOnly = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/feedback-settings`, {
      ...ON_PREVIEWS,
      identity: 'account',
    });
    expect(accountOnly.status, accountOnly.text).toBe(200);

    // A visitor without an account is offered feedback with an account, the
    // privacy statement and hiding the button, and nothing to start a remark
    // they cannot send. The consent page after the sign-in says what signing
    // in keeps.
    await visitorPage.goto(PREVIEW_URL);
    const panel = await openPanel(visitorPage);
    const account = panel.getByRole('link', { name: 'Feedback geven met je account' });
    await expect(account).toBeVisible();
    await expect(account).toHaveAccessibleDescription('');
    // On the shielded preview the statement opens in this tab.
    const privacy = panel.getByRole('link', { name: 'Privacyverklaring' });
    await expect(privacy).toHaveAttribute('href', PRIVACY_URL);
    await expect(privacy).not.toHaveAttribute('target');
    await expect(panel.getByRole('button', { name: 'Knop verbergen' })).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Feedback op geselecteerde tekst' })).toHaveCount(0);
    const selected = await selectionSeen(visitorPage, PARAGRAPH);
    await visitorPage.locator('#alinea').click({ clickCount: 3 });
    await selected();
    await expect(widgetHost(visitorPage).getByRole('button', { name: 'Feedback op deze tekst' })).toBeHidden();

    const back = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/feedback-settings`, ON_PREVIEWS);
    expect(back.status, back.text).toBe(200);
  });

  test('pointing works with the keyboard, and Escape backs out one step at a time', async () => {
    await visitorPage.goto(PREVIEW_URL);
    const panel = await openPanel(visitorPage);
    await panel.getByRole('button', { name: 'Wijs een punt of gebied aan' }).click();
    const overlay = widgetHost(visitorPage).getByRole('application');
    await expect(overlay).toBeFocused();
    // The bar holds its way out first, then how it works with a pointer, the
    // way to an area without dragging, and how it works with the keyboard
    // once a key is pressed: the menu button was pressed with the mouse. Both
    // lines describe the overlay to a screen reader from the start.
    const pointerWay = 'Klik of tik op een plek. Sleep voor een gebied, of kies Gebied zonder slepen.';
    const keyboardWay =
      'De pijltjes zetten een kruis in het midden en verplaatsen het, met Shift sneller. Enter kiest de plek. Voor een gebied is er Gebied zonder slepen.';
    await expect(overlay).toHaveAccessibleDescription(`${pointerWay} ${keyboardWay}`);
    await expect(overlay).toMatchAriaSnapshot(`
      - application "Wijs een punt of gebied aan":
        - /children: equal
        - button "Stoppen"
        - paragraph: ${pointerWay}
        - button "Gebied zonder slepen"
    `);
    // The crosshair shows at the first arrow key, in the middle.
    const crosshair = overlay.locator('.crosshair');
    await expect(crosshair).toBeHidden();
    await visitorPage.keyboard.press('Shift+ArrowLeft');
    await expect(crosshair).toBeVisible();
    await expect(overlay).toMatchAriaSnapshot(`
      - application "Wijs een punt of gebied aan":
        - /children: equal
        - button "Stoppen"
        - paragraph: ${pointerWay}
        - button "Gebied zonder slepen"
        - paragraph: ${keyboardWay}
    `);
    // Tab goes round the overlay and its two buttons, both ways, the
    // keyboard line staying in the bar.
    const stop = overlay.getByRole('button', { name: 'Stoppen' });
    const area = overlay.getByRole('button', { name: 'Gebied zonder slepen' });
    for (const [key, focused] of [
      ['Tab', stop],
      ['Tab', area],
      ['Tab', overlay],
      ['Shift+Tab', area],
      ['Shift+Tab', stop],
      ['Shift+Tab', overlay],
    ] as const) {
      await visitorPage.keyboard.press(key);
      await expect(focused).toBeFocused();
    }
    await expect(widgetHost(visitorPage).getByText(keyboardWay)).toBeVisible();
    await visitorPage.keyboard.press('ArrowUp');
    // The status region says what lies under the crosshair.
    await expect(widgetHost(visitorPage).getByRole('status')).toContainText('Onder het kruis');
    await visitorPage.keyboard.press('Enter');
    await expect(panel.getByText('Je feedback gaat over de plek die je aanwees.')).toBeVisible();
    await expect(commentEditor(panel)).toBeFocused();

    await visitorPage.keyboard.press('Escape');
    await expect(panel.getByRole('button', { name: 'Wijs een punt of gebied aan' })).toBeFocused();
    await visitorPage.keyboard.press('Escape');
    await expect(panel).toBeHidden();
    await expect(feedbackButton(visitorPage)).toBeFocused();
  });

  test('an area dragged with the mouse goes along with a screenshot that marks it', async () => {
    await visitorPage.goto(PREVIEW_URL);
    const target = (await visitorPage.locator('#doel').boundingBox())!;
    const panel = await openPanel(visitorPage);
    await panel.getByRole('button', { name: 'Wijs een punt of gebied aan' }).click();
    const overlay = widgetHost(visitorPage).getByRole('application');
    await expect(overlay).toBeVisible();

    // A frame 20 px inside the box, drawn live while the pointer moves.
    const from = { x: target.x + 20, y: target.y + 20 };
    const to = { x: target.x + target.width - 20, y: target.y + target.height - 20 };
    const drawn = overlay.locator('.drawn');
    await visitorPage.mouse.move(from.x, from.y);
    await visitorPage.mouse.down();
    await expect(drawn).toBeHidden();
    await visitorPage.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 5 });
    await expect(drawn).toBeVisible();
    await visitorPage.mouse.move(to.x, to.y, { steps: 5 });
    const live = (await drawn.boundingBox())!;
    expect(live.width).toBeCloseTo(to.x - from.x, 0);
    expect(live.height).toBeCloseTo(to.y - from.y, 0);
    await visitorPage.mouse.up();
    await expect(overlay).toBeHidden();
    await expect(panel.getByText('Je feedback gaat over het gebied dat je aanwees.')).toBeVisible();
    await expect(
      panel.getByRole('img', { name: 'Schermafbeelding van wat je nu ziet, met het aangewezen gebied gemarkeerd' }),
    ).toBeVisible({ timeout: 15_000 });
    await sendFeedback(panel, COMMENT_AREA, NAME_POINT);

    const item = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT_AREA);
    expect(item).toMatchObject({ kind: 'area', hasScreenshot: true });
    const detail = await feedbackDetail(adminPage, GROUP, SITE, item.id);
    // The box under the middle of the frame holds it whole: the area is in
    // fractions of the box, and in px of the page.
    expect(detail.anchor!.selectors).toEqual([{ type: 'CssSelector', value: '#doel' }]);
    expect(detail.anchor!.point).toBeNull();
    const area = detail.anchor!.area!;
    expect(area.x).toBeCloseTo(20 / target.width, 2);
    expect(area.y).toBeCloseTo(20 / target.height, 2);
    expect(area.width).toBeCloseTo((target.width - 40) / target.width, 2);
    expect(area.height).toBeCloseTo((target.height - 40) / target.height, 2);
    expect(area.pageX).toBeCloseTo(from.x, 0);
    expect(area.pageY).toBeCloseTo(from.y, 0);
    expect(area.pageWidth).toBeCloseTo(to.x - from.x, 0);
    expect(area.pageHeight).toBeCloseTo(to.y - from.y, 0);
    expect(detail.highlightUrl).toContain(`${PREVIEW_URL}#toon-feedback=`);

    // The link to the place marks the area with a frame over it.
    const viewer = await visitorContext.newPage();
    await viewer.goto(detail.highlightUrl!);
    const mark = widgetHost(viewer).locator('.mark');
    await expect(mark).toHaveCount(1);
    const { marked, box } = await areaMark(viewer, '#doel');
    expect(marked.left - box.left).toBeCloseTo(20, 0);
    expect(marked.width).toBeCloseTo(box.width - 40, 0);
    await viewer.close();

    // Gone again, so the counts of the scenarios below stay as they are.
    const removed = await adminFetch(adminPage, 'DELETE', `${API}/sites/${GROUP}/${SITE}/feedback/${item.id}`);
    expect(removed.status).toBe(204);
  });

  test('an area without dragging, from two clicks on the page', async () => {
    await visitorPage.goto(PREVIEW_URL);
    const target = (await visitorPage.locator('#doel').boundingBox())!;
    const panel = await openPanel(visitorPage);
    await panel.getByRole('button', { name: 'Wijs een punt of gebied aan' }).click();
    const host = widgetHost(visitorPage);
    await host.getByRole('button', { name: 'Gebied zonder slepen' }).click();
    const overlay = host.getByRole('application', { name: 'Wijs een gebied aan' });
    await expect(overlay).toBeFocused();
    await expect(overlay).toMatchAriaSnapshot(`
      - application "Wijs een gebied aan":
        - /children: equal
        - button "Terug"
        - paragraph: Sleep een kader of klik op twee hoeken van het gebied waar je feedback over gaat.
    `);
    // The live page lies under it, not a screenshot.
    await expect(overlay.locator('.frozen')).toHaveCount(0);
    await visitorPage.mouse.click(target.x + 10, target.y + 10);
    await expect(overlay.locator('.corner')).toBeVisible();
    await visitorPage.mouse.click(target.x + target.width - 10, target.y + target.height - 10);
    await expect(overlay).toBeHidden();
    await expect(panel.getByText('Je feedback gaat over het gebied dat je aanwees.')).toBeVisible();
    await expect(commentEditor(panel)).toBeFocused();
    // Not sent: back to the menu, and closed. An Escape in the editor reaches
    // the panel by a message, so the next one waits for the menu.
    await visitorPage.keyboard.press('Escape');
    await expect(panel.getByRole('button', { name: 'Algemene feedback', exact: true })).toBeVisible();
    await visitorPage.keyboard.press('Escape');
    await expect(panel).toBeHidden();
  });

  test('an area without dragging, from the keyboard, and Escape steps back to pointing', async () => {
    await visitorPage.goto(PREVIEW_URL);
    const panel = await openPanel(visitorPage);
    const menuButton = panel.getByRole('button', { name: 'Wijs een punt of gebied aan' });
    await menuButton.focus();
    await visitorPage.keyboard.press('Enter');
    const host = widgetHost(visitorPage);
    const pointingOverlay = host.getByRole('application', { name: 'Wijs een punt of gebied aan' });
    await expect(pointingOverlay).toBeFocused();
    const areaButton = host.getByRole('button', { name: 'Gebied zonder slepen' });
    await visitorPage.keyboard.press('Tab');
    await visitorPage.keyboard.press('Tab');
    await expect(areaButton).toBeFocused();
    await visitorPage.keyboard.press('Enter');
    const overlay = host.getByRole('application', { name: 'Wijs een gebied aan' });
    await expect(overlay).toBeFocused();
    // Opened from the keyboard: the keyboard line shows from the start.
    await expect(
      host.getByText('Pijltjes verplaatsen het kader, met Shift veranderen ze de maat. Enter bevestigt.'),
    ).toBeVisible();

    // Escape steps back one step: to pointing, onto the button that opened it.
    await visitorPage.keyboard.press('Escape');
    await expect(pointingOverlay).toBeVisible();
    await expect(areaButton).toBeFocused();
    await expect(host.getByRole('status')).toContainText('Terug bij aanwijzen.');

    await visitorPage.keyboard.press('Enter');
    await expect(overlay).toBeFocused();
    // The frame starts in the middle of the window, half its size.
    await visitorPage.keyboard.press('ArrowDown');
    await expect(host.getByRole('status')).toContainText('Kader van 640 bij 360 pixels.');
    await visitorPage.keyboard.press('Shift+ArrowLeft');
    await visitorPage.keyboard.press('Enter');
    await expect(overlay).toBeHidden();
    await expect(panel.getByText('Je feedback gaat over het gebied dat je aanwees.')).toBeVisible();
    await expect(commentEditor(panel)).toBeFocused();

    // Not sent: back to the menu, onto the way it came in, and closed.
    await visitorPage.keyboard.press('Escape');
    await expect(menuButton).toBeFocused();
    await visitorPage.keyboard.press('Escape');
    await expect(panel).toBeHidden();
  });

  test('the link to a place shows it on the page, and keeps it there when the window narrows', async () => {
    // A fresh load, as from the link in the admin SPA, which opens a new tab.
    const point = await feedbackDetail(adminPage, GROUP, SITE, pointId);
    const page = await visitorContext.newPage();
    await page.goto(point.highlightUrl!);
    const pin = widgetHost(page).getByRole('img', { name: 'Hier gaat de feedback over' });
    await expect(pin).toBeVisible();
    // The pin is only seen. The status region names the place, in the words
    // of the element the point is on.
    await expect(widgetHost(page).getByRole('status')).toContainText('Gemarkeerd: Wijs dit vak aan');
    const target = page.locator('#doel');
    const spot = point.anchor!.point!;
    await expect.poll(() => pinOffset(pin, target, spot)).toBeLessThan(1.5);
    // A ring around the point, not a disc on it: what it marks stays in
    // sight, and a click there reaches it.
    const reached = await target.evaluate((element, at) => {
      const box = element.getBoundingClientRect();
      const hit = document.elementFromPoint(box.left + at.x * box.width, box.top + at.y * box.height);
      return hit !== null && element.contains(hit);
    }, spot);
    expect(reached, 'the element under the ring takes the press').toBe(true);

    // Narrower, the paragraph above the box takes more lines: the box moves
    // down, and the pin goes with it.
    const wide = (await target.boundingBox())!;
    await page.setViewportSize(NARROW);
    await expect.poll(async () => (await target.boundingBox())!.y).toBeGreaterThan(wide.y + 10);
    await expect.poll(() => pinOffset(pin, target, spot)).toBeLessThan(1.5);
    expect(await sidewaysOverflow(page)).toBe(0);
    await page.close();

    // A quote is marked line by line. Its first line is wider than the
    // narrow window, so marks left as they were would push the page sideways.
    const text = await feedbackDetail(adminPage, GROUP, SITE, textId);
    const quoted = await visitorContext.newPage();
    await quoted.goto(text.highlightUrl!);
    await expect(widgetHost(quoted).getByRole('img', { name: 'Hier gaat de feedback over' })).toBeVisible();
    // A quote is named by the first words of the passage it matched on the page.
    await expect(widgetHost(quoted).getByRole('status')).toContainText(
      'Gemarkeerd: De begroting voor dit jaar bedraagt 2,4 miljoen euro voor het onderhoud...',
    );
    await expect
      .poll(async () => {
        const marks = await quoteMarks(quoted, '#alinea');
        return { marked: marks.lines > 0, out: marks.out };
      })
      .toEqual({ marked: true, out: 0 });
    const wideMarks = await quoteMarks(quoted, '#alinea');
    expect(wideMarks.right).toBeGreaterThan(NARROW.width);
    await quoted.setViewportSize(NARROW);
    // More lines now, each marked where it is.
    await expect
      .poll(async () => {
        const marks = await quoteMarks(quoted, '#alinea');
        return { more: marks.lines > wideMarks.lines, out: marks.out };
      })
      .toEqual({ more: true, out: 0 });
    expect(await sidewaysOverflow(quoted)).toBe(0);
    await quoted.close();
  });

  test('a link to a place in a part of the page that scrolls on its own brings it into view, and the pin follows that part', async () => {
    // The widget reads any such link: no item is needed for it.
    const page = await visitorContext.newPage();
    const place = { k: 'point', s: '#p30', x: 0.5, y: 0.5, px: null, py: null, q: null };
    const fragment = `#toon-feedback=${Buffer.from(JSON.stringify(place)).toString('base64url')}`;
    await page.goto(`${PREVIEW_URL}${PANE_PAGE}${fragment}`);
    const pin = widgetHost(page).getByRole('img', { name: 'Hier gaat de feedback over' });
    const target = page.locator('#p30');
    // The window cannot scroll: the pane brings the paragraph into view.
    await expect(target).toBeInViewport();
    await expect(widgetHost(page).getByRole('status')).toContainText(
      'Gemarkeerd: Alinea 30: tekst over onderdeel 30 van de begroting.',
    );
    await expect.poll(() => pinOffset(pin, target, place)).toBeLessThan(1.5);

    // The pane takes the place along as it scrolls, and the pin follows.
    // Scrolled back to its top, the pane holds the place out of sight, and
    // the pin goes as well.
    const pane = page.locator('#paneel');
    await pane.evaluate((element) => element.scrollBy(0, -150));
    await expect.poll(() => pinOffset(pin, target, place)).toBeLessThan(1.5);
    await pane.evaluate((element) => element.scrollTo(0, 0));
    await expect(target).not.toBeInViewport();
    await expect(pin).toBeHidden();
    await page.close();
  });

  test('a link to a place that the boxes around it do not clip marks it and says so, and one a box clips away stays hidden', async () => {
    // Root and body are one window high and clip sideways only, so what lies
    // below the first window shows. Overflow does nothing on a link, and an
    // svg inside a chart has no client box in any browser. A quote is marked
    // on its line as well.
    const quote = { k: 'text', s: null, x: null, y: null, px: null, py: null, q: 'Het doel van deze alinea' };
    for (const [place, target, said, lines] of [
      [pointOn('#ver'), '#ver', 'Gemarkeerd: Het doel van deze alinea', 0],
      [quote, '#ver', 'Gemarkeerd: Het doel van deze alinea', 1],
      [pointOn('#staaf'), '#staaf', 'De plek waar de feedback over gaat, is gemarkeerd.', 0],
      [pointOn('#sterk'), '#sterk', 'Gemarkeerd: volledige rapport', 0],
    ] as const) {
      const page = await visitorContext.newPage();
      await page.goto(`${PREVIEW_URL}${CLIP_PAGE}${linkTo(place)}`);
      await expect(widgetHost(page).getByRole('status')).toContainText(said);
      await expect(widgetHost(page).getByRole('img', { name: 'Hier gaat de feedback over' })).toBeInViewport();
      await expect(widgetHost(page).locator('.mark')).toHaveCount(lines);
      await expect(page.locator(target)).toBeInViewport();
      await page.close();
    }

    // A line below a box of 40 px that hides its overflow, and a word right
    // of a line that clips sideways. Once the window has brought that box
    // into view, and the widget has had two frames since, neither is marked
    // or said. Without motion, the window gets there at once.
    for (const [selector, box] of [
      ['#verborgen', '.dicht'],
      ['#weg', '.smal'],
    ] as const) {
      const page = await visitorContext.newPage();
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.goto(`${PREVIEW_URL}${CLIP_PAGE}${linkTo(pointOn(selector))}`);
      await expect(page.locator(box)).toBeInViewport();
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const ring = widgetHost(page).getByRole('img', { name: 'Hier gaat de feedback over', includeHidden: true });
      await expect(ring).toBeAttached();
      await expect(ring).toBeHidden();
      await expect(widgetHost(page).getByRole('status')).toHaveText('');
      await page.close();
    }
  });

  test('a link to a place right of the middle leaves the window where it is sideways, on a page whose body hides what overflows sideways', async () => {
    // A menu waits right of the window. Moved sideways, the window could not
    // be scrolled back by the visitor, and the place is in view already.
    // Without motion, the window is where it stays once the place is said.
    const page = await visitorContext.newPage();
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(`${PREVIEW_URL}${OFFCANVAS_PAGE}${linkTo(pointOn('#rechts'))}`);
    await expect(widgetHost(page).getByRole('status')).toContainText('Gemarkeerd: Lees meer');
    await expect(page.locator('#rechts')).toBeInViewport();
    expect(await sidewaysOverflow(page)).toBeGreaterThan(0);
    expect(await page.evaluate(() => window.scrollX)).toBe(0);
    await page.close();
  });

  test('the place from a link lies over the Feedback button, and under the open panel', async () => {
    // The footer link of the long page lies under the button, which does not
    // move off a place. Without motion, the window is where it stays once
    // the place is said. The ring takes no press: a press there reaches the
    // button.
    const page = await visitorContext.newPage();
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(`${PREVIEW_URL}${LONG_PAGE}${linkTo(pointOn('footer a'))}`);
    await expect(widgetHost(page).getByRole('status')).toContainText('Gemarkeerd: Contact');
    expect(await drawnOnTop(page, '.pin-highlight', '.trigger')).toEqual({
      shared: true,
      press: 'button',
      top: 'ring',
    });
    await page.close();

    // A second link while the panel is open draws its place under the panel.
    const listed = await visitorContext.newPage();
    await listed.emulateMedia({ reducedMotion: 'reduce' });
    await listed.goto(`${PREVIEW_URL}${LIST_PAGE}`);
    const panel = edges((await (await openPanel(listed)).boundingBox())!);
    // Whichever link of the list the panel covers in the middle: its height
    // follows the menu, not the list.
    const covered = await listed.locator('aside a').evaluateAll(
      (links, box) =>
        links
          .map((link) => ({ text: link.textContent!, rect: link.getBoundingClientRect() }))
          .find(({ rect }) => {
            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;
            return x > box.left + 24 && x < box.right - 24 && y > box.top + 24 && y < box.bottom - 24;
          })?.text ?? null,
      panel,
    );
    expect(covered, 'a link of the list lies under the open panel').not.toBeNull();
    const quote = { k: 'text', s: null, x: null, y: null, px: null, py: null, q: covered! };
    await listed.evaluate((hash) => {
      location.hash = hash;
    }, linkTo(quote));
    await expect(widgetHost(listed).getByRole('status')).toContainText(`Gemarkeerd: ${covered}`);
    expect(await drawnOnTop(listed, '.mark', '.panel')).toEqual({ shared: true, press: 'panel', top: 'panel' });
    await listed.close();
  });

  test('a link to a place that is gone says so beside the button, and opens nothing', async () => {
    // The team corrected the amount the text item quotes, on the preview.
    const dist = distFor('Feedback preview', CORRECTED);
    const corrected = await uploadViaSession(adminPage, GROUP, SITE, dist, PREVIEW_REF);
    expect(corrected.status).toBe(201);
    const text = await feedbackDetail(adminPage, GROUP, SITE, textId);
    const page = await visitorContext.newPage();
    await page.goto(text.highlightUrl!);
    await expect(page.locator('#alinea')).toHaveText(CORRECTED);

    // A note beside the button with a way to close it, and the status region
    // says the same. Anyone can make such a link, so no panel opens.
    const host = widgetHost(page);
    const close = host.getByRole('button', { name: 'Sluiten' });
    await expect(close).toBeVisible();
    await expect(close).toHaveAccessibleDescription(PLACE_GONE);
    await expect(host.getByRole('status')).toContainText(PLACE_GONE);
    await expect(feedbackPanel(page)).toBeHidden();
    await expect(host.getByRole('img', { name: 'Hier gaat de feedback over' })).toHaveCount(0);

    // The page has no links: Tab reaches the Feedback button, then the note.
    // Closed from the keyboard, the note hands focus to the button.
    await page.keyboard.press('Tab');
    await expect(feedbackButton(page)).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(close).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(close).toBeHidden();
    await expect(feedbackButton(page)).toBeFocused();
    await page.close();

    const restored = await uploadViaSession(adminPage, GROUP, SITE, distFor('Feedback preview'), PREVIEW_REF);
    expect(restored.status).toBe(201);
  });

  test('the note about a place that is gone makes way for keyboard focus, and Escape on the page closes it', async () => {
    const page = await visitorContext.newPage();
    await page.goto(`${PREVIEW_URL}${LIST_PAGE}${NOWHERE}`);
    const close = widgetHost(page).getByRole('button', { name: 'Sluiten' });
    await expect(close).toHaveAccessibleDescription(PLACE_GONE);
    // Not focused, the end of the list lies under the note.
    const links = page.getByRole('complementary').getByRole('link');
    const count = await links.count();
    let under = -1;
    for (let index = 0; under < 0 && index < count; index += 1) {
      if (await widgetCovers(links.nth(index))) under = index;
    }
    expect(under).toBeGreaterThan(0);

    // Keyboard focus on a link clear of the note leaves it be. Once a link
    // under it has focus, the note goes: it has been said already. No link
    // with keyboard focus is hidden on the way down.
    for (let index = 0; index < count; index += 1) {
      await page.keyboard.press('Tab');
      const link = links.nth(index);
      await expect(link).toBeFocused();
      await expect.poll(() => widgetCovers(link)).toBe(false);
      if (index === 0) await expect(close).toBeVisible();
      if (index === under) await expect(close).toBeHidden();
    }
    await page.close();

    // Escape pressed on the page closes the note, and leaves both the key and
    // the focus to the page.
    const again = await visitorContext.newPage();
    await again.goto(`${PREVIEW_URL}${LIST_PAGE}${NOWHERE}`);
    const note = widgetHost(again).getByRole('button', { name: 'Sluiten' });
    const first = again.getByRole('complementary').getByRole('link').first();
    await expect(note).toBeVisible();
    await again.keyboard.press('Tab');
    await expect(first).toBeFocused();
    await expect(note).toBeVisible();
    await again.keyboard.press('Escape');
    await expect(note).toBeHidden();
    await expect(first).toBeFocused();
    await expect(again.locator('body')).toHaveAttribute('data-escape', 'received');
    await again.close();
  });

  test('in a wide window the open panel moves clear of keyboard focus on the page, stays put for the mouse, and Escape there closes it', async () => {
    const page = await visitorContext.newPage();
    await page.goto(`${PREVIEW_URL}${LIST_PAGE}`);
    const panel = await openPanel(page);
    await panel.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    const comment = commentEditor(panel);
    await comment.fill(COMMENT_KEPT);
    // Measured where it ends up: the panel slides in.
    await panel.evaluate(async (element) => {
      await Promise.all(element.getAnimations().map((animation) => animation.finished));
    });
    const home = (await panel.boundingBox())!;

    // The panel of a wide window is not modal: Tab leaves it for the page.
    // Backwards, focus goes through the button to the end of the list and up
    // it, until a link that the panel in its corner lies over has it. The
    // panel then goes to the bottom left corner, clear of it.
    const focusUnderPanel = async (): Promise<Locator> => {
      let under: string | null = null;
      for (let press = 0; under === null && press < 20; press += 1) {
        await page.keyboard.press('Shift+Tab');
        under = await page.evaluate((home) => {
          const focused = document.activeElement;
          if (focused === null || focused.closest('aside') === null) return null;
          const box = focused.getBoundingClientRect();
          const x = box.left + box.width / 2;
          const y = box.top + box.height / 2;
          const inside = x > home.x && x < home.x + home.width && y > home.y && y < home.y + home.height;
          return inside ? focused.textContent : null;
        }, home);
      }
      expect(under, 'a link under the panel in its corner got keyboard focus').not.toBeNull();
      const link = page.getByRole('link', { name: under!, exact: true });
      await expect(link).toBeFocused();
      await expect.poll(() => widgetCovers(link)).toBe(false);
      expect((await panel.boundingBox())!.x).toBe(16);
      return link;
    };
    const link = await focusUnderPanel();

    // Escape pressed there closes the panel and keeps the text, as "Close"
    // does. The key and the focus stay with the page.
    await page.keyboard.press('Escape');
    await expect(panel).toBeHidden();
    await expect(link).toBeFocused();
    await expect(page.locator('body')).toHaveAttribute('data-escape', 'received');
    await openPanel(page);
    // The way to an account leaves the page, and with it the text not sent
    // yet: the menu says so now that there is some, and so does the privacy
    // statement, which opens in this tab on the shielded preview.
    const lost = 'Je gaat naar een pagina van Toon. Tekst die je nog niet hebt verstuurd, gaat dan verloren.';
    await expect(panel.getByText(lost)).toBeVisible();
    await expect(panel.getByRole('link', { name: 'Met je account versturen' })).toHaveAccessibleDescription(lost);
    await expect(panel.getByRole('link', { name: 'Privacyverklaring' })).toHaveAccessibleDescription(lost);
    await panel.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    await expect(comment).toHaveText(COMMENT_KEPT);

    // Once keyboard focus on the page has moved it, the panel stays under the
    // pointer: a press in its comment field leaves it where it is, and so
    // does a click on its link, which then follows it. On the shielded
    // preview the privacy statement opens in this tab.
    await focusUnderPanel();
    await comment.click();
    await expect(comment).toBeFocused();
    expect((await panel.boundingBox())!.x).toBe(16);
    await focusUnderPanel();
    await panel.getByRole('link', { name: 'Privacyverklaring' }).click();
    await expect(page).toHaveURL(PRIVACY_URL);
    await page.close();
  });

  test('at 320 px the panel is a sheet: the button goes while it shows, and Tab stays inside', async () => {
    const page = await visitorContext.newPage();
    await page.setViewportSize(NARROW);
    await page.goto(PREVIEW_URL);
    const trigger = feedbackButton(page);
    const panel = feedbackPanel(page);
    // The page has no links, so the first Tab reaches the button.
    await page.keyboard.press('Tab');
    await expect(trigger).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(panel).toBeVisible();
    // The sheet would cover the button: it goes, and focus goes to the sheet.
    await expect(trigger).toBeHidden();
    await expect(panel.getByRole('heading', { name: 'Feedback op deze pagina' })).toBeFocused();
    // The X beside the heading comes first, the privacy statement last.
    const close = panel.getByRole('button', { name: 'Sluiten' });
    const privacy = panel.getByRole('link', { name: 'Privacyverklaring' });
    await page.keyboard.press('Shift+Tab');
    await expect(privacy).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(close).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(panel.getByRole('button', { name: 'Wijs een punt of gebied aan' })).toBeFocused();

    await panel.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    const comment = commentEditor(panel);
    await expect(comment).toBeFocused();
    await expect(trigger).toBeHidden();
    await page.keyboard.press('Shift+Tab');
    await expect(close).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(privacy).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(close).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(comment).toBeFocused();

    // An Escape in the editor reaches the panel by a message, so the next
    // one waits for the menu.
    await page.keyboard.press('Escape');
    await expect(panel.getByRole('button', { name: 'Algemene feedback', exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(panel).toBeHidden();
    await expect(trigger).toBeVisible();
    await expect(trigger).toBeFocused();
    await page.close();
  });

  test('at 320 px the button moves clear of a link with keyboard focus at the bottom right', async () => {
    const page = await visitorContext.newPage();
    await page.setViewportSize(NARROW);
    await page.goto(`${PREVIEW_URL}${LONG_PAGE}`);
    const contact = page.getByRole('link', { name: 'Contact' });
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    // Not focused, the link lies under the button in its corner.
    await expect.poll(() => widgetCovers(contact)).toBe(true);

    await page.keyboard.press('Tab');
    await expect(contact).toBeFocused();
    await expect.poll(() => widgetCovers(contact)).toBe(false);
    await expect(feedbackButton(page)).toBeVisible();

    // Focus goes on to the button, which goes back to its corner.
    await page.keyboard.press('Tab');
    await expect(feedbackButton(page)).toBeFocused();
    await expect.poll(() => widgetCovers(contact)).toBe(true);
    await page.close();
  });

  test('at 320 px the button moves clear of keyboard focus inside a web component of the page', async () => {
    const page = await visitorContext.newPage();
    await page.setViewportSize(NARROW);
    // The document hears focus inside the component as the component itself,
    // which never shows as keyboard focus: the link inside it does.
    await page.goto(`${PREVIEW_URL}${COMPONENT_PAGE}`);
    const contact = page.getByRole('link', { name: 'Contact', exact: true });
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await expect.poll(() => widgetCovers(contact)).toBe(true);
    await page.keyboard.press('Tab');
    await expect(contact).toBeFocused();
    await expect.poll(() => widgetCovers(contact)).toBe(false);
    expect(await triggerInset(page)).toMatchObject({ left: 16, bottom: 16 });

    // Three links in one component across the footer. Tab from one to the
    // next stays inside the component and never reaches the document, and
    // each still moves the button.
    await page.goto(`${PREVIEW_URL}${COMPONENTS_PAGE}`);
    const english = page.getByRole('link', { name: 'English', exact: true });
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await expect.poll(() => widgetCovers(english)).toBe(true);
    for (const name of ['Privacy', 'Contact', 'English']) {
      await page.keyboard.press('Tab');
      const link = page.getByRole('link', { name, exact: true });
      await expect(link).toBeFocused();
      await expect.poll(() => widgetCovers(link)).toBe(false);
    }
    // The button keeps clear of the link, not of the component around it,
    // which spans the whole footer.
    expect(await triggerInset(page)).toMatchObject({ left: 16, bottom: 16 });
    await page.close();
  });

  test('focus from the mouse, or on a whole main below a sticky header, leaves the button in its corner', async () => {
    const page = await visitorContext.newPage();
    await page.goto(`${PREVIEW_URL}${HEADER_PAGE}`);
    const main = page.getByRole('main');
    const english = page.getByRole('link', { name: 'English' });
    await expect(feedbackButton(page)).toBeVisible();
    expect(await triggerInset(page)).toMatchObject(CORNER);

    // A click into the text gives focus to main, the skip link's target. The
    // button stays clear of the navigation above, and the site's link takes
    // the click meant for it.
    await page.locator('#inleiding').click();
    await expect(main).toBeFocused();
    expect(await main.evaluate((element) => element.matches(':focus-visible'))).toBe(false);
    expect(await widgetCovers(english)).toBe(false);
    expect(await triggerInset(page)).toMatchObject(CORNER);
    await english.click();
    await expect(page.locator('h1')).toHaveText('English');

    // Through the skip link, main has keyboard focus right below the sticky
    // header. The button cannot hide it, so it stays in its corner as well.
    await page.goto(`${PREVIEW_URL}${HEADER_PAGE}`);
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'Naar de inhoud' })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(main).toBeFocused();
    expect(await main.evaluate((element) => element.matches(':focus-visible'))).toBe(true);
    expect(await widgetCovers(english)).toBe(false);
    expect(await triggerInset(page)).toMatchObject(CORNER);

    // The page's own button at the bottom right lies under the Feedback
    // button. Pressed with the mouse where it shows, it leaves the Feedback
    // button where it is. Reached with the keyboard, it sends the Feedback
    // button to the other corner.
    const up = page.getByRole('button', { name: 'Naar boven' });
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await expect.poll(() => widgetCovers(up)).toBe(true);
    const box = (await up.boundingBox())!;
    await page.mouse.click(box.x + 8, box.y + box.height / 2);
    await expect(up).toBeFocused();
    expect(await triggerInset(page)).toMatchObject(CORNER);
    expect(await widgetCovers(up)).toBe(true);
    await page.keyboard.press('Shift+Tab');
    await page.keyboard.press('Tab');
    await expect(up).toBeFocused();
    await expect.poll(() => widgetCovers(up)).toBe(false);
    expect(await triggerInset(page)).toMatchObject({ left: 16, bottom: 16 });
    await page.close();
  });

  for (const size of OVERLAY_WINDOWS) {
    test(`at ${size.width}x${size.height} each overlay shows its way out and keeps its bar off the work`, async () => {
      const page = await visitorContext.newPage();
      await page.setViewportSize(size);
      await page.goto(PREVIEW_URL);
      const host = widgetHost(page);
      const panel = await openPanel(page);
      await panel.getByRole('button', { name: 'Wijs een punt of gebied aan' }).click();
      const overlay = host.getByRole('application', { name: 'Wijs een punt of gebied aan' });
      await expect(overlay).toBeFocused();
      // No crosshair until the first arrow key.
      expect(await pointing(page)).toMatchObject({ ...WAY_OUT, crosshairY: null, crosshairClear: null });

      // Enough presses to reach an edge from anywhere: an arrow moves the
      // frame 10 px, and with Shift the crosshair 50. At either edge the
      // crosshair is clear of the bar, which went to the other one.
      const presses = Math.ceil(size.height / 10);
      for (let press = 0; press < presses; press += 1) await page.keyboard.press('Shift+ArrowUp');
      expect(await pointing(page)).toMatchObject({ ...WAY_OUT, barAt: 'bottom', crosshairY: 0, crosshairClear: true });
      for (let press = 0; press < presses; press += 1) await page.keyboard.press('Shift+ArrowDown');
      expect(await pointing(page)).toMatchObject({
        ...WAY_OUT,
        barAt: 'top',
        crosshairY: size.height,
        crosshairClear: true,
      });

      // The way to an area without dragging has its own way out in sight,
      // and Escape comes back from it.
      await host.getByRole('button', { name: 'Gebied zonder slepen' }).click();
      await expect(host.getByRole('application', { name: 'Wijs een gebied aan' })).toBeFocused();
      const live = await overlayLayout(page);
      expect(
        { stopInWindow: inside(live.stop, live.window), stopOnTop: live.stopOnTop },
        JSON.stringify(live),
      ).toEqual(WAY_OUT);
      await page.keyboard.press('Escape');
      await expect(host.getByRole('button', { name: 'Gebied zonder slepen' })).toBeFocused();

      // On touch a press on the bar moves it to the other edge. The rest of
      // the window takes a press, which places the point.
      await host.getByText('Klik of tik op een plek. Sleep voor een gebied, of kies Gebied zonder slepen.').click();
      const { layout, ...moved } = await pointing(page);
      expect(moved, JSON.stringify(layout)).toMatchObject({ ...WAY_OUT, barAt: 'bottom' });
      expect(layout.bar.top).toBeGreaterThanOrEqual(44);
      await page.mouse.click(size.width / 2, layout.bar.top / 2);
      await expect(overlay).toBeHidden();
      await expect(panel.getByText('Je feedback gaat over de plek die je aanwees.')).toBeVisible();
      await expect(
        panel.getByRole('img', { name: 'Schermafbeelding van wat je nu ziet, met de aangewezen plek gemarkeerd' }),
      ).toBeVisible({ timeout: 15_000 });

      // The frame overlays fit the frozen screenshot below their bar. The
      // frame starts on it, and stays clear of the bar at the top edge too,
      // with the bar grown by the keyboard line.
      const clear = { ...WAY_OUT, pictureBelowBar: true, frameOnPicture: true, frameUnderBar: 0 };
      for (const [opener, name] of [
        ['Iets afdekken', 'Kies wat je wilt afdekken'],
        ['Ander deel kiezen', 'Kies het deel van de schermafbeelding dat je wilt versturen'],
      ] as const) {
        await panel.getByRole('button', { name: opener }).click();
        const frame = host.getByRole('application', { name });
        await expect(frame).toBeFocused();
        expect(await framing(page)).toMatchObject(clear);
        for (let press = 0; press < presses; press += 1) await page.keyboard.press('ArrowUp');
        await expect(
          host.getByText('Pijltjes verplaatsen het kader, met Shift veranderen ze de maat. Enter bevestigt.'),
        ).toBeVisible();
        expect(await framing(page)).toMatchObject({ ...clear, frameAtTop: true });
        await page.keyboard.press('Escape');
        await expect(frame).toBeHidden();
      }
      await page.close();
    });
  }

  test('at 320 px the link that brings the button back wraps inside the panel', async () => {
    const page = await visitorContext.newPage();
    await page.setViewportSize(NARROW);
    await page.goto(`${PREVIEW_URL}${LONG_PAGE}`);
    const panel = await openPanel(page);
    await panel.getByRole('button', { name: 'Knop verbergen' }).click();
    await panel.getByRole('button', { name: 'Tot je de browser sluit' }).click();
    const back = `${CONTENT_URL}/-/feedback/show?returnTo=${encodeURIComponent(`${PREVIEW_PATH}${LONG_PAGE}`)}`;
    await expect(panel.getByRole('link', { name: back })).toBeVisible();
    // The encoded path has no place to break. The panel scrolls on its own,
    // which sidewaysOverflow, measuring the page, cannot see.
    expect(await panel.evaluate((element) => element.scrollWidth - element.clientWidth)).toBe(0);
    await page.close();
  });

  test('a visitor with an account gives feedback after the consent page', async () => {
    await giverPage.goto(PREVIEW_URL);
    const panel = await openPanel(giverPage);
    await expect(panel.getByRole('button', { name: 'Mijn feedback' })).toHaveCount(0);
    await panel.getByRole('link', { name: 'Met je account versturen' }).click();

    // First the sign-in, then Toon's own consent page, which names exactly
    // what the site team will see.
    await expect(onMockLoginPage(giverPage)).toBeVisible();
    await fillMockLogin(giverPage, GIVER_SUB, { email: GIVER_EMAIL, name: GIVER_NAME });
    await giverPage.waitForURL((url) => url.pathname === '/-/feedback/account', { timeout: 15_000 });
    await expect(giverPage.locator('html')).toHaveAttribute('lang', 'nl');
    await expect(giverPage.locator('h1')).toHaveText('Feedback geven met je account');
    await expect(giverPage.locator('dd')).toHaveText([GIVER_NAME, GIVER_EMAIL]);
    await expect(giverPage.getByText('Het e-mailadres zien alleen redacteuren en beheerders van de site.')).toBeVisible();
    // It says what the consent covers and for how long, and what signing in
    // keeps, also when the sign-in service ends the session early, and what
    // consent changes for a visitor who came through a secret link.
    await expect(
      giverPage.getByText(
        'Je toestemming geldt tot je uitlogt of tot je sessie afloopt, uiterlijk na 12 uur. ' +
          'Wat je in die tijd stuurt, houdt die gegevens zolang de feedback bestaat.',
        { exact: true },
      ),
    ).toBeVisible();
    await expect(
      giverPage.getByText(
        new RegExp(
          'Beëindigt de inlogdienst je sessie eerder, ' +
            'dan blijft je IP-adres drie jaar in het toegangslogboek\\. ' +
            'Kwam je via een geheime link op deze site\\? Dan staat na je toestemming, tot je sessie eindigt, ' +
            'elke pagina die je op de site bekijkt 180 dagen in het toegangslogboek, met je account\\.$',
        ),
      ),
    ).toBeVisible();

    // No single press consents, and the form takes no press in its first
    // second: a site can send the tab here between the two presses of a
    // double-click. The presses go by coordinates, because a locator click
    // would wait for the form to show. That needs the form in the window,
    // which at 720 px high it is not: the page is longer.
    await giverPage.setViewportSize({ width: 1280, height: 1024 });
    const agree = giverPage.getByRole('checkbox', {
      name: 'Het siteteam mag bij mijn feedback zien wat hierboven staat.',
    });
    const yes = giverPage.getByRole('button', { name: 'Ja, met mijn account' });
    const label = giverPage.getByText('Het siteteam mag bij mijn feedback zien wat hierboven staat.');
    await expect(yes).toBeInViewport({ ratio: 1 });
    await expect(label).toBeInViewport({ ratio: 1 });
    const onLabel = await middleOf(label);
    const onYes = await middleOf(yes);

    // Saying yes sets no cookie: the way back runs through the login, whose
    // shortcut sets the site cookie from the anchor the browser itself sends.
    const hops: Response[] = [];
    const hop = (response: Response) => {
      if (response.request().isNavigationRequest() && response.frame() === giverPage.mainFrame()) {
        hops.push(response);
      }
    };
    giverPage.on('response', hop);
    // A tick on the label and a press on the button 300 ms after the page
    // loads land on nothing.
    const reloaded = Date.now();
    await giverPage.reload();
    await giverPage.waitForTimeout(300);
    await giverPage.mouse.click(onLabel.x, onLabel.y);
    await giverPage.mouse.click(onYes.x, onYes.y);
    expect(Date.now() - reloaded, 'both presses came within the first second').toBeLessThan(1000);
    await expect(agree, 'the presses in the first second ticked nothing and sent nothing').not.toBeChecked();
    await expect(yes).toBeVisible();
    // Once the form shows, the button alone sends nothing: the browser asks
    // for the tick instead.
    await giverPage.mouse.click(onYes.x, onYes.y);
    await expect(agree, 'the button alone sent nothing, and the browser asks for the tick').toBeFocused();
    await expect(agree).not.toBeChecked();
    // The same two presses as before now consent.
    await giverPage.mouse.click(onLabel.x, onLabel.y);
    await expect(agree).toBeChecked();
    await giverPage.mouse.click(onYes.x, onYes.y);
    await giverPage.waitForURL(PREVIEW_URL);
    await giverPage.setViewportSize({ width: 1280, height: 720 });
    giverPage.off('response', hop);
    const steps = hops.map((response) => {
      const { pathname } = new URL(response.url());
      return `${response.request().method()} ${pathname} ${response.status()}`;
    });
    expect(steps).toEqual([
      'GET /-/feedback/account 200',
      'POST /-/feedback/account 303',
      'GET /-/login 303',
      `GET ${PREVIEW_PATH} 200`,
    ]);
    const [, consent, login] = hops;
    expect(consent!.request().postDataJSON()).toEqual({ nonce: expect.any(String), agree: '1' });
    expect(await consent!.headerValue('set-cookie')).toBeNull();
    const back = new URL((await consent!.headerValue('location'))!, CONTENT_URL);
    expect(back.pathname).toBe('/-/login');
    expect(back.searchParams.get('returnTo')).toBe(PREVIEW_PATH);
    // The raw headers of these hops are there to read: the login's carries the site cookie.
    expect(await login!.headerValue('set-cookie')).toContain('__Secure-toon-content=');

    // Back on the page, the widget now sends with the account.
    const signedIn = await openPanel(giverPage);
    await expect(signedIn.getByRole('button', { name: 'Mijn feedback' })).toBeVisible();
    await expect(signedIn.getByRole('link', { name: 'Stoppen met feedback via je account' })).toBeVisible();
    await expect(signedIn.getByRole('link', { name: 'Met je account versturen' })).toHaveCount(0);
    await signedIn.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    await expect(
      signedIn.getByText(
        'Je stuurt dit met je account. Wat het siteteam van je ziet, stond op de pagina waar je toestemming gaf.',
      ),
    ).toBeVisible();
    await expect(signedIn.getByRole('textbox', { name: /^Je naam/ })).toHaveCount(0);
    await sendFeedback(signedIn, COMMENT_ACCOUNT);
    await expect(signedIn.getByText('Je vindt die terug onder Mijn feedback.')).toBeVisible();

    await signedIn.getByRole('button', { name: 'Mijn feedback' }).click();
    await expect(signedIn.getByRole('heading', { name: 'Mijn feedback' })).toBeVisible();
    const mine = signedIn.getByRole('listitem').filter({ hasText: COMMENT_ACCOUNT });
    await expect(mine).toBeVisible();
    await expect(mine).toContainText('Nieuw');

    const item = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT_ACCOUNT);
    accountId = item.id;
    expect(item.author).toEqual({ kind: 'account', name: GIVER_NAME, email: GIVER_EMAIL });
  });

  test('a page opened before feedback went off can no longer send, and keeps the text', async () => {
    await visitorPage.goto(PREVIEW_URL);
    const panel = await openPanel(visitorPage);
    await panel.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    await commentEditor(panel).fill(COMMENT_REFUSED);

    // The token in this page is checked again on every request.
    const off = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/feedback-settings`, {
      ...ON_PREVIEWS,
      enabled: false,
    });
    expect(off.status).toBe(200);
    await panel.getByRole('button', { name: 'Versturen' }).click();
    await expect(panel.getByRole('alert')).toHaveText(
      'Versturen lukt niet meer vanaf deze pagina. Kopieer je tekst en laad de pagina opnieuw.',
    );
    await expect(commentText(panel)).toHaveValue(COMMENT_REFUSED);
    // A shielded page may be refused the clipboard: either way it says what happened.
    await panel.getByRole('button', { name: 'Kopieer je tekst' }).click();
    await expect(widgetHost(visitorPage).getByRole('status')).toContainText(
      /Je tekst is gekopieerd\.|Kopiëren lukte niet\./,
    );

    const on = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/feedback-settings`, ON_PREVIEWS);
    expect(on.status).toBe(200);
    const { items } = await feedbackPage(adminPage, GROUP, SITE);
    expect(items.map((item) => item.comment)).not.toContain(COMMENT_REFUSED);
  });

  test('hiding the button until the browser closes, and the link that brings it back', async ({ browser }) => {
    const context = await browser.newContext(CONTEXT_OPTIONS);
    const page = await context.newPage();
    await page.goto(PREVIEW_URL);
    const panel = await openPanel(page);
    await panel.getByRole('button', { name: 'Knop verbergen' }).click();
    await panel.getByRole('button', { name: 'Tot je de browser sluit' }).click();
    const showLink = await panel.getByRole('link').getAttribute('href');
    expect(showLink).toBe(`${CONTENT_URL}/-/feedback/show?returnTo=${encodeURIComponent(PREVIEW_PATH)}`);

    const hidden = page.waitForResponse((response) => response.url().endsWith('/-/feedback/hide'));
    const reloaded = page.waitForEvent('load');
    await panel.getByRole('button', { name: 'Verbergen' }).click();
    expect((await hidden).status()).toBe(303);
    // The page comes back without any Toon code, and stays that way. After
    // the load event a deferred widget would have run.
    await reloaded;
    await expect(page.locator('h1')).toHaveText('Feedback preview');
    await expect(widgetScript(page)).toHaveCount(0);
    await expect(widgetHost(page)).toHaveCount(0);
    const cookies = (await context.cookies(PREVIEW_URL)).map((cookie) => cookie.name);
    expect(cookies).toContain('__Secure-toon-feedback-off');
    await page.reload();
    await expect(page.locator('h1')).toHaveText('Feedback preview');
    await expect(widgetScript(page)).toHaveCount(0);

    await page.goto(showLink!);
    await expect(page).toHaveURL(PREVIEW_URL);
    await expect(feedbackButton(page)).toBeVisible();
    const after = (await context.cookies(PREVIEW_URL)).map((cookie) => cookie.name);
    expect(after).not.toContain('__Secure-toon-feedback-off');
    await context.close();
  });

  test('after hiding the button for a minute, keyboard focus goes back to the page element that had it', async () => {
    const page = await visitorContext.newPage();
    await page.goto(`${PREVIEW_URL}${LIST_PAGE}`);
    // From the last link of the page, Tab goes on to the button.
    const last = page.getByRole('link', { name: 'Document 30', exact: true });
    await last.focus();
    await page.keyboard.press('Tab');
    await expect(feedbackButton(page)).toBeFocused();
    await page.keyboard.press('Enter');
    const panel = feedbackPanel(page);
    await panel.getByRole('button', { name: 'Knop verbergen' }).focus();
    await page.keyboard.press('Enter');
    await panel.getByRole('button', { name: 'Een minuut' }).focus();
    await page.keyboard.press('Enter');
    // The button goes along with the panel that had focus. Focus does not
    // fall to nothing: it goes back to the link it came from.
    await expect(widgetHost(page).getByRole('status')).toContainText(
      'De feedbackknop is verborgen en komt over een minuut terug.',
    );
    await expect(panel).toBeHidden();
    await expect(feedbackButton(page)).toBeHidden();
    await expect(last).toBeFocused();
    await page.close();
  });

  test('axe finds nothing serious or critical in the open widget', async () => {
    await visitorPage.goto(PREVIEW_URL);
    const panel = await openPanel(visitorPage);

    async function audit(step: string): Promise<void> {
      // The panel fades in: measure the colours it ends with.
      await panel.evaluate(async (element) => {
        await Promise.all(element.getAnimations().map((animation) => animation.finished));
      });
      const results = await new AxeBuilder({ page: visitorPage }).include('[data-toon-feedback-host]').analyze();
      const serious = results.violations.filter(
        (violation) => violation.impact === 'serious' || violation.impact === 'critical',
      );
      expect(serious, `${step}: ${JSON.stringify(serious, null, 2)}`).toEqual([]);
      // Not vacuous: the audit reached the elements inside the shadow root,
      // whose targets are a path through it (host, then element).
      const inside = results.passes.flatMap((rule) => rule.nodes).filter((node) => Array.isArray(node.target[0]));
      expect(inside.length, `${step}: nodes audited inside the widget`).toBeGreaterThan(0);
    }

    await audit('menu');
    await panel.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    await expect(commentEditor(panel)).toBeFocused();
    await audit('composer');
  });

  test('a preview with open feedback links to the list of that preview, and back', async () => {
    await teamPage.goto(`${ADMIN_URL}/${GROUP}/${SITE}/previews`);
    const link = teamPage.getByTestId(`preview-feedback-${PREVIEW_REF}`);
    await expect(link).toHaveText('4 open feedbackitems');

    await link.click();
    await expect(teamPage).toHaveURL(`${ADMIN_URL}/${GROUP}/${SITE}/feedback?target=preview&ref=${PREVIEW_REF}`);
    await expect(teamPage.getByTestId('feedback-where')).toHaveValue(`preview:${PREVIEW_REF}`);
    await expect(teamPage.getByTestId('feedback-list').locator('nldd-list-item')).toHaveCount(4);

    // A choice of status replaces the address: back leaves the list.
    await teamPage.getByRole('radio', { name: 'Alles' }).click();
    await expect(teamPage).toHaveURL(/\?status=all&target=preview&ref=pr-1$/);
    await expect(teamPage.getByRole('radio', { name: 'Alles' })).toBeFocused();
    await teamPage.goBack();
    await expect(teamPage).toHaveURL(`${ADMIN_URL}/${GROUP}/${SITE}/previews`);
  });

  test('the Feedback tab lists the items, and the team replies and resolves one', async () => {
    const { items, counts } = await feedbackPage(adminPage, GROUP, SITE);
    expect(items).toHaveLength(4);
    expect(counts).toEqual({ new: 4, seen: 0, resolved: 0 });

    await adminPage.goto(`${ADMIN_URL}/${GROUP}/${SITE}/feedback`);
    await expect(adminPage.getByTestId('tab-feedback')).toContainText('(4)');
    await expect(adminPage.getByTestId('feedback-list')).toBeVisible();
    for (const item of items) {
      await expect(adminPage.getByTestId(`feedback-${item.id}`)).toContainText(item.comment);
    }

    // The point: its screenshot loads in the sheet.
    await adminPage.getByTestId(`feedback-${pointId}`).click();
    const screenshot = adminPage.getByTestId('feedback-screenshot').locator('img');
    await expect(screenshot).toBeVisible();
    await expect.poll(() => screenshot.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
    await adminPage.getByTestId('feedback-sheet-close').click();
    await expect(adminPage.getByTestId('feedback-screenshot')).toBeHidden();

    // The account item: the team replies, then resolves it. A reply typed
    // and not sent stays with its item when Escape closes the sheet.
    await adminPage.getByTestId(`feedback-${accountId}`).click();
    await expect(adminPage.getByTestId('feedback-comment')).toContainText(COMMENT_ACCOUNT);
    await expect(adminPage.getByTestId('feedback-meta-author')).toContainText(GIVER_NAME);
    await expect(adminPage.getByTestId('feedback-meta-author')).toContainText(GIVER_EMAIL);
    const replyField = adminPage.getByTestId('feedback-reply').locator('textarea');
    await replyField.fill(REPLY);
    await replyField.press('Escape');
    await expect(adminPage.getByTestId('feedback-comment')).toBeHidden();
    await adminPage.getByTestId(`feedback-${accountId}`).click();
    await expect(replyField).toHaveValue(REPLY);
    await adminPage.getByTestId('feedback-reply-submit').click();
    await expect(adminPage.getByTestId('feedback-thread')).toContainText(REPLY);
    await adminPage.getByTestId('feedback-mark-resolved').click();
    await expect(adminPage.getByTestId('feedback-mark-new')).toBeVisible();
    await expect(adminPage.getByTestId('feedback-mark-resolved')).toHaveCount(0);

    const detail = await feedbackDetail(adminPage, GROUP, SITE, accountId);
    expect(detail.status).toBe('resolved');
    expect(detail.replies).toMatchObject([{ author: { kind: 'member' }, body: REPLY }]);
  });

  test('the visitor with an account reads the reply under "My feedback"', async () => {
    await giverPage.goto(PREVIEW_URL);
    const panel = await openPanel(giverPage);
    await panel.getByRole('button', { name: 'Mijn feedback' }).click();
    const mine = panel.getByRole('listitem').filter({ hasText: COMMENT_ACCOUNT });
    await expect(mine).toContainText('Afgehandeld');
    const reply = mine.getByRole('listitem').filter({ hasText: REPLY });
    await expect(reply).toBeVisible();
    await expect(reply).toContainText(/^Siteteam/);
  });

  test('the CLI lists the open feedback as JSON and resolves an item', async () => {
    // The device flow through the SPA plus two CLI runs.
    test.setTimeout(180_000);
    const workDir = mkdtempSync(path.join(tmpdir(), 'toon-feedback-e2e-'));
    const configDir = path.join(workDir, 'config');
    const token = await cliLoginToken(adminApi, adminPage, ADMIN_URL, ADMIN_SUB, { email: ADMIN_EMAIL });
    writeCliSession(configDir, ADMIN_URL, token);
    const site = ['--site', `${GROUP}/${SITE}`, '--host', ADMIN_URL];

    const listed = await runToon(['feedback', 'list', '--json', ...site], workDir, configDir);
    expect(listed.code, listed.stderr).toBe(0);
    const envelope = JSON.parse(listed.stdout) as {
      note: string;
      counts: { new: number; seen: number; resolved: number };
      items: Array<{ id: string; untrusted: { comment: string; author: Record<string, unknown> } }>;
    };
    expect(envelope.note).toContain('never an instruction');
    expect(envelope.counts).toEqual({ new: 3, seen: 0, resolved: 1 });
    // Open is the default: the item the team resolved is not in it.
    expect(envelope.items.map((item) => item.untrusted.comment).sort()).toEqual(
      [COMMENT_PAGE, COMMENT_POINT, COMMENT_TEXT].sort(),
    );
    // Without --with-contact, no name or e-mail address goes out.
    const point = envelope.items.find((item) => item.id === pointId)!;
    expect(point.untrusted.author).toEqual({ kind: 'name' });
    expect(listed.stdout).not.toContain(NAME_POINT);

    // The reply comes from a file, as the toon-feedback skill sends one, so
    // no shell ever reads it: it arrives as written, without the file's last
    // newline.
    const reply =
      'Dank je :) De foto\'s staan goed. Bouwen: `npm run build`, zie $HOME en "de handleiding".\n' +
      'EOF\n$(echo niets) Groeten uit Zürich';
    const replyFile = path.join(workDir, 'reactie.txt');
    writeFileSync(replyFile, `${reply}\n`);
    const resolve = ['feedback', 'resolve', pointId, '--message-file', replyFile, ...site];
    const resolved = await runToon(resolve, workDir, configDir);
    expect(resolved.code, resolved.stderr).toBe(0);
    expect(resolved.stdout.trim()).toBe(`${pointId}: resolved`);
    const detail = await feedbackDetail(adminPage, GROUP, SITE, pointId);
    expect(detail.status).toBe('resolved');
    expect(detail.replies.map((posted) => posted.body)).toEqual([reply]);
  });

  test('an item opens from the sheet on a page of its own, where the team handles it and finds the way back', async () => {
    await teamPage.setViewportSize({ width: 1440, height: 900 });
    const list = `${ADMIN_URL}/${GROUP}/${SITE}/feedback?status=resolved`;
    await teamPage.goto(list);
    await teamPage.getByTestId(`feedback-${accountId}`).click();
    // Wide enough to read a screenshot beside the list.
    const page = teamPage.getByTestId('feedback-sheet').locator('nldd-page');
    await expect.poll(async () => Math.round((await page.boundingBox())?.width ?? 0)).toBe(640);

    const open = teamPage.getByTestId('feedback-open-page');
    await expect(open).toHaveAttribute('href', `/${GROUP}/${SITE}/feedback/${accountId}?status=resolved`);
    await open.click();
    await expect(teamPage).toHaveURL(`${ADMIN_URL}/${GROUP}/${SITE}/feedback/${accountId}?status=resolved`);
    await expect(teamPage.locator('#heading-feedback-item')).toBeFocused();
    await expect(teamPage.locator('#heading-feedback-item')).toHaveText(/^Feedback op /);
    await expect(teamPage).toHaveTitle(/^Feedback op .* - Website - Toon$/);
    await expect(teamPage.getByTestId('feedback-sheet')).toHaveCount(0);
    await expect(teamPage.getByTestId('tab-feedback')).toHaveAttribute('current', '');

    // Replying and the status work here as in the sheet.
    const reply = 'Ook op de eigen pagina beantwoord.';
    await teamPage.getByTestId('feedback-reply').locator('textarea').fill(reply);
    await teamPage.getByTestId('feedback-reply-submit').click();
    await expect(teamPage.getByTestId('feedback-thread')).toContainText(reply);
    await teamPage.getByTestId('feedback-mark-new').click();
    await teamPage.getByTestId('feedback-mark-resolved').click();
    await expect(teamPage.getByTestId('feedback-mark-new')).toBeVisible();
    expect((await feedbackDetail(adminPage, GROUP, SITE, accountId)).status).toBe('resolved');

    await teamPage.getByTestId('feedback-page-back').click();
    await expect(teamPage).toHaveURL(list);
    await expect(teamPage.getByTestId(`feedback-${accountId}`)).toBeVisible();

    // An id the site has no item under says only that.
    await teamPage.goto(`${ADMIN_URL}/${GROUP}/${SITE}/feedback/00000000-0000-4000-8000-000000000000`);
    await expect(teamPage.locator('nldd-banner')).toHaveAttribute('text', 'Onbekende feedback');
  });

  test('at 320 px the status filter of the Feedback tab shows every label in full', async () => {
    await teamPage.setViewportSize(NARROW);
    await teamPage.goto(`${ADMIN_URL}/${GROUP}/${SITE}/feedback`);
    await expect(teamPage.getByRole('radio', { name: 'Afgehandeld (2)' })).toBeVisible();
    // Items under each status, so every label carries a count.
    await expect
      .poll(() => statusFilter(teamPage))
      .toEqual([
        { label: 'Open (2)', cut: false, outside: false },
        { label: 'Afgehandeld (2)', cut: false, outside: false },
        { label: 'Alles (4)', cut: false, outside: false },
      ]);
    expect(await sidewaysOverflow(teamPage)).toBe(0);
  });

  test('deleting the last item under a status puts the focus on the heading of the list', async () => {
    // "Resolved" holds the item the team resolved and the one the CLI did.
    await teamPage.getByRole('radio', { name: 'Afgehandeld (2)' }).click();
    const point = teamPage.getByTestId(`feedback-${pointId}`);
    const account = teamPage.getByTestId(`feedback-${accountId}`);
    await expect(point).toBeVisible();
    await expect(account).toBeVisible();

    // One of two goes: the focus goes to the row that is left.
    await deleteFromTab(teamPage, point);
    await expect(account).toBeFocused();
    // The last one goes: no row is left, so the focus goes to the heading.
    await deleteFromTab(teamPage, account);
    await expect(teamPage.locator('#heading-feedback-inbox')).toBeFocused();
    await expect(teamPage.getByTestId('feedback-empty-filter')).toBeVisible();
    expect((await feedbackPage(adminPage, GROUP, SITE)).counts).toEqual({ new: 2, seen: 0, resolved: 0 });
  });

  test('a pin of "My feedback" under the open panel takes no press and is no stop for Tab, and clear of it lets a press through to what it marks', async () => {
    // The visitor with an account points at a link down the right-hand side
    // of the page, where the panel of a wide window lies.
    await giverPage.goto(`${PREVIEW_URL}${LIST_PAGE}`);
    const link = giverPage.getByRole('link', { name: 'Document 10', exact: true });
    const spot = await middleOf(link);
    const panel = await openPanel(giverPage);
    await panel.getByRole('button', { name: 'Wijs een punt of gebied aan' }).click();
    await giverPage.mouse.click(spot.x, spot.y);
    await expect(
      panel.getByRole('img', { name: 'Schermafbeelding van wat je nu ziet, met de aangewezen plek gemarkeerd' }),
    ).toBeVisible({ timeout: 15_000 });
    await sendFeedback(panel, COMMENT_PINNED);

    // Under "My feedback" its pin lies on the link, and so under the panel,
    // which is drawn over it and takes a press there. Out of sight, the pin
    // is no stop for Tab: its item is in the list.
    await panel.getByRole('button', { name: 'Mijn feedback' }).click();
    await expect(panel.getByRole('listitem').filter({ hasText: COMMENT_PINNED })).toBeVisible();
    const pin = widgetHost(giverPage).getByRole('button', { name: /^Feedback: Deze link hoort/ });
    await expect.poll(() => pinOffset(pin, link, { x: 0.5, y: 0.5 })).toBeLessThan(1.5);
    const under = edges((await pin.boundingBox())!);
    expect(overlap(under, edges((await panel.boundingBox())!)), 'the pin lies under the panel').toBe(true);
    const onTop = await widgetHost(giverPage).evaluate((host, at) => {
      const hit = host.shadowRoot!.elementFromPoint(at.x, at.y);
      if (hit?.closest('[data-pin]')) return 'pin';
      return hit?.closest('[role="dialog"]') ? 'panel' : 'other';
    }, await middleOf(pin));
    expect(onTop).toBe('panel');
    await expect(pin).toHaveAttribute('tabindex', '-1');

    // Keyboard focus on a link under the panel sends the panel to the left,
    // clear of the pin. A ring, the pin then lets a press through to the link
    // it lies on, and it is a stop for Tab again.
    for (let press = 0; press < 20 && (await panel.boundingBox())!.x !== 16; press += 1) {
      await giverPage.keyboard.press('Shift+Tab');
    }
    expect((await panel.boundingBox())!.x).toBe(16);
    await expect(pin).toHaveAttribute('tabindex', '0');
    const free = await middleOf(pin);
    expect(await giverPage.evaluate((at) => document.elementFromPoint(at.x, at.y)?.textContent, free)).toBe(
      'Document 10',
    );
    await giverPage.mouse.click(free.x, free.y);
    await expect(giverPage).toHaveURL(/#document-10$/);
  });

  test('the screenshot of a scrolled page shows the bar fixed at the top of the window', async () => {
    // Far down the page, a visitor points at the link in the bar.
    const page = await visitorContext.newPage();
    await page.goto(`${PREVIEW_URL}${BAR_PAGE}`);
    await page.evaluate(() => window.scrollTo(0, 700));
    const spot = await middleOf(page.getByRole('link', { name: 'Contact' }));
    const panel = await openPanel(page);
    await panel.getByRole('button', { name: 'Wijs een punt of gebied aan' }).click();
    await page.mouse.click(spot.x, spot.y);
    await expect(
      panel.getByRole('img', { name: 'Schermafbeelding van wat je nu ziet, met de aangewezen plek gemarkeerd' }),
    ).toBeVisible({ timeout: 15_000 });
    await sendFeedback(panel, COMMENT_BAR);
    await page.close();

    // The picture the team gets is the window as it was, the bar at its top,
    // here halfway along it and clear of the mark on the link.
    const { id } = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT_BAR);
    const shot = await adminFetchBytes(adminPage, `${API}/sites/${GROUP}/${SITE}/feedback/${id}/screenshot`);
    expect(shot.status).toBe(200);
    const colour = await adminPage.evaluate(
      async ({ data, at }) => {
        const bytes = Uint8Array.from(atob(data), (char) => char.charCodeAt(0));
        const picture = await createImageBitmap(new Blob([bytes], { type: 'image/jpeg' }));
        const canvas = new OffscreenCanvas(picture.width, picture.height);
        const context = canvas.getContext('2d')!;
        context.drawImage(picture, 0, 0);
        const x = Math.round(at.x * picture.width);
        const y = Math.round(at.y * picture.height);
        return [...context.getImageData(x, y, 1, 1).data.slice(0, 3)];
      },
      { data: shot.bytes.toString('base64'), at: { x: 0.5, y: 28 / 720 } },
    );
    expect(
      colour.every((channel, index) => Math.abs(channel - BAR_COLOUR[index]!) < 24),
      `the bar's colour, not ${JSON.stringify(colour)}`,
    ).toBe(true);
  });

  test('the screenshot of a page scrolled a window down shows that window where the root hides sideways overflow', async () => {
    // The page's root hides what overflows it sideways. A copy of that root
    // as high as the window would clip all below it, so the picture of a
    // page scrolled a window down would be blank.
    const page = await visitorContext.newPage();
    await page.goto(`${PREVIEW_URL}${MATERIAL_PAGE}`);
    await page.evaluate(() => window.scrollTo(0, window.innerHeight));
    expect(await page.evaluate(() => window.scrollY)).toBe(720);
    // The window as the visitor sees it before the widget opens.
    const seen = await page.screenshot();
    const target = page.locator('#p20');
    await expect(target).toBeInViewport();
    const spot = await middleOf(target);
    // The widget only reads the page: nothing of it changes for the picture,
    // not even for a moment. Its own host is the widget's, not the page's.
    await page.evaluate(() => {
      const host = document.querySelector('[data-toon-feedback-host]');
      const changes: string[] = [];
      new MutationObserver((records) => {
        for (const record of records) {
          if (record.type === 'attributes' && record.target === host) continue;
          changes.push(`${record.type} ${record.target.nodeName} ${record.attributeName ?? ''}`.trim());
        }
      }).observe(document.documentElement, { attributes: true, childList: true, characterData: true, subtree: true });
      Object.assign(window, { toonPageChanges: changes });
    });
    const panel = await openPanel(page);
    await panel.getByRole('button', { name: 'Wijs een punt of gebied aan' }).click();
    await page.mouse.click(spot.x, spot.y);
    const thumbnail = panel.getByRole('img', {
      name: 'Schermafbeelding van wat je nu ziet, met de aangewezen plek gemarkeerd',
    });
    await expect(thumbnail).toBeVisible({ timeout: 15_000 });
    expect(await page.evaluate(() => (window as unknown as { toonPageChanges: string[] }).toonPageChanges)).toEqual([]);
    expect(await page.evaluate(() => window.scrollY)).toBe(720);

    // Over the column of text, clear of the mark on the point and of the
    // button at the bottom right, the picture the composer holds shows the
    // bands and their text where the window showed them.
    const column = edges((await page.locator('main').boundingBox())!);
    const compared = await pictureAgainstWindow(
      thumbnail,
      seen,
      { left: column.left, top: 0, right: column.right, bottom: 720 },
      { left: spot.x - 24, top: spot.y - 24, right: spot.x + 24, bottom: spot.y + 24 },
    );
    expect(compared.inked, 'the share of the window that shows more than white').toBeGreaterThan(0.3);
    expect(compared.differ, 'the share of the picture that differs from the window').toBeLessThan(0.1);
    await page.close();
  });

  test('"View on the page" brings a point low on a listing three windows high into view, and says it is marked', async () => {
    // A visitor points at nine tenths of the listing's height.
    const page = await visitorContext.newPage();
    await page.goto(`${PREVIEW_URL}${LISTING_PAGE}`);
    const listing = page.locator('#regels');
    const tall = (await listing.boundingBox())!;
    expect(tall.height).toBe(3 * 720);
    await page.evaluate((y) => window.scrollTo(0, y - window.innerHeight / 2), tall.y + 0.9 * tall.height);
    const box = (await listing.boundingBox())!;
    const spot = { x: box.x + 0.3 * box.width, y: box.y + 0.9 * box.height };
    const panel = await openPanel(page);
    await panel.getByRole('button', { name: 'Wijs een punt of gebied aan' }).click();
    await page.mouse.click(spot.x, spot.y);
    await expect(
      panel.getByRole('img', { name: 'Schermafbeelding van wat je nu ziet, met de aangewezen plek gemarkeerd' }),
    ).toBeVisible({ timeout: 15_000 });
    await sendFeedback(panel, COMMENT_LOW);
    await page.close();
    const { id } = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT_LOW);
    const { anchor } = await feedbackDetail(adminPage, GROUP, SITE, id);
    expect(anchor!.selectors).toEqual([{ type: 'CssSelector', value: '#regels' }]);
    expect(anchor!.point!.y).toBeCloseTo(0.9, 2);

    // The team opens its page, which loads at the top. Centred as a whole,
    // the listing would keep the point below the window, and nothing would
    // be said: the window takes the point itself to its middle.
    const opened = await viewOnPage(id);
    const ring = widgetHost(opened).getByRole('img', { name: 'Hier gaat de feedback over' });
    await expect(ring).toBeInViewport();
    await expect.poll(() => pinOffset(ring, opened.locator('#regels'), anchor!.point!)).toBeLessThan(1.5);
    // In the words of the listing the point is on.
    await expect(widgetHost(opened).getByRole('status')).toContainText(
      'Gemarkeerd: regel 1: bedrag_1 = som(onderdeel_1)',
    );
    await opened.close();
  });

  test('"View on the page" brings a quote near the end of a long listing into view, and says it is marked', async () => {
    // A visitor selects a line near the end of the listing, by a triple
    // click, which takes the whole line.
    const page = await visitorContext.newPage();
    await page.goto(`${PREVIEW_URL}${LISTING_PAGE}`);
    const line = await page.locator('#regels').evaluate((element, text) => {
      const node = element.firstChild as Text;
      const range = document.createRange();
      range.setStart(node, node.data.indexOf(text));
      range.setEnd(node, node.data.indexOf(text) + text.length);
      window.scrollTo(0, window.scrollY + range.getBoundingClientRect().top - window.innerHeight / 2);
      const box = range.getBoundingClientRect();
      return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    }, LISTED);
    const panel = await openPanel(page);
    await page.mouse.click(line.x, line.y, { clickCount: 3 });
    await widgetHost(page).getByRole('button', { name: 'Feedback op deze tekst' }).click();
    await expect(panel.getByText(LISTED, { exact: true })).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Schermafbeelding maken' })).toBeVisible();
    await sendFeedback(panel, COMMENT_LISTED);
    await page.close();
    const item = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT_LISTED);
    expect(item).toMatchObject({ quote: LISTED, hasScreenshot: false });

    // Opened from the item, the passage is marked in the window, line and
    // ring, and said in its own first words.
    const opened = await viewOnPage(item.id);
    const marks = widgetHost(opened).locator('.mark');
    await expect(marks).toHaveCount(1);
    await expect(marks).toBeInViewport();
    await expect(widgetHost(opened).getByRole('img', { name: 'Hier gaat de feedback over' })).toBeInViewport();
    await expect(widgetHost(opened).getByRole('status')).toContainText(`Gemarkeerd: ${LISTED}`);
    expect(await quoteMarks(opened, '#regels')).toMatchObject({ lines: 1, out: 0 });
    await opened.close();
  });

  test('an emoji held together by a zero-width joiner arrives whole, from a visitor and from the team', async () => {
    // Typed with an emoji picker: the widget keeps the joiner, and the API
    // takes it. The item is found by its comment exactly as it was typed.
    await visitorPage.goto(PREVIEW_URL);
    const panel = await openPanel(visitorPage);
    await panel.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    await sendFeedback(panel, COMMENT_JOINED);
    const item = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT_JOINED);

    // The team replies in the Feedback tab with a word pasted from the site:
    // the tab drops its soft hyphens, which the API refuses, and keeps the
    // joiner.
    await adminPage.goto(`${ADMIN_URL}/${GROUP}/${SITE}/feedback`);
    await adminPage.getByTestId(`feedback-${item.id}`).click();
    await expect(adminPage.getByTestId('feedback-comment')).toContainText(COMMENT_JOINED);
    await adminPage.getByTestId('feedback-reply').locator('textarea').fill(REPLY_PASTED);
    await adminPage.getByTestId('feedback-reply-submit').click();
    await expect(adminPage.getByTestId('feedback-thread')).toContainText(REPLY_JOINED);
    const detail = await feedbackDetail(adminPage, GROUP, SITE, item.id);
    expect(detail.replies.map((posted) => posted.body)).toEqual([REPLY_JOINED]);
  });

  test('a page opened before its site got a new address says so when it sends, and keeps the text', async () => {
    await visitorPage.goto(PREVIEW_URL);
    const panel = await openPanel(visitorPage);
    await panel.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    await commentEditor(panel).fill(COMMENT_MOVED);

    // The site moves while the page is open. The page's token names the site
    // by its id and still holds, but the page sits at the old address.
    const moved = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/slug`, { slug: SITE_MOVED });
    expect(moved.status, moved.text).toBe(200);
    await panel.getByRole('button', { name: 'Versturen' }).click();
    await expect(panel.getByRole('alert')).toHaveText(
      'De site of groep heeft een nieuw adres gekregen. Kopieer je tekst en laad de pagina opnieuw.',
    );
    await expect(commentText(panel)).toHaveValue(COMMENT_MOVED);
    await expect(panel.getByRole('button', { name: 'Kopieer je tekst' })).toBeVisible();
    const { items } = await feedbackPage(adminPage, GROUP, SITE_MOVED);
    expect(items.map((item) => item.comment)).not.toContain(COMMENT_MOVED);

    // Loaded again, the old address leads to the new one, and from there it sends.
    await visitorPage.goto(PREVIEW_URL);
    expect(visitorPage.url()).toBe(MOVED_PREVIEW_URL);
    const again = await openPanel(visitorPage);
    await again.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    await sendFeedback(again, COMMENT_MOVED);
    const item = await feedbackWithComment(adminPage, GROUP, SITE_MOVED, COMMENT_MOVED);
    expect(item.page).toMatchObject({ path: '', url: MOVED_PREVIEW_URL });

    // Back to the old address, which stayed reserved for this site.
    const back = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE_MOVED}/slug`, { slug: SITE });
    expect(back.status, back.text).toBe(200);
  });

  test('a team member who did not consent sends without a name on a team-only preview', async ({ browser }) => {
    // The preview gets access of its own: the site team only, which the admin
    // who made the group belongs to. The site itself stays public.
    const teamOnly = await adminFetch(
      adminPage,
      'PUT',
      `${API}/sites/${GROUP}/${SITE}/previews/${PREVIEW_REF}/access`,
      { access: { base: 'site_team', keys: false, invitees: false } },
    );
    expect(teamOnly.status, teamOnly.text).toBe(200);
    expect((await contentApi.get(PREVIEW_PATH, { maxRedirects: 0 })).status()).toBe(404);

    // Signed in on the content host, the session is what lets the member see
    // the page. The widget sends with that, without any yes to feedback with
    // the account.
    const context = await browser.newContext(CONTEXT_OPTIONS);
    const page = await context.newPage();
    await visitWithLogin(page, PREVIEW_URL, ADMIN_SUB, { email: ADMIN_EMAIL });
    await expect(page.locator('h1')).toHaveText('Feedback preview');
    const panel = await openPanel(page);
    await expect(panel.getByRole('link', { name: 'Met je account versturen' })).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Mijn feedback' })).toHaveCount(0);
    await panel.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    await sendFeedback(panel, COMMENT_TEAM);
    await expect(
      panel.getByText('Je stuurde dit zonder account. Daarom kun je reacties van het siteteam niet zien.'),
    ).toBeVisible();
    await context.close();

    // Nothing of the session goes with the item.
    const item = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT_TEAM);
    expect(item).toMatchObject({
      kind: 'page',
      target: 'preview',
      previewRef: PREVIEW_REF,
      author: { kind: 'anonymous', name: null, email: null },
    });
  });

  test('on a shielded preview only the team can see, the screenshot asks for none of the images of the page, and the gate refuses nothing', async ({
    browser,
  }) => {
    // Shielded, the page has an opaque origin: a request of the screenshot
    // for one of its images would go without the site's cookie, and the gate
    // would refuse it and keep that with the member's IP address. The audit
    // log has what the gate turned away on this site.
    const turnedAway = async () => {
      const read = await adminFetch(
        adminPage,
        'GET',
        `${API}/platform/audit?action=content_access&group=${GROUP}&site=${SITE}&limit=200`,
      );
      expect(read.status, read.text).toBe(200);
      const { entries } = read.json as {
        entries: Array<{ id: string; result: string; reasonCode: string | null; refs: { path?: string } }>;
      };
      return entries
        .filter((entry) => entry.result !== 'allowed')
        .map((entry) => ({ id: entry.id, result: entry.result, reason: entry.reasonCode, path: entry.refs.path }));
    };
    const context = await browser.newContext(CONTEXT_OPTIONS);
    const page = await context.newPage();
    await visitWithLogin(page, `${PREVIEW_URL}${IMAGES_PAGE}`, ADMIN_SUB, { email: ADMIN_EMAIL });
    // The page shows its images, which it asked for with the site's cookie.
    const chart = page.getByRole('img', { name: 'Grafiek van de begroting' });
    await expect.poll(() => chart.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
    // What it turned away until now, the first look before the login among it.
    const before = new Set((await turnedAway()).map((entry) => entry.id));
    // The page loads its images as images. The screenshot would fetch them.
    const fetched: string[] = [];
    page.on('request', (request) => {
      const { pathname } = new URL(request.url());
      if (request.resourceType() === 'fetch' && /\/(grafiek|achtergrond)\.svg$/.test(pathname)) {
        fetched.push(pathname);
      }
    });
    const panel = await openPanel(page);
    await panel.getByRole('button', { name: 'Wijs een punt of gebied aan' }).click();
    // The chart, on the left: clear of the bar the pointing overlay shows at the top.
    const spot = await middleOf(chart);
    await page.mouse.click(spot.x, spot.y);
    // The composer holds the picture all the same, the images in it grey,
    // and says so.
    await expect(
      panel.getByRole('img', { name: 'Schermafbeelding van wat je nu ziet, met de aangewezen plek gemarkeerd' }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      panel.getByText('Afbeeldingen op deze pagina staan als grijze vlakken in de schermafbeelding.'),
    ).toBeVisible();
    await panel.getByRole('button', { name: 'Annuleren' }).click();
    await context.close();

    expect(fetched).toEqual([]);
    expect((await turnedAway()).filter((entry) => !before.has(entry.id))).toEqual([]);
  });

  test('a member signed in on a team-only preview is led straight to the consent page', async ({ browser }) => {
    // The site takes feedback with an account only, and the member never said
    // yes: the menu offers the way to an account and nothing else.
    const accountOnly = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/feedback-settings`, {
      ...ON_PREVIEWS,
      identity: 'account',
    });
    expect(accountOnly.status, accountOnly.text).toBe(200);

    // A browser that asks for Dutch, the language of the page and so of the
    // widget, which the consent page then speaks as well.
    const context = await browser.newContext({ ...CONTEXT_OPTIONS, locale: 'nl-NL' });
    const page = await context.newPage();
    await visitWithLogin(page, PREVIEW_URL, ADMIN_SUB, { email: ADMIN_EMAIL });
    await expect(page.locator('h1')).toHaveText('Feedback preview');
    const panel = await openPanel(page);
    await expect(panel.getByRole('button', { name: 'Algemene feedback', exact: true })).toHaveCount(0);

    // The link names the page it opens, with a login first or not. The
    // member signed in to see the preview, so no login comes.
    const link = panel.getByRole('link', { name: 'Feedback geven met je account' });
    await expect(link).toBeVisible();
    const hops: string[] = [];
    page.on('response', (response) => {
      if (response.request().isNavigationRequest() && response.frame() === page.mainFrame()) {
        const { host, pathname } = new URL(response.url());
        hops.push(`${response.request().method()} ${host}${pathname} ${response.status()}`);
      }
    });
    await link.click();
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Feedback geven met je account');
    expect(hops).toEqual([`GET ${CONTENT_HOST}/-/feedback/account 200`]);
    // Without a name, the page says which account is signed in. The team
    // sees only the email address the IdP verified, never the account id.
    await expect(page.getByRole('main')).toContainText(`Je bent ingelogd met account ${ADMIN_SUB}.`);
    await expect(page.locator('dd')).toHaveText([ADMIN_EMAIL]);
    await context.close();

    const back = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/feedback-settings`, ON_PREVIEWS);
    expect(back.status, back.text).toBe(200);
  });

  test('a visitor let in by a secret link alone gives feedback with their account after the consent page', async ({
    browser,
  }) => {
    // The site is shared by secret link alone, its preview with it, and it
    // takes feedback with an account only, as a new site does: people outside
    // the team see its pages through the link.
    const shared = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/access`, {
      base: 'nobody',
      keys: true,
    });
    expect(shared.status, shared.text).toBe(200);
    const followsSite = await adminFetch(
      adminPage,
      'PUT',
      `${API}/sites/${GROUP}/${SITE}/previews/${PREVIEW_REF}/access`,
      { access: null },
    );
    expect(followsSite.status, followsSite.text).toBe(200);
    const accountOnly = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/feedback-settings`, {
      ...ON_PREVIEWS,
      identity: 'account',
    });
    expect(accountOnly.status, accountOnly.text).toBe(200);
    const created = await adminFetch(adminPage, 'POST', `${API}/sites/${GROUP}/${SITE}/keys`, {
      label: 'e2e-feedback',
    });
    expect(created.status, created.text).toBe(201);
    const { value, key } = created.json as { value: string; key: { selector: string } };

    // Not signed in, and in through the link alone. Every address the browser
    // asks for is noted, and so is the page token of each view of the preview.
    const context = await browser.newContext({ ...CONTEXT_OPTIONS, locale: 'nl-NL' });
    const page = await context.newPage();
    const addresses: string[] = [];
    context.on('request', (request) => addresses.push(request.url()));
    const tokens: string[] = [];
    const keepToken = async (response: Response | null): Promise<void> => {
      const config = /data-toon-feedback="([\w-]+)"/.exec(await response!.text());
      expect(config, 'the preview has the widget').not.toBeNull();
      tokens.push((JSON.parse(Buffer.from(config![1]!, 'base64url').toString('utf8')) as { token: string }).token);
    };
    await keepToken(await page.goto(`${PREVIEW_URL}?key=${encodeURIComponent(value)}`));
    await expect(page.locator('h1')).toHaveText('Feedback preview');
    expect(page.url()).toBe(PREVIEW_URL);

    // The key cookie stays on the preview's path, so the way to the consent
    // page carries the link along, as a ticket of its own.
    const panel = await openPanel(page);
    const link = panel.getByRole('link', { name: 'Feedback geven met je account' });
    const ticket = new URL((await link.getAttribute('href'))!, CONTENT_URL).searchParams.get('link');
    expect(ticket).toMatch(/^[\w-]+$/);
    await link.click();

    // The sign-in, then Toon's consent page, decided with the ticket's link.
    await expect(onMockLoginPage(page)).toBeVisible();
    await fillMockLogin(page, OUTSIDER_SUB, { email: OUTSIDER_EMAIL, name: OUTSIDER_NAME });
    await page.waitForURL((url) => url.pathname === '/-/feedback/account', { timeout: 15_000 });
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Feedback geven met je account');
    await expect(page.locator('dd')).toHaveText([OUTSIDER_NAME, OUTSIDER_EMAIL]);
    await page.getByRole('checkbox', { name: 'Het siteteam mag bij mijn feedback zien wat hierboven staat.' }).check();
    const returned = page.waitForResponse(
      (response) => response.url() === PREVIEW_URL && response.request().isNavigationRequest(),
    );
    await page.getByRole('button', { name: 'Ja, met mijn account' }).click();
    await keepToken(await returned);
    await page.waitForURL(PREVIEW_URL);

    // Back on the preview, through the link still, and in account state.
    const signedIn = await openPanel(page);
    await expect(signedIn.getByRole('button', { name: 'Mijn feedback' })).toBeVisible();
    await signedIn.getByRole('button', { name: 'Algemene feedback', exact: true }).click();
    await sendFeedback(signedIn, COMMENT_LINK);
    await context.close();

    // The ticket went into addresses. A page token, the widget API's
    // credential, never did.
    expect(addresses.filter((address) => address.includes(`link=${ticket}`))).not.toEqual([]);
    expect(tokens).toHaveLength(2);
    for (const token of tokens) {
      expect(addresses.filter((address) => address.includes(token))).toEqual([]);
    }
    const item = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT_LINK);
    expect(item).toMatchObject({
      target: 'preview',
      previewRef: PREVIEW_REF,
      author: { kind: 'account', name: OUTSIDER_NAME, email: OUTSIDER_EMAIL },
    });

    // As the scenarios before left it: a public site with its preview for the
    // team only, and anonymous feedback on previews.
    const revoked = await adminFetch(adminPage, 'DELETE', `${API}/sites/${GROUP}/${SITE}/keys/${key.selector}`);
    expect(revoked.status, revoked.text).toBe(204);
    const open = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/access`, { base: 'public' });
    expect(open.status, open.text).toBe(200);
    const teamOnly = await adminFetch(
      adminPage,
      'PUT',
      `${API}/sites/${GROUP}/${SITE}/previews/${PREVIEW_REF}/access`,
      { access: { base: 'site_team', keys: false, invitees: false } },
    );
    expect(teamOnly.status, teamOnly.text).toBe(200);
    const reset = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/feedback-settings`, ON_PREVIEWS);
    expect(reset.status, reset.text).toBe(200);
  });

  test('an item from a preview that is gone says its place can no longer be viewed', async () => {
    const { id } = await feedbackWithComment(adminPage, GROUP, SITE, COMMENT_TEAM);
    const row = teamPage.getByTestId(`feedback-${id}`);
    // While its preview opens, the item offers a look at its place.
    await teamPage.goto(`${ADMIN_URL}/${GROUP}/${SITE}/feedback`);
    await row.click();
    await expect(teamPage.getByTestId('feedback-view')).toBeVisible();
    await expect(teamPage.getByTestId('feedback-view-gone')).toHaveCount(0);

    // Its address would only open the neutral 404 now: a line says so instead.
    const removed = await adminFetch(adminPage, 'DELETE', `${API}/sites/${GROUP}/${SITE}/previews/${PREVIEW_REF}`);
    expect(removed.status, removed.text).toBe(204);
    await teamPage.getByTestId('feedback-sheet-close').click();
    await teamPage.getByRole('radio', { name: /^Alles/ }).click();
    await row.click();
    await expect(teamPage.getByTestId('feedback-view-gone')).toHaveText(
      `Preview ${PREVIEW_REF} is verlopen of bestaat niet meer, dus deze plek is niet meer te bekijken.`,
    );
    await expect(teamPage.getByTestId('feedback-view')).toHaveCount(0);

    // With no preview left, the list still holds items from previews, and
    // the place filter still tells them from the live site's. The tab reads
    // the previews when it opens.
    await teamPage.reload();
    const where = teamPage.getByTestId('feedback-where');
    await expect(where.locator('option')).toHaveText(['Live en previews', 'De live site', 'Alle previews']);
    await where.selectOption('live');
    await expect(teamPage.getByTestId('feedback-empty-filter')).toBeVisible();
    await expect(row).toHaveCount(0);
    await where.selectOption('preview');
    await expect(row).toBeVisible();
  });
});

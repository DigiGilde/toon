import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '../../../tests/mock-api';
import * as toon from '@/api/toon';
import type { FeedbackItem } from '@/api/types';
import { formatTimestamp } from '@/format';
import { _setLocaleForTest } from '@/i18n';

import type { FeedbackFilters } from './feedbackRoute';
import FeedbackSheet from './FeedbackSheet.vue';
import { feedbackRouter, fireDetailEvent, problemFetch, untilIdle } from './testHelpers';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  _setLocaleForTest('nl');
});

/** The items as the list hands them to the sheet. */
async function itemOf(id: string): Promise<FeedbackItem> {
  const page = await toon.feedbackList('team-aurora', 'website', { status: 'all' });
  return page.items.find((item) => item.id === id)!;
}

interface SheetProps {
  item?: FeedbackItem | null;
  filters?: FeedbackFilters;
  canEdit?: boolean;
  canAdmin?: boolean;
  enabled?: boolean;
  live?: boolean;
}

function sheetProps(props: SheetProps = {}) {
  return {
    group: 'team-aurora',
    site: 'website',
    contentBase: MOCK_CONTENT_BASE,
    item: null,
    filters: { status: 'open', where: 'all' } as FeedbackFilters,
    canEdit: true,
    canAdmin: true,
    enabled: true,
    live: true,
    ...props,
  };
}

function makeWrapper(props: SheetProps = {}, router = feedbackRouter()) {
  return mount(FeedbackSheet, {
    props: sheetProps(props),
    global: { stubs: { teleport: true }, plugins: [router] },
  });
}

async function opened(id: string, props: Omit<SheetProps, 'item'> = {}) {
  const wrapper = makeWrapper({ ...props, item: await itemOf(id) });
  await untilIdle();
  return wrapper;
}

function meta(wrapper: ReturnType<typeof makeWrapper>, row: string) {
  return wrapper.find(`[data-testid="feedback-meta-${row}"]`);
}

/** The value of a row, without the line under it. */
function metaValue(wrapper: ReturnType<typeof makeWrapper>, row: string): string {
  const cell = meta(wrapper, row).findAll('nldd-text-cell')[1]!;
  const note = cell.find('[slot="supporting-text"]');
  return note.exists() ? cell.text().replace(note.text(), '').trim() : cell.text();
}

function type(wrapper: ReturnType<typeof makeWrapper>, value: string): void {
  fireDetailEvent(wrapper.find('[data-testid="feedback-reply"]').element, 'input', { value });
}

function field(wrapper: ReturnType<typeof makeWrapper>) {
  return wrapper.find('[data-testid="feedback-reply"]');
}

async function send(wrapper: ReturnType<typeof makeWrapper>): Promise<void> {
  await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
}

/** Takes away the preview fb-1 was given on. */
function previewGone(): void {
  backend.data.previews = [];
}

/** Closes the sheet and opens an item in it, the way the tab does. */
async function switchTo(wrapper: ReturnType<typeof makeWrapper>, id: string): Promise<void> {
  await wrapper.setProps({ item: null });
  await wrapper.setProps({ item: await itemOf(id) });
  await untilIdle();
}

describe('FeedbackSheet: closed', () => {
  it('keeps the sheet in the page, empty, until an item is opened', () => {
    const wrapper = makeWrapper();

    const sheet = wrapper.find('[data-testid="feedback-sheet"]');
    expect(sheet.exists()).toBe(true);
    expect(sheet.find('h2').exists()).toBe(false);
  });
});

describe('FeedbackSheet: the page of the item', () => {
  it('links to the page of the item, carrying the filters of the list', async () => {
    const wrapper = makeWrapper({ item: await itemOf('fb-1'), filters: { status: 'all', where: 'preview:pr-42' } });

    const link = wrapper.find('[data-testid="feedback-open-page"]');
    expect(link.element.tagName).toBe('NLDD-ICON-BUTTON');
    expect(link.attributes('icon')).toBe('arrow-down-left-arrow-up-right');
    // The label is the accessible name and the tooltip of the icon.
    expect(link.attributes('text')).toBe('Openen als pagina');
    expect(link.attributes('href')).toBe('/team-aurora/website/feedback/fb-1?status=all&target=preview&ref=pr-42');
    expect(link.attributes('target')).toBeUndefined();
  });

  it('sits in the title bar, before the close button at the far end', async () => {
    const wrapper = makeWrapper({ item: await itemOf('fb-1') });

    const group = wrapper.find('nldd-title > nldd-button-group[slot="end"]');
    expect(group.exists()).toBe(true);
    expect(group.findAll(':scope > *').map((button) => button.attributes('data-testid'))).toEqual([
      'feedback-open-page',
      'feedback-sheet-close',
    ]);
  });

  it('goes there inside the app on a plain click, and leaves a click for a new tab to the browser', async () => {
    const router = feedbackRouter();
    const wrapper = makeWrapper({ item: await itemOf('fb-2') }, router);
    await untilIdle();
    const link = wrapper.find('[data-testid="feedback-open-page"]').element;

    const newTab = new MouseEvent('click', { button: 0, ctrlKey: true, cancelable: true });
    link.dispatchEvent(newTab);
    await untilIdle();
    expect(newTab.defaultPrevented).toBe(false);
    expect(router.currentRoute.value.path).toBe('/team-aurora/website/feedback');

    link.dispatchEvent(new MouseEvent('click', { button: 0, cancelable: true }));
    await untilIdle();
    expect(router.currentRoute.value.fullPath).toBe('/team-aurora/website/feedback/fb-2');
  });
});

describe('FeedbackSheet: what was sent', () => {
  it('names the page and the date, and says it is loading until the item is there', async () => {
    const item = await itemOf('fb-1');
    const wrapper = makeWrapper({ item });

    const sheet = wrapper.find('[data-testid="feedback-sheet"]');
    expect(sheet.attributes('accessible-label')).toBe(
      `Feedback op Documentatie, ${formatTimestamp(item.createdAt)}`,
    );
    expect(sheet.find('h2').text()).toBe('Feedback op Documentatie');
    // Wide enough for a screenshot to be read beside the list.
    expect(sheet.attributes('width')).toBe('640px');
    expect(wrapper.find('[data-testid="feedback-sheet-loading"]').attributes('variant')).toBe('loading');

    await untilIdle();
    expect(wrapper.find('[data-testid="feedback-sheet-loading"]').exists()).toBe(false);
  });

  it('shows the comment with its line breaks, and the quote as written', async () => {
    const wrapper = await opened('fb-1');

    // The blocks of the comment are the rich text's own: no rich text inside it.
    const comment = wrapper.find('[data-testid="feedback-comment"] > p');
    expect(comment.text()).toBe('Klopt dit bedrag?In de begroting staat 2,6 miljoen.');
    expect(comment.findAll('br')).toHaveLength(1);
    expect(wrapper.find('[data-testid="feedback-comment"] nldd-rich-text').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-comment"]').text()).toContain('Over deze tekst');
    expect(wrapper.find('[data-testid="feedback-quote"]').text()).toBe('2,4 miljoen');
  });

  it('opens the place on the page in a new tab', async () => {
    const wrapper = await opened('fb-1');

    const view = wrapper.find('[data-testid="feedback-view"]');
    expect(view.attributes('text')).toBe('Bekijk op de pagina');
    expect(view.attributes('href')).toBe(
      'https://sites.toon.test/team-aurora/website/_preview/pr-42/docs/#toon-feedback=fb-1',
    );
    expect(view.attributes('target')).toBe('_blank');
  });

  it('says the place cannot be viewed once its preview is gone or expired, instead of a way to "Niet gevonden"', async () => {
    const expired = () => (backend.data.previews[0]!.expiresAt = new Date(Date.now() - 60_000).toISOString());
    for (const end of [previewGone, expired]) {
      backend = makeMockBackend();
      vi.stubGlobal('fetch', backend.fetch);
      end();
      const wrapper = await opened('fb-1');

      expect(wrapper.find('[data-testid="feedback-view"]').exists()).toBe(false);
      expect(wrapper.find('[data-testid="feedback-view-gone"]').text()).toBe(
        'Preview pr-42 is verlopen of bestaat niet meer, dus deze plek is niet meer te bekijken.',
      );
      wrapper.unmount();
    }
  });

  it('keeps the way to the live site once the previews are gone', async () => {
    previewGone();
    const live = await opened('fb-2');
    expect(live.find('[data-testid="feedback-view"]').attributes('href')).toBe(
      'https://sites.toon.test/team-aurora/website/#toon-feedback=fb-2',
    );
    expect(live.find('[data-testid="feedback-view-gone"]').exists()).toBe(false);
  });

  it('marks nothing on a page that the settings leave without the button, and says so beside the way to it', async () => {
    const off = await opened('fb-1', { enabled: false });
    // The page is still worth opening: the button stays, with the place in it.
    expect(off.find('[data-testid="feedback-view"]').attributes('href')).toBe(
      'https://sites.toon.test/team-aurora/website/_preview/pr-42/docs/#toon-feedback=fb-1',
    );
    expect(off.find('[data-testid="feedback-view-unmarked"]').text()).toBe(
      'Feedback staat uit voor deze site, dus de pagina opent zonder deze plek te markeren.',
    );
    off.unmount();

    const liveOff = await opened('fb-2', { live: false });
    expect(liveOff.find('[data-testid="feedback-view"]').exists()).toBe(true);
    expect(liveOff.find('[data-testid="feedback-view-unmarked"]').text()).toBe(
      'Feedback staat uit op de live site, dus de pagina opent zonder deze plek te markeren.',
    );
  });

  it('says nothing more where the page still gets the button, or where there is no place to mark', async () => {
    // A preview gets the button whatever the live site does.
    const preview = await opened('fb-1', { live: false });
    expect(preview.find('[data-testid="feedback-view-unmarked"]').exists()).toBe(false);
    preview.unmount();

    const live = await opened('fb-2');
    expect(live.find('[data-testid="feedback-view-unmarked"]').exists()).toBe(false);
    live.unmount();

    // Feedback on the whole page only opens the page anyway.
    const page = await opened('fb-3', { enabled: false });
    expect(page.find('[data-testid="feedback-view"]').exists()).toBe(true);
    expect(page.find('[data-testid="feedback-view-unmarked"]').exists()).toBe(false);
    page.unmount();

    // Once the preview is gone, that is what the line says.
    previewGone();
    const gone = await opened('fb-1', { enabled: false });
    expect(gone.find('[data-testid="feedback-view-gone"]').exists()).toBe(true);
    expect(gone.find('[data-testid="feedback-view-unmarked"]').exists()).toBe(false);
  });

  it('says in English why the page will not mark the place', async () => {
    _setLocaleForTest('en');
    const off = await opened('fb-1', { enabled: false });
    expect(off.find('[data-testid="feedback-view-unmarked"]').text()).toBe(
      'Feedback is off for this site, so the page opens without marking this place.',
    );
    off.unmount();

    const liveOff = await opened('fb-2', { live: false });
    expect(liveOff.find('[data-testid="feedback-view-unmarked"]').text()).toBe(
      'Feedback is off on the live site, so the page opens without marking this place.',
    );
  });

  it('opens the page itself for feedback on the whole page, which has no place on it', async () => {
    const wrapper = await opened('fb-3');

    expect(wrapper.find('[data-testid="feedback-view"]').attributes('href')).toBe(
      'https://sites.toon.test/team-aurora/website/_preview/pr-42/contact/',
    );
    expect(wrapper.find('[data-testid="feedback-quote"]').exists()).toBe(false);
  });

  it('shows the screenshot from this origin, with a text alternative and a link to the full size', async () => {
    const wrapper = await opened('fb-1');

    const url = '/-/api/v1/sites/team-aurora/website/feedback/fb-1/screenshot';
    const image = wrapper.find('[data-testid="feedback-screenshot"] nldd-image');
    expect(image.attributes('src')).toBe(url);
    expect(image.attributes('alt')).toBe('Schermafbeelding bij deze feedback');
    const open = wrapper.find('[data-testid="feedback-screenshot-open"]');
    expect(open.attributes('href')).toBe(url);
    expect(open.attributes('target')).toBe('_blank');
    expect(open.text()).toBe('Schermafbeelding op ware grootte');
  });

  it('has no screenshot block for an item without one', async () => {
    const wrapper = await opened('fb-2');

    expect(wrapper.find('[data-testid="feedback-screenshot"]').exists()).toBe(false);
  });

  it('puts what the sender wrote in as text, with its Markdown and never its HTML', async () => {
    backend.data.feedback.find((item) => item.id === 'fb-1')!.comment =
      '<img src=x onerror=alert(1)> **vet**\n\n- [hier](https://x.nl/a) [x](javascript:alert(1))';
    const wrapper = await opened('fb-1');

    const comment = wrapper.find('[data-testid="feedback-comment"]');
    expect(comment.find('img').exists()).toBe(false);
    expect(comment.text()).toContain('<img src=x onerror=alert(1)> vet');
    expect(comment.find('strong').text()).toBe('vet');
    expect(comment.find('li').text()).toBe('hier (https://x.nl/a) [x](javascript:alert(1))');
    const links = comment.findAll('a');
    expect(links).toHaveLength(1);
    expect([links[0]!.text(), links[0]!.attributes('href'), links[0]!.attributes('target')]).toEqual([
      'https://x.nl/a',
      'https://x.nl/a',
      '_blank',
    ]);
  });
});

describe('FeedbackSheet: the details', () => {
  it('lists the status, the kind, the sender, the date, the page, the place, the version and the screen', async () => {
    const wrapper = await opened('fb-1');

    expect(meta(wrapper, 'status').find('nldd-text-cell').attributes('text')).toBe('**Status**');
    expect(metaValue(wrapper, 'status')).toBe('Nieuw');
    expect(metaValue(wrapper, 'kind')).toBe('Tekst');
    expect(metaValue(wrapper, 'author')).toContain('Jan');
    expect(meta(wrapper, 'author').find('nldd-tag').attributes('text')).toBe('Naam niet gecontroleerd');
    expect(metaValue(wrapper, 'created')).toBe(formatTimestamp('2026-07-18T09:15:00.000Z'));
    expect(metaValue(wrapper, 'page')).toBe('/docs/');
    expect(metaValue(wrapper, 'target')).toBe('Preview pr-42');
    expect(metaValue(wrapper, 'version')).toBe(formatTimestamp('2026-07-17T14:32:00.000Z'));
    // The size of the browser window, not of the screen.
    expect(meta(wrapper, 'viewport').find('nldd-text-cell').attributes('text')).toBe('**Venstergrootte**');
    expect(metaValue(wrapper, 'viewport')).toBe('1280 bij 800 pixels');
  });

  it('says nothing more about a version that is the one shown now', async () => {
    const wrapper = await opened('fb-1');

    expect(wrapper.find('[data-testid="feedback-meta-version-note"]').exists()).toBe(false);
  });

  it('says an item is on an earlier version, what the live site shows now, and opens the one it was given on', async () => {
    backend.data.feedback.find((candidate) => candidate.id === 'fb-2')!.versionId = 'versie-0';
    backend.data.feedback.find((candidate) => candidate.id === 'fb-2')!.page.path = 'over ons/?.html';
    const wrapper = await opened('fb-2');

    expect(metaValue(wrapper, 'version')).toBe(formatTimestamp('2026-07-10T09:00:00.000Z'));
    const note = wrapper.find('[data-testid="feedback-meta-version-note"]');
    expect(note.attributes('slot')).toBe('supporting-text');
    expect(note.text()).toBe(
      `Eerdere versie. De live site laat nu de versie van ${formatTimestamp('2026-07-17T14:32:00.000Z')} zien. Bekijk deze versie`,
    );
    const link = wrapper.find('[data-testid="feedback-meta-version-link"]');
    expect(link.attributes('href')).toBe(`${MOCK_CONTENT_BASE}/team-aurora/website/_version/versie-0/over%20ons/%3F.html`);
    expect(link.attributes('target')).toBe('_blank');
  });

  it('says which version a preview shows now, with no way to one that is gone', async () => {
    const wrapper = await opened('fb-3');

    expect(metaValue(wrapper, 'version')).toBe('Niet meer beschikbaar');
    expect(wrapper.find('[data-testid="feedback-meta-version-note"]').text()).toBe(
      `Eerdere versie. Preview pr-42 laat nu de versie van ${formatTimestamp('2026-07-17T14:32:00.000Z')} zien.`,
    );
    expect(wrapper.find('[data-testid="feedback-meta-version-link"]').exists()).toBe(false);
  });

  it('names an area as one', async () => {
    const item = backend.data.feedback.find((candidate) => candidate.id === 'fb-2')!;
    item.kind = 'area';
    item.anchor = {
      selectors: [{ type: 'CssSelector', value: 'main > footer' }],
      point: null,
      area: { x: 0, y: 0.25, width: 1, height: 0.5, pageX: 0, pageY: 1300, pageWidth: 390, pageHeight: 120 },
    };
    const wrapper = await opened('fb-2');

    expect(metaValue(wrapper, 'kind')).toBe('Gebied');
  });

  it('gives an editor the address of an account, and says who changed the status', async () => {
    const wrapper = await opened('fb-2');

    expect(metaValue(wrapper, 'author')).toBe('Ada Vermeer, ada@voorbeeld.nl');
    expect(meta(wrapper, 'author').find('nldd-tag').exists()).toBe(false);
    expect(metaValue(wrapper, 'status')).toBe(
      `Gezien, door Bea Heerder op ${formatTimestamp('2026-07-18T07:30:00.000Z')}`,
    );
    expect(metaValue(wrapper, 'version')).toBe(`${formatTimestamp('2026-07-17T14:32:00.000Z')}, nu live`);
    expect(metaValue(wrapper, 'page')).toBe('/');
  });

  it('says a version is gone, and that it is unknown when the versions cannot be read', async () => {
    const gone = await opened('fb-3');
    expect(metaValue(gone, 'version')).toBe('Niet meer beschikbaar');

    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/versions') ? Promise.reject(new TypeError('network down')) : backend.fetch(input, init),
    );
    const unknown = await opened('fb-1');
    expect(metaValue(unknown, 'version')).toBe('Onbekend');
  });
});

describe('FeedbackSheet: the thread', () => {
  it('says there are no replies yet', async () => {
    const wrapper = await opened('fb-1');

    expect(wrapper.find('[data-testid="feedback-thread-empty"]').text()).toBe('Nog geen reacties.');
    expect(wrapper.find('[data-testid="feedback-thread"]').exists()).toBe(false);
  });

  it('shows the replies oldest first, the site team apart from the sender', async () => {
    const wrapper = await opened('fb-2');

    const replies = wrapper.findAll('[data-testid="feedback-thread"] nldd-list-item');
    expect(replies.map((reply) => reply.find('[slot="overline"]').text())).toEqual([
      `Siteteam, Bea Heerder, ${formatTimestamp('2026-07-18T07:30:00.000Z')}`,
      `Indiener, ${formatTimestamp('2026-07-18T08:10:00.000Z')}`,
    ]);
    expect(replies[1]!.find('nldd-rich-text').text()).toBe('Een iPhone 13 mini.');
  });
});

describe('FeedbackSheet: by role', () => {
  it('gives an admin the reply, the status buttons that change something, and delete', async () => {
    const wrapper = await opened('fb-1');

    expect(wrapper.find('[data-testid="feedback-reply-form"]').exists()).toBe(true);
    const actions = wrapper.findAll('[data-testid="feedback-status-actions"] nldd-button');
    expect(actions.map((action) => action.attributes('text'))).toEqual([
      'Markeer als gezien',
      'Markeer als afgehandeld',
    ]);
    expect(wrapper.find('[data-testid="feedback-delete"]').attributes('text')).toBe('Verwijderen');
    expect(wrapper.find('[data-testid="feedback-read-only"]').exists()).toBe(false);
  });

  it('offers only what moves a seen item or a resolved one on', async () => {
    const seen = await opened('fb-2');
    expect(seen.findAll('[data-testid="feedback-status-actions"] nldd-button').map((b) => b.attributes('text'))).toEqual([
      'Markeer als afgehandeld',
    ]);

    const resolved = await opened('fb-3');
    expect(
      resolved.findAll('[data-testid="feedback-status-actions"] nldd-button').map((b) => b.attributes('text')),
    ).toEqual(['Heropenen']);
  });

  it('gives an editor everything but delete', async () => {
    const wrapper = await opened('fb-1', { canAdmin: false });

    expect(wrapper.find('[data-testid="feedback-reply-form"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-delete"]').exists()).toBe(false);
  });

  it('tells a reader who can reply or change the status, and offers neither', async () => {
    const wrapper = await opened('fb-1', { canEdit: false, canAdmin: false });

    expect(wrapper.find('[data-testid="feedback-read-only"]').text()).toBe(
      'Alleen redacteuren en beheerders kunnen reageren of de status wijzigen.',
    );
    expect(wrapper.find('[data-testid="feedback-reply-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-status-actions"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-delete"]').exists()).toBe(false);
  });
});

describe('FeedbackSheet: replying', () => {
  it('tells the team whether the sender can read a reply at all', async () => {
    const named = await opened('fb-1');
    expect(named.find('[data-testid="feedback-reply-form"] nldd-form-field-help-text').text()).toBe(
      'Deze feedback kwam zonder account binnen, dus de indiener ziet je reactie niet.',
    );

    const account = await opened('fb-2');
    expect(account.find('[data-testid="feedback-reply-form"] nldd-form-field-help-text').text()).toBe(
      'De indiener ziet je reactie, met je naam, in de feedbackknop onder Mijn feedback.',
    );
  });

  it('posts a reply, puts it in the thread and empties the field', async () => {
    const wrapper = await opened('fb-1');
    const field = wrapper.find('[data-testid="feedback-reply"]');
    expect(field.attributes('maxlength')).toBe('5000');

    type(wrapper, '  Dank je, we passen het aan.  ');
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await untilIdle();

    const thread = wrapper.findAll('[data-testid="feedback-thread"] nldd-list-item');
    expect(thread).toHaveLength(1);
    expect(thread[0]!.find('nldd-rich-text').text()).toBe('Dank je, we passen het aan.');
    expect(thread[0]!.find('[slot="overline"]').text()).toMatch(/^Siteteam, Bea Heerder, /);
    expect(wrapper.find('[data-testid="feedback-reply"]').attributes('value')).toBe('');
    expect(wrapper.find('nldd-notification[variant="success"]').attributes('text')).toBe('Reactie geplaatst');
    expect(wrapper.emitted('replied')).toEqual([['fb-1']]);
  });

  it('sends nothing without a reply, and marks the field', async () => {
    const posted = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', posted);
    const wrapper = await opened('fb-1');
    posted.mockClear();

    type(wrapper, '   ');
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await untilIdle();

    expect(posted).not.toHaveBeenCalled();
    const field = wrapper.find('[data-testid="feedback-reply"]');
    expect(field.attributes('invalid')).toBeDefined();
    // Named outright: the list's own required check passes on spaces, and
    // would then show its generic fallback item instead of this one.
    expect(field.attributes('unmet')).toBe('feedback-reply-required');
    expect(wrapper.find('nldd-validation-item#feedback-reply-required').text()).toBe('Een reactie is nodig');

    type(wrapper, 'Toch wel');
    await untilIdle();
    expect(wrapper.find('[data-testid="feedback-reply"]').attributes('invalid')).toBeUndefined();
    expect(wrapper.find('[data-testid="feedback-reply"]').attributes('unmet')).toBeUndefined();
  });

  it('sends a reply without the format characters the API refuses, and keeps the joiners an emoji or a word needs', async () => {
    const posted = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', posted);
    const wrapper = await opened('fb-1');
    posted.mockClear();

    // A direction mark, soft hyphens, a zero-width space, a word joiner, a
    // bidi override, a tag character and a byte order mark, around an emoji
    // joined with U+200D and a Persian word written with U+200C.
    type(
      wrapper,
      '\u200EOpgelost, dank je \u{1F9D1}\u200D\u{1F4BB}! De zorg\u00ADverzekerings\u00ADwet staat er\u200B nu goed\u2060 in. \u202E\u0645\u06CC\u200C\u062E\u0648\u0627\u0647\u0645\u{E0041}\uFEFF',
    );
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await untilIdle();

    const body =
      'Opgelost, dank je \u{1F9D1}\u200D\u{1F4BB}! De zorgverzekeringswet staat er nu goed in. \u0645\u06CC\u200C\u062E\u0648\u0627\u0647\u0645';
    expect(posted).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(posted.mock.calls[0]![1]!.body))).toEqual({ body });
    expect(wrapper.findAll('[data-testid="feedback-thread"] nldd-rich-text').at(-1)!.text()).toBe(body);
    expect(wrapper.find('[data-testid="feedback-reply"]').attributes('value')).toBe('');
  });

  it('counts a reply of nothing but format characters as empty', async () => {
    const posted = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', posted);
    const wrapper = await opened('fb-1');
    posted.mockClear();

    type(wrapper, ' \u00AD\u200B\u2060\uFEFF ');
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await untilIdle();

    expect(posted).not.toHaveBeenCalled();
    expect(wrapper.find('[data-testid="feedback-reply"]').attributes('invalid')).toBeDefined();
  });

  it('reads the value off the field when the event carries none', async () => {
    const wrapper = await opened('fb-1');
    const field = wrapper.find('[data-testid="feedback-reply"]').element as HTMLElement & { value: string };

    field.value = 'Van het veld zelf';
    field.dispatchEvent(new Event('input'));
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.feedback.find((item) => item.id === 'fb-1')!.replies.at(-1)!.body).toBe(
      'Van het veld zelf',
    );
  });

  it('keeps the text and says why at the field when the reply is refused', async () => {
    const wrapper = await opened('fb-1');
    vi.stubGlobal(
      'fetch',
      problemFetch(422, { title: 'Onverwerkbare invoer', detail: "Veld 'body' mag hoogstens 5000 tekens lang zijn." }),
    );

    type(wrapper, 'Een reactie');
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await untilIdle();

    const field = wrapper.find('[data-testid="feedback-reply"]');
    expect(field.attributes('value')).toBe('Een reactie');
    expect(field.attributes('invalid')).toBeDefined();
    expect(field.attributes('unmet')).toBe('feedback-reply-server');
    expect(wrapper.find('nldd-validation-item#feedback-reply-server').text()).toBe(
      "Veld 'body' mag hoogstens 5000 tekens lang zijn.",
    );
    // Said as well, inside the sheet: the validation list is no live region.
    const alert = wrapper.find('nldd-sheet [data-testid="feedback-reply-alert"]');
    expect(alert.attributes('role')).toBe('alert');
    expect(alert.element.localName).toBe('nldd-visually-hidden');
    expect(alert.text()).toBe("Veld 'body' mag hoogstens 5000 tekens lang zijn.");
    expect(wrapper.emitted('replied')).toBeUndefined();

    // Typing on takes the refusal away, so the next one is said again.
    type(wrapper, 'Een kortere reactie');
    await untilIdle();
    expect(wrapper.find('[data-testid="feedback-reply-alert"]').exists()).toBe(false);
  });

  it('says it did not work when the network fails', async () => {
    const wrapper = await opened('fb-1');
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));

    type(wrapper, 'Een reactie');
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await untilIdle();

    expect(wrapper.find('nldd-validation-item#feedback-reply-server').text()).toBe(
      'Reageren lukte niet. Probeer het opnieuw.',
    );
    expect(wrapper.find('nldd-sheet [role="alert"]').text()).toBe('Reageren lukte niet. Probeer het opnieuw.');
    expect(wrapper.find('[data-testid="feedback-reply-submit"]').attributes('loading')).toBeUndefined();
  });

  it('says nothing aloud while no reply was refused', async () => {
    const wrapper = await opened('fb-1');

    expect(wrapper.find('[data-testid="feedback-reply-alert"]').exists()).toBe(false);
    type(wrapper, 'Dank je');
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await untilIdle();
    expect(wrapper.find('[data-testid="feedback-reply-alert"]').exists()).toBe(false);
  });

  it('sends a reply once, with its field read-only until the answer, also when the button is pressed again', async () => {
    const wrapper = await opened('fb-1');
    const held = holding((url, method) => method === 'POST' && url.endsWith('/feedback/fb-1/replies'));

    type(wrapper, 'Een keer');
    const form = wrapper.find('[data-testid="feedback-reply-form"]');
    await form.trigger('submit');
    expect(wrapper.find('[data-testid="feedback-reply-submit"]').attributes('loading')).toBeDefined();
    expect(field(wrapper).attributes('readonly')).toBeDefined();
    await form.trigger('submit');
    held[0]!.release();
    await untilIdle();

    expect(backend.data.feedback.find((item) => item.id === 'fb-1')!.replies).toHaveLength(1);
    expect(field(wrapper).attributes('readonly')).toBeUndefined();
    expect(field(wrapper).attributes('value')).toBe('');
  });
});

describe('FeedbackSheet: the status', () => {
  it('marks an item resolved and says so', async () => {
    const wrapper = await opened('fb-1');

    await wrapper.find('[data-testid="feedback-mark-resolved"]').trigger('click');
    await untilIdle();

    expect(backend.data.feedback.find((item) => item.id === 'fb-1')!.status).toBe('resolved');
    expect(wrapper.find('nldd-notification[variant="success"]').attributes('text')).toBe(
      'Gemarkeerd als afgehandeld',
    );
    expect(wrapper.emitted('updated')![0]![0]).toMatchObject({ id: 'fb-1', status: 'resolved' });
    expect(metaValue(wrapper, 'status')).toMatch(/^Afgehandeld, door Bea Heerder op /);
    expect(wrapper.find('[data-testid="feedback-mark-new"]').attributes('text')).toBe('Heropenen');
  });

  it('moves the focus on to what can be done next, as the pressed button goes', async () => {
    // A real teleport: the stub builds its content anew on every render, which
    // would leave the focus on an element that is no longer there.
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');
    const wrapper = mount(FeedbackSheet, {
      props: sheetProps({ item: await itemOf('fb-1') }),
      attachTo: document.body,
      global: { plugins: [feedbackRouter()] },
    });
    await untilIdle();

    document.querySelector<HTMLElement>('[data-testid="feedback-mark-resolved"]')!.click();
    await untilIdle();

    const reopen = document.querySelector('[data-testid="feedback-mark-new"]');
    expect(reopen).not.toBeNull();
    expect(focus.mock.contexts.at(-1)).toBe(reopen);
    wrapper.unmount();
  });

  it('marks an item seen and reopens one', async () => {
    const fresh = await opened('fb-1');
    await fresh.find('[data-testid="feedback-mark-seen"]').trigger('click');
    await untilIdle();
    expect(fresh.find('nldd-notification[variant="success"]').attributes('text')).toBe('Gemarkeerd als gezien');

    const resolved = await opened('fb-3');
    await resolved.find('[data-testid="feedback-mark-new"]').trigger('click');
    await untilIdle();
    expect(resolved.find('nldd-notification[variant="success"]').attributes('text')).toBe('Feedback heropend');
    expect(backend.data.feedback.find((item) => item.id === 'fb-3')!.status).toBe('new');
  });

  it('changes nothing and says why when the change is refused', async () => {
    const wrapper = await opened('fb-1');
    vi.stubGlobal('fetch', problemFetch(403, { title: 'Geen toegang', detail: 'Hiervoor heb je minimaal de rol redacteur nodig.' }));

    await wrapper.find('[data-testid="feedback-mark-seen"]').trigger('click');
    await untilIdle();

    const notice = wrapper.find('nldd-notification[variant="critical"]');
    expect(notice.attributes('text')).toBe('Status niet gewijzigd');
    expect(notice.attributes('supporting-text')).toBe('Hiervoor heb je minimaal de rol redacteur nodig.');
    expect(wrapper.find('[data-testid="feedback-mark-seen"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-mark-seen"]').attributes('loading')).toBeUndefined();
    expect(wrapper.emitted('updated')).toBeUndefined();
  });

  it('says it did not work when the network fails', async () => {
    const wrapper = await opened('fb-1');
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));

    await wrapper.find('[data-testid="feedback-mark-seen"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('nldd-notification[variant="critical"]').attributes('supporting-text')).toBe(
      'Opslaan lukte niet. Probeer het opnieuw.',
    );
  });

  it('changes the status once, also when a second button is pressed while the first goes', async () => {
    const calls = vi.fn(backend.fetch);
    const wrapper = await opened('fb-1');
    vi.stubGlobal('fetch', calls);

    await wrapper.find('[data-testid="feedback-mark-seen"]').trigger('click');
    expect(wrapper.find('[data-testid="feedback-mark-seen"]').attributes('loading')).toBeDefined();
    await wrapper.find('[data-testid="feedback-mark-resolved"]').trigger('click');
    await untilIdle();

    expect(calls).toHaveBeenCalledTimes(1);
    expect(backend.data.feedback.find((item) => item.id === 'fb-1')!.status).toBe('seen');
  });
});

describe('FeedbackSheet: deleting', () => {
  it('asks first, then deletes and says the item is gone', async () => {
    const wrapper = await opened('fb-3');

    await wrapper.find('[data-testid="feedback-delete"]').trigger('click');
    await untilIdle();
    const dialog = wrapper.find('nldd-modal-dialog');
    expect(dialog.attributes('text')).toBe('Deze feedback verwijderen?');
    expect(dialog.attributes('supporting-text')).toContain('met de reacties en de schermafbeelding');
    expect(wrapper.find('[data-testid="confirm-cancel"]').attributes('text')).toBe('Behouden');

    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    expect(backend.data.feedback.map((item) => item.id)).not.toContain('fb-3');
    expect(wrapper.emitted('removed')).toEqual([['fb-3']]);
  });

  it('keeps the item when the answer is to keep it', async () => {
    const wrapper = await opened('fb-3');

    await wrapper.find('[data-testid="feedback-delete"]').trigger('click');
    await wrapper.find('[data-testid="confirm-cancel"]').trigger('click');
    await untilIdle();

    expect(backend.data.feedback.map((item) => item.id)).toContain('fb-3');
    expect(wrapper.emitted('removed')).toBeUndefined();
  });

  it('says why when deleting is refused, and when the network fails', async () => {
    const wrapper = await opened('fb-3');
    vi.stubGlobal('fetch', problemFetch(403, { title: 'Geen toegang', detail: 'Alleen een beheerder.' }));

    await wrapper.find('[data-testid="feedback-delete"]').trigger('click');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    const notice = wrapper.find('nldd-notification[variant="critical"]');
    expect(notice.attributes('text')).toBe('Feedback niet verwijderd');
    expect(notice.attributes('supporting-text')).toBe('Alleen een beheerder.');

    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));
    await wrapper.find('[data-testid="feedback-delete"]').trigger('click');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    expect(wrapper.findAll('nldd-notification[variant="critical"]').at(-1)!.attributes('supporting-text')).toBe(
      'Verwijderen lukte niet. Probeer het opnieuw.',
    );
    expect(wrapper.emitted('removed')).toBeUndefined();
  });
});

describe('FeedbackSheet: loading and closing', () => {
  it('shows why an item cannot be loaded', async () => {
    const item = await itemOf('fb-1');
    backend.data.feedback = backend.data.feedback.filter((row) => row.id !== 'fb-1');

    const wrapper = makeWrapper({ item });
    await untilIdle();

    const banner = wrapper.find('[data-testid="feedback-sheet"] nldd-banner');
    expect(banner.attributes('variant')).toBe('critical');
    expect(banner.attributes('supporting-text')).toBe('Onbekende feedback.');
    expect(wrapper.find('[data-testid="feedback-sheet-loading"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-comment"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-reply-form"]').exists()).toBe(false);
  });

  it('shows only the item opened last when an earlier one answers late', async () => {
    const first = await itemOf('fb-1');
    const second = await itemOf('fb-2');
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/feedback/fb-1')) await gate;
      return backend.fetch(input, init);
    });

    const wrapper = makeWrapper({ item: first });
    await wrapper.setProps({ item: null });
    await wrapper.setProps({ item: second });
    await untilIdle();
    release();
    await untilIdle();

    expect(wrapper.find('h2').text()).toBe('Feedback op Team Aurora');
    expect(wrapper.find('[data-testid="feedback-comment"]').text()).toContain('De knop onderaan');
  });

  it('starts every item with an empty reply field', async () => {
    const wrapper = await opened('fb-1');
    fireDetailEvent(wrapper.find('[data-testid="feedback-reply"]').element, 'input', { value: 'Half af' });
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    vi.stubGlobal('fetch', backend.fetch);

    await wrapper.setProps({ item: null });
    await wrapper.setProps({ item: await itemOf('fb-2') });
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-reply"]').attributes('value')).toBe('');
  });

  it('says it is closing from its own button and from the sheet itself', async () => {
    const wrapper = await opened('fb-1');

    await wrapper.find('[data-testid="feedback-sheet-close"]').trigger('click');
    wrapper.find('[data-testid="feedback-sheet"]').element.dispatchEvent(new Event('close'));

    expect(wrapper.emitted('close')).toHaveLength(2);
  });

  it('keeps showing the item while the sheet closes', async () => {
    const wrapper = await opened('fb-1');

    await wrapper.setProps({ item: null });

    expect(wrapper.find('h2').text()).toBe('Feedback op Documentatie');
  });
});

/** A request held until the test lets it go through, or fail. */
interface Held {
  url: string;
  release: () => void;
  fail: () => void;
}

/** Holds every request `matches` picks. The rest go straight to the mock backend. */
function holding(matches: (url: string, method: string) => boolean): Held[] {
  const held: Held[] = [];
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (!matches(url, init?.method ?? 'GET')) return backend.fetch(input, init);
    return new Promise<Response>((resolve, reject) => {
      held.push({
        url,
        release: () => resolve(backend.fetch(input, init)),
        fail: () => reject(new TypeError('network down')),
      });
    });
  });
  return held;
}

describe('FeedbackSheet: an answer that comes back after another item was opened', () => {
  it('leaves the item opened since alone when a status change of the one before comes back', async () => {
    const wrapper = makeWrapper({ item: await itemOf('fb-1') });
    await untilIdle();
    const held = holding((url, method) => method === 'PUT' && url.endsWith('/feedback/fb-1/status'));

    await wrapper.find('[data-testid="feedback-mark-resolved"]').trigger('click');
    await switchTo(wrapper, 'fb-2');
    // Not held up by the change of the item before.
    expect(wrapper.find('[data-testid="feedback-mark-resolved"]').attributes('loading')).toBeUndefined();
    held[0]!.release();
    await untilIdle();

    expect(wrapper.find('h2').text()).toBe('Feedback op Team Aurora');
    expect(wrapper.find('[data-testid="feedback-comment"]').text()).toContain('De knop onderaan');
    expect(metaValue(wrapper, 'status')).toMatch(/^Gezien, door Bea Heerder op /);
    expect(wrapper.find('[data-testid="feedback-mark-resolved"]').exists()).toBe(true);
    // The list still hears about it, and nothing claims it for the item on screen.
    expect(wrapper.emitted('updated')![0]![0]).toMatchObject({ id: 'fb-1', status: 'resolved' });
    expect(wrapper.find('nldd-notification[variant="success"]').exists()).toBe(false);
    expect(backend.data.feedback.find((item) => item.id === 'fb-1')!.status).toBe('resolved');
  });

  it('lets the item opened since change its status once, while the change before comes back', async () => {
    const wrapper = makeWrapper({ item: await itemOf('fb-1') });
    await untilIdle();
    const held = holding((url, method) => method === 'PUT' && url.endsWith('/status'));

    await wrapper.find('[data-testid="feedback-mark-resolved"]').trigger('click');
    await switchTo(wrapper, 'fb-2');
    await wrapper.find('[data-testid="feedback-mark-resolved"]').trigger('click');
    held[0]!.release();
    await untilIdle();

    // The answer for the item before leaves this item's change going.
    expect(wrapper.find('[data-testid="feedback-mark-resolved"]').attributes('loading')).toBeDefined();
    await wrapper.find('[data-testid="feedback-mark-resolved"]').trigger('click');
    expect(held.map((request) => request.url.split('/feedback/')[1])).toEqual(['fb-1/status', 'fb-2/status']);

    held[1]!.release();
    await untilIdle();
    expect(metaValue(wrapper, 'status')).toMatch(/^Afgehandeld, door Bea Heerder op /);
    expect(wrapper.find('nldd-notification[variant="success"]').attributes('text')).toBe('Gemarkeerd als afgehandeld');
  });

  it('keeps the draft of the item opened since when a reply to the one before comes back', async () => {
    const wrapper = makeWrapper({ item: await itemOf('fb-1') });
    await untilIdle();
    const held = holding((url, method) => method === 'POST' && url.endsWith('/feedback/fb-1/replies'));

    type(wrapper, 'Antwoord voor A');
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await switchTo(wrapper, 'fb-2');
    type(wrapper, 'Concept voor B');
    await untilIdle();
    expect(wrapper.find('[data-testid="feedback-reply-submit"]').attributes('loading')).toBeUndefined();
    held[0]!.release();
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-reply"]').attributes('value')).toBe('Concept voor B');
    expect(wrapper.findAll('[data-testid="feedback-thread"] nldd-list-item')).toHaveLength(2);
    expect(wrapper.find('nldd-notification[variant="success"]').exists()).toBe(false);
    expect(wrapper.emitted('replied')).toEqual([['fb-1']]);
    expect(backend.data.feedback.find((item) => item.id === 'fb-1')!.replies.map((reply) => reply.body)).toEqual([
      'Antwoord voor A',
    ]);

    // And the reply typed for this item goes to this item.
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await untilIdle();
    expect(backend.data.feedback.find((item) => item.id === 'fb-2')!.replies.at(-1)!.body).toBe('Concept voor B');
    expect(wrapper.find('[data-testid="feedback-reply"]').attributes('value')).toBe('');
  });

  it('sends the reply of the item opened since once, while the reply before comes back', async () => {
    const wrapper = makeWrapper({ item: await itemOf('fb-1') });
    await untilIdle();
    const held = holding((url, method) => method === 'POST' && url.endsWith('/replies'));

    type(wrapper, 'Antwoord voor A');
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await switchTo(wrapper, 'fb-2');
    type(wrapper, 'Antwoord voor B');
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    held[0]!.release();
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-reply-submit"]').attributes('loading')).toBeDefined();
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    expect(held).toHaveLength(2);

    held[1]!.release();
    await untilIdle();
    expect(wrapper.findAll('[data-testid="feedback-thread"] nldd-list-item').at(-1)!.text()).toContain('Antwoord voor B');
    expect(wrapper.find('nldd-notification[variant="success"]').attributes('text')).toBe('Reactie geplaatst');
  });

  it('says by its page that a change to the item before failed, and not at the field of this one', async () => {
    const wrapper = makeWrapper({ item: await itemOf('fb-1') });
    await untilIdle();
    const held = holding((_url, method) => method !== 'GET');

    type(wrapper, 'Antwoord voor A');
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await wrapper.find('[data-testid="feedback-mark-resolved"]').trigger('click');
    await switchTo(wrapper, 'fb-2');
    for (const request of held) request.fail();
    await untilIdle();

    const field = wrapper.find('[data-testid="feedback-reply"]');
    expect(field.attributes('invalid')).toBeUndefined();
    expect(field.attributes('unmet')).toBeUndefined();
    expect(wrapper.find('[data-testid="feedback-reply-alert"]').exists()).toBe(false);
    const notices = wrapper.findAll('nldd-notification[variant="critical"]');
    expect(notices.map((notice) => [notice.attributes('text'), notice.attributes('supporting-text')])).toEqual([
      [
        'Reactie niet geplaatst bij de feedback op Documentatie',
        'Reageren lukte niet. Probeer het opnieuw. Je reactie staat klaar in het reactieveld van die feedback.',
      ],
      ['Status niet gewijzigd bij de feedback op Documentatie', 'Opslaan lukte niet. Probeer het opnieuw.'],
    ]);
    expect(wrapper.emitted('replied')).toBeUndefined();
    expect(wrapper.emitted('updated')).toBeUndefined();
    // Its own buttons are free.
    expect(wrapper.find('[data-testid="feedback-reply-submit"]').attributes('loading')).toBeUndefined();
    expect(wrapper.find('[data-testid="feedback-mark-resolved"]').attributes('loading')).toBeUndefined();
  });
});

describe('FeedbackSheet: a reply refused once the sheet moved on', () => {
  const KEPT = 'Reageren lukte niet. Probeer het opnieuw. Je reactie staat klaar in het reactieveld van die feedback.';

  function critical(wrapper: ReturnType<typeof makeWrapper>): string[][] {
    return wrapper
      .findAll('nldd-notification[variant="critical"]')
      .map((notice) => [notice.attributes('text')!, notice.attributes('supporting-text')!]);
  }

  /** Opens fb-1 and holds every reply sent to it from then on. */
  async function sendingToFirst() {
    const wrapper = makeWrapper({ item: await itemOf('fb-1') });
    await untilIdle();
    const held = holding((url, method) => method === 'POST' && url.endsWith('/feedback/fb-1/replies'));
    return { wrapper, held };
  }

  function repliesOfFirst(): string[] {
    return backend.data.feedback.find((item) => item.id === 'fb-1')!.replies.map((reply) => reply.body);
  }

  it('keeps the text for its item, says so, and puts it back when that item opens again', async () => {
    const { wrapper, held } = await sendingToFirst();
    type(wrapper, 'Antwoord voor A');
    await send(wrapper);
    await switchTo(wrapper, 'fb-2');
    held[0]!.fail();
    await untilIdle();

    expect(critical(wrapper)).toEqual([['Reactie niet geplaatst bij de feedback op Documentatie', KEPT]]);
    // The item on screen keeps its own field, and so does every other item.
    expect(field(wrapper).attributes('value')).toBe('');
    await switchTo(wrapper, 'fb-3');
    expect(field(wrapper).attributes('value')).toBe('');

    await switchTo(wrapper, 'fb-1');
    expect(field(wrapper).attributes('value')).toBe('Antwoord voor A');
    expect(field(wrapper).attributes('invalid')).toBeUndefined();

    // Sent from there it reaches its own item, and does not come back after.
    vi.stubGlobal('fetch', backend.fetch);
    await send(wrapper);
    await untilIdle();
    expect(repliesOfFirst()).toEqual(['Antwoord voor A']);
    await switchTo(wrapper, 'fb-2');
    await switchTo(wrapper, 'fb-1');
    expect(field(wrapper).attributes('value')).toBe('');
  });

  it('keeps the text when the sheet was closed before the refusal came, which no field shows', async () => {
    const { wrapper, held } = await sendingToFirst();
    type(wrapper, 'Antwoord voor A');
    await send(wrapper);
    await wrapper.setProps({ item: null });
    held[0]!.fail();
    await untilIdle();

    expect(critical(wrapper)).toEqual([['Reactie niet geplaatst bij de feedback op Documentatie', KEPT]]);
    expect(wrapper.find('[data-testid="feedback-reply-alert"]').exists()).toBe(false);

    await wrapper.setProps({ item: await itemOf('fb-1') });
    await untilIdle();
    expect(field(wrapper).attributes('value')).toBe('Antwoord voor A');
    expect(field(wrapper).attributes('invalid')).toBeUndefined();

    // Back in the field it is a draft: closed again unsent, it stays.
    await switchTo(wrapper, 'fb-2');
    await switchTo(wrapper, 'fb-1');
    expect(field(wrapper).attributes('value')).toBe('Antwoord voor A');
  });

  it('puts the text in the field at once when its item is open again, before what was typed there since', async () => {
    const { wrapper, held } = await sendingToFirst();
    type(wrapper, 'Antwoord voor A');
    await send(wrapper);
    await switchTo(wrapper, 'fb-1');
    expect(field(wrapper).attributes('value')).toBe('');
    expect(field(wrapper).attributes('readonly')).toBeUndefined();
    type(wrapper, 'Een tweede versie');
    await untilIdle();

    held[0]!.fail();
    await untilIdle();

    expect(field(wrapper).attributes('value')).toBe('Antwoord voor A\n\nEen tweede versie');
    expect(critical(wrapper)).toEqual([['Reactie niet geplaatst bij de feedback op Documentatie', KEPT]]);
    // In the field it is a draft like any other: the next opening has it once.
    vi.stubGlobal('fetch', backend.fetch);
    await switchTo(wrapper, 'fb-2');
    await switchTo(wrapper, 'fb-1');
    expect(field(wrapper).attributes('value')).toBe('Antwoord voor A\n\nEen tweede versie');
  });

  it('puts the text in the field of its item opened again while that is still loading', async () => {
    const wrapper = makeWrapper({ item: await itemOf('fb-1') });
    await untilIdle();
    const held = holding(
      (url, method) =>
        (method === 'POST' && url.endsWith('/feedback/fb-1/replies')) || (method === 'GET' && url.endsWith('/feedback/fb-1')),
    );
    type(wrapper, 'Antwoord voor A');
    await send(wrapper);
    await wrapper.setProps({ item: null });
    await wrapper.setProps({ item: await itemOf('fb-1') });
    await untilIdle();
    expect(wrapper.find('[data-testid="feedback-sheet-loading"]').exists()).toBe(true);

    held[0]!.fail();
    await untilIdle();
    held[1]!.release();
    await untilIdle();

    expect(critical(wrapper)).toEqual([['Reactie niet geplaatst bij de feedback op Documentatie', KEPT]]);
    expect(field(wrapper).attributes('value')).toBe('Antwoord voor A');
  });

  it('keeps both texts when two replies to one item are refused after the sheet moved on', async () => {
    const { wrapper, held } = await sendingToFirst();
    type(wrapper, 'Eerste antwoord');
    await send(wrapper);
    await switchTo(wrapper, 'fb-1');
    type(wrapper, 'Tweede antwoord');
    await send(wrapper);
    await switchTo(wrapper, 'fb-2');

    held[0]!.fail();
    await untilIdle();
    held[1]!.fail();
    await untilIdle();

    expect(critical(wrapper)).toHaveLength(2);
    vi.stubGlobal('fetch', backend.fetch);
    await switchTo(wrapper, 'fb-1');
    // Each in front of what waited there already.
    expect(field(wrapper).attributes('value')).toBe('Tweede antwoord\n\nEerste antwoord');
    expect(repliesOfFirst()).toEqual([]);
  });

  it('says it in English', async () => {
    _setLocaleForTest('en');
    const { wrapper, held } = await sendingToFirst();
    type(wrapper, 'Answer for A');
    await send(wrapper);
    await switchTo(wrapper, 'fb-2');
    held[0]!.fail();
    await untilIdle();
    expect(critical(wrapper)).toEqual([
      [
        'Reply not posted to the feedback on Documentatie',
        'Replying did not work. Try again. Your reply waits in the reply field of that feedback.',
      ],
    ]);
  });
});

describe('FeedbackSheet: a reply typed and not sent', () => {
  function repliesOf(id: string): string[] {
    return backend.data.feedback.find((item) => item.id === id)!.replies.map((reply) => reply.body);
  }

  it('keeps it when the sheet closes, and puts it back when its item opens again', async () => {
    const wrapper = await opened('fb-1');
    type(wrapper, 'Dank je wel, we passen het bedrag aan in de volgende versie.');

    // Escape closes the native dialog, which says so, and the tab closes the sheet.
    wrapper.find('[data-testid="feedback-sheet"]').element.dispatchEvent(new Event('close'));
    expect(wrapper.emitted('close')).toHaveLength(1);
    await switchTo(wrapper, 'fb-1');

    expect(field(wrapper).attributes('value')).toBe('Dank je wel, we passen het bedrag aan in de volgende versie.');
    // A draft, not a refusal: nothing is marked or said.
    expect(field(wrapper).attributes('invalid')).toBeUndefined();
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('keeps one for each item, apart from the others', async () => {
    const wrapper = await opened('fb-1');
    type(wrapper, 'Voor de eerste');
    await switchTo(wrapper, 'fb-2');
    expect(field(wrapper).attributes('value')).toBe('');
    type(wrapper, 'Voor de tweede');
    await switchTo(wrapper, 'fb-3');
    expect(field(wrapper).attributes('value')).toBe('');

    await switchTo(wrapper, 'fb-1');
    expect(field(wrapper).attributes('value')).toBe('Voor de eerste');
    await switchTo(wrapper, 'fb-2');
    expect(field(wrapper).attributes('value')).toBe('Voor de tweede');
  });

  it('keeps nothing once the field is emptied, or once its reply is sent', async () => {
    const wrapper = await opened('fb-1');
    type(wrapper, 'Toch maar niet');
    type(wrapper, '');
    await switchTo(wrapper, 'fb-1');
    expect(field(wrapper).attributes('value')).toBe('');

    type(wrapper, 'Verstuurd');
    await send(wrapper);
    await untilIdle();
    await switchTo(wrapper, 'fb-1');
    expect(field(wrapper).attributes('value')).toBe('');
    expect(repliesOf('fb-1')).toEqual(['Verstuurd']);
  });

  it('keeps a reply refused at the field, once the sheet closes after it', async () => {
    const wrapper = await opened('fb-1');
    vi.stubGlobal(
      'fetch',
      problemFetch(422, { title: 'Onverwerkbare invoer', detail: "Veld 'body' mag hoogstens 5000 tekens lang zijn." }),
    );
    type(wrapper, 'Een reactie');
    await send(wrapper);
    await untilIdle();
    expect(field(wrapper).attributes('unmet')).toBe('feedback-reply-server');

    vi.stubGlobal('fetch', backend.fetch);
    await switchTo(wrapper, 'fb-1');
    expect(field(wrapper).attributes('value')).toBe('Een reactie');
    expect(field(wrapper).attributes('invalid')).toBeUndefined();
  });

  it('does not offer a reply on its way again when its item opens meanwhile', async () => {
    const wrapper = await opened('fb-1');
    const held = holding((url, method) => method === 'POST' && url.endsWith('/feedback/fb-1/replies'));
    type(wrapper, 'Onderweg');
    await send(wrapper);
    await switchTo(wrapper, 'fb-1');
    expect(field(wrapper).attributes('value')).toBe('');

    held[0]!.release();
    await untilIdle();
    expect(field(wrapper).attributes('value')).toBe('');
    await switchTo(wrapper, 'fb-1');
    expect(field(wrapper).attributes('value')).toBe('');
    expect(repliesOf('fb-1')).toEqual(['Onderweg']);
  });
});

describe('FeedbackSheet: in English', () => {
  it('speaks English when the interface does', async () => {
    _setLocaleForTest('en');
    const wrapper = await opened('fb-1');

    expect(wrapper.find('h2').text()).toBe('Feedback on Documentatie');
    expect(wrapper.find('[data-testid="feedback-view"]').attributes('text')).toBe('View on the page');
    expect(wrapper.find('[data-testid="feedback-mark-seen"]').attributes('text')).toBe('Mark as seen');
    expect(meta(wrapper, 'viewport').find('nldd-text-cell').attributes('text')).toBe('**Window size**');
    expect(metaValue(wrapper, 'viewport')).toBe('1280 by 800 pixels');
    expect(wrapper.find('[data-testid="feedback-reply-form"] nldd-form-field-help-text').text()).toBe(
      'This feedback came in without an account, so the submitter does not see your reply.',
    );
  });

  it('says in English that a preview is gone and that the submitter sees the name', async () => {
    _setLocaleForTest('en');
    previewGone();
    const gone = await opened('fb-1');
    expect(gone.find('[data-testid="feedback-view-gone"]').text()).toBe(
      'Preview pr-42 has expired or no longer exists, so this place can no longer be viewed.',
    );

    const account = await opened('fb-2');
    expect(account.find('[data-testid="feedback-reply-form"] nldd-form-field-help-text').text()).toBe(
      'The submitter sees your reply, with your name, in the feedback button under My feedback.',
    );
  });
});

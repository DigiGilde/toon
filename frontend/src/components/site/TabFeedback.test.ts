import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '../../../tests/mock-api';
import ErrorBanner from '@/components/ErrorBanner.vue';
import { _resetCurrentMemberCache } from '@/composables/currentMember';
import { stayOnNextMove } from '@/composables/routeAnnouncer';
import { SITE_GROUP } from '@/composables/siteGroup';
import { _setLocaleForTest } from '@/i18n';

// Watched, not replaced: a choice of filter tells the route announcer it is no
// move to another page.
vi.mock('@/composables/routeAnnouncer', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/composables/routeAnnouncer')>();
  return { ...actual, stayOnNextMove: vi.fn(actual.stayOnNextMove) };
});

import FeedbackInbox from './FeedbackInbox.vue';
import FeedbackSettings from './FeedbackSettings.vue';
import FeedbackSheet from './FeedbackSheet.vue';
import { markDeleted } from './feedbackRoute';
import TabFeedback from './TabFeedback.vue';
import { feedbackRouter, feedbackRouterAt, fireDetailEvent, provideSiteGroup, serverErrorFetch, untilIdle } from './testHelpers';

const DAY_MS = 24 * 60 * 60 * 1000;

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
  _resetCurrentMemberCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  _setLocaleForTest('nl');
});

function makeWrapper(router = feedbackRouter(), provide = provideSiteGroup(backend)) {
  return mount(TabFeedback, {
    props: { group: 'team-aurora', site: 'website', contentBase: MOCK_CONTENT_BASE },
    global: { stubs: { teleport: true, FeedbackNotifications: true }, provide, plugins: [router] },
  });
}

async function loaded() {
  const wrapper = makeWrapper();
  await untilIdle();
  return wrapper;
}

function rowIds(wrapper: ReturnType<typeof makeWrapper>): string[] {
  return wrapper.findAll('[data-testid="feedback-list"] nldd-list-item').map((row) => row.attributes('data-item-id')!);
}

/** `upper` comes before `lower` in the page: the empty list points up to it. */
function expectAbove(upper: Element, lower: Element): void {
  expect(upper.compareDocumentPosition(lower) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
}

/** Chooses a status the way the toggle button group reports it. */
function chooseStatus(wrapper: ReturnType<typeof makeWrapper>, value: string): void {
  fireDetailEvent(wrapper.find('[data-testid="feedback-status"]').element, 'change', { selected: true, value });
}

/** Records the query of every list request. */
function recordListQueries(): string[] {
  const queries: string[] = [];
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'https://toon.test');
    if (url.pathname.endsWith('/feedback')) queries.push(url.search);
    return backend.fetch(input, init);
  });
  return queries;
}

/** The values of the place filter's options. */
function places(wrapper: ReturnType<typeof makeWrapper>): (string | undefined)[] {
  return wrapper.findAll('[data-testid="feedback-where"] option').map((option) => option.attributes('value'));
}

/** Answers every request as the mock does, except the read of the previews, which fails. */
function stubUnreadablePreviews(): void {
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
    String(input).endsWith('/previews') ? Promise.reject(new TypeError('network down')) : backend.fetch(input, init),
  );
}

/** Opens the sheet of an item by its row. */
async function openItem(wrapper: ReturnType<typeof makeWrapper>, id: string): Promise<void> {
  await wrapper.find(`[data-testid="feedback-${id}"]`).trigger('click');
  await untilIdle();
}

/** Signs in as Sanne, who has only the given role, on this site alone. */
function signInWithSiteRole(role: 'reader' | 'editor'): void {
  backend.data.siteRoles.push({ groupSlug: 'team-aurora', siteSlug: 'website', identifier: 'sanne@voorbeeld.nl', role });
  backend.data.loggedInMemberId = 'lid-6';
}

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

/** Answers the list requests that `failing` picks with a 503 while `on`, the rest as the mock does. */
function failingList(failing: (query: string) => boolean): { on: boolean } {
  const control = { on: true };
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'https://toon.test');
    if (control.on && url.pathname.endsWith('/feedback') && failing(url.search)) {
      return Promise.resolve(
        new Response(
          JSON.stringify({ type: 'about:blank', title: 'Serverfout', status: 503, detail: 'Even niet beschikbaar.' }),
          { status: 503, headers: { 'content-type': 'application/problem+json' } },
        ),
      );
    }
    return backend.fetch(input, init);
  });
  return control;
}

/** The preview the mock site has, open until `expiresAt`. */
function previewUntil(expiresAt: string | null): void {
  // The API sends null for a preview that never expires, which the type has no room for.
  backend.data.previews[0]!.expiresAt = expiresAt as string;
}

/** Many more open items than one page holds, the newest first. */
function addItems(count: number): void {
  for (let n = 0; n < count; n += 1) {
    backend.data.feedback.push({
      ...backend.data.feedback.find((item) => item.id === 'fb-2')!,
      id: `extra-${String(n).padStart(3, '0')}`,
      createdAt: new Date(Date.parse('2026-07-01T00:00:00.000Z') - n * 60_000).toISOString(),
      replies: [],
    });
  }
}

describe('TabFeedback: the list', () => {
  it('shows the open items first, newest first, with the counts, under the settings', async () => {
    const wrapper = await loaded();

    expect(rowIds(wrapper)).toEqual(['fb-1', 'fb-2']);
    expect(wrapper.find('[data-testid="feedback-status-open"]').attributes('text')).toBe('Open (2)');
    expect(wrapper.find('[data-testid="feedback-status-all"]').attributes('text')).toBe('Alles (3)');
    // The settings are a section of their own at the top of the tab, in place.
    const headings = wrapper.findAll('h2').map((heading) => heading.text());
    expect(headings).toEqual(['Feedback verzamelen', 'Ontvangen feedback']);
    const enabled = wrapper.find('[data-testid="feedback-enabled"]').element;
    expect(enabled.compareDocumentPosition(wrapper.find('#heading-feedback-inbox').element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('gives those who cannot change the settings one line about them above the list', async () => {
    signInWithSiteRole('reader');
    const wrapper = await loaded();

    expect(wrapper.findAll('h2').map((heading) => heading.text())).toEqual(['Feedback verzamelen', 'Ontvangen feedback']);
    const line = wrapper.find('[data-testid="feedback-summary"]').element;
    expect(line.textContent?.trim()).toBe(
      'Feedback staat aan op de previews. Geven kan met een account of een zelf ingevulde naam.',
    );
    expect(line.compareDocumentPosition(wrapper.find('#heading-feedback-inbox').element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('offers the previews of the site as places to filter on', async () => {
    const wrapper = await loaded();

    expect(places(wrapper)).toEqual(['all', 'live', 'preview', 'preview:pr-42']);
  });

  it('narrows to the live site or to all previews once no preview is left, and when they cannot be read', async () => {
    // The open items mix the live site and a preview that is gone.
    backend.data.previews = [];
    const none = await loaded();
    expect(places(none)).toEqual(['all', 'live', 'preview']);
    expect(rowIds(none)).toEqual(['fb-1', 'fb-2']);
    await none.find('[data-testid="feedback-where"]').setValue('live');
    await untilIdle();
    expect(rowIds(none)).toEqual(['fb-2']);
    await none.find('[data-testid="feedback-where"]').setValue('preview');
    await untilIdle();
    expect(rowIds(none)).toEqual(['fb-1']);

    _resetCurrentMemberCache();
    backend = makeMockBackend();
    stubUnreadablePreviews();
    const failed = await loaded();
    expect(places(failed)).toEqual(['all', 'live', 'preview']);
    expect(rowIds(failed)).toEqual(['fb-1', 'fb-2']);
  });

  it('asks for another status and another place when they are chosen', async () => {
    const queries = recordListQueries();
    const wrapper = await loaded();

    chooseStatus(wrapper, 'resolved');
    await untilIdle();
    expect(rowIds(wrapper)).toEqual(['fb-3']);

    chooseStatus(wrapper, 'all');
    await untilIdle();
    await wrapper.find('[data-testid="feedback-where"]').setValue('live');
    await untilIdle();
    expect(rowIds(wrapper)).toEqual(['fb-2']);

    await wrapper.find('[data-testid="feedback-where"]').setValue('preview');
    await untilIdle();
    expect(rowIds(wrapper)).toEqual(['fb-1', 'fb-3']);

    await wrapper.find('[data-testid="feedback-where"]').setValue('preview:pr-42');
    await untilIdle();

    expect(queries).toEqual([
      '?status=open',
      '?status=resolved',
      '?status=all',
      '?status=all&target=live',
      '?status=all&target=preview',
      '?status=all&target=preview&ref=pr-42',
    ]);
  });

  it('counts the statuses beside the whole list only, since the counts cover the whole site', async () => {
    const wrapper = await loaded();
    const labels = () =>
      wrapper.findAll('[data-testid="feedback-status"] nldd-toggle-button').map((choice) => choice.attributes('text'));

    await wrapper.find('[data-testid="feedback-where"]').setValue('live');
    await untilIdle();
    expect(rowIds(wrapper)).toEqual(['fb-2']);
    expect(labels()).toEqual(['Open', 'Afgehandeld', 'Alles']);

    await wrapper.find('[data-testid="feedback-where"]').setValue('all');
    await untilIdle();
    expect(labels()).toEqual(['Open (2)', 'Afgehandeld (1)', 'Alles (3)']);
  });

  it('says what went wrong when the list cannot be loaded', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());
    const wrapper = await loaded();

    expect(wrapper.find('nldd-banner').attributes('text')).toBe('Serverfout');
    expect(wrapper.findComponent(FeedbackInbox).exists()).toBe(false);
    // The banner says it aloud already.
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('keeps the inbox, its filters and the focus when the list fails to come back after a filter change', async () => {
    addItems(55);
    const wrapper = await loaded();
    expect(wrapper.find('[data-testid="feedback-more"]').exists()).toBe(true);
    const chosen = wrapper.find('[data-testid="feedback-status-resolved"]').element;
    failingList((query) => query.includes('status=resolved'));

    chooseStatus(wrapper, 'resolved');
    await untilIdle();

    expect(wrapper.findComponent(ErrorBanner).exists()).toBe(false);
    expect(wrapper.find('#heading-feedback-inbox').exists()).toBe(true);
    // The same element: the filter that has the focus was never taken away.
    expect(wrapper.find('[data-testid="feedback-status-resolved"]').element).toBe(chosen);
    expect(wrapper.find('[data-testid="feedback-where"]').exists()).toBe(true);
    const failure = wrapper.find('[data-testid="feedback-list-failed"]');
    expect(failure.attributes('text')).toBe('Feedback laden lukte niet');
    expect(failure.attributes('supporting-text')).toBe('Even niet beschikbaar.');
    expect(failure.find('[data-testid="feedback-retry"]').attributes('text')).toBe('Opnieuw proberen');
    // The rows and the next page of the filter before make way.
    expect(wrapper.find('[data-testid="feedback-list"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-more"]').exists()).toBe(false);
    const notice = wrapper.find('nldd-notification[variant="critical"]');
    expect(notice.attributes('text')).toBe('Feedback laden lukte niet');
    expect(notice.attributes('supporting-text')).toBe('Even niet beschikbaar.');
  });

  it('asks for the same list again from the failure, with the focus on the chosen status first', async () => {
    const wrapper = await loaded();
    const failing = failingList((query) => query.includes('status=resolved'));
    chooseStatus(wrapper, 'resolved');
    await untilIdle();
    expect(wrapper.findAll('nldd-notification[variant="critical"]')).toHaveLength(1);
    failing.on = false;
    const queries = recordListQueries();
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');

    await wrapper.find('[data-testid="feedback-retry"]').trigger('click');
    await untilIdle();

    expect(queries).toEqual(['?status=resolved']);
    expect(rowIds(wrapper)).toEqual(['fb-3']);
    expect(wrapper.find('[data-testid="feedback-list-failed"]').exists()).toBe(false);
    expect(focus.mock.contexts.at(-1)).toBe(wrapper.find('[data-testid="feedback-status-resolved"]').element);
  });

  it('says it again each time trying again fails too', async () => {
    const wrapper = await loaded();
    failingList((query) => query.includes('status=resolved'));
    chooseStatus(wrapper, 'resolved');
    await untilIdle();

    await wrapper.find('[data-testid="feedback-retry"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-list-failed"]').exists()).toBe(true);
    expect(wrapper.findAll('nldd-notification[variant="critical"]').map((notice) => notice.attributes('text'))).toEqual([
      'Feedback laden lukte niet',
      'Feedback laden lukte niet',
    ]);
  });
});

describe('TabFeedback: the filters in the address', () => {
  function where(wrapper: ReturnType<typeof makeWrapper>): string {
    return wrapper.find<HTMLSelectElement>('[data-testid="feedback-where"]').element.value;
  }

  it('lands on the list of one preview, and asks for nothing else', async () => {
    const queries = recordListQueries();
    const wrapper = makeWrapper(await feedbackRouterAt('/team-aurora/website/feedback?status=all&target=preview&ref=pr-42'));
    await untilIdle();

    expect(queries).toEqual(['?status=all&target=preview&ref=pr-42']);
    expect(rowIds(wrapper)).toEqual(['fb-1', 'fb-3']);
    expect(where(wrapper)).toBe('preview:pr-42');
    expect(wrapper.find('[data-testid="feedback-status-all"]').attributes('selected')).toBeDefined();
  });

  it('keeps a preview that is gone on offer when the address names it', async () => {
    const wrapper = makeWrapper(await feedbackRouterAt('/team-aurora/website/feedback?target=preview&ref=pr-7'));
    await untilIdle();

    expect(where(wrapper)).toBe('preview:pr-7');
    expect(wrapper.find('[data-testid="feedback-empty-filter"]').exists()).toBe(true);
  });

  it('reads an address it cannot make sense of as the open items of everything', async () => {
    const queries = recordListQueries();
    const wrapper = makeWrapper(await feedbackRouterAt('/team-aurora/website/feedback?status=weg&target=preview&ref=..%2Fpr'));
    await untilIdle();

    expect(queries).toEqual(['?status=open&target=preview']);
    expect(where(wrapper)).toBe('preview');
  });

  it('writes a choice into the address in place of the one before, so going back leaves the list', async () => {
    const router = await feedbackRouterAt('/team-aurora/website/previews', '/team-aurora/website/feedback?target=preview&ref=pr-42');
    const wrapper = makeWrapper(router);
    await untilIdle();
    vi.mocked(stayOnNextMove).mockClear();

    chooseStatus(wrapper, 'resolved');
    await untilIdle();
    // The focus stays on the filter just chosen.
    expect(stayOnNextMove).toHaveBeenCalledTimes(1);
    expect(router.currentRoute.value.fullPath).toBe('/team-aurora/website/feedback?status=resolved&target=preview&ref=pr-42');
    expect(rowIds(wrapper)).toEqual(['fb-3']);

    await wrapper.find('[data-testid="feedback-where"]').setValue('all');
    await untilIdle();
    expect(router.currentRoute.value.fullPath).toBe('/team-aurora/website/feedback?status=resolved');

    router.back();
    await untilIdle();
    expect(router.currentRoute.value.fullPath).toBe('/team-aurora/website/previews');
  });

  it('says an item is deleted when the page of that item comes back to the list, and only then', async () => {
    const router = await feedbackRouterAt('/team-aurora/website/previews');
    await markDeleted(router, '/team-aurora/website/feedback?status=all');
    const wrapper = makeWrapper(router);
    await untilIdle();

    expect(wrapper.findAll('nldd-notification').map((notice) => notice.attributes('text'))).toEqual([
      'Feedback verwijderd',
    ]);
    expect(router.currentRoute.value.fullPath).toBe('/team-aurora/website/feedback?status=all');
    // Taken: a list shown again from this entry says nothing more.
    expect(router.options.history.state.toonFeedbackDeleted).toBe(false);
    wrapper.unmount();
    const again = makeWrapper(router);
    await untilIdle();
    expect(again.find('nldd-notification').exists()).toBe(false);
  });

  it('follows the address when it changes under the list, as the back button does', async () => {
    const router = feedbackRouter();
    const wrapper = makeWrapper(router);
    await untilIdle();
    expect(rowIds(wrapper)).toEqual(['fb-1', 'fb-2']);

    await router.push('/team-aurora/website/feedback?target=live');
    await untilIdle();

    expect(rowIds(wrapper)).toEqual(['fb-2']);
    expect(where(wrapper)).toBe('live');
  });
});

describe('TabFeedback: more', () => {
  it('loads the next page and puts the focus on the first item it brought', async () => {
    addItems(55);
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');
    const wrapper = await loaded();
    expect(rowIds(wrapper)).toHaveLength(50);

    await wrapper.find('[data-testid="feedback-more"]').trigger('click');
    await untilIdle();

    expect(rowIds(wrapper)).toHaveLength(57);
    expect(wrapper.find('[data-testid="feedback-more"]').exists()).toBe(false);
    expect(focus.mock.contexts.at(-1)).toBe(wrapper.find(`[data-item-id="${rowIds(wrapper)[50]}"]`).element);
  });

  it('asks once, also when "more" is pressed again while it loads', async () => {
    addItems(55);
    const queries = recordListQueries();
    const wrapper = await loaded();

    const inbox = wrapper.findComponent(FeedbackInbox);
    inbox.vm.$emit('more');
    inbox.vm.$emit('more');
    await untilIdle();

    expect(queries.filter((query) => query.includes('cursor'))).toHaveLength(1);
  });

  it('asks nothing when there is no next page', async () => {
    const queries = recordListQueries();
    const wrapper = await loaded();

    wrapper.findComponent(FeedbackInbox).vm.$emit('more');
    await untilIdle();

    expect(queries).toEqual(['?status=open']);
  });

  it('says so when the next page cannot be loaded, and keeps what is there', async () => {
    addItems(55);
    const wrapper = await loaded();
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));

    await wrapper.find('[data-testid="feedback-more"]').trigger('click');
    await untilIdle();

    const notice = wrapper.find('nldd-notification[variant="critical"]');
    expect(notice.attributes('text')).toBe('Meer feedback laden lukte niet');
    expect(notice.attributes('supporting-text')).toBe('Probeer het opnieuw.');
    expect(rowIds(wrapper)).toHaveLength(50);
    expect(wrapper.find('[data-testid="feedback-more"]').attributes('loading')).toBeUndefined();
  });

  it('drops a next page that arrives after the list was replaced', async () => {
    addItems(55);
    const wrapper = await loaded();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('cursor=')) await gate;
      return backend.fetch(input, init);
    });

    await wrapper.find('[data-testid="feedback-more"]').trigger('click');
    chooseStatus(wrapper, 'resolved');
    await untilIdle();
    release();
    await untilIdle();

    expect(rowIds(wrapper)).toEqual(['fb-3']);
  });

  it('moves the focus nowhere when the next page turns out empty', async () => {
    addItems(55);
    const wrapper = await loaded();
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).includes('cursor=')
        ? Promise.resolve(
            new Response(JSON.stringify({ items: [], nextCursor: null, counts: { new: 1, seen: 56, resolved: 1 } }), {
              status: 200,
              headers: { 'content-type': 'application/json' },
            }),
          )
        : backend.fetch(input, init),
    );

    await wrapper.find('[data-testid="feedback-more"]').trigger('click');
    await untilIdle();

    expect(focus).not.toHaveBeenCalled();
    expect(wrapper.find('[data-testid="feedback-status-open"]').attributes('text')).toBe('Open (57)');
  });
});

describe('TabFeedback: one item', () => {
  it('opens an item in the sheet and puts the focus back on its row when the sheet is gone', async () => {
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');
    const wrapper = await loaded();
    const sheet = wrapper.findComponent(FeedbackSheet);
    expect(sheet.props('item')).toBeNull();

    await openItem(wrapper, 'fb-2');
    expect(sheet.props('item')).toMatchObject({ id: 'fb-2' });
    expect(wrapper.find('[data-testid="feedback-sheet"] h2').text()).toBe('Feedback op Team Aurora');

    sheet.vm.$emit('close');
    await untilIdle();

    expect(sheet.props('item')).toBeNull();
    expect(focus.mock.contexts.at(-1)).toBe(wrapper.find('[data-testid="feedback-fb-2"]').element);
  });

  it('moves the counts and the row along with a status set in the sheet, and tells the page', async () => {
    const wrapper = await loaded();

    await wrapper.find('[data-testid="feedback-fb-1"]').trigger('click');
    await untilIdle();
    await wrapper.find('[data-testid="feedback-mark-resolved"]').trigger('click');
    await untilIdle();

    // The row stays, with its new status, until the filter changes.
    expect(rowIds(wrapper)).toEqual(['fb-1', 'fb-2']);
    expect(wrapper.find('[data-testid="feedback-badge-fb-1"]').attributes('text')).toBe('Afgehandeld');
    expect(wrapper.find('[data-testid="feedback-status-open"]').attributes('text')).toBe('Open (1)');
    expect(wrapper.find('[data-testid="feedback-status-resolved"]').attributes('text')).toBe('Afgehandeld (2)');
    expect(wrapper.emitted('changed')).toHaveLength(1);
  });

  it('changes nothing for an answer without a change, or about an item it does not list', async () => {
    const wrapper = await loaded();
    const sheet = wrapper.findComponent(FeedbackSheet);
    const fb1 = backend.data.feedback.find((item) => item.id === 'fb-1')!;

    sheet.vm.$emit('updated', { ...fb1, status: 'new' });
    sheet.vm.$emit('updated', { ...fb1, id: 'elders', status: 'resolved' });
    sheet.vm.$emit('removed', 'elders');
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-status-open"]').attributes('text')).toBe('Open (2)');
    expect(wrapper.emitted('changed')).toEqual([[]]);
  });

  it('counts a reply on the row', async () => {
    const wrapper = await loaded();

    await wrapper.find('[data-testid="feedback-fb-1"]').trigger('click');
    await untilIdle();
    fireDetailEvent(wrapper.find('[data-testid="feedback-reply"]').element, 'input', { value: 'Dank je' });
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-fb-1"] [slot="supporting-text"]').text()).toContain('1 reactie');
    expect(wrapper.find('[data-testid="feedback-fb-2"] [slot="supporting-text"]').text()).toContain('2 reacties');
  });

  it('takes a deleted item out of the list and the counts, closes the sheet and says so', async () => {
    const wrapper = await loaded();

    await wrapper.find('[data-testid="feedback-fb-1"]').trigger('click');
    await untilIdle();
    await wrapper.find('[data-testid="feedback-delete"]').trigger('click');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    expect(rowIds(wrapper)).toEqual(['fb-2']);
    expect(wrapper.find('[data-testid="feedback-status-open"]').attributes('text')).toBe('Open (1)');
    expect(wrapper.findComponent(FeedbackSheet).props('item')).toBeNull();
    expect(wrapper.find('nldd-notification[variant="success"]').attributes('text')).toBe('Feedback verwijderd');
    expect(wrapper.emitted('changed')).toHaveLength(1);
  });

  it.each([
    ['the last one under a filter', 'feedback-empty-filter', false],
    ['the last one of the site', 'feedback-empty-none', true],
  ])('puts the focus on the heading of the list once %s is deleted and the sheet is gone', async (_, shown, alone) => {
    // Alone, its delete takes the filters away too: there is nothing left to filter.
    if (alone) backend.data.feedback = backend.data.feedback.filter((item) => item.id === 'fb-3');
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');
    const wrapper = await loaded();
    chooseStatus(wrapper, 'resolved');
    await untilIdle();
    expect(rowIds(wrapper)).toEqual(['fb-3']);

    await wrapper.find('[data-testid="feedback-fb-3"]').trigger('click');
    await untilIdle();
    await wrapper.find('[data-testid="feedback-delete"]').trigger('click');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();
    wrapper.findComponent(FeedbackSheet).vm.$emit('close');
    await untilIdle();

    expect(rowIds(wrapper)).toEqual([]);
    expect(wrapper.find(`[data-testid="${shown}"]`).exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-status"]').exists()).toBe(!alone);
    const heading = wrapper.find('#heading-feedback-inbox');
    expect(focus.mock.contexts.at(-1)).toBe(heading.element);
    expect(heading.attributes('tabindex')).toBe('-1');
  });
});

describe('TabFeedback: an answer for an item that is no longer open', () => {
  /** Closes the sheet with its own button and opens another row, as a hurried editor would. */
  async function closeAndOpen(wrapper: ReturnType<typeof makeWrapper>, id: string): Promise<void> {
    await wrapper.find('[data-testid="feedback-sheet-close"]').trigger('click');
    await untilIdle();
    await wrapper.find(`[data-testid="feedback-${id}"]`).trigger('click');
    await untilIdle();
  }

  it('keeps the item opened since on screen, and moves the row of the one before along', async () => {
    const wrapper = await loaded();
    await wrapper.find('[data-testid="feedback-fb-1"]').trigger('click');
    await untilIdle();
    const held = holding((url, method) => method === 'PUT' && url.endsWith('/feedback/fb-1/status'));
    await wrapper.find('[data-testid="feedback-mark-resolved"]').trigger('click');

    await closeAndOpen(wrapper, 'fb-2');
    held[0]!.release();
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-sheet"] h2').text()).toBe('Feedback op Team Aurora');
    expect(wrapper.find('[data-testid="feedback-comment"]').text()).toContain('De knop onderaan');
    expect(wrapper.find('[data-testid="feedback-meta-status"]').text()).toContain('Gezien');
    expect(wrapper.find('[data-testid="feedback-badge-fb-1"]').attributes('text')).toBe('Afgehandeld');
    expect(wrapper.find('[data-testid="feedback-status-open"]').attributes('text')).toBe('Open (1)');

    // What is done in the sheet now reaches the item it shows.
    fireDetailEvent(wrapper.find('[data-testid="feedback-reply"]').element, 'input', { value: 'Antwoord voor B' });
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await untilIdle();
    expect(backend.data.feedback.find((item) => item.id === 'fb-2')!.replies.at(-1)!.body).toBe('Antwoord voor B');
    expect(backend.data.feedback.find((item) => item.id === 'fb-1')!.replies).toEqual([]);

    await wrapper.find('[data-testid="feedback-delete"]').trigger('click');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();
    expect(backend.data.feedback.map((item) => item.id).sort()).toEqual(['fb-1', 'fb-3']);
  });

  it('keeps the draft of the item opened since, and counts the reply on the row it belongs to', async () => {
    const wrapper = await loaded();
    await wrapper.find('[data-testid="feedback-fb-1"]').trigger('click');
    await untilIdle();
    const held = holding((url, method) => method === 'POST' && url.endsWith('/feedback/fb-1/replies'));
    fireDetailEvent(wrapper.find('[data-testid="feedback-reply"]').element, 'input', { value: 'Antwoord voor A' });
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');

    await closeAndOpen(wrapper, 'fb-2');
    fireDetailEvent(wrapper.find('[data-testid="feedback-reply"]').element, 'input', { value: 'Concept voor B' });
    await untilIdle();
    // Not held up by the reply before.
    expect(wrapper.find('[data-testid="feedback-reply-submit"]').attributes('loading')).toBeUndefined();
    held[0]!.release();
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-reply"]').attributes('value')).toBe('Concept voor B');
    expect(wrapper.find('[data-testid="feedback-fb-1"] [slot="supporting-text"]').text()).toContain('1 reactie');
    expect(wrapper.find('[data-testid="feedback-fb-2"] [slot="supporting-text"]').text()).toContain('2 reacties');
  });
});

describe('TabFeedback: the way to the place of an item', () => {
  async function openedItem(id: string) {
    const wrapper = await loaded();
    await openItem(wrapper, id);
    return wrapper;
  }

  it('leads to the page while the preview it was given on still opens', async () => {
    previewUntil(new Date(Date.now() + DAY_MS).toISOString());
    const wrapper = await openedItem('fb-1');

    expect(wrapper.find('[data-testid="feedback-view"]').exists()).toBe(true);
  });

  it('leads to the page of a preview that never expires', async () => {
    previewUntil(null);
    const wrapper = await openedItem('fb-1');

    expect(wrapper.find('[data-testid="feedback-view"]').exists()).toBe(true);
  });

  it('says the place cannot be viewed once the site has no previews left', async () => {
    backend.data.previews = [];
    const wrapper = await openedItem('fb-1');

    expect(wrapper.find('[data-testid="feedback-view"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-view-gone"]').text()).toBe(
      'Preview pr-42 is verlopen of bestaat niet meer, dus deze plek is niet meer te bekijken.',
    );
  });

  it('counts an expired preview as gone, while the place filter still offers it', async () => {
    previewUntil(new Date(Date.now() - 60_000).toISOString());
    const wrapper = await openedItem('fb-1');

    expect(wrapper.find('[data-testid="feedback-view-gone"]').exists()).toBe(true);
    // Its feedback is still there to filter on.
    expect(places(wrapper)).toContain('preview:pr-42');
  });

  it('takes the way to the page from the item, also when the previews cannot be read', async () => {
    stubUnreadablePreviews();
    const wrapper = await openedItem('fb-1');

    expect(wrapper.find('[data-testid="feedback-view"]').exists()).toBe(true);
  });
});

describe('TabFeedback: the previews, read with the tab', () => {
  const FUTURE = new Date(Date.now() + DAY_MS).toISOString();

  /** A preview deployed since, with an open item given on it. */
  function deployWithItem(ref: string, id: string): void {
    const template = makeMockBackend().data;
    backend.data.previews.push({
      ...template.previews[0]!,
      ref,
      url: `/team-aurora/website/_preview/${ref}/`,
      lastUpdatedAt: new Date().toISOString(),
      expiresAt: FUTURE,
    });
    backend.data.feedback.push({
      ...template.feedback.find((item) => item.id === 'fb-1')!,
      id,
      previewRef: ref,
      createdAt: new Date().toISOString(),
    });
  }

  async function close(wrapper: ReturnType<typeof makeWrapper>): Promise<void> {
    wrapper.findComponent(FeedbackSheet).vm.$emit('close');
    await untilIdle();
  }

  it('reads them once, not again with every list', async () => {
    const wrapper = await loaded();
    const asked: string[] = [];
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      asked.push(new URL(String(input), 'https://toon.test').pathname.split('/').at(-1)!);
      return backend.fetch(input, init);
    });

    chooseStatus(wrapper, 'all');
    await untilIdle();

    expect(asked).toEqual(['feedback']);
  });

  it('leads to the page of a preview deployed after the tab opened', async () => {
    previewUntil(FUTURE);
    const wrapper = await loaded();
    deployWithItem('pr-43', 'fb-43');

    chooseStatus(wrapper, 'all');
    await untilIdle();
    await openItem(wrapper, 'fb-43');

    expect(wrapper.find('[data-testid="feedback-view"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-view-gone"]').exists()).toBe(false);
  });

  it('reads them although the site counted none when the page loaded', async () => {
    backend.data.previews = [];
    const provide = provideSiteGroup(backend);
    expect(provide[SITE_GROUP as symbol]!.site.value.previewCount).toBe(0);
    deployWithItem('pr-1', 'fb-p1');

    const wrapper = makeWrapper(feedbackRouter(), provide);
    await untilIdle();
    await openItem(wrapper, 'fb-p1');

    expect(wrapper.find('[data-testid="feedback-view"]').exists()).toBe(true);
    expect(places(wrapper)).toEqual(['all', 'live', 'preview', 'preview:pr-1']);
  });

  it('says the place cannot be viewed once its preview is gone since the tab opened', async () => {
    previewUntil(FUTURE);
    const wrapper = await loaded();
    await openItem(wrapper, 'fb-1');
    expect(wrapper.find('[data-testid="feedback-view"]').exists()).toBe(true);
    await close(wrapper);

    backend.data.previews = [];
    chooseStatus(wrapper, 'all');
    await untilIdle();
    await openItem(wrapper, 'fb-1');

    expect(wrapper.find('[data-testid="feedback-view"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-view-gone"]').text()).toBe(
      'Preview pr-42 is verlopen of bestaat niet meer, dus deze plek is niet meer te bekijken.',
    );
  });

  it('offers a preview that is gone only while the list is narrowed to it', async () => {
    const wrapper = makeWrapper(await feedbackRouterAt('/team-aurora/website/feedback?target=preview&ref=pr-7'));
    await untilIdle();
    expect(places(wrapper)).toEqual(['all', 'live', 'preview', 'preview:pr-42', 'preview:pr-7']);

    await wrapper.find('[data-testid="feedback-where"]').setValue('preview');
    await untilIdle();
    expect(places(wrapper)).toEqual(['all', 'live', 'preview', 'preview:pr-42']);
  });
});

describe('TabFeedback: feedback from the public through a preview', () => {
  beforeEach(() => {
    backend.data.sites[0]!.access = { base: 'sso', keys: false, invitees: false };
    backend.data.sites[0]!.feedbackIdentity = 'anonymous';
  });

  it('warns when a preview that still opens is public by an access of its own', async () => {
    previewUntil(new Date(Date.now() + DAY_MS).toISOString());
    backend.data.previews[0]!.accessOverride = { base: 'public', keys: false, invitees: false };
    const wrapper = await loaded();

    expect(wrapper.findComponent(FeedbackSettings).props('publicPreview')).toBe(true);
    expect(wrapper.find('[data-testid="feedback-public-warning"]').attributes('supporting-text')).toContain(
      'Iedereen met de link naar een preview kan feedback geven zonder in te loggen.',
    );
  });

  it('does not warn for an expired public preview, nor for previews that cannot be read', async () => {
    previewUntil(new Date(Date.now() - 60_000).toISOString());
    backend.data.previews[0]!.accessOverride = { base: 'public', keys: false, invitees: false };
    const expired = await loaded();
    expect(expired.findComponent(FeedbackSettings).props('publicPreview')).toBe(false);
    expect(expired.find('[data-testid="feedback-public-warning"]').exists()).toBe(false);

    stubUnreadablePreviews();
    const unknown = await loaded();
    expect(unknown.findComponent(FeedbackSettings).props('publicPreview')).toBe(false);
  });
});

describe('TabFeedback: no page carries the button', () => {
  beforeEach(() => {
    backend.data.previews = [];
    backend.data.feedback = [];
  });

  /** The help text under the live switch. */
  function liveHint(wrapper: ReturnType<typeof makeWrapper>): string | undefined {
    return wrapper
      .find('[data-testid="feedback-live"]')
      .element.parentElement?.querySelector('nldd-form-field-help-text')
      ?.textContent?.trim();
  }

  it('says so when only the previews take feedback and the site has none, and points an admin up to the live switch', async () => {
    const wrapper = await loaded();

    const empty = wrapper.find('[data-testid="feedback-empty-nowhere"]');
    expect(empty.attributes('text')).toBe('Nog geen pagina heeft de feedbackknop');
    expect(empty.attributes('supporting-text')).toBe(
      'Deze site heeft geen previews, en op de live site staat feedback uit. Zet feedback hierboven, onder Feedback verzamelen, ook op de live site aan, of publiceer een preview met de CLI of vanuit je repository.',
    );
    expect(empty.find('[slot="actions"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-empty-none"]').exists()).toBe(false);
    expectAbove(wrapper.find('[data-testid="feedback-live"]').element, empty.element);
    // Right under the switch it says the same without the way up.
    expect(liveHint(wrapper)).toBe(
      'Anders alleen op previews. Deze site heeft geen previews, dus nu heeft geen enkele pagina de feedbackknop. Zet feedback ook op de live site aan, of publiceer een preview met de CLI of vanuit je repository.',
    );
  });

  it('says the site needs a version first when the live site takes feedback but has none', async () => {
    backend.data.sites[0]!.feedbackLive = true;
    backend.data.sites[0]!.hasLiveVersion = false;
    backend.data.sites[0]!.liveVersionId = null;
    const wrapper = await loaded();

    const empty = wrapper.find('[data-testid="feedback-empty-nowhere"]');
    expect(empty.attributes('supporting-text')).toBe(
      'Er staat nog geen versie live en deze site heeft geen previews. Publiceer eerst een versie van de site.',
    );
    expect(empty.find('[slot="actions"]').exists()).toBe(false);
    expect(liveHint(wrapper)).toContain('Publiceer eerst een versie van de site.');
  });

  it('says it beside the feedback a site already has, once its previews are gone', async () => {
    backend.data.feedback = makeMockBackend().data.feedback;
    const wrapper = await loaded();

    expect(rowIds(wrapper)).toEqual(['fb-1', 'fb-2']);
    expect(wrapper.find('[data-testid="feedback-empty-nowhere"]').exists()).toBe(false);
    expect(liveHint(wrapper)).toContain('dus nu heeft geen enkele pagina de feedbackknop.');
  });

  it('tells a member who cannot change it, in the list and in the summary', async () => {
    signInWithSiteRole('editor');
    const wrapper = await loaded();

    expect(wrapper.find('[data-testid="feedback-empty-nowhere"]').attributes('supporting-text')).toBe(
      'Deze site heeft geen previews, en op de live site staat feedback uit.',
    );
    expect(wrapper.find('[data-testid="feedback-summary"]').text()).toBe(
      'Feedback staat aan op de previews. Geven kan met een account of een zelf ingevulde naam. Deze site heeft geen previews, dus nu heeft geen enkele pagina de feedbackknop.',
    );
  });

  it('says nothing of the kind when the previews cannot be read, since the site has some', async () => {
    backend = makeMockBackend();
    backend.data.feedback = [];
    stubUnreadablePreviews();
    const wrapper = await loaded();

    expect(wrapper.find('[data-testid="feedback-empty-nowhere"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-empty-none"]').exists()).toBe(true);
    expect(liveHint(wrapper)).toBe('Anders alleen op previews.');
  });

  it('drops it once the live site takes feedback too', async () => {
    const wrapper = await loaded();

    fireDetailEvent(wrapper.find('[data-testid="feedback-live"]').element, 'change', { checked: true });
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-empty-nowhere"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-empty-none"]').exists()).toBe(true);
    expect(liveHint(wrapper)).toBe('Anders alleen op previews.');
  });
});

describe('TabFeedback: by role', () => {
  it('gives an admin the settings and the sheet everything', async () => {
    const wrapper = await loaded();

    expect(wrapper.find('[data-testid="feedback-enabled"]').exists()).toBe(true);
    const sheet = wrapper.findComponent(FeedbackSheet);
    expect(sheet.props('canEdit')).toBe(true);
    expect(sheet.props('canAdmin')).toBe(true);
  });

  it('gives an editor of the site the reply and the status, not the settings', async () => {
    signInWithSiteRole('editor');
    const wrapper = await loaded();

    expect(wrapper.find('[data-testid="feedback-summary"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-enabled"]').exists()).toBe(false);
    const sheet = wrapper.findComponent(FeedbackSheet);
    expect(sheet.props('canEdit')).toBe(true);
    expect(sheet.props('canAdmin')).toBe(false);
  });

  it('lets a reader of the site read, and treats a session that cannot be read the same', async () => {
    signInWithSiteRole('reader');
    const reader = await loaded();
    expect(reader.findComponent(FeedbackSheet).props('canEdit')).toBe(false);
    expect(reader.find('[data-testid="feedback-summary"]').exists()).toBe(true);

    _resetCurrentMemberCache();
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/me') ? Promise.reject(new TypeError('network down')) : backend.fetch(input, init),
    );
    const unknown = await loaded();
    expect(unknown.findComponent(FeedbackSheet).props('canEdit')).toBe(false);
    expect(unknown.find('[data-testid="feedback-summary"]').exists()).toBe(true);
  });
});

describe('TabFeedback: the place on the page', () => {
  it('tells the sheet that the live site takes no feedback, and follows the switch once it is saved', async () => {
    const wrapper = await loaded();
    await openItem(wrapper, 'fb-2');
    expect(wrapper.find('[data-testid="feedback-view-unmarked"]').text()).toBe(
      'Feedback staat uit op de live site, dus de pagina opent zonder deze plek te markeren.',
    );
    wrapper.findComponent(FeedbackSheet).vm.$emit('close');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="feedback-live"]').element, 'change', { checked: true });
    await untilIdle();
    await openItem(wrapper, 'fb-2');

    expect(wrapper.find('[data-testid="feedback-view"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-view-unmarked"]').exists()).toBe(false);
  });

  it('tells the sheet that the site takes no feedback at all', async () => {
    backend.data.sites[0]!.feedbackEnabled = false;
    previewUntil(null);
    const wrapper = await loaded();
    await openItem(wrapper, 'fb-1');

    expect(wrapper.find('[data-testid="feedback-view-unmarked"]').text()).toBe(
      'Feedback staat uit voor deze site, dus de pagina opent zonder deze plek te markeren.',
    );
  });
});

describe('TabFeedback: feedback off', () => {
  beforeEach(() => {
    backend.data.sites[0]!.feedbackEnabled = false;
    backend.data.feedback = [];
  });

  it('tells an admin that feedback is off, and points up to the switch above the list', async () => {
    const wrapper = await loaded();

    const empty = wrapper.find('[data-testid="feedback-empty-off"]');
    expect(empty.attributes('supporting-text')).toBe(
      'Zet feedback hierboven aan, onder Feedback verzamelen. Dan krijgen de previews van deze site een feedbackknop.',
    );
    expect(empty.find('[slot="actions"]').exists()).toBe(false);
    const enabled = wrapper.find('[data-testid="feedback-enabled"]');
    expect(enabled.attributes('checked')).toBeUndefined();
    expectAbove(enabled.element, empty.element);
  });

  it('says that nothing came in yet once feedback is turned on, and tells the page', async () => {
    const wrapper = await loaded();

    fireDetailEvent(wrapper.find('[data-testid="feedback-enabled"]').element, 'change', { checked: true });
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-empty-off"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-empty-none"]').exists()).toBe(true);
    expect(wrapper.emitted('changed')).toHaveLength(1);
  });
});

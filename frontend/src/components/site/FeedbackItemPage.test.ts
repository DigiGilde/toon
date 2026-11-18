import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '../../../tests/mock-api';
import { _resetCurrentMemberCache } from '@/composables/currentMember';
import { _setLocaleForTest } from '@/i18n';

import FeedbackItemPage from './FeedbackItemPage.vue';
import { feedbackRouterAt, fireDetailEvent, problemFetch, provideSiteGroup, serverErrorFetch, untilIdle } from './testHelpers';

/** Feedback ids are UUIDs, and the page asks for nothing else. */
const ID = '6f1c2b3a-4d5e-4f60-8a7b-9c0d1e2f3a4b';
const OTHER_SITE_ID = '0a1b2c3d-4e5f-4a6b-8c7d-8e9f0a1b2c3d';
const LIST = '/team-aurora/website/feedback';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  // fb-1 under an id like the API hands out.
  backend.data.feedback.find((item) => item.id === 'fb-1')!.id = ID;
  // Its preview still opens.
  backend.data.previews[0]!.expiresAt = '2099-01-01T00:00:00.000Z';
  vi.stubGlobal('fetch', backend.fetch);
  _resetCurrentMemberCache();
  document.title = '';
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  _setLocaleForTest('nl');
});

async function openPage(path = `${LIST}/${ID}?status=all&target=preview&ref=pr-42`, before: string[] = []) {
  const router = await feedbackRouterAt(...before, path);
  const wrapper = mount(FeedbackItemPage, {
    props: { group: 'team-aurora', site: 'website', contentBase: MOCK_CONTENT_BASE },
    global: { stubs: { teleport: true }, provide: provideSiteGroup(backend), plugins: [router] },
  });
  await untilIdle();
  return { wrapper, router };
}

/** Records the paths of the requests made. */
function recordRequests(): string[] {
  const paths: string[] = [];
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    paths.push(new URL(String(input), 'https://toon.test').pathname);
    return backend.fetch(input, init);
  });
  return paths;
}

function backLink(wrapper: Awaited<ReturnType<typeof openPage>>['wrapper']) {
  return wrapper.find('[data-testid="feedback-page-back"]');
}

describe('FeedbackItemPage: the item', () => {
  it('shows the item under a heading that names it, as the sheet does', async () => {
    const { wrapper } = await openPage();

    const heading = wrapper.find('h2#heading-feedback-item');
    expect(heading.text()).toBe('Feedback op Documentatie');
    expect(wrapper.find('[data-testid="feedback-comment"]').text()).toContain('Klopt dit bedrag?');
    expect(wrapper.find('[data-testid="feedback-screenshot"] nldd-image').exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-reply-form"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-delete"]').exists()).toBe(true);
  });

  it('names the item and the site in the title of the browser tab', async () => {
    await openPage();

    expect(document.title).toBe('Feedback op Documentatie - Team Aurora website - Toon');
  });

  it('names the site by its address in the title when it has no title', async () => {
    backend.data.sites[0]!.title = '';
    await openPage();

    expect(document.title).toBe('Feedback op Documentatie - website - Toon');
  });

  it('leads back to the list as it was filtered', async () => {
    const { wrapper } = await openPage();

    expect(backLink(wrapper).text()).toBe('Terug naar feedback');
    expect(backLink(wrapper).attributes('href')).toBe(`${LIST}?status=all&target=preview&ref=pr-42`);
    expect(backLink(wrapper).attributes('start-icon')).toBe('arrow-left');
  });

  it('goes back inside the app on a plain click', async () => {
    const { wrapper, router } = await openPage();

    backLink(wrapper).element.dispatchEvent(new MouseEvent('click', { button: 0, cancelable: true }));
    await untilIdle();

    expect(router.currentRoute.value.fullPath).toBe(`${LIST}?status=all&target=preview&ref=pr-42`);
  });

  it('waits for the roles before it offers anything', async () => {
    let release: () => void = () => undefined;
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/me')
        ? new Promise<Response>((resolve) => {
            release = () => resolve(backend.fetch(input, init));
          })
        : backend.fetch(input, init),
    );
    const { wrapper } = await openPage();

    expect(wrapper.find('nldd-inline-dialog[variant="loading"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-reply-form"]').exists()).toBe(false);

    release();
    await untilIdle();
    expect(wrapper.find('[data-testid="feedback-reply-form"]').exists()).toBe(true);
  });

  it('lets a reader of the site read, and nothing more', async () => {
    backend.data.siteRoles.push({ groupSlug: 'team-aurora', siteSlug: 'website', identifier: 'sanne@voorbeeld.nl', role: 'reader' });
    backend.data.loggedInMemberId = 'lid-6';
    const { wrapper } = await openPage();

    expect(wrapper.find('[data-testid="feedback-read-only"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-reply-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-delete"]').exists()).toBe(false);
  });

  it('says the place of an item on a preview that is gone can no longer be viewed', async () => {
    backend.data.previews = [];
    const { wrapper } = await openPage();

    expect(wrapper.find('[data-testid="feedback-view-gone"]').exists()).toBe(true);
  });

  it('leaves the way to the page in place when the previews cannot be read', async () => {
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/previews') ? Promise.reject(new TypeError('network down')) : backend.fetch(input, init),
    );
    const { wrapper } = await openPage();

    expect(wrapper.find('[data-testid="feedback-view"]').exists()).toBe(true);
  });

  it('keeps an expired preview from the way to the page', async () => {
    backend.data.previews[0]!.expiresAt = '2020-01-01T00:00:00.000Z';
    const { wrapper } = await openPage();

    expect(wrapper.find('[data-testid="feedback-view-gone"]').exists()).toBe(true);
  });

  it('marks the place as the site is set: feedback off leaves the page without the button', async () => {
    backend.data.sites[0]!.feedbackEnabled = false;
    const { wrapper } = await openPage();

    expect(wrapper.find('[data-testid="feedback-view-unmarked"]').text()).toContain('Feedback staat uit');
  });

  it('says it in English', async () => {
    _setLocaleForTest('en');
    const { wrapper } = await openPage();

    expect(wrapper.find('h2').text()).toBe('Feedback on Documentatie');
    expect(backLink(wrapper).text()).toBe('Back to feedback');
  });
});

describe('FeedbackItemPage: no such item', () => {
  async function notFoundText(path: string): Promise<{ text: string; requests: string[] }> {
    const requests = recordRequests();
    const { wrapper } = await openPage(path);
    expect(wrapper.find('h2#heading-feedback-item').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-item"]').exists()).toBe(false);
    const banner = wrapper.find('nldd-banner');
    return { text: `${banner.attributes('text')} ${banner.attributes('supporting-text')}`, requests };
  }

  it('says the same for an id that is no id, one that is gone, and one of another site', async () => {
    backend.data.feedback.push({
      ...backend.data.feedback.find((item) => item.id === ID)!,
      id: OTHER_SITE_ID,
      siteSlug: 'elders',
    });

    const malformed = await notFoundText(`${LIST}/fb-1`);
    const unknown = await notFoundText(`${LIST}/${ID.replace('6f1c', '7f1c')}`);
    const elsewhere = await notFoundText(`${LIST}/${OTHER_SITE_ID}`);

    expect(malformed.text).toContain('Onbekende feedback');
    expect(malformed.text).toContain('Site team-aurora/website heeft geen feedback op dit adres.');
    expect(unknown.text).toBe(malformed.text);
    expect(elsewhere.text).toBe(malformed.text);
    // An id that is no id is not even asked for.
    expect(malformed.requests.some((path) => path.includes('/feedback/'))).toBe(false);
    expect(unknown.requests.some((path) => path.includes('/feedback/'))).toBe(true);
  });

  it('names it in the title of the browser tab, and keeps the way back', async () => {
    const { wrapper } = await openPage(`${LIST}/fb-1?target=live`);

    expect(document.title).toBe('Onbekende feedback - Team Aurora website - Toon');
    expect(backLink(wrapper).attributes('href')).toBe(`${LIST}?target=live`);
  });

  it('says what went wrong when the item cannot be read for another reason', async () => {
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).includes(`/feedback/${ID}`) ? serverErrorFetch()(input, init) : backend.fetch(input, init),
    );
    const { wrapper } = await openPage();

    const banner = wrapper.find('[data-testid="feedback-item"] nldd-banner');
    expect(banner.exists()).toBe(true);
    expect(banner.attributes('text')).not.toBe('Onbekende feedback');
    expect(document.title).toBe('');
  });

  it('starts afresh when the address names another item', async () => {
    const { wrapper, router } = await openPage(`${LIST}/fb-1`);
    expect(wrapper.find('nldd-banner').exists()).toBe(true);

    await router.push(`${LIST}/${ID}`);
    await untilIdle();

    expect(wrapper.find('nldd-banner').exists()).toBe(false);
    expect(wrapper.find('h2').text()).toBe('Feedback op Documentatie');
  });
});

describe('FeedbackItemPage: handling it', () => {
  it('takes a reply, as the sheet does', async () => {
    const { wrapper } = await openPage();

    fireDetailEvent(wrapper.find('[data-testid="feedback-reply"]').element, 'input', { value: 'Dank je, aangepast.' });
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-thread"]').text()).toContain('Dank je, aangepast.');
    expect(wrapper.find('nldd-notification').attributes('text')).toBe('Reactie geplaatst');
  });

  it('says at the field why a reply was refused, and keeps the text there', async () => {
    const { wrapper } = await openPage();
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === 'POST'
        ? problemFetch(409, { title: 'Conflict', detail: 'Bij deze feedback kunnen geen reacties meer bij.' })(input, init)
        : backend.fetch(input, init),
    );

    fireDetailEvent(wrapper.find('[data-testid="feedback-reply"]').element, 'input', { value: 'Nog een.' });
    await wrapper.find('[data-testid="feedback-reply-form"]').trigger('submit');
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-reply-alert"]').text()).toBe(
      'Bij deze feedback kunnen geen reacties meer bij.',
    );
    expect(wrapper.find('[data-testid="feedback-reply"]').attributes('value')).toBe('Nog een.');
  });

  it('reads a session that cannot be read as no role, and only shows the item', async () => {
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/me') ? Promise.reject(new TypeError('network down')) : backend.fetch(input, init),
    );
    const { wrapper } = await openPage();

    expect(wrapper.find('[data-testid="feedback-comment"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-read-only"]').exists()).toBe(true);
  });

  it('moves the status on, and tells the site page so the count follows', async () => {
    const { wrapper } = await openPage();

    await wrapper.find('[data-testid="feedback-mark-resolved"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-mark-new"]').exists()).toBe(true);
    expect(wrapper.emitted('changed')).toHaveLength(1);
    expect(backend.data.feedback.find((item) => item.id === ID)!.status).toBe('resolved');
  });

  it('goes back to the list as it was filtered once the item is deleted, in place of the page', async () => {
    const { wrapper, router } = await openPage(`${LIST}/${ID}?status=all`, [LIST]);

    await wrapper.find('[data-testid="feedback-delete"]').trigger('click');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    expect(backend.data.feedback.some((item) => item.id === ID)).toBe(false);
    expect(wrapper.emitted('changed')).toHaveLength(1);
    expect(router.currentRoute.value.fullPath).toBe(`${LIST}?status=all`);
    expect(router.options.history.state.toonFeedbackDeleted).toBe(true);
    // The page of the deleted item is gone from the history.
    router.back();
    await untilIdle();
    expect(router.currentRoute.value.fullPath).toBe(LIST);
  });

  it('stays and says so when deleting fails', async () => {
    const { wrapper, router } = await openPage();
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === 'DELETE' ? serverErrorFetch()(input, init) : backend.fetch(input, init),
    );

    await wrapper.find('[data-testid="feedback-delete"]').trigger('click');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('nldd-notification[variant="critical"]').attributes('text')).toBe('Feedback niet verwijderd');
    expect(wrapper.emitted('changed')).toBeUndefined();
    expect(router.currentRoute.value.params.item).toBe(ID);
  });
});

import '@nldd/design-system';

import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import { createMemoryHistory, createRouter, type Router } from 'vue-router';

import { expectNoAxeViolations } from '../../tests/a11y';
import { makeMockBackend, type MockBackend } from '../../tests/mock-api';
import { _resetAuditPurgeHold } from '@/composables/auditPurgeHold';
import { _resetCurrentMemberCache, fetchCurrentMember } from '@/composables/currentMember';
import { formatTimestamp } from '@/format';
import { _setLocaleForTest } from '@/i18n';

import AuditPurgeHold from './AuditPurgeHold.vue';
import AuditPurgeWarning from './AuditPurgeWarning.vue';
import { fireDetailEvent } from './site/testHelpers';

// jsdom has no dialog: nldd-modal-dialog calls showModal() and close().
{
  const proto = HTMLDialogElement.prototype;
  proto.showModal ??= function showModal(this: HTMLDialogElement) {
    this.setAttribute('open', '');
  };
  proto.close ??= function close(this: HTMLDialogElement) {
    this.removeAttribute('open');
  };
}

const PURGE_HOLD = '/-/api/v1/platform/audit/purge-hold';
const STARTED_AT = '2026-11-14T09:30:00Z';
const HOLD = { reason: 'ticket 4521', startedAt: STARTED_AT, startedByName: 'Bea Heerder' };

let backend: MockBackend;
let router: Router;
/** Every request the page made, as `METHOD path`. */
let requests: string[];

const Page = defineComponent({ render: () => h('h1', 'Pagina') });

beforeEach(() => {
  backend = makeMockBackend();
  requests = [];
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init: RequestInit = {}) => {
    requests.push(`${init.method ?? 'GET'} ${String(input)}`);
    return backend.fetch(input, init);
  });
  _resetCurrentMemberCache();
  _resetAuditPurgeHold();
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Page },
      { path: '/-/groups', component: Page },
      { path: '/-/platform', name: 'platform', component: Page },
    ],
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

function property<T>(el: Element, name: string): T {
  return (el as unknown as Record<string, T>)[name];
}

function purgeReads(): number {
  return requests.filter((request) => request === `GET ${PURGE_HOLD}`).length;
}

/** Mounts the warning the way the shell does: before the session is known. */
async function mountAt(path: string, component = AuditPurgeWarning) {
  await router.push(path);
  await router.isReady();
  const wrapper = mount(component, {
    attachTo: document.body,
    global: { plugins: [router], stubs: { teleport: true } },
  });
  await fetchCurrentMember().catch(() => null);
  await flushPromises();
  return wrapper;
}

type Wrapper = Awaited<ReturnType<typeof mountAt>>;

function warning(wrapper: Wrapper) {
  return wrapper.find('[data-testid="audit-purge-warning"]');
}

function link(wrapper: Wrapper): Element {
  return wrapper.find('[data-testid="audit-purge-warning-link"]').element;
}

describe('AuditPurgeWarning', () => {
  describe('for a platform administrator while the purge is paused', () => {
    beforeEach(() => {
      backend.data.auditPurgeHold = { ...HOLD };
    });

    it('warns since when, in a warning banner with a link to the platform page', async () => {
      const wrapper = await mountAt('/');

      expect(warning(wrapper).exists()).toBe(true);
      const banner = warning(wrapper).find('nldd-banner').element;
      expect(property<string>(banner, 'variant')).toBe('warning');
      expect(property<string>(banner, 'size')).toBe('sm');
      expect(property<string>(banner, 'text')).toBe(
        `Het opruimen van het auditlog staat sinds ${formatTimestamp(STARTED_AT)} op pauze.`,
      );
      // Not dismissible: a forgotten pause is exactly what this is for.
      expect(property<boolean>(banner, 'dismissible')).toBeFalsy();
      expect(link(wrapper).getAttribute('slot')).toBe('actions');
      expect(property<string>(link(wrapper), 'href')).toBe('/-/platform');
      expect(property<string>(link(wrapper), 'text')).toBe('Naar Platformbeheer');
      expect(property<string>(link(wrapper), 'appearance')).toBe('secondary');
    });

    it('says the same in English, with the date in English', async () => {
      _setLocaleForTest('en');
      const wrapper = await mountAt('/');

      const banner = warning(wrapper).find('nldd-banner').element;
      expect(property<string>(banner, 'text')).toBe(
        `The audit purge has been paused since ${formatTimestamp(STARTED_AT)}.`,
      );
      expect(formatTimestamp(STARTED_AT)).toMatch(/Nov/);
      expect(property<string>(link(wrapper), 'text')).toBe('Go to Platform administration');
    });

    it('reads the status once, when the session turns out to be an administrator', async () => {
      const wrapper = await mountAt('/');
      await router.push('/-/groups');
      await flushPromises();

      expect(warning(wrapper).exists()).toBe(true);
      expect(purgeReads()).toBe(1);
    });

    it('stays away on the platform page, which shows the pause itself, and is back after it', async () => {
      const wrapper = await mountAt('/-/platform');
      expect(warning(wrapper).exists()).toBe(false);
      expect(purgeReads()).toBe(1);

      await router.push('/');
      await flushPromises();
      expect(warning(wrapper).exists()).toBe(true);
      expect(purgeReads()).toBe(1);
    });

    it('goes to the platform page inside the app on a plain click', async () => {
      const wrapper = await mountAt('/');

      const click = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
      link(wrapper).dispatchEvent(click);
      await flushPromises();

      expect(click.defaultPrevented).toBe(true);
      expect(router.currentRoute.value.path).toBe('/-/platform');
      expect(warning(wrapper).exists()).toBe(false);
    });

    it.each([
      ['a modifier key', { ctrlKey: true }],
      ['the middle button', { button: 1 }],
    ])('leaves a click with %s to the browser', async (_name, init) => {
      const wrapper = await mountAt('/');

      const click = new MouseEvent('click', { bubbles: true, cancelable: true, ...init });
      link(wrapper).dispatchEvent(click);
      await flushPromises();

      expect(click.defaultPrevented).toBe(false);
      expect(router.currentRoute.value.path).toBe('/');
    });

    it('has no axe violations', async () => {
      const wrapper = await mountAt('/');

      await expectNoAxeViolations(wrapper.element.parentElement!);
    });
  });

  it('shows nothing while the purge runs', async () => {
    const wrapper = await mountAt('/');

    expect(purgeReads()).toBe(1);
    expect(warning(wrapper).exists()).toBe(false);
    expect(wrapper.find('nldd-banner').exists()).toBe(false);
  });

  it('neither shows nor asks anything for a member who is not a platform administrator', async () => {
    backend.data.auditPurgeHold = { ...HOLD };
    backend.data.loggedInMemberId = 'lid-3';

    const wrapper = await mountAt('/');

    expect(requests).toContain('GET /-/api/v1/me');
    expect(purgeReads()).toBe(0);
    expect(warning(wrapper).exists()).toBe(false);
  });

  it('neither shows nor asks anything without a session', async () => {
    backend.data.auditPurgeHold = { ...HOLD };
    backend.data.loggedInMemberId = null;

    const wrapper = await mountAt('/');

    expect(purgeReads()).toBe(0);
    expect(warning(wrapper).exists()).toBe(false);
  });

  it('shows no warning and no error when the status cannot be read', async () => {
    backend.data.auditPurgeHold = { ...HOLD };
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init: RequestInit = {}) => {
      requests.push(`${init.method ?? 'GET'} ${String(input)}`);
      if (String(input) === PURGE_HOLD) return Promise.reject(new TypeError('Failed to fetch'));
      return backend.fetch(input, init);
    });

    const wrapper = await mountAt('/');

    expect(purgeReads()).toBe(1);
    expect(warning(wrapper).exists()).toBe(false);
    expect(wrapper.find('nldd-banner').exists()).toBe(false);
  });

  it('follows a pause and a resume on the switch without a reload or a second read', async () => {
    const Both = defineComponent({
      render: () => [h(AuditPurgeWarning), h(AuditPurgeHold)],
    });
    // As on the platform page: the router guard has read the session first.
    await fetchCurrentMember();
    const wrapper = await mountAt('/', Both);
    expect(warning(wrapper).exists()).toBe(false);

    async function switchWith(reason: string): Promise<void> {
      fireDetailEvent(wrapper.find('[data-testid="audit-purge-reason"]').element, 'input', {
        value: reason,
      });
      await wrapper.find('nldd-form').trigger('submit');
      await flushPromises();
      await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
      await flushPromises();
    }

    await switchWith('incident 2026-091');
    expect(warning(wrapper).exists()).toBe(true);
    expect(property<string>(warning(wrapper).find('nldd-banner').element, 'text')).toContain(
      'op pauze',
    );

    await switchWith('incident afgehandeld, ticket 4521');
    expect(warning(wrapper).exists()).toBe(false);

    expect(purgeReads()).toBe(1);
  });
});

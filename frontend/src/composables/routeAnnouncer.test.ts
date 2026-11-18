import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h, onMounted, ref } from 'vue';
import { createMemoryHistory, createRouter, RouterView, type Router } from 'vue-router';

import { stayOnNextMove, useRouteAnnouncer } from './routeAnnouncer';

let wrapper: ReturnType<typeof mount> | null = null;

afterEach(() => {
  vi.restoreAllMocks();
  wrapper?.unmount();
  wrapper = null;
  document.body.innerHTML = '';
  document.title = '';
});

const Page = (title: string) =>
  defineComponent({ render: () => h('div', [h('h1', title), h('button', 'iets')]) });

/** A page whose heading only shows up once its "data" has arrived. */
const SlowPage = defineComponent({
  setup() {
    const loaded = ref(false);
    const label = ref('Laden');
    // Something changes in the page before the heading arrives.
    onMounted(() => setTimeout(() => (label.value = 'Nog even'), 10));
    onMounted(() => setTimeout(() => (loaded.value = true), 40));
    return () => h('div', loaded.value ? [h('h1', 'Traag')] : [h('p', label.value)]);
  },
});

const Tab = (name: string) => defineComponent({ render: () => h('p', name) });

/** A page inside a tab whose heading names its item, once that has loaded. */
const SlowItem = defineComponent({
  setup() {
    const loaded = ref(false);
    onMounted(() => setTimeout(() => (loaded.value = true), 40));
    return () => h('section', loaded.value ? [h('h2', 'Feedback op Contact')] : [h('p', 'Laden')]);
  },
});

const SitePage = defineComponent({
  render: () =>
    h('div', [
      h('h1', 'Een site'),
      h('div', { 'data-tab-panel': '' }, [h(RouterView)]),
    ]),
});

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Page('Overzicht') },
      { path: '/groups', component: Page('Groepen') },
      { path: '/slow', component: SlowPage },
      { path: '/nothing', component: Tab('zonder kop') },
      {
        path: '/:group/:site',
        component: SitePage,
        children: [
          { path: '', component: Tab('overzicht') },
          { path: 'versions', component: Tab('versies') },
          { path: 'feedback/:item', component: SlowItem, meta: { panelHeading: true } },
        ],
      },
    ],
  });
}

async function mountShell(router: Router, start = '/') {
  await router.push(start);
  await router.isReady();
  const Shell = defineComponent({
    setup() {
      const message = useRouteAnnouncer(router);
      return () =>
        h('div', [
          h('div', { id: 'main-content' }, [h(RouterView)]),
          h('div', { role: 'status', 'data-testid': 'status' }, message.value),
        ]);
    },
  });
  wrapper = mount(Shell, { global: { plugins: [router] }, attachTo: document.body });
  await flushPromises();
  return wrapper;
}

const status = () => document.querySelector('[data-testid="status"]')!.textContent;

describe('useRouteAnnouncer', () => {
  it('moves focus to the heading of the new page and announces its title', async () => {
    const router = makeRouter();
    await mountShell(router);
    document.title = 'Groepen - Toon';

    await router.push('/groups');
    await flushPromises();

    expect(document.activeElement).toBe(document.querySelector('h1'));
    expect(document.activeElement!.textContent).toBe('Groepen');
    expect(document.activeElement!.getAttribute('tabindex')).toBe('-1');
    expect(status()).toBe('Groepen - Toon');
  });

  it('leaves the first load alone', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: Page('Overzicht') }],
    });
    const Shell = defineComponent({
      setup() {
        const message = useRouteAnnouncer(router);
        return () =>
          h('div', [
            h('div', { id: 'main-content' }, [h(RouterView)]),
            h('div', { role: 'status', 'data-testid': 'status' }, message.value),
          ]);
      },
    });
    wrapper = mount(Shell, { global: { plugins: [router] }, attachTo: document.body });
    await router.isReady();
    await flushPromises();

    expect(document.activeElement).toBe(document.body);
    expect(status()).toBe('');
  });

  it('waits for a heading that only appears once the page has loaded', async () => {
    const router = makeRouter();
    await mountShell(router);
    document.title = 'Traag - Toon';

    await router.push('/slow');
    await flushPromises();
    expect(document.querySelector('h1')).toBeNull();
    expect(status()).toBe('');

    await new Promise((resolve) => setTimeout(resolve, 80));
    await flushPromises();

    expect(document.activeElement).toBe(document.querySelector('h1'));
    expect(status()).toBe('Traag - Toon');
  });

  it('stops waiting for a heading when the visitor navigates on', async () => {
    const router = makeRouter();
    await mountShell(router);

    await router.push('/slow');
    await flushPromises();
    await router.push('/groups');
    await flushPromises();
    await new Promise((resolve) => setTimeout(resolve, 80));
    await flushPromises();

    expect(document.activeElement!.textContent).toBe('Groepen');
  });

  it('moves focus to the tab panel, not the heading, when only the tab changes', async () => {
    const router = makeRouter();
    await mountShell(router, '/team/website');
    document.title = 'Een site - Versies - Toon';

    await router.push('/team/website/versions');
    await flushPromises();

    const panel = document.querySelector('[data-tab-panel]');
    expect(document.activeElement).toBe(panel);
    expect(status()).toBe('Een site - Versies - Toon');
  });

  it('moves focus to the heading of a page inside a tab once it names its item', async () => {
    const router = makeRouter();
    await mountShell(router, '/team/website/versions');
    document.title = 'Feedback op Contact - Een site - Toon';

    await router.push('/team/website/feedback/fb-1');
    await flushPromises();
    // Not to the panel meanwhile: the heading is where it lands.
    expect(document.activeElement).toBe(document.body);

    await new Promise((resolve) => setTimeout(resolve, 80));
    await flushPromises();

    const heading = document.querySelector('[data-tab-panel] h2');
    expect(document.activeElement).toBe(heading);
    expect(heading!.getAttribute('tabindex')).toBe('-1');
    expect(status()).toBe('Feedback op Contact - Een site - Toon');
  });

  it('tries once more a frame later when the heading takes no focus yet', async () => {
    // As a heading slotted into an nldd-title before that has drawn its slot.
    const focus = HTMLElement.prototype.focus;
    let refused = 0;
    vi.spyOn(HTMLElement.prototype, 'focus').mockImplementation(function (this: HTMLElement, options) {
      if (this.tagName === 'H2' && refused === 0) {
        refused += 1;
        return;
      }
      focus.call(this, options);
    });
    const router = makeRouter();
    await mountShell(router, '/team/website/versions');

    await router.push('/team/website/feedback/fb-1');
    await new Promise((resolve) => setTimeout(resolve, 120));
    await flushPromises();

    expect(refused).toBe(1);
    expect(document.activeElement).toBe(document.querySelector('[data-tab-panel] h2'));
  });

  it('drops that second try when the visitor navigates on first', async () => {
    vi.spyOn(HTMLElement.prototype, 'focus').mockImplementation(() => undefined);
    const request = vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(42);
    const cancel = vi.spyOn(window, 'cancelAnimationFrame');
    const router = makeRouter();
    await mountShell(router);

    await router.push('/groups');
    await flushPromises();
    expect(request).toHaveBeenCalledTimes(1);

    await router.push('/');
    await flushPromises();
    expect(cancel).toHaveBeenCalledWith(42);
  });

  it('treats another site as another page: focus goes to the heading', async () => {
    const router = makeRouter();
    await mountShell(router, '/team/website');

    await router.push('/team/other');
    await flushPromises();

    expect(document.activeElement).toBe(document.querySelector('h1'));
  });

  it('leaves focus where it is when the page moves its own address, once', async () => {
    const router = makeRouter();
    await mountShell(router, '/team/website');
    const button = document.createElement('button');
    document.querySelector('[data-tab-panel]')!.append(button);
    button.focus();

    stayOnNextMove();
    await router.replace('/team/renamed');
    await flushPromises();

    expect(document.activeElement).toBe(button);
    expect(status()).toBe('');

    await router.push('/groups');
    await flushPromises();

    expect(document.activeElement!.textContent).toBe('Groepen');
  });

  it('does nothing when the navigation failed', async () => {
    const router = makeRouter();
    await mountShell(router);

    await router.push('/');
    await flushPromises();

    expect(document.activeElement).toBe(document.body);
    expect(status()).toBe('');
  });

  it('stops listening when the shell goes away', async () => {
    const router = makeRouter();
    const shell = await mountShell(router);
    shell.unmount();
    wrapper = null;

    await router.push('/groups');
    await flushPromises();

    expect(document.activeElement).toBe(document.body);
  });
});

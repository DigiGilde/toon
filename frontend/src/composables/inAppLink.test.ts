import { flushPromises, mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import { defineComponent, h } from 'vue';
import { createMemoryHistory, createRouter } from 'vue-router';

import { useInAppLink } from './inAppLink';

const Empty = defineComponent({ render: () => null });
const TARGET = '/team-aurora/website/feedback?target=live';

async function mountLink() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/team-aurora/website/previews', component: Empty },
      { path: '/team-aurora/website/feedback', component: Empty },
    ],
  });
  await router.push('/team-aurora/website/previews');
  const Link = defineComponent({
    setup() {
      const follow = useInAppLink();
      return () => h('a', { href: TARGET, onClick: (event: MouseEvent) => follow(event, TARGET) });
    },
  });
  const wrapper = mount(Link, { global: { plugins: [router] } });
  return { wrapper, router };
}

function click(target: Element, init: MouseEventInit = {}): MouseEvent {
  const event = new MouseEvent('click', { bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

describe('useInAppLink', () => {
  it('takes a plain left click into the router instead of a page load', async () => {
    const { wrapper, router } = await mountLink();

    const event = click(wrapper.element);
    await flushPromises();

    expect(event.defaultPrevented).toBe(true);
    expect(router.currentRoute.value.fullPath).toBe(TARGET);
  });

  it('leaves a click with a modifier or another button to the browser', async () => {
    const { wrapper, router } = await mountLink();

    for (const init of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
      const event = click(wrapper.element, init);
      expect(event.defaultPrevented, JSON.stringify(init)).toBe(false);
    }
    await flushPromises();
    expect(router.currentRoute.value.path).toBe('/team-aurora/website/previews');
  });

  it('leaves a click that something before it already handled', async () => {
    const { wrapper, router } = await mountLink();
    const event = new MouseEvent('click', { cancelable: true });
    event.preventDefault();

    wrapper.element.dispatchEvent(event);
    await flushPromises();

    expect(router.currentRoute.value.path).toBe('/team-aurora/website/previews');
  });
});

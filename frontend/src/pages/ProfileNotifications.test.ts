import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter } from 'vue-router';

import { makeMockBackend, type MockBackend } from '../../tests/mock-api';
import { makeNotificationsMock } from '../../tests/notifications-mock';
import { untilIdle } from '@/components/site/testHelpers';
import { _resetBreadcrumbs } from '@/composables/breadcrumbs';
import { _resetCurrentMemberCache } from '@/composables/currentMember';
import { _setLocaleForTest } from '@/i18n';
import Profile from './Profile.vue';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
  _resetBreadcrumbs();
  _resetCurrentMemberCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
  _setLocaleForTest('nl');
});

async function mountProfile() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/-/profile', component: Profile }],
  });
  await router.push('/-/profile');
  await router.isReady();
  const wrapper = mount(Profile, { global: { plugins: [router], stubs: { teleport: true } } });
  await untilIdle();
  return wrapper;
}

describe('Profile: email about new feedback', () => {
  it('puts the switch of the profile between the language and the sessions', async () => {
    vi.stubGlobal('fetch', makeNotificationsMock(backend.fetch).fetch);

    const wrapper = await mountProfile();

    const html = wrapper.html();
    expect(wrapper.find('#heading-feedback-email-profile').text()).toBe('E-mail over nieuwe feedback');
    expect(html.indexOf('heading-feedback-email-profile')).toBeGreaterThan(html.indexOf('language-choice'));
    expect(html.indexOf('heading-feedback-email-profile')).toBeLessThan(html.indexOf('heading-sessions'));
    expect(wrapper.find('[data-testid="feedback-email-switch"]').attributes('checked')).toBeUndefined();
  });

  it('shows nothing of it while the installation sends no email', async () => {
    const wrapper = await mountProfile();

    expect(wrapper.find('#heading-feedback-email-profile').exists()).toBe(false);
  });
});

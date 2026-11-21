import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '../../../tests/mock-api';
import { makeNotificationsMock } from '../../../tests/notifications-mock';
import { _resetCurrentMemberCache } from '@/composables/currentMember';
import { _setLocaleForTest } from '@/i18n';

import TabFeedback from './TabFeedback.vue';
import { feedbackRouter, provideSiteGroup, untilIdle } from './testHelpers';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', makeNotificationsMock(backend.fetch).fetch);
  _resetCurrentMemberCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
  _setLocaleForTest('nl');
});

async function mountTab() {
  const wrapper = mount(TabFeedback, {
    props: { group: 'team-aurora', site: 'website', contentBase: MOCK_CONTENT_BASE },
    global: { stubs: { teleport: true }, provide: provideSiteGroup(backend), plugins: [feedbackRouter()] },
  });
  await untilIdle();
  return wrapper;
}

describe('TabFeedback: notifications about new feedback', () => {
  it('puts the email choice and, for a site admin, the Mattermost channel between the settings and the list', async () => {
    const wrapper = await mountTab();

    const html = wrapper.html();
    expect(wrapper.find('#heading-feedback-email-site').exists()).toBe(true);
    expect(wrapper.find('#heading-feedback-webhook').exists()).toBe(true);
    expect(html.indexOf('heading-feedback-email-site')).toBeGreaterThan(html.indexOf('heading-feedback-settings'));
    expect(html.indexOf('heading-feedback-webhook')).toBeLessThan(html.indexOf('data-testid="feedback-list'));
  });

  it('gives a reader the email choice and no Mattermost channel', async () => {
    backend.data.siteRoles.push({
      groupSlug: 'team-aurora',
      siteSlug: 'website',
      identifier: 'sanne@voorbeeld.nl',
      role: 'reader',
    });
    backend.data.loggedInMemberId = 'lid-6';

    const wrapper = await mountTab();

    expect(wrapper.find('#heading-feedback-email-site').exists()).toBe(true);
    expect(wrapper.find('#heading-feedback-webhook').exists()).toBe(false);
  });
});

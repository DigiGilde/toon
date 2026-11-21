import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, MOCK_CONTENT_BASE } from '../../../tests/mock-api';
import { makeNotificationsMock, type NotificationsMock } from '../../../tests/notifications-mock';
import { untilIdle } from '@/components/site/testHelpers';
import { _setLocaleForTest } from '@/i18n';
import TabSettings from './TabSettings.vue';

let mock: NotificationsMock;

beforeEach(() => {
  mock = makeNotificationsMock(makeMockBackend().fetch);
  vi.stubGlobal('fetch', mock.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
  _setLocaleForTest('nl');
});

async function mountTab(canAdmin: boolean) {
  const wrapper = mount(TabSettings, {
    props: {
      group: 'team-aurora',
      groupName: 'Team Aurora',
      access: { base: 'public', keys: false, invitees: false },
      sites: [],
      previousSlugs: [],
      contentBase: MOCK_CONTENT_BASE,
      redirectDays: 30,
      canAdmin,
      canDelete: false,
      canRename: false,
      canChangeAddress: false,
    },
    global: { stubs: { teleport: true } },
  });
  await untilIdle();
  return wrapper;
}

describe('group TabSettings: notifications about new feedback', () => {
  it('gives every member their email choice for the group, below the default access', async () => {
    const wrapper = await mountTab(false);

    const html = wrapper.html();
    expect(wrapper.find('#heading-feedback-email-group').text()).toBe('E-mail over nieuwe feedback');
    expect(html.indexOf('heading-feedback-email-group')).toBeGreaterThan(html.indexOf('default-access-group'));
    expect(wrapper.find('[data-testid="feedback-webhook"]').exists()).toBe(false);
    expect(mock.calls).toEqual(['/-/api/v1/groups/team-aurora/feedback-email']);
  });

  it('gives a group admin the Mattermost channel as well', async () => {
    const wrapper = await mountTab(true);

    expect(wrapper.find('#heading-feedback-webhook').text()).toBe('Mattermost-kanaal');
    expect(mock.calls).toContain('/-/api/v1/groups/team-aurora/feedback-webhook');
  });
});

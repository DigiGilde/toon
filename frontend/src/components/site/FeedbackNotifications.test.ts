import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend } from '../../../tests/mock-api';
import { makeNotificationsMock, type NotificationsMock } from '../../../tests/notifications-mock';
import { _setLocaleForTest } from '@/i18n';

import FeedbackNotifications from './FeedbackNotifications.vue';
import { untilIdle } from './testHelpers';

let mock: NotificationsMock;

beforeEach(() => {
  mock = makeNotificationsMock(makeMockBackend().fetch);
  vi.stubGlobal('fetch', mock.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
  _setLocaleForTest('nl');
});

async function mountFor(canAdmin: boolean) {
  const wrapper = mount(FeedbackNotifications, {
    props: { group: 'team-aurora', site: 'website', canAdmin },
    global: { stubs: { teleport: true } },
  });
  await untilIdle();
  return wrapper;
}

describe('FeedbackNotifications: the Feedback tab', () => {
  it('gives every member the email choice of the site, and no webhook', async () => {
    const wrapper = await mountFor(false);

    expect(wrapper.find('[data-testid="feedback-email-site"]').exists()).toBe(true);
    expect(mock.calls).toEqual(['/-/api/v1/sites/team-aurora/website/feedback-email']);
    expect(wrapper.find('[data-testid="feedback-webhook"]').exists()).toBe(false);
  });

  it('gives an admin the Mattermost channel of the site as well', async () => {
    mock.email.site = { available: true, enabled: null, effective: false, source: 'default' };
    mock.webhook.site = { ...mock.webhook.site, groupHost: 'groep.example.nl' };

    const wrapper = await mountFor(true);

    expect(wrapper.find('[data-testid="feedback-email-site"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-webhook"]').exists()).toBe(true);
    expect(mock.calls).toContain('/-/api/v1/sites/team-aurora/website/feedback-webhook');
  });

  it('shows neither where the installation sends no email and allows no webhooks', async () => {
    mock.email.site = { ...mock.email.site, available: false };
    mock.webhook.site = { ...mock.webhook.site, available: false };

    const wrapper = await mountFor(true);

    expect(wrapper.find('section').exists()).toBe(false);
  });
});

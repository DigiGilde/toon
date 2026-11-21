import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './client';
import { makeMockBackend } from '../../tests/mock-api';
import { makeNotificationsMock, type NotificationsMock } from '../../tests/notifications-mock';
import * as toon from './toon';

let mock: NotificationsMock;

beforeEach(() => {
  mock = makeNotificationsMock(makeMockBackend().fetch);
  vi.stubGlobal('fetch', mock.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the email choice about new feedback', () => {
  it('reads and writes at profile level', async () => {
    mock.email.me = { available: true, enabled: true, effective: true, source: 'profile' };

    expect(await toon.myFeedbackEmail()).toEqual(mock.email.me);
    const saved = await toon.setMyFeedbackEmail(false);

    expect(mock.calls).toEqual(['/-/api/v1/me/feedback-email', '/-/api/v1/me/feedback-email']);
    expect(mock.writes).toEqual([['/-/api/v1/me/feedback-email', { enabled: false }]]);
    expect(saved.enabled).toBe(false);
  });

  it('reads and writes at group level, with null to take the choice back', async () => {
    await toon.groupFeedbackEmail('team-aurora');
    await toon.setGroupFeedbackEmail('team-aurora', true);
    await toon.setGroupFeedbackEmail('team-aurora', null);

    expect(mock.calls).toEqual(Array(3).fill('/-/api/v1/groups/team-aurora/feedback-email'));
    expect(mock.writes.map(([, body]) => body)).toEqual([{ enabled: true }, { enabled: null }]);
  });

  it('reads and writes at site level, every part of the address encoded', async () => {
    await toon.siteFeedbackEmail('team-aurora', 'website');
    await toon.setSiteFeedbackEmail('team-aurora', 'a/b', true);

    expect(mock.calls).toEqual([
      '/-/api/v1/sites/team-aurora/website/feedback-email',
      '/-/api/v1/sites/team-aurora/a%2Fb/feedback-email',
    ]);
    expect(mock.writes).toEqual([['/-/api/v1/sites/team-aurora/a%2Fb/feedback-email', { enabled: true }]]);
  });

  it('hands a refusal on as the problem it is', async () => {
    mock.refuse.set('/me/feedback-email', {
      status: 409,
      body: { title: 'Niet beschikbaar', code: 'FEEDBACK_EMAIL_UNAVAILABLE' },
    });

    const error = await toon.setMyFeedbackEmail(true).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).problem).toMatchObject({ status: 409, code: 'FEEDBACK_EMAIL_UNAVAILABLE' });
  });
});

describe('the Mattermost webhook', () => {
  it('reads, sets and removes it on a group', async () => {
    expect(await toon.groupFeedbackWebhook('team-aurora')).toMatchObject({ configured: false });
    const saved = await toon.setGroupFeedbackWebhook('team-aurora', 'https://chat.example.nl/hooks/abc', 'en');
    await toon.deleteGroupFeedbackWebhook('team-aurora');

    expect(saved).toMatchObject({ configured: true, host: 'chat.example.nl', language: 'en' });
    expect(mock.writes[0]).toEqual([
      '/-/api/v1/groups/team-aurora/feedback-webhook',
      { url: 'https://chat.example.nl/hooks/abc', language: 'en' },
    ]);
    expect(mock.writes[1]).toEqual(['/-/api/v1/groups/team-aurora/feedback-webhook', null]);
    expect(mock.webhook.group.configured).toBe(false);
  });

  it('reads, sets and removes it on a site, the language optional', async () => {
    await toon.siteFeedbackWebhook('team-aurora', 'website');
    await toon.setSiteFeedbackWebhook('team-aurora', 'website', 'https://chat.example.nl/hooks/abc');
    await toon.deleteSiteFeedbackWebhook('team-aurora', 'website');

    expect(mock.calls).toEqual(Array(3).fill('/-/api/v1/sites/team-aurora/website/feedback-webhook'));
    expect(mock.writes[0]![1]).toEqual({ url: 'https://chat.example.nl/hooks/abc' });
  });
});

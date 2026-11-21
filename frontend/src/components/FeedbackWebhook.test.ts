import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { nextTick } from 'vue';

import { makeMockBackend } from '../../tests/mock-api';
import { makeNotificationsMock, type NotificationsMock } from '../../tests/notifications-mock';
import type { FeedbackWebhook as Webhook } from '@/api/types';
import { _setLocaleForTest } from '@/i18n';

import ConfirmModal from './ConfirmModal.vue';
import FeedbackWebhook from './FeedbackWebhook.vue';
import { fireDetailEvent, untilIdle } from './site/testHelpers';

let mock: NotificationsMock;

const URL_OK = 'https://chat.example.nl/hooks/abcdef123456';

const OWN: Webhook = {
  available: true,
  configured: true,
  host: 'chat.example.nl',
  language: 'en',
  setAt: '2026-11-20T10:00:00Z',
  groupHost: null,
};

beforeEach(() => {
  mock = makeNotificationsMock(makeMockBackend().fetch);
  vi.stubGlobal('fetch', mock.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
  _setLocaleForTest('nl');
});

async function mountFor(level: 'group' | 'site') {
  const wrapper = mount(FeedbackWebhook, {
    props: level === 'site' ? { group: 'team-aurora', site: 'website' } : { group: 'team-aurora' },
    global: { stubs: { teleport: true } },
  });
  await untilIdle();
  return wrapper;
}

type Wrapper = Awaited<ReturnType<typeof mountFor>>;

function status(wrapper: Wrapper): string {
  return wrapper.find('[data-testid="feedback-webhook-status"]').text();
}

function type(wrapper: Wrapper, value: string): void {
  fireDetailEvent(wrapper.find('[data-testid="feedback-webhook-url"]').element, 'input', { value });
}

function save(wrapper: Wrapper) {
  return wrapper.find('[data-testid="feedback-webhook-form"]').trigger('submit');
}

function critical(wrapper: Wrapper) {
  return wrapper.find('nldd-notification[variant="critical"]');
}

describe('FeedbackWebhook: when there is nothing to manage', () => {
  it('draws nothing while the installation allows no webhooks', async () => {
    mock.webhook.group = { ...mock.webhook.group, available: false };

    const wrapper = await mountFor('group');

    expect(wrapper.find('section').exists()).toBe(false);
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('says so when the channel cannot be loaded', async () => {
    mock.refuse.set('/feedback-webhook', { status: 403, body: { title: 'Geen toegang', detail: 'Alleen beheerders.' } });

    const wrapper = await mountFor('site');

    expect(wrapper.find('section').exists()).toBe(false);
    expect(critical(wrapper).attributes('text')).toBe('Kon het Mattermost-kanaal niet laden');
    expect(critical(wrapper).attributes('supporting-text')).toBe('Alleen beheerders.');
  });

  it('gives its own sentence when the network is down', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));

    const wrapper = await mountFor('group');

    expect(critical(wrapper).attributes('supporting-text')).toBe('Laad de pagina opnieuw.');
  });
});

describe('FeedbackWebhook: what it says', () => {
  it('says there is no channel, and offers a field, a language and Save but no Remove', async () => {
    const wrapper = await mountFor('group');

    expect(wrapper.find('section').attributes('aria-labelledby')).toBe('heading-feedback-webhook');
    expect(wrapper.find('h2').text()).toBe('Mattermost-kanaal');
    expect(status(wrapper)).toBe('Geen Mattermost-kanaal.');
    expect(wrapper.find('[data-testid="feedback-webhook-url"]').attributes('value')).toBe('');
    expect(wrapper.find('[data-testid="feedback-webhook-save"]').attributes('text')).toBe('Webhook opslaan');
    expect(wrapper.find('[data-testid="feedback-webhook-remove"]').exists()).toBe(false);
    expect(wrapper.find('nldd-form-field-help-text').text()).not.toContain('vervangt');
  });

  it('names the host and the language of the posts of its own channel', async () => {
    mock.webhook.site = OWN;

    const wrapper = await mountFor('site');

    expect(status(wrapper)).toBe(
      'Berichten gaan naar een Mattermost-kanaal op chat.example.nl. Taal van de berichten: Engels.',
    );
    expect(wrapper.find('[data-testid="feedback-webhook-remove"]').attributes('text')).toBe('Webhook verwijderen');
    expect(wrapper.find('nldd-form-field-help-text').text()).toContain('Opslaan vervangt de huidige webhook.');
    expect(wrapper.find('[data-testid="feedback-webhook-language-en"]').attributes('checked')).toBeDefined();
    expect(wrapper.find('[data-testid="feedback-webhook-language-nl"]').attributes('checked')).toBeUndefined();
  });

  it("names the group's host on a site without a channel of its own", async () => {
    mock.webhook.site = { ...mock.webhook.site, groupHost: 'groep.example.nl' };

    const wrapper = await mountFor('site');

    expect(status(wrapper)).toBe(
      'Deze site heeft geen eigen kanaal. Berichten gaan naar het kanaal van de groep op groep.example.nl.',
    );
    expect(wrapper.find('[data-testid="feedback-webhook-remove"]').exists()).toBe(false);
  });

  it('speaks English when the interface does, and starts the language there', async () => {
    _setLocaleForTest('en');

    const wrapper = await mountFor('group');

    expect(wrapper.find('h2').text()).toBe('Mattermost channel');
    expect(status(wrapper)).toBe('No Mattermost channel.');
    expect(wrapper.find('[data-testid="feedback-webhook-language-en"]').attributes('checked')).toBeDefined();
  });

  it('starts the language at the Dutch interface when the channel has none yet', async () => {
    const wrapper = await mountFor('group');

    expect(wrapper.find('[data-testid="feedback-webhook-language-nl"]').attributes('checked')).toBeDefined();
  });

  it('warns that the text of every item lands in the channel and stays there', async () => {
    const wrapper = await mountFor('group');

    const warning = wrapper.find('[data-testid="feedback-webhook-warning"]');
    expect(warning.attributes('variant')).toBe('warning');
    expect(warning.attributes('text')).toBe('Iedereen in het kanaal leest de feedback mee');
    const detail = warning.attributes('supporting-text')!;
    expect(detail).toContain('afgekapt op 500 tekens');
    expect(detail).toContain('zonder naam of e-mailadres van de afzender');
    expect(detail).toContain('als de feedback in Toon wordt verwijderd');
    expect(detail).not.toContain(';');
  });

  it('warns in English too', async () => {
    _setLocaleForTest('en');

    const detail = (await mountFor('group'))
      .find('[data-testid="feedback-webhook-warning"]')
      .attributes('supporting-text')!;

    expect(detail).toContain('cut at 500 characters');
    expect(detail).toContain('stays there after the feedback is deleted in Toon');
  });
});

describe('FeedbackWebhook: saving', () => {
  it('holds the Save button back until there is a URL', async () => {
    const wrapper = await mountFor('group');
    const button = () => wrapper.find('[data-testid="feedback-webhook-save"]');
    expect(button().attributes('disabled')).toBeDefined();

    type(wrapper, URL_OK);
    await nextTick();
    expect(button().attributes('disabled')).toBeUndefined();

    type(wrapper, '   ');
    await nextTick();
    expect(button().attributes('disabled')).toBeDefined();
  });

  it('sends nothing for an empty field', async () => {
    const wrapper = await mountFor('group');

    type(wrapper, '  ');
    await save(wrapper);
    await untilIdle();

    expect(mock.writes).toEqual([]);
  });

  it('saves the URL with the language, clears the field and shows the new status', async () => {
    const wrapper = await mountFor('group');

    type(wrapper, `  ${URL_OK}  `);
    await wrapper.find('[data-testid="feedback-webhook-language-en"]').trigger('change');
    await save(wrapper);
    await untilIdle();

    expect(mock.writes).toEqual([['/-/api/v1/groups/team-aurora/feedback-webhook', { url: URL_OK, language: 'en' }]]);
    expect(wrapper.find('[data-testid="feedback-webhook-url"]').attributes('value')).toBe('');
    expect(status(wrapper)).toBe(
      'Berichten gaan naar een Mattermost-kanaal op chat.example.nl. Taal van de berichten: Engels.',
    );
    const notice = wrapper.find('nldd-notification[variant="success"]');
    expect(notice.attributes('text')).toBe('Webhook opgeslagen');
    expect(notice.attributes('supporting-text')).toBe(
      'Berichten gaan nu naar een Mattermost-kanaal op chat.example.nl.',
    );
    expect(wrapper.find('[data-testid="feedback-webhook-remove"]').exists()).toBe(true);
  });

  it('saves for a site on the path of the site', async () => {
    const wrapper = await mountFor('site');

    type(wrapper, URL_OK);
    await save(wrapper);
    await untilIdle();

    expect(mock.writes).toEqual([
      ['/-/api/v1/sites/team-aurora/website/feedback-webhook', { url: URL_OK, language: 'nl' }],
    ]);
  });

  it('keeps the field and shows the refusal of the server, in its own words', async () => {
    const wrapper = await mountFor('group');
    mock.refuse.set('/feedback-webhook', {
      status: 422,
      body: { title: 'Ongeldig', detail: 'Deze host is niet toegestaan.', code: 'WEBHOOK_HOST_NOT_ALLOWED' },
    });

    type(wrapper, URL_OK);
    await save(wrapper);
    await nextTick();
    expect(wrapper.find('[data-testid="feedback-webhook-url"]').attributes('disabled')).toBeDefined();
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-webhook-url"]').attributes('disabled')).toBeUndefined();
    expect(wrapper.find('[data-testid="feedback-webhook-url"]').attributes('value')).toBe(URL_OK);
    expect(critical(wrapper).attributes('text')).toBe('Kon de webhook niet opslaan');
    expect(critical(wrapper).attributes('supporting-text')).toBe('Deze host is niet toegestaan.');
    expect(status(wrapper)).toBe('Geen Mattermost-kanaal.');
  });

  it('gives its own sentence when the network fails', async () => {
    const wrapper = await mountFor('group');
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));

    type(wrapper, URL_OK);
    await save(wrapper);
    await untilIdle();

    expect(critical(wrapper).attributes('supporting-text')).toBe('Probeer het opnieuw.');
  });

  it('sends one call while the first is on its way', async () => {
    const wrapper = await mountFor('group');

    type(wrapper, URL_OK);
    void save(wrapper);
    void save(wrapper);
    await untilIdle();

    expect(mock.writes).toHaveLength(1);
  });

  it('reads the typed value from the element when the event carries none', async () => {
    const wrapper = await mountFor('group');
    const field = wrapper.find('[data-testid="feedback-webhook-url"]');
    (field.element as HTMLInputElement).value = URL_OK;

    field.element.dispatchEvent(new CustomEvent('input'));
    await save(wrapper);
    await untilIdle();

    expect(mock.writes).toHaveLength(1);
  });
});

describe('FeedbackWebhook: removing', () => {
  it('asks first, and removes only when confirmed', async () => {
    mock.webhook.group = OWN;
    const wrapper = await mountFor('group');
    const modal = () => wrapper.findComponent(ConfirmModal);
    expect(modal().props('open')).toBe(false);

    await wrapper.find('[data-testid="feedback-webhook-remove"]').trigger('click');
    expect(modal().props('open')).toBe(true);
    expect(modal().props('title')).toBe('De webhook verwijderen?');
    expect(mock.writes).toEqual([]);

    modal().vm.$emit('close');
    await nextTick();
    expect(modal().props('open')).toBe(false);
    expect(mock.writes).toEqual([]);

    await wrapper.find('[data-testid="feedback-webhook-remove"]').trigger('click');
    modal().vm.$emit('confirm');
    await untilIdle();

    expect(mock.writes).toEqual([['/-/api/v1/groups/team-aurora/feedback-webhook', null]]);
    expect(status(wrapper)).toBe('Geen Mattermost-kanaal.');
    expect(wrapper.find('[data-testid="feedback-webhook-remove"]').exists()).toBe(false);
    expect(wrapper.find('nldd-notification[variant="success"]').attributes('text')).toBe('Webhook verwijderd');
    expect(modal().props('open')).toBe(false);
  });

  it('falls back to the channel of the group on a site, which the answer names', async () => {
    mock.webhook.site = { ...OWN, groupHost: 'groep.example.nl' };
    const wrapper = await mountFor('site');

    await wrapper.find('[data-testid="feedback-webhook-remove"]').trigger('click');
    wrapper.findComponent(ConfirmModal).vm.$emit('confirm');
    await untilIdle();

    expect(mock.writes).toEqual([['/-/api/v1/sites/team-aurora/website/feedback-webhook', null]]);
    expect(status(wrapper)).toContain('op groep.example.nl');
  });

  it('keeps the channel and shows why when removing fails', async () => {
    mock.webhook.group = OWN;
    const wrapper = await mountFor('group');
    mock.refuse.set('/feedback-webhook', {
      status: 500,
      body: { title: 'Serverfout', detail: 'Even geduld.' },
    });

    await wrapper.find('[data-testid="feedback-webhook-remove"]').trigger('click');
    wrapper.findComponent(ConfirmModal).vm.$emit('confirm');
    await untilIdle();

    expect(critical(wrapper).attributes('text')).toBe('Kon de webhook niet verwijderen');
    expect(critical(wrapper).attributes('supporting-text')).toBe('Even geduld.');
    expect(wrapper.find('[data-testid="feedback-webhook-remove"]').exists()).toBe(true);
  });

  it('gives its own sentence when the network fails while removing', async () => {
    mock.webhook.group = OWN;
    const wrapper = await mountFor('group');
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));

    await wrapper.find('[data-testid="feedback-webhook-remove"]').trigger('click');
    wrapper.findComponent(ConfirmModal).vm.$emit('confirm');
    await untilIdle();

    expect(critical(wrapper).attributes('supporting-text')).toBe('Probeer het opnieuw.');
  });
});

import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { nextTick } from 'vue';

import { makeMockBackend } from '../../tests/mock-api';
import { makeNotificationsMock, type NotificationsMock } from '../../tests/notifications-mock';
import type { FeedbackEmailSetting } from '@/api/types';
import { _setLocaleForTest } from '@/i18n';

import FeedbackEmailChoice from './FeedbackEmailChoice.vue';
import { fireDetailEvent, untilIdle } from './site/testHelpers';

let mock: NotificationsMock;

beforeEach(() => {
  mock = makeNotificationsMock(makeMockBackend().fetch);
  vi.stubGlobal('fetch', mock.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
  _setLocaleForTest('nl');
});

function setting(
  enabled: boolean | null,
  effective: boolean,
  source: FeedbackEmailSetting['source'],
): FeedbackEmailSetting {
  return { available: true, enabled, effective, source };
}

async function mountLevel(level: 'profile' | 'group' | 'site') {
  const wrapper = mount(FeedbackEmailChoice, { props: { level, group: 'team-aurora', site: 'website' } });
  await untilIdle();
  return wrapper;
}

function row(wrapper: Awaited<ReturnType<typeof mountLevel>>, choice: string) {
  return wrapper.find(`[data-testid="feedback-email-${choice}"]`);
}

function label(wrapper: Awaited<ReturnType<typeof mountLevel>>, choice: string): string | undefined {
  return row(wrapper, choice).find('nldd-title-cell').attributes('text');
}

function hint(wrapper: Awaited<ReturnType<typeof mountLevel>>, choice: string): string | undefined {
  return row(wrapper, choice).find('nldd-title-cell').attributes('supporting-text');
}

function checkedChoices(wrapper: Awaited<ReturnType<typeof mountLevel>>): string[] {
  return ['follow', 'on', 'off'].filter((choice) => row(wrapper, choice).attributes('checked') !== undefined);
}

function critical(wrapper: Awaited<ReturnType<typeof mountLevel>>) {
  return wrapper.find('nldd-notification[variant="critical"]');
}

describe('FeedbackEmailChoice: when there is nothing to choose', () => {
  it('draws nothing while the installation sends no email, and says nothing about it', async () => {
    mock.email.me = { ...mock.email.me, available: false };

    const wrapper = await mountLevel('profile');

    expect(wrapper.find('section').exists()).toBe(false);
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('says so when the choice cannot be loaded, and offers none', async () => {
    mock.refuse.set('/feedback-email', { status: 500, body: { title: 'Serverfout', detail: 'Het ging mis.' } });

    const wrapper = await mountLevel('site');

    expect(wrapper.find('section').exists()).toBe(false);
    expect(critical(wrapper).attributes('text')).toBe('Kon de e-mailkeuze niet laden');
    expect(critical(wrapper).attributes('supporting-text')).toBe('Het ging mis.');
  });

  it('gives its own sentence when the network is down', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));

    const wrapper = await mountLevel('profile');

    expect(critical(wrapper).attributes('supporting-text')).toBe('Laad de pagina opnieuw.');
  });
});

describe('FeedbackEmailChoice: the profile', () => {
  it('is one switch, off while nothing is set, under a heading that says what the email holds', async () => {
    const wrapper = await mountLevel('profile');

    const section = wrapper.find('section');
    expect(section.attributes('aria-labelledby')).toBe('heading-feedback-email-profile');
    expect(section.find('h2').text()).toBe('E-mail over nieuwe feedback');
    expect(section.find('nldd-title').text()).toContain('De tekst van de feedback en de afzender staan er nooit in');
    expect(section.find('nldd-title').text()).toContain('het adres van je login');
    const toggle = wrapper.find('[data-testid="feedback-email-switch"]');
    expect(toggle.attributes('label')).toBe('Stuur me e-mail over nieuwe feedback');
    expect(toggle.attributes('checked')).toBeUndefined();
    expect(wrapper.find('nldd-list').exists()).toBe(false);
  });

  it('reads an explicit off the same as nothing set, and an on as on', async () => {
    mock.email.me = setting(false, false, 'profile');
    expect(
      (await mountLevel('profile')).find('[data-testid="feedback-email-switch"]').attributes('checked'),
    ).toBeUndefined();

    mock.email.me = setting(true, true, 'profile');
    expect(
      (await mountLevel('profile')).find('[data-testid="feedback-email-switch"]').attributes('checked'),
    ).toBeDefined();
  });

  it('saves true on switching on and false on switching off, and says what applies', async () => {
    const wrapper = await mountLevel('profile');
    const toggle = () => wrapper.find('[data-testid="feedback-email-switch"]');

    fireDetailEvent(toggle().element, 'change', { checked: true });
    await untilIdle();
    expect(mock.writes).toEqual([['/-/api/v1/me/feedback-email', { enabled: true }]]);
    expect(toggle().attributes('checked')).toBeDefined();
    const notice = wrapper.find('nldd-notification[variant="success"]');
    expect(notice.attributes('text')).toBe('E-mailkeuze opgeslagen');
    expect(notice.attributes('supporting-text')).toBe('Je krijgt nu e-mail over nieuwe feedback.');

    fireDetailEvent(toggle().element, 'change', { checked: false });
    await untilIdle();
    expect(mock.writes[1]).toEqual(['/-/api/v1/me/feedback-email', { enabled: false }]);
    expect(toggle().attributes('checked')).toBeUndefined();
    expect(wrapper.findAll('nldd-notification[variant="success"]')[1]!.attributes('supporting-text')).toBe(
      'Je krijgt nu geen e-mail over nieuwe feedback.',
    );
  });

  it('sends nothing when the switch reports the state it already has', async () => {
    const wrapper = await mountLevel('profile');

    fireDetailEvent(wrapper.find('[data-testid="feedback-email-switch"]').element, 'change', {});
    await untilIdle();

    expect(mock.writes).toEqual([]);
  });

  it('waits for the answer, then puts the switch back and shows the refusal', async () => {
    const wrapper = await mountLevel('profile');
    mock.refuse.set('/me/feedback-email', {
      status: 409,
      body: { title: 'Niet beschikbaar', detail: 'Deze installatie stuurt geen e-mail.', code: 'FEEDBACK_EMAIL_UNAVAILABLE' },
    });

    fireDetailEvent(wrapper.find('[data-testid="feedback-email-switch"]').element, 'change', { checked: true });
    await nextTick();
    expect(wrapper.find('[data-testid="feedback-email-switch"]').attributes('checked')).toBeDefined();
    expect(wrapper.find('[data-testid="feedback-email-switch"]').attributes('disabled')).toBeDefined();
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-email-switch"]').attributes('checked')).toBeUndefined();
    expect(wrapper.find('[data-testid="feedback-email-switch"]').attributes('disabled')).toBeUndefined();
    expect(critical(wrapper).attributes('text')).toBe('Kon de e-mailkeuze niet opslaan');
    expect(critical(wrapper).attributes('supporting-text')).toBe('Deze installatie stuurt geen e-mail.');
  });

  it('puts the switch back with a sentence of its own when the network fails', async () => {
    const wrapper = await mountLevel('profile');
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));

    fireDetailEvent(wrapper.find('[data-testid="feedback-email-switch"]').element, 'change', { checked: true });
    await untilIdle();

    expect(critical(wrapper).attributes('supporting-text')).toBe('Probeer het opnieuw.');
  });

  it('speaks English when the interface does', async () => {
    _setLocaleForTest('en');

    const wrapper = await mountLevel('profile');

    expect(wrapper.find('h2').text()).toBe('Email about new feedback');
    expect(wrapper.find('[data-testid="feedback-email-switch"]').attributes('label')).toBe(
      'Email me about new feedback',
    );
  });
});

describe('FeedbackEmailChoice: a group', () => {
  it('offers follow, on and off, with follow on while the group has no choice of its own', async () => {
    const wrapper = await mountLevel('group');

    expect(wrapper.find('section').attributes('aria-labelledby')).toBe('heading-feedback-email-group');
    expect(wrapper.find('nldd-list').attributes('type')).toBe('radiogroup');
    expect(label(wrapper, 'follow')).toBe('Volg mijn profiel (nu: uit)');
    expect(label(wrapper, 'on')).toBe('Aan');
    expect(label(wrapper, 'off')).toBe('Uit');
    expect(hint(wrapper, 'on')).toContain('elke site in deze groep');
    expect(hint(wrapper, 'off')).toContain('geen e-mail');
    expect(hint(wrapper, 'follow')).toBe('Je eigen keuze voor een site in deze groep gaat voor.');
    expect(checkedChoices(wrapper)).toEqual(['follow']);
    expect(mock.calls).toEqual(['/-/api/v1/groups/team-aurora/feedback-email']);
  });

  it('names what the profile gives next to follow', async () => {
    mock.email.group = setting(null, true, 'profile');

    const wrapper = await mountLevel('group');

    expect(label(wrapper, 'follow')).toBe('Volg mijn profiel (nu: aan)');
  });

  it('asks the profile what following would give once the group is set', async () => {
    mock.email.group = setting(false, false, 'group');
    mock.email.me = setting(true, true, 'profile');

    const wrapper = await mountLevel('group');

    expect(checkedChoices(wrapper)).toEqual(['off']);
    expect(label(wrapper, 'follow')).toBe('Volg mijn profiel (nu: aan)');
    expect(mock.calls).toEqual(['/-/api/v1/groups/team-aurora/feedback-email', '/-/api/v1/me/feedback-email']);
  });

  it('names no result next to follow when the profile cannot be read', async () => {
    mock.email.group = setting(true, true, 'group');
    mock.refuse.set('/me/feedback-email', { status: 500, body: { title: 'Serverfout' } });

    const wrapper = await mountLevel('group');

    expect(checkedChoices(wrapper)).toEqual(['on']);
    expect(label(wrapper, 'follow')).toBe('Volg mijn profiel (nu: uit)');
  });

  it('saves on, off and follow as true, false and null', async () => {
    const wrapper = await mountLevel('group');

    await row(wrapper, 'on').trigger('change');
    await untilIdle();
    expect(checkedChoices(wrapper)).toEqual(['on']);

    await row(wrapper, 'off').trigger('change');
    await untilIdle();
    expect(checkedChoices(wrapper)).toEqual(['off']);

    await row(wrapper, 'follow').trigger('change');
    await untilIdle();
    expect(checkedChoices(wrapper)).toEqual(['follow']);

    expect(mock.writes.map(([path, body]) => [path, body])).toEqual([
      ['/-/api/v1/groups/team-aurora/feedback-email', { enabled: true }],
      ['/-/api/v1/groups/team-aurora/feedback-email', { enabled: false }],
      ['/-/api/v1/groups/team-aurora/feedback-email', { enabled: null }],
    ]);
    expect(wrapper.find('nldd-notification[variant="success"]').attributes('text')).toBe('E-mailkeuze opgeslagen');
  });

  it('updates what follow gives from the answer when the choice is taken back', async () => {
    mock.email.group = setting(true, true, 'group');
    mock.email.me = setting(null, false, 'default');
    const wrapper = await mountLevel('group');
    expect(label(wrapper, 'follow')).toBe('Volg mijn profiel (nu: uit)');
    mock.email.me = setting(true, true, 'profile');

    await row(wrapper, 'follow').trigger('change');
    await untilIdle();

    // The mock answers a take-back with the effective result it holds.
    expect(label(wrapper, 'follow')).toBe('Volg mijn profiel (nu: aan)');
  });

  it('sends nothing for the choice that is already made', async () => {
    const wrapper = await mountLevel('group');

    await row(wrapper, 'follow').trigger('change');
    await untilIdle();

    expect(mock.writes).toEqual([]);
  });

  it('waits for the answer, then puts the choice back and shows the refusal', async () => {
    const wrapper = await mountLevel('group');
    mock.refuse.set('/groups/team-aurora/feedback-email', {
      status: 403,
      body: { title: 'Geen toegang', detail: 'Je hebt geen rol in deze groep.' },
    });

    await row(wrapper, 'on').trigger('change');
    expect(checkedChoices(wrapper)).toEqual(['on']);
    expect(row(wrapper, 'off').attributes('disabled')).toBeDefined();
    await untilIdle();

    expect(checkedChoices(wrapper)).toEqual(['follow']);
    expect(row(wrapper, 'off').attributes('disabled')).toBeUndefined();
    expect(critical(wrapper).attributes('supporting-text')).toBe('Je hebt geen rol in deze groep.');
  });
});

describe('FeedbackEmailChoice: a site', () => {
  async function followHint(initial: FeedbackEmailSetting): Promise<string | undefined> {
    mock.email.site = initial;
    return hint(await mountLevel('site'), 'follow');
  }

  it('names the group as the source while the site has no choice and the group decides', async () => {
    expect(await followHint(setting(null, true, 'group'))).toBe('Overgenomen van de groep: aan');
  });

  it('names the profile as the source when the group has no choice', async () => {
    expect(await followHint(setting(null, false, 'profile'))).toBe('Van je profiel: uit');
  });

  it('says it is set nowhere when nothing is', async () => {
    const wrapper = await mountLevel('site');

    expect(label(wrapper, 'follow')).toBe('Volg de groep en mijn profiel');
    expect(hint(wrapper, 'follow')).toBe('Nergens ingesteld: uit');
    expect(hint(wrapper, 'on')).toBe('Je krijgt e-mail over nieuwe feedback op deze site.');
    expect(hint(wrapper, 'off')).toBe('Je krijgt geen e-mail over nieuwe feedback op deze site.');
    expect(checkedChoices(wrapper)).toEqual(['follow']);
    expect(mock.calls).toEqual(['/-/api/v1/sites/team-aurora/website/feedback-email']);
  });

  it('asks the group first once the site is set', async () => {
    mock.email.site = setting(false, false, 'site');
    mock.email.group = setting(true, true, 'group');

    const wrapper = await mountLevel('site');

    expect(checkedChoices(wrapper)).toEqual(['off']);
    expect(hint(wrapper, 'follow')).toBe('Overgenomen van de groep: aan');
    expect(mock.calls).toEqual([
      '/-/api/v1/sites/team-aurora/website/feedback-email',
      '/-/api/v1/groups/team-aurora/feedback-email',
    ]);
  });

  it('asks the profile when the group has no choice', async () => {
    mock.email.site = setting(true, true, 'site');
    mock.email.me = setting(true, true, 'profile');

    expect(hint(await mountLevel('site'), 'follow')).toBe('Van je profiel: aan');
  });

  it('goes on to the profile when the group cannot be read, as for a member with a site role only', async () => {
    mock.email.site = setting(true, true, 'site');
    mock.email.me = setting(null, false, 'default');
    mock.refuse.set('/groups/team-aurora/feedback-email', { status: 404, body: { title: 'Niet gevonden' } });

    expect(hint(await mountLevel('site'), 'follow')).toBe('Nergens ingesteld: uit');
  });

  it('names no result when the profile cannot be read either', async () => {
    mock.email.site = setting(true, true, 'site');
    mock.refuse.set('/me/feedback-email', { status: 500, body: { title: 'Serverfout' } });

    const wrapper = await mountLevel('site');

    expect(label(wrapper, 'follow')).toBe('Volg de groep en mijn profiel');
    expect(row(wrapper, 'follow').find('nldd-title-cell').attributes('supporting-text')).toBeUndefined();
  });

  it('saves a choice for the site and keeps what follow gives while the site stays set', async () => {
    mock.email.site = setting(null, true, 'group');
    const wrapper = await mountLevel('site');

    await row(wrapper, 'off').trigger('change');
    await untilIdle();

    expect(mock.writes).toEqual([['/-/api/v1/sites/team-aurora/website/feedback-email', { enabled: false }]]);
    expect(checkedChoices(wrapper)).toEqual(['off']);
    expect(hint(wrapper, 'follow')).toBe('Overgenomen van de groep: aan');
    expect(wrapper.find('nldd-notification[variant="success"]').attributes('supporting-text')).toBe(
      'Je krijgt nu geen e-mail over nieuwe feedback.',
    );
  });

  it('speaks English when the interface does', async () => {
    _setLocaleForTest('en');
    mock.email.site = setting(null, true, 'group');

    const wrapper = await mountLevel('site');

    expect(label(wrapper, 'follow')).toBe('Follow the group and my profile');
    expect(hint(wrapper, 'follow')).toBe('Inherited from the group: on');
  });
});

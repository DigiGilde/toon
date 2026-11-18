import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { nextTick } from 'vue';

import { makeMockBackend, type MockBackend } from '../../../tests/mock-api';
import type { Site } from '@/api/types';
import { _setLocaleForTest } from '@/i18n';

import FeedbackSettings from './FeedbackSettings.vue';
import { fireDetailEvent, problemFetch, provideSiteGroup, untilIdle } from './testHelpers';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  _setLocaleForTest('nl');
});

function site(): Site {
  return backend.data.sites[0]!;
}

function makeWrapper(canAdmin = true, publicPreview = false) {
  return mount(FeedbackSettings, {
    props: { group: 'team-aurora', site: 'website', canAdmin, publicPreview },
    global: { provide: provideSiteGroup(backend) },
  });
}

/** The settings as a site admin sees them. */
function admin(publicPreview = false) {
  return makeWrapper(true, publicPreview);
}

/** The one line a member who cannot change the settings gets. */
function summary(): string {
  return makeWrapper(false).find('[data-testid="feedback-summary"]').text();
}

/** The help text under the live switch. */
function liveHint(wrapper: ReturnType<typeof makeWrapper>): string | undefined {
  return wrapper
    .find('[data-testid="feedback-live"]')
    .element.parentElement?.querySelector('nldd-form-field-help-text')
    ?.textContent?.trim();
}

/** A site without previews: the mock counts them from its list. */
function withoutPreviews(): void {
  backend.data.previews = [];
}

/** Records the bodies of the settings calls, so a test can say what was sent and how often. */
function recordSettingsCalls(): unknown[] {
  const bodies: unknown[] = [];
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith('/feedback-settings')) bodies.push(JSON.parse(String(init?.body)));
    return backend.fetch(input, init);
  });
  return bodies;
}

describe('FeedbackSettings: a section of the tab', () => {
  it('gives an admin the settings in place under their heading, with no line or button in between', () => {
    const wrapper = admin();

    const section = wrapper.find('section');
    expect(section.attributes('aria-labelledby')).toBe('heading-feedback-settings');
    expect(section.find('h2#heading-feedback-settings').text()).toBe('Feedback verzamelen');
    expect(section.find('nldd-title').text()).toContain('voegt Toon een knop Feedback toe');
    expect(section.find('[data-testid="feedback-enabled"]').exists()).toBe(true);
    // The switches say what the line would: an admin gets no second account of it.
    expect(wrapper.find('[data-testid="feedback-summary"]').exists()).toBe(false);
    expect(wrapper.find('nldd-sheet').exists()).toBe(false);
    expect(wrapper.find('nldd-button').exists()).toBe(false);
  });

  it('has no link that brings a hidden feedback button back: that is for the visitors, on the site itself', () => {
    for (const wrapper of [admin(), makeWrapper(false)]) {
      expect(wrapper.find('nldd-link').exists()).toBe(false);
      expect(wrapper.html()).not.toContain('/-/feedback/show');
    }
  });
});

describe('FeedbackSettings: for an admin', () => {
  it('shows the switch, the three ways to give feedback and the live switch while feedback is on', async () => {
    const wrapper = admin();

    expect(wrapper.find('h2').text()).toBe('Feedback verzamelen');
    expect(wrapper.text()).toContain('voegt Toon een knop Feedback toe');
    const enabled = wrapper.find('[data-testid="feedback-enabled"]');
    expect(enabled.attributes('label')).toBe('Feedback toestaan');
    expect(enabled.attributes('checked')).toBeDefined();

    // A sub-heading inside the section, as on the Settings tab of a group.
    const subheading = wrapper.find('h3#heading-feedback-identity');
    expect(subheading.text()).toBe('Wie mag feedback geven');
    expect(subheading.element.closest('nldd-title')?.getAttribute('size')).toBe('display-5');

    const list = wrapper.find('[data-testid="feedback-identity"]');
    expect(list.attributes('type')).toBe('radiogroup');
    expect(list.attributes('accessible-label')).toBe('Wie mag feedback geven');
    const rows = list.findAll('nldd-list-item');
    expect(rows.map((row) => row.find('nldd-title-cell').attributes('text'))).toEqual([
      'Alleen met een account',
      'Ook met een zelf ingevulde naam',
      'Ook zonder naam',
    ]);
    expect(rows[1]!.find('nldd-title-cell').attributes('supporting-text')).toBe(
      'Inloggen hoeft dan niet. Toon controleert die naam niet.',
    );
    expect(wrapper.find('[data-testid="feedback-identity-name"]').attributes('checked')).toBeDefined();
    expect(wrapper.find('[data-testid="feedback-identity-account"]').attributes('checked')).toBeUndefined();
    // The row is the radio. The button inside only draws it.
    expect(rows[0]!.find('nldd-radio-button').attributes('decorative')).toBeDefined();

    const live = wrapper.find('[data-testid="feedback-live"]');
    expect(live.attributes('label')).toBe('Ook op de live site');
    expect(live.attributes('checked')).toBeUndefined();
    // The help text sits in the form field, beside the switch, not inside it.
    expect(live.element.parentElement?.querySelector('nldd-form-field-help-text')?.textContent?.trim()).toBe(
      'Anders alleen op previews.',
    );
  });

  it('hides the other two choices while feedback is off, and says what turning it on does', async () => {
    site().feedbackEnabled = false;
    const wrapper = admin();

    expect(wrapper.find('[data-testid="feedback-enabled"]').attributes('checked')).toBeUndefined();
    expect(wrapper.find('[data-testid="feedback-identity"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-live"]').exists()).toBe(false);
    expect(wrapper.find('nldd-form-field-help-text').text()).toBe(
      'Zet je dit aan, dan krijgen de previews van deze site de feedbackknop.',
    );
  });

  it('turns feedback off with the other two settings in the same call', async () => {
    const bodies = recordSettingsCalls();
    const wrapper = admin();

    fireDetailEvent(wrapper.find('[data-testid="feedback-enabled"]').element, 'change', { checked: false });
    // Optimistic: the other two go at once.
    await nextTick();
    expect(wrapper.find('[data-testid="feedback-identity"]').exists()).toBe(false);
    await untilIdle();

    expect(bodies).toEqual([{ enabled: false, identity: 'name', live: false }]);
    expect(site().feedbackEnabled).toBe(false);
    const notice = wrapper.find('nldd-notification[variant="success"]');
    expect(notice.attributes('text')).toBe('Feedback-instelling opgeslagen');
    expect(notice.attributes('supporting-text')).toBe('Bezoekers zien de feedbackknop niet meer.');
    expect(wrapper.emitted('saved')![0]![0]).toMatchObject({ feedbackEnabled: false });
  });

  it('turns feedback on and says where and from whom it now comes', async () => {
    site().feedbackEnabled = false;
    const wrapper = admin();

    fireDetailEvent(wrapper.find('[data-testid="feedback-enabled"]').element, 'change', { checked: true });
    await untilIdle();

    expect(site().feedbackEnabled).toBe(true);
    expect(wrapper.find('nldd-notification[variant="success"]').attributes('supporting-text')).toBe(
      'Feedback staat aan op de previews. Geven kan met een account of een zelf ingevulde naam.',
    );
    expect(wrapper.find('[data-testid="feedback-identity"]').exists()).toBe(true);
  });

  it('saves another way to give feedback, and nothing for the one already set', async () => {
    const bodies = recordSettingsCalls();
    const wrapper = admin();

    wrapper.find('[data-testid="feedback-identity-name"]').element.dispatchEvent(new CustomEvent('change'));
    wrapper.find('[data-testid="feedback-identity-anonymous"]').element.dispatchEvent(new CustomEvent('change'));
    await untilIdle();

    expect(bodies).toEqual([{ enabled: true, identity: 'anonymous', live: false }]);
    expect(site().feedbackIdentity).toBe('anonymous');
    expect(wrapper.find('[data-testid="feedback-identity-anonymous"]').attributes('checked')).toBeDefined();
  });

  it('turns the live site on, and does nothing for a switch that did not change', async () => {
    const bodies = recordSettingsCalls();
    const wrapper = admin();
    const live = wrapper.find('[data-testid="feedback-live"]').element;

    fireDetailEvent(live, 'change', {});
    fireDetailEvent(wrapper.find('[data-testid="feedback-enabled"]').element, 'change', { checked: true });
    fireDetailEvent(live, 'change', { checked: true });
    await untilIdle();

    expect(bodies).toEqual([{ enabled: true, identity: 'name', live: true }]);
    expect(site().feedbackLive).toBe(true);
    expect(wrapper.find('nldd-notification[variant="success"]').attributes('supporting-text')).toBe(
      'Feedback staat aan op de previews en de live site. Geven kan met een account of een zelf ingevulde naam.',
    );
  });

  it('puts the setting back and says why when the server refuses it', async () => {
    const wrapper = admin();
    vi.stubGlobal(
      'fetch',
      problemFetch(403, { title: 'Geen toegang', detail: 'Hiervoor heb je minimaal de rol beheerder nodig.' }),
    );

    fireDetailEvent(wrapper.find('[data-testid="feedback-live"]').element, 'change', { checked: true });
    await nextTick();
    expect(wrapper.find('[data-testid="feedback-live"]').attributes('checked')).toBeDefined();
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-live"]').attributes('checked')).toBeUndefined();
    const notice = wrapper.find('nldd-notification[variant="critical"]');
    expect(notice.attributes('text')).toBe('Feedback-instelling niet opgeslagen');
    expect(notice.attributes('supporting-text')).toBe('Hiervoor heb je minimaal de rol beheerder nodig.');
    expect(wrapper.emitted('saved')).toBeUndefined();
  });

  it('puts the setting back with a sentence of its own when the network fails', async () => {
    const wrapper = admin();
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));

    wrapper.find('[data-testid="feedback-identity-account"]').element.dispatchEvent(new CustomEvent('change'));
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-identity-name"]').attributes('checked')).toBeDefined();
    expect(wrapper.find('nldd-notification[variant="critical"]').attributes('supporting-text')).toBe(
      'Opslaan lukte niet. Probeer het opnieuw.',
    );
  });
});

describe('FeedbackSettings: while a save is on its way', () => {
  /** Holds every settings call until the test lets the server take it, or fails it. */
  function holdingSaves(): { release: () => void; fail: () => void }[] {
    const held: { release: () => void; fail: () => void }[] = [];
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      if (!String(input).endsWith('/feedback-settings')) return backend.fetch(input, init);
      return new Promise<Response>((resolve) => {
        held.push({
          release: () => resolve(backend.fetch(input, init)),
          fail: () => resolve(problemFetch(503, { title: 'Serverfout', detail: 'Even niet beschikbaar.' })(input, init)),
        });
      });
    });
    return held;
  }

  /** Whether each control is disabled: the switch, the three ways to give feedback and the live switch. */
  function disabled(wrapper: ReturnType<typeof makeWrapper>): boolean[] {
    return wrapper
      .findAll('[data-testid="feedback-enabled"], [data-testid="feedback-identity"] nldd-list-item, [data-testid="feedback-live"]')
      .map((control) => control.attributes('disabled') !== undefined);
  }

  it('disables every control until the answer, which the switches then show', async () => {
    const saves = holdingSaves();
    const wrapper = admin();
    expect(disabled(wrapper)).toEqual([false, false, false, false, false]);

    fireDetailEvent(wrapper.find('[data-testid="feedback-live"]').element, 'change', { checked: true });
    await untilIdle();
    expect(disabled(wrapper)).toEqual([true, true, true, true, true]);

    saves[0]!.release();
    await untilIdle();
    expect(disabled(wrapper)).toEqual([false, false, false, false, false]);
    expect(wrapper.find('[data-testid="feedback-live"]').attributes('checked')).toBeDefined();
    expect(site().feedbackLive).toBe(true);
  });

  it('frees the controls again when the save fails, with the switch put back', async () => {
    const saves = holdingSaves();
    const wrapper = admin();

    fireDetailEvent(wrapper.find('[data-testid="feedback-live"]').element, 'change', { checked: true });
    await untilIdle();
    saves[0]!.fail();
    await untilIdle();

    expect(disabled(wrapper)).toEqual([false, false, false, false, false]);
    expect(wrapper.find('[data-testid="feedback-live"]').attributes('checked')).toBeUndefined();
    expect(wrapper.find('nldd-notification[variant="critical"]').attributes('supporting-text')).toBe(
      'Even niet beschikbaar.',
    );
  });
});

describe('FeedbackSettings: feedback from the public', () => {
  it('warns when a public live site takes feedback without a login', async () => {
    site().feedbackLive = true;
    const wrapper = admin();

    const warning = wrapper.find('[data-testid="feedback-public-warning"]');
    // A banner, as the Access tab warns, not an empty state.
    expect(warning.element.tagName).toBe('NLDD-BANNER');
    expect(warning.attributes('variant')).toBe('warning');
    expect(warning.attributes('text')).toBe('Je verzamelt feedback van het publiek');
    // The cap on open items is a setting of the installation, so the warning names none.
    expect(warning.attributes('supporting-text')).toContain(
      'Reken op spam: voor open feedback geldt per site een maximum. Is dat bereikt, dan neemt de site niets nieuws meer aan.',
    );
    expect(warning.attributes('supporting-text')).not.toMatch(/\d/);
    expect(warning.attributes('supporting-text')).toContain('in haar eigen privacyverklaring noemen');
  });

  it('warns without naming a cap in English too', async () => {
    _setLocaleForTest('en');
    site().feedbackLive = true;

    const text = admin().find('[data-testid="feedback-public-warning"]').attributes('supporting-text');
    expect(text).toContain(
      'Expect spam: the number of open items per site has a maximum. Once it is reached, the site takes nothing new.',
    );
    expect(text).not.toMatch(/\d/);
  });

  it('warns while only the previews take it, since they follow the access of a public site', async () => {
    site().feedbackIdentity = 'anonymous';
    expect(site().feedbackLive).toBe(false);

    const warning = admin().find('[data-testid="feedback-public-warning"]');
    expect(warning.attributes('text')).toBe('Je verzamelt feedback van het publiek');
    const text = warning.attributes('supporting-text');
    expect(text).toContain('Iedereen met de link naar een preview kan feedback geven zonder in te loggen.');
    expect(text).not.toContain('De live site is openbaar');
    expect(text).toContain('in haar eigen privacyverklaring noemen');
    expect(text).not.toMatch(/\d/);
  });

  it('warns about the previews when one is public by an access of its own, on a site that is not', async () => {
    site().access = { base: 'sso', keys: false, invitees: false };
    site().feedbackLive = true;

    expect(admin().find('[data-testid="feedback-public-warning"]').exists()).toBe(false);
    expect(
      admin(true).find('[data-testid="feedback-public-warning"]').attributes('supporting-text'),
    ).toContain('Iedereen met de link naar een preview kan feedback geven zonder in te loggen.');
  });

  it('does not warn while only an account can give it, or when nothing of the site is public, or feedback is off', async () => {
    site().feedbackLive = true;
    site().feedbackIdentity = 'account';
    expect(admin(true).find('[data-testid="feedback-public-warning"]').exists()).toBe(false);

    site().feedbackIdentity = 'anonymous';
    site().access = { base: 'sso', keys: false, invitees: false };
    expect(admin().find('[data-testid="feedback-public-warning"]').exists()).toBe(false);

    site().access = { base: 'public', keys: false, invitees: false };
    site().feedbackEnabled = false;
    expect(admin().find('[data-testid="feedback-public-warning"]').exists()).toBe(false);
  });

  it('warns about the previews in English too', async () => {
    _setLocaleForTest('en');

    expect(admin().find('[data-testid="feedback-public-warning"]').attributes('supporting-text')).toBe(
      'Anyone with the link to a preview can give feedback without signing in. Expect spam: the number of open items per site has a maximum. Once it is reached, the site takes nothing new. Your organisation has to name this collection in its own privacy statement too.',
    );
  });
});

describe('FeedbackSettings: when no page carries the button', () => {
  it('says under the live switch that the site has no previews, and what helps', async () => {
    withoutPreviews();

    expect(liveHint(admin())).toBe(
      'Anders alleen op previews. Deze site heeft geen previews, dus nu heeft geen enkele pagina de feedbackknop. Zet feedback ook op de live site aan, of publiceer een preview met de CLI of vanuit je repository.',
    );
  });

  it('says a site that takes feedback on its live site needs a version there first', async () => {
    withoutPreviews();
    site().feedbackLive = true;
    site().hasLiveVersion = false;
    site().liveVersionId = null;

    expect(liveHint(admin())).toBe(
      'Anders alleen op previews. Er staat nog geen versie live en deze site heeft geen previews, dus nog geen enkele pagina heeft de feedbackknop. Publiceer eerst een versie van de site.',
    );
  });

  it('says why in the summary for anyone who cannot change it', () => {
    withoutPreviews();
    site().feedbackLive = true;
    site().hasLiveVersion = false;
    site().liveVersionId = null;

    expect(summary()).toBe(
      'Feedback staat aan op de previews en de live site. Geven kan met een account of een zelf ingevulde naam. Er staat nog geen versie live en deze site heeft geen previews, dus nog geen enkele pagina heeft de feedbackknop.',
    );
  });

  it('drops the line once the live site takes feedback, and says nothing of it with feedback off', async () => {
    withoutPreviews();
    const wrapper = admin();

    fireDetailEvent(wrapper.find('[data-testid="feedback-live"]').element, 'change', { checked: true });
    await untilIdle();
    expect(liveHint(wrapper)).toBe('Anders alleen op previews.');

    site().feedbackEnabled = false;
    expect(summary()).toBe(
      'Feedback staat uit. Alleen een beheerder van deze site kan dat veranderen.',
    );
  });

  it('says it in English', async () => {
    _setLocaleForTest('en');
    withoutPreviews();

    expect(liveHint(admin())).toBe(
      'Otherwise on previews only. This site has no previews, so no page has the feedback button now. Turn feedback on for the live site too, or publish a preview with the CLI or from your repository.',
    );
    site().feedbackLive = true;
    site().hasLiveVersion = false;
    site().liveVersionId = null;
    expect(summary()).toBe(
      'Feedback is on for the previews and the live site. It can be given with an account or with a name typed in. No version is live yet and this site has no previews, so no page has the feedback button yet.',
    );
  });
});

describe('FeedbackSettings: for everyone else', () => {
  it('says under the heading in one line where feedback is on and from whom, without any control', () => {
    site().feedbackLive = true;
    site().feedbackIdentity = 'anonymous';
    const wrapper = makeWrapper(false, true);

    expect(wrapper.find('h2#heading-feedback-settings').text()).toBe('Feedback verzamelen');
    expect(wrapper.find('nldd-title').text()).not.toContain('voegt Toon een knop Feedback toe');
    for (const control of ['feedback-enabled', 'feedback-identity', 'feedback-live', 'feedback-public-warning']) {
      expect(wrapper.find(`[data-testid="${control}"]`).exists()).toBe(false);
    }
    expect(wrapper.find('nldd-button').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-summary"]').text()).toBe(
      'Feedback staat aan op de previews en de live site. Geven kan ook zonder naam.',
    );
  });

  it('says it for a site that takes feedback on its previews only', () => {
    const wrapper = makeWrapper(false);

    expect(wrapper.find('[data-testid="feedback-summary"]').text()).toBe(
      'Feedback staat aan op de previews. Geven kan met een account of een zelf ingevulde naam.',
    );
  });

  it('names the live site and each way to give feedback', () => {
    site().feedbackLive = true;
    site().feedbackIdentity = 'account';
    expect(summary()).toBe(
      'Feedback staat aan op de previews en de live site. Geven kan alleen met een account.',
    );

    site().feedbackIdentity = 'anonymous';
    expect(summary()).toBe(
      'Feedback staat aan op de previews en de live site. Geven kan ook zonder naam.',
    );
  });

  it('says that feedback is off and who can change that', () => {
    site().feedbackEnabled = false;

    expect(summary()).toBe(
      'Feedback staat uit. Alleen een beheerder van deze site kan dat veranderen.',
    );
  });
});

describe('FeedbackSettings: in English', () => {
  it('speaks English when the interface does', async () => {
    _setLocaleForTest('en');
    const wrapper = admin();

    expect(wrapper.find('h2').text()).toBe('Collecting feedback');
    expect(wrapper.find('[data-testid="feedback-enabled"]').attributes('label')).toBe('Allow feedback');
    expect(summary()).toBe(
      'Feedback is on for the previews. It can be given with an account or with a name typed in.',
    );
  });
});

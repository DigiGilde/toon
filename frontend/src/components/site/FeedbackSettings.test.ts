import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { nextTick } from 'vue';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '../../../tests/mock-api';
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

function makeWrapper(canAdmin = true, newestPreview: string | null = 'pr-42', publicPreview = false) {
  return mount(FeedbackSettings, {
    props: {
      group: 'team-aurora',
      site: 'website',
      contentBase: MOCK_CONTENT_BASE,
      canAdmin,
      newestPreview,
      publicPreview,
    },
    global: { provide: provideSiteGroup(backend) },
  });
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

/** The page the link that brings the button back goes on to, or null without a link. */
function showTarget(wrapper: ReturnType<typeof makeWrapper>): string | null {
  const link = wrapper.find('[data-testid="feedback-show-link"]');
  if (!link.exists()) return null;
  const url = new URL(link.attributes('href')!);
  expect(`${url.origin}${url.pathname}`).toBe(`${MOCK_CONTENT_BASE}/-/feedback/show`);
  return url.searchParams.get('returnTo');
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

describe('FeedbackSettings: for an admin', () => {
  it('shows the switch, the three ways to give feedback and the live switch while feedback is on', () => {
    const wrapper = makeWrapper();

    expect(wrapper.find('h2').text()).toBe('Feedback verzamelen');
    expect(wrapper.text()).toContain('voegt Toon een knop Feedback toe');
    const enabled = wrapper.find('[data-testid="feedback-enabled"]');
    expect(enabled.attributes('label')).toBe('Feedback toestaan');
    expect(enabled.attributes('checked')).toBeDefined();

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
    // The row is the radio; the button inside only draws it.
    expect(rows[0]!.find('nldd-radio-button').attributes('decorative')).toBeDefined();

    const live = wrapper.find('[data-testid="feedback-live"]');
    expect(live.attributes('label')).toBe('Ook op de live site');
    expect(live.attributes('checked')).toBeUndefined();
    // The help text sits in the form field, beside the switch, not inside it.
    expect(live.element.parentElement?.querySelector('nldd-form-field-help-text')?.textContent?.trim()).toBe(
      'Anders alleen op previews.',
    );
    expect(wrapper.find('[data-testid="feedback-summary"]').exists()).toBe(false);
  });

  it('hides the other two choices while feedback is off, and says what turning it on does', () => {
    site().feedbackEnabled = false;
    const wrapper = makeWrapper();

    expect(wrapper.find('[data-testid="feedback-enabled"]').attributes('checked')).toBeUndefined();
    expect(wrapper.find('[data-testid="feedback-identity"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-live"]').exists()).toBe(false);
    expect(wrapper.find('nldd-form-field-help-text').text()).toBe(
      'Zet je dit aan, dan krijgen de previews van deze site de feedbackknop.',
    );
    expect(wrapper.find('[data-testid="feedback-show"]').exists()).toBe(false);
  });

  it('turns feedback off with the other two settings in the same call', async () => {
    const bodies = recordSettingsCalls();
    const wrapper = makeWrapper();

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
    const wrapper = makeWrapper();

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
    const wrapper = makeWrapper();

    wrapper.find('[data-testid="feedback-identity-name"]').element.dispatchEvent(new CustomEvent('change'));
    wrapper.find('[data-testid="feedback-identity-anonymous"]').element.dispatchEvent(new CustomEvent('change'));
    await untilIdle();

    expect(bodies).toEqual([{ enabled: true, identity: 'anonymous', live: false }]);
    expect(site().feedbackIdentity).toBe('anonymous');
    expect(wrapper.find('[data-testid="feedback-identity-anonymous"]').attributes('checked')).toBeDefined();
  });

  it('turns the live site on, and does nothing for a switch that did not change', async () => {
    const bodies = recordSettingsCalls();
    const wrapper = makeWrapper();
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
    const wrapper = makeWrapper();
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
    const wrapper = makeWrapper();
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));

    wrapper.find('[data-testid="feedback-identity-account"]').element.dispatchEvent(new CustomEvent('change'));
    await untilIdle();

    expect(wrapper.find('[data-testid="feedback-identity-name"]').attributes('checked')).toBeDefined();
    expect(wrapper.find('nldd-notification[variant="critical"]').attributes('supporting-text')).toBe(
      'Opslaan lukte niet. Probeer het opnieuw.',
    );
  });

  it('links to the page that brings back a hidden feedback button, in a new tab', () => {
    const wrapper = makeWrapper();

    expect(wrapper.find('[data-testid="feedback-show"]').text()).toContain(
      'Heb je de knop op deze site verborgen tot je de browser sluit?',
    );
    const link = wrapper.find('[data-testid="feedback-show-link"]');
    expect(link.text()).toBe('De feedbackknop weer laten zien');
    expect(link.attributes('href')).toBe(
      `${MOCK_CONTENT_BASE}/-/feedback/show?returnTo=%2Fteam-aurora%2Fwebsite%2F_preview%2Fpr-42%2F`,
    );
    expect(link.attributes('target')).toBe('_blank');
  });

  it('puts the focus on the switch asked for', () => {
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');
    const wrapper = makeWrapper();

    wrapper.vm.focusSwitch('enabled');
    expect(focus.mock.contexts.at(-1)).toBe(wrapper.find('[data-testid="feedback-enabled"]').element);

    wrapper.vm.focusSwitch('live');
    expect(focus.mock.contexts.at(-1)).toBe(wrapper.find('[data-testid="feedback-live"]').element);
  });
});

describe('FeedbackSettings: changes made while a save is on its way', () => {
  /** A settings call held until the test lets the server take it, or fails it. */
  interface HeldSave {
    body: unknown;
    release: () => void;
    fail: () => void;
  }

  /** Holds every settings call; the rest go straight to the mock backend. */
  function holdingSaves(): HeldSave[] {
    const held: HeldSave[] = [];
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      if (!String(input).endsWith('/feedback-settings')) return backend.fetch(input, init);
      return new Promise<Response>((resolve) => {
        held.push({
          body: JSON.parse(String(init?.body)),
          release: () => resolve(backend.fetch(input, init)),
          fail: () => resolve(problemFetch(503, { title: 'Serverfout', detail: 'Even niet beschikbaar.' })(input, init)),
        });
      });
    });
    return held;
  }

  /** What the switches show. */
  function shown(wrapper: ReturnType<typeof makeWrapper>) {
    const identity = wrapper
      .findAll('[data-testid="feedback-identity"] nldd-list-item')
      .find((row) => row.attributes('checked') !== undefined)
      ?.attributes('data-testid')
      ?.replace('feedback-identity-', '');
    return {
      enabled: wrapper.find('[data-testid="feedback-enabled"]').attributes('checked') !== undefined,
      identity,
      live: wrapper.find('[data-testid="feedback-live"]').attributes('checked') !== undefined,
    };
  }

  /** What the server holds. */
  function held(): { enabled: boolean; identity: string; live: boolean } {
    return { enabled: site().feedbackEnabled, identity: site().feedbackIdentity, live: site().feedbackLive };
  }

  /** Turns the live site on and lets its save go out, as it has before a next click can come. */
  async function turnLiveOn(wrapper: ReturnType<typeof makeWrapper>): Promise<void> {
    fireDetailEvent(wrapper.find('[data-testid="feedback-live"]').element, 'change', { checked: true });
    await untilIdle();
  }

  function choose(wrapper: ReturnType<typeof makeWrapper>, identity: string): void {
    wrapper.find(`[data-testid="feedback-identity-${identity}"]`).element.dispatchEvent(new CustomEvent('change'));
  }

  function notices(wrapper: ReturnType<typeof makeWrapper>): (string | undefined)[][] {
    return wrapper
      .findAll('nldd-notification')
      .map((notice) => [notice.attributes('variant'), notice.attributes('supporting-text')]);
  }

  it('sends one save at a time, the next from the switches as they stand when its turn comes', async () => {
    const saves = holdingSaves();
    const wrapper = makeWrapper();

    await turnLiveOn(wrapper);
    choose(wrapper, 'anonymous');
    await untilIdle();
    // Both show at once, and only the first is on its way.
    expect(shown(wrapper)).toEqual({ enabled: true, identity: 'anonymous', live: true });
    expect(saves.map((save) => save.body)).toEqual([{ enabled: true, identity: 'name', live: true }]);

    saves[0]!.release();
    await untilIdle();
    // The change made meanwhile stays on screen and goes out next.
    expect(shown(wrapper)).toEqual({ enabled: true, identity: 'anonymous', live: true });
    expect(saves.map((save) => save.body)).toEqual([
      { enabled: true, identity: 'name', live: true },
      { enabled: true, identity: 'anonymous', live: true },
    ]);

    saves[1]!.release();
    await untilIdle();
    expect(shown(wrapper)).toEqual({ enabled: true, identity: 'anonymous', live: true });
    expect(held()).toEqual({ enabled: true, identity: 'anonymous', live: true });
    // Each notice says what that save stored.
    expect(notices(wrapper)).toEqual([
      ['success', 'Feedback staat aan op de previews en de live site. Geven kan met een account of een zelf ingevulde naam.'],
      ['success', 'Feedback staat aan op de previews en de live site. Geven kan ook zonder naam.'],
    ]);
    expect(wrapper.emitted('saved')).toHaveLength(2);
  });

  it('goes back to what the server holds when a save fails, and sends nothing of what came after it', async () => {
    const saves = holdingSaves();
    const wrapper = makeWrapper();

    await turnLiveOn(wrapper);
    choose(wrapper, 'anonymous');
    await untilIdle();
    saves[0]!.fail();
    await untilIdle();

    expect(shown(wrapper)).toEqual({ enabled: true, identity: 'name', live: false });
    expect(held()).toEqual({ enabled: true, identity: 'name', live: false });
    expect(saves).toHaveLength(1);
    expect(notices(wrapper)).toEqual([['critical', 'Even niet beschikbaar.']]);
    expect(wrapper.emitted('saved')).toBeUndefined();
  });

  it('goes back to what the save before stored when a later one fails', async () => {
    const saves = holdingSaves();
    const wrapper = makeWrapper();

    await turnLiveOn(wrapper);
    choose(wrapper, 'anonymous');
    await untilIdle();
    saves[0]!.release();
    await untilIdle();
    saves[1]!.fail();
    await untilIdle();

    expect(shown(wrapper)).toEqual({ enabled: true, identity: 'name', live: true });
    expect(held()).toEqual({ enabled: true, identity: 'name', live: true });
    expect(notices(wrapper).map(([variant]) => variant)).toEqual(['success', 'critical']);
    expect(wrapper.emitted('saved')).toHaveLength(1);
  });

  it('sends nothing more once later changes come back to what the save before stored', async () => {
    const saves = holdingSaves();
    const wrapper = makeWrapper();

    await turnLiveOn(wrapper);
    choose(wrapper, 'anonymous');
    choose(wrapper, 'name');
    await untilIdle();
    saves[0]!.release();
    await untilIdle();

    expect(saves).toHaveLength(1);
    expect(shown(wrapper)).toEqual({ enabled: true, identity: 'name', live: true });
    expect(held()).toEqual({ enabled: true, identity: 'name', live: true });
    expect(wrapper.emitted('saved')).toHaveLength(1);
  });
});

describe('FeedbackSettings: where the button comes back', () => {
  it('goes on to the live site once that takes feedback too', () => {
    site().feedbackLive = true;

    expect(showTarget(makeWrapper())).toBe('/team-aurora/website/');
  });

  it('goes on to the newest preview while only the previews take feedback', () => {
    expect(site().hasLiveVersion).toBe(true);

    expect(showTarget(makeWrapper(true, 'pr-7'))).toBe('/team-aurora/website/_preview/pr-7/');
  });

  it('goes on to the newest preview when the site has no live version for the button to show on', () => {
    site().feedbackLive = true;
    site().hasLiveVersion = false;
    site().liveVersionId = null;

    expect(showTarget(makeWrapper())).toBe('/team-aurora/website/_preview/pr-42/');
  });

  it('leaves the link out when no page of the site carries the button', () => {
    expect(showTarget(makeWrapper(true, null))).toBeNull();
    expect(makeWrapper(false, null).find('[data-testid="feedback-show"]').exists()).toBe(false);

    site().feedbackLive = true;
    site().hasLiveVersion = false;
    site().liveVersionId = null;
    expect(showTarget(makeWrapper(true, null))).toBeNull();
  });

  it('follows the live switch: on, the live site carries the button as well', async () => {
    const wrapper = makeWrapper();

    fireDetailEvent(wrapper.find('[data-testid="feedback-live"]').element, 'change', { checked: true });
    await untilIdle();

    expect(showTarget(wrapper)).toBe('/team-aurora/website/');
  });
});

describe('FeedbackSettings: feedback from the public', () => {
  it('warns when a public live site takes feedback without a login', () => {
    site().feedbackLive = true;
    const wrapper = makeWrapper();

    const warning = wrapper.find('[data-testid="feedback-public-warning"]');
    expect(warning.attributes('text')).toBe('Je verzamelt feedback van het publiek');
    // The cap on open items is a setting of the installation, so the warning names none.
    expect(warning.attributes('supporting-text')).toContain(
      'Reken op spam: voor open feedback geldt per site een maximum. Is dat bereikt, dan neemt de site niets nieuws meer aan.',
    );
    expect(warning.attributes('supporting-text')).not.toMatch(/\d/);
    expect(warning.attributes('supporting-text')).toContain('in haar eigen privacyverklaring noemen');
  });

  it('warns without naming a cap in English too', () => {
    _setLocaleForTest('en');
    site().feedbackLive = true;

    const text = makeWrapper().find('[data-testid="feedback-public-warning"]').attributes('supporting-text');
    expect(text).toContain(
      'Expect spam: the number of open items per site has a maximum. Once it is reached, the site takes nothing new.',
    );
    expect(text).not.toMatch(/\d/);
  });

  it('warns while only the previews take it, since they follow the access of a public site', () => {
    site().feedbackIdentity = 'anonymous';
    expect(site().feedbackLive).toBe(false);

    const warning = makeWrapper().find('[data-testid="feedback-public-warning"]');
    expect(warning.attributes('text')).toBe('Je verzamelt feedback van het publiek');
    const text = warning.attributes('supporting-text');
    expect(text).toContain('Iedereen met de link naar een preview kan feedback geven zonder in te loggen.');
    expect(text).not.toContain('De live site is openbaar');
    expect(text).toContain('in haar eigen privacyverklaring noemen');
    expect(text).not.toMatch(/\d/);
  });

  it('warns about the previews when one is public by an access of its own, on a site that is not', () => {
    site().access = { base: 'sso', keys: false, invitees: false };
    site().feedbackLive = true;

    expect(makeWrapper().find('[data-testid="feedback-public-warning"]').exists()).toBe(false);
    expect(
      makeWrapper(true, 'pr-42', true).find('[data-testid="feedback-public-warning"]').attributes('supporting-text'),
    ).toContain('Iedereen met de link naar een preview kan feedback geven zonder in te loggen.');
  });

  it('does not warn while only an account can give it, or when nothing of the site is public, or feedback is off', () => {
    site().feedbackLive = true;
    site().feedbackIdentity = 'account';
    expect(makeWrapper(true, 'pr-42', true).find('[data-testid="feedback-public-warning"]').exists()).toBe(false);

    site().feedbackIdentity = 'anonymous';
    site().access = { base: 'sso', keys: false, invitees: false };
    expect(makeWrapper().find('[data-testid="feedback-public-warning"]').exists()).toBe(false);

    site().access = { base: 'public', keys: false, invitees: false };
    site().feedbackEnabled = false;
    expect(makeWrapper().find('[data-testid="feedback-public-warning"]').exists()).toBe(false);
  });

  it('warns about the previews in English too', () => {
    _setLocaleForTest('en');

    expect(makeWrapper().find('[data-testid="feedback-public-warning"]').attributes('supporting-text')).toBe(
      'Anyone with the link to a preview can give feedback without signing in. Expect spam: the number of open items per site has a maximum. Once it is reached, the site takes nothing new. Your organisation has to name this collection in its own privacy statement too.',
    );
  });
});

describe('FeedbackSettings: when no page carries the button', () => {
  it('says under the live switch that the site has no previews, and what helps', () => {
    withoutPreviews();

    expect(liveHint(makeWrapper(true, null))).toBe(
      'Anders alleen op previews. Deze site heeft geen previews, dus nu heeft geen enkele pagina de feedbackknop. Zet feedback ook op de live site aan, of publiceer een preview met de CLI of vanuit je repository.',
    );
  });

  it('says a site that takes feedback on its live site needs a version there first', () => {
    withoutPreviews();
    site().feedbackLive = true;
    site().hasLiveVersion = false;
    site().liveVersionId = null;

    expect(liveHint(makeWrapper(true, null))).toBe(
      'Anders alleen op previews. Er staat nog geen versie live en deze site heeft geen previews, dus nog geen enkele pagina heeft de feedbackknop. Publiceer eerst een versie van de site.',
    );
  });

  it('says why in the summary for anyone who cannot change it', () => {
    withoutPreviews();
    site().feedbackLive = true;
    site().hasLiveVersion = false;
    site().liveVersionId = null;

    expect(makeWrapper(false, null).find('[data-testid="feedback-summary"]').text()).toBe(
      'Feedback staat aan op de previews en de live site. Geven kan met een account of een zelf ingevulde naam. Er staat nog geen versie live en deze site heeft geen previews, dus nog geen enkele pagina heeft de feedbackknop.',
    );
  });

  it('drops the line once the live site takes feedback, and says nothing of it with feedback off', async () => {
    withoutPreviews();
    const wrapper = makeWrapper(true, null);

    fireDetailEvent(wrapper.find('[data-testid="feedback-live"]').element, 'change', { checked: true });
    await untilIdle();
    expect(liveHint(wrapper)).toBe('Anders alleen op previews.');

    site().feedbackEnabled = false;
    expect(makeWrapper(false, null).find('[data-testid="feedback-summary"]').text()).toBe(
      'Feedback staat uit. Alleen een beheerder van deze site kan dat veranderen.',
    );
  });

  it('says it in English', () => {
    _setLocaleForTest('en');
    withoutPreviews();

    expect(liveHint(makeWrapper(true, null))).toBe(
      'Otherwise on previews only. This site has no previews, so no page has the feedback button now. Turn feedback on for the live site too, or publish a preview with the CLI or from your repository.',
    );
    site().feedbackLive = true;
    site().hasLiveVersion = false;
    site().liveVersionId = null;
    expect(makeWrapper(false, null).find('[data-testid="feedback-summary"]').text()).toBe(
      'Feedback is on for the previews and the live site. It can be given with an account or with a name typed in. No version is live yet and this site has no previews, so no page has the feedback button yet.',
    );
  });
});

describe('FeedbackSettings: for everyone else', () => {
  it('says in one line where feedback is on and from whom, without controls', () => {
    const wrapper = makeWrapper(false);

    expect(wrapper.find('[data-testid="feedback-enabled"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-identity"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-summary"]').text()).toBe(
      'Feedback staat aan op de previews. Geven kan met een account of een zelf ingevulde naam.',
    );
    // A member who hid the button can bring it back too.
    expect(wrapper.find('[data-testid="feedback-show-link"]').exists()).toBe(true);
  });

  it('names the live site and each way to give feedback', () => {
    site().feedbackLive = true;
    site().feedbackIdentity = 'account';
    expect(makeWrapper(false).find('[data-testid="feedback-summary"]').text()).toBe(
      'Feedback staat aan op de previews en de live site. Geven kan alleen met een account.',
    );

    site().feedbackIdentity = 'anonymous';
    expect(makeWrapper(false).find('[data-testid="feedback-summary"]').text()).toBe(
      'Feedback staat aan op de previews en de live site. Geven kan ook zonder naam.',
    );
  });

  it('says that feedback is off and who can change that', () => {
    site().feedbackEnabled = false;

    expect(makeWrapper(false).find('[data-testid="feedback-summary"]').text()).toBe(
      'Feedback staat uit. Alleen een beheerder van deze site kan dat veranderen.',
    );
  });
});

describe('FeedbackSettings: in English', () => {
  it('speaks English when the interface does', () => {
    _setLocaleForTest('en');
    const wrapper = makeWrapper();

    expect(wrapper.find('[data-testid="feedback-enabled"]').attributes('label')).toBe('Allow feedback');
    expect(wrapper.find('[data-testid="feedback-show-link"]').text()).toBe('Show the feedback button again');
    expect(makeWrapper(false).find('[data-testid="feedback-summary"]').text()).toBe(
      'Feedback is on for the previews. It can be given with an account or with a name typed in.',
    );
  });
});

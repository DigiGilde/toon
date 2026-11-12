/**
 * The privacy and accessibility statements are legal texts, and Toon is
 * installed by more than one organisation. What matters: every authority is
 * there as a working link, in both languages; the operator's address comes
 * from `GET /operator`, never from the code; and without an operator, or
 * without an answer, the statements still say who to turn to.
 */
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter } from 'vue-router';

import type { Operator } from '../src/api/types';
import { _setLocaleForTest } from '../src/i18n';
import Accessibility from '../src/pages/Accessibility.vue';
import Privacy from '../src/pages/Privacy.vue';
import { makeMockBackend } from './mock-api';

afterEach(() => {
  _setLocaleForTest('nl');
  vi.unstubAllGlobals();
});

function serve(operator: Operator): void {
  const backend = makeMockBackend();
  backend.data.operator = operator;
  vi.stubGlobal('fetch', backend.fetch);
}

async function render(component: typeof Privacy, locale: 'nl' | 'en') {
  _setLocaleForTest(locale);
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component }],
  });
  await router.push('/');
  await router.isReady();
  const wrapper = mount(component, { global: { plugins: [router] } });
  await flushPromises();
  return wrapper;
}

function hrefs(wrapper: Awaited<ReturnType<typeof render>>): string[] {
  return wrapper.findAll('a').map((a) => a.attributes('href') ?? '');
}

const OPERATOR = { name: 'Gemeente Voorbeeld', email: 'privacy@voorbeeld.nl' };
const NOBODY = { name: null, email: null };

describe('privacy statement', () => {
  it.each(['nl', 'en'] as const)('names the operator and its address in %s', async (locale) => {
    serve(OPERATOR);
    const wrapper = await render(Privacy, locale);

    expect(hrefs(wrapper)).toEqual(['mailto:privacy@voorbeeld.nl', 'https://www.autoriteitpersoonsgegevens.nl']);
    expect(wrapper.text()).toContain('Gemeente Voorbeeld');
    wrapper.unmount();
  });

  it.each(['nl', 'en'] as const)('points to the organisation that runs it when none is named, in %s', async (locale) => {
    serve(NOBODY);
    const wrapper = await render(Privacy, locale);

    expect(hrefs(wrapper)).toEqual(['https://www.autoriteitpersoonsgegevens.nl']);
    const unnamed =
      locale === 'nl'
        ? 'De organisatie die deze installatie van Toon beheert'
        : 'The organisation that runs this installation of Toon';
    expect(wrapper.text()).toContain(unnamed);
    wrapper.unmount();
  });

  it('names an address without a name, and a name without an address', async () => {
    serve({ name: null, email: 'privacy@voorbeeld.nl' });
    const address = await render(Privacy, 'en');
    expect(hrefs(address)).toContain('mailto:privacy@voorbeeld.nl');
    expect(address.text()).toContain('The organisation that runs this installation of Toon');
    address.unmount();

    serve({ name: 'Gemeente Voorbeeld', email: null });
    const name = await render(Privacy, 'en');
    expect(hrefs(name)).not.toContain('mailto:privacy@voorbeeld.nl');
    expect(name.text()).toContain('This installation of Toon is run by Gemeente Voorbeeld.');
    expect(name.text()).toContain('to the organisation that runs this installation.');
    name.unmount();
  });

  it('still reads as a statement when the operator cannot be fetched', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('netwerkfout'))));
    const wrapper = await render(Privacy, 'nl');

    expect(wrapper.text()).toContain('stuur je naar de organisatie die deze installatie beheert.');
    wrapper.unmount();
  });

  it.each([
    [
      'nl',
      [
        'Feedback: wat je met de feedbackknop over een pagina stuurt',
        'Met een naam bewaart Toon alleen die naam, zonder naam geen naam, SSO-id of e-mailadres.',
        'Je tekst of de schermafbeelding kan nog wel iets over je zeggen.',
        'dus soms ook gegevens van anderen of van jezelf.',
        'Bij feedback bewaart Toon geen IP-adres en niets van wat je browser over zichzelf meestuurt.',
        'Om misbruik te beperken telt Toon wel hoeveel feedback er per site binnenkomt: per account als je feedback met je account geeft of moest inloggen om de pagina te zien, en anders per IP-adres.',
        'Bezoekgegevens en geslaagde inlogpogingen worden na 90 dagen verwijderd.',
        'Overige auditgegevens, zoals geweigerde toegang, mislukte of afgebroken inlogpogingen en beheerhandelingen, blijven drie jaar bewaard',
        'Oudere feedback, of feedback op een site waar de knop niet meer staat, kun je opvragen of laten verwijderen met een verzoek',
        'Die telling staat alleen in het werkgeheugen. Ze loopt standaard over tien minuten en is uiterlijk even lang na afloop weg.',
        'de grootte en de pixelverhouding van je browservenster',
        'dan onthoudt een cookie alleen dat, en niet langer dan 12 uur.',
        'een platformbeheerder zou de tijden naast elkaar kunnen leggen',
        'zodat het team van een site opmerkingen over die site kan lezen, beantwoorden en afhandelen',
        'Wie feedback zo in een AI-assistent laadt, beslist zelf over die verwerker.',
        'de schermafbeelding 30 dagen nadat de feedback is afgehandeld',
        'om afgeschermde content te bekijken of om feedback te geven met je account',
        'zien alleen redacteuren en beheerders van de site',
        'Reageert het siteteam op feedback die met een account is gestuurd, dan ziet de indiener bij die reactie de naam van wie reageerde, nooit een e-mailadres.',
        'Die naam blijft bij de reactie staan zolang de feedback bestaat.',
        'ook op een openbare site, bijvoorbeeld om feedback te geven met je account',
        'Mijn feedback staat in de knop zolang je op die site feedback geeft met je account. Kom je later terug, dan kies je eerst Met je account versturen of Feedback geven met je account, log je in en zeg je opnieuw ja. Dat beëindig je daarna weer met Stoppen met je account.',
        'Een schermafbeelding die het team zo ophaalt, en wat een AI-assistent van feedback in zijn eigen geschiedenis bewaart, zijn kopieën buiten Toon. De bewaartermijnen hieronder en het verwijderen door de indiener gelden daar niet voor, dus die kopieën opruimen is aan het team.',
      ],
    ],
    [
      'en',
      [
        'Feedback: what you send about a page with the feedback button',
        'With a name Toon keeps only that name, without a name no name, SSO ID or e-mail address.',
        'Your text or the screenshot can still say something about you.',
        'so sometimes data of other people or of yourself too.',
        'With feedback Toon keeps no IP address and none of the details your browser sends about itself.',
        'To limit misuse, Toon does count how much feedback comes in per site: per account when you give feedback with your account or had to sign in to see the page, otherwise per IP address.',
        'Visit data and successful sign-ins are deleted after 90 days.',
        'Other audit data, such as refused access, failed or abandoned sign-ins and administrative actions, is kept for three years',
        'For older feedback, or feedback on a site that no longer shows the button, send a request to see or delete it',
        'That count is kept in working memory only. It runs over ten minutes by default, and is gone at most that long after it ends.',
        'the size and the pixel ratio of your browser window',
        'a cookie remembers only that, and for 12 hours at most.',
        'a platform administrator could put the times side by side',
        'so the team of a site can read, answer and resolve remarks about that site',
        'Whoever loads feedback into an AI assistant that way decides about that processor themselves.',
        'the screenshot 30 days after the feedback was resolved',
        'to view restricted content or to give feedback with your account',
        'is seen only by editors and administrators of the site',
        'When the site team replies to feedback sent with an account, the submitter sees with that reply the name of whoever replied, never an e-mail address.',
        'That name stays with the reply for as long as the feedback exists.',
        'a public one included, for instance to give feedback with your account',
        'My feedback is in the button while you give feedback on that site with your account. When you come back later, you first choose Send with your account or Give feedback with your account, sign in and say yes again. You end that again afterwards with Stop using your account.',
        "A screenshot the team fetches that way, and what an AI assistant keeps of feedback in its own history, are copies outside Toon. The retention periods below and a submitter's own deletion do not reach them, so removing those copies is up to the team.",
      ],
    ],
  ] as const)('says in %s what feedback keeps, how long, who sees it and where it may go', async (locale, sentences) => {
    serve(OPERATOR);
    const wrapper = await render(Privacy, locale);

    for (const sentence of sentences) expect(wrapper.text()).toContain(sentence);
    // Signing in to give feedback is recorded on a public site too.
    expect(wrapper.text()).not.toMatch(/Van openbare sites wordt niets bijgehouden|Nothing is recorded for public sites/);
    // Without a name the text and the screenshot can still say who it was, and
    // the count against misuse is not only per address.
    expect(wrapper.text()).not.toMatch(/niets over jou|nothing about you|even per IP-adres|for a short while per IP address/);
    // Signed in is not enough to be counted per account: a public page, or one
    // a secret link opened, counts a signed-in visitor by address too. And a
    // sign-in that failed or was broken off stays three years, not 90 days.
    expect(wrapper.text()).not.toMatch(/als je bent ingelogd|when you are signed in|inlogmomenten|sign-in moments/);
    // Every item keeps the size of the window, which is data about the browser
    // and not the size of the screen.
    expect(wrapper.text()).not.toMatch(/gegevens over je browser|data about your browser/);
    expect(wrapper.text()).not.toMatch(/grootte van je scherm|size of your screen/);
    wrapper.unmount();
  });

  it.each([
    [
      'nl',
      'Op sites die feedback verzamelen, verwerkt Toon ook wat bezoekers met de feedbackknop sturen: hun opmerking, eventueel een schermafbeelding en hun naam, en met een account ook hun e-mailadres. Zo kan het team van de site die feedback lezen en beantwoorden.',
    ],
    [
      'en',
      'On sites that collect feedback, Toon also processes what visitors send with the feedback button: their remark, possibly a screenshot and their name, and with an account their e-mail address too. That way the team of the site can read and answer the feedback.',
    ],
  ] as const)('says in its opening, in %s, that it covers what the feedback button sends', async (locale, sentence) => {
    serve(OPERATOR);
    const wrapper = await render(Privacy, locale);

    // The widget and the consent page link here: the first paragraph says it.
    expect(wrapper.find('p').text()).toContain(sentence);
    wrapper.unmount();
  });

  it('names no organisation of its own and does not claim to store one', async () => {
    serve(NOBODY);
    for (const locale of ['nl', 'en'] as const) {
      const wrapper = await render(Privacy, locale);
      expect(wrapper.text()).not.toMatch(/Binnenlandse|Interior|Digi Gilde|ZAD|minbzk|rijksoverheid\.nl/);
      expect(wrapper.text()).not.toMatch(/organisatie,? via inloggen|and organisation/);
      wrapper.unmount();
    }
  });
});

describe('accessibility statement', () => {
  it.each(['nl', 'en'] as const)('links register, report address and enforcement in %s', async (locale) => {
    serve(OPERATOR);
    const wrapper = await render(Accessibility, locale);

    expect(hrefs(wrapper)).toEqual([
      'https://www.toegankelijkheidsverklaring.nl',
      'mailto:privacy@voorbeeld.nl',
      'https://www.mensenrechten.nl',
    ]);
    expect(wrapper.text()).toContain('WCAG 2.1');
    expect(wrapper.text()).toContain('2026');
    wrapper.unmount();
  });

  it.each(['nl', 'en'] as const)('sends a report to the organisation that runs it when none is named, in %s', async (locale) => {
    serve(NOBODY);
    const wrapper = await render(Accessibility, locale);

    expect(hrefs(wrapper)).toEqual(['https://www.toegankelijkheidsverklaring.nl', 'https://www.mensenrechten.nl']);
    const unnamed =
      locale === 'nl'
        ? 'Laat het de organisatie weten die deze installatie beheert.'
        : 'Let the organisation that runs this installation know.';
    expect(wrapper.text()).toContain(unnamed);
    wrapper.unmount();
  });
});

import { describe, expect, it, vi } from 'vitest';

import { translator } from './i18n';
import { STYLES } from './styles';
import { fakeConfig } from './testPlatform';
import { hideConfirmView, hidingView, menuView, sentView } from './views';

const t = translator('nl');

function labels(element: HTMLElement): string[] {
  return Array.from(element.querySelectorAll('button, a'), (control) => control.textContent!);
}

function press(element: HTMLElement, key: string): void {
  element.querySelector<HTMLElement>(`[data-key="${key}"]`)!.click();
}

function actions() {
  return { point: vi.fn(), text: vi.fn(), page: vi.fn(), mine: vi.fn(), hide: vi.fn() };
}

const hint = () => document.createElement('p');

/** What the ids in an aria-describedby say, in order. */
function description(view: HTMLElement, control: Element): string[] {
  return control
    .getAttribute('aria-describedby')!
    .split(' ')
    .map((id) => view.querySelector(`#${id}`)!.textContent!);
}

/** The menu's groups, each as the labels of its rows, the footer last. */
function groups(view: HTMLElement): string[][] {
  return Array.from(view.querySelectorAll<HTMLElement>('.actions, .menu-foot'), (group) => labels(group));
}

describe('the menu', () => {
  it('offers only giving feedback with an account, hiding the button and the privacy statement on a site that takes it with one only', () => {
    const calls = actions();
    const view = menuView(t, fakeConfig({ identity: 'account', account: false }), calls, hint(), false, false);
    expect(groups(view)).toEqual([
      ['Feedback geven met je account'],
      ['Knop verbergen', 'Privacyverklaring (opent in een nieuw tabblad)'],
    ]);
    const link = view.querySelector('[data-key="menu.login"]')!;
    expect(link.getAttribute('href')).toBe(fakeConfig().accountUrl);
    // The consent page after the sign-in says what it keeps: no note here.
    expect(link.hasAttribute('aria-describedby')).toBe(false);
    expect(view.querySelector('.notice')).toBeNull();
    press(view, 'menu.hide');
    expect(calls.hide).toHaveBeenCalledOnce();
  });

  it('links the privacy statement in a new tab, or in this tab on a shielded page', () => {
    const config = fakeConfig({ identity: 'account', account: false });
    const open = menuView(t, config, actions(), hint(), false, false).querySelector('.menu-foot a')!;
    expect([open.getAttribute('href'), open.getAttribute('target'), open.getAttribute('rel')]).toEqual([
      'https://beheer.toon.test/-/privacy',
      '_blank',
      'noopener noreferrer',
    ]);
    const shielded = menuView(t, config, actions(), hint(), true, false).querySelector<HTMLAnchorElement>('.menu-foot a')!;
    expect([shielded.textContent, shielded.hasAttribute('target'), shielded.getAttribute('rel')]).toEqual([
      'Privacyverklaring',
      false,
      'noreferrer',
    ]);
    expect(shielded.hasAttribute('aria-describedby')).toBe(false);
  });

  it('puts the line about a missing selection under the text button, which it describes', () => {
    const line = hint();
    line.id = 'tf-text-hint';
    const view = menuView(t, fakeConfig(), actions(), line, false, false);
    const text = view.querySelector('[data-key="menu.text"]')!;
    expect(text.nextElementSibling).toBe(line);
    expect(text.getAttribute('aria-describedby')).toBe('tf-text-hint');
  });

  it('offers the three ways, sending with an account, hiding and the privacy statement to a giver without account', () => {
    const view = menuView(t, fakeConfig({ identity: 'anonymous', account: false }), actions(), hint(), false, false);
    expect(groups(view)).toEqual([
      ['Wijs een punt of gebied aan', 'Feedback op geselecteerde tekst', 'Algemene feedback'],
      ['Met je account versturen'],
      ['Knop verbergen', 'Privacyverklaring (opent in een nieuw tabblad)'],
    ]);
    const link = view.querySelector('[data-key="menu.account"]')!;
    expect(link.getAttribute('href')).toBe(fakeConfig().accountUrl);
    // Nothing typed, nothing to lose on the way.
    expect(link.hasAttribute('aria-describedby')).toBe(false);
    expect(view.querySelector('#tf-account-note')).toBeNull();
  });

  it('says under the way to an account that unsent text is lost, only when there is some', () => {
    const view = menuView(t, fakeConfig({ identity: 'anonymous', account: false }), actions(), hint(), false, true);
    const link = view.querySelector('[data-key="menu.account"]')!;
    expect(link.nextElementSibling!.id).toBe('tf-account-note');
    expect(description(view, link)).toEqual([
      'Je gaat naar een pagina van Toon. Tekst die je nog niet hebt verstuurd, gaat dan verloren.',
    ]);
    // A new tab loses nothing.
    expect(view.querySelector('.menu-foot a')!.hasAttribute('aria-describedby')).toBe(false);
  });

  it('says it for stopping with the account too, and for the privacy statement in this tab on a shielded page', () => {
    const view = menuView(t, fakeConfig({ identity: 'account', account: true }), actions(), hint(), true, true);
    const stop = view.querySelector('[data-key="menu.stopAccount"]')!;
    expect(description(view, stop)).toEqual([t('menu.account.note')]);
    expect(description(view, view.querySelector('.menu-foot a')!)).toEqual([t('menu.account.note')]);
  });

  it('offers "My feedback" and stopping to a giver with an account', () => {
    const view = menuView(t, fakeConfig({ identity: 'account', account: true }), actions(), hint(), false, false);
    expect(groups(view)).toEqual([
      ['Wijs een punt of gebied aan', 'Feedback op geselecteerde tekst', 'Algemene feedback'],
      ['Mijn feedback', 'Stoppen met feedback via je account'],
      ['Knop verbergen', 'Privacyverklaring (opent in een nieuw tabblad)'],
    ]);
    expect(view.querySelector('[data-key="menu.stopAccount"]')!.getAttribute('href')).toBe(fakeConfig().stopUrl);
    expect(view.querySelector('#tf-account-note')).toBeNull();
  });

  describe('without a way back to the page', () => {
    const noWayBack = { accountUrl: null, stopUrl: null, returnTo: null, showUrl: null };

    it('offers the three ways and the privacy statement, no account and no hiding', () => {
      const view = menuView(t, fakeConfig({ ...noWayBack, account: false }), actions(), hint(), false, false);
      expect(groups(view)).toEqual([
        ['Wijs een punt of gebied aan', 'Feedback op geselecteerde tekst', 'Algemene feedback'],
        ['Privacyverklaring (opent in een nieuw tabblad)'],
      ]);
    });

    it('keeps "My feedback" for a giver with an account, without a way to stop', () => {
      const view = menuView(t, fakeConfig({ ...noWayBack, account: true }), actions(), hint(), false, false);
      expect(groups(view)).toEqual([
        ['Wijs een punt of gebied aan', 'Feedback op geselecteerde tekst', 'Algemene feedback'],
        ['Mijn feedback'],
        ['Privacyverklaring (opent in een nieuw tabblad)'],
      ]);
    });

    it('has no line about unsent text when nothing leaves this tab', () => {
      const view = menuView(t, fakeConfig({ ...noWayBack, identity: 'anonymous', account: false }), actions(), hint(), false, true);
      expect(view.querySelector('#tf-account-note')).toBeNull();
      expect(view.querySelector('[aria-describedby="tf-account-note"]')).toBeNull();
    });

    it('keeps the line about unsent text for the privacy statement in this tab on a shielded page', () => {
      const view = menuView(t, fakeConfig({ ...noWayBack, account: false }), actions(), hint(), true, true);
      expect(description(view, view.querySelector('.menu-foot a')!)).toEqual([t('menu.account.note')]);
    });

    it('offers only the privacy statement on a site that takes feedback with an account only', () => {
      const config = fakeConfig({ ...noWayBack, identity: 'account', account: false });
      const view = menuView(t, config, actions(), hint(), false, false);
      expect(groups(view)).toEqual([['Privacyverklaring (opent in een nieuw tabblad)']]);
      expect(view.querySelector('.actions')).toBeNull();
    });
  });

  it('draws each row with one leading icon hidden from screen readers, and no arrow on a link that stays in this tab', () => {
    const icons = (view: HTMLElement, key: string) => {
      const row = view.querySelector(`[data-key="${key}"]`)!;
      expect(row.firstElementChild!.localName).toBe('svg');
      return Array.from(row.querySelectorAll('svg'), (icon) => icon.getAttribute('aria-hidden'));
    };
    const full = menuView(t, fakeConfig({ account: true }), actions(), hint(), false, false);
    for (const key of ['menu.point', 'menu.text', 'menu.page', 'menu.mine', 'menu.hide', 'menu.stopAccount']) {
      expect(icons(full, key)).toEqual(['true']);
    }
    expect(full.querySelector('[data-key="menu.stopAccount"]')!.className).toBe('action');
    const without = menuView(t, fakeConfig({ account: false }), actions(), hint(), false, false);
    expect(icons(without, 'menu.account')).toEqual(['true']);
    const signIn = menuView(t, fakeConfig({ identity: 'account', account: false }), actions(), hint(), false, false);
    expect(icons(signIn, 'menu.login')).toEqual(['true']);
    // The privacy statement in a new tab keeps saying so in its name.
    expect(signIn.querySelector('.menu-foot a')!.textContent).toBe('Privacyverklaring (opent in een nieuw tabblad)');
  });

  it('calls the action of each button, and has no close row: the panel closes from its header', () => {
    const calls = actions();
    const view = menuView(t, fakeConfig({ account: true }), calls, hint(), false, false);
    for (const key of ['menu.point', 'menu.text', 'menu.page', 'menu.mine', 'menu.hide']) press(view, key);
    for (const action of Object.values(calls)) expect(action).toHaveBeenCalledOnce();
    expect(labels(view)).not.toContain('Sluiten');
  });

  it('styles the rows as a menu: no border, normal weight, 44 px high, tinted on hover and focus', () => {
    expect(STYLES).toMatch(/\.action \{[^}]*min-height: 44px;[^}]*border: 0;[^}]*font: 400 /s);
    expect(STYLES).toContain('.action:hover, .action:focus-visible { background: var(--tf-accent-tinted); }');
    expect(STYLES).toContain('.actions + .actions, .menu-foot { border-top-color: CanvasText; }');
    // The footer is set apart by space and the divider, in the rows' own size.
    const footer = STYLES.split('\n').filter((line) => line.startsWith('.menu-foot'));
    expect(footer.join('\n')).not.toMatch(/font-size|font:/);
  });
});

describe('after sending', () => {
  it('points a giver with an account to "My feedback"', () => {
    const calls = { mine: vi.fn(), more: vi.fn(), close: vi.fn() };
    const view = sentView(t, fakeConfig({ account: true }), calls);
    expect(view.querySelector('p')!.textContent).toBe('Je vindt die terug onder Mijn feedback.');
    expect(labels(view)).toEqual(['Mijn feedback', 'Meer feedback geven', 'Sluiten']);
    for (const key of ['sent.mine', 'sent.more', 'sent.close']) press(view, key);
    for (const action of Object.values(calls)) expect(action).toHaveBeenCalledOnce();
  });

  it('tells a giver without an account that replies cannot be seen', () => {
    const view = sentView(t, fakeConfig({ account: false }), { mine: vi.fn(), more: vi.fn(), close: vi.fn() });
    expect(view.querySelector('p')!.textContent).toBe(
      'Je stuurde dit zonder account. Daarom kun je reacties van het siteteam niet zien.',
    );
    expect(labels(view)).toEqual(['Meer feedback geven', 'Sluiten']);
  });
});

describe('hiding the button', () => {
  it('offers a minute, until the browser closes, and cancelling', () => {
    const calls = { minute: vi.fn(), session: vi.fn(), back: vi.fn() };
    const view = hidingView(t, calls);
    expect(labels(view)).toEqual(['Een minuut', 'Tot je de browser sluit', 'Annuleren']);
    for (const key of ['hide.minute', 'hide.session', 'hide.cancel']) press(view, key);
    for (const action of Object.values(calls)) expect(action).toHaveBeenCalledOnce();
  });

  it('asks first, names the way back, and posts the token and the page to the hide address', () => {
    const calls = { submit: vi.fn(), back: vi.fn() };
    const config = fakeConfig();
    const link = 'https://content.toon.test/-/feedback/show?returnTo=%2Fteam%2Fsite%2F';
    const view = hideConfirmView(t, config, link, calls);
    const question = view.querySelector('#tf-hide-question')!;
    expect(question.textContent).toBe(`${t('hide.confirm')} ${link}`);
    expect(question.querySelector('a')!.getAttribute('href')).toBe(link);
    // An encoded address has no place to break by itself: at 320 px it would
    // run out of the panel.
    expect(question.querySelector('a')!.className).toBe('url');
    expect(STYLES).toContain('.url { overflow-wrap: anywhere; }');
    expect(view.querySelector('[data-key="cancel"]')!.getAttribute('aria-describedby')).toBe('tf-hide-question');

    press(view, 'hide.confirm');
    const form = calls.submit.mock.calls[0]![0] as HTMLFormElement;
    expect(form.getAttribute('method')).toBe('post');
    expect(form.getAttribute('action')).toBe('/-/feedback/hide');
    expect(form.hidden).toBe(true);
    expect(Object.fromEntries(new FormData(form))).toEqual({ token: 'sealed-token', returnTo: '/team/site/docs/' });

    press(view, 'cancel');
    expect(calls.back).toHaveBeenCalledOnce();
  });
});

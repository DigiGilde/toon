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
  return { point: vi.fn(), text: vi.fn(), page: vi.fn(), mine: vi.fn(), hide: vi.fn(), close: vi.fn() };
}

const hint = () => document.createElement('p');

/** What the ids in an aria-describedby say, in order. */
function description(view: HTMLElement, control: Element): string[] {
  return control
    .getAttribute('aria-describedby')!
    .split(' ')
    .map((id) => view.querySelector(`#${id}`)!.textContent!);
}

describe('the menu', () => {
  it('offers only giving feedback with an account, and hiding the button, on a site that takes it with one only', () => {
    const calls = actions();
    const view = menuView(t, fakeConfig({ identity: 'account', account: false }), calls, hint(), false);
    expect(labels(view)).toEqual([
      'Feedback geven met je account',
      'Privacyverklaring (opent in een nieuw tabblad)',
      'Knop verbergen',
      'Sluiten',
    ]);
    const link = view.querySelector('[data-key="menu.login"]')!;
    expect(link.getAttribute('href')).toBe(fakeConfig().accountUrl);
    press(view, 'menu.hide');
    expect(calls.hide).toHaveBeenCalledOnce();
  });

  it('says what signing in keeps before the way to an account is taken, with the privacy statement', () => {
    const view = menuView(t, fakeConfig({ identity: 'account', account: false }), actions(), hint(), false);
    const link = view.querySelector('[data-key="menu.login"]')!;
    const note = link.nextElementSibling!;
    expect(note.textContent).toBe(
      'Log je in, dan bewaart Toon je SSO-id, e-mailadres en IP-adres tot 90 dagen na je laatste keer inloggen. ' +
        'Mislukt het inloggen, breek je het af of beëindigt de inlogdienst je sessie eerder, ' +
        'dan blijft je IP-adres drie jaar in het toegangslogboek. ' +
        'Privacyverklaring (opent in een nieuw tabblad)',
    );
    expect(description(view, link)).toEqual([note.textContent]);
    const privacy = note.querySelector('a')!;
    expect([privacy.getAttribute('href'), privacy.getAttribute('target'), privacy.getAttribute('rel')]).toEqual([
      'https://beheer.toon.test/-/privacy',
      '_blank',
      'noopener noreferrer',
    ]);
  });

  it('says in English too that a sign-in that fails, is stopped or is ended early keeps the address three years', () => {
    const view = menuView(translator('en'), fakeConfig({ identity: 'account', account: false }), actions(), hint(), true);
    expect(view.querySelector('#tf-login-note')!.textContent).toBe(
      'If you sign in, Toon keeps your SSO ID, email address and IP address until 90 days after the last time you signed in. ' +
        'If signing in fails, you stop it, or the sign-in service ends your session early, ' +
        'your IP address stays in the access log for three years. Privacy statement',
    );
  });

  it('opens the privacy statement in this tab on a shielded page, without a note: nothing is typed yet', () => {
    const view = menuView(t, fakeConfig({ identity: 'account', account: false }), actions(), hint(), true);
    const privacy = view.querySelector<HTMLAnchorElement>('#tf-login-note a')!;
    expect([privacy.textContent, privacy.hasAttribute('target'), privacy.getAttribute('rel')]).toEqual([
      'Privacyverklaring',
      false,
      'noreferrer',
    ]);
    expect(privacy.hasAttribute('aria-describedby')).toBe(false);
    expect(view.textContent).not.toContain(t('composer.privacy.note'));
  });

  it('puts the line about a missing selection under the text button, which it describes', () => {
    const line = hint();
    line.id = 'tf-text-hint';
    const view = menuView(t, fakeConfig(), actions(), line, false);
    const text = view.querySelector('[data-key="menu.text"]')!;
    expect(text.nextElementSibling).toBe(line);
    expect(text.getAttribute('aria-describedby')).toBe('tf-text-hint');
  });

  it('offers the three ways, sending with an account, hiding and closing to a giver without account', () => {
    const view = menuView(t, fakeConfig({ identity: 'anonymous', account: false }), actions(), hint(), false);
    expect(labels(view)).toEqual([
      'Wijs een punt aan',
      'Feedback op geselecteerde tekst',
      'Feedback op deze pagina',
      'Met je account versturen',
      'Knop verbergen',
      'Sluiten',
    ]);
    const link = view.querySelector('[data-key="menu.account"]')!;
    expect(link.getAttribute('href')).toBe(fakeConfig().accountUrl);
    // Unsent text is lost on the way, and what signing in keeps.
    expect(description(view, link)).toEqual([t('menu.account.note'), t('menu.login.note')]);
  });

  it('offers Mijn feedback and stopping to a giver with an account', () => {
    const view = menuView(t, fakeConfig({ identity: 'account', account: true }), actions(), hint(), false);
    expect(labels(view)).toEqual([
      'Wijs een punt aan',
      'Feedback op geselecteerde tekst',
      'Feedback op deze pagina',
      'Mijn feedback',
      'Stoppen met je account',
      'Knop verbergen',
      'Sluiten',
    ]);
    expect(view.querySelector('[data-key="menu.stopAccount"]')!.getAttribute('href')).toBe(fakeConfig().stopUrl);
    expect(view.querySelector('#tf-account-note')).toBeNull();
    expect(view.querySelector('#tf-login-note')).toBeNull();
  });

  it('calls the action of each button', () => {
    const calls = actions();
    const view = menuView(t, fakeConfig({ account: true }), calls, hint(), false);
    for (const key of ['menu.point', 'menu.text', 'menu.page', 'menu.mine', 'menu.hide', 'menu.close']) press(view, key);
    for (const action of Object.values(calls)) expect(action).toHaveBeenCalledOnce();
  });
});

describe('after sending', () => {
  it('points a giver with an account to Mijn feedback', () => {
    const calls = { mine: vi.fn(), more: vi.fn(), close: vi.fn() };
    const view = sentView(t, fakeConfig({ account: true }), calls);
    expect(view.querySelector('p')!.textContent).toBe('Je vindt die terug onder Mijn feedback.');
    expect(labels(view)).toEqual(['Mijn feedback', 'Meer feedback geven', 'Sluiten']);
    for (const key of ['sent.mine', 'sent.more', 'sent.close']) press(view, key);
    for (const action of Object.values(calls)) expect(action).toHaveBeenCalledOnce();
  });

  it('tells a giver without an account that replies cannot be seen', () => {
    const view = sentView(t, fakeConfig({ account: false }), { mine: vi.fn(), more: vi.fn(), close: vi.fn() });
    expect(view.querySelector('p')!.textContent).toBe('Reacties van het siteteam kun je niet zien.');
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

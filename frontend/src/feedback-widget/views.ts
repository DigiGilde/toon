/**
 * The panel's smaller screens: the menu, the thanks after sending, and hiding
 * the button. The menu depends on the identity setting and on consent, so
 * nobody points at the page before learning they cannot send: on a site that
 * takes feedback with an account only, a visitor without one is offered the
 * way to give feedback with an account, and hiding the button, and nothing
 * else. Without a way back to the page the menu offers neither an account nor
 * hiding. The menu links the privacy statement; the consent page after the
 * sign-in says what signing in keeps.
 */
import { button, type Child, h, lineIcon, privacyLink } from './dom';
import type { Translate } from './i18n';
import type { Config } from './types';

export interface MenuActions {
  point(): void;
  text(): void;
  page(): void;
  mine(): void;
  hide(): void;
}

const ICONS = {
  point: 'M12 2v4M12 18v4M2 12h4M18 12h4M19 12a7 7 0 1 1-14 0a7 7 0 1 1 14 0z',
  text: 'M9 4h6M9 20h6M12 4v16',
  page: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h6',
  mine: 'M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01',
  account: 'M16 8a4 4 0 1 1-8 0a4 4 0 1 1 8 0zM4 21a8 8 0 0 1 16 0',
  hide:
    'M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c5 0 9 4.5 10 7a13 13 0 0 1-3.2 4.2M6.6 6.6C4.4 8 2.8 10.1 2 12' +
    'c1 2.5 5 7 10 7a10 10 0 0 0 4.4-1M9.9 9.9a3 3 0 0 0 4.2 4.2',
} as const;

function action(icon: keyof typeof ICONS, text: string, onPress: () => void, key: string, describedBy?: string) {
  const element = h('button', { type: 'button', class: 'action', 'data-key': key, 'aria-describedby': describedBy }, [
    lineIcon(ICONS[icon]),
    h('span', {}, [text]),
  ]);
  element.addEventListener('click', onPress);
  return element;
}

/** A row that goes to a page of Toon, in this tab. */
function navigation(text: string, href: string, key: string, describedBy?: string): HTMLAnchorElement {
  return h('a', { class: 'action', href, 'data-key': key, 'aria-describedby': describedBy }, [
    lineIcon(ICONS.account),
    h('span', {}, [text]),
  ]);
}

function group(rows: Child[][]): HTMLUListElement | null {
  const kept = rows.filter((row) => row.some(Boolean));
  return kept.length === 0 ? null : h('ul', { class: 'actions' }, kept.map((row) => h('li', {}, row)));
}

/** Whether the menu offers the way to an account and hiding the button only:
 * the site takes feedback with an account only, and the visitor has not said
 * yes. */
export function signInOnly(config: Config): boolean {
  return config.identity === 'account' && !config.account;
}

/** The menu. `textHint` is the line under "Feedback on selected text"
 * that says what is missing when there is no selection: the widget keeps it,
 * so it can fill it in without drawing the menu anew. `opaque` is a shielded
 * page, where the privacy statement opens in this tab. `unsent` says the
 * giver typed text that has not gone yet, which a page of Toon in this tab
 * loses: only then does the menu say so. */
export function menuView(
  t: Translate,
  config: Config,
  actions: MenuActions,
  textHint: HTMLElement,
  opaque: boolean,
  unsent: boolean,
): HTMLElement {
  const footer = (leaving?: string) =>
    h('div', { class: 'menu-foot' }, [
      config.showUrl !== null && action('hide', t('menu.hide'), actions.hide, 'menu.hide'),
      privacyLink(t, config.privacyUrl, opaque, leaving),
    ]);
  if (signInOnly(config)) {
    // Nothing can have been typed yet: nothing to lose on the way.
    return h('div', { class: 'menu' }, [
      group([[config.accountUrl !== null && navigation(t('menu.login'), config.accountUrl, 'menu.login')]]),
      footer(),
    ]);
  }
  const toAccount = config.account ? config.stopUrl : config.accountUrl;
  // Only beside a row that leaves this tab: the privacy statement does on a
  // shielded page alone.
  const note = unsent && (toAccount !== null || opaque) ? 'tf-account-note' : undefined;
  return h('div', { class: 'menu' }, [
    group([
      [action('point', t('menu.point'), actions.point, 'menu.point')],
      [action('text', t('menu.text'), actions.text, 'menu.text', textHint.id), textHint],
      [action('page', t('menu.page'), actions.page, 'menu.page')],
    ]),
    group([
      [config.account && action('mine', t('menu.mine'), actions.mine, 'menu.mine')],
      [
        toAccount !== null &&
          (config.account
            ? navigation(t('menu.stopAccount'), toAccount, 'menu.stopAccount', note)
            : navigation(t('menu.account'), toAccount, 'menu.account', note)),
        note !== undefined && h('p', { id: note, class: 'notice' }, [t('menu.account.note')]),
      ],
    ]),
    footer(note),
  ]);
}

export interface SentActions {
  mine(): void;
  more(): void;
  close(): void;
}

export function sentView(t: Translate, config: Config, actions: SentActions): HTMLElement {
  return h('div', {}, [
    h('p', {}, [t(config.account ? 'sent.account' : 'sent.noAccount')]),
    config.account && button(t('menu.mine'), actions.mine, { key: 'sent.mine' }),
    button(t('sent.more'), actions.more, { key: 'sent.more' }),
    button(t('menu.close'), actions.close, { key: 'sent.close' }),
  ]);
}

export interface HidingActions {
  minute(): void;
  session(): void;
  back(): void;
}

export function hidingView(t: Translate, actions: HidingActions): HTMLElement {
  return h('div', {}, [
    button(t('hide.minute'), actions.minute, { key: 'hide.minute' }),
    button(t('hide.session'), actions.session, { key: 'hide.session' }),
    button(t('composer.cancel'), actions.back, { key: 'hide.cancel' }),
  ]);
}

export interface ConfirmActions {
  /** Submit the form that hides the button until the browser closes. */
  submit(form: HTMLFormElement): void;
  back(): void;
}

/** The confirmation before hiding until the browser closes, with the link
 * that brings the button back, and the form that does it: a POST to the hide
 * address with the page token, which reloads the page without the widget. */
export function hideConfirmView(
  t: Translate,
  config: Config,
  showLink: string,
  actions: ConfirmActions,
): HTMLElement {
  const form = h('form', { method: 'post', action: config.hideUrl, hidden: true }, [
    h('input', { type: 'hidden', name: 'token', value: config.token }),
    h('input', { type: 'hidden', name: 'returnTo', value: config.returnTo }),
  ]);
  return h('div', {}, [
    h('p', { id: 'tf-hide-question' }, [t('hide.confirm'), ' ', h('a', { href: showLink, class: 'url' }, [showLink])]),
    h('div', { class: 'row' }, [
      button(t('hide.confirmButton'), () => actions.submit(form), { key: 'hide.confirm', primary: true }),
      button(t('composer.cancel'), actions.back, { key: 'cancel', describedBy: 'tf-hide-question' }),
    ]),
    form,
  ]);
}

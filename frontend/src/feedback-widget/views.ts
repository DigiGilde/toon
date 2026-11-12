/**
 * The panel's smaller screens: the menu, the thanks after sending, and hiding
 * the button. The menu depends on the identity setting and on consent, so
 * nobody points at the page before learning they cannot send: on a site that
 * takes feedback with an account only, a visitor without one is offered the
 * way to give feedback with an account, and hiding the button, and nothing
 * else. Every way to an account says first what signing in keeps.
 */
import { button, type Child, h, privacyLink } from './dom';
import type { Translate } from './i18n';
import type { Config } from './types';

export interface MenuActions {
  point(): void;
  text(): void;
  page(): void;
  mine(): void;
  hide(): void;
  close(): void;
}

function navigation(text: string, href: string, key: string, describedBy?: string): HTMLAnchorElement {
  return h('a', { class: 'btn', href, 'data-key': key, 'aria-describedby': describedBy }, [text]);
}

/** Whether the menu offers the way to an account and hiding the button only:
 * the site takes feedback with an account only, and the visitor has not said
 * yes. */
export function signInOnly(config: Config): boolean {
  return config.identity === 'account' && !config.account;
}

/** What signing in keeps, under each way to an account: the login itself
 * leaves a trace before the consent page can say so. */
function loginNote(t: Translate, after: Child[] = []): HTMLElement {
  return h('p', { id: 'tf-login-note', class: 'notice' }, [t('menu.login.note'), ...after]);
}

/** The menu. `textHint` is the line under "Feedback op geselecteerde tekst"
 * that says what is missing when there is no selection: the widget keeps it,
 * so it can fill it in without drawing the menu anew. `opaque` is a shielded
 * page, where the privacy statement opens in this tab. */
export function menuView(
  t: Translate,
  config: Config,
  actions: MenuActions,
  textHint: HTMLElement,
  opaque: boolean,
): HTMLElement {
  const hide = button(t('menu.hide'), actions.hide, { key: 'menu.hide' });
  const close = button(t('menu.close'), actions.close, { key: 'menu.close' });
  if (signInOnly(config)) {
    // Nothing can have been typed yet: no note that unsent text is lost.
    return h('div', {}, [
      navigation(t('menu.login'), config.accountUrl, 'menu.login', 'tf-login-note'),
      loginNote(t, [' ', privacyLink(t, config.privacyUrl, opaque)]),
      hide,
      close,
    ]);
  }
  return h('div', {}, [
    button(t('menu.point'), actions.point, { key: 'menu.point' }),
    button(t('menu.text'), actions.text, { key: 'menu.text', describedBy: textHint.id }),
    textHint,
    button(t('menu.page'), actions.page, { key: 'menu.page' }),
    config.account && button(t('menu.mine'), actions.mine, { key: 'menu.mine' }),
    config.account
      ? navigation(t('menu.stopAccount'), config.stopUrl, 'menu.stopAccount')
      : navigation(t('menu.account'), config.accountUrl, 'menu.account', 'tf-account-note tf-login-note'),
    !config.account && h('p', { id: 'tf-account-note', class: 'notice' }, [t('menu.account.note')]),
    !config.account && loginNote(t),
    hide,
    close,
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

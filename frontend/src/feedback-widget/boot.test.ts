import { afterEach, describe, expect, it } from 'vitest';

import { boot, CONFIG_ATTRIBUTE } from './boot';
import { FRAGMENT_PREFIX } from './fragment';
import { ACCOUNT_URL, encodeConfig, fakePlatform, SHOW_URL } from './testPlatform';

const CONFIG = {
  v: 1,
  token: 'sealed',
  api: '/-/feedback/v1',
  identity: 'name',
  account: false,
  accountUrl: ACCOUNT_URL,
  hideUrl: '/-/feedback/hide',
  showUrl: SHOW_URL,
  privacyUrl: 'https://beheer.toon.test/-/privacy',
  retentionDays: 90,
};

function script(raw: string | null = encodeConfig(CONFIG)): HTMLScriptElement {
  const element = document.createElement('script');
  if (raw !== null) element.setAttribute(CONFIG_ATTRIBUTE, raw);
  document.head.append(element);
  return element;
}

afterEach(() => {
  document.body.replaceChildren();
  document.head.replaceChildren();
  document.documentElement.removeAttribute('lang');
  window.history.replaceState(null, '', '/');
});

describe('boot', () => {
  it('mounts one widget, reads the configuration once and removes it from the page', () => {
    const platform = fakePlatform();
    const tag = script();
    platform.currentScript.mockReturnValue(tag);
    const widget = boot(platform)!;
    expect(document.body.lastElementChild).toBe(widget.host);
    expect(tag.hasAttribute(CONFIG_ATTRIBUTE)).toBe(false);
    expect(platform.replaceUrl).not.toHaveBeenCalled();
  });

  it('starts no second widget when the script runs twice', () => {
    const platform = fakePlatform();
    platform.currentScript.mockReturnValue(script());
    expect(boot(platform)).not.toBeNull();
    platform.currentScript.mockReturnValue(script());
    expect(boot(platform)).toBeNull();
    expect(document.querySelectorAll('[data-toon-feedback-host]').length).toBe(1);
  });

  it('starts nothing without its script tag', () => {
    expect(boot(fakePlatform())).toBeNull();
    expect(document.querySelector('[data-toon-feedback-host]')).toBeNull();
  });

  it('starts nothing for a configuration it cannot read, and still removes it', () => {
    const platform = fakePlatform();
    const tag = script('not-a-config');
    platform.currentScript.mockReturnValue(tag);
    expect(boot(platform)).toBeNull();
    expect(tag.hasAttribute(CONFIG_ATTRIBUTE)).toBe(false);
    platform.currentScript.mockReturnValue(script(null));
    expect(boot(platform)).toBeNull();
  });

  it('starts nothing on a document without a body', () => {
    const platform = fakePlatform();
    platform.currentScript.mockReturnValue(script());
    const body = document.body;
    body.remove();
    try {
      expect(boot(platform)).toBeNull();
    } finally {
      document.documentElement.append(body);
    }
  });

  it('speaks the language of the page, else the browser', () => {
    const platform = fakePlatform();
    platform.currentScript.mockReturnValue(script());
    document.documentElement.setAttribute('lang', 'en-GB');
    expect(boot(platform)!.host.getAttribute('lang')).toBe('en');
    document.body.replaceChildren();
    document.documentElement.setAttribute('lang', 'fy');
    platform.languages.mockReturnValue(['nl-NL']);
    platform.currentScript.mockReturnValue(script());
    expect(boot(platform)!.host.getAttribute('lang')).toBe('nl');
  });

  it('shows the place a #toon-feedback= link points at, and takes the fragment off the address', () => {
    const platform = fakePlatform();
    platform.currentScript.mockReturnValue(script());
    const highlight = encodeConfig({ k: 'point', px: 30, py: 40 });
    window.history.replaceState(null, '', `/team/site/docs/?x=1${FRAGMENT_PREFIX}${highlight}`);
    boot(platform);
    expect(platform.replaceUrl).toHaveBeenCalledWith('/team/site/docs/?x=1');
    expect(platform.scrollToPage).toHaveBeenCalledWith(30, 40);
  });

  it('says so when the quote of a link is no longer on the page, as after a fixed typo', () => {
    const platform = fakePlatform();
    platform.currentScript.mockReturnValue(script());
    document.body.append(Object.assign(document.createElement('p'), { textContent: 'De typfout is hersteld.' }));
    // What the admin's "Bekijk op de pagina" sends for a text: a quote, no page coordinates.
    const highlight = encodeConfig({ k: 'text', s: 'body > p:nth-of-type(1)', q: 'tpyfout' });
    window.history.replaceState(null, '', `/team/site/docs/${FRAGMENT_PREFIX}${highlight}`);
    const widget = boot(platform)!;
    expect(platform.replaceUrl).toHaveBeenCalledWith('/team/site/docs/');
    expect(platform.scrollToPage).not.toHaveBeenCalled();
    expect(widget.root.querySelector<HTMLElement>('.lost')!.hidden).toBe(false);
    expect(widget.root.querySelector('[role="status"]')!.textContent).toBe(
      'De plek van deze feedback is op deze pagina niet meer te vinden.',
    );
  });

  it('takes a fragment it cannot read off the address too, and shows nothing', () => {
    const platform = fakePlatform();
    platform.currentScript.mockReturnValue(script());
    window.history.replaceState(null, '', `/team/site/${FRAGMENT_PREFIX}broken`);
    boot(platform);
    expect(platform.replaceUrl).toHaveBeenCalledWith('/team/site/');
    expect(platform.scrollToPage).not.toHaveBeenCalled();
  });

  it('leaves another fragment alone', () => {
    const platform = fakePlatform();
    platform.currentScript.mockReturnValue(script());
    window.history.replaceState(null, '', '/team/site/#section-2');
    boot(platform);
    expect(platform.replaceUrl).not.toHaveBeenCalled();
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    expect(platform.replaceUrl).not.toHaveBeenCalled();
  });

  it('follows a later link to the same page, which only changes the fragment', () => {
    const platform = fakePlatform();
    platform.currentScript.mockReturnValue(script());
    boot(platform);
    window.history.replaceState(null, '', `/team/site/docs/${FRAGMENT_PREFIX}${encodeConfig({ k: 'point', px: 7, py: 8 })}`);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    expect(platform.replaceUrl).toHaveBeenCalledWith('/team/site/docs/');
    expect(platform.scrollToPage).toHaveBeenCalledWith(7, 8);
  });
});

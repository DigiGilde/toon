import { afterEach, describe, expect, it } from 'vitest';

import { boot, CONFIG_ATTRIBUTE } from './boot';
import { FRAGMENT_PREFIX } from './fragment';
import { PLACE_ATTRIBUTE } from './place';
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
  screenshotMaxBytes: 2097152,
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
    // What the admin's "Show on the site" sends for a text: a quote, no page coordinates.
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

  it('puts the fragment the place was given at back in the address', () => {
    const platform = fakePlatform();
    platform.currentScript.mockReturnValue(script());
    platform.replaceUrl.mockImplementation((url) => window.history.replaceState(null, '', url));
    const highlight = encodeConfig({ k: 'point', px: 30, py: 40, f: '#/7' });
    window.history.replaceState(null, '', `/team/site/deck/?x=1${FRAGMENT_PREFIX}${highlight}`);
    boot(platform);
    expect(platform.replaceUrl).toHaveBeenCalledWith('/team/site/deck/?x=1#/7');
    expect(platform.scrollToPage).toHaveBeenCalledWith(30, 40);
    // Its hashchange listener stays on the window: it may not move the address for later tests.
    platform.replaceUrl.mockReset();
  });

  it('shows the place of a link the place script took over, leaving the address as the page made it', () => {
    const platform = fakePlatform();
    const tag = script();
    tag.setAttribute(PLACE_ATTRIBUTE, `${FRAGMENT_PREFIX}${encodeConfig({ k: 'point', px: 30, py: 40, f: '#/7' })}`);
    platform.currentScript.mockReturnValue(tag);
    // The deck has read #/7 and rewritten the address as its own.
    window.history.replaceState(null, '', '/team/site/deck/#/7');
    boot(platform);
    expect(tag.hasAttribute(PLACE_ATTRIBUTE)).toBe(false);
    expect(platform.replaceUrl).not.toHaveBeenCalled();
    expect(platform.reload).not.toHaveBeenCalled();
    expect(platform.scrollToPage).toHaveBeenCalledWith(30, 40);
  });

  it('ignores what a page put in the place attribute that is no link', () => {
    const platform = fakePlatform();
    const tag = script();
    tag.setAttribute(PLACE_ATTRIBUTE, '#/7');
    platform.currentScript.mockReturnValue(tag);
    boot(platform);
    expect(platform.replaceUrl).not.toHaveBeenCalled();
    expect(platform.scrollToPage).not.toHaveBeenCalled();
  });

  it('loads the page again for a later link to a place on a view of its own, such as a slide', () => {
    const platform = fakePlatform();
    platform.currentScript.mockReturnValue(script());
    boot(platform);
    window.history.replaceState(null, '', `/team/site/deck/${FRAGMENT_PREFIX}${encodeConfig({ k: 'point', px: 7, py: 8, f: '#/3' })}`);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    expect(platform.reload).toHaveBeenCalledOnce();
    expect(platform.replaceUrl).not.toHaveBeenCalled();
    expect(platform.scrollToPage).not.toHaveBeenCalled();
  });

  it('follows a later link to a place at a fragment that names an element without loading the page again', () => {
    const platform = fakePlatform();
    platform.currentScript.mockReturnValue(script());
    document.body.append(Object.assign(document.createElement('h2'), { id: 'intro' }));
    boot(platform);
    window.history.replaceState(null, '', `/team/site/docs/${FRAGMENT_PREFIX}${encodeConfig({ k: 'point', px: 7, py: 8, f: '#intro' })}`);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    expect(platform.reload).not.toHaveBeenCalled();
    expect(platform.replaceUrl).toHaveBeenCalledWith('/team/site/docs/#intro');
    expect(platform.scrollToPage).toHaveBeenCalledWith(7, 8);
  });
});

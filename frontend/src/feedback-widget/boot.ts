/**
 * Starting the widget from its script tag: read the configuration once and
 * remove it from the page, mount one widget, and show the place a
 * #toon-feedback= link points at. A page that loads the script twice gets one
 * widget; a configuration that does not parse gets none.
 */
import { parseConfig } from './config';
import { isOurFragment, readFragment } from './fragment';
import { detectLanguage } from './i18n';
import type { Platform } from './types';
import { Widget } from './widget';

export const CONFIG_ATTRIBUTE = 'data-toon-feedback';

/** Show the place in the address, and take it off the address. */
function followFragment(platform: Platform, widget: Widget): void {
  const hash = location.hash;
  if (!isOurFragment(hash)) return;
  platform.replaceUrl(`${location.pathname}${location.search}`);
  const highlight = readFragment(hash);
  if (highlight !== null) widget.highlightWhenLoaded(highlight);
}

export function boot(platform: Platform): Widget | null {
  if (document.querySelector('[data-toon-feedback-host]') !== null) return null;
  const script = platform.currentScript();
  if (script === null) return null;
  const raw = script.getAttribute(CONFIG_ATTRIBUTE);
  script.removeAttribute(CONFIG_ATTRIBUTE);
  const config = parseConfig(raw);
  if (config === null || document.body === null) return null;
  const lang = detectLanguage(document.documentElement.getAttribute('lang'), platform.languages());
  const widget = new Widget({ config, lang, platform });
  widget.mount();
  followFragment(platform, widget);
  // A second link to the same page only changes the fragment: no new load.
  window.addEventListener('hashchange', () => followFragment(platform, widget));
  return widget;
}

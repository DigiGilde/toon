/**
 * Starting the widget from its script tag: read the configuration once and
 * remove it from the page, mount one widget, and show the place a
 * #toon-feedback= link points at. A page that loads the script twice gets one
 * widget; a configuration that does not parse gets none.
 */
import { parseConfig } from './config';
import { isOurFragment, readFragment } from './fragment';
import { detectLanguage } from './i18n';
import { CONFIG_ATTRIBUTE, PLACE_ATTRIBUTE } from './place';
import type { Platform } from './types';
import { Widget } from './widget';

export { CONFIG_ATTRIBUTE };

/**
 * Show the place in the address, and put the fragment it was given at back
 * in its stead. A link the place script took over as the page started
 * (`handed`) is in the address no more. A second link to the same page only
 * changes the fragment (`later`): the page's own script has read its address
 * already, so for a place on a view of its own, such as a slide, the page is
 * loaded again and the place script takes the link.
 */
function followFragment(platform: Platform, widget: Widget, hash: string, handed: boolean, later = false): void {
  if (!isOurFragment(hash)) return;
  const highlight = readFragment(hash);
  const fragment = highlight?.f ?? null;
  if (later && fragment !== null && document.getElementById(fragment.slice(1)) === null) {
    platform.reload();
    return;
  }
  if (!handed) platform.replaceUrl(`${location.pathname}${location.search}${fragment ?? ''}`);
  if (highlight !== null) widget.highlightWhenLoaded(highlight);
}

export function boot(platform: Platform): Widget | null {
  if (document.querySelector('[data-toon-feedback-host]') !== null) return null;
  const script = platform.currentScript();
  if (script === null) return null;
  const raw = script.getAttribute(CONFIG_ATTRIBUTE);
  const handed = script.getAttribute(PLACE_ATTRIBUTE);
  script.removeAttribute(CONFIG_ATTRIBUTE);
  script.removeAttribute(PLACE_ATTRIBUTE);
  const config = parseConfig(raw);
  if (config === null || document.body === null) return null;
  const lang = detectLanguage(document.documentElement.getAttribute('lang'), platform.languages());
  const widget = new Widget({ config, lang, platform });
  widget.mount();
  if (handed !== null) followFragment(platform, widget, handed, true);
  else followFragment(platform, widget, location.hash, false);
  window.addEventListener('hashchange', () => followFragment(platform, widget, location.hash, false, true));
  return widget;
}

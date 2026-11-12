/**
 * The widget's language: the page's own `<html lang>` when that is Dutch or
 * English, else the first of the browser's languages that is, else English.
 * The SPA's language module is not used here: it brings Vue and sets the
 * page's own `lang`, which belongs to the site.
 */
import { widgetEn } from '../i18n/widget.en';
import { widgetNl } from '../i18n/widget.nl';
import type { Lang } from './types';

export type WidgetKey = keyof typeof widgetNl;
export type Params = Record<string, string | number>;
export type Translate = (key: WidgetKey, params?: Params) => string;

const CATALOGUES: Record<Lang, Record<WidgetKey, string>> = { nl: widgetNl, en: widgetEn };

/** European English, as in the SPA: 24-hour times and day-month dates. */
const INTL_LOCALES: Record<Lang, string> = { nl: 'nl-NL', en: 'en-GB' };

function primaryLanguage(tag: string | null | undefined): Lang | null {
  const base = (tag ?? '').trim().split('-')[0]!.toLowerCase();
  return base === 'nl' || base === 'en' ? base : null;
}

export function detectLanguage(htmlLang: string | null, languages: readonly string[]): Lang {
  const fromPage = primaryLanguage(htmlLang);
  if (fromPage !== null) return fromPage;
  for (const tag of languages) {
    const lang = primaryLanguage(tag);
    if (lang !== null) return lang;
  }
  return 'en';
}

/** The language a node of the page is in, as the page says it: the lang
 * attribute on it or on the closest element around it, out of the shadow
 * roots it is in through their hosts. Null where the page does not say, or
 * says it does not know. */
export function pageLanguage(node: Node): string | null {
  for (let at: Node | null = node; at !== null; at = at instanceof ShadowRoot ? at.host : at.parentNode) {
    if (at instanceof Element && at.hasAttribute('lang')) return at.getAttribute('lang')!.trim() || null;
  }
  return null;
}

/** A language of the page to mark the page's words with in the widget: only
 * one that is not the widget's own. */
export function otherLanguage(tag: string | null, lang: Lang): string | null {
  return tag === null || tag.split('-')[0]!.toLowerCase() === lang ? null : tag;
}

export function translator(lang: Lang): Translate {
  const catalogue = CATALOGUES[lang];
  return (key, params) => {
    const message = catalogue[key];
    if (params === undefined) return message;
    return message.replace(/\{(\w+)\}/g, (whole, name: string) =>
      name in params ? String(params[name]) : whole,
    );
  };
}

export function formatDate(iso: string, lang: Lang): string {
  try {
    return new Intl.DateTimeFormat(INTL_LOCALES[lang], {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(iso));
  } catch {
    return '';
  }
}

/** "1 second", "40 seconds", "3 minutes": how long until a retry. */
export function formatWait(seconds: number, t: Translate): string {
  const whole = Math.max(1, Math.ceil(seconds));
  if (whole <= 90) return whole === 1 ? t('time.second') : t('time.seconds', { n: whole });
  const minutes = Math.ceil(whole / 60);
  return t('time.minutes', { n: minutes });
}

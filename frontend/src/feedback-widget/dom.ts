/**
 * Building the widget's elements. Text only ever goes in as text nodes: no
 * innerHTML, so nothing a giver, the site team or a page wrote can become
 * markup.
 */
import type { Translate } from './i18n';
import type { PageWords } from './types';

export type Child = Node | string | null | undefined | false;
export type Attributes = Record<string, string | number | boolean | null | undefined>;
/** What the status region says: text, or text with words of the page in it. */
export type Message = string | Child[];

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attributes: Attributes = {},
  children: Child[] = [],
): HTMLElementTagNameMap[K] {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === undefined || value === false) continue;
    element.setAttribute(name, value === true ? '' : String(value));
  }
  append(element, children);
  return element;
}

export function append(parent: Node, children: Child[]): void {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    parent.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
}

/** A message with words of the page in its `{description}`. Words in
 * another language than the widget's go in a span that says which, so a
 * screen reader can say them in it. */
export function withWords(message: string, words: PageWords): Message {
  if (words.lang === null) return message.replace('{description}', () => words.text);
  const [before, after] = message.split('{description}');
  return [before, h('span', { lang: words.lang }, [words.text]), after];
}

/** Replace everything inside `parent`. */
export function replace(parent: Node, children: Child[]): void {
  while (parent.firstChild) parent.removeChild(parent.firstChild);
  append(parent, children);
}

export interface ButtonOptions {
  key?: string;
  primary?: boolean;
  describedBy?: string;
  className?: string;
}

/** A labelled button; `key` lets the focus plan find it again after a redraw. */
export function button(text: string, onPress: () => void, options: ButtonOptions = {}): HTMLButtonElement {
  const classes = ['btn', options.primary ? 'btn-primary' : '', options.className ?? ''].filter(Boolean);
  const element = h('button', {
    type: 'button',
    class: classes.join(' '),
    'data-key': options.key,
    'aria-describedby': options.describedBy,
  }, [text]);
  element.addEventListener('click', onPress);
  return element;
}

/** The link to the privacy statement. On an opaque origin (a shielded site) a
 * new tab would inherit the sandbox, in which the statement cannot load: there
 * it opens in this tab. */
export function privacyLink(t: Translate, href: string, opaque: boolean, describedBy?: string): HTMLAnchorElement {
  return opaque
    ? h('a', { href, rel: 'noreferrer', 'aria-describedby': describedBy }, [t('composer.privacy')])
    : h('a', { href, target: '_blank', rel: 'noopener noreferrer' }, [
        t('composer.privacy'),
        h('span', { class: 'vh' }, [` ${t('widget.newTab')}`]),
      ]);
}

/** A button that copies, and the line beside it that says how that went. The
 * line is not a live region: the status region says it once already. */
export function copyControl(text: string, copy: () => Promise<string>, key: string): [HTMLButtonElement, HTMLElement] {
  const notice = h('p', { class: 'hint' });
  const control = button(text, () => {
    void copy().then((message) => {
      notice.textContent = message;
    });
  }, { key });
  return [control, notice];
}

const TABBABLE = 'a[href], button, input:not([type="hidden"]), textarea, select, [tabindex]:not([tabindex="-1"])';

/** The controls under `root` that Tab reaches, in order. The widget disables
 * nothing, so only what is hidden drops out. */
export function tabbable(root: ParentNode): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(TABBABLE)).filter(
    (element) => element.closest('[hidden]') === null,
  );
}

/** An element in the SVG namespace, for the icons. */
export function svg(tag: string, attributes: Record<string, string>, children: Element[] = []): SVGElement {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  for (const child of children) element.appendChild(child);
  return element;
}

export function speechBubbleIcon(): SVGElement {
  return svg('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true', focusable: 'false' }, [
    svg('path', {
      d: 'M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H10l-5 4v-4H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': '2',
      'stroke-linejoin': 'round',
    }),
  ]);
}

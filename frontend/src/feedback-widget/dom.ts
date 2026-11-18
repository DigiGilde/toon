/**
 * Building the widget's elements. Text only ever goes in as text nodes: no
 * innerHTML, so nothing a giver, the site team or a page wrote can become
 * markup.
 */
import type { Translate } from './i18n';
import { type Check, checkText } from './text';
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
    if (typeof child === 'string') {
      const text = document.createTextNode(child);
      parent.appendChild(text);
    } else {
      parent.appendChild(child);
    }
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

/** A line icon on a 24 px grid, drawn in the text colour. */
export function lineIcon(d: string): SVGElement {
  return svg('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true', focusable: 'false', class: 'icon' }, [
    svg('path', {
      d,
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': '2',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
    }),
  ]);
}

const ERROR_ICON = 'M21 12a9 9 0 1 1-18 0a9 9 0 1 1 18 0zM12 7.5v5M12 16.5v.01';

/** The message of one field, between its label and the field, which it
 * describes. It is empty while there is nothing to say: a description that
 * is only hidden is still read out. Only an error marks the field invalid; a
 * message about valid text, such as reaching the limit, does not. */
export interface FieldError {
  readonly element: HTMLElement;
  /** The field is marked invalid. */
  readonly invalid: boolean;
  show(message: string, options?: { invalid?: boolean }): void;
  clear(): void;
}

export function fieldError(field: HTMLElement, id: string): FieldError {
  const element = h('p', { id, class: 'error' });
  return {
    element,
    get invalid() {
      return field.hasAttribute('aria-invalid');
    },
    show(message, { invalid = true } = {}) {
      replace(element, [lineIcon(ERROR_ICON), message]);
      if (invalid) field.setAttribute('aria-invalid', 'true');
      else field.removeAttribute('aria-invalid');
    },
    clear() {
      replace(element, []);
      field.removeAttribute('aria-invalid');
    },
  };
}

export interface LimitedText {
  /** The text changed: say once that it is full, and follow an error shown. */
  typed(): void;
  /** Mark the field after a check before sending. */
  mark(check: Check): void;
}

/** A multiline field that `maxlength` holds at its limit, in UTF-16 units,
 * never more characters than the API takes. Once it is full the message says
 * so, between label and field, and the status region says it once, without
 * marking the text invalid: it is the limit, not past it. An error shown
 * (empty, a character that cannot go) follows the text while it shows, and
 * goes as soon as the text is right; before a first check typing marks
 * nothing. A draft that is full already shows the message without saying it. */
export function limitedText(
  field: HTMLTextAreaElement,
  error: FieldError,
  messages: { full: string; problem(reason: 'empty' | 'long' | 'chars'): string },
  announce: (message: string) => void,
): LimitedText {
  let full = field.value.length >= field.maxLength;
  const mark = (check: Check) => {
    if (!check.ok) error.show(messages.problem(check.reason));
    else if (full) error.show(messages.full, { invalid: false });
    else error.clear();
  };
  const typed = () => {
    const now = field.value.length >= field.maxLength;
    if (now && !full) announce(messages.full);
    full = now;
    if (error.invalid) mark(checkText(field.value, field.maxLength, true));
    else if (full) error.show(messages.full, { invalid: false });
    else error.clear();
  };
  if (full) error.show(messages.full, { invalid: false });
  return { typed, mark };
}

/** Put a message in an alert that is not about one field, with the error
 * icon; null empties it. */
export function setAlert(element: HTMLElement, message: string | null): void {
  replace(element, message === null ? [] : [lineIcon(ERROR_ICON), message]);
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

/**
 * A text field of the widget that turns into the NLDD markdown editor once
 * someone starts writing in it. The editor runs in a frame of its own
 * (feedback-editor/, protocol.ts): a sandboxed document with an opaque
 * origin, so it defines its custom elements in its own registry, its styles
 * stay inside it, and nothing it does can reach the site's page, nor the
 * site's page reach into it.
 *
 * The textarea stays the field: the composer and "My feedback" read, check
 * and mark it as before, and the editor follows. Each change in the editor
 * is written into it as an input event; its invalid mark and its message
 * go to the editor, which cannot point at them across the frame. While the
 * frame loads, the textarea takes the typing. A frame that does not get
 * ready in time, or fails later, goes, and the textarea comes back with
 * the text so far.
 */
import { type FromFrame, HELLO, type Init, type Scheme, type ToFrame } from '../feedback-editor/protocol';
import { h } from './dom';
import type { Translate } from './i18n';
import type { Lang, Platform } from './types';

/** How long the frame gets to say it is ready. */
export const EDITOR_TIMEOUT = 8000;

export type EditorPlatform = Pick<Platform, 'connectFrame' | 'colorScheme' | 'onColorSchemeChange'>;

export interface EditorParts {
  field: HTMLTextAreaElement;
  label: HTMLLabelElement;
  /** The field's message, which describes it. */
  message: HTMLElement;
}

export interface EditorOptions extends EditorParts {
  /** The frame's address; null keeps the textarea. */
  url: string | null;
  t: Translate;
  lang: Lang;
  platform: EditorPlatform;
}

export class EditorField {
  private frame: HTMLIFrameElement | null = null;
  private port: MessagePort | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private live = false;
  /** The text the editor holds as far as the widget knows. */
  private shown = '';
  private stops: Array<() => void> = [];

  constructor(private readonly options: EditorOptions) {
    const { field, label, url } = options;
    if (url === null) return;
    field.addEventListener('focus', () => this.start(), { once: true });
    // The label points at the textarea, which hides while the editor shows.
    label.addEventListener('click', (event) => {
      if (!this.live) return;
      event.preventDefault();
      this.focus();
    });
  }

  /** The editor shows in place of the textarea. */
  get showing(): boolean {
    return this.live;
  }

  focus(): void {
    if (!this.live) {
      this.options.field.focus();
      return;
    }
    this.frame!.focus();
    this.post({ type: 'focus' });
  }

  /** Select all the text, for someone to copy it by hand. */
  select(): void {
    if (!this.live) {
      this.options.field.focus();
      this.options.field.select();
      return;
    }
    this.frame!.focus();
    this.post({ type: 'select' });
  }

  /** The textarea's text was changed by the widget itself. */
  sync(): void {
    const { value } = this.options.field;
    if (!this.live || value === this.shown) return;
    this.shown = value;
    this.post({ type: 'value', value });
  }

  /** Take the frame away for good, leaving the textarea as it is. */
  dispose(): void {
    this.teardown();
  }

  private start(): void {
    const { t, url, field } = this.options;
    this.frame = h('iframe', {
      class: 'editor',
      src: url,
      sandbox: 'allow-scripts',
      title: t('editor.title'),
    });
    field.after(this.frame);
    addEventListener('message', this.hello);
    this.timer = setTimeout(() => this.fail(), EDITOR_TIMEOUT);
  }

  private readonly hello = (event: MessageEvent) => {
    const frame = this.frame!.contentWindow;
    if (event.source !== frame || (event.data as { type?: unknown } | null)?.type !== HELLO) return;
    removeEventListener('message', this.hello);
    const { field, label, lang, platform } = this.options;
    this.port = platform.connectFrame(frame!);
    this.port.onmessage = (message) => this.receive(message.data as FromFrame);
    this.shown = field.value;
    const init: Init = {
      type: 'init',
      value: field.value,
      label: label.textContent!,
      lang,
      maxLength: field.maxLength,
      rows: field.rows,
      scheme: platform.colorScheme(),
      ...this.state(),
    };
    this.post(init);
  };

  private receive(message: FromFrame): void {
    switch (message.type) {
      case 'ready':
        this.show(message.height);
        break;
      case 'input':
        this.typed(message.value);
        break;
      case 'height':
        this.frame!.style.height = `${message.height}px`;
        break;
      case 'escape':
        // The panel's own Escape, as if pressed in the panel.
        this.frame!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, composed: true }));
        break;
      case 'failed':
        this.fail();
        break;
    }
  }

  private show(height: number): void {
    if (this.live) return;
    const { field, message, platform } = this.options;
    clearTimeout(this.timer!);
    this.timer = null;
    this.live = true;
    // Typed in the textarea while the frame got ready.
    this.sync();
    const frame = this.frame!;
    const root = field.getRootNode() as Document | ShadowRoot;
    const hadFocus = root.activeElement === field;
    frame.style.height = `${height}px`;
    frame.classList.add('live');
    field.hidden = true;
    this.moveKey(field, frame);
    const observer = new MutationObserver(() => this.post({ type: 'state', ...this.state() }));
    observer.observe(field, { attributes: true, attributeFilter: ['aria-invalid'] });
    observer.observe(message, { childList: true, subtree: true, characterData: true });
    this.stops.push(
      () => observer.disconnect(),
      platform.onColorSchemeChange((scheme: Scheme) => this.post({ type: 'scheme', scheme })),
    );
    if (hadFocus) this.focus();
  }

  private typed(value: string): void {
    const { field } = this.options;
    this.shown = value;
    field.value = value;
    field.dispatchEvent(new Event('input'));
  }

  private fail(): void {
    const { field } = this.options;
    const root = field.getRootNode() as Document | ShadowRoot;
    const hadFocus = this.frame !== null && root.activeElement === this.frame;
    const wasLive = this.live;
    this.teardown();
    if (!wasLive) return;
    field.hidden = false;
    if (hadFocus) field.focus();
  }

  private teardown(): void {
    removeEventListener('message', this.hello);
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    for (const stop of this.stops) stop();
    this.stops = [];
    this.port?.close();
    this.port = null;
    if (this.frame !== null && this.live) this.moveKey(this.frame, this.options.field);
    this.frame?.remove();
    this.frame = null;
    this.live = false;
  }

  /** The focus plan finds a field by its key: on the frame while it shows. */
  private moveKey(from: HTMLElement, to: HTMLElement): void {
    const key = from.getAttribute('data-key');
    if (key === null) return;
    from.removeAttribute('data-key');
    to.setAttribute('data-key', key);
  }

  private state(): { invalid: boolean; description: string } {
    const { field, message } = this.options;
    return { invalid: field.getAttribute('aria-invalid') === 'true', description: message.textContent! };
  }

  private post(message: ToFrame): void {
    this.port!.postMessage(message);
  }
}

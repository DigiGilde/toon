/**
 * The frame's side of the editor protocol (protocol.ts): say hello to the
 * parent, take the one port the parent hands over, and from then on show
 * the field it describes and tell it every change. Messages from anywhere
 * else, and a second port, are ignored.
 */
import { type FromFrame, HELLO, type Init, PORT, type ToFrame } from './protocol';

/** What the frame needs of the editor element (editor.ts). */
export interface FrameEditor extends HTMLElement {
  value: string;
  invalid: boolean;
  rows: number;
  accessibleLabel: string;
  maxLength: number;
  describedByElements: readonly Element[];
  translations: Record<string, string>;
  readonly updateComplete: Promise<boolean>;
  selectAll(): void;
  caretToEnd(): void;
  /** A list or message of the editor shows over the text. */
  showsPopup(): boolean;
}

export interface FrameOptions {
  window: Window;
  create(): FrameEditor;
  /** The editor's own words for assistive software, in a language. */
  words(lang: Init['lang']): Record<string, string>;
  /** Calls `listener` whenever the element changes size. */
  observeSize(element: Element, listener: () => void): void;
}

export function startFrame(options: FrameOptions): void {
  const { window: win } = options;
  const doc = win.document;
  let port: MessagePort | null = null;
  let editor: FrameEditor | null = null;
  let sent = '';
  const description = doc.createElement('p');
  description.id = 'description';
  description.hidden = true;

  const send = (message: FromFrame) => port?.postMessage(message);
  const height = () => Math.ceil(doc.body.getBoundingClientRect().height);

  const state = (invalid: boolean, text: string) => {
    editor!.invalid = invalid;
    description.textContent = text;
    editor!.describedByElements = text === '' ? [] : [description];
  };

  const show = async (init: Init) => {
    const root = doc.documentElement;
    root.lang = init.lang;
    root.dataset.scheme = init.scheme;
    const created = options.create();
    created.rows = init.rows;
    created.maxLength = init.maxLength;
    created.value = init.value;
    created.accessibleLabel = init.label;
    created.translations = options.words(init.lang);
    editor = created;
    sent = init.value;
    state(init.invalid, init.description);
    // The editor's own input event and the native one from inside it both
    // arrive here; only a value that changed goes out.
    created.addEventListener('input', () => {
      if (created.value === sent) return;
      sent = created.value;
      send({ type: 'input', value: sent });
    });
    doc.body.append(created, description);
    await created.updateComplete;
    created.caretToEnd();
    options.observeSize(doc.body, () => send({ type: 'height', height: height() }));
    send({ type: 'ready', height: height() });
  };

  const receive = (message: ToFrame) => {
    if (message.type === 'init') {
      if (editor === null) show(message).catch(() => send({ type: 'failed' }));
      return;
    }
    if (editor === null) return;
    switch (message.type) {
      case 'value':
        sent = message.value;
        editor.value = message.value;
        break;
      case 'state':
        state(message.invalid, message.description);
        break;
      case 'scheme':
        doc.documentElement.dataset.scheme = message.scheme;
        break;
      case 'focus':
        editor.focus();
        break;
      case 'select':
        editor.selectAll();
        break;
    }
  };

  win.addEventListener('message', (event) => {
    if (port !== null || event.source !== win.parent || event.ports.length !== 1) return;
    if ((event.data as { type?: unknown } | null)?.type !== PORT) return;
    port = event.ports[0]!;
    port.onmessage = (message) => receive(message.data as ToFrame);
  });
  // Focus given to the frame itself, by Tab or by the widget, goes on to the
  // editor; a press inside the editor has put it there already.
  win.addEventListener('focus', () => {
    if (doc.activeElement === doc.body) editor?.focus();
  });
  // Escape closes a list the editor shows, and is the panel's otherwise.
  // Seen before the editor: CodeMirror also takes an Escape when nothing
  // shows, to drop a suggestion it was still looking for.
  win.addEventListener(
    'keydown',
    (event) => {
      if (event.key !== 'Escape' || event.isComposing || editor === null || editor.showsPopup()) return;
      send({ type: 'escape' });
    },
    { capture: true },
  );
  win.addEventListener('error', () => send({ type: 'failed' }));
  win.addEventListener('unhandledrejection', () => send({ type: 'failed' }));
  win.parent.postMessage({ type: HELLO }, '*');
}

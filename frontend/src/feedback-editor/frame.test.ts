import { afterEach, describe, expect, it, vi } from 'vitest';

import { type FrameEditor, type FrameOptions, startFrame } from './frame';
import { type FromFrame, HELLO, type Init, PORT, type ToFrame } from './protocol';

/** The editor element, without CodeMirror, which jsdom cannot run. */
class StubEditor extends HTMLElement implements FrameEditor {
  value = '';
  invalid = false;
  rows = 0;
  accessibleLabel = '';
  maxLength = 0;
  describedByElements: readonly Element[] = [];
  translations: Record<string, string> = {};
  updateComplete = Promise.resolve(true);
  selectAll = vi.fn();
  caretToEnd = vi.fn();
  popup = false;
  showsPopup(): boolean {
    return this.popup;
  }
}
customElements.define('stub-editor', StubEditor);

const INIT: Init = {
  type: 'init',
  value: 'Eerste **tekst**',
  label: 'Je feedback',
  lang: 'en',
  maxLength: 5000,
  rows: 5,
  scheme: 'dark',
  invalid: false,
  description: '',
};

class FakePort {
  sent: FromFrame[] = [];
  onmessage: ((event: { data: ToFrame }) => void) | null = null;
  postMessage(message: FromFrame): void {
    this.sent.push(message);
  }
  say(message: ToFrame): void {
    this.onmessage!({ data: message });
  }
}

afterEach(() => {
  document.body.innerHTML = '';
  document.documentElement.removeAttribute('lang');
  delete document.documentElement.dataset.scheme;
});

function setUp(create?: () => FrameEditor) {
  const parent = { postMessage: vi.fn() };
  const win = Object.assign(new EventTarget(), { document, parent });
  const editors: StubEditor[] = [];
  const sizes: Array<[Element, () => void]> = [];
  const options: FrameOptions = {
    window: win as unknown as Window,
    create:
      create ??
      (() => {
        const editor = document.createElement('stub-editor') as StubEditor;
        editors.push(editor);
        return editor;
      }),
    words: (lang): Record<string, string> => (lang === 'en' ? { 'components.text-editor.task-open-label': 'Task, open' } : {}),
    observeSize: (element, listener) => sizes.push([element, listener]),
  };
  startFrame(options);
  const port = new FakePort();
  const message = (init: { source?: unknown; data?: unknown; ports?: unknown[] }) =>
    win.dispatchEvent(
      Object.assign(new Event('message'), { source: parent, data: { type: PORT }, ports: [port], ...init }),
    );
  const connect = () => message({});
  const editor = () => editors[0]!;
  /** Connect, describe the field, and wait until the frame says it is ready. */
  const show = async (init: Partial<Init> = {}) => {
    connect();
    port.say({ ...INIT, ...init });
    await vi.waitFor(() => expect(port.sent.some((sent) => sent.type === 'ready')).toBe(true));
  };
  return { win, parent, port, message, connect, editor, editors, sizes, show };
}

describe('the handshake', () => {
  it('says hello to its parent, to any origin, as its own is opaque', () => {
    const { parent } = setUp();
    expect(parent.postMessage).toHaveBeenCalledWith({ type: HELLO }, '*');
  });

  it.each([
    ['another window', { source: {} }],
    ['no port', { ports: [] }],
    ['two ports', { ports: [{}, {}] }],
    ['another message', { data: { type: 'something' } }],
    ['no data', { data: null }],
  ])('takes no port from %s', (_name, init) => {
    const { message, port } = setUp();
    message(init);
    expect(port.onmessage).toBeNull();
  });

  it('takes the first port and no other', () => {
    const { message, connect, port } = setUp();
    connect();
    const second = new FakePort();
    message({ ports: [second] });
    expect(port.onmessage).not.toBeNull();
    expect(second.onmessage).toBeNull();
  });
});

describe('showing the field', () => {
  it('builds the editor from the init and says it is ready', async () => {
    const { show, editor, port, sizes } = setUp();
    await show();
    const shown = editor();
    expect(shown.isConnected).toBe(true);
    expect(shown.value).toBe('Eerste **tekst**');
    expect(shown.rows).toBe(5);
    expect(shown.maxLength).toBe(5000);
    expect(shown.accessibleLabel).toBe('Je feedback');
    expect(shown.translations).toEqual({ 'components.text-editor.task-open-label': 'Task, open' });
    expect(shown.caretToEnd).toHaveBeenCalledOnce();
    expect(document.documentElement.lang).toBe('en');
    expect(document.documentElement.dataset.scheme).toBe('dark');
    expect(port.sent).toEqual([{ type: 'ready', height: 0 }]);
    expect(sizes.map(([element]) => element)).toEqual([document.body]);
    sizes[0]![1]();
    expect(port.sent[1]).toEqual({ type: 'height', height: 0 });
  });

  it('describes an invalid field with its message, which it cannot point at across the frame', async () => {
    const { show, editor } = setUp();
    await show({ invalid: true, description: 'Write your feedback first.', lang: 'nl' });
    const shown = editor();
    expect(shown.invalid).toBe(true);
    expect(shown.translations).toEqual({});
    const [description] = shown.describedByElements;
    expect(description!.textContent).toBe('Write your feedback first.');
    expect((description as HTMLElement).hidden).toBe(true);
  });

  it('builds one editor, whatever the parent says twice', async () => {
    const { show, port, editors } = setUp();
    await show();
    port.say(INIT);
    expect(editors).toHaveLength(1);
  });

  it('says it failed when the editor cannot be built', async () => {
    const { connect, port } = setUp(() => {
      throw new Error('no editor');
    });
    connect();
    port.say(INIT);
    await vi.waitFor(() => expect(port.sent).toEqual([{ type: 'failed' }]));
  });

  it('does nothing asked of it before the init', () => {
    const { connect, port, win } = setUp();
    connect();
    for (const message of [
      { type: 'value', value: 'x' },
      { type: 'state', invalid: true, description: 'x' },
      { type: 'scheme', scheme: 'light' },
      { type: 'focus' },
      { type: 'select' },
    ] as ToFrame[]) {
      port.say(message);
    }
    win.dispatchEvent(new Event('focus'));
    expect(port.sent).toEqual([]);
  });
});

describe('writing', () => {
  it('tells the parent each new value once, from either input event', async () => {
    const { show, editor, port } = setUp();
    await show();
    const shown = editor();
    shown.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    shown.value = 'Eerste **tekst** en meer';
    shown.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    shown.dispatchEvent(new CustomEvent('input', { bubbles: true, composed: true }));
    expect(port.sent.slice(1)).toEqual([{ type: 'input', value: 'Eerste **tekst** en meer' }]);
  });

  it('takes a value from the parent without saying it back', async () => {
    const { show, editor, port } = setUp();
    await show();
    port.say({ type: 'value', value: '' });
    const shown = editor();
    expect(shown.value).toBe('');
    shown.dispatchEvent(new Event('input'));
    expect(port.sent).toHaveLength(1);
  });

  it('follows the invalid mark and the message, and drops an empty one', async () => {
    const { show, editor, port } = setUp();
    await show();
    port.say({ type: 'state', invalid: true, description: 'Write your feedback first.' });
    const shown = editor();
    expect(shown.invalid).toBe(true);
    expect(shown.describedByElements[0]!.textContent).toBe('Write your feedback first.');
    port.say({ type: 'state', invalid: false, description: '' });
    expect(shown.invalid).toBe(false);
    expect(shown.describedByElements).toEqual([]);
  });

  it('follows light and dark', async () => {
    const { show, port } = setUp();
    await show();
    port.say({ type: 'scheme', scheme: 'light' });
    expect(document.documentElement.dataset.scheme).toBe('light');
  });

  it('focuses and selects all when asked', async () => {
    const { show, editor, port } = setUp();
    await show();
    const shown = editor();
    const focus = vi.spyOn(shown, 'focus');
    port.say({ type: 'focus' });
    expect(focus).toHaveBeenCalledOnce();
    port.say({ type: 'select' });
    expect(shown.selectAll).toHaveBeenCalledOnce();
  });

  it('passes focus given to the frame on to the editor, and leaves focus inside it alone', async () => {
    const { show, editor, win } = setUp();
    await show();
    const shown = editor();
    const focus = vi.spyOn(shown, 'focus');
    win.dispatchEvent(new Event('focus'));
    expect(focus).toHaveBeenCalledOnce();
    const inside = document.createElement('button');
    document.body.append(inside);
    inside.focus();
    win.dispatchEvent(new Event('focus'));
    expect(focus).toHaveBeenCalledOnce();
  });
});

describe('telling the parent', () => {
  it.each<[string, KeyboardEventInit, boolean, boolean]>([
    ['Escape', { key: 'Escape' }, false, true],
    ['an Escape the editor also took', { key: 'Escape', cancelable: true }, false, true],
    ['an Escape that closes a list of the editor', { key: 'Escape' }, true, false],
    ['an Escape that ends a composition', { key: 'Escape', isComposing: true }, false, false],
    ['another key', { key: 'a' }, false, false],
  ])('passes on %s: %s', async (_name, init, popup, passed) => {
    const { show, port, win, editor } = setUp();
    await show();
    editor().popup = popup;
    const event = new KeyboardEvent('keydown', init);
    if (init.cancelable) event.preventDefault();
    win.dispatchEvent(event);
    expect(port.sent.slice(1)).toEqual(passed ? [{ type: 'escape' }] : []);
  });

  it('passes on no Escape before the editor shows', () => {
    const { connect, port, win } = setUp();
    connect();
    win.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(port.sent).toEqual([]);
  });

  it.each(['error', 'unhandledrejection'])('says it failed on an %s', async (type) => {
    const { show, port, win } = setUp();
    await show();
    win.dispatchEvent(new Event(type));
    expect(port.sent.slice(1)).toEqual([{ type: 'failed' }]);
  });

  it('says nothing before it has a port', () => {
    const { win, port } = setUp();
    win.dispatchEvent(new Event('error'));
    expect(port.sent).toEqual([]);
  });
});

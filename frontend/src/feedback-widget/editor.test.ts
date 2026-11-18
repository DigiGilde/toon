import { afterEach, describe, expect, it, vi } from 'vitest';

import { HELLO } from '../feedback-editor/protocol';
import { EDITOR_TIMEOUT, EditorField } from './editor';
import { translator } from './i18n';
import { STYLES } from './styles';
import { FakePort, fakePlatform } from './testPlatform';

const URL = '/-/feedback/editor.0123456789abcdef.html';

afterEach(() => {
  document.body.innerHTML = '';
  vi.useRealTimers();
});

function setUp(options: { url?: string | null; key?: string | null; lang?: 'nl' | 'en' } = {}) {
  const host = document.createElement('div');
  const root = host.attachShadow({ mode: 'open' });
  document.body.append(host);
  const label = document.createElement('label');
  label.textContent = 'Je feedback';
  const message = document.createElement('p');
  const field = document.createElement('textarea');
  field.rows = 5;
  field.maxLength = 5000;
  if (options.key !== null) field.setAttribute('data-key', options.key ?? 'comment');
  const other = document.createElement('button');
  root.append(label, message, field, other);
  const platform = fakePlatform();
  const port = new FakePort();
  platform.connectFrame.mockReturnValue(port as unknown as MessagePort);
  const lang = options.lang ?? 'nl';
  const editor = new EditorField({
    field,
    label,
    message,
    url: options.url === undefined ? URL : options.url,
    t: translator(lang),
    lang,
    platform,
  });
  const frame = () => root.querySelector('iframe');
  const hello = (source: Window | null = frame()!.contentWindow, data: unknown = { type: HELLO }) =>
    window.dispatchEvent(new MessageEvent('message', { source, data }));
  /** Open the field, let the frame say hello and get ready. */
  const ready = (height = 160) => {
    field.focus();
    hello();
    port.say({ type: 'ready', height });
  };
  return { root, label, message, field, other, platform, port, editor, frame, hello, ready };
}

/** Let a MutationObserver report. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('a field without an editor', () => {
  it('stays the textarea when Toon has no editor', () => {
    const { field, editor, frame, label, platform } = setUp({ url: null });
    field.focus();
    expect(frame()).toBeNull();
    expect(editor.showing).toBe(false);
    const click = new MouseEvent('click', { bubbles: true, cancelable: true });
    label.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(false);
    editor.sync();
    editor.dispose();
    expect(platform.connectFrame).not.toHaveBeenCalled();
  });

  it('focuses and selects the textarea itself', () => {
    const { root, field, editor } = setUp({ url: null });
    field.value = 'Mijn tekst';
    editor.select();
    expect(root.activeElement).toBe(field);
    expect([field.selectionStart, field.selectionEnd]).toEqual([0, 10]);
    field.blur();
    editor.focus();
    expect(root.activeElement).toBe(field);
  });
});

describe('loading the editor', () => {
  it('waits until someone starts writing', () => {
    const { frame } = setUp();
    expect(frame()).toBeNull();
  });

  it('puts a sandboxed frame with a title after the field on the first focus, once', () => {
    const { field, frame, root } = setUp({ lang: 'en' });
    field.focus();
    field.blur();
    field.focus();
    expect(root.querySelectorAll('iframe')).toHaveLength(1);
    const iframe = frame()!;
    expect(field.nextElementSibling).toBe(iframe);
    expect(iframe.getAttribute('src')).toBe(URL);
    expect(iframe.getAttribute('sandbox')).toBe('allow-scripts');
    expect(iframe.title).toBe('Text box with formatting');
    expect(iframe.className).toBe('editor');
    expect(field.hidden).toBe(false);
  });

  it('answers only a hello from its own frame', () => {
    const { field, hello, platform } = setUp();
    field.focus();
    hello(window);
    hello(undefined, { type: 'something else' });
    hello(undefined, null);
    expect(platform.connectFrame).not.toHaveBeenCalled();
  });

  it('hands the frame a port, once, and describes the field over it', () => {
    const { field, message, hello, platform, port, frame } = setUp({ lang: 'en' });
    platform.colorScheme.mockReturnValue('dark');
    field.value = 'Al **getypt**';
    field.setAttribute('aria-invalid', 'true');
    message.textContent = 'Schrijf eerst je feedback.';
    field.focus();
    hello();
    hello();
    expect(platform.connectFrame).toHaveBeenCalledOnce();
    expect(platform.connectFrame).toHaveBeenCalledWith(frame()!.contentWindow);
    expect(port.sent).toEqual([
      {
        type: 'init',
        value: 'Al **getypt**',
        label: 'Je feedback',
        lang: 'en',
        maxLength: 5000,
        rows: 5,
        scheme: 'dark',
        invalid: true,
        description: 'Schrijf eerst je feedback.',
      },
    ]);
  });

  it('keeps the textarea while the frame loads, and shows the editor when it is ready', () => {
    const { root, field, frame, ready, editor, port } = setUp();
    ready(172);
    const iframe = frame()!;
    expect(editor.showing).toBe(true);
    expect(field.hidden).toBe(true);
    expect(iframe.classList.contains('live')).toBe(true);
    expect(iframe.style.height).toBe('172px');
    // The focus plan finds the field on the frame now.
    expect(iframe.getAttribute('data-key')).toBe('comment');
    expect(field.hasAttribute('data-key')).toBe(false);
    // The field had focus: the editor takes it over.
    expect(root.activeElement).toBe(iframe);
    expect(port.last()).toEqual({ type: 'focus' });
  });

  it('leaves focus alone when it went elsewhere meanwhile', () => {
    const { root, field, hello, port, frame, other } = setUp();
    field.focus();
    hello();
    other.focus();
    port.say({ type: 'ready', height: 100 });
    expect(root.activeElement).not.toBe(frame());
    expect(port.sent.map((sent) => sent.type)).toEqual(['init']);
  });

  it('brings along what was typed in the textarea while the frame got ready', () => {
    const { field, hello, port } = setUp();
    field.focus();
    hello();
    field.value = 'Snel getypt';
    port.say({ type: 'ready', height: 100 });
    expect(port.sent).toContainEqual({ type: 'value', value: 'Snel getypt' });
  });

  it('shows the editor once, whatever the frame says twice', () => {
    const { port, ready } = setUp();
    ready();
    const count = port.sent.length;
    port.say({ type: 'ready', height: 100 });
    expect(port.sent).toHaveLength(count);
  });

  it('moves no key for a field without one', () => {
    const { frame, ready, port } = setUp({ key: null });
    ready();
    expect(frame()!.hasAttribute('data-key')).toBe(false);
    port.say({ type: 'failed' });
  });
});

describe('writing in the editor', () => {
  it('writes each change into the textarea as an input event', () => {
    const { field, port, ready } = setUp();
    const input = vi.fn();
    field.addEventListener('input', input);
    ready();
    port.say({ type: 'input', value: '- een\n- twee' });
    expect(field.value).toBe('- een\n- twee');
    expect(input).toHaveBeenCalledOnce();
  });

  it('follows the height of the editor', () => {
    const { frame, port, ready } = setUp();
    ready();
    port.say({ type: 'height', height: 240 });
    expect(frame()!.style.height).toBe('240px');
  });

  it('tells the editor when the field is marked or its message changes', async () => {
    const { field, message, port, ready } = setUp();
    ready();
    field.setAttribute('aria-invalid', 'true');
    message.textContent = 'Schrijf eerst je feedback.';
    await settle();
    expect(port.last()).toEqual({ type: 'state', invalid: true, description: 'Schrijf eerst je feedback.' });
    field.removeAttribute('aria-invalid');
    message.textContent = '';
    await settle();
    expect(port.last()).toEqual({ type: 'state', invalid: false, description: '' });
  });

  it('gives the panel an Escape the editor left alone', () => {
    const { root, frame, port, ready } = setUp();
    const keys: Array<[string, EventTarget | null]> = [];
    root.addEventListener('keydown', (event) => keys.push([(event as KeyboardEvent).key, event.target]));
    ready();
    port.say({ type: 'escape' });
    expect(keys).toEqual([['Escape', frame()]]);
  });

  it('sends a text the widget changed itself, and nothing when it did not change', () => {
    const { field, editor, port, ready } = setUp();
    ready();
    port.say({ type: 'input', value: 'Bedankt' });
    const count = port.sent.length;
    editor.sync();
    expect(port.sent).toHaveLength(count);
    field.value = '';
    editor.sync();
    expect(port.last()).toEqual({ type: 'value', value: '' });
  });

  it('sends nothing while the frame is not showing', () => {
    const { field, editor, hello, port } = setUp();
    field.focus();
    hello();
    field.value = 'nog niet';
    editor.sync();
    expect(port.sent.map((sent) => sent.type)).toEqual(['init']);
  });

  it('focuses and selects in the editor', () => {
    const { root, frame, editor, port, ready, other } = setUp();
    ready();
    other.focus();
    editor.focus();
    expect(root.activeElement).toBe(frame());
    expect(port.last()).toEqual({ type: 'focus' });
    editor.select();
    expect(port.last()).toEqual({ type: 'select' });
  });

  it('sends a press on the label to the editor, which the textarea behind it cannot take', () => {
    const { label, port, ready } = setUp();
    const before = new MouseEvent('click', { bubbles: true, cancelable: true });
    label.dispatchEvent(before);
    expect(before.defaultPrevented).toBe(false);
    ready();
    const click = new MouseEvent('click', { bubbles: true, cancelable: true });
    label.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
    expect(port.last()).toEqual({ type: 'focus' });
  });

  it('follows light and dark, until the editor goes', () => {
    const { platform, port, ready, editor } = setUp();
    const stop = vi.fn();
    platform.onColorSchemeChange.mockReturnValue(stop);
    ready();
    platform.onColorSchemeChange.mock.calls[0]![0]('dark');
    expect(port.last()).toEqual({ type: 'scheme', scheme: 'dark' });
    editor.dispose();
    expect(stop).toHaveBeenCalledOnce();
  });
});

describe('falling back to the textarea', () => {
  it('drops a frame that is not ready in time, and keeps the textarea', () => {
    vi.useFakeTimers();
    const { field, frame, hello, platform } = setUp();
    field.focus();
    field.value = 'Getypt terwijl het laadde';
    vi.advanceTimersByTime(EDITOR_TIMEOUT - 1);
    expect(frame()).not.toBeNull();
    const iframe = frame()!;
    vi.advanceTimersByTime(1);
    expect(frame()).toBeNull();
    expect(field.hidden).toBe(false);
    expect(field.value).toBe('Getypt terwijl het laadde');
    hello(iframe.contentWindow);
    expect(platform.connectFrame).not.toHaveBeenCalled();
  });

  it('is in time when the frame is ready before the time is up', () => {
    vi.useFakeTimers();
    const { field, frame, ready } = setUp();
    ready();
    vi.advanceTimersByTime(EDITOR_TIMEOUT);
    expect(frame()).not.toBeNull();
    expect(field.hidden).toBe(true);
  });

  it('brings the textarea back with the text so far when the frame fails, with focus where the editor had it', () => {
    const { root, field, frame, port, ready } = setUp();
    ready();
    port.say({ type: 'input', value: 'Tot hier' });
    port.say({ type: 'failed' });
    expect(frame()).toBeNull();
    expect(port.closed).toBe(true);
    expect(field.hidden).toBe(false);
    expect(field.value).toBe('Tot hier');
    expect(field.getAttribute('data-key')).toBe('comment');
    expect(root.activeElement).toBe(field);
  });

  it('leaves focus alone when the failing editor did not have it', () => {
    const { root, frame, port, ready, other } = setUp();
    ready();
    other.focus();
    port.say({ type: 'failed' });
    expect(frame()).toBeNull();
    expect(root.activeElement).toBe(other);
  });

  it('drops a frame that fails before it is ready, and changes nothing else', () => {
    const { root, field, frame, hello, port } = setUp();
    field.focus();
    hello();
    port.say({ type: 'failed' });
    expect(frame()).toBeNull();
    expect(field.hidden).toBe(false);
    expect(root.activeElement).toBe(field);
  });

  it('takes the frame away when the field goes', () => {
    vi.useFakeTimers();
    const { field, frame, editor } = setUp();
    field.focus();
    editor.dispose();
    expect(frame()).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('the frame in the panel', () => {
  it('has the colour scheme of its document in both modes, so the browser draws it see-through', () => {
    // A frame whose scheme differs from its document's gets an opaque
    // backdrop: a dark block around the field in dark mode.
    expect(STYLES).toContain('.editor { color-scheme: light; }');
    expect(STYLES).toMatch(/@media \(prefers-color-scheme: dark\) \{\s*\.editor \{ color-scheme: dark; \}/);
  });
});

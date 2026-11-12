import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { translator } from './i18n';
import { STYLES } from './styles';
import { fakeConfig, fakePlatform, json, problem } from './testPlatform';
import type { Box, Capture, Config, Lang } from './types';
import { HIDE_FOR_MS, samePage, Widget } from './widget';

const t = translator('nl');

beforeEach(() => {
  window.history.replaceState(null, '', '/team/site/docs/');
  document.title = 'Documentatie';
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  document.getSelection()!.removeAllRanges();
  document.body.replaceChildren();
  window.history.replaceState(null, '', '/');
});

function mount(config: Partial<Config> = {}, lang: Lang = 'nl') {
  const platform = fakePlatform();
  platform.fetch.mockImplementation(async () => json(201, { id: 'new', status: 'new', createdAt: 'x' }));
  const widget = new Widget({ config: fakeConfig(config), lang, platform });
  widget.mount();
  const root = widget.root;
  const find = <T extends HTMLElement = HTMLElement>(key: string) => root.querySelector<T>(`[data-key="${key}"]`);
  const press = (key: string) => find(key)!.click();
  const said = () => Array.from(root.querySelectorAll('[role="status"] p'), (line) => line.textContent!);
  const status = () => said()[said().length - 1];
  const alert = () => root.querySelector('.panel [role="alert"]')!.textContent;
  const focused = () => root.activeElement as HTMLElement | null;
  const escape = (target: Element = focused() ?? root.querySelector('.heading')!) =>
    target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, composed: true, cancelable: true }));
  const trigger = find<HTMLButtonElement>('trigger')!;
  const panel = root.querySelector<HTMLElement>('.panel')!;
  return { widget, platform, root, find, press, said, status, alert, focused, escape, trigger, panel };
}

function pageText(html: string): void {
  const container = document.createElement('main');
  container.id = 'content';
  // Fixture markup only; the widget itself never parses HTML.
  container.innerHTML = html;
  document.body.prepend(container);
}

function select(node: Node, start: number, end: number): void {
  const range = document.createRange();
  range.setStart(node, start);
  range.setEnd(node, end);
  const selection = document.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
}

function sentItem(platform: ReturnType<typeof fakePlatform>, call = 0) {
  const body = platform.fetch.mock.calls[call]![1].body as FormData;
  return { item: JSON.parse(body.get('item') as string), file: body.get('screenshot') };
}

// A page's own web components, as nldd-link, nldd-button or a site's footer
// of its own build them: the controls sit in a shadow root, so the document
// sees focus on them as focus on the host.
customElements.define(
  'page-links',
  class extends HTMLElement {
    constructor() {
      super();
      const root = this.attachShadow({ mode: 'open' });
      for (const text of ['Privacy', 'Contact', 'English']) {
        const link = document.createElement('a');
        link.href = `#${text}`;
        link.textContent = text;
        root.append(link);
      }
    }
  },
);
customElements.define(
  'page-footer',
  class extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' }).append(document.createElement('page-links'));
    }
  },
);
// A host that takes focus itself, with nothing to focus in its root.
customElements.define(
  'page-card',
  class extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' }).append('Een kaart');
    }
  },
);
// A pane that scrolls inside its own shadow root, as nldd-page does in an
// app shell, and a frame that passes its content on into such a pane.
customElements.define(
  'page-pane',
  class extends HTMLElement {
    constructor() {
      super();
      const scroller = document.createElement('div');
      scroller.className = 'scroll';
      scroller.append(document.createElement('slot'));
      this.attachShadow({ mode: 'open' }).append(scroller);
    }
  },
);
customElements.define(
  'page-frame',
  class extends HTMLElement {
    constructor() {
      super();
      const pane = document.createElement('page-pane');
      pane.append(document.createElement('slot'));
      this.attachShadow({ mode: 'open' }).append(pane);
    }
  },
);
class ClosedLink extends HTMLElement {
  readonly inner = document.createElement('a');
  constructor() {
    super();
    this.inner.href = '#gesloten';
    this.inner.textContent = 'Gesloten';
    this.attachShadow({ mode: 'closed' }).append(this.inner);
  }
}
customElements.define('closed-link', ClosedLink);

/** The links in the shadow root of a page-links element. */
function linksOf(host: Element): HTMLAnchorElement[] {
  return Array.from(host.shadowRoot!.querySelectorAll('a'));
}

/** The z-index the widget's stylesheet gives the elements of a class, or
 * null for none: they then lie under every element that has one. */
function zIndexOf(className: string): number | null {
  const rules = STYLES.replace(/\/\*[\s\S]*?\*\//g, '');
  for (const [, selectors, value] of rules.matchAll(/([^{}]+)\{\s*z-index:\s*(\d+);\s*\}/g)) {
    if (selectors!.split(',').some((selector) => selector.trim() === `.${className}`)) return Number(value);
  }
  return null;
}

describe('the host', () => {
  it('is one element at the end of body, with an open shadow root and a labelled landmark', () => {
    const { widget, root, trigger, panel } = mount({}, 'en');
    expect(document.body.lastElementChild).toBe(widget.host);
    expect(widget.host.hasAttribute('data-toon-feedback-host')).toBe(true);
    expect(widget.host.getAttribute('lang')).toBe('en');
    expect(widget.host.shadowRoot).toBe(root);
    expect(root.querySelector('style')!.textContent).toContain(':host');
    const region = root.querySelector('[role="region"]')!;
    expect(region.getAttribute('aria-label')).toBe('Feedback');
    expect(trigger.textContent).toBe('Feedback');
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(trigger.getAttribute('aria-controls')).toBe('tf-panel');
    expect(panel.hidden).toBe(true);
    expect(panel.getAttribute('role')).toBe('dialog');
    expect(panel.hasAttribute('aria-modal')).toBe(false);
    expect(panel.getAttribute('aria-labelledby')).toBe('tf-heading');
  });

  it('stops key and input events from the widget at the host, and lets the page have its own', () => {
    const { find } = mount();
    const seen = vi.fn();
    for (const type of ['keydown', 'keyup', 'keypress', 'input', 'beforeinput']) document.addEventListener(type, seen);
    for (const type of ['keydown', 'keyup', 'keypress', 'input', 'beforeinput']) {
      find('trigger')!.dispatchEvent(new Event(type, { bubbles: true, composed: true }));
    }
    expect(seen).not.toHaveBeenCalled();
    document.body.dispatchEvent(new Event('keydown', { bubbles: true }));
    expect(seen).toHaveBeenCalledOnce();
    for (const type of ['keydown', 'keyup', 'keypress', 'input', 'beforeinput']) document.removeEventListener(type, seen);
  });

  it('keeps the page selection when a widget button is pressed, and only then', () => {
    const { trigger, root } = mount();
    const onButton = new MouseEvent('mousedown', { bubbles: true, cancelable: true, composed: true });
    trigger.firstElementChild!.dispatchEvent(onButton);
    expect(onButton.defaultPrevented).toBe(true);
    const elsewhere = new MouseEvent('mousedown', { bubbles: true, cancelable: true, composed: true });
    root.querySelector('.heading')!.dispatchEvent(elsewhere);
    expect(elsewhere.defaultPrevented).toBe(false);
  });

  it('says each message as a line of its own, a repeated one too, and keeps the last three', () => {
    const { press, trigger, said, root } = mount();
    trigger.click();
    press('menu.text');
    press('menu.text');
    expect(said()).toEqual([t('menu.noSelection'), t('menu.noSelection')]);
    press('menu.text');
    press('menu.text');
    expect(said()).toHaveLength(3);
    expect(root.querySelector('[role="status"]')!.getAttribute('class')).toBe('vh');
  });
});

describe('opening and closing', () => {
  it('opens with focus on the heading and closes back to the button', () => {
    const { trigger, panel, focused, root, widget } = mount();
    trigger.click();
    expect(widget.state).toBe('menu');
    expect(panel.hidden).toBe(false);
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    expect(root.querySelector('.heading')!.textContent).toBe('Feedback op deze pagina');
    expect(focused()).toBe(root.querySelector('.heading'));
    trigger.click();
    expect(widget.state).toBe('idle');
    expect(panel.hidden).toBe(true);
    expect(focused()).toBe(trigger);
  });

  it('backs out one step with Escape and ignores other keys', () => {
    const { trigger, escape, widget, focused } = mount();
    trigger.click();
    const other = new KeyboardEvent('keydown', { key: 'a', bubbles: true, composed: true, cancelable: true });
    focused()!.dispatchEvent(other);
    expect(other.defaultPrevented).toBe(false);
    escape();
    expect(widget.state).toBe('idle');
    expect(focused()).toBe(trigger);
    escape(trigger);
    expect(widget.state).toBe('idle');
  });

  it('offers only feedback with an account, and hiding the button, on an account-only site to whoever has not said yes', () => {
    const { trigger, root, press, widget } = mount({ identity: 'account', account: false });
    trigger.click();
    expect(Array.from(root.querySelectorAll('.panel .btn'), (button) => button.textContent)).toEqual([
      'Feedback geven met je account',
      'Knop verbergen',
      'Sluiten',
    ]);
    expect(root.querySelector('#tf-login-note')!.textContent).toContain(t('menu.login.note'));
    press('menu.hide');
    expect(widget.state).toBe('hiding');
  });

  it('opens the privacy statement from that menu in this tab on a shielded page', () => {
    const { trigger, root, platform } = mount({ identity: 'account', account: false });
    platform.opaqueOrigin.mockReturnValue(true);
    trigger.click();
    expect(root.querySelector('#tf-login-note a')!.hasAttribute('target')).toBe(false);
  });

  it('closes from the menu button', () => {
    const { trigger, press, widget, focused } = mount();
    trigger.click();
    press('menu.close');
    expect(widget.state).toBe('idle');
    expect(focused()).toBe(trigger);
  });
});

describe('pointing at a spot', () => {
  it('places a point by pointer, takes the screenshot and sends it all as multipart', async () => {
    pageText('<p id="target">De knop staat hier.</p>');
    const { trigger, press, root, platform, widget, focused, status, said, find } = mount({ identity: 'name' });
    platform.elementAt.mockReturnValue(document.getElementById('target'));
    platform.box.mockReturnValue({ left: 50, top: 20, width: 200, height: 40 });
    trigger.click();
    press('menu.point');
    expect(widget.state).toBe('placing');
    expect(trigger.hidden).toBe(true);
    const overlay = root.querySelector<HTMLElement>('.overlay')!;
    expect(focused()).toBe(overlay);

    overlay.dispatchEvent(new MouseEvent('click', { clientX: 150, clientY: 30, bubbles: true }));
    expect(widget.state).toBe('composing');
    expect(said()).toEqual(['Punt gekozen.', 'Schermafbeelding wordt gemaakt.']);
    expect(focused()).toBe(find('comment'));
    expect(platform.capture).toHaveBeenCalledWith(widget.host);
    await vi.waitFor(() => expect(root.querySelector('img.thumb')).not.toBeNull());
    expect(platform.draw.mock.calls[0]![1].marks).toEqual([{ type: 'pin', x: 150, y: 30 }]);

    find<HTMLTextAreaElement>('comment')!.value = 'Deze knop is te klein.';
    root.querySelector<HTMLInputElement>('#tf-name')!.value = 'Jan';
    press('composer.send');
    expect(widget.state).toBe('sending');
    expect(status()).toBe('Je feedback wordt verstuurd.');
    await vi.waitFor(() => expect(widget.state).toBe('sent'));

    const { item, file } = sentItem(platform);
    expect(item).toEqual({
      kind: 'point',
      comment: 'Deze knop is te klein.',
      page: { path: '/team/site/docs/', title: 'Documentatie' },
      anchor: {
        selectors: [{ type: 'CssSelector', value: '#target' }],
        point: { x: 0.5, y: 0.25, pageX: 150, pageY: 30 },
      },
      viewport: { width: 1024, height: 768, dpr: 2 },
      author: { kind: 'name', name: 'Jan' },
      screenshot: { region: null },
    });
    expect((file as File).type).toBe('image/jpeg');
    expect(root.querySelector('.heading')!.textContent).toBe('Bedankt, je feedback is verstuurd.');
    expect(focused()).toBe(root.querySelector('.heading'));
    expect(status()).toBe('Bedankt, je feedback is verstuurd.');
    expect(root.querySelector('.panel')!.textContent).toContain('Reacties van het siteteam kun je niet zien.');
    expect(platform.revokeObjectUrl).toHaveBeenCalledWith('blob:screenshot');

    press('sent.more');
    expect(widget.state).toBe('menu');
    press('menu.page');
    expect(find<HTMLTextAreaElement>('comment')!.value).toBe('');
    expect(root.querySelector<HTMLInputElement>('#tf-name')!.value).toBe('Jan');
  });

  it('closes from the thanks', async () => {
    const { trigger, press, widget, find, focused } = mount();
    trigger.click();
    press('menu.page');
    find<HTMLTextAreaElement>('comment')!.value = 'Prima';
    widget.root.querySelector<HTMLInputElement>('#tf-name')!.value = 'Jan';
    press('composer.send');
    await vi.waitFor(() => expect(widget.state).toBe('sent'));
    press('sent.close');
    expect(widget.state).toBe('idle');
    expect(focused()).toBe(trigger);
  });

  it('places a point with the keyboard crosshair, on the body when nothing is under it', () => {
    const { trigger, press, root, platform } = mount();
    trigger.click();
    press('menu.point');
    const overlay = root.querySelector<HTMLElement>('.overlay')!;
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, cancelable: true }));
    expect(root.querySelector('[role="status"]')!.textContent).toBe(t('placing.nothing'));
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    expect(platform.elementAt).toHaveBeenLastCalledWith(502, 384, expect.anything());
    expect(platform.box).toHaveBeenCalledWith(document.body);
  });

  it('says what lies under the crosshair, as the page renders it', () => {
    pageText('<div id="card"><h2>Onderdeel A</h2><p>Een alinea</p></div>');
    const { trigger, press, root, platform, status } = mount();
    const card = document.getElementById('card');
    platform.elementAt.mockReturnValue(card);
    platform.visibleText.mockReturnValue('Onderdeel A\n\nEen alinea');
    trigger.click();
    press('menu.point');
    root.querySelector('.overlay')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect(platform.visibleText).toHaveBeenCalledWith(card);
    expect(status()).toBe('Onder het kruis: Onderdeel A Een alinea');
  });

  it('says the words under the crosshair in their own language where the page gives another', () => {
    pageText('<p lang="fy" id="fy">Jierferslach</p><p lang="nl-NL" id="nl">Jaarverslag</p>');
    const { trigger, press, root, platform } = mount();
    platform.visibleText.mockImplementation((element) => element.textContent!);
    const line = () => root.querySelector('[role="status"]')!.lastElementChild!;
    vi.useFakeTimers();
    trigger.click();
    press('menu.point');
    const overlay = root.querySelector('.overlay')!;
    platform.elementAt.mockReturnValue(document.getElementById('fy'));
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect(line().innerHTML).toBe('Onder het kruis: <span lang="fy">Jierferslach</span>');
    vi.runAllTimers();
    platform.elementAt.mockReturnValue(document.getElementById('nl'));
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect(line().innerHTML).toBe('Onder het kruis: Jaarverslag');
  });

  it('shows the keyboard line of an overlay opened from the keyboard, and not of one opened by a press', () => {
    const { trigger, press, root, find, escape } = mount();
    trigger.click();
    const pointButton = find('menu.point')!;
    pointButton.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, composed: true }));
    pointButton.click();
    const overlay = () => root.querySelector('.overlay')!;
    expect(overlay().classList.contains('keyboard')).toBe(true);
    escape(overlay());
    find('menu.point')!.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, composed: true }));
    press('menu.point');
    expect(overlay().classList.contains('keyboard')).toBe(false);
  });

  it('moves the bar off the crosshair as the overlay shows', () => {
    const { trigger, press, root, platform } = mount();
    // A bar that reaches down past the crosshair in the middle, at 384.
    platform.box.mockImplementation((element) =>
      element.classList.contains('bar') && !element.classList.contains('bottom')
        ? { left: 0, top: 8, width: 1024, height: 400 }
        : { left: 0, top: 0, width: 0, height: 0 },
    );
    trigger.click();
    press('menu.point');
    expect(root.querySelector('.bar')!.classList.contains('bottom')).toBe(true);
  });

  it('stops with Escape or the Stop button, back to the control that started it', () => {
    const { trigger, press, root, widget, focused, escape, find, status } = mount();
    trigger.click();
    press('menu.point');
    escape(root.querySelector('.overlay')!);
    expect(widget.state).toBe('menu');
    expect(status()).toBe('Gestopt met aanwijzen.');
    expect(focused()).toBe(find('menu.point'));
    press('menu.point');
    press('placing.stop');
    expect(widget.state).toBe('menu');
    expect(root.querySelector('.overlay')).toBeNull();
    expect(trigger.hidden).toBe(false);
  });
});

describe('feedback on text', () => {
  it('shows a quote in its own language where the page gives another than the widget\'s', () => {
    pageText('<p>Zie het <span lang="en-GB">Annual report</span> en <span lang="nl">het jaarverslag</span>.</p>');
    const { trigger, press, root, escape } = mount();
    select(document.querySelector('#content [lang="en-GB"]')!.firstChild!, 0, 6);
    trigger.click();
    press('menu.text');
    const quote = root.querySelector('.quote')!;
    expect([quote.textContent, quote.getAttribute('lang')]).toEqual(['Annual', 'en-GB']);
    escape();
    select(document.querySelector('#content [lang="nl"]')!.firstChild!, 4, 15);
    press('menu.text');
    expect(root.querySelector('.quote')!.hasAttribute('lang')).toBe(false);
  });

  it('takes the selection from the menu button, with the quote and the selection marked', async () => {
    pageText('<p>Het budget is 2,4 miljoen euro.</p>');
    const text = document.querySelector('#content p')!.firstChild!;
    const { trigger, press, root, platform, widget } = mount({ identity: 'anonymous' });
    select(text, 14, 25);
    trigger.click();
    press('menu.text');
    expect(widget.state).toBe('composing');
    expect(root.querySelector('.quote')!.textContent).toBe('2,4 miljoen');
    await vi.waitFor(() => expect(platform.draw).toHaveBeenCalled());
    expect(platform.draw.mock.calls[0]![1].marks).toEqual([{ type: 'rects', rects: [{ x: 10, y: 20, width: 120, height: 18 }] }]);

    root.querySelector<HTMLTextAreaElement>('#tf-comment')!.value = 'Klopt dit bedrag?';
    press('composer.send');
    await vi.waitFor(() => expect(widget.state).toBe('sent'));
    const { item } = sentItem(platform);
    expect(item.kind).toBe('text');
    expect(item.author).toEqual({ kind: 'anonymous' });
    expect(item.anchor.selectors[0]).toEqual({
      type: 'TextQuoteSelector',
      exact: '2,4 miljoen',
      prefix: 'Het budget is ',
      suffix: ' euro.',
    });
  });

  it('asks for a selection first when there is none, or none with text, on screen and aloud', () => {
    pageText('<p>   </p>');
    const { trigger, press, widget, status, root, find } = mount();
    trigger.click();
    const hint = root.querySelector('#tf-text-hint')!;
    expect(hint.textContent).toBe('');
    expect(find('menu.text')!.getAttribute('aria-describedby')).toBe('tf-text-hint');
    press('menu.text');
    expect(widget.state).toBe('menu');
    expect(status()).toBe('Selecteer eerst tekst op de pagina.');
    // Seen under the button too, and not a live region, so it is heard once.
    expect(hint.textContent).toBe('Selecteer eerst tekst op de pagina.');
    expect(hint.hasAttribute('role')).toBe(false);
    expect(root.querySelector('.panel')!.contains(hint)).toBe(true);
    select(document.querySelector('#content p')!.firstChild!, 0, 3);
    press('menu.text');
    expect(widget.state).toBe('menu');
    vi.spyOn(document, 'getSelection').mockReturnValue(null);
    press('menu.text');
    expect(widget.state).toBe('menu');
    // The menu opens anew without it.
    trigger.click();
    trigger.click();
    expect(root.querySelector('#tf-text-hint')!.textContent).toBe('');
  });

  it('shows a floating button next to a selection while the menu is open', () => {
    pageText('<p>Een zin om te kiezen.</p>');
    const text = document.querySelector('#content p')!.firstChild!;
    const { trigger, find, platform, widget, focused, escape } = mount();
    const floating = find('floating')!;
    select(text, 0, 3);
    document.dispatchEvent(new Event('selectionchange'));
    expect(floating.hidden).toBe(true);

    trigger.click();
    document.dispatchEvent(new Event('selectionchange'));
    expect(floating.hidden).toBe(false);
    expect([floating.style.left, floating.style.top]).toEqual(['10px', '46px']);

    platform.coarsePointer.mockReturnValue(true);
    platform.rangeBoxes.mockReturnValue([{ left: 2000, top: 760, width: 10, height: 10 }]);
    document.dispatchEvent(new Event('selectionchange'));
    expect([floating.style.left, floating.style.top]).toEqual(['784px', '712px']);

    floating.click();
    expect(widget.state).toBe('composing');
    expect(floating.hidden).toBe(true);
    escape();
    expect(focused()).toBe(widget.root.querySelector('.heading'));

    document.getSelection()!.removeAllRanges();
    document.dispatchEvent(new Event('selectionchange'));
    expect(floating.hidden).toBe(true);
    vi.spyOn(document, 'getSelection').mockReturnValue(null);
    document.dispatchEvent(new Event('selectionchange'));
    expect(floating.hidden).toBe(true);
  });

  it('shows no floating button for a selection without text', () => {
    pageText('<p>   </p>');
    const { trigger, find } = mount();
    trigger.click();
    select(document.querySelector('#content p')!.firstChild!, 0, 2);
    document.dispatchEvent(new Event('selectionchange'));
    expect(find('floating')!.hidden).toBe(true);
  });

  it('shows no floating button in the menu that offers only signing in', () => {
    pageText('<p>Een zin om te kiezen.</p>');
    const { trigger, find, widget } = mount({ identity: 'account', account: false });
    trigger.click();
    select(document.querySelector('#content p')!.firstChild!, 0, 3);
    document.dispatchEvent(new Event('selectionchange'));
    expect(widget.state).toBe('menu');
    expect(find('floating')!.hidden).toBe(true);
  });

  it('shows the floating button on a site for accounts only once the visitor has said yes', () => {
    pageText('<p>Een zin om te kiezen.</p>');
    const { trigger, find } = mount({ identity: 'account', account: true });
    trigger.click();
    select(document.querySelector('#content p')!.firstChild!, 0, 3);
    document.dispatchEvent(new Event('selectionchange'));
    expect(find('floating')!.hidden).toBe(false);
  });
});

describe('feedback on the page and the screenshot', () => {
  async function composeOnPage(config: Partial<Config> = {}) {
    const mounted = mount(config);
    mounted.trigger.click();
    mounted.press('menu.page');
    expect(mounted.platform.capture).not.toHaveBeenCalled();
    mounted.press('shot.make');
    await vi.waitFor(() => expect(mounted.find('shot.region')).not.toBeNull());
    mounted.find<HTMLTextAreaElement>('comment')!.value = 'Mooie pagina';
    mounted.root.querySelector<HTMLInputElement>('#tf-name')!.value = 'Jan';
    return mounted;
  }

  it('takes a screenshot of the whole page only when asked, without marks', async () => {
    const { platform } = await composeOnPage();
    expect(platform.draw.mock.calls[0]![1].marks).toEqual([]);
  });

  it('makes the screenshot fit the limit of the site, trying a lower quality first', async () => {
    const { trigger, press, find, root, platform, widget } = mount({ screenshotLimit: 1000 });
    const jpeg = (size: number) => new Blob([new Uint8Array(size)], { type: 'image/jpeg' });
    platform.encode.mockResolvedValueOnce(jpeg(1500)).mockResolvedValueOnce(jpeg(900));
    trigger.click();
    press('menu.page');
    press('shot.make');
    await vi.waitFor(() => expect(find('shot.region')).not.toBeNull());
    expect(platform.encode.mock.calls.map(([, quality]) => quality)).toEqual([0.8, 0.6]);
    find<HTMLTextAreaElement>('comment')!.value = 'Mooie pagina';
    root.querySelector<HTMLInputElement>('#tf-name')!.value = 'Jan';
    press('composer.send');
    await vi.waitFor(() => expect(widget.state).toBe('sent'));
    expect((sentItem(platform).file as File).size).toBe(900);
  });

  it('chooses another part with a frame, and sends that part along', async () => {
    const { press, root, platform, widget, focused, find, status } = await composeOnPage();
    press('shot.region');
    expect(widget.state).toBe('cropping');
    expect(root.querySelector('.panel')!.hasAttribute('hidden')).toBe(true);
    const overlay = root.querySelector<HTMLElement>('.overlay')!;
    expect(overlay.contains(platform.show.mock.results[0]!.value)).toBe(true);
    expect(focused()).toBe(overlay);
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    expect(widget.state).toBe('composing');
    expect(status()).toBe('Deel gekozen.');
    expect(focused()).toBe(find('shot.region'));
    await vi.waitFor(() =>
      expect(platform.draw).toHaveBeenLastCalledWith(
        expect.anything(),
        expect.objectContaining({ source: { x: 256, y: 192, width: 512, height: 384 } }),
      ),
    );
    press('composer.send');
    await vi.waitFor(() => expect(widget.state).toBe('sent'));
    expect(sentItem(platform).item.screenshot).toEqual({ region: { x: 256, y: 192, width: 512, height: 384 } });
  });

  it('covers something with a frame, and keeps focus on Iets afdekken through the redraw', async () => {
    const { press, root, platform, widget, status, focused, find } = await composeOnPage();
    press('shot.cover');
    expect(widget.state).toBe('covering');
    root.querySelector('.overlay')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(status()).toBe('Afgedekt.');
    expect(focused()).toBe(find('shot.cover'));
    await vi.waitFor(() => expect(find('shot.uncover')).not.toBeNull());
    expect(platform.draw).toHaveBeenLastCalledWith(
      expect.anything(),
      expect.objectContaining({ covers: [{ x: 256, y: 192, width: 512, height: 384 }] }),
    );
    expect(focused()).toBe(find('shot.cover'));
  });

  it('says the size of the frame while it moves', async () => {
    const { press, root, status } = await composeOnPage();
    press('shot.region');
    root.querySelector('.overlay')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
    expect(status()).toBe('Kader van 512 bij 384 pixels.');
  });

  it('fits the screenshot into the window below the bar of the frame, and again when the window changes', async () => {
    const { press, root, platform, find } = await composeOnPage();
    let stage = { left: 0, top: 100, width: 512, height: 384 };
    platform.box.mockImplementation((element) =>
      element.classList.contains('stage') ? stage : { left: 0, top: 0, width: 200, height: 100 },
    );
    find('shot.region')!.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, composed: true }));
    press('shot.region');
    const picture = platform.show.mock.results[0]!.value as HTMLElement;
    expect(root.querySelector('.stage')!.contains(picture)).toBe(true);
    expect(root.querySelector('.overlay')!.classList.contains('keyboard')).toBe(false);
    expect([picture.style.width, picture.style.height]).toEqual(['512px', '384px']);
    stage = { left: 0, top: 100, width: 256, height: 192 };
    platform.onViewportChange.mock.calls[0]![0]();
    expect([picture.style.width, picture.style.height]).toEqual(['256px', '192px']);
  });

  it('stops drawing a frame with Escape or the Annuleren button, changing nothing', async () => {
    const { press, root, widget, escape, status, platform } = await composeOnPage();
    const draws = platform.draw.mock.calls.length;
    press('shot.region');
    escape(root.querySelector('.overlay')!);
    expect(widget.state).toBe('composing');
    expect(status()).toBe('Gestopt zonder wijziging.');
    press('shot.cover');
    press('frame.stop');
    expect(widget.state).toBe('composing');
    expect(platform.draw.mock.calls.length).toBe(draws + 2);
  });

  it('sends without the file when the giver leaves the screenshot out', async () => {
    const { press, platform, widget } = await composeOnPage();
    press('shot.without');
    press('composer.send');
    await vi.waitFor(() => expect(widget.state).toBe('sent'));
    expect(sentItem(platform).file).toBeNull();
    expect(sentItem(platform).item.screenshot).toEqual({ region: null });
  });

  async function pointWhileCapturing() {
    const mounted = mount();
    let capture!: (value: Capture) => void;
    mounted.platform.capture.mockReturnValue(new Promise((resolve) => (capture = resolve)));
    mounted.trigger.click();
    mounted.press('menu.point');
    mounted.root.querySelector('.overlay')!.dispatchEvent(new MouseEvent('click', { clientX: 1, clientY: 1, bubbles: true }));
    mounted.find<HTMLTextAreaElement>('comment')!.value = 'Snel';
    mounted.root.querySelector<HTMLInputElement>('#tf-name')!.value = 'Jan';
    return { ...mounted, capture: (value: Capture) => capture(value) };
  }

  it('holds Versturen back while the screenshot is being made, and says so with the way to send without', async () => {
    const { press, platform, widget, alert, find, said } = await pointWhileCapturing();
    press('composer.send');
    expect(widget.state).toBe('composing');
    expect(platform.fetch).not.toHaveBeenCalled();
    expect(alert()).toBe(t('error.shot.notReady'));
    expect(said()).not.toContain(t('composer.sending'));
    press('error.withoutShot');
    await vi.waitFor(() => expect(widget.state).toBe('sent'));
    const { file, item } = sentItem(platform);
    expect(file).toBeNull();
    expect(item.screenshot).toEqual({ region: null });
    expect(find('error.withoutShot')).toBeNull();
  });

  it('lets the giver leave out a screenshot that is still being made', async () => {
    const { press, platform, widget, capture } = await pointWhileCapturing();
    press('shot.without');
    press('composer.send');
    await vi.waitFor(() => expect(widget.state).toBe('sent'));
    expect(sentItem(platform).file).toBeNull();
    // A capture that ends now is dropped: nothing is drawn or shown.
    capture({ image: 'late', width: 1024, height: 768, scale: 1, placeholders: false });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(platform.encode).not.toHaveBeenCalled();
  });

  it('sends no screenshot that arrives while the way to send without it has focus', async () => {
    const { press, platform, alert, capture, find, root, focused } = await pointWhileCapturing();
    press('composer.send');
    expect(alert()).toBe(t('error.shot.notReady'));
    find('error.withoutShot')!.focus();
    capture({ image: 'capture', width: 1024, height: 768, scale: 1, placeholders: false });
    await vi.waitFor(() => expect(find('shot.region')).not.toBeNull());
    // On the screenshot's heading, the next Enter or press does nothing.
    expect(focused()).toBe(root.querySelector('.block h3'));
    focused()!.click();
    focused()!.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, composed: true, cancelable: true }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(platform.fetch).not.toHaveBeenCalled();
  });

  it('lifts the hold once the screenshot is there, and then sends it', async () => {
    const { press, platform, widget, alert, capture, find } = await pointWhileCapturing();
    press('composer.send');
    expect(alert()).toBe(t('error.shot.notReady'));
    capture({ image: 'capture', width: 1024, height: 768, scale: 1, placeholders: false });
    await vi.waitFor(() => expect(find('shot.region')).not.toBeNull());
    expect(alert()).toBe('');
    expect(find('error.withoutShot')).toBeNull();
    press('composer.send');
    await vi.waitFor(() => expect(widget.state).toBe('sent'));
    expect(sentItem(platform).file).not.toBeNull();
  });

  it('waits for a cover being drawn when Versturen is pressed, and sends the covered picture', async () => {
    const { press, root, platform, widget, find } = await composeOnPage();
    let finish!: (blob: Blob) => void;
    platform.encode.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    press('shot.cover');
    root.querySelector('.overlay')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    expect(root.querySelector('.block')!.textContent).toContain('De schermafbeelding wordt bijgewerkt.');
    expect(find('shot.uncover')).not.toBeNull();
    press('composer.send');
    expect(widget.state).toBe('sending');
    // While it waits, the screenshot stays as it was asked for.
    press('shot.uncover');
    press('shot.without');
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(platform.fetch).not.toHaveBeenCalled();
    finish(new Blob(['covered'], { type: 'image/jpeg' }));
    await vi.waitFor(() => expect(widget.state).toBe('sent'));
    const { file, item } = sentItem(platform);
    expect(await (file as File).text()).toBe('covered');
    expect(item.screenshot).toEqual({ region: null });
  });

  it('waits for another part being drawn, and sends that part with its own region', async () => {
    const { press, root, platform, widget } = await composeOnPage();
    let finish!: (blob: Blob) => void;
    platform.encode.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    press('shot.region');
    root.querySelector('.overlay')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    press('composer.send');
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(platform.fetch).not.toHaveBeenCalled();
    finish(new Blob(['cropped'], { type: 'image/jpeg' }));
    await vi.waitFor(() => expect(widget.state).toBe('sent'));
    const { file, item } = sentItem(platform);
    expect(await (file as File).text()).toBe('cropped');
    expect(item.screenshot).toEqual({ region: { x: 256, y: 192, width: 512, height: 384 } });
  });

  it('sends nothing when the picture it waited for cannot be drawn, and says so', async () => {
    const { press, root, platform, widget, alert, focused, find } = await composeOnPage();
    let fail!: (error: Error) => void;
    platform.encode.mockReturnValueOnce(new Promise((_resolve, reject) => (fail = reject)));
    press('shot.cover');
    root.querySelector('.overlay')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    find('composer.send')!.focus();
    press('composer.send');
    fail(new Error('out of memory'));
    await vi.waitFor(() => expect(widget.state).toBe('composing'));
    expect(platform.fetch).not.toHaveBeenCalled();
    expect(alert()).toBe(t('error.shot.changed'));
    expect(root.querySelector('.block')!.textContent).toContain(t('shot.failed'));
    expect(focused()).toBe(find('composer.send'));
    expect(root.querySelector('.panel')!.textContent).not.toContain(t('composer.sending'));
    press('error.withoutShot');
    await vi.waitFor(() => expect(widget.state).toBe('sent'));
    expect(sentItem(platform).file).toBeNull();
  });
});

describe('sending', () => {
  async function composing(config: Partial<Config> = {}) {
    const mounted = mount(config);
    mounted.trigger.click();
    mounted.press('menu.point');
    mounted.root.querySelector('.overlay')!.dispatchEvent(new MouseEvent('click', { clientX: 5, clientY: 5, bubbles: true }));
    await vi.waitFor(() => expect(mounted.find('shot.region')).not.toBeNull());
    mounted.find<HTMLTextAreaElement>('comment')!.value = 'Mijn tekst';
    mounted.root.querySelector<HTMLInputElement>('#tf-name')?.setAttribute('value', '');
    const name = mounted.root.querySelector<HTMLInputElement>('#tf-name');
    if (name) name.value = 'Jan';
    return mounted;
  }

  it('does not send what does not pass the checks', async () => {
    const { press, platform, widget, find } = await composing();
    find<HTMLTextAreaElement>('comment')!.value = '   ';
    press('composer.send');
    expect(widget.state).toBe('composing');
    expect(platform.fetch).not.toHaveBeenCalled();
  });

  it('cannot be interrupted while sending, and ignores a second press', async () => {
    const { press, platform, widget, trigger, escape, find } = await composing();
    let answer!: (response: Response) => void;
    platform.fetch.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    press('composer.send');
    press('composer.send');
    trigger.click();
    escape(find('composer.send')!);
    expect(widget.state).toBe('sending');
    await vi.waitFor(() => expect(platform.fetch).toHaveBeenCalled());
    press('composer.send');
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(platform.fetch).toHaveBeenCalledOnce();
    answer(json(201, { id: 'x' }));
    await vi.waitFor(() => expect(widget.state).toBe('sent'));
  });

  it('keeps the text and offers to copy it when the page can no longer send', async () => {
    const { press, platform, widget, alert, find, status, focused } = await composing();
    platform.fetch.mockResolvedValue(problem(404, 'FEEDBACK_UNAVAILABLE'));
    press('composer.send');
    await vi.waitFor(() => expect(widget.state).toBe('composing'));
    expect(alert()).toBe(t('error.unavailable'));
    expect(find<HTMLTextAreaElement>('comment')!.value).toBe('Mijn tekst');
    expect(find('error.withoutShot')).toBeNull();

    press('error.copy');
    await vi.waitFor(() => expect(status()).toBe('Je tekst is gekopieerd.'));
    expect(platform.copyText).toHaveBeenCalledWith(find('comment'));
    // Shown beside the button as well, where a sighted giver looks.
    const shown = () => find('error.copy')!.parentElement!.lastElementChild!.textContent;
    await vi.waitFor(() => expect(shown()).toBe('Je tekst is gekopieerd.'));

    platform.copyText.mockResolvedValue(false);
    press('error.copy');
    await vi.waitFor(() => expect(status()).toBe(t('error.copyFailed')));
    const field = find<HTMLTextAreaElement>('comment')!;
    expect(focused()).toBe(field);
    expect([field.selectionStart, field.selectionEnd]).toEqual([0, 'Mijn tekst'.length]);
    await vi.waitFor(() => expect(shown()).toBe(t('error.copyFailed')));
  });

  it('says to copy the text and reload when the site moved since the page was served', async () => {
    const { press, platform, widget, alert, find, status } = await composing();
    platform.fetch.mockResolvedValue(problem(422, 'FEEDBACK_PAGE_MOVED', 'De site of groep heeft een nieuw adres gekregen.'));
    press('composer.send');
    await vi.waitFor(() => expect(widget.state).toBe('composing'));
    expect(alert()).toBe(t('error.moved'));
    expect(find<HTMLTextAreaElement>('comment')!.value).toBe('Mijn tekst');
    press('error.copy');
    await vi.waitFor(() => expect(status()).toBe('Je tekst is gekopieerd.'));
    expect(platform.copyText).toHaveBeenCalledWith(find('comment'));
  });

  it('keeps focus on Versturen when the remedy that had it goes and sending fails again', async () => {
    const { press, platform, widget, alert, find, focused, escape } = await composing();
    platform.fetch
      .mockResolvedValueOnce(problem(413, 'FEEDBACK_SCREENSHOT_TOO_LARGE'))
      .mockResolvedValueOnce(problem(429, 'FEEDBACK_RATE_LIMITED', null, { 'Retry-After': '30' }));
    press('composer.send');
    await vi.waitFor(() => expect(alert()).toBe(t('error.shot.large')));
    const remedy = find('error.withoutShot')!;
    remedy.focus();
    remedy.click();
    expect(remedy.isConnected).toBe(false);
    expect(focused()).toBe(find('composer.send'));
    await vi.waitFor(() =>
      expect(alert()).toBe('Er komt nu veel feedback binnen. Probeer het over 30 seconden opnieuw.'),
    );
    expect(widget.state).toBe('composing');
    expect(focused()).toBe(find('composer.send'));
    // Escape works again, since focus is in the widget.
    escape();
    expect(widget.state).toBe('menu');
  });

  it('leaves focus where it was when the remedy is pressed with the mouse', async () => {
    const { press, platform, alert, find, focused } = await composing();
    platform.fetch
      .mockResolvedValueOnce(problem(413, 'FEEDBACK_SCREENSHOT_TOO_LARGE'))
      .mockResolvedValueOnce(problem(408, 'FEEDBACK_TIMEOUT'));
    press('composer.send');
    await vi.waitFor(() => expect(alert()).toBe(t('error.shot.large')));
    find('comment')!.focus();
    press('error.withoutShot');
    await vi.waitFor(() => expect(alert()).toBe(t('error.timeout')));
    expect(focused()).toBe(find('comment'));
    // Sent without a screenshot this time, so there is none to leave out now.
    expect(find('error.withoutShot')).toBeNull();
  });

  it('says when Toon is too busy, and offers to leave the screenshot out when sending took too long', async () => {
    const { press, platform, alert, find } = await composing();
    platform.fetch
      .mockResolvedValueOnce(problem(503, 'FEEDBACK_BUSY', null, { 'Retry-After': '60' }))
      .mockResolvedValueOnce(problem(408, 'FEEDBACK_TIMEOUT'));
    press('composer.send');
    await vi.waitFor(() => expect(alert()).toBe('Toon heeft het nu te druk. Probeer het over 60 seconden opnieuw.'));
    expect(find('error.withoutShot')).toBeNull();
    press('composer.send');
    await vi.waitFor(() => expect(alert()).toBe(t('error.timeout')));
    expect(find('error.withoutShot')).not.toBeNull();
  });

  it('sends again without the screenshot when the screenshot was the problem', async () => {
    const { press, platform, widget, alert } = await composing();
    platform.fetch
      .mockResolvedValueOnce(problem(413, 'FEEDBACK_SCREENSHOT_TOO_LARGE'))
      .mockResolvedValueOnce(json(201, { id: 'x' }));
    press('composer.send');
    await vi.waitFor(() => expect(alert()).toBe(t('error.shot.large')));
    press('error.withoutShot');
    await vi.waitFor(() => expect(widget.state).toBe('sent'));
    expect(sentItem(platform, 0).file).not.toBeNull();
    expect(sentItem(platform, 1).file).toBeNull();
  });

  it('keeps the draft when the giver cancels, and offers it again', async () => {
    const { press, widget, find, focused } = await composing();
    press('composer.cancel');
    expect(widget.state).toBe('menu');
    expect(focused()).toBe(find('menu.point'));
    press('menu.page');
    expect(find<HTMLTextAreaElement>('comment')!.value).toBe('Mijn tekst');
  });

  it('closes from the button while composing, keeping the draft', async () => {
    const { trigger, widget, find, press } = await composing();
    trigger.click();
    expect(widget.state).toBe('idle');
    trigger.click();
    press('menu.page');
    expect(find<HTMLTextAreaElement>('comment')!.value).toBe('Mijn tekst');
  });
});

describe('Mijn feedback', () => {
  const ON_PAGE = {
    id: 'here',
    kind: 'point',
    status: 'new',
    comment: 'Deze knop is te klein voor een duim',
    quote: null,
    anchor: { k: 'point', s: '#target', x: 0.5, y: 0.5, px: 10, py: 10 },
    page: { path: 'docs/', title: 'Docs', url: '/team/site/docs/' },
    createdAt: '2026-11-03T13:30:00Z',
    replies: [],
  };
  const LOST = { ...ON_PAGE, id: 'lost', kind: 'text', anchor: { k: 'text', q: 'verdwenen zin' } };
  const ELSEWHERE = { ...ON_PAGE, id: 'elsewhere', page: { path: '', title: null, url: '/team/site/' } };
  const WHOLE = { ...ON_PAGE, id: 'whole', kind: 'page', anchor: null };

  async function openMine(fetchItems: unknown[] = [ON_PAGE, LOST, ELSEWHERE, WHOLE], more?: boolean) {
    pageText('<p id="target">Knop</p>');
    const mounted = mount({ identity: 'account', account: true });
    mounted.platform.fetch.mockImplementation(async (_url, init) =>
      init.method === 'GET' ? json(200, { items: fetchItems, more }) : new Response(null, { status: 204 }),
    );
    mounted.platform.box.mockImplementation((element) =>
      element === mounted.widget.host
        ? { left: -8, top: -100, width: 0, height: 0 }
        : { left: 100, top: 200, width: 40, height: 20 },
    );
    mounted.platform.viewport.mockReturnValue({ ...mounted.platform.viewport(), scrollY: 300 });
    mounted.trigger.click();
    mounted.press('menu.mine');
    return mounted;
  }

  it('lists own items with pins for the ones on this page, and says which places are gone', async () => {
    const { root, widget, focused } = await openMine();
    expect(widget.state).toBe('mine');
    expect(root.querySelector('.heading')!.textContent).toBe('Mijn feedback');
    expect(focused()).toBe(root.querySelector('.heading'));
    await vi.waitFor(() => expect(root.querySelectorAll('li.item').length).toBe(4));
    const pins = root.querySelectorAll<HTMLElement>('.pins .pin');
    expect(pins.length).toBe(1);
    const pin = pins[0]!;
    // A ring like the place from a link, which a press goes through to the
    // control it lies on. As a button it still leads to the item from the keyboard.
    expect([pin.localName, pin.className]).toEqual(['button', 'pin']);
    expect(pin.getAttribute('aria-label')).toBe('Feedback: Deze knop is te klein voor een duim');
    expect([pin.style.left, pin.style.top]).toEqual(['128px', '310px']);
    expect(root.querySelector('[data-item="lost"]')!.textContent).toContain(t('mine.lost'));
    pin.click();
    expect(focused()).toBe(root.querySelector('[data-item="here"]'));
    expect(root.querySelector('.panel')!.textContent).not.toContain(t('mine.olderItems'));
  });

  it('says older feedback is not in the list when the answer left some out', async () => {
    const { root } = await openMine([ON_PAGE], true);
    await vi.waitFor(() => expect(root.querySelectorAll('li.item').length).toBe(1));
    expect(root.querySelector('.panel')!.textContent).toContain('Oudere feedback staat niet in deze lijst.');
  });

  it('moves its pins along with the layout, drops a pin whose place is gone, and says so in the list', async () => {
    const GONE = { ...ON_PAGE, id: 'gone', anchor: { k: 'point', s: '#target', x: 0.5, y: 0.5 } };
    const BACK = { ...LOST, id: 'back' };
    const { root, platform } = await openMine([ON_PAGE, GONE, BACK]);
    await vi.waitFor(() => expect(root.querySelectorAll('.pins .pin').length).toBe(2));
    expect(platform.observeLayout).toHaveBeenCalledOnce();
    const relayout = platform.observeLayout.mock.calls[0]![0];
    const lostOf = (id: string) => root.querySelector(`[data-item="${id}"]`)!.textContent!.includes(t('mine.lost'));
    expect([lostOf('gone'), lostOf('back')]).toEqual([false, true]);

    const kept = root.querySelector<HTMLElement>('[data-pin="here"]')!;
    expect([kept.style.left, kept.style.top]).toEqual(['128px', '310px']);
    document.getElementById('target')!.remove();
    pageText('<p>Een verdwenen zin staat er weer.</p>');
    relayout();
    // The pin with fallback coordinates stays and moves there, the other goes,
    // the quote that came back gets a pin.
    expect(root.querySelector('[data-pin="here"]')).toBe(kept);
    expect([kept.style.left, kept.style.top]).toEqual(['18px', '-190px']);
    expect(root.querySelector('[data-pin="gone"]')).toBeNull();
    expect(root.querySelector('[data-pin="back"]')).not.toBeNull();
    expect([lostOf('gone'), lostOf('back')]).toEqual([true, false]);
  });

  it('hands focus from a pin whose place is gone to its item in the list', async () => {
    const GONE = { ...ON_PAGE, id: 'gone', anchor: { k: 'point', s: '#target', x: 0.5, y: 0.5 } };
    const { root, platform, focused } = await openMine([GONE]);
    await vi.waitFor(() => expect(root.querySelector('[data-pin="gone"]')).not.toBeNull());
    root.querySelector<HTMLElement>('[data-pin="gone"]')!.focus();
    document.getElementById('target')!.remove();
    platform.observeLayout.mock.calls[0]![0]();
    expect(root.querySelector('[data-pin="gone"]')).toBeNull();
    expect(focused()).toBe(root.querySelector('[data-item="gone"]'));
  });

  it('stops following the layout when it closes, and follows it once for a place from a link meanwhile', async () => {
    const { root, platform, widget, escape } = await openMine();
    await vi.waitFor(() => expect(root.querySelector('.pins .pin')).not.toBeNull());
    widget.highlightWhenLoaded({ k: 'point', s: null, x: null, y: null, px: 40, py: 50, q: null });
    expect(root.querySelector('.pin-highlight')).not.toBeNull();
    expect(platform.observeLayout).toHaveBeenCalledOnce();
    const stop = platform.observeLayout.mock.results[0]!.value as ReturnType<typeof vi.fn>;
    escape();
    expect(widget.state).toBe('menu');
    expect(root.querySelector('.pins')!.childElementCount).toBe(0);
    expect(stop).toHaveBeenCalledOnce();
  });

  it('changes nothing when a delete ends after the giver left Mijn feedback', async () => {
    const { root, press, platform, escape, widget, focused, find } = await openMine();
    await vi.waitFor(() => expect(find('delete-here')).not.toBeNull());
    let answer!: (response: Response) => void;
    platform.fetch.mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
    press('delete-here');
    press('confirm-here');
    escape();
    escape();
    expect(widget.state).toBe('menu');
    expect(focused()).toBe(find('menu.mine'));
    answer(new Response(null, { status: 204 }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(focused()).toBe(find('menu.mine'));
    expect(root.querySelector('.pins .pin')).toBeNull();
  });

  it('removes the pin of an item the giver deletes, and puts focus on the heading', async () => {
    const { root, press, focused } = await openMine();
    await vi.waitFor(() => expect(root.querySelector('.pins .pin')).not.toBeNull());
    press('delete-here');
    press('confirm-here');
    await vi.waitFor(() => expect(root.querySelector('.pins .pin')).toBeNull());
    expect(focused()).toBe(root.querySelector('.heading'));
    press('delete-whole');
    press('confirm-whole');
    await vi.waitFor(() => expect(root.querySelector('[data-item="whole"]')).toBeNull());
  });

  it('offers to copy a reply that can no longer be sent, and goes back with its button', async () => {
    const { root, press, platform, widget, focused, find, status } = await openMine();
    await vi.waitFor(() => expect(root.querySelector('[data-item="here"] textarea')).not.toBeNull());
    platform.fetch.mockResolvedValueOnce(problem(404, 'FEEDBACK_UNAVAILABLE'));
    const field = root.querySelector<HTMLTextAreaElement>('[data-item="here"] textarea')!;
    field.value = 'Mijn reactie';
    press('reply-here');
    await vi.waitFor(() => expect(find('copy-here')).not.toBeNull());
    press('copy-here');
    await vi.waitFor(() => expect(status()).toBe('Je tekst is gekopieerd.'));
    expect(platform.copyText).toHaveBeenCalledWith(field);
    press('mine.back');
    expect(widget.state).toBe('menu');
    expect(focused()).toBe(find('menu.mine'));
    // The reply that did not go is there again when the giver comes back.
    press('menu.mine');
    await vi.waitFor(() =>
      expect(root.querySelector<HTMLTextAreaElement>('[data-item="here"] textarea')?.value).toBe('Mijn reactie'),
    );
  });

  it('says a reply refused after the giver left Mijn feedback, and where its text waits', async () => {
    const { root, press, platform, escape, widget, status } = await openMine();
    await vi.waitFor(() => expect(root.querySelector('[data-item="here"] textarea')).not.toBeNull());
    let answer!: (response: Response) => void;
    platform.fetch.mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
    root.querySelector<HTMLTextAreaElement>('[data-item="here"] textarea')!.value = 'Mijn reactie';
    press('reply-here');
    escape();
    expect(widget.state).toBe('menu');
    answer(problem(409, 'FEEDBACK_THREAD_FULL'));
    await vi.waitFor(() =>
      expect(status()).toBe(
        'Je reactie is niet geplaatst. Bij deze feedback kun je geen reacties meer plaatsen. ' +
          'Open Mijn feedback opnieuw, dan staat je tekst er weer.',
      ),
    );
    press('menu.mine');
    await vi.waitFor(() =>
      expect(root.querySelector<HTMLTextAreaElement>('[data-item="here"] textarea')?.value).toBe('Mijn reactie'),
    );
  });

  describe('a reply typed and not sent', () => {
    const typed = 'Dank je, maar de kop is nog steeds te groot op mijn telefoon.';
    const field = (root: ShadowRoot, id = 'here') => root.querySelector<HTMLTextAreaElement>(`[data-item="${id}"] textarea`);
    const type = (target: HTMLTextAreaElement, value: string) => {
      target.value = value;
      target.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    };

    it('stays through Escape, Terug and closing, and is in its field when Mijn feedback opens again', async () => {
      const { root, press, escape, trigger, widget } = await openMine();
      await vi.waitFor(() => expect(field(root)).not.toBeNull());
      type(field(root)!, typed);
      escape(field(root)!);
      expect(widget.state).toBe('menu');
      press('menu.mine');
      await vi.waitFor(() => expect(field(root)?.value).toBe(typed));
      press('mine.back');
      press('menu.mine');
      await vi.waitFor(() => expect(field(root)?.value).toBe(typed));
      trigger.click();
      expect(widget.state).toBe('idle');
      trigger.click();
      press('menu.mine');
      await vi.waitFor(() => expect(field(root)?.value).toBe(typed));
      expect(field(root, 'whole')!.value).toBe('');
    });

    it('stays when the list cannot be loaded meanwhile', async () => {
      const { root, press, escape, platform, alert } = await openMine();
      await vi.waitFor(() => expect(field(root)).not.toBeNull());
      type(field(root)!, typed);
      escape(field(root)!);
      platform.fetch.mockResolvedValueOnce(problem(404, 'FEEDBACK_UNAVAILABLE'));
      press('menu.mine');
      await vi.waitFor(() => expect(alert()).toBe(t('error.unavailable.other')));
      escape();
      press('menu.mine');
      await vi.waitFor(() => expect(field(root)?.value).toBe(typed));
    });

    it('typed after leaving and opening the list again, stays whatever the answer to a reply sent before', async () => {
      for (const late of [json(201, { id: 'r', author: 'you', name: null, body: 'A', createdAt: 'x' }), problem(429, 'FEEDBACK_RATE_LIMITED')]) {
        const { root, press, escape, platform, status } = await openMine();
        await vi.waitFor(() => expect(field(root)).not.toBeNull());
        let answer!: (response: Response) => void;
        platform.fetch.mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
        type(field(root)!, 'Antwoord A');
        press('reply-here');
        press('mine.back');
        press('menu.mine');
        await vi.waitFor(() => expect(field(root)?.value).toBe(''));
        type(field(root)!, 'Antwoord B');
        answer(late);
        await vi.waitFor(() => expect(status()).toMatch(/^Je reactie is (verstuurd|niet geplaatst)/));
        expect(field(root)!.value).toBe('Antwoord B');
        escape(field(root)!);
        press('menu.mine');
        await vi.waitFor(() => expect(field(root)?.value).toBe('Antwoord B'));
        document.body.replaceChildren();
      }
    });

    it('typed while a reply is on its way, stays once that reply is sent', async () => {
      const { root, press, escape, platform } = await openMine();
      await vi.waitFor(() => expect(field(root)).not.toBeNull());
      let answer!: (response: Response) => void;
      platform.fetch.mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
      type(field(root)!, 'Dank je.');
      press('reply-here');
      type(field(root)!, 'Dank je. Welke versie van de planning bedoel je?');
      answer(json(201, { id: 'r', author: 'you', name: null, body: 'Dank je.', createdAt: 'x' }));
      await vi.waitFor(() => expect(field(root)!.value).toBe('Welke versie van de planning bedoel je?'));
      escape(field(root)!);
      press('menu.mine');
      await vi.waitFor(() => expect(field(root)?.value).toBe('Welke versie van de planning bedoel je?'));
    });

    it('is gone once the reply is sent, and once its item is deleted', async () => {
      const { root, press, escape, platform } = await openMine();
      const answer = { id: 'r', author: 'you', name: null, body: 'Verstuurd', createdAt: '2026-11-11T08:00:00Z' };
      platform.fetch.mockImplementation(async (_url, init) =>
        init.method === 'GET'
          ? json(200, { items: [ON_PAGE, WHOLE] })
          : init.method === 'POST'
            ? json(201, answer)
            : new Response(null, { status: 204 }),
      );
      await vi.waitFor(() => expect(field(root)).not.toBeNull());
      type(field(root)!, 'Verstuurd');
      press('reply-here');
      await vi.waitFor(() => expect(field(root)!.value).toBe(''));
      type(field(root, 'whole')!, 'Weg ermee');
      press('delete-whole');
      press('confirm-whole');
      await vi.waitFor(() => expect(field(root, 'whole')).toBeNull());
      escape();
      press('menu.mine');
      await vi.waitFor(() => expect(field(root, 'whole')).not.toBeNull());
      expect([field(root)!.value, field(root, 'whole')!.value]).toEqual(['', '']);
    });
  });

  it('replies through the API', async () => {
    const { root, press, platform } = await openMine();
    platform.fetch.mockResolvedValueOnce(json(201, { id: 'r', author: 'you', name: null, body: 'Dank', createdAt: 'x' }));
    await vi.waitFor(() => expect(root.querySelector('[data-item="here"] textarea')).not.toBeNull());
    root.querySelector<HTMLTextAreaElement>('[data-item="here"] textarea')!.value = 'Dank';
    press('reply-here');
    await vi.waitFor(() => expect(root.querySelector('[data-item="here"] .replies')!.textContent).toContain('Dank'));
    expect(platform.fetch).toHaveBeenLastCalledWith('/-/feedback/v1/items/here/replies', expect.anything());
  });

  it('closes an open delete question with Escape before it leaves', async () => {
    const { root, press, escape, widget, find } = await openMine();
    await vi.waitFor(() => expect(find('delete-here')).not.toBeNull());
    press('delete-here');
    escape();
    expect(widget.state).toBe('mine');
    escape();
    expect(widget.state).toBe('menu');
    expect(root.querySelector('.pins .pin')).toBeNull();
    expect(find('menu.mine')).toBe(widget.root.activeElement);
  });

  it('shows why the list could not load', async () => {
    pageText('');
    const mounted = mount({ account: true });
    mounted.platform.fetch.mockResolvedValue(problem(404, 'FEEDBACK_UNAVAILABLE'));
    mounted.trigger.click();
    mounted.press('menu.mine');
    await vi.waitFor(() => expect(mounted.alert()).toBe(t('error.unavailable.other')));
  });

  it('ignores a list or an error that arrives after the giver left', async () => {
    const mounted = mount({ account: true });
    let answer!: (response: Response) => void;
    mounted.platform.fetch.mockImplementation(() => new Promise((resolve) => (answer = resolve)));
    mounted.trigger.click();
    mounted.press('menu.mine');
    mounted.escape();
    answer(json(200, { items: [ON_PAGE] }));
    await Promise.resolve();
    mounted.press('menu.mine');
    mounted.escape();
    answer(problem(404, 'FEEDBACK_UNAVAILABLE'));
    await vi.waitFor(() => expect(mounted.platform.fetch).toHaveBeenCalledTimes(2));
    expect(mounted.root.querySelector('.pins .pin')).toBeNull();
    expect(mounted.widget.state).toBe('menu');
  });

  it('opens from the thanks after sending, and goes back to the menu', async () => {
    const mounted = mount({ identity: 'account', account: true });
    mounted.trigger.click();
    mounted.press('menu.page');
    mounted.find<HTMLTextAreaElement>('comment')!.value = 'Goed zo';
    mounted.press('composer.send');
    await vi.waitFor(() => expect(mounted.widget.state).toBe('sent'));
    expect(sentItem(mounted.platform).item.author).toEqual({ kind: 'account' });
    expect(mounted.root.querySelector('.panel')!.textContent).toContain('Je vindt die terug onder Mijn feedback.');
    mounted.platform.fetch.mockResolvedValue(json(200, { items: [] }));
    mounted.press('sent.mine');
    expect(mounted.widget.state).toBe('mine');
    mounted.escape();
    expect(mounted.focused()).toBe(mounted.root.querySelector('.heading'));
  });

  it('keeps an earlier name in the draft when the giver composes with the account', () => {
    const mounted = mount({ account: true });
    mounted.trigger.click();
    mounted.press('menu.page');
    mounted.press('composer.cancel');
    mounted.press('menu.page');
    expect(mounted.root.querySelector('#tf-name')).toBeNull();
  });
});

describe('hiding the button', () => {
  it('hides for a minute on this page and comes back by itself', () => {
    vi.useFakeTimers();
    const { trigger, press, widget, status, root, focused } = mount();
    trigger.click();
    press('menu.hide');
    expect(root.querySelector('.heading')!.textContent).toBe('Knop verbergen');
    expect(focused()).toBe(root.querySelector('.heading'));
    press('hide.minute');
    expect(widget.state).toBe('hidden');
    expect(trigger.hidden).toBe(true);
    expect(status()).toBe('De feedbackknop is verborgen en komt over een minuut terug.');
    vi.advanceTimersByTime(HIDE_FOR_MS);
    expect(widget.state).toBe('idle');
    expect(trigger.hidden).toBe(false);
    expect(status()).toBe('De feedbackknop is weer zichtbaar.');
  });

  describe('after "Een minuut", focus goes back to the page element that had it when the widget opened', () => {
    function hideForAMinute(mounted: ReturnType<typeof mount>): void {
      mounted.press('menu.hide');
      mounted.press('hide.minute');
      expect(mounted.widget.state).toBe('hidden');
    }

    beforeEach(() => {
      vi.useFakeTimers();
    });

    it('the one the keyboard left for the button', () => {
      pageText('<a id="before" href="#b">Vorige</a><a id="after" href="#a">Volgende</a>');
      const mounted = mount();
      const before = document.getElementById('before')!;
      before.focus();
      mounted.trigger.focus();
      mounted.trigger.click();
      // Where focus goes meanwhile does not count: only where it was.
      document.getElementById('after')!.focus();
      mounted.find('menu.hide')!.focus();
      const focus = vi.spyOn(before, 'focus');
      hideForAMinute(mounted);
      expect(document.activeElement).toBe(before);
      expect(focus.mock.calls).toEqual([[{ preventScroll: true }]]);
    });

    it('the one that kept focus while the button was pressed with a pointer, which takes none', () => {
      pageText('<input id="search">');
      const mounted = mount();
      const search = document.getElementById('search')!;
      search.focus();
      mounted.trigger.click();
      const focus = vi.spyOn(search, 'focus');
      hideForAMinute(mounted);
      expect(document.activeElement).toBe(search);
      // The page stays where the visitor read on to: a search field at its
      // top does not pull it back up.
      expect(focus.mock.calls).toEqual([[{ preventScroll: true }]]);
    });

    it('the control inside a web component of the page that had it', () => {
      pageText('<page-links id="links"></page-links>');
      const mounted = mount();
      const host = document.getElementById('links')!;
      const [, contact] = linksOf(host);
      contact!.focus();
      mounted.trigger.focus();
      mounted.trigger.click();
      hideForAMinute(mounted);
      expect([document.activeElement, host.shadowRoot!.activeElement]).toEqual([host, contact]);
    });

    it('the control inside a web component that kept focus while the button was pressed with a pointer', () => {
      pageText('<page-links id="links"></page-links>');
      const mounted = mount();
      const host = document.getElementById('links')!;
      const [, contact] = linksOf(host);
      contact!.focus();
      mounted.trigger.click();
      // On into the panel with the keyboard from there.
      mounted.find('menu.hide')!.focus();
      hideForAMinute(mounted);
      expect([document.activeElement, host.shadowRoot!.activeElement]).toEqual([host, contact]);
    });

    it('nowhere when that element is gone or takes no focus now, or focus was on no element of the page', () => {
      pageText('<a id="gone" href="#g">Weg</a><button id="off" type="button">Uit</button>');
      const mounted = mount();
      const gone = document.getElementById('gone')!;
      gone.focus();
      mounted.trigger.focus();
      mounted.trigger.click();
      gone.remove();
      hideForAMinute(mounted);
      expect(document.activeElement).toBe(mounted.widget.host);
      vi.advanceTimersByTime(HIDE_FOR_MS);

      const off = document.getElementById('off') as HTMLButtonElement;
      off.focus();
      mounted.trigger.focus();
      mounted.trigger.click();
      off.disabled = true;
      hideForAMinute(mounted);
      expect(document.activeElement).toBe(mounted.widget.host);
      vi.advanceTimersByTime(HIDE_FOR_MS);

      // Focus on the page that went nowhere, as when the window lost it.
      off.disabled = false;
      off.focus();
      off.blur();
      mounted.trigger.focus();
      mounted.trigger.click();
      hideForAMinute(mounted);
      expect(document.activeElement).toBe(mounted.widget.host);
    });

    it('nowhere when no element of the page had focus and the button was pressed with a pointer', () => {
      pageText('<a id="link" href="#l">Link</a>');
      const mounted = mount();
      expect(document.activeElement).toBe(document.body);
      mounted.trigger.click();
      hideForAMinute(mounted);
      expect(document.activeElement).toBe(mounted.widget.host);
    });
  });

  it('asks before hiding until the browser closes, names the way back, and posts the form', () => {
    const { trigger, press, root, platform, focused, find } = mount();
    trigger.click();
    press('menu.hide');
    press('hide.session');
    expect(focused()).toBe(find('cancel'));
    const link = root.querySelector('#tf-hide-question a')!;
    expect(link.getAttribute('href')).toBe('https://toon.test/-/feedback/show?returnTo=%2Fteam%2Fsite%2F');
    press('hide.confirm');
    const form = platform.submitForm.mock.calls[0]![0];
    expect(root.contains(form)).toBe(true);
    expect(form.getAttribute('action')).toBe('/-/feedback/hide');
  });

  it('backs out of hiding one step at a time', () => {
    const { trigger, press, escape, widget, focused, find } = mount();
    trigger.click();
    press('menu.hide');
    press('hide.session');
    escape();
    expect(widget.state).toBe('hiding');
    expect(focused()).toBe(find('hide.session'));
    press('hide.session');
    press('cancel');
    expect(widget.state).toBe('hiding');
    press('hide.cancel');
    expect(widget.state).toBe('menu');
    expect(focused()).toBe(find('menu.hide'));
  });
});

describe('the bottom sheet', () => {
  function narrow(platform: ReturnType<typeof fakePlatform>, width = 400): void {
    platform.viewport.mockReturnValue({ ...platform.viewport(), width, height: 800, visualHeight: 800, visualOffsetTop: 0 });
  }

  function tab(target: Element, shiftKey = false): KeyboardEvent {
    const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey, bubbles: true, composed: true, cancelable: true });
    target.dispatchEvent(event);
    return event;
  }

  it('stays above an on-screen keyboard on a narrow screen', () => {
    const { platform, panel, trigger } = mount();
    const listener = platform.onViewportChange.mock.calls[0]![0];
    platform.viewport.mockReturnValue({ ...platform.viewport(), width: 400, height: 800, visualHeight: 500, visualOffsetTop: 0 });
    listener();
    expect([panel.style.bottom, panel.style.maxHeight]).toEqual(['300px', '425px']);
    platform.viewport.mockReturnValue({ ...platform.viewport(), visualHeight: 900, visualOffsetTop: 0 });
    trigger.click();
    expect(panel.style.bottom).toBe('0px');
    platform.viewport.mockReturnValue({ ...platform.viewport(), width: 1024 });
    listener();
    expect([panel.style.bottom, panel.style.maxHeight]).toEqual(['', '']);
  });

  it('hides the button it would cover while it shows, and hands focus back to it when it closes', () => {
    const { platform, trigger, focused, escape, press, root } = mount();
    narrow(platform);
    trigger.focus();
    trigger.click();
    expect(trigger.hidden).toBe(true);
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    expect(focused()).toBe(root.querySelector('.heading'));
    escape();
    expect(trigger.hidden).toBe(false);
    expect(focused()).toBe(trigger);
    trigger.click();
    press('menu.close');
    expect(trigger.hidden).toBe(false);
    expect(focused()).toBe(trigger);
  });

  it('shows the button again when the window widens, and moves focus off it when the window narrows', () => {
    const { platform, trigger, focused, root } = mount();
    const listener = platform.onViewportChange.mock.calls[0]![0];
    trigger.click();
    expect(trigger.hidden).toBe(false);
    trigger.focus();
    narrow(platform);
    listener();
    expect(trigger.hidden).toBe(true);
    expect(focused()).toBe(root.querySelector('.heading'));
    narrow(platform, 600);
    listener();
    expect(trigger.hidden).toBe(false);
    expect(focused()).toBe(root.querySelector('.heading'));
  });

  it('keeps Tab and Shift+Tab inside the sheet, which covers the page behind it', () => {
    const { platform, trigger, find, focused, root } = mount();
    narrow(platform);
    trigger.click();
    const [first, last] = [find('menu.point')!, find('menu.close')!];
    last.focus();
    expect(tab(last).defaultPrevented).toBe(true);
    expect(focused()).toBe(first);
    expect(tab(first, true).defaultPrevented).toBe(true);
    expect(focused()).toBe(last);
    const heading = root.querySelector<HTMLElement>('.heading')!;
    heading.focus();
    expect(tab(heading, true).defaultPrevented).toBe(true);
    expect(focused()).toBe(last);
    // In between, Tab is the browser's own.
    expect(tab(find('menu.text')!).defaultPrevented).toBe(false);
    expect(tab(find('menu.text')!, true).defaultPrevented).toBe(false);
  });

  it('leaves Tab alone in the panel of a wide window, and on the closed button', () => {
    const { platform, trigger, find } = mount();
    trigger.click();
    expect(tab(find('menu.close')!).defaultPrevented).toBe(false);
    trigger.click();
    narrow(platform);
    expect(tab(trigger).defaultPrevented).toBe(false);
  });
});

describe('keeping focus where it can be seen', () => {
  it('takes focus back from the page while an overlay covers it', () => {
    pageText('<a id="link" href="#x">Home</a>');
    const { trigger, press, root, widget } = mount();
    trigger.click();
    press('menu.point');
    const overlay = root.querySelector<HTMLElement>('.overlay')!;
    document.getElementById('link')!.focus();
    expect(root.activeElement).toBe(overlay);
    expect(document.activeElement).toBe(widget.host);
  });

  it('lets a page that insists on focus have it, rather than the two going round for ever', () => {
    pageText('<a id="link" href="#x">Home</a>');
    const { trigger, press, widget } = mount();
    trigger.click();
    press('menu.point');
    const link = document.getElementById('link')!;
    const insist = (event: FocusEvent) => {
      if (event.target === widget.host) link.focus();
    };
    document.addEventListener('focusin', insist);
    link.focus();
    document.removeEventListener('focusin', insist);
    expect(document.activeElement).toBe(link);
  });

  function withBoxes(html: string) {
    pageText(html);
    const mounted = mount();
    // A 1024 by 768 window, the button in its corner.
    const boxes = new Map<Element, Box>([[mounted.trigger, { left: 888, top: 708, width: 120, height: 44 }]]);
    mounted.platform.box.mockImplementation((element) => boxes.get(element) ?? { left: 0, top: 0, width: 0, height: 0 });
    const place = () => [mounted.trigger.style.left, mounted.trigger.style.right, mounted.trigger.style.bottom];
    return { ...mounted, boxes, place };
  }

  function footer() {
    const mounted = withBoxes('<a id="foot" href="#x">English</a>');
    return { ...mounted, link: document.getElementById('foot')! };
  }

  it('moves the button to the other corner while a focused page element lies under it', () => {
    const { link, boxes, place } = footer();
    boxes.set(link, { left: 900, top: 720, width: 60, height: 20 });
    link.focus();
    expect(place()).toEqual(['16px', 'auto', '']);
    link.blur();
    expect(place()).toEqual(['', '', '']);
  });

  it('lifts the button above an element across the bottom, and follows it as the page scrolls or the window changes', () => {
    const { link, boxes, place, platform } = footer();
    boxes.set(link, { left: 0, top: 700, width: 1024, height: 40 });
    link.focus();
    expect(place()).toEqual(['', '', '76px']);
    boxes.set(link, { left: 0, top: 100, width: 60, height: 20 });
    document.dispatchEvent(new Event('scroll'));
    expect(place()).toEqual(['', '', '']);
    boxes.set(link, { left: 900, top: 720, width: 60, height: 20 });
    platform.onViewportChange.mock.calls[0]![0]();
    expect(place()).toEqual(['16px', 'auto', '']);
  });

  it('leaves the button in its corner for focus from the mouse, and measures nothing on scroll then', () => {
    const { link, boxes, place, platform } = footer();
    boxes.set(link, { left: 900, top: 720, width: 60, height: 20 });
    platform.keyboardFocus.mockReturnValue(false);
    link.focus();
    expect(platform.keyboardFocus).toHaveBeenCalledWith(link);
    expect(place()).toEqual(['', '', '']);
    platform.box.mockClear();
    document.dispatchEvent(new Event('scroll'));
    expect(platform.box).not.toHaveBeenCalled();
  });

  it('leaves the button in its corner by a focused element it cannot hide entirely, a skip link target', () => {
    const { boxes, place } = footer();
    // <main tabindex="-1"> below a header and a navigation bar, which the
    // button lifted above it would cover.
    const main = document.getElementById('content')!;
    main.tabIndex = -1;
    boxes.set(main, { left: 0, top: 148, width: 1024, height: 2000 });
    main.focus();
    expect(place()).toEqual(['', '', '']);
  });

  it('takes focus back to an overlay whatever moved it to the page', () => {
    pageText('<a id="link" href="#x">Home</a>');
    const { trigger, press, root, platform } = mount();
    platform.keyboardFocus.mockReturnValue(false);
    trigger.click();
    press('menu.point');
    document.getElementById('link')!.focus();
    expect(root.activeElement).toBe(root.querySelector('.overlay'));
  });

  it('puts the button back when focus goes into the widget, and measures nothing on scroll then', () => {
    const { link, boxes, place, trigger, platform } = footer();
    boxes.set(link, { left: 900, top: 720, width: 60, height: 20 });
    link.focus();
    trigger.focus();
    expect(place()).toEqual(['', '', '']);
    platform.box.mockClear();
    document.dispatchEvent(new Event('scroll'));
    expect(platform.box).not.toHaveBeenCalled();
  });

  describe('inside a web component of the page', () => {
    // As in the browsers: the host never matches :focus-visible, the control
    // inside it does.
    function components(html: string) {
      const mounted = withBoxes(html);
      mounted.platform.keyboardFocus.mockImplementation((element) => element.localName === 'a');
      return mounted;
    }

    it('moves the button off a control with keyboard focus inside one, measured as the control itself', () => {
      const { boxes, place, platform } = components('<page-links id="links"></page-links>');
      const host = document.getElementById('links')!;
      const [, , english] = linksOf(host);
      // The row of links spans the window: measured as the host, the button
      // would go up rather than to the other corner.
      boxes.set(host, { left: 0, top: 700, width: 1024, height: 40 });
      boxes.set(english!, { left: 900, top: 720, width: 60, height: 20 });
      english!.focus();
      expect(document.activeElement).toBe(host);
      expect(platform.keyboardFocus).toHaveBeenLastCalledWith(english);
      expect(place()).toEqual(['16px', 'auto', '']);
    });

    it('hears a move between two controls inside one, which the document never hears', () => {
      const { boxes, place } = components('<page-links id="links"></page-links>');
      const [privacy, , english] = linksOf(document.getElementById('links')!);
      boxes.set(privacy!, { left: 10, top: 720, width: 60, height: 20 });
      boxes.set(english!, { left: 900, top: 720, width: 60, height: 20 });
      const heard = vi.fn();
      document.addEventListener('focusin', heard, true);
      privacy!.focus();
      expect(place()).toEqual(['', '', '']);
      english!.focus();
      document.removeEventListener('focusin', heard, true);
      expect(heard).toHaveBeenCalledOnce();
      expect(place()).toEqual(['16px', 'auto', '']);
      privacy!.focus();
      expect(place()).toEqual(['', '', '']);
    });

    it('listens inside each one once, however often focus goes in', () => {
      const { platform } = components('<a id="out" href="#o">Uit</a><page-links id="links"></page-links>');
      const [privacy, contact] = linksOf(document.getElementById('links')!);
      const out = document.getElementById('out')!;
      privacy!.focus();
      out.focus();
      privacy!.focus();
      platform.keyboardFocus.mockClear();
      contact!.focus();
      expect(platform.keyboardFocus.mock.calls).toEqual([[contact]]);
    });

    it('goes down through a component inside a component', () => {
      const { boxes, place, platform } = components('<page-footer id="footer"></page-footer>');
      const inner = document.getElementById('footer')!.shadowRoot!.querySelector('page-links')!;
      const [privacy, , english] = linksOf(inner);
      boxes.set(english!, { left: 900, top: 720, width: 60, height: 20 });
      english!.focus();
      expect(platform.keyboardFocus).toHaveBeenLastCalledWith(english);
      expect(place()).toEqual(['16px', 'auto', '']);
      privacy!.focus();
      expect(platform.keyboardFocus).toHaveBeenLastCalledWith(privacy);
      expect(place()).toEqual(['', '', '']);
    });

    it('leaves the button in its corner for a control inside one that has focus from the mouse', () => {
      const { boxes, place, platform } = components('<page-links id="links"></page-links>');
      const [, , english] = linksOf(document.getElementById('links')!);
      boxes.set(english!, { left: 900, top: 720, width: 60, height: 20 });
      platform.keyboardFocus.mockReturnValue(false);
      english!.focus();
      expect(platform.keyboardFocus).toHaveBeenLastCalledWith(english);
      expect(place()).toEqual(['', '', '']);
    });

    it('asks a host that has focus itself, with nothing focused inside it', () => {
      const { boxes, place, platform } = components('<page-card id="card" tabindex="0"></page-card>');
      const card = document.getElementById('card')!;
      boxes.set(card, { left: 900, top: 720, width: 60, height: 20 });
      card.focus();
      expect(platform.keyboardFocus).toHaveBeenLastCalledWith(card);
      expect(place()).toEqual(['', '', '']);
      platform.keyboardFocus.mockReturnValue(true);
      card.blur();
      card.focus();
      expect(place()).toEqual(['16px', 'auto', '']);
    });

    it('counts focus inside a closed one as keyboard focus, which it cannot tell apart', () => {
      const { boxes, place, platform } = components('<closed-link id="closed"></closed-link>');
      const host = document.getElementById('closed') as ClosedLink;
      boxes.set(host, { left: 900, top: 720, width: 60, height: 20 });
      platform.keyboardFocus.mockReturnValue(false);
      host.inner.focus();
      expect(document.activeElement).toBe(host);
      expect(platform.keyboardFocus).not.toHaveBeenCalled();
      expect(place()).toEqual(['16px', 'auto', '']);
    });

    it('takes focus inside one back to an overlay', () => {
      pageText('<page-links id="links"></page-links>');
      const { trigger, press, root } = mount();
      trigger.click();
      press('menu.point');
      linksOf(document.getElementById('links')!)[0]!.focus();
      expect(root.activeElement).toBe(root.querySelector('.overlay'));
    });
  });

  describe('the open panel of a wide window', () => {
    // At 1024 by 768 the panel sits at right 16 and bottom 76, 384 wide.
    function opened(html = '<a id="side" href="#x">Document 11</a>') {
      const mounted = withBoxes(html);
      mounted.boxes.set(mounted.panel, { left: 624, top: 24, width: 384, height: 668 });
      mounted.trigger.click();
      const side = document.getElementById('side')!;
      const panelPlace = () => [mounted.panel.style.left, mounted.panel.style.right];
      return { ...mounted, side, panelPlace };
    }

    it('goes to the bottom left corner while the page element with keyboard focus lies under it, and back', () => {
      const { boxes, side, panelPlace } = opened('<a id="side" href="#x">Document 11</a><a id="home" href="#y">Home</a>');
      boxes.set(side, { left: 700, top: 231, width: 105, height: 21 });
      side.focus();
      expect(panelPlace()).toEqual(['16px', 'auto']);
      // Keyboard focus on a page element clear of its own corner brings it back.
      const home = document.getElementById('home')!;
      boxes.set(home, { left: 10, top: 10, width: 60, height: 20 });
      home.focus();
      expect(panelPlace()).toEqual(['', '']);
    });

    it('stays where it is while focus is in it, or nowhere: a press in it never moves it from under the pointer', () => {
      const { boxes, side, panelPlace, root, find, trigger, platform } = opened();
      boxes.set(side, { left: 700, top: 231, width: 105, height: 21 });
      side.focus();
      expect(panelPlace()).toEqual(['16px', 'auto']);
      // Chromium and Firefox focus a link pressed in the panel.
      root.querySelector<HTMLElement>('.heading')!.focus();
      expect(panelPlace()).toEqual(['16px', 'auto']);
      find('menu.close')!.focus();
      expect(panelPlace()).toEqual(['16px', 'auto']);
      // WebKit leaves the link unfocused, and focus goes nowhere.
      side.focus();
      side.blur();
      expect(document.activeElement).toBe(document.body);
      expect(panelPlace()).toEqual(['16px', 'auto']);
      // So does focus from the mouse on the page, and the window changing.
      platform.keyboardFocus.mockReturnValue(false);
      side.focus();
      platform.onViewportChange.mock.calls[0]![0]();
      expect(panelPlace()).toEqual(['16px', 'auto']);
      // Closing puts it back in its corner, where it opens again.
      trigger.click();
      expect(panelPlace()).toEqual(['', '']);
      trigger.click();
      expect(panelPlace()).toEqual(['', '']);
    });

    it('stays in its corner by an element it would cover on the left as well, or one clear of it', () => {
      const { boxes, side, panelPlace } = opened();
      boxes.set(side, { left: 0, top: 300, width: 1024, height: 40 });
      side.focus();
      expect(panelPlace()).toEqual(['', '']);
      boxes.set(side, { left: 100, top: 300, width: 100, height: 20 });
      document.dispatchEvent(new Event('scroll'));
      expect(panelPlace()).toEqual(['', '']);
    });

    it('follows the element as the page scrolls', () => {
      const { boxes, side, panelPlace } = opened();
      boxes.set(side, { left: 700, top: 900, width: 105, height: 21 });
      side.focus();
      expect(panelPlace()).toEqual(['', '']);
      boxes.set(side, { left: 700, top: 400, width: 105, height: 21 });
      document.dispatchEvent(new Event('scroll'));
      expect(panelPlace()).toEqual(['16px', 'auto']);
    });

    it('stays for focus from the mouse', () => {
      const { boxes, side, panelPlace, platform } = opened();
      boxes.set(side, { left: 700, top: 231, width: 105, height: 21 });
      platform.keyboardFocus.mockReturnValue(false);
      side.focus();
      expect(panelPlace()).toEqual(['', '']);
    });

    it('leaves the sheet of a narrow window to its own rules', () => {
      const { boxes, side, panelPlace, platform } = opened();
      boxes.set(side, { left: 700, top: 231, width: 105, height: 21 });
      side.focus();
      expect(panelPlace()).toEqual(['16px', 'auto']);
      platform.viewport.mockReturnValue({ ...platform.viewport(), width: 400 });
      platform.onViewportChange.mock.calls[0]![0]();
      expect(panelPlace()).toEqual(['', '']);
    });

    it('measures nothing while the panel is closed', () => {
      const { boxes, side, panel, panelPlace, trigger, platform } = opened();
      trigger.click();
      boxes.set(side, { left: 700, top: 231, width: 105, height: 21 });
      side.focus();
      expect(platform.box).not.toHaveBeenCalledWith(panel);
      expect(panelPlace()).toEqual(['', '']);
    });
  });

  describe('Escape pressed on the page', () => {
    function escapeOn(target: Element, init: KeyboardEventInit = {}): KeyboardEvent {
      const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true, ...init });
      target.dispatchEvent(event);
      return event;
    }

    it('closes the open panel, keeps what was typed and leaves focus and the key to the page', () => {
      pageText('<a id="side" href="#x">Document 11</a>');
      const { trigger, press, find, widget } = mount();
      trigger.click();
      press('menu.page');
      find<HTMLTextAreaElement>('comment')!.value = 'De kop klopt niet';
      const side = document.getElementById('side')!;
      side.focus();
      const after = vi.fn();
      window.addEventListener('keydown', after);
      const event = escapeOn(side);
      window.removeEventListener('keydown', after);
      expect(widget.state).toBe('idle');
      expect(document.activeElement).toBe(side);
      expect(event.defaultPrevented).toBe(false);
      expect(after).toHaveBeenCalledOnce();
      trigger.click();
      press('menu.page');
      expect(find<HTMLTextAreaElement>('comment')!.value).toBe('De kop klopt niet');
    });

    it('leaves the panel to a key the page took for itself, to one that ends a composition, and to other keys', () => {
      pageText('<input id="search">');
      const { trigger, widget } = mount();
      trigger.click();
      const search = document.getElementById('search')!;
      search.focus();
      const own = (event: Event) => event.preventDefault();
      search.addEventListener('keydown', own);
      escapeOn(search);
      search.removeEventListener('keydown', own);
      escapeOn(search, { isComposing: true });
      escapeOn(search, { key: 'a' });
      expect(widget.state).toBe('menu');
    });

    it('cannot stop feedback on its way', async () => {
      pageText('<a id="side" href="#x">Document 11</a>');
      const { trigger, press, find, platform, widget } = mount();
      platform.fetch.mockReturnValue(new Promise(() => {}));
      trigger.click();
      press('menu.page');
      find<HTMLTextAreaElement>('comment')!.value = 'Prima';
      widget.root.querySelector<HTMLInputElement>('#tf-name')!.value = 'Jan';
      press('composer.send');
      await vi.waitFor(() => expect(platform.fetch).toHaveBeenCalled());
      escapeOn(document.getElementById('side')!);
      expect(widget.state).toBe('sending');
    });

    it('is not what Escape inside the widget is, which stops at its host and backs out one step', () => {
      const { trigger, press, find, widget } = mount();
      trigger.click();
      press('menu.page');
      escapeOn(find('comment')!, { composed: true });
      expect(widget.state).toBe('menu');
    });
  });

  it('opens the privacy statement in this tab on a shielded page, where a new tab cannot show it', () => {
    const { trigger, press, root, platform } = mount();
    platform.opaqueOrigin.mockReturnValue(true);
    trigger.click();
    press('menu.page');
    const link = root.querySelector('#tf-notice a')!;
    expect(link.hasAttribute('target')).toBe(false);
    expect(root.querySelector('#tf-privacy-note')!.textContent).toBe(t('composer.privacy.note'));
  });
});

describe('a place from a link', () => {
  const GONE = { k: 'text' as const, s: null, x: null, y: null, px: null, py: null, q: 'nergens' };

  it('scrolls the point itself into view, marks it and says so, until the panel opens', () => {
    pageText('<p id="a">x</p>');
    const { widget, platform, root, trigger, status } = mount();
    platform.box.mockImplementation((element) =>
      element === widget.host ? { left: 0, top: 0, width: 0, height: 0 } : { left: 10, top: 20, width: 100, height: 50 },
    );
    widget.highlightWhenLoaded({ k: 'point', s: '#a', x: 0.5, y: 0.5, px: null, py: null, q: null });
    // Nothing on this page scrolls but the window, which puts the point in
    // its middle, not the element the point is on.
    expect(platform.scrollers).toHaveBeenCalledWith(document.getElementById('a'));
    expect(platform.scrollElementTo).not.toHaveBeenCalled();
    expect(platform.scrollToPage.mock.calls).toEqual([[60, 45]]);
    const pin = root.querySelector<HTMLElement>('.pin-highlight')!;
    expect([pin.getAttribute('role'), pin.getAttribute('aria-label')]).toEqual(['img', 'Hier gaat de feedback over']);
    expect([pin.style.left, pin.style.top]).toEqual(['60px', '45px']);
    expect(status()).toBe('De plek waar de feedback over gaat, is gemarkeerd.');
    expect(root.querySelector<HTMLElement>('.lost')!.hidden).toBe(true);
    trigger.click();
    expect(root.querySelector('[data-highlight]')).toBeNull();
  });

  it('marks a point with a ring that keeps what it marks in sight and lets it take its own clicks', () => {
    // The pins of Mijn feedback are the same ring.
    expect(STYLES).toMatch(/\.pin \{[^}]*width: 36px;[^}]*height: 36px;[^}]*background: transparent;/);
    expect(STYLES).toMatch(/\.pin \{[^}]*pointer-events: none;/);
    expect(STYLES).toContain('.frame, .crosshair, .corner, .pin { border-color: Highlight; box-shadow: none; }');
  });

  it('marks a quote with its boxes, in two tones that show on any band of the page in any scheme', () => {
    pageText('<p>Hier staat de knop.</p>');
    const { widget, root } = mount();
    widget.highlightWhenLoaded({ k: 'text', s: null, x: null, y: null, px: null, py: null, q: 'de knop' });
    const mark = root.querySelector<HTMLElement>('.mark')!;
    expect([mark.style.left, mark.style.top, mark.style.width, mark.style.height]).toEqual(['10px', '20px', '120px', '18px']);
    const rule = /^\.mark \{[^}]*\}/m.exec(STYLES)![0];
    // A white edge against the box and a dark one around it, both outside
    // the text: neither a border nor an inset shadow lies over its glyphs.
    expect(rule).toContain('box-shadow: 0 0 0 2px #ffffff, 0 0 0 4px #1b1f24;');
    expect(rule).not.toMatch(/border|inset/);
    expect(rule).not.toContain('var(--tf-accent)');
    // Forced colours drop the shadow: an outline, outside the text too.
    expect(STYLES).toContain('.mark { outline: 2px solid Highlight; background: transparent; }');
  });

  it('lies over the button, which does not move off it, and under the panel, the note and the button beside a selection', () => {
    for (const mark of ['pin-highlight', 'mark']) {
      expect(zIndexOf(mark)).toBeGreaterThan(zIndexOf('trigger')!);
      for (const above of ['panel', 'lost', 'floating']) expect(zIndexOf(mark)).toBeLessThan(zIndexOf(above)!);
    }
  });

  it('replaces an earlier place by a later one', () => {
    pageText('<p>Hier staat de knop.</p>');
    const { widget, root } = mount();
    const lost = root.querySelector<HTMLElement>('.lost')!;
    widget.highlightWhenLoaded({ k: 'text', s: null, x: null, y: null, px: null, py: null, q: 'de knop' });
    widget.highlightWhenLoaded({ k: 'point', s: null, x: null, y: null, px: 70, py: 80, q: null });
    expect(root.querySelectorAll('.mark').length).toBe(0);
    expect(root.querySelectorAll('.pin-highlight').length).toBe(1);
    widget.highlightWhenLoaded(GONE);
    expect(root.querySelectorAll('[data-highlight]').length).toBe(0);
    expect(lost.hidden).toBe(false);
    widget.highlightWhenLoaded({ k: 'point', s: null, x: null, y: null, px: 70, py: 80, q: null });
    expect(lost.hidden).toBe(true);
  });

  it('says when the place is gone, and shows it beside the button without opening the panel', () => {
    const { widget, root, platform, status, panel } = mount();
    widget.highlightWhenLoaded(GONE);
    expect(root.querySelector('[data-highlight]')).toBeNull();
    expect(platform.scrollToPage).not.toHaveBeenCalled();
    const lost = root.querySelector<HTMLElement>('.lost')!;
    expect(lost.hidden).toBe(false);
    expect(lost.querySelector('p')!.textContent).toBe('De plek van deze feedback is op deze pagina niet meer te vinden.');
    expect(status()).toBe(t('highlight.lost'));
    expect([widget.state, panel.hidden]).toEqual(['idle', true]);
    const close = lost.querySelector('[data-key="lost.close"]')!;
    expect([close.textContent, close.getAttribute('aria-describedby')]).toEqual(['Sluiten', 'tf-lost']);
  });

  it('takes the note away when the panel opens', () => {
    const { widget, root, trigger } = mount();
    widget.highlightWhenLoaded(GONE);
    trigger.click();
    expect(root.querySelector<HTMLElement>('.lost')!.hidden).toBe(true);
  });

  it('closes the note with its own button, handing focus on it to the Feedback button', () => {
    const { widget, root, trigger, find, focused } = mount();
    widget.highlightWhenLoaded(GONE);
    const lost = root.querySelector<HTMLElement>('.lost')!;
    find('lost.close')!.click();
    expect(lost.hidden).toBe(true);
    expect(focused()).toBeNull();
    widget.highlightWhenLoaded(GONE);
    find('lost.close')!.focus();
    find('lost.close')!.click();
    expect(lost.hidden).toBe(true);
    expect(focused()).toBe(trigger);
  });

  it('waits for the page to finish loading', () => {
    const { widget, platform } = mount();
    const readyState = vi.spyOn(document, 'readyState', 'get').mockReturnValue('interactive');
    widget.highlightWhenLoaded({ k: 'point', s: null, x: null, y: null, px: 5, py: 6, q: null });
    expect(platform.scrollToPage).not.toHaveBeenCalled();
    readyState.mockReturnValue('complete');
    window.dispatchEvent(new Event('load'));
    expect(platform.scrollToPage).toHaveBeenCalledWith(5, 6);
  });

  describe('says what it marked in the words of the page, never in those of the link', () => {
    it('for a point: the element it names, as the crosshair would describe it', () => {
      pageText('<p id="a">x</p>');
      const { widget, platform, status } = mount();
      platform.visibleText.mockReturnValue('Onderdeel A\nHet eerste onderdeel');
      widget.highlightWhenLoaded({ k: 'point', s: '#a', x: 0.5, y: 0.5, px: null, py: null, q: 'Geef je wachtwoord' });
      expect(platform.visibleText).toHaveBeenCalledWith(document.getElementById('a'));
      expect(status()).toBe('Gemarkeerd: Onderdeel A Het eerste onderdeel');
    });

    it('in the language the page gives those words, where that is not the widget\'s', () => {
      pageText('<p id="a" lang="en">Annual report</p><p lang="fy">It jierferslach</p>');
      const { widget, platform, root } = mount();
      platform.visibleText.mockReturnValue('Annual report');
      const line = () => root.querySelector('[role="status"]')!.lastElementChild!;
      widget.highlightWhenLoaded({ k: 'point', s: '#a', x: 0.5, y: 0.5, px: null, py: null, q: null });
      expect(line().innerHTML).toBe('Gemarkeerd: <span lang="en">Annual report</span>');
      widget.highlightWhenLoaded({ k: 'text', s: null, x: null, y: null, px: null, py: null, q: 'jierferslach' });
      expect(line().innerHTML).toBe('Gemarkeerd: <span lang="fy">jierferslach</span>');
    });

    it('for a quote: the first words of the passage it matched on the page', () => {
      // Read as the quote was matched: the icon-font glyph out, the
      // whitespace one space.
      pageText('<p>Hier staat de\n   knop van het formulier.</p>');
      const { widget, status } = mount();
      widget.highlightWhenLoaded({ k: 'text', s: null, x: null, y: null, px: null, py: null, q: 'de knop van' });
      expect(status()).toBe('Gemarkeerd: de knop van');
      const long = 'een twee drie vier vijf zes zeven acht negen tien elf twaalf dertien';
      pageText(`<p>${long}</p>`);
      widget.highlightWhenLoaded({ k: 'text', s: null, x: null, y: null, px: null, py: null, q: long });
      expect(status()).toBe('Gemarkeerd: een twee drie vier vijf zes zeven acht negen tien elf twaalf...');
    });

    it('for a place from page coordinates: what lies there in the window now', () => {
      pageText('<p id="b">Contact</p>');
      const { widget, platform, status } = mount({}, 'en');
      platform.viewport.mockReturnValue({ ...platform.viewport(), scrollX: 10, scrollY: 500 });
      platform.elementAt.mockReturnValue(document.getElementById('b'));
      platform.visibleText.mockReturnValue('Contact');
      widget.highlightWhenLoaded({ k: 'point', s: '#weg', x: 0.5, y: 0.5, px: 70, py: 580, q: 'Geef je wachtwoord' });
      expect(platform.elementAt).toHaveBeenCalledWith(60, 80, widget.host);
      expect(status()).toBe('Marked: Contact');
    });

    it('only that it marked the place when the page has no words for it', () => {
      pageText('<p id="a"></p>');
      const { widget, platform, status } = mount();
      // The page as a whole has words, which are not the place.
      platform.visibleText.mockImplementation((element) => (element.id === 'a' ? '' : 'De hele pagina'));
      const at = { k: 'point' as const, s: null, x: null, y: null, px: 70, py: 80, q: 'Geef je wachtwoord' };
      const shown = 'De plek waar de feedback over gaat, is gemarkeerd.';
      for (const element of [null, document.body, document.documentElement, document.getElementById('a')]) {
        platform.elementAt.mockReturnValue(element);
        widget.highlightWhenLoaded(at);
        expect(status()).toBe(shown);
      }
      // A quote of whitespace alone matches, and says nothing.
      pageText('<p>a  b</p>');
      widget.highlightWhenLoaded({ ...at, k: 'text', px: null, py: null, q: ' ' });
      expect(status()).toBe(shown);
    });
  });

  describe('the note about a place that is gone', () => {
    // At 1024 by 768 the note sits at right 16 and bottom 76, 320 wide.
    const NOTE = { left: 688, top: 566, width: 320, height: 126 };

    function gone(html = '<a id="under" href="#u">Document 14</a><a id="beside" href="#b">Home</a>') {
      pageText(html);
      const mounted = mount();
      const lost = mounted.root.querySelector<HTMLElement>('.lost')!;
      const boxes = new Map<Element, Box>([[lost, NOTE]]);
      mounted.platform.box.mockImplementation((element) => {
        if (element !== mounted.trigger) return boxes.get(element) ?? { left: 0, top: 0, width: 0, height: 0 };
        // The button where its style puts it: up from its corner when lifted.
        const bottom = mounted.trigger.style.bottom === '' ? 16 : parseInt(mounted.trigger.style.bottom, 10);
        return { left: 888, top: 768 - bottom - 44, width: 120, height: 44 };
      });
      mounted.widget.highlightWhenLoaded(GONE);
      expect(lost.hidden).toBe(false);
      return { ...mounted, lost, boxes };
    }

    it('goes once keyboard focus lands on a page element under it, having been said already', () => {
      const { lost, boxes } = gone();
      const under = document.getElementById('under')!;
      boxes.set(under, { left: 700, top: 600, width: 105, height: 21 });
      under.focus();
      expect(lost.hidden).toBe(true);
    });

    it('stays for focus beside it, for focus from the mouse, and for focus on its own button', () => {
      const { lost, boxes, platform, find } = gone();
      const [under, beside] = [document.getElementById('under')!, document.getElementById('beside')!];
      boxes.set(under, { left: 700, top: 600, width: 105, height: 21 });
      boxes.set(beside, { left: 10, top: 10, width: 60, height: 20 });
      beside.focus();
      expect(lost.hidden).toBe(false);
      find('lost.close')!.focus();
      expect(lost.hidden).toBe(false);
      platform.keyboardFocus.mockReturnValue(false);
      under.focus();
      expect(lost.hidden).toBe(false);
    });

    it('goes when the page scrolls the element with keyboard focus under it', () => {
      const { lost, boxes } = gone();
      const beside = document.getElementById('beside')!;
      boxes.set(beside, { left: 10, top: 10, width: 60, height: 20 });
      beside.focus();
      boxes.set(beside, { left: 700, top: 690, width: 60, height: 20 });
      document.dispatchEvent(new Event('scroll'));
      expect(lost.hidden).toBe(true);
    });

    it('goes when the button moves up into it for an element across the bottom', () => {
      const { lost, boxes, trigger } = gone();
      const under = document.getElementById('under')!;
      // Clear of the note itself, but the button goes up above it, to 76 px.
      boxes.set(under, { left: 0, top: 700, width: 1024, height: 30 });
      under.focus();
      expect(trigger.style.bottom).toBe('76px');
      expect(lost.hidden).toBe(true);
    });

    it('closes with Escape pressed on the page, leaving the key to the page', () => {
      const { lost } = gone();
      const beside = document.getElementById('beside')!;
      beside.focus();
      const key = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
      beside.dispatchEvent(key);
      expect(lost.hidden).toBe(true);
      expect(key.defaultPrevented).toBe(false);
    });

    it('fits a short window, scrolling inside itself', () => {
      expect(STYLES).toMatch(/\.lost \{[^}]*max-height: calc\(100vh - 92px\);[^}]*overflow-y: auto;/);
      expect(STYLES).toContain('.lost { max-height: calc(100dvh - 92px); }');
    });
  });
});

describe('following the layout', () => {
  const POINT = { k: 'point' as const, s: '#a', x: 0.5, y: 0.5, px: null, py: null, q: null };
  const QUOTE = { k: 'text' as const, s: null, x: null, y: null, px: null, py: null, q: 'de knop' };

  function placed(highlight: typeof POINT | typeof QUOTE) {
    pageText('<p id="a">Hier staat de knop.</p>');
    const mounted = mount();
    let target: Box = { left: 10, top: 20, width: 100, height: 50 };
    mounted.platform.box.mockImplementation((element) =>
      element === mounted.widget.host ? { left: 0, top: 0, width: 0, height: 0 } : target,
    );
    mounted.widget.highlightWhenLoaded(highlight);
    const relayout = () => mounted.platform.observeLayout.mock.calls[0]![0]();
    const stop = mounted.platform.observeLayout.mock.results[0]!.value as ReturnType<typeof vi.fn>;
    return { ...mounted, relayout, stop, move: (box: Box) => (target = box) };
  }

  it('puts the place from a link back on its element after a resize or a zoom, keeping its pin', () => {
    const { root, platform, relayout, move } = placed(POINT);
    expect(platform.observeLayout).toHaveBeenCalledOnce();
    const pin = root.querySelector<HTMLElement>('.pin-highlight')!;
    expect([pin.style.left, pin.style.top]).toEqual(['60px', '45px']);
    move({ left: 300, top: 400, width: 100, height: 50 });
    relayout();
    // The same element, so its pulse does not start over, and no scrolling.
    expect(root.querySelector('.pin-highlight')).toBe(pin);
    expect([pin.style.left, pin.style.top]).toEqual(['350px', '425px']);
    expect(platform.scrollToPage).toHaveBeenCalledOnce();
  });

  it('draws the marks of a quote anew when it wraps differently, under the pin', () => {
    const { root, platform, relayout } = placed(QUOTE);
    expect(root.querySelectorAll('.mark').length).toBe(1);
    platform.rangeBoxes.mockReturnValue([
      { left: 500, top: 20, width: 80, height: 18 },
      { left: 10, top: 40, width: 40, height: 18 },
    ]);
    relayout();
    const marks = Array.from(root.querySelectorAll<HTMLElement>('.mark'));
    expect(marks.map((mark) => [mark.style.left, mark.style.top, mark.style.width])).toEqual([
      ['500px', '20px', '80px'],
      ['10px', '40px', '40px'],
    ]);
    expect(root.querySelector('.pins')!.lastElementChild).toBe(root.querySelector('.pin-highlight'));
  });

  it('takes the place away once it is gone, and stops following the layout', () => {
    const { root, relayout, stop } = placed(QUOTE);
    document.querySelector('#content p')!.textContent = 'De tekst is anders.';
    relayout();
    expect(root.querySelector('[data-highlight]')).toBeNull();
    expect(stop).toHaveBeenCalledOnce();
  });

  it('stops following the layout when the panel opens and takes the place away', () => {
    const { trigger, stop, platform } = placed(POINT);
    trigger.click();
    expect(stop).toHaveBeenCalledOnce();
    trigger.click();
    trigger.click();
    expect(stop).toHaveBeenCalledOnce();
    expect(platform.observeLayout).toHaveBeenCalledOnce();
  });

  it('does not follow the layout for a place it could not find', () => {
    const { widget, platform } = mount();
    widget.highlightWhenLoaded(QUOTE);
    expect(platform.observeLayout).not.toHaveBeenCalled();
  });
});

describe('a place from a link, as the page scrolls', () => {
  const POINT = { k: 'point' as const, s: '#a', x: 0.5, y: 0.5, px: null, py: null, q: null };
  const QUOTE = { k: 'text' as const, s: null, x: null, y: null, px: null, py: null, q: 'de knop' };
  // An app shell in a window of 1024 by 768: the page does not scroll, its
  // main does, below a header of 100 px.
  const PANE: Box = { left: 0, top: 100, width: 1024, height: 668 };

  function shell(highlight: typeof POINT | typeof QUOTE, clip: Box | null = PANE) {
    pageText('<p id="a">Hier staat <b>de knop</b>.</p>');
    const mounted = mount();
    let target: Box = { left: 10, top: 1347, width: 100, height: 50 };
    mounted.platform.box.mockImplementation((element) =>
      element === mounted.widget.host ? { left: 0, top: 0, width: 0, height: 0 } : target,
    );
    // A passage whose text is gone shows nothing, as in a browser.
    mounted.platform.rangeBoxes.mockImplementation((range) =>
      range.collapsed ? [] : [{ left: target.left, top: target.top, width: 120, height: 18 }],
    );
    mounted.platform.clipBox.mockReturnValue(clip);
    mounted.platform.visibleText.mockReturnValue('Hier staat de knop.');
    mounted.widget.highlightWhenLoaded(highlight);
    const scroll = (box: Box) => {
      target = box;
      document.dispatchEvent(new Event('scroll'));
    };
    const pin = mounted.root.querySelector<HTMLElement>('.pin-highlight')!;
    return { ...mounted, scroll, pin };
  }

  it('brings a quote into view through what scrolls around the text its passage starts in', () => {
    // The element that holds that text moves it when it scrolls itself, as a
    // code listing that scrolls on its own does.
    const { platform } = shell(QUOTE);
    expect(platform.scrollers).toHaveBeenCalledWith(document.querySelector('#content b')!.firstChild);
  });

  it('scrolls the window to a place from page coordinates, or on the page as a whole', () => {
    pageText('<p>x</p>');
    const { widget, platform } = mount();
    platform.box.mockReturnValue({ left: 10, top: 20, width: 100, height: 50 });
    widget.highlightWhenLoaded({ ...POINT, s: null, x: null, y: null, px: 70, py: 80 });
    widget.highlightWhenLoaded({ ...POINT, s: 'body' });
    widget.highlightWhenLoaded({ ...POINT, s: 'html' });
    expect(platform.scrollToPage.mock.calls).toEqual([
      [70, 80],
      [60, 45],
      [60, 45],
    ]);
    // Page coordinates lie in no part of the page.
    expect(platform.scrollers.mock.calls).toEqual([[document.body], [document.documentElement]]);
    expect(platform.scrollElementTo).not.toHaveBeenCalled();
  });

  it('puts the place in the middle of each part that scrolls around it, then the window where it will be', () => {
    pageText('<p id="a">Hier staat de knop.</p>');
    const { widget, platform } = mount();
    // The window was scrolled when the link was followed.
    platform.viewport.mockReturnValue({ ...platform.viewport(), scrollX: 30, scrollY: 200 });
    platform.box.mockImplementation((element) =>
      element === widget.host ? { left: 0, top: 0, width: 0, height: 0 } : { left: 10, top: 1347, width: 100, height: 50 },
    );
    const pane = document.getElementById('content')!;
    const frame = document.createElement('section');
    platform.scrollers.mockReturnValue([
      { element: pane, box: PANE, scroll: { x: 0, y: 500 }, min: { x: 0, y: 0 }, max: { x: 0, y: 3000 } },
      // Around the pane, a part with room to scroll 30 px only.
      {
        element: frame,
        box: { left: 0, top: 0, width: 1024, height: 768 },
        scroll: { x: 0, y: 0 },
        min: { x: 0, y: 0 },
        max: { x: 0, y: 30 },
      },
    ]);
    widget.highlightWhenLoaded(POINT);
    // The point is at 60, 1372 in the window: the pane puts it at the middle
    // of its box, 434, and the part around it takes it up by 30 more.
    expect(platform.scrollElementTo.mock.calls).toEqual([
      [pane, 0, 1438],
      [frame, 0, 30],
    ]);
    expect(platform.scrollToPage.mock.calls).toEqual([[90, 604]]);
  });

  it('shows the place once the scroll brings it in sight, and says it then, once', () => {
    const { scroll, pin, said } = shell(POINT);
    // Below the fold of the pane: out of sight, and not said.
    expect(pin.hidden).toBe(true);
    expect(said()).toEqual([]);
    // Still below it, and out of the window too.
    scroll({ left: 10, top: 760, width: 100, height: 50 });
    expect([pin.hidden, said()]).toEqual([true, []]);
    // In the pane, and the pin with it.
    scroll({ left: 10, top: 400, width: 100, height: 50 });
    expect(pin.hidden).toBe(false);
    expect([pin.style.left, pin.style.top]).toEqual(['60px', '425px']);
    expect(said()).toEqual(['Gemarkeerd: Hier staat de knop.']);
    scroll({ left: 10, top: 300, width: 100, height: 50 });
    expect(pin.style.top).toBe('325px');
    expect(said()).toEqual(['Gemarkeerd: Hier staat de knop.']);
    // Scrolled on, out of the pane again.
    scroll({ left: 10, top: 40, width: 100, height: 50 });
    expect(pin.hidden).toBe(true);
  });

  it('says it once the window has scrolled to it, on a page that scrolls as a whole', () => {
    const { platform, scroll, said } = shell(POINT, null);
    expect(said()).toEqual([]);
    platform.viewport.mockReturnValue({ ...platform.viewport(), scrollY: 1100 });
    scroll({ left: 10, top: 247, width: 100, height: 50 });
    expect(said()).toEqual(['Gemarkeerd: Hier staat de knop.']);
  });

  it('marks only the lines of a quote the pane shows, and hides the pin at its start above them', () => {
    const { root, platform, scroll, pin } = shell(QUOTE);
    platform.rangeBoxes.mockReturnValue([
      { left: 10, top: 40, width: 120, height: 18 },
      { left: 10, top: 90, width: 300, height: 18 },
      { left: 10, top: 110, width: 200, height: 18 },
    ]);
    scroll({ left: 10, top: 40, width: 100, height: 50 });
    const marks = Array.from(root.querySelectorAll<HTMLElement>('.mark'), (mark) => [
      mark.style.top,
      mark.style.height,
      mark.style.width,
    ]);
    expect(marks).toEqual([
      ['100px', '8px', '300px'],
      ['110px', '18px', '200px'],
    ]);
    expect(pin.hidden).toBe(true);
  });

  it('shows and says a quote whose first box reaches a pixel above the pane its line sits flush in, the pin at its corner', () => {
    // A heading with a tight line height at the top of a box that clips:
    // the font's box reaches above the line, and the line lies in the pane.
    const { scroll, pin, said } = shell(QUOTE);
    expect([pin.hidden, said()]).toEqual([true, []]);
    scroll({ left: 10, top: 99, width: 100, height: 50 });
    expect(pin.hidden).toBe(false);
    expect([pin.style.left, pin.style.top]).toEqual(['10px', '99px']);
    expect(said()).toEqual(['Gemarkeerd: de knop']);
  });

  it('says a quote at the top of the page whose first box reaches above the window', () => {
    const { scroll, said } = shell(QUOTE, null);
    expect(said()).toEqual([]);
    scroll({ left: 10, top: -2, width: 100, height: 50 });
    expect(said()).toEqual(['Gemarkeerd: de knop']);
  });

  it('follows a scroll once per frame, measuring the place where it was found, while it shows', () => {
    const { platform, scroll, pin, trigger } = shell(QUOTE);
    const frames: Array<() => void> = [];
    platform.nextFrame.mockImplementation((callback) => void frames.push(callback));
    const read = vi.spyOn(document, 'createTreeWalker');
    scroll({ left: 10, top: 400, width: 100, height: 50 });
    scroll({ left: 10, top: 380, width: 100, height: 50 });
    expect(frames).toHaveLength(1);
    read.mockClear();
    frames.shift()!();
    expect(pin.style.top).toBe('380px');
    // The passage was not searched for again: that reads all the text of
    // the page. (jsdom walks the tree for a selector too, not for text.)
    const textRead = () => read.mock.calls.filter(([, show]) => show === NodeFilter.SHOW_TEXT);
    expect(textRead()).toEqual([]);
    // A change in the layout does search for it.
    platform.observeLayout.mock.calls[0]![0]();
    expect(textRead()).toHaveLength(1);
    scroll({ left: 10, top: 360, width: 100, height: 50 });
    expect(frames).toHaveLength(1);
    // The panel opens and takes the place away: a frame asked for before
    // changes nothing, and a scroll asks for none.
    trigger.click();
    frames.shift()!();
    scroll({ left: 10, top: 340, width: 100, height: 50 });
    expect(frames).toHaveLength(0);
    expect(pin.isConnected).toBe(false);
  });

  it('hears a pane that scrolls inside a web component of the page, whose scrolls never reach the document', () => {
    pageText('<page-frame id="frame"><p id="a">Hier staat de knop.</p></page-frame>');
    const mounted = mount();
    let target: Box = { left: 10, top: 1347, width: 100, height: 50 };
    mounted.platform.box.mockImplementation((element) =>
      element === mounted.widget.host ? { left: 0, top: 0, width: 0, height: 0 } : target,
    );
    mounted.platform.visibleText.mockReturnValue('Hier staat de knop.');
    const listen = vi.spyOn(ShadowRoot.prototype, 'addEventListener');
    mounted.widget.highlightWhenLoaded(POINT);
    const pin = mounted.root.querySelector<HTMLElement>('.pin-highlight')!;
    const frame = document.getElementById('frame')!;
    const pane = frame.shadowRoot!.querySelector('page-pane')!;
    const scroller = pane.shadowRoot!.querySelector('.scroll')!;
    const heard = vi.fn();
    document.addEventListener('scroll', heard, true);
    target = { left: 10, top: 300, width: 100, height: 50 };
    // As a browser sends it: neither composed nor bubbling.
    scroller.dispatchEvent(new Event('scroll'));
    document.removeEventListener('scroll', heard, true);
    expect(heard).not.toHaveBeenCalled();
    expect(pin.style.top).toBe('325px');
    expect(mounted.said()).toEqual(['Gemarkeerd: Hier staat de knop.']);
    // One listener in each root on the way, however often the place is shown.
    scroller.dispatchEvent(new Event('scroll'));
    mounted.platform.observeLayout.mock.calls[0]![0]();
    const roots = listen.mock.contexts.filter((_root, call) => listen.mock.calls[call]![0] === 'scroll');
    expect(roots).toEqual([frame.shadowRoot, pane.shadowRoot]);
  });

  it('asks for no frame on a scroll while the pin layer shows nothing', () => {
    const { platform } = mount();
    document.dispatchEvent(new Event('scroll'));
    expect(platform.nextFrame).not.toHaveBeenCalled();
  });

  it('searches for the place again after a scroll when what it was found by is gone', () => {
    const { scroll, root } = shell(QUOTE);
    document.querySelector('#content p')!.textContent = 'De tekst is anders.';
    scroll({ left: 10, top: 400, width: 100, height: 50 });
    expect(root.querySelector('[data-highlight]')).toBeNull();
  });
});

describe('the pins of Mijn feedback and what the widget draws over them', () => {
  const ITEM = {
    kind: 'point',
    status: 'new',
    comment: 'Deze knop is te klein',
    quote: null,
    page: { path: 'docs/', title: 'Docs', url: '/team/site/docs/' },
    createdAt: '2026-11-03T13:30:00Z',
    replies: [],
  };
  const ITEMS = [
    { ...ITEM, id: 'left', anchor: { k: 'point', s: '#left', x: 0.5, y: 0.5 } },
    { ...ITEM, id: 'right', anchor: { k: 'point', s: '#right', x: 0.5, y: 0.5 } },
  ];

  async function pinned(width = 1024, right: Box = { left: 690, top: 290, width: 20, height: 20 }) {
    pageText('<p id="left">Links</p><p id="right">Rechts</p>');
    const mounted = mount({ identity: 'account', account: true });
    mounted.platform.fetch.mockImplementation(async () => json(200, { items: ITEMS }));
    mounted.platform.viewport.mockReturnValue({ ...mounted.platform.viewport(), width });
    const boxes = new Map<Element, Box>([
      [mounted.widget.host, { left: 0, top: 0, width: 0, height: 0 }],
      [document.getElementById('left')!, { left: 90, top: 290, width: 20, height: 20 }],
      [document.getElementById('right')!, right],
      [mounted.trigger, { left: 888, top: 708, width: 120, height: 44 }],
    ]);
    mounted.platform.box.mockImplementation((element) => {
      // The panel where its style puts it: in its corner of a wide window,
      // over the right one, or in the bottom left corner.
      if (element === mounted.panel && width >= 600) {
        return { left: mounted.panel.style.left === '16px' ? 16 : 624, top: 24, width: 384, height: 668 };
      }
      if (element === mounted.panel) return { left: 0, top: 400, width, height: 400 };
      const known = boxes.get(element);
      if (known !== undefined) return known;
      // A pin where its style puts it, 36 px across.
      const pin = element as HTMLElement;
      return { left: parseInt(pin.style.left, 10) - 18, top: parseInt(pin.style.top, 10) - 18, width: 36, height: 36 };
    });
    mounted.trigger.click();
    mounted.press('menu.mine');
    await vi.waitFor(() => expect(mounted.root.querySelectorAll('.pins .pin').length).toBe(2));
    const pin = (id: string) => mounted.root.querySelector<HTMLElement>(`[data-pin="${id}"]`)!;
    return { ...mounted, boxes, pin };
  }

  it('lie under the button and the panel, which are drawn above them', () => {
    // A positioned element without a z-index lies under every one with one.
    expect(zIndexOf('pin')).toBeNull();
    for (const above of ['trigger', 'panel', 'lost', 'floating']) expect(zIndexOf(above)).toBeGreaterThan(0);
  });

  it('are no stop for Tab while the panel lies over them, and are once it moves clear', async () => {
    const { pin, boxes, panel } = await pinned();
    expect([pin('left').tabIndex, pin('right').tabIndex]).toEqual([0, -1]);
    // Keyboard focus on a page link under the panel sends it to the left.
    const side = document.createElement('a');
    side.href = '#s';
    side.textContent = 'Document 11';
    document.body.prepend(side);
    boxes.set(side, { left: 700, top: 231, width: 105, height: 21 });
    side.focus();
    expect(panel.style.left).toBe('16px');
    expect([pin('left').tabIndex, pin('right').tabIndex]).toEqual([-1, 0]);
  });

  it('are no stop for Tab under the button either', async () => {
    const { pin } = await pinned(1024, { left: 890, top: 710, width: 20, height: 20 });
    expect([pin('left').tabIndex, pin('right').tabIndex]).toEqual([0, -1]);
  });

  it('lie under the sheet of a narrow window', async () => {
    const { pin } = await pinned(400, { left: 290, top: 590, width: 20, height: 20 });
    // The left one above the sheet, the right one under it.
    expect([pin('left').tabIndex, pin('right').tabIndex]).toEqual([0, -1]);
  });

  it('are measured again where they were found as the page scrolls, not searched for', async () => {
    const { pin, boxes } = await pinned();
    expect(pin('left').style.left).toBe('100px');
    // A search would find this one first.
    const decoy = document.createElement('p');
    decoy.id = 'left';
    document.body.prepend(decoy);
    boxes.set(decoy, { left: 490, top: 490, width: 20, height: 20 });
    boxes.set(document.querySelectorAll('#left')[1]!, { left: 190, top: 290, width: 20, height: 20 });
    document.dispatchEvent(new Event('scroll'));
    expect(pin('left').style.left).toBe('200px');
  });

  it('hide while a part of the page that scrolls holds their place out of sight, handing focus to the item', async () => {
    const { pin, platform, root, focused } = await pinned();
    pin('left').focus();
    // A pane at the right shows the right one, and no longer the left one.
    platform.clipBox.mockReturnValue({ left: 400, top: 0, width: 624, height: 768 });
    document.dispatchEvent(new Event('scroll'));
    expect([pin('left').hidden, pin('right').hidden]).toEqual([true, false]);
    expect(focused()).toBe(root.querySelector('[data-item="left"]'));
    // Scrolled back into sight.
    platform.clipBox.mockReturnValue(null);
    document.dispatchEvent(new Event('scroll'));
    expect([pin('left').hidden, pin('right').hidden]).toEqual([false, false]);
  });

  it('show the pin of a quote whose first box reaches a pixel above the pane its line sits flush in', async () => {
    pageText('<p id="right">Rechts</p>');
    const mounted = mount({ identity: 'account', account: true });
    const item = { ...ITEM, id: 'quote', kind: 'text', quote: 'Rechts', anchor: { k: 'text', s: '#right', q: 'Rechts' } };
    mounted.platform.fetch.mockImplementation(async () => json(200, { items: [item] }));
    mounted.platform.rangeBoxes.mockReturnValue([{ left: 90, top: 99, width: 60, height: 36 }]);
    // A pane from 100 down.
    mounted.platform.clipBox.mockReturnValue({ left: 0, top: 100, width: 1024, height: 668 });
    mounted.trigger.click();
    mounted.press('menu.mine');
    await vi.waitFor(() => expect(mounted.root.querySelectorAll('.pins .pin').length).toBe(1));
    expect(mounted.root.querySelector<HTMLElement>('[data-pin="quote"]')!.hidden).toBe(false);
    // A box that reaches above the pane by more than half its height is out of sight.
    mounted.platform.rangeBoxes.mockReturnValue([{ left: 90, top: 81, width: 60, height: 36 }]);
    document.dispatchEvent(new Event('scroll'));
    expect(mounted.root.querySelector<HTMLElement>('[data-pin="quote"]')!.hidden).toBe(true);
  });
});

describe('samePage', () => {
  it('compares the page of an item with the page on screen', () => {
    expect(samePage(null, '/a/')).toBe(false);
    expect(samePage('/a/b/', '/a/b/')).toBe(true);
    expect(samePage('/a/caf%C3%A9/', '/a/café/')).toBe(true);
    expect(samePage('/a/b/', '/a/c/')).toBe(false);
    expect(samePage('/a/%E0%A4%A/', '/a/')).toBe(false);
  });
});

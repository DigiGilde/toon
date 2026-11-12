/**
 * The widget: one host element at the end of `body` with an open shadow root,
 * holding the button, the panel, the overlays and the pins. It moves through
 * the steps of state.ts, puts focus where focus.ts says, and talks to the
 * widget API through api.ts. Nothing here touches the page beyond reading it:
 * no storage, no cookies, no global styles, no custom elements, and key and
 * input events that start inside the widget stop at the host so a site's own
 * shortcuts do not eat what someone types.
 *
 * Focus stays where the screen shows it: an overlay keeps the keyboard until
 * it closes, Tab goes round inside the bottom sheet, and the button and the
 * open panel move out of the way of a page element that has keyboard focus
 * beneath them, also inside the page's own web components. Escape pressed on
 * the page closes the panel and the note beside the button.
 */
import {
  describeElement,
  describeText,
  foundElement,
  foundNode,
  locate,
  type Located,
  pageElement,
  pointAnchor,
  query,
  seenAt,
  selectorFor,
  selectsText,
  textAnchor,
} from './anchor';
import { type Api, createApi, describeFailure, type MineItem, type MineList, type NewItem } from './api';
import { Composer } from './composer';
import { button, h, type Message, replace, speechBubbleIcon, tabbable, withWords } from './dom';
import { FocusPlan } from './focus';
import { otherLanguage, pageLanguage, type Translate, translator } from './i18n';
import { MineView } from './mine';
import { frameOverlay, type Overlay, placingOverlay } from './overlay';
import {
  centreThrough,
  covers,
  inside,
  intersection,
  overlaps,
  panelSide,
  TRIGGER_MARGIN,
  triggerPlace,
} from './rect';
import { Shot } from './screenshot';
import { type Move, next, showsOverlay, showsPanel, type State } from './state';
import { STYLES } from './styles';
import { cleanPageText, cut, firstWords, MAX_TITLE } from './text';
import type { Anchor, Box, Config, Highlight, Kind, Lang, Mark, PageWords, Platform, Point } from './types';
import { hideConfirmView, hidingView, menuView, sentView, signInOnly } from './views';

const ISOLATED_EVENTS = ['keydown', 'keyup', 'keypress', 'input', 'beforeinput'];
const COMPOSING: readonly State[] = ['composing', 'cropping', 'covering', 'sending'];

/** "Een minuut": the button comes back on this page after this long. */
export const HIDE_FOR_MS = 60_000;
/** Below this width the panel is a bottom sheet. */
export const NARROW_WIDTH = 600;
/** The floating text button sits this far below a selection; further on touch,
 * clear of the browser's own selection menu. */
const FLOAT_GAP = 8;
const FLOAT_GAP_TOUCH = 48;
const FLOAT_WIDTH = 240;
const ANNOUNCED_LINES = 3;

interface Subject {
  kind: Kind;
  anchor: Anchor | null;
  quote: string | null;
  range: Range | null;
}

export interface WidgetOptions {
  config: Config;
  lang: Lang;
  platform: Platform;
}

/** Whether an item's page is the page on screen. */
export function samePage(url: string | null, pathname: string): boolean {
  if (url === null) return false;
  if (url === pathname) return true;
  try {
    return decodeURI(url) === decodeURI(pathname);
  } catch {
    return false;
  }
}

export class Widget {
  readonly host: HTMLDivElement;
  readonly root: ShadowRoot;
  state: State = 'idle';
  private readonly t: Translate;
  private readonly config: Config;
  private readonly lang: Lang;
  private readonly platform: Platform;
  private readonly api: Api;
  private readonly focusPlan = new FocusPlan();
  private readonly trigger: HTMLButtonElement;
  private readonly panel: HTMLElement;
  private readonly heading: HTMLElement;
  private readonly body: HTMLElement;
  private readonly status: HTMLElement;
  private readonly layer: HTMLElement;
  private readonly pins: HTMLElement;
  private readonly floating: HTMLButtonElement;
  /** The line under "Feedback op geselecteerde tekst" in the menu. */
  private readonly textHint: HTMLElement;
  /** Beside the button: the place a link points at is not on this page. */
  private readonly lostNote: HTMLElement;
  private overlay: Overlay | null = null;
  private composer: Composer | null = null;
  private shot: Shot | null = null;
  private subject: Subject | null = null;
  private mine: MineView | null = null;
  private draft = { comment: '', name: '' };
  /** Replies typed under Mijn feedback and not sent, per item id. */
  private readonly replyDrafts = new Map<string, string>();
  /** The reply field of each item in the list drawn last, per item id. */
  private readonly replyFields = new Map<string, HTMLTextAreaElement>();
  /** The page element that has keyboard focus, which the button and the
   * panel keep clear of: the innermost one, inside the page's own web
   * components too. */
  private pageFocus: Element | null = null;
  /** The page element that had focus last, from the keyboard or not, kept
   * while focus is in the widget and dropped when focus goes nowhere. */
  private lastPageFocus: Element | null = null;
  /** The page element that had focus when the widget opened: "Een minuut"
   * hides the button with the panel, and focus goes back there. */
  private returnFocus: Element | null = null;
  /** The open shadow roots of the page that focus went into, each with a
   * listener of its own. */
  private readonly pageRoots = new WeakSet<ShadowRoot>();
  /** The open shadow roots of the page that a place shown lies in, each with
   * a listener for its scrolls. */
  private readonly scrollRoots = new WeakSet<ShadowRoot>();
  /** Focus is being sent back to an overlay: a page that sends it away again
   * at that moment wins, rather than the two going round for ever. */
  private refocusing = false;
  /** The widget was last worked with keys rather than a pointer. Not
   * `:focus-visible`: its buttons take no focus from a press, so a browser
   * shows a focus ring after a click as well. */
  private keyboardInUse = false;
  /** What the pin layer shows, to put back in place when the layout changes. */
  private highlighted: Highlight | null = null;
  private mineItems: MineItem[] | null = null;
  /** Where the pin layer last put the place from a link, and each pin of
   * Mijn feedback: a scroll measures them again there. */
  private highlightPlace: Located | null = null;
  private readonly pinPlaces = new Map<string, Located>();
  /** The place from a link is said once it is in view, which scrolling to it
   * can take a moment for. */
  private unannounced = false;
  /** A frame is asked for to follow a scroll. */
  private scrollFrame = false;
  private stopLayout: (() => void) | null = null;

  constructor(options: WidgetOptions) {
    const { config, lang, platform } = options;
    this.config = config;
    this.lang = lang;
    this.platform = platform;
    const t = (this.t = translator(lang));
    this.api = createApi({
      api: config.api,
      token: config.token,
      lang,
      fetch: (url, init) => platform.fetch(url, init),
      now: () => Date.now(),
    });
    this.host = h('div', { 'data-toon-feedback-host': true, lang });
    this.root = this.host.attachShadow({ mode: 'open' });
    this.trigger = h(
      'button',
      { type: 'button', class: 'trigger', 'aria-expanded': 'false', 'aria-controls': 'tf-panel', 'data-key': 'trigger' },
      [speechBubbleIcon(), t('widget.trigger')],
    );
    this.heading = h('h2', { id: 'tf-heading', class: 'heading', tabindex: '-1', 'data-key': 'heading' });
    this.body = h('div', {});
    this.panel = h('div', { id: 'tf-panel', class: 'panel', role: 'dialog', 'aria-labelledby': 'tf-heading', hidden: true }, [
      this.heading,
      this.body,
    ]);
    this.floating = button(t('menu.floating'), () => this.chooseText('heading'), {
      key: 'floating',
      className: 'floating',
    });
    this.floating.hidden = true;
    this.textHint = h('p', { id: 'tf-text-hint', class: 'hint' });
    this.lostNote = h('div', { class: 'lost', hidden: true }, [
      h('p', { id: 'tf-lost' }, [t('highlight.lost')]),
      button(t('menu.close'), () => this.closeLost(), { key: 'lost.close', describedBy: 'tf-lost' }),
    ]);
    this.layer = h('div', {});
    this.pins = h('div', { class: 'pins' });
    this.status = h('div', { class: 'vh', role: 'status' });
    this.root.append(
      h('style', {}, [STYLES]),
      h('div', { class: 'region', role: 'region', 'aria-label': t('widget.region') }, [
        this.trigger,
        this.lostNote,
        this.panel,
        this.floating,
        this.layer,
        this.pins,
        this.status,
      ]),
    );
    this.wire();
  }

  mount(): void {
    document.body.append(this.host);
    this.render();
  }

  private wire(): void {
    for (const type of ISOLATED_EVENTS) this.host.addEventListener(type, (event) => event.stopPropagation());
    // A click on a widget button keeps the page's text selection alive.
    this.root.addEventListener('mousedown', (event) => {
      if ((event.target as Element).closest('button')) event.preventDefault();
    });
    this.root.addEventListener('pointerdown', () => {
      this.keyboardInUse = false;
    });
    this.root.addEventListener('keydown', (event) => {
      this.keyboardInUse = true;
      const key = (event as KeyboardEvent).key;
      if (key === 'Tab') this.wrapTab(event as KeyboardEvent);
      if (key !== 'Escape') return;
      event.preventDefault();
      this.escape();
    });
    this.trigger.addEventListener('click', () => {
      if (this.state === 'idle') this.open();
      else this.move('close');
    });
    document.addEventListener('selectionchange', () => this.placeFloating());
    // Where focus is on the page is only read, to keep the button clear of it.
    // Focus from inside the widget reaches the document as the host.
    document.addEventListener('focusin', (event) => this.focusMoved(event.target), true);
    document.addEventListener(
      'focusout',
      (event) => {
        if ((event as FocusEvent).relatedTarget === null) this.focusMoved(null);
      },
      true,
    );
    // A scroll of the window or of any part of the page that scrolls on its
    // own: in the capture phase, as a scroll event does not bubble. A part
    // inside a web component of the page is heard in its shadow root.
    document.addEventListener('scroll', () => this.scrolled(), { capture: true, passive: true });
    // Keys pressed on the page are only read too: keys from inside the widget
    // stop at the host and never get here.
    document.addEventListener('keydown', (event) => this.pageKey(event as KeyboardEvent), { passive: true });
    this.platform.onViewportChange(() => this.layout());
  }

  // -- Moving between steps --------------------------------------------------

  /** `focus` false leaves focus where it is: on the page, for a move made
   * from there. */
  private move(move: Move, focus = true): boolean {
    const from = this.state;
    const to = next(from, move);
    if (to === null) return false;
    this.state = to;
    this.leave(from, to);
    this.render();
    if (focus) this.focusFirst(this.focusPlan.targets(from, move, to));
    return true;
  }

  private leave(from: State, to: State): void {
    this.overlay?.dispose();
    this.overlay = null;
    if (this.composer !== null && !COMPOSING.includes(to)) {
      this.draft = {
        comment: to === 'sent' ? '' : this.composer.comment.value,
        name: this.composer.name?.value ?? this.draft.name,
      };
      this.composer.dispose();
      this.composer = null;
      this.shot = null;
      this.subject = null;
    }
    if (from === 'mine') {
      this.mine = null;
      this.mineItems = null;
      this.pinPlaces.clear();
      this.highlighted = null;
      this.highlightPlace = null;
      this.unannounced = false;
      replace(this.pins, []);
      this.followLayout();
    }
  }

  private escape(): void {
    if (this.state === 'mine' && this.mine!.escape()) return;
    if (this.state === 'placing') this.announce(this.t('placing.cancelled'));
    if (this.state === 'cropping' || this.state === 'covering') this.announce(this.t('rect.cancelled'));
    this.move('back');
  }

  /** Escape pressed on the page closes what the widget shows over it: the
   * panel, keeping what was typed as Sluiten does, and the note beside the
   * button. Focus stays on the page, and so does the key: a key the page
   * took for itself, or one that ends a composition, changes nothing here. */
  private pageKey(event: KeyboardEvent): void {
    if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing) return;
    this.lostNote.hidden = true;
    if (showsPanel(this.state)) this.move('close', false);
  }

  private focusFirst(keys: string[]): void {
    for (const key of keys) {
      const element = this.root.querySelector<HTMLElement>(`[data-key="${CSS.escape(key)}"]`);
      if (element !== null) {
        element.focus();
        return;
      }
    }
  }

  /** Each message is a line of its own: a live region reads what is added, so
   * two messages in one tick are both heard, and a repeated one is heard
   * again. Only the last few lines stay. */
  private announce(message: Message): void {
    this.status.append(h('p', {}, typeof message === 'string' ? [message] : message));
    while (this.status.childElementCount > ANNOUNCED_LINES) this.status.firstElementChild!.remove();
  }

  // -- Keeping focus where it can be seen ---------------------------------------

  /** Focus moved to a page element, into the widget (the host, as seen from
   * the document) or nowhere. An overlay covers the page, so focus that goes
   * there goes back to the overlay. Only keyboard focus moves the button: a
   * click into a skip link's target, a whole `main`, would otherwise lift it
   * onto the site's own navigation. Whether focus is on the page is decided
   * on the target as the document sees it, before looking inside it: the
   * widget's own open root is no page. */
  private focusMoved(target: EventTarget | null): void {
    const onPage = target instanceof Element && target !== this.host ? target : null;
    if (onPage !== null && this.overlay !== null) {
      if (this.refocusing) return;
      this.refocusing = true;
      this.overlay.element.focus();
      this.refocusing = false;
      return;
    }
    const focused = onPage === null ? null : this.innermost(onPage);
    if (target !== this.host) this.lastPageFocus = focused;
    this.pageFocus = focused !== null && this.keyboardFocused(focused) ? focused : null;
    this.keepClear();
  }

  /** The element that has focus, inside the page's own web components too:
   * the document hears focus inside an open shadow root as its host. Each
   * root on the way gets a listener of its own, once, because a move between
   * two controls inside one root never reaches the document. */
  private innermost(element: Element): Element {
    let focused = element;
    for (let root = focused.shadowRoot; root?.activeElement; root = focused.shadowRoot) {
      if (!this.pageRoots.has(root)) {
        this.pageRoots.add(root);
        root.addEventListener('focusin', () => this.focusMoved(document.activeElement), true);
      }
      focused = root.activeElement;
    }
    return focused;
  }

  /** Focus the page shows as from the keyboard (`:focus-visible`). A custom
   * element whose shadow root cannot be read, a closed one, does not match
   * that itself while its inner control does: focus on it counts as keyboard
   * focus. */
  private keyboardFocused(element: Element): boolean {
    return (element.localName.includes('-') && element.shadowRoot === null) || this.platform.keyboardFocus(element);
  }

  /** Keep the widget off the page element that has keyboard focus (WCAG
   * 2.4.11): the button and the open panel move, and the note about a place
   * that is gone goes, as it has been said already. */
  private keepClear(): void {
    this.placeTrigger();
    this.placePanel();
    this.dropLostNote();
    this.reachPins();
  }

  /** Keep the button clear of the page element that has keyboard focus. The
   * button goes back to its corner first, where it is measured. */
  private placeTrigger(): void {
    const style = this.trigger.style;
    style.left = '';
    style.right = '';
    style.bottom = '';
    if (this.pageFocus === null) return;
    const place = triggerPlace(
      this.platform.box(this.pageFocus),
      this.platform.box(this.trigger),
      this.platform.viewport(),
    );
    if (place.side === 'left') {
      style.left = `${TRIGGER_MARGIN}px`;
      style.right = 'auto';
    }
    if (place.bottom !== TRIGGER_MARGIN) style.bottom = `${Math.round(place.bottom)}px`;
  }

  /** In a wide window the panel is not modal, and Tab leaves it for the page.
   * It goes to the bottom left corner while the page element with keyboard
   * focus lies under it in its own corner and is clear of it there, and is
   * placed anew only when keyboard focus lands on the page again: focus in
   * the panel, or nowhere, leaves it where it is, so a press in it never
   * moves it from under the pointer. It goes back to its corner first, where
   * it is measured, and when it closes. */
  private placePanel(): void {
    const style = this.panel.style;
    if (this.panel.hidden || this.platform.viewport().width < NARROW_WIDTH) {
      style.left = '';
      style.right = '';
      return;
    }
    if (this.pageFocus === null) return;
    style.left = '';
    style.right = '';
    if (panelSide(this.platform.box(this.pageFocus), this.platform.box(this.panel)) === 'left') {
      style.left = `${TRIGGER_MARGIN}px`;
      style.right = 'auto';
    }
  }

  /** The note about a place that is gone goes once it lies on the page
   * element with keyboard focus, or on the button that moved up for it. */
  private dropLostNote(): void {
    if (this.pageFocus === null || this.lostNote.hidden) return;
    const note = this.platform.box(this.lostNote);
    if (covers(note, this.platform.box(this.pageFocus)) || overlaps(note, this.platform.box(this.trigger))) {
      this.lostNote.hidden = true;
    }
  }

  /** Mijn feedback's pins lie under the button and the panel, which come
   * before them for Tab and are drawn over them: a pin under one of those is
   * out of sight, and no stop for Tab (WCAG 2.4.11). Its item is in the list.
   * A hidden button has no box, and covers nothing. */
  private reachPins(): void {
    const pins = Array.from(this.pins.querySelectorAll<HTMLElement>('[data-pin]'));
    if (pins.length === 0) return;
    const over = [this.trigger, this.panel].map((element) => this.platform.box(element));
    for (const pin of pins) {
      const box = this.platform.box(pin);
      pin.tabIndex = over.some((above) => overlaps(above, box)) ? -1 : 0;
    }
  }

  /** Tab and Shift+Tab go round inside the bottom sheet: what lies beyond it
   * is page that the sheet covers. */
  private wrapTab(event: KeyboardEvent): void {
    if (!showsPanel(this.state) || this.platform.viewport().width >= NARROW_WIDTH) return;
    const controls = tabbable(this.panel);
    const first = controls[0]!;
    const last = controls[controls.length - 1]!;
    const active = event.target as Element;
    const wrap = event.shiftKey
      ? !(active.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_PRECEDING)
      : !(active.compareDocumentPosition(last) & Node.DOCUMENT_POSITION_FOLLOWING);
    if (!wrap) return;
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  }

  // -- Drawing the current step ------------------------------------------------

  private render(): void {
    const panel = showsPanel(this.state);
    this.trigger.setAttribute('aria-expanded', String(panel));
    this.panel.hidden = !panel;
    if (this.state !== 'menu') this.floating.hidden = true;
    this.renderOverlay();
    if (panel) this.renderPanel();
    this.layout();
  }

  private renderOverlay(): void {
    if (!showsOverlay(this.state)) {
      replace(this.layer, []);
      return;
    }
    this.overlay = this.state === 'placing' ? this.placing() : this.frame(this.state === 'cropping' ? 'region' : 'cover');
    replace(this.layer, [this.overlay.element]);
  }

  private renderPanel(): void {
    const { t, config } = this;
    switch (this.state) {
      case 'menu':
        this.textHint.textContent = '';
        this.showPanel(
          t('menu.heading'),
          menuView(
            t,
            config,
            {
              point: () => this.startPoint(),
              text: () => this.chooseText('menu.text'),
              page: () => this.choosePage(),
              mine: () => this.openMine('menu.mine'),
              hide: () => this.startHiding(),
              close: () => this.move('close'),
            },
            this.textHint,
            this.platform.opaqueOrigin(),
          ),
        );
        break;
      case 'composing':
      case 'sending':
        this.showPanel(t('menu.heading'), this.ensureComposer().element);
        break;
      case 'sent':
        this.showPanel(
          t('sent.heading'),
          sentView(t, config, {
            mine: () => this.openMine('sent.mine'),
            more: () => this.move('more'),
            close: () => this.move('close'),
          }),
        );
        break;
      case 'mine':
        this.showPanel(t('mine.heading'), this.ensureMine().element);
        break;
      case 'hiding':
        this.showPanel(
          t('hide.heading'),
          hidingView(t, {
            minute: () => this.hideForAMinute(),
            session: () => {
              this.focusPlan.opened('hideConfirm', 'hide.session');
              this.move('session');
            },
            back: () => this.move('back'),
          }),
        );
        break;
      default:
        this.showPanel(
          t('hide.heading'),
          hideConfirmView(t, config, new URL(config.showUrl, location.href).href, {
            submit: (form) => this.platform.submitForm(form),
            back: () => this.move('back'),
          }),
        );
    }
  }

  private showPanel(heading: string, content: HTMLElement): void {
    this.heading.textContent = heading;
    if (this.body.firstChild !== content) replace(this.body, [content]);
  }

  /** Below the narrow width the panel is a sheet at the bottom, above an
   * on-screen keyboard. It would cover the button, so the button goes while
   * the sheet shows, and focus on it moves to the sheet's heading. */
  private layout(): void {
    const view = this.platform.viewport();
    const narrow = view.width < NARROW_WIDTH;
    this.trigger.hidden =
      showsOverlay(this.state) || this.state === 'hidden' || (showsPanel(this.state) && narrow);
    if (this.trigger.hidden && this.root.activeElement === this.trigger) this.heading.focus();
    this.overlay?.layout();
    if (narrow) {
      const keyboard = Math.max(0, view.height - view.visualHeight - view.visualOffsetTop);
      this.panel.style.bottom = `${Math.round(keyboard)}px`;
      this.panel.style.maxHeight = `${Math.round(view.visualHeight * 0.85)}px`;
    } else {
      this.panel.style.removeProperty('bottom');
      this.panel.style.removeProperty('max-height');
    }
    // Last, so the panel is measured where it now is.
    this.keepClear();
  }

  /** Place an element of the pin layer at page coordinates. */
  private position(element: HTMLElement, place: Point): void {
    const view = this.platform.viewport();
    const host = this.platform.box(this.host);
    element.style.left = `${Math.round(place.x - (host.left + view.scrollX))}px`;
    element.style.top = `${Math.round(place.y - (host.top + view.scrollY))}px`;
  }

  // -- The menu ------------------------------------------------------------------

  private open(): void {
    this.returnFocus = this.focusedOnPage();
    this.clearHighlight();
    this.focusPlan.opened('menu', 'trigger');
    this.move('open');
  }

  /** The page element that has focus: still on the page when the button is
   * pressed with a pointer, which takes no focus, or the last one before the
   * keyboard went into the widget. */
  private focusedOnPage(): Element | null {
    const active = document.activeElement;
    if (active === this.host) return this.lastPageFocus;
    return active === null || active === document.body ? null : this.innermost(active);
  }

  private startPoint(): void {
    this.focusPlan.opened('placing', 'menu.point');
    this.focusPlan.opened('composing', 'menu.point');
    this.move('point');
  }

  private chooseText(opener: string): void {
    const selection = document.getSelection();
    const range =
      selection !== null && selection.rangeCount > 0 && !selection.isCollapsed
        ? selection.getRangeAt(0).cloneRange()
        : null;
    const choice = range === null ? null : textAnchor(range, document, this.host);
    if (choice === null) {
      // Heard once from the status region, and seen under the button.
      this.announce(this.t('menu.noSelection'));
      this.textHint.textContent = this.t('menu.noSelection');
      return;
    }
    this.subject = { kind: 'text', anchor: choice.anchor, quote: choice.exact, range };
    this.focusPlan.opened('composing', opener);
    this.move('compose');
  }

  private choosePage(): void {
    this.subject = { kind: 'page', anchor: null, quote: null, range: null };
    this.focusPlan.opened('composing', 'menu.page');
    this.move('compose');
  }

  private startHiding(): void {
    this.focusPlan.opened('hiding', 'menu.hide');
    this.move('hide');
  }

  /** The panel goes with the button that had focus. Focus goes back to the
   * page element that had it when the widget opened, when that is still there
   * and takes focus, and else stays where it is. The widget gives no page
   * element a tabindex. */
  private hideForAMinute(): void {
    this.move('minute');
    this.announce(this.t('hide.minute.done'));
    // An element that had focus has focus() of its own, HTML, SVG or MathML,
    // which does nothing for one that is gone or takes no focus now. Like a
    // dialog that closes, it leaves the page where the visitor read on to.
    (this.returnFocus as HTMLElement | null)?.focus({ preventScroll: true });
    setTimeout(() => {
      this.move('show');
      this.announce(this.t('hide.back'));
    }, HIDE_FOR_MS);
  }

  /** "Feedback op deze tekst" next to a selection, while the menu is open,
   * unless that menu offers signing in only. */
  private placeFloating(): void {
    if (this.state !== 'menu' || signInOnly(this.config)) return;
    const selection = document.getSelection();
    const range =
      selection !== null && selection.rangeCount > 0 && !selection.isCollapsed ? selection.getRangeAt(0) : null;
    const boxes = range !== null && selectsText(range, document, this.host) ? this.platform.rangeBoxes(range) : [];
    const last = boxes[boxes.length - 1];
    if (last === undefined) {
      this.floating.hidden = true;
      return;
    }
    const view = this.platform.viewport();
    const gap = this.platform.coarsePointer() ? FLOAT_GAP_TOUCH : FLOAT_GAP;
    this.floating.style.left = `${Math.round(Math.max(8, Math.min(last.left, view.width - FLOAT_WIDTH)))}px`;
    this.floating.style.top = `${Math.round(Math.min(last.top + last.height + gap, view.height - 56))}px`;
    this.floating.hidden = false;
  }

  // -- Pointing ------------------------------------------------------------------

  private placing(): Overlay {
    const { t } = this;
    return placingOverlay({
      t,
      keyboard: this.keyboardInUse,
      bounds: () => {
        const view = this.platform.viewport();
        return { width: view.width, height: view.height };
      },
      box: (element) => this.platform.box(element),
      describe: (point) => {
        const element = this.platform.elementAt(point.x, point.y, this.host);
        return element === null ? { text: '', lang: null } : this.wordsOf(element);
      },
      announce: (message) => this.announce(message),
      place: (point) => this.place(point),
      stop: () => {
        this.announce(t('placing.cancelled'));
        this.move('back');
      },
    });
  }

  private place(point: Point): void {
    const element = pageElement(this.platform.elementAt(point.x, point.y, this.host), document, this.host);
    const selector = selectorFor(element, document);
    const target = query(document, selector, this.host)!;
    const view = this.platform.viewport();
    this.subject = {
      kind: 'point',
      anchor: pointAnchor(selector, this.platform.box(target), point, { x: view.scrollX, y: view.scrollY }),
      quote: null,
      range: null,
    };
    this.announce(this.t('placing.done'));
    this.move('placed');
  }

  // -- Composing and sending ---------------------------------------------------

  private ensureComposer(): Composer {
    if (this.composer !== null) return this.composer;
    const subject = this.subject!;
    const shot = new Shot(this.platform, this.host, () => this.composer!.renderShot(), this.config.screenshotLimit);
    this.shot = shot;
    this.composer = new Composer({
      t: this.t,
      config: this.config,
      kind: subject.kind,
      quote: subject.quote,
      quoteLang: subject.range === null ? null : this.languageOf(subject.range.startContainer),
      shot,
      draft: this.draft,
      opaque: this.platform.opaqueOrigin(),
      announce: (message) => this.announce(message),
      take: () => void shot.take(this.marks(subject)),
      send: () => void this.send(),
      cancel: () => this.move('back'),
      region: () => {
        this.focusPlan.opened('cropping', 'shot.region');
        this.move('region');
      },
      cover: () => {
        this.focusPlan.opened('covering', 'shot.cover');
        this.move('cover');
      },
      copy: (field) => this.copy(field),
      withoutScreenshot: () => {
        shot.exclude();
        void this.send();
      },
    });
    if (subject.kind !== 'page') void shot.take(this.marks(subject));
    return this.composer;
  }

  /** The pin or the selection, where the screenshot will show them. */
  private marks(subject: Subject): Mark[] {
    const view = this.platform.viewport();
    if (subject.kind === 'point') {
      const point = subject.anchor!.point!;
      return [{ type: 'pin', x: point.pageX - view.scrollX, y: point.pageY - view.scrollY }];
    }
    if (subject.kind === 'text') {
      const rects = this.platform
        .rangeBoxes(subject.range!)
        .map((box) => ({ x: box.left, y: box.top, width: box.width, height: box.height }));
      return [{ type: 'rects', rects }];
    }
    return [];
  }

  private frame(mode: 'region' | 'cover'): Overlay {
    const { t } = this;
    const shot = this.shot!;
    return frameOverlay({
      t,
      mode,
      keyboard: this.keyboardInUse,
      picture: shot.view(),
      bounds: shot.bounds!,
      box: (element) => this.platform.box(element),
      announce: (message) => this.announce(message),
      done: (rect) => {
        void (mode === 'region' ? shot.choose(rect) : shot.cover(rect));
        this.announce(t(mode === 'region' ? 'region.done' : 'cover.done'));
        this.move('done');
      },
      stop: () => {
        this.announce(t('rect.cancelled'));
        this.move('back');
      },
    });
  }

  /** Send the feedback. A screenshot still being made holds it back, with
   * the offer to send without. A cover or a part still being drawn is waited
   * for, so what goes is what the giver asked for, never the picture before. */
  private async send(): Promise<void> {
    if (this.state !== 'composing') return;
    const composer = this.composer!;
    const shot = this.shot!;
    const subject = this.subject!;
    const input = composer.validate();
    if (input === null) return;
    if (shot.state === 'capturing') {
      composer.shotProblem(this.t('error.shot.notReady'));
      return;
    }
    composer.showError(null, false);
    const view = this.platform.viewport();
    const redrawing = shot.rendering;
    this.move('send');
    composer.setBusy(true);
    this.announce(this.t('composer.sending'));
    await shot.settled();
    if (redrawing && shot.state !== 'ready') {
      composer.setBusy(false);
      composer.shotProblem(this.t('error.shot.changed'));
      this.move('failed');
      return;
    }
    const screenshot = shot.attachment;
    const item: NewItem = {
      kind: subject.kind,
      comment: input.comment,
      page: { path: location.pathname, title: cut(document.title, MAX_TITLE) },
      anchor: subject.anchor,
      viewport: { width: Math.round(view.width), height: Math.round(view.height), dpr: Math.min(view.dpr, 10) },
      author: input.author,
      screenshot: { region: screenshot === null ? null : screenshot.region },
    };
    try {
      await this.api.create(item, screenshot === null ? null : screenshot.file);
    } catch (failure) {
      composer.setBusy(false);
      composer.showError(describeFailure(failure, 'send', this.t), screenshot !== null);
      this.move('failed');
      return;
    }
    this.move('sent');
    this.announce(this.t('sent.heading'));
  }

  /** Copy a field, say how that went, and give the same words to show. */
  private async copy(field: HTMLTextAreaElement): Promise<string> {
    const copied = await this.platform.copyText(field);
    if (!copied) {
      field.focus();
      field.select();
    }
    const message = this.t(copied ? 'error.copied' : 'error.copyFailed');
    this.announce(message);
    return message;
  }

  // -- Mijn feedback -------------------------------------------------------------

  private openMine(opener: string): void {
    this.focusPlan.opened('mine', opener);
    this.move('mine');
    void this.loadMine();
  }

  private ensureMine(): MineView {
    this.mine ??= new MineView({
      t: this.t,
      lang: this.lang,
      drafts: this.replyDrafts,
      fields: this.replyFields,
      announce: (message) => this.announce(message),
      reply: (id, body) => this.api.reply(id, body),
      remove: (id) => this.api.remove(id),
      copy: (field) => this.copy(field),
      removed: (id) => this.removedItem(id),
      back: () => this.move('back'),
    });
    return this.mine;
  }

  /** An item is gone: its pin goes, and focus goes to the heading. A delete
   * that finishes after the giver left Mijn feedback changes nothing. */
  private removedItem(id: string): void {
    if (this.mineItems === null) return;
    this.mineItems = this.mineItems.filter((item) => item.id !== id);
    this.pinPlaces.delete(id);
    this.pins.querySelector(`[data-pin="${CSS.escape(id)}"]`)?.remove();
    this.focusFirst(['heading']);
  }

  private async loadMine(): Promise<void> {
    const mine = this.mine!;
    let list: MineList;
    try {
      list = await this.api.mine();
    } catch (failure) {
      if (this.mine === mine) mine.showError(describeFailure(failure, 'list', this.t).message);
      return;
    }
    if (this.mine !== mine) return;
    this.mineItems = list.items;
    mine.show(list.items, this.placePins(list.items), list.more);
    this.followLayout();
  }

  /** Pins for the items on this page: new where there were none, moved where
   * there were, hidden while a part of the page that scrolls on its own
   * holds their place out of sight. After a scroll each is measured again
   * where it was found. Returns the ids whose place is gone, whose pins go
   * too. */
  private placePins(items: MineItem[], scrolled = false): Set<string> {
    const lost = new Set<string>();
    for (const item of items) {
      if (item.anchor === null || !samePage(item.page.url, location.pathname)) continue;
      const previous = scrolled ? (this.pinPlaces.get(item.id) ?? null) : null;
      const place = locate(item.anchor, document, this.platform, this.host, previous);
      const pin = this.pins.querySelector<HTMLElement>(`[data-pin="${CSS.escape(item.id)}"]`);
      const shows = place !== null && this.shows(place);
      // A pin with focus hands it to its item in the list before it goes.
      if (!shows && pin !== null && pin === this.root.activeElement) this.mine!.focusItem(item.id);
      if (place === null) {
        lost.add(item.id);
        this.pinPlaces.delete(item.id);
        pin?.remove();
        continue;
      }
      this.pinPlaces.set(item.id, place);
      this.hearScrollsAround(foundElement(place));
      const placed = pin ?? this.addPin(item);
      this.position(placed, place);
      placed.hidden = !shows;
    }
    this.reachPins();
    return lost;
  }

  private addPin(item: MineItem): HTMLElement {
    const pin = h('button', {
      type: 'button',
      class: 'pin',
      'aria-label': this.t('mine.pin', { words: firstWords(item.comment) }),
      'data-pin': item.id,
    });
    pin.addEventListener('click', () => this.mine!.focusItem(item.id));
    this.pins.append(pin);
    return pin;
  }

  // -- A place from a link -------------------------------------------------------

  /** Show the place a #toon-feedback= link points at, once the page has its
   * final layout. */
  highlightWhenLoaded(highlight: Highlight): void {
    if (document.readyState === 'complete') this.highlight(highlight);
    else window.addEventListener('load', () => this.highlight(highlight), { once: true });
  }

  /** A place that is gone is said, and shown beside the button rather than in
   * the panel: anyone can make such a link, and a dialog that opens by itself
   * would take over the page. A place that is there is said once it is in
   * view. */
  private highlight(highlight: Highlight): void {
    this.clearHighlight();
    const place: Located | null = locate(highlight, document, this.platform, this.host);
    if (place === null) {
      this.lostNote.hidden = false;
      this.announce(this.t('highlight.lost'));
      return;
    }
    this.reveal(place);
    this.highlighted = highlight;
    this.unannounced = true;
    this.pins.append(
      h('div', {
        class: 'pin pin-highlight',
        role: 'img',
        'aria-label': this.t('highlight.label'),
        'data-highlight': true,
      }),
    );
    this.showHighlight(place);
    this.followLayout();
  }

  /** Bring the place itself into view, not the element it was found by,
   * which can be taller than the window or have no box at all: each part of
   * the page that scrolls on its own and holds it puts it in its middle, as
   * far as it can, and the window then scrolls to where it will be. */
  private reveal(place: Located): void {
    const view = this.platform.viewport();
    const node = foundNode(place);
    const scrollers = node === null ? [] : this.platform.scrollers(node);
    const centred = centreThrough({ x: place.x - view.scrollX, y: place.y - view.scrollY }, scrollers);
    for (const step of centred.steps) this.platform.scrollElementTo(step.element, step.to.x, step.to.y);
    this.platform.scrollToPage(centred.point.x + view.scrollX, centred.point.y + view.scrollY);
  }

  /** The part of the page, in page coordinates, in which a place can show:
   * what the parts of the page that scroll on their own leave of the window.
   * Null when nothing but the window clips it. */
  private clipOf(place: Located): Box | null {
    const element = foundElement(place);
    const clip = element === null ? null : this.platform.clipBox(element);
    if (clip === null) return null;
    const view = this.platform.viewport();
    return { left: clip.left + view.scrollX, top: clip.top + view.scrollY, width: clip.width, height: clip.height };
  }

  /** Whether a place shows where it is: no part of the page that scrolls on
   * its own holds it out of sight. */
  private shows(place: Located): boolean {
    const clip = this.clipOf(place);
    return clip === null || inside(seenAt(place), clip);
  }

  /** Say what is marked once the place is in the window and shows there. */
  private announceWhenInView(clip: Box | null): void {
    if (!this.unannounced) return;
    const place = this.highlightPlace!;
    const view = this.platform.viewport();
    const seen = { left: view.scrollX, top: view.scrollY, width: view.width, height: view.height };
    const area = clip === null ? seen : intersection(seen, clip);
    if (area === null || !inside(seenAt(place), area)) return;
    this.unannounced = false;
    const words = this.placeWords(place);
    this.announce(words.text === '' ? this.t('highlight.shown') : withWords(this.t('highlight.shownAt'), words));
  }

  /** The marked place in the page's own words, never the link's: anyone can
   * make a link, and its quote need not be on the page. The element a point
   * names as the crosshair describes it, the first words of the passage a
   * quote matched, or else what lies at the place in the window now. Nothing
   * for the page as a whole. */
  private placeWords(place: Located): PageWords {
    if (place.found instanceof Range) {
      const text = describeText(cleanPageText(place.found.toString()));
      return { text, lang: this.languageOf(place.found.startContainer) };
    }
    const view = this.platform.viewport();
    const element = place.found ?? this.platform.elementAt(place.x - view.scrollX, place.y - view.scrollY, this.host);
    if (element === null || element === document.body || !document.body.contains(element)) {
      return { text: '', lang: null };
    }
    return this.wordsOf(element);
  }

  /** An element in its own words, as the crosshair describes it. */
  private wordsOf(element: Element): PageWords {
    return { text: describeElement(element, this.platform.visibleText(element)), lang: this.languageOf(element) };
  }

  /** The language of a node of the page, where it is not the widget's. */
  private languageOf(node: Node): string | null {
    return otherLanguage(pageLanguage(node), this.lang);
  }

  /** Focus on the note's own button goes to the Feedback button, not nowhere. */
  private closeLost(): void {
    const hadFocus = this.lostNote.contains(this.root.activeElement);
    this.lostNote.hidden = true;
    if (hadFocus) this.trigger.focus();
  }

  /** The marks of the place, drawn anew because a quote can wrap otherwise,
   * and its pin moved there: the pin stays, so its pulse does not start over.
   * Only what the parts of the page that scroll on their own show of it is
   * drawn. */
  private showHighlight(place: Located): void {
    this.highlightPlace = place;
    this.hearScrollsAround(foundElement(place));
    const pin = this.pins.querySelector<HTMLElement>('.pin-highlight')!;
    for (const mark of Array.from(this.pins.querySelectorAll('.mark'))) mark.remove();
    const clip = this.clipOf(place);
    for (const box of place.boxes) {
      const shown = clip === null ? box : intersection(box, clip);
      if (shown === null) continue;
      const mark = h('div', { class: 'mark', 'data-highlight': true });
      this.position(mark, { x: shown.left, y: shown.top });
      mark.style.width = `${Math.round(shown.width)}px`;
      mark.style.height = `${Math.round(shown.height)}px`;
      this.pins.insertBefore(mark, pin);
    }
    this.position(pin, place);
    pin.hidden = clip !== null && !inside(seenAt(place), clip);
    this.announceWhenInView(clip);
  }

  private clearHighlight(): void {
    for (const mark of Array.from(this.pins.querySelectorAll('[data-highlight]'))) mark.remove();
    this.highlighted = null;
    this.highlightPlace = null;
    this.unannounced = false;
    this.lostNote.hidden = true;
    this.followLayout();
  }

  // -- Following the page's layout ------------------------------------------------

  /** Follow the layout while the pin layer shows something, and only then. */
  private followLayout(): void {
    const needed = this.highlighted !== null || this.mineItems !== null;
    if (needed && this.stopLayout === null) this.stopLayout = this.platform.observeLayout(() => this.relayout());
    if (!needed && this.stopLayout !== null) {
      this.stopLayout();
      this.stopLayout = null;
    }
  }

  private scrolled(): void {
    if (this.pageFocus !== null) this.keepClear();
    if (this.stopLayout !== null) this.followScroll();
  }

  /** A scroll inside the shadow root of one of the page's web components
   * never leaves it: each root a place lies in, through the slots it is
   * shown in too, gets a listener of its own, once. */
  private hearScrollsAround(element: Element | null): void {
    for (let node = element; node !== null; ) {
      const root = node.getRootNode();
      if (root instanceof ShadowRoot && !this.scrollRoots.has(root)) {
        this.scrollRoots.add(root);
        root.addEventListener('scroll', () => this.scrolled(), { capture: true, passive: true });
      }
      node = node.assignedSlot ?? node.parentElement ?? (root instanceof ShadowRoot ? root.host : null);
    }
  }

  /** Follow a scroll, once per frame at most, while the pin layer shows
   * something: its pins and marks lie on the page as a whole, and a part of
   * the page that scrolls on its own takes what they mark along. */
  private followScroll(): void {
    if (this.scrollFrame) return;
    this.scrollFrame = true;
    this.platform.nextFrame(() => {
      this.scrollFrame = false;
      this.relayout(true);
    });
  }

  /** The page's layout changed (a resize, a zoom, a rotation, content that
   * loaded late), or it scrolled: put the place from a link and the pins
   * back on what they point at, and take away what is no longer there. */
  private relayout(scrolled = false): void {
    if (this.highlighted !== null) {
      const previous = scrolled ? this.highlightPlace : null;
      const place = locate(this.highlighted, document, this.platform, this.host, previous);
      if (place === null) this.clearHighlight();
      else this.showHighlight(place);
    }
    if (this.mineItems !== null) this.mine!.markLost(this.placePins(this.mineItems, scrolled));
  }
}

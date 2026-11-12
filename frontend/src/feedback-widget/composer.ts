/**
 * The composer: what the remark is about, the text, the screenshot, who sends
 * it, and what happens to it. The notice under "Versturen" is there for every
 * author kind, and so is the screenshot's own warning: the giver sees the
 * picture before it can be sent. While the feedback is on its way the
 * screenshot does not change.
 */
import type { Author, ErrorView } from './api';
import { button, copyControl, h, privacyLink, replace } from './dom';
import type { Translate } from './i18n';
import type { Shot, ShotState } from './screenshot';
import { checkText, codePoints, cut, MAX_COMMENT, MAX_NAME } from './text';
import type { Config, Kind } from './types';

export interface ComposerOptions {
  t: Translate;
  config: Config;
  kind: Kind;
  quote: string | null;
  /** The quote's language where it is not the widget's. */
  quoteLang: string | null;
  shot: Shot;
  draft: { comment: string; name: string };
  /** The page has an opaque origin (a shielded site): a new tab would
   * inherit its sandbox, in which the privacy statement cannot load. */
  opaque: boolean;
  announce(message: string): void;
  /** Take the screenshot (again), with the marks of this remark. */
  take(): void;
  send(): void;
  cancel(): void;
  region(): void;
  cover(): void;
  /** Copy what a field holds, and give the words that say how that went. */
  copy(field: HTMLTextAreaElement): Promise<string>;
  withoutScreenshot(): void;
}

export interface ComposerInput {
  comment: string;
  author: Author;
}

/** Where the counter speaks up: at 90% and at 100% of the limit. */
const NEAR_LIMIT = 0.9;
const QUOTE_SHOWN = 280;

const COMMENT_ERRORS = {
  empty: 'composer.comment.empty',
  long: 'composer.comment.long',
  chars: 'composer.comment.chars',
} as const;

const NAME_ERRORS = {
  empty: 'composer.name.empty',
  long: 'composer.name.long',
  chars: 'composer.name.chars',
} as const;

const SHOT_ALT = {
  point: 'shot.alt.point',
  text: 'shot.alt.text',
  page: 'shot.alt.page',
} as const;

const CONTEXT = {
  point: 'composer.context.point',
  text: 'composer.context.text',
  page: 'composer.context.page',
} as const;

const SHOT_LOST = { failed: 'shot.failed', tooLarge: 'shot.tooLarge' } as const;

export class Composer {
  readonly element: HTMLElement;
  readonly comment: HTMLTextAreaElement;
  readonly name: HTMLInputElement | null;
  private readonly counter: HTMLElement;
  private readonly commentError: HTMLElement;
  private readonly nameError: HTMLElement;
  private readonly shotBlock: HTMLElement;
  private readonly alert: HTMLElement;
  private readonly fixes: HTMLElement;
  private readonly sendButton: HTMLButtonElement;
  private readonly progress: HTMLElement;
  private shownShot: ShotState | null = null;
  /** A picture was being drawn anew when the block was drawn last. */
  private shownRendering = false;
  private counterLevel = 0;
  private busy = false;
  /** The screenshot's state when the alert was about it: the alert goes when that changes. */
  private shotAlert: ShotState | null = null;

  constructor(private readonly options: ComposerOptions) {
    const { t, config, kind } = options;
    const mode = config.account ? 'account' : config.identity;

    this.comment = h('textarea', {
      id: 'tf-comment',
      class: 'field',
      rows: 5,
      'data-key': 'comment',
      'aria-describedby': 'tf-comment-hint tf-comment-error',
    });
    this.comment.value = options.draft.comment;
    this.comment.addEventListener('input', () => this.count());
    this.counter = h('p', { class: 'counter', 'aria-hidden': 'true' });
    this.commentError = h('p', { id: 'tf-comment-error', class: 'error' });
    this.nameError = h('p', { id: 'tf-name-error', class: 'error' });
    this.name =
      mode === 'account'
        ? null
        : h('input', {
            id: 'tf-name',
            class: 'field',
            type: 'text',
            autocomplete: 'name',
            'aria-required': mode === 'name' ? 'true' : null,
            'aria-describedby': mode === 'anonymous' ? 'tf-name-error tf-anonymous-note' : 'tf-name-error',
          });
    if (this.name !== null) this.name.value = options.draft.name;
    this.shotBlock = h('div', { class: 'block' });
    this.alert = h('p', { class: 'alert', role: 'alert' });
    this.fixes = h('div', { class: 'row' });
    this.sendButton = button(t('composer.send'), options.send, {
      key: 'composer.send',
      primary: true,
      describedBy: 'tf-notice',
    });
    this.progress = h('p', { class: 'hint' });

    const retention =
      config.retentionDays === 0
        ? t('composer.notice.retention.keep')
        : config.retentionDays === 1
          ? t('composer.notice.retention.one')
          : t('composer.notice.retention', { days: config.retentionDays });
    // Opening in this tab loses what was typed here: the note says so.
    const privacy = privacyLink(t, config.privacyUrl, options.opaque, 'tf-privacy-note');
    // Cut in characters, as everywhere: an emoji counts as one.
    const start = options.quote === null ? null : cut(options.quote, QUOTE_SHOWN);
    const quote = start === options.quote ? start : `${start}...`;

    this.element = h('div', {}, [
      h('p', {}, [t(CONTEXT[kind])]),
      quote !== null && h('p', { class: 'quote', lang: options.quoteLang }, [quote]),
      h('label', { class: 'label', for: 'tf-comment' }, [t('composer.comment')]),
      this.comment,
      h('p', { id: 'tf-comment-hint', class: 'hint' }, [t('composer.comment.hint', { max: MAX_COMMENT })]),
      this.counter,
      this.commentError,
      this.shotBlock,
      mode === 'account'
        ? h('p', { class: 'block' }, [t('composer.account')])
        : h('div', {}, [
            h('label', { class: 'label', for: 'tf-name' }, [
              t(mode === 'name' ? 'composer.name.required' : 'composer.name.optional'),
            ]),
            this.name,
            this.nameError,
            mode === 'anonymous' &&
              h('p', { id: 'tf-anonymous-note', class: 'hint' }, [t('composer.anonymous.note')]),
          ]),
      this.alert,
      this.fixes,
      h('div', { class: 'row' }, [
        this.sendButton,
        button(t('composer.cancel'), options.cancel, { key: 'composer.cancel' }),
      ]),
      this.progress,
      h('p', { id: 'tf-notice', class: 'notice' }, [
        t('composer.notice.visibility'),
        ' ',
        retention,
        config.account ? '' : ` ${t('composer.notice.noAccount')}`,
        ' ',
        privacy,
      ]),
      options.opaque && h('p', { id: 'tf-privacy-note', class: 'notice' }, [t('composer.privacy.note')]),
    ]);
    this.count();
    this.renderShot();
  }

  /** The counter, and its announcement when the text reaches 90% and 100%. */
  private count(): void {
    const { t, announce } = this.options;
    const count = codePoints(this.comment.value);
    this.counter.textContent = t('composer.counter', { count, max: MAX_COMMENT });
    const level = count >= MAX_COMMENT ? 2 : count >= MAX_COMMENT * NEAR_LIMIT ? 1 : 0;
    if (level === 2 && this.counterLevel < 2) announce(t('composer.counter.full', { max: MAX_COMMENT }));
    if (level === 1 && this.counterLevel < 1) announce(t('composer.counter.near', { left: MAX_COMMENT - count }));
    this.counterLevel = level;
  }

  /** The text and the author, or null after marking what is wrong. */
  validate(): ComposerInput | null {
    const { t, config } = this.options;
    this.clearFieldError(this.comment, this.commentError);
    const comment = checkText(this.comment.value, MAX_COMMENT, true);
    let firstWrong: HTMLElement | null = null;
    if (!comment.ok) {
      this.fieldError(this.comment, this.commentError, t(COMMENT_ERRORS[comment.reason], { max: MAX_COMMENT }));
      firstWrong = this.comment;
    }
    let author: Author = { kind: 'account' };
    if (this.name !== null) {
      this.clearFieldError(this.name, this.nameError);
      const name = checkText(this.name.value, MAX_NAME, false);
      if (name.ok) {
        author = { kind: 'name', name: name.value };
      } else if (config.identity === 'anonymous' && name.reason === 'empty') {
        author = { kind: 'anonymous' };
      } else {
        this.fieldError(this.name, this.nameError, t(NAME_ERRORS[name.reason], { max: MAX_NAME }));
        firstWrong ??= this.name;
      }
    }
    if (firstWrong !== null || !comment.ok) {
      firstWrong!.focus();
      return null;
    }
    return { comment: comment.value, author };
  }

  private fieldError(field: HTMLElement, error: HTMLElement, message: string): void {
    error.textContent = message;
    field.setAttribute('aria-invalid', 'true');
  }

  private clearFieldError(field: HTMLElement, error: HTMLElement): void {
    error.textContent = '';
    field.removeAttribute('aria-invalid');
  }

  /** Redraw the screenshot block. Focus inside it stays on the same control
   * when that is still there, else on the block's first button, or on its
   * heading while a screenshot is made, rather than on "Zonder
   * schermafbeelding". An alert about the screenshot goes once the screenshot
   * changes. A screenshot that arrives while that alert's remedy has focus
   * sends focus to the block's heading: on "Versturen" the next Enter would
   * send the picture the giver was about to leave out. */
  renderShot(): void {
    const { t, shot, announce } = this.options;
    const root = this.element.getRootNode() as Document | ShadowRoot;
    const hadFocus = this.shotBlock.contains(root.activeElement);
    const key = hadFocus ? root.activeElement!.getAttribute('data-key') : null;
    const heading = h('h3', { class: 'sub', tabindex: '-1' }, [t('shot.heading')]);
    replace(this.shotBlock, [heading, ...this.shotContent()]);
    if (hadFocus) {
      const same = key === null ? null : this.shotBlock.querySelector<HTMLElement>(`[data-key="${key}"]`);
      (same ?? (shot.state === 'capturing' ? heading : this.shotBlock.querySelector('button')!)).focus();
    }
    if (shot.state === 'capturing' && this.shownShot !== 'capturing') announce(t('shot.making'));
    if (shot.state === 'ready' && this.shownShot === 'capturing') announce(t('shot.ready'));
    // Versturen would go without it: said when it comes to that, also by a
    // part or a cover drawn anew, which can end where the picture was.
    if (
      (shot.state === 'failed' || shot.state === 'tooLarge') &&
      (shot.state !== this.shownShot || (this.shownRendering && !shot.rendering))
    ) {
      announce(t(SHOT_LOST[shot.state]));
    }
    this.shownShot = shot.state;
    this.shownRendering = shot.rendering;
    if (this.shotAlert !== null && shot.state !== this.shotAlert) {
      if (shot.state === 'ready' && this.fixes.contains(root.activeElement)) heading.focus();
      this.showError(null, false);
    }
  }

  private shotContent(): Array<Node | false> {
    const { t, shot, kind } = this.options;
    // While the feedback is on its way the screenshot stays as it is sent.
    const act = (run: () => void) => () => {
      if (!this.busy) run();
    };
    const region = () => button(t('shot.region'), act(this.options.region), { key: 'shot.region' });
    const without = () => button(t('shot.without'), act(() => shot.exclude()), { key: 'shot.without' });
    const updating = shot.rendering && h('p', {}, [t('shot.updating')]);
    switch (shot.state) {
      case 'none':
        return [button(t('shot.make'), act(this.options.take), { key: 'shot.make' })];
      case 'capturing':
        return [h('p', {}, [t('shot.making')]), h('div', { class: 'row' }, [without()])];
      case 'ready':
        return [
          updating,
          h('img', { class: 'thumb', src: shot.url!, alt: t(SHOT_ALT[kind]) }),
          h('p', {}, [t('shot.notice')]),
          shot.placeholders && h('p', {}, [t('shot.placeholders')]),
          h('div', { class: 'row' }, [
            region(),
            button(t('shot.cover'), act(this.options.cover), { key: 'shot.cover' }),
            shot.covers.length > 0 &&
              button(t('shot.uncover'), act(() => void shot.uncover()), { key: 'shot.uncover' }),
            without(),
          ]),
        ];
      case 'tooLarge':
        return [updating, h('p', {}, [t('shot.tooLarge')]), h('div', { class: 'row' }, [region(), without()])];
      case 'failed':
        return [
          h('p', {}, [t('shot.failed')]),
          button(t('shot.retry'), act(this.options.take), { key: 'shot.retry' }),
        ];
      case 'excluded':
        return [
          updating,
          h('p', {}, [t('shot.excluded')]),
          button(t('shot.include'), act(() => void shot.include()), { key: 'shot.include' }),
        ];
    }
  }

  setBusy(busy: boolean): void {
    this.busy = busy;
    this.progress.textContent = busy ? this.options.t('composer.sending') : '';
  }

  /** Show why sending failed, with the remedies that fit; null clears it.
   * Focus on a remedy that goes moves to "Versturen" rather than nowhere. */
  showError(view: ErrorView | null, hadScreenshot: boolean): void {
    const { t } = this.options;
    const root = this.element.getRootNode() as Document | ShadowRoot;
    if (this.fixes.contains(root.activeElement)) this.sendButton.focus();
    replace(this.fixes, []);
    this.shotAlert = null;
    this.alert.textContent = view === null ? '' : view.message;
    if (view === null) return;
    const remedies: HTMLElement[] = [];
    let notice: HTMLElement | null = null;
    if (view.copy) {
      const [copy, said] = copyControl(t('error.copy'), () => this.options.copy(this.comment), 'error.copy');
      remedies.push(copy);
      notice = said;
    }
    if (view.withoutScreenshot && hadScreenshot) {
      remedies.push(button(t('error.withoutShot'), this.options.withoutScreenshot, { key: 'error.withoutShot' }));
    }
    replace(this.fixes, [...remedies, notice]);
  }

  /** Versturen cannot go yet because of the screenshot: say why and offer to
   * send without it, until the screenshot changes. */
  shotProblem(message: string): void {
    this.showError({ message, copy: false, withoutScreenshot: true }, true);
    this.shotAlert = this.options.shot.state;
  }

  dispose(): void {
    this.options.shot.dispose();
  }
}

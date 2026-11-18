/**
 * The composer: what the remark is about, the text, the screenshot, who sends
 * it, and the privacy statement under "Send". The screenshot's own
 * warning is there for every author kind: the giver sees the picture before
 * it can be sent. While the feedback is on its way the screenshot does not
 * change.
 */
import type { Author, ErrorView } from './api';
import {
  button,
  copyControl,
  type FieldError,
  fieldError,
  h,
  type LimitedText,
  limitedText,
  replace,
  setAlert,
} from './dom';
import type { EditorField, EditorParts } from './editor';
import type { Translate } from './i18n';
import type { Shot, ShotState } from './screenshot';
import { checkText, cut, MAX_COMMENT, MAX_NAME } from './text';
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
  announce(message: string): void;
  /** Take the screenshot (again), with the marks of this remark. */
  take(): void;
  send(): void;
  cancel(): void;
  /** Point at another spot or area, for a remark on one. */
  point(): void;
  region(): void;
  cover(): void;
  /** Copy what a field holds, and give the words that say how that went. */
  copy(field: HTMLTextAreaElement): Promise<string>;
  withoutScreenshot(): void;
  /** The editor that a text field turns into once someone writes in it. */
  editor(parts: EditorParts): EditorField;
}

export interface ComposerInput {
  comment: string;
  author: Author;
}

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
  area: 'shot.alt.area',
  text: 'shot.alt.text',
  page: 'shot.alt.page',
} as const;

const CONTEXT = {
  point: 'composer.context.point',
  area: 'composer.context.area',
  text: 'composer.context.text',
  page: 'composer.context.page',
} as const;

const SHOT_LOST = { failed: 'shot.failed', tooLarge: 'shot.tooLarge' } as const;

export class Composer {
  readonly element: HTMLElement;
  readonly comment: HTMLTextAreaElement;
  readonly name: HTMLInputElement | null;
  private readonly commentEditor: EditorField;
  private readonly commentLimit: LimitedText;
  private readonly nameError: FieldError | null;
  private readonly shotBlock: HTMLElement;
  private readonly alert: HTMLElement;
  private readonly fixes: HTMLElement;
  private readonly sendButton: HTMLButtonElement;
  private readonly progress: HTMLElement;
  private shownShot: ShotState | null = null;
  /** A picture was being drawn anew when the block was drawn last. */
  private shownRendering = false;
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
      maxlength: MAX_COMMENT,
      'aria-describedby': 'tf-comment-error',
    });
    this.comment.value = options.draft.comment;
    const commentError = fieldError(this.comment, 'tf-comment-error');
    this.commentLimit = limitedText(
      this.comment,
      commentError,
      {
        full: t(COMMENT_ERRORS.long, { max: MAX_COMMENT }),
        problem: (reason) => t(COMMENT_ERRORS[reason], { max: MAX_COMMENT }),
      },
      options.announce,
    );
    this.comment.addEventListener('input', () => this.commentLimit.typed());
    const commentLabel = h('label', { class: 'label', for: 'tf-comment' }, [t('composer.comment')]);
    this.commentEditor = options.editor({ field: this.comment, label: commentLabel, message: commentError.element });
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
    this.nameError = this.name && fieldError(this.name, 'tf-name-error');
    if (this.name !== null) {
      this.name.value = options.draft.name;
      this.name.addEventListener('input', () => {
        if (this.nameError!.invalid) this.markName();
      });
    }
    this.shotBlock = h('div', { class: 'block' });
    this.alert = h('p', { class: 'alert', role: 'alert' });
    this.fixes = h('div', { class: 'row' });
    this.sendButton = button(t('composer.send'), options.send, {
      key: 'composer.send',
      primary: true,
    });
    this.progress = h('p', { class: 'hint' });

    // Cut in characters, as everywhere: an emoji counts as one.
    const start = options.quote === null ? null : cut(options.quote, QUOTE_SHOWN);
    const quote = start === options.quote ? start : `${start}...`;

    this.element = h('div', {}, [
      h('p', {}, [t(CONTEXT[kind])]),
      (kind === 'point' || kind === 'area') &&
        h('div', { class: 'row' }, [button(t('composer.repoint'), options.point, { key: 'composer.repoint' })]),
      quote !== null && h('p', { class: 'quote', lang: options.quoteLang }, [quote]),
      commentLabel,
      commentError.element,
      this.comment,
      this.shotBlock,
      mode === 'account'
        ? h('p', { class: 'block' }, [t('composer.account')])
        : h('div', {}, [
            h('label', { class: 'label', for: 'tf-name' }, [
              t(mode === 'name' ? 'composer.name.required' : 'composer.name.optional'),
            ]),
            this.nameError!.element,
            this.name,
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
      h('p', { class: 'notice' }, [h('a', { href: config.privacyUrl, rel: 'noreferrer' }, [t('composer.privacy')])]),
    ]);
    this.renderShot();
  }

  /** Marks the name field, and gives the author it names, or null. */
  private markName(): Author | null {
    const { t, config } = this.options;
    const name = checkText(this.name!.value, MAX_NAME, false);
    this.nameError!.clear();
    if (name.ok) return { kind: 'name', name: name.value };
    if (config.identity === 'anonymous' && name.reason === 'empty') return { kind: 'anonymous' };
    this.nameError!.show(t(NAME_ERRORS[name.reason], { max: MAX_NAME }));
    return null;
  }

  /** The text and the author, or null after marking what is wrong. */
  validate(): ComposerInput | null {
    const comment = checkText(this.comment.value, MAX_COMMENT, true);
    this.commentLimit.mark(comment);
    const author: Author | null = this.name === null ? { kind: 'account' } : this.markName();
    if (!comment.ok) this.commentEditor.focus();
    else if (author === null) this.name!.focus();
    if (!comment.ok || author === null) return null;
    return { comment: comment.value, author };
  }

  /** Redraw the screenshot block. Focus inside it stays on the same control
   * when that is still there, else on the block's first button, or on its
   * heading while a screenshot is made, rather than on "Without
   * screenshot". An alert about the screenshot goes once the screenshot
   * changes. A screenshot that arrives while that alert's remedy has focus
   * sends focus to the block's heading: on "Send" the next Enter would
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
    // "Send" would go without it: said when it comes to that, also by a
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
   * Focus on a remedy that goes moves to "Send" rather than nowhere. */
  showError(view: ErrorView | null, hadScreenshot: boolean): void {
    const { t } = this.options;
    const root = this.element.getRootNode() as Document | ShadowRoot;
    if (this.fixes.contains(root.activeElement)) this.sendButton.focus();
    replace(this.fixes, []);
    this.shotAlert = null;
    setAlert(this.alert, view === null ? null : view.message);
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

  /** "Send" cannot go yet because of the screenshot: say why and offer to
   * send without it, until the screenshot changes. */
  shotProblem(message: string): void {
    this.showError({ message, copy: false, withoutScreenshot: true }, true);
    this.shotAlert = this.options.shot.state;
  }

  dispose(): void {
    this.commentEditor.dispose();
    this.options.shot.dispose();
  }
}

/**
 * "My feedback": what a giver sent with their account on this site, newest
 * first, with the site team's latest replies, a reply field and a delete with
 * an inline question. Every text from the API goes in as text, never as
 * markup. A comment and a reply show the Markdown subset of feedback-text,
 * built from text nodes too. A reply typed and not sent stays in `drafts`, which outlives the
 * view, so leaving "My feedback" does not lose it. A reply answered after the
 * list was drawn anew leaves the draft of that newer list alone.
 */
import { renderFeedbackText, TEXT_CLASS } from '../feedback-text/markdown';
import { describeFailure, type MineItem, type MineReply } from './api';
import { button, copyControl, fieldError, h, limitedText, replace, setAlert } from './dom';
import type { EditorField, EditorParts } from './editor';
import { formatDate, type Translate } from './i18n';
import { checkText, MAX_COMMENT } from './text';
import type { Lang } from './types';

export interface MineOptions {
  t: Translate;
  lang: Lang;
  /** Replies typed and not sent, per item id. */
  drafts: Map<string, string>;
  /** The reply field of each item in the list drawn last, per item id. */
  fields: Map<string, HTMLTextAreaElement>;
  announce(message: string): void;
  reply(id: string, body: string): Promise<MineReply>;
  remove(id: string): Promise<void>;
  /** Copy what a field holds, and give the words that say how that went. */
  copy(field: HTMLTextAreaElement): Promise<string>;
  /** An item is gone: drop its pin and put focus somewhere that exists. */
  removed(id: string): void;
  back(): void;
  /** The editor that a text field turns into once someone writes in it. */
  editor(parts: EditorParts): EditorField;
  /** The page has an opaque origin (a shielded site): a link in a comment or
   * reply opens in this tab, as a new one would inherit the sandbox. */
  opaque: boolean;
}

const STATUS = { new: 'mine.status.new', seen: 'mine.status.seen', resolved: 'mine.status.resolved' } as const;
const KIND = { page: 'mine.kind.page', point: 'mine.kind.point', area: 'mine.kind.area', text: 'mine.kind.text' } as const;
const REPLY_ERRORS = { empty: 'mine.reply.empty', long: 'mine.reply.long', chars: 'mine.reply.chars' } as const;

export class MineView {
  readonly element: HTMLElement;
  private readonly content: HTMLElement;
  private readonly alert: HTMLElement;
  private closeQuestion: (() => void) | null = null;
  /** Per item, where `mine.lost` goes. */
  private readonly lostSlots = new Map<string, HTMLElement>();
  /** The list leaves older items out. */
  private more = false;

  constructor(private readonly options: MineOptions) {
    const { t } = options;
    this.alert = h('p', { class: 'alert', role: 'alert' });
    this.content = h('div', {}, [h('p', {}, [t('mine.loading')])]);
    this.element = h('div', {}, [
      this.alert,
      this.content,
      button(t('mine.back'), options.back, { key: 'mine.back' }),
    ]);
  }

  showError(message: string): void {
    setAlert(this.alert, message);
    replace(this.content, []);
  }

  show(items: MineItem[], lost: ReadonlySet<string>, more: boolean): void {
    this.lostSlots.clear();
    this.more = more;
    const listed = new Set(items.map((item) => item.id));
    for (const id of this.options.drafts.keys()) if (!listed.has(id)) this.options.drafts.delete(id);
    for (const id of this.options.fields.keys()) if (!listed.has(id)) this.options.fields.delete(id);
    if (items.length === 0 && !more) {
      this.showEmpty();
      return;
    }
    replace(this.content, [
      h('ul', { class: 'list' }, items.map((item, index) => this.itemView(item, index, lost.has(item.id)))),
      // Newest first: below the list is where the older items would be.
      more && h('p', {}, [this.options.t('mine.olderItems')]),
    ]);
  }

  private showEmpty(): void {
    replace(this.content, [h('p', {}, [this.options.t('mine.empty')])]);
  }

  /** Escape closes an open delete question first; true when it did. */
  escape(): boolean {
    if (this.closeQuestion === null) return false;
    this.closeQuestion();
    return true;
  }

  focusItem(id: string): void {
    this.content.querySelector<HTMLElement>(`[data-item="${CSS.escape(id)}"]`)?.focus();
  }

  /** Say which places are gone, again, after the page changed its layout. */
  markLost(lost: ReadonlySet<string>): void {
    for (const [id, slot] of this.lostSlots) replace(slot, [lost.has(id) && this.lostNote()]);
  }

  private lostNote(): HTMLElement {
    return h('p', {}, [this.options.t('mine.lost')]);
  }

  /** A comment or reply: its Markdown subset as elements, never as markup. */
  private textView(text: string): HTMLElement {
    const { t, opaque } = this.options;
    return h('div', { class: TEXT_CLASS }, [
      renderFeedbackText(text, document, { newTab: !opaque, newTabHint: t('widget.newTab') }),
    ]);
  }

  private replyView(reply: MineReply): HTMLElement {
    const { t, lang } = this.options;
    const who =
      reply.author === 'you'
        ? t('mine.you')
        : reply.name
          ? t('mine.teamNamed', { name: reply.name })
          : t('mine.team');
    return h('li', {}, [
      h('p', { class: 'meta' }, [[who, formatDate(reply.createdAt, lang)].filter(Boolean).join(', ')]),
      this.textView(reply.body),
    ]);
  }

  private itemView(item: MineItem, index: number, lost: boolean): HTMLElement {
    const { t, lang, drafts, fields } = this.options;
    const ids = { field: `tf-reply-${index}`, error: `tf-reply-error-${index}`, question: `tf-delete-${index}` };
    const replies = h('ul', { class: 'replies' }, item.replies.map((reply) => this.replyView(reply)));
    const field = h('textarea', {
      id: ids.field,
      class: 'field',
      rows: 3,
      maxlength: MAX_COMMENT,
      'aria-describedby': ids.error,
    });
    field.value = drafts.get(item.id) ?? '';
    fields.set(item.id, field);
    /** Whether this field still holds the item's draft: a list drawn anew has
     * a field of its own for it. */
    const own = () => fields.get(item.id) === field;
    const keep = () => {
      if (field.value === '') drafts.delete(item.id);
      else drafts.set(item.id, field.value);
    };
    const error = fieldError(field, ids.error);
    const limit = limitedText(
      field,
      error,
      {
        full: t(REPLY_ERRORS.long, { max: MAX_COMMENT }),
        problem: (reason) => t(REPLY_ERRORS[reason], { max: MAX_COMMENT }),
      },
      this.options.announce,
    );
    field.addEventListener('input', () => {
      keep();
      limit.typed();
    });
    const label = h('label', { class: 'label', for: ids.field }, [t('mine.reply')]);
    const editor = this.options.editor({ field, label, message: error.element });
    const alert = h('p', { class: 'alert', role: 'alert' });
    const fixes = h('div', { class: 'row' });
    const actions = h('div', {});
    const lostSlot = h('div', {}, [lost && this.lostNote()]);
    this.lostSlots.set(item.id, lostSlot);
    // The fragment says where on the page, such as the slide of a deck.
    const fragment = item.page.fragment ?? '';
    const pageLabel = `${item.page.title || `/${item.page.path}`}${fragment === '' ? '' : ` ${fragment}`}`;
    const meta = [t(STATUS[item.status]), t(KIND[item.kind]), formatDate(item.createdAt, lang)];
    const element = h('li', { class: 'item', tabindex: '-1', 'data-item': item.id }, [
      h('p', { class: 'meta' }, [meta.filter(Boolean).join(', ')]),
      this.textView(item.comment),
      item.quote !== null && h('p', { class: 'quote' }, [item.quote]),
      // A title of one long word, or a path standing in for it, breaks anywhere.
      h('p', { class: 'url' }, [
        t('mine.page'),
        ' ',
        item.page.url === null ? pageLabel : h('a', { href: `${item.page.url}${fragment}` }, [pageLabel]),
      ]),
      lostSlot,
      // Only the latest replies come along: say so where the older ones would be.
      item.replyCount > item.replies.length && h('p', {}, [t('mine.olderReplies')]),
      replies,
      label,
      error.element,
      field,
      alert,
      fixes,
      actions,
    ]);
    let busy = false;

    const sendReply = async () => {
      if (busy) return;
      setAlert(alert, null);
      replace(fixes, []);
      const check = checkText(field.value, MAX_COMMENT, true);
      limit.mark(check);
      if (!check.ok) {
        editor.focus();
        return;
      }
      busy = true;
      // The field takes typing while the reply is on its way: once it is
      // answered, only what was sent goes from it.
      const sent = field.value;
      // A reply on its way is no draft: coming back meanwhile must not offer
      // it a second time.
      drafts.delete(item.id);
      try {
        replies.append(this.replyView(await this.options.reply(item.id, check.value)));
        if (field.value.startsWith(sent)) field.value = field.value.slice(sent.length).trimStart();
        editor.sync();
        limit.typed();
        this.options.announce(t('mine.replySent'));
      } catch (failure) {
        const view = describeFailure(failure, 'reply', t);
        setAlert(alert, view.message);
        if (view.copy) fixes.append(...copyControl(t('error.copy'), () => this.options.copy(field), `copy-${item.id}`));
        // The text waits for the next time the list opens, unless a list
        // drawn anew meanwhile holds a draft of its own for the item.
        const waits = own() || !drafts.has(item.id);
        if (waits) drafts.set(item.id, field.value);
        // Left meanwhile: nobody sees that alert, so the status region says
        // it, with the reason that does not ask to copy from a field that is
        // gone, and where the text waits, unless the refusal means the page
        // cannot list it any more.
        if (!element.isConnected) {
          const reason = describeFailure(failure, 'list', t).message;
          const back = view.copy || !waits ? '' : ` ${t('mine.reply.kept')}`;
          this.options.announce(`${t('mine.reply.refused', { reason })}${back}`);
        }
      } finally {
        busy = false;
        if (own()) keep();
      }
    };

    const confirmDelete = async () => {
      if (busy) return;
      busy = true;
      try {
        await this.options.remove(item.id);
        this.closeQuestion = null;
        this.lostSlots.delete(item.id);
        drafts.delete(item.id);
        element.remove();
        if (this.content.querySelector('.item') === null && !this.more) this.showEmpty();
        this.options.announce(t('mine.deleted'));
        this.options.removed(item.id);
      } catch (failure) {
        setAlert(alert, describeFailure(failure, 'delete', t).message);
      } finally {
        busy = false;
      }
    };

    const close = () => {
      this.closeQuestion = null;
      showActions().focus();
    };

    const ask = () => {
      this.closeQuestion?.();
      const cancel = button(t('composer.cancel'), close, { key: `cancel-${item.id}`, describedBy: ids.question });
      replace(actions, [
        h('p', { id: ids.question }, [t('mine.delete.question')]),
        h('div', { class: 'row' }, [
          button(t('mine.delete'), () => void confirmDelete(), { key: `confirm-${item.id}` }),
          cancel,
        ]),
      ]);
      this.closeQuestion = close;
      cancel.focus();
    };

    /** The reply and delete buttons; returns the delete button. */
    const showActions = (): HTMLButtonElement => {
      const remove = button(t('mine.delete'), ask, { key: `delete-${item.id}` });
      replace(actions, [
        h('div', { class: 'row' }, [
          button(t('mine.replySend'), () => void sendReply(), { key: `reply-${item.id}` }),
          remove,
        ]),
      ]);
      return remove;
    };

    showActions();
    return element;
  }
}

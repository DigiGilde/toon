/**
 * What one open feedback item holds while the team works on it: the item and
 * the versions it is read with, the reply being written, what was typed and
 * not sent, and the status and delete actions. The sheet beside
 * the list and the page of the item each keep one, and FeedbackItemView
 * shows it.
 *
 * It lives with the sheet or the page, not with the view inside it: the
 * replies typed for other items have to outlast the view, which the sheet
 * renders anew in a teleport.
 */
import { nextTick, ref, type Ref } from 'vue';

import * as toon from '@/api/toon';
import { errorText } from '@/api/client';
import type { FeedbackDetail, FeedbackStatus, Version } from '@/api/types';
import type Notices from '@/components/site/Notices.vue';
import { pageLabel } from '@/components/site/feedbackText';
import { useConfirm } from '@/composables/confirm';
import { stripFormat } from '@/feedback-widget/text';
import { t } from '@/i18n';

export interface FeedbackItemOptions {
  group: () => string;
  site: () => string;
  /** The id of the item open now, or null once the sheet closed. */
  openId: () => string | null;
  /** Where the outcome of a reply, a status change or a delete is said. */
  notices: Ref<InstanceType<typeof Notices> | null>;
  /** The item has loaded. */
  loaded?: (detail: FeedbackDetail) => void;
  /** The item could not be loaded. */
  failed?: (reason: unknown) => void;
  /** Its status changed, whatever is shown by now. */
  updated?: (detail: FeedbackDetail) => void;
  /** A reply to the item with this id is in, whatever is shown by now. */
  replied?: (id: string) => void;
  /** The item with this id is deleted. */
  removed?: (id: string) => void;
}

export function useFeedbackItem(options: FeedbackItemOptions) {
  const { notices } = options;
  const statusActions = ref<HTMLElement | null>(null);

  const detail = ref<FeedbackDetail | null>(null);
  /** The site's versions, for the date of the one the page showed, null when they could not be read. */
  const versions = ref<Version[] | null>(null);
  const error = ref<unknown>(null);
  let latest = 0;

  const reply = ref('');
  const replyEmpty = ref(false);
  const replyError = ref<string | null>(null);
  const replyBusy = ref(false);
  const statusBusy = ref<FeedbackStatus | null>(null);
  /**
   * What was typed in the reply field of each item and not sent, by item id, so
   * Escape, "Close" or another item does not throw it away.
   */
  const drafts = new Map<string, string>();

  function keepDraft(id: string): void {
    if (reply.value === '') drafts.delete(id);
    else drafts.set(id, reply.value);
  }

  /** Opens the item with this id: each opening reads it anew, also of the same item. */
  async function load(id: string): Promise<void> {
    latest += 1;
    const request = latest;
    detail.value = null;
    versions.value = null;
    error.value = null;
    reply.value = drafts.get(id) ?? '';
    replyEmpty.value = false;
    replyError.value = null;
    // What is still on its way for the item shown before must not hold up the
    // buttons of this one. Its answer leaves these flags alone (see showing).
    replyBusy.value = false;
    statusBusy.value = null;
    const [found, list] = await Promise.allSettled([
      toon.feedbackDetail(options.group(), options.site(), id),
      toon.versions(options.group(), options.site()),
    ]);
    // Another item was opened in the meantime: this answer is about the old one.
    if (request !== latest) return;
    if (found.status === 'rejected') {
      error.value = found.reason;
      options.failed?.(found.reason);
      return;
    }
    detail.value = found.value;
    versions.value = list.status === 'fulfilled' ? list.value : null;
    options.loaded?.(found.value);
  }

  /**
   * Whether the item is still shown as it was loaded when an action on it
   * started. An answer that comes back after another item was opened, or the
   * same one again, is no longer about what is on screen.
   */
  function showing(request: number): boolean {
    return request === latest;
  }

  function inputValue(event: Event): string {
    return (
      (event as CustomEvent<{ value?: string }>).detail?.value ?? (event.target as HTMLTextAreaElement).value
    );
  }

  function editReply(event: Event, current: FeedbackDetail): void {
    reply.value = inputValue(event);
    replyEmpty.value = false;
    replyError.value = null;
    keepDraft(current.id);
  }

  async function sendReply(current: FeedbackDetail): Promise<void> {
    // Without the format characters the API refuses: pasted text carries them
    // unseen.
    const body = stripFormat(reply.value).trim();
    replyEmpty.value = body === '';
    replyError.value = null;
    if (replyEmpty.value || replyBusy.value) return;
    const request = latest;
    replyBusy.value = true;
    // The field is read-only until the answer, so it holds this reply alone.
    drafts.delete(current.id);
    try {
      const added = await toon.addFeedbackReply(options.group(), options.site(), current.id, body);
      // The list counts the reply, whatever is shown by now.
      options.replied?.(current.id);
      // Another item is open: its thread and its field are not this one's, and
      // "posted" over it would read as its own reply sent.
      if (!showing(request)) return;
      current.replies = [...current.replies, added];
      current.replyCount += 1;
      reply.value = '';
      notices.value?.notify('success', t('feedback.reply.sent'));
    } catch (f) {
      const reason = errorText(f, t('feedback.reply.failed'));
      if (showing(request) && options.openId() !== null) {
        replyError.value = reason;
        keepDraft(current.id);
        return;
      }
      // In front of whatever was typed for that item since it was sent.
      const draft = [body, drafts.get(current.id)].filter((text) => text !== undefined).join('\n\n');
      drafts.set(current.id, draft);
      if (options.openId() === current.id) reply.value = draft;
      notices.value?.notify(
        'critical',
        t('feedback.reply.failed.other', { page: pageLabel(current.page) }),
        `${reason} ${t('feedback.reply.failed.kept')}`,
      );
    } finally {
      if (showing(request)) replyBusy.value = false;
    }
  }

  async function setStatus(current: FeedbackDetail, status: FeedbackStatus): Promise<void> {
    if (statusBusy.value !== null) return;
    const request = latest;
    statusBusy.value = status;
    try {
      const updated = await toon.setFeedbackStatus(options.group(), options.site(), current.id, status);
      // The list follows the new status, whatever is shown by now.
      options.updated?.(updated);
      if (!showing(request)) return;
      detail.value = updated;
      notices.value?.notify('success', t(`feedback.action.done.${updated.status}`));
      // The button that was pressed is gone with the status it set. The focus
      // goes on to what can be done next, not back to the top.
      await nextTick();
      statusActions.value?.querySelector<HTMLElement>('nldd-button')?.focus();
    } catch (f) {
      notices.value?.notify(
        'critical',
        showing(request)
          ? t('feedback.action.failed')
          : t('feedback.action.failed.other', { page: pageLabel(current.page) }),
        errorText(f, t('feedback.action.failed.detail')),
      );
    } finally {
      if (showing(request)) statusBusy.value = null;
    }
  }

  async function remove(id: string): Promise<void> {
    try {
      await toon.deleteFeedback(options.group(), options.site(), id);
      options.removed?.(id);
    } catch (f) {
      notices.value?.notify('critical', t('feedback.delete.failed'), errorText(f, t('feedback.delete.failed.detail')));
    }
  }

  const deletion = useConfirm<string>(remove);

  return {
    load,
    detail,
    versions,
    error,
    reply,
    replyEmpty,
    replyError,
    replyBusy,
    statusBusy,
    statusActions,
    editReply,
    sendReply,
    setStatus,
    deletion,
  };
}

export type FeedbackItemState = ReturnType<typeof useFeedbackItem>;

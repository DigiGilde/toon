<script setup lang="ts">
/**
 * One feedback item beside the list: what was sent, the place and the
 * screenshot, who sent it on which version, the thread, and what the reader's
 * role lets them do with it. An editor replies and moves the status on, only
 * with the buttons that change something; an admin can also delete it.
 *
 * Everything a sender wrote is put in as text, never as markup, and never in
 * an attribute that formats it.
 */
import { computed, nextTick, ref } from 'vue';

import * as toon from '@/api/toon';
import { errorText } from '@/api/client';
import type { FeedbackDetail, FeedbackItem, FeedbackStatus, Version } from '@/api/types';
import ConfirmModal from '@/components/ConfirmModal.vue';
import ErrorBanner from '@/components/ErrorBanner.vue';
import Notices from '@/components/site/Notices.vue';
import {
  authorDetail,
  kindLabel,
  nameUnverified,
  pageLabel,
  replyAuthor,
  statusDetail,
  targetLabel,
  versionText,
  viewportText,
  withoutFormatCharacters,
} from '@/components/site/feedbackText';
import { useConfirm } from '@/composables/confirm';
import { useSheetOpen } from '@/composables/sheetOpen';
import { formatTimestamp } from '@/format';
import { t } from '@/i18n';

const props = defineProps<{
  group: string;
  site: string;
  /** The item to show; null closes the sheet. */
  item: FeedbackItem | null;
  canEdit: boolean;
  canAdmin: boolean;
  /**
   * The refs of the previews whose pages still open: empty when the site has
   * none, null when they could not be read, which leaves the way to the page
   * in place.
   */
  reachablePreviews: string[] | null;
  /**
   * Whether the site takes feedback, and on its live site too, as last saved:
   * a page without the feedback button opens without marking the place.
   */
  enabled: boolean;
  live: boolean;
}>();

const emit = defineEmits<{
  close: [];
  updated: [FeedbackDetail];
  replied: [string];
  removed: [string];
}>();

/** The cap on a reply in the API. */
const REPLY_MAX_LENGTH = 5000;

const sheet = ref<HTMLElement & { show: () => void; hide: () => void }>();
const notices = ref<InstanceType<typeof Notices> | null>(null);
const statusActions = ref<HTMLElement | null>(null);

/** The item last opened. It stays while the sheet closes, so the closing sheet is not empty. */
const shown = ref<FeedbackItem | null>(null);
const detail = ref<FeedbackDetail | null>(null);
/** The site's versions, for the date of the one the page showed; null when they could not be read. */
const versions = ref<Version[] | null>(null);
const error = ref<unknown>(null);
let latest = 0;

const reply = ref('');
const replyEmpty = ref(false);
const replyError = ref<string | null>(null);
const replyBusy = ref(false);
const statusBusy = ref<FeedbackStatus | null>(null);
/**
 * Replies refused after the sheet moved on to another item, or to the same one
 * opened again, by item id. A reply on its way is no draft, so without these
 * the text would be gone. It goes back into the field when that item opens.
 */
const unsent = new Map<string, string>();
/**
 * What was typed in the reply field of each item and not sent, by item id, so
 * Escape, "Sluiten" or another item does not throw it away.
 */
const drafts = new Map<string, string>();
/**
 * The reply of each item that is on its way, as the field held it when sent.
 * It is no draft: opening its item again meanwhile must not offer it twice.
 */
const sending = new Map<string, { text: string }>();

/** `text` without the reply that went out at its start; left as it is when it no longer starts with it. */
function withoutSent(text: string, sent: string): string {
  return text.startsWith(sent) ? text.slice(sent.length).trimStart() : text;
}

/** The field as the draft of item `id`, without its reply on its way, and no draft once that is empty. */
function keepDraft(id: string): void {
  const away = sending.get(id);
  const text = away === undefined ? reply.value : withoutSent(reply.value, away.text);
  if (text === '') drafts.delete(id);
  else drafts.set(id, text);
}

async function load(item: FeedbackItem): Promise<void> {
  latest += 1;
  const request = latest;
  shown.value = item;
  detail.value = null;
  versions.value = null;
  error.value = null;
  // A refused reply first, then what was typed after it.
  reply.value = [unsent.get(item.id), drafts.get(item.id)].filter((text) => text !== undefined).join('\n\n');
  unsent.delete(item.id);
  keepDraft(item.id);
  replyEmpty.value = false;
  replyError.value = null;
  // What is still on its way for the item shown before must not hold up the
  // buttons of this one. Its answer leaves these flags alone (see showing).
  replyBusy.value = false;
  statusBusy.value = null;
  const [found, list] = await Promise.allSettled([
    toon.feedbackDetail(props.group, props.site, item.id),
    toon.versions(props.group, props.site),
  ]);
  // Another item was opened in the meantime: this answer is about the old one.
  if (request !== latest) return;
  if (found.status === 'rejected') {
    error.value = found.reason;
    return;
  }
  detail.value = found.value;
  versions.value = list.status === 'fulfilled' ? list.value : null;
}

/**
 * Whether the sheet still shows the item as it was loaded when an action on it
 * started. An answer that comes back after another item was opened, or the
 * same one again, is no longer about what is on screen.
 */
function showing(request: number): boolean {
  return request === latest;
}

// The sheet only opens with an item, so there is one to load.
useSheetOpen(
  () => props.item !== null,
  sheet,
  () => void load(props.item!),
);

const page = computed(() => (shown.value ? pageLabel(shown.value.page) : ''));
const title = computed(() => t('feedback.sheet.title', { page: page.value }));
const label = computed(() =>
  shown.value
    ? t('feedback.sheet.label', { page: page.value, date: formatTimestamp(shown.value.createdAt) })
    : title.value,
);

interface MetaRow {
  label: string;
  value: string;
  /** "Naam niet gecontroleerd" beside a name the sender typed. */
  unverified?: boolean;
  testid: string;
}

function metaOf(current: FeedbackDetail): MetaRow[] {
  return [
    { label: t('feedback.meta.status'), value: statusDetail(current), testid: 'status' },
    { label: t('feedback.meta.kind'), value: kindLabel(current.kind), testid: 'kind' },
    {
      label: t('feedback.meta.author'),
      value: authorDetail(current.author),
      unverified: nameUnverified(current.author),
      testid: 'author',
    },
    { label: t('feedback.meta.created'), value: formatTimestamp(current.createdAt), testid: 'created' },
    { label: t('feedback.meta.page'), value: `/${current.page.path}`, testid: 'page' },
    { label: t('feedback.meta.target'), value: targetLabel(current), testid: 'target' },
    { label: t('feedback.meta.version'), value: versionText(current.versionId, versions.value), testid: 'version' },
    { label: t('feedback.meta.viewport'), value: viewportText(current.viewport), testid: 'viewport' },
  ];
}

function screenshotOf(current: FeedbackDetail): string {
  return toon.feedbackScreenshotUrl(props.group, props.site, current.id);
}

/** Only what changes something: no "mark as seen" on what was seen already. */
function nextStatusesOf(current: FeedbackDetail): { status: FeedbackStatus; text: string }[] {
  switch (current.status) {
    case 'new':
      return [
        { status: 'seen', text: t('feedback.action.seen') },
        { status: 'resolved', text: t('feedback.action.resolve') },
      ];
    case 'seen':
      return [{ status: 'resolved', text: t('feedback.action.resolve') }];
    default:
      return [{ status: 'new', text: t('feedback.action.reopen') }];
  }
}

/** A preview item whose preview is gone: the place on its page cannot be opened any more. */
function previewGoneOf(current: FeedbackDetail): boolean {
  return (
    current.target === 'preview' &&
    props.reachablePreviews !== null &&
    !props.reachablePreviews.includes(String(current.previewRef))
  );
}

/**
 * Why the page of an item with a place opens without marking it, or null when
 * it marks it. Only the widget reads the place from the address, and the
 * settings decide which pages get it. Other reasons, such as a button the
 * reader hid, are not known here.
 */
function unmarkedOf(current: FeedbackDetail): string | null {
  if (current.highlightUrl === null) return null;
  if (!props.enabled) return t('feedback.sheet.view.off');
  if (current.target === 'live' && !props.live) return t('feedback.sheet.view.offLive');
  return null;
}

/** A sender without an account has no way to read a reply, and the team had better know. */
function replyHelpOf(current: FeedbackDetail): string {
  return current.author.kind === 'account'
    ? t('feedback.reply.help.account')
    : t('feedback.reply.help.noAccount');
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
  // The field as it was sent: once the reply is in, only this leaves it.
  const sent = reply.value;
  // Without the format characters the API refuses, as the widget does with
  // what a giver types: pasted text carries them unseen.
  const body = withoutFormatCharacters(sent).trim();
  replyEmpty.value = body === '';
  replyError.value = null;
  if (replyEmpty.value || replyBusy.value) return;
  const request = latest;
  replyBusy.value = true;
  const away = { text: sent };
  sending.set(current.id, away);
  drafts.delete(current.id);
  // No longer on its way, unless a later reply of the same item is by now.
  const arrived = () => {
    if (sending.get(current.id) === away) sending.delete(current.id);
  };
  try {
    const added = await toon.addFeedbackReply(props.group, props.site, current.id, body);
    arrived();
    // The list counts the reply, whatever the sheet shows by now.
    emit('replied', current.id);
    // Another item is open: its thread and its draft are not this one's, and
    // "posted" over it would read as its own reply sent.
    if (!showing(request)) return;
    current.replies = [...current.replies, added];
    current.replyCount += 1;
    // What was typed while it was on its way stays, and is its draft already.
    reply.value = withoutSent(reply.value, sent);
    notices.value?.notify('success', t('feedback.reply.sent'));
  } catch (f) {
    arrived();
    const reason = errorText(f, t('feedback.reply.failed'));
    // A closed sheet shows no field either, and a reply on its way is no draft
    // the next opening would put back.
    if (showing(request) && props.item !== null) {
      replyError.value = reason;
      keepDraft(current.id);
    } else {
      // That item open again since, its field on screen and empty: the text
      // goes back in now. Otherwise it waits for the next time the item opens,
      // after any other text that waits there.
      const now = props.item?.id === current.id && detail.value?.id === current.id && reply.value === '';
      if (now) {
        reply.value = body;
        replyEmpty.value = false;
        keepDraft(current.id);
      } else {
        const waiting = unsent.get(current.id);
        unsent.set(current.id, waiting === undefined ? body : `${waiting}\n\n${body}`);
      }
      notices.value?.notify(
        'critical',
        t('feedback.reply.failed.other', { page: pageLabel(current.page) }),
        `${reason} ${t(now ? 'feedback.reply.failed.restored' : 'feedback.reply.failed.kept')}`,
      );
    }
  } finally {
    if (showing(request)) replyBusy.value = false;
  }
}

async function setStatus(current: FeedbackDetail, status: FeedbackStatus): Promise<void> {
  if (statusBusy.value !== null) return;
  const request = latest;
  statusBusy.value = status;
  try {
    const updated = await toon.setFeedbackStatus(props.group, props.site, current.id, status);
    // The list follows the new status, whatever the sheet shows by now.
    emit('updated', updated);
    if (!showing(request)) return;
    detail.value = updated;
    notices.value?.notify('success', t(`feedback.action.done.${updated.status}`));
    // The button that was pressed is gone with the status it set; the focus
    // goes on to what can be done next, not back to the top of the sheet.
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
    await toon.deleteFeedback(props.group, props.site, id);
    emit('removed', id);
  } catch (f) {
    notices.value?.notify(
      'critical',
      t('feedback.delete.failed'),
      errorText(f, t('feedback.delete.failed.detail')),
    );
  }
}

const {
  open: deleteOpen,
  busy: deleteBusy,
  ask: askDelete,
  cancel: cancelDelete,
  confirm: confirmDelete,
} = useConfirm<string>(remove);
</script>

<template>
  <Notices ref="notices" />

  <!-- Overlays belong on the document root (NLDD), see PublishSheet. -->
  <Teleport to="body">
    <nldd-sheet
      ref="sheet"
      placement="right"
      :accessible-label="label"
      data-testid="feedback-sheet"
      @close="emit('close')"
    >
      <!-- nldd-page brings the scroller the sheet expects; see PublishSheet. -->
      <nldd-page>
        <nldd-container v-if="shown" padding="24" gap="16">
          <nldd-title :size="4">
            <h2>{{ title }}</h2>
            <!-- Close belongs at the top right, not up against the primary
                 button (NLDD guideline: give the way out physical distance). -->
            <nldd-button
              slot="end"
              appearance="neutral-transparent"
              size="sm"
              :text="t('feedback.sheet.close')"
              type="button"
              data-testid="feedback-sheet-close"
              @click="emit('close')"
            ></nldd-button>
          </nldd-title>

          <ErrorBanner v-if="error" :error="error" />

          <nldd-inline-dialog
            v-else-if="!detail"
            variant="loading"
            :text="t('feedback.loading')"
            data-testid="feedback-sheet-loading"
          ></nldd-inline-dialog>

          <template v-else>
            <nldd-rich-text data-testid="feedback-comment">
              <p class="feedback-sheet__text">{{ detail.comment }}</p>
              <template v-if="detail.quote !== null">
                <p>
                  <strong>{{ t('feedback.sheet.quote') }}</strong>
                </p>
                <blockquote class="feedback-sheet__text" data-testid="feedback-quote">{{ detail.quote }}</blockquote>
              </template>
            </nldd-rich-text>

            <!-- A line, not a disabled button, once the preview is gone: its
                 address would only open "Niet gevonden". -->
            <nldd-rich-text v-if="previewGoneOf(detail)" data-testid="feedback-view-gone">
              <p>{{ t('feedback.sheet.view.gone', { ref: String(detail.previewRef) }) }}</p>
            </nldd-rich-text>
            <template v-else>
              <nldd-button-group orientation="horizontal">
                <nldd-button
                  appearance="secondary"
                  start-icon="open-new-page"
                  :text="t('feedback.sheet.view')"
                  :href="detail.highlightUrl ?? detail.page.url"
                  target="_blank"
                  data-testid="feedback-view"
                ></nldd-button>
              </nldd-button-group>
              <nldd-rich-text v-if="unmarkedOf(detail) !== null" data-testid="feedback-view-unmarked">
                <p>{{ unmarkedOf(detail) }}</p>
              </nldd-rich-text>
            </template>

            <nldd-container v-if="detail.hasScreenshot" layout="stack" gap="8" data-testid="feedback-screenshot">
              <nldd-image :src="screenshotOf(detail)" :alt="t('feedback.sheet.screenshot.alt')"></nldd-image>
              <nldd-link :href="screenshotOf(detail)" target="_blank" data-testid="feedback-screenshot-open">{{
                t('feedback.sheet.screenshot.open')
              }}</nldd-link>
            </nldd-container>

            <nldd-list appearance="box-tinted" :accessible-label="t('feedback.meta.label')" data-testid="feedback-meta">
              <nldd-list-item
                v-for="row in metaOf(detail)"
                :key="row.testid"
                size="md"
                :data-testid="`feedback-meta-${row.testid}`"
              >
                <nldd-text-cell :text="`**${row.label}**`"></nldd-text-cell>
                <!-- The value in the slot: it can be the sender's own words. -->
                <nldd-text-cell>{{ row.value }}<template v-if="row.unverified">&nbsp;<nldd-tag size="sm" :text="t('feedback.author.unverified')"></nldd-tag></template></nldd-text-cell>
              </nldd-list-item>
            </nldd-list>

            <section aria-labelledby="heading-feedback-thread">
              <nldd-container layout="stack" gap="8">
                <nldd-title :size="5">
                  <h3 id="heading-feedback-thread">{{ t('feedback.thread.heading') }}</h3>
                </nldd-title>
                <nldd-rich-text v-if="detail.replies.length === 0" data-testid="feedback-thread-empty">
                  <p>{{ t('feedback.thread.empty') }}</p>
                </nldd-rich-text>
                <nldd-list
                  v-else
                  appearance="box-tinted"
                  :accessible-label="t('feedback.thread.heading')"
                  data-testid="feedback-thread"
                >
                  <nldd-list-item
                    v-for="message in detail.replies"
                    :key="message.id"
                    size="md"
                    :data-testid="`feedback-reply-${message.id}`"
                  >
                    <nldd-text-cell>
                      <span slot="overline">{{
                        t('feedback.thread.byline', {
                          author: replyAuthor(message),
                          date: formatTimestamp(message.createdAt),
                        })
                      }}</span>
                      <span class="feedback-sheet__text">{{ message.body }}</span>
                    </nldd-text-cell>
                  </nldd-list-item>
                </nldd-list>
              </nldd-container>
            </section>

            <template v-if="canEdit">
              <nldd-form data-testid="feedback-reply-form" @submit.prevent="sendReply(detail)">
                <nldd-form-field :label="t('feedback.reply.label')">
                  <nldd-multi-line-text-field
                    name="feedback-reply"
                    required
                    :maxlength="REPLY_MAX_LENGTH"
                    :value="reply"
                    :invalid="replyEmpty || replyError !== null || undefined"
                    :unmet="replyError !== null ? 'feedback-reply-server' : undefined"
                    data-testid="feedback-reply"
                    @input="editReply($event, detail)"
                  ></nldd-multi-line-text-field>
                  <nldd-form-field-help-text>{{ replyHelpOf(detail) }}</nldd-form-field-help-text>
                  <nldd-validation-list>
                    <nldd-validation-item id="feedback-reply-required" required>
                      {{ t('feedback.reply.required') }}
                    </nldd-validation-item>
                    <nldd-validation-item id="feedback-reply-server">
                      {{ replyError }}
                    </nldd-validation-item>
                  </nldd-validation-list>
                </nldd-form-field>
                <nldd-form-actions>
                  <nldd-button-group>
                    <nldd-button
                      appearance="primary"
                      type="submit"
                      :text="t('feedback.reply.submit')"
                      :loading="replyBusy || undefined"
                      data-testid="feedback-reply-submit"
                    ></nldd-button>
                  </nldd-button-group>
                </nldd-form-actions>
              </nldd-form>
              <!-- The validation list is no live region, so a refusal from the
                   server is said here too. Inside the sheet: while it is open,
                   everything outside it is hidden from a screen reader. -->
              <div v-if="replyError" role="alert" class="visually-hidden" data-testid="feedback-reply-alert">
                {{ replyError }}
              </div>

              <nldd-button-group ref="statusActions" orientation="horizontal" data-testid="feedback-status-actions">
                <nldd-button
                  v-for="action in nextStatusesOf(detail)"
                  :key="action.status"
                  appearance="secondary"
                  :text="action.text"
                  :loading="statusBusy === action.status || undefined"
                  :data-testid="`feedback-mark-${action.status}`"
                  @click="setStatus(detail, action.status)"
                ></nldd-button>
              </nldd-button-group>
            </template>

            <nldd-rich-text v-else data-testid="feedback-read-only">
              <p>{{ t('feedback.readOnly') }}</p>
            </nldd-rich-text>

            <nldd-button-group v-if="canAdmin" orientation="horizontal">
              <nldd-button
                appearance="destructive"
                :text="t('feedback.delete')"
                data-testid="feedback-delete"
                @click="askDelete(detail.id)"
              ></nldd-button>
            </nldd-button-group>
          </template>
        </nldd-container>
      </nldd-page>
    </nldd-sheet>
  </Teleport>

  <ConfirmModal
    :open="deleteOpen"
    :title="t('feedback.delete.confirm.title')"
    :text="t('feedback.delete.confirm.text')"
    :keep-label="t('feedback.delete.confirm.keep')"
    :confirm-label="t('feedback.delete')"
    :busy="deleteBusy"
    @confirm="confirmDelete"
    @close="cancelDelete"
  />
</template>

<style scoped>
/*
 * A comment, a quote and a reply keep the line breaks and the spaces their
 * writer typed, and a long address in them wraps rather than pushing a 320 px
 * screen sideways (WCAG 1.4.10).
 */
.feedback-sheet__text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>

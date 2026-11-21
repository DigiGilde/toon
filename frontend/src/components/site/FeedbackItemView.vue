<script setup lang="ts">
/**
 * One feedback item: what was sent, the place and the screenshot, who sent it
 * on which version, the thread, and what the reader's role lets them do with
 * it. An editor replies and moves the status on, only with the buttons that
 * change something, and an admin can also delete it. The sheet beside the list
 * and the page of the item both show it, each around the state it keeps
 * (feedbackItem.ts), with a title and a way out of their own. The `actions`
 * slot takes toolbar items of their own, after the ones here.
 *
 * Everything a sender wrote is put in as text, never as markup, and never in
 * an attribute that formats it. A comment and a reply show the Markdown
 * subset of feedback-text, which FeedbackMarkdown builds from its tree.
 */
import { useSlots } from 'vue';

import * as toon from '@/api/toon';
import type { FeedbackDetail, FeedbackStatus } from '@/api/types';
import ConfirmModal from '@/components/ConfirmModal.vue';
import ErrorBanner from '@/components/ErrorBanner.vue';
import FeedbackMarkdown from '@/components/FeedbackMarkdown.vue';
import type { FeedbackItemState } from '@/components/site/feedbackItem';
import {
  authorDetail,
  earlierVersionText,
  feedbackViewUrl,
  kindLabel,
  nameUnverified,
  replyAuthor,
  safeFragment,
  statusDetail,
  targetLabel,
  versionPageUrl,
  versionText,
  viewportText,
  withFragment,
} from '@/components/site/feedbackText';
import { formatTimestamp } from '@/format';
import { t } from '@/i18n';

const props = defineProps<{
  /** The item and what is being done with it, kept by the sheet or the page around this. */
  state: FeedbackItemState;
  group: string;
  site: string;
  /** The content origin, for the address of the version an item was given on. */
  contentBase: string;
  canEdit: boolean;
  canAdmin: boolean;
  /**
   * Whether the site takes feedback, and on its live site too, as last saved:
   * a page without the feedback button opens without marking the place.
   */
  enabled: boolean;
  live: boolean;
}>();

const slots = useSlots();

/** The cap on a reply in the API. */
const REPLY_MAX_LENGTH = 5000;

// One state for the life of this view: the sheet or the page keeps it.
const {
  detail,
  versions,
  error,
  reply,
  replyEmpty,
  replyError,
  replyBusy,
  statusBusy,
  editReply,
  sendReply,
  setStatus,
} = props.state;
const {
  open: deleteOpen,
  busy: deleteBusy,
  ask: askDelete,
  cancel: cancelDelete,
  confirm: confirmDelete,
} = props.state.deletion;

interface MetaRow {
  label: string;
  value: string;
  /** "Name not checked" beside a name the sender typed. */
  unverified?: boolean;
  /** A line under the value. */
  note?: string | null;
  /** A link after that line. */
  link?: { href: string; text: string } | null;
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
    { label: t('feedback.meta.page'), value: `/${current.page.path}${safeFragment(current.page.fragment) ?? ''}`, testid: 'page' },
    { label: t('feedback.meta.target'), value: targetLabel(current), testid: 'target' },
    {
      label: t('feedback.meta.version'),
      value: versionText(current.versionId, versions.value),
      ...earlierOf(current),
      testid: 'version',
    },
    { label: t('feedback.meta.viewport'), value: viewportText(current.viewport), testid: 'viewport' },
  ];
}

/**
 * On an earlier version: what the live site or the preview shows now, and the
 * way to the version the item was given on while it is still there. Everyone
 * who reads feedback has a role on the site, and so opens its `_version`
 * addresses.
 */
function earlierOf(current: FeedbackDetail): Pick<MetaRow, 'note' | 'link'> {
  const note = earlierVersionText(current);
  if (note === null || current.versionId === null) return { note };
  const href = withFragment(
    versionPageUrl(props.contentBase, props.group, props.site, current.versionId, current.page.path),
    current.page.fragment,
  );
  return { note, link: { href, text: t('feedback.meta.version.view') } };
}

function screenshotOf(current: FeedbackDetail): string {
  return toon.feedbackScreenshotUrl(props.group, props.site, current.id);
}

/**
 * Only what changes something: no "mark as seen" on what was seen already.
 * The priority is how long a button stays in the toolbar when the row runs
 * out of room: resolving or reopening longest, "mark as seen" first to go,
 * with the other actions of the toolbar in between.
 */
function nextStatusesOf(current: FeedbackDetail): { status: FeedbackStatus; text: string; priority: number }[] {
  switch (current.status) {
    case 'new':
      return [
        { status: 'seen', text: t('feedback.action.seen'), priority: 1 },
        { status: 'resolved', text: t('feedback.action.resolve'), priority: 5 },
      ];
    case 'seen':
      return [{ status: 'resolved', text: t('feedback.action.resolve'), priority: 5 }];
    default:
      return [{ status: 'new', text: t('feedback.action.reopen'), priority: 5 }];
  }
}

/** A preview item whose preview is gone or expired: the place on its page cannot be opened any more. */
function previewGoneOf(current: FeedbackDetail): boolean {
  return current.target === 'preview' && current.currentVersion === null;
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

/** Whether the toolbar has anything in it: a reader of an item whose preview is gone has nothing to do. */
function hasActionsOf(current: FeedbackDetail): boolean {
  return props.canEdit || props.canAdmin || !previewGoneOf(current) || slots.actions !== undefined;
}

/** A sender without an account has no way to read a reply, and the team had better know. */
function replyHelpOf(current: FeedbackDetail): string {
  return current.author.kind === 'account'
    ? t('feedback.reply.help.account')
    : t('feedback.reply.help.noAccount');
}

</script>

<template>
  <nldd-container layout="stack" gap="16" data-testid="feedback-item">
    <ErrorBanner v-if="error" :error="error" />

    <nldd-inline-dialog
      v-else-if="!detail"
      variant="loading"
      :text="t('feedback.loading')"
      data-testid="feedback-sheet-loading"
    ></nldd-inline-dialog>

    <template v-else>
      <!-- What does not fit moves into the toolbar's own menu, by priority. -->
      <nldd-toolbar
        v-if="hasActionsOf(detail)"
        :ref="state.statusActions"
        size="sm"
        data-testid="feedback-actions"
      >
        <template v-if="canEdit">
          <nldd-toolbar-item
            v-for="action in nextStatusesOf(detail)"
            :key="action.status"
            slot="start"
            :priority="action.priority"
          >
            <nldd-button
              appearance="secondary"
              :text="action.text"
              :loading="statusBusy === action.status || undefined"
              :data-testid="`feedback-mark-${action.status}`"
              @click="setStatus(detail, action.status)"
            ></nldd-button>
            <nldd-menu-item slot="overflow" :text="action.text" @select="setStatus(detail, action.status)"></nldd-menu-item>
          </nldd-toolbar-item>
        </template>
        <!-- No button once the preview is gone: its address would only open
             "Niet gevonden". A line below says so. -->
        <nldd-toolbar-item v-if="!previewGoneOf(detail)" slot="end" priority="4">
          <nldd-button
            appearance="secondary"
            start-icon="open-new-page"
            :text="t('feedback.sheet.view')"
            :href="feedbackViewUrl(detail)"
            target="_blank"
            data-testid="feedback-view"
          ></nldd-button>
          <nldd-menu-item
            slot="overflow"
            icon="open-new-page"
            :text="t('feedback.sheet.view')"
            :href="feedbackViewUrl(detail)"
            target="_blank"
          ></nldd-menu-item>
        </nldd-toolbar-item>
        <slot name="actions"></slot>
        <nldd-toolbar-item v-if="canAdmin" slot="end" priority="2">
          <nldd-button
            appearance="secondary"
            :text="t('feedback.delete')"
            data-testid="feedback-delete"
            @click="askDelete(detail.id)"
          ></nldd-button>
          <nldd-menu-item
            slot="overflow"
            destructive
            :text="t('feedback.delete')"
            @select="askDelete(detail.id)"
          ></nldd-menu-item>
        </nldd-toolbar-item>
      </nldd-toolbar>

      <nldd-rich-text data-testid="feedback-comment">
        <FeedbackMarkdown :text="detail.comment" bare />
        <template v-if="detail.quote !== null">
          <p>
            <strong>{{ t('feedback.sheet.quote') }}</strong>
          </p>
          <blockquote class="feedback-item__text" data-testid="feedback-quote">{{ detail.quote }}</blockquote>
        </template>
      </nldd-rich-text>

      <nldd-rich-text v-if="previewGoneOf(detail)" data-testid="feedback-view-gone">
        <p>{{ t('feedback.sheet.view.gone', { ref: String(detail.previewRef) }) }}</p>
      </nldd-rich-text>
      <nldd-rich-text v-else-if="unmarkedOf(detail) !== null" data-testid="feedback-view-unmarked">
        <p>{{ unmarkedOf(detail) }}</p>
      </nldd-rich-text>

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
          <nldd-text-cell>{{ row.value }}<template v-if="row.unverified">&nbsp;<nldd-tag size="sm" :text="t('feedback.author.unverified')"></nldd-tag></template><span v-if="row.note" slot="supporting-text" :data-testid="`feedback-meta-${row.testid}-note`">{{ row.note }}<template v-if="row.link">{{ ' ' }}<nldd-link :href="row.link.href" target="_blank" :data-testid="`feedback-meta-${row.testid}-link`">{{ row.link.text }}</nldd-link></template></span></nldd-text-cell>
        </nldd-list-item>
      </nldd-list>

      <section aria-labelledby="heading-feedback-thread">
        <nldd-container layout="stack" gap="8">
          <nldd-title size="display-5">
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
                <FeedbackMarkdown :text="message.body" />
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
              :readonly="replyBusy || undefined"
              :invalid="replyEmpty || replyError !== null || undefined"
              :unmet="replyError !== null ? 'feedback-reply-server' : replyEmpty ? 'feedback-reply-required' : undefined"
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
        <nldd-visually-hidden v-if="replyError" role="alert" data-testid="feedback-reply-alert">
          {{ replyError }}
        </nldd-visually-hidden>
      </template>

      <nldd-rich-text v-else data-testid="feedback-read-only">
        <p>{{ t('feedback.readOnly') }}</p>
      </nldd-rich-text>

    </template>
  </nldd-container>

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
 * A quote keeps the line breaks and the spaces of the page it came from, and
 * a long address in it wraps rather than pushing a 320 px screen sideways
 * (WCAG 1.4.10).
 */
.feedback-item__text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>

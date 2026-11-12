<script setup lang="ts">
/**
 * The list half of the Feedback tab: the filters, one row per item, and what
 * the list says when it has nothing to show. It only shows; the tab loads and
 * keeps the items.
 *
 * A row is one button that opens the item beside the list. What the sender
 * wrote goes into the slots of the cell as text: the `text` attribute of a
 * cell reads `**` as bold, and a comment is not ours to format.
 */
import { computed, ref } from 'vue';

import { errorText } from '@/api/client';
import type { FeedbackCounts, FeedbackItem } from '@/api/types';
import SectionHeading from '@/components/SectionHeading.vue';
import {
  authorText,
  excerpt,
  nameUnverified,
  placeText,
  rowDetails,
  statusColor,
  statusLabel,
  type FeedbackSwitch,
  type InboxStatus,
  type NoButton,
} from '@/components/site/feedbackText';
import { t } from '@/i18n';

const props = defineProps<{
  items: FeedbackItem[];
  counts: FeedbackCounts;
  status: InboxStatus;
  /** `all`, `live`, `preview`, or `preview:` followed by the ref of one preview. */
  where: string;
  /**
   * The refs of the site's previews: empty when it has none, null when they
   * could not be read. Only the choice of one preview comes from these: the
   * live site and all previews are on offer without them, since items outlive
   * the preview they were given on.
   */
  previewRefs: string[] | null;
  /** The first page has arrived. */
  loaded: boolean;
  /** A page that replaces the list is on its way. */
  loading: boolean;
  /**
   * Why the last page that should have replaced the list did not come, or
   * null. The rows below belong to the filter before, so they make way.
   */
  failed: unknown;
  nextCursor: string | null;
  moreBusy: boolean;
  /** Whether the site takes feedback now. */
  enabled: boolean;
  /** Why no page carries the button although feedback is on, or null when one does. */
  noButton: NoButton | null;
  canAdmin: boolean;
}>();

const emit = defineEmits<{
  'update:status': [InboxStatus];
  'update:where': [string];
  open: [FeedbackItem];
  more: [];
  retry: [];
  configure: [FeedbackSwitch];
}>();

const section = ref<HTMLElement | null>(null);
const list = ref<HTMLElement | null>(null);

const open = computed(() => props.counts.new + props.counts.seen);
const total = computed(() => open.value + props.counts.resolved);

const STATUSES: readonly InboxStatus[] = ['open', 'resolved', 'all'];

function statusText(status: InboxStatus): string {
  // The counts cover the whole site: beside a list narrowed to one place they
  // would contradict the rows below them.
  if (props.where !== 'all') return t(`feedback.inbox.status.${status}.uncounted`);
  if (status === 'open') return t('feedback.inbox.status.open', { count: open.value });
  if (status === 'resolved') return t('feedback.inbox.status.resolved', { count: props.counts.resolved });
  return t('feedback.inbox.status.all', { count: total.value });
}

function pickStatus(event: Event): void {
  const detail = (event as CustomEvent<{ selected?: boolean; value?: string }>).detail;
  const value = detail?.value;
  if (!detail?.selected || !STATUSES.includes(value as InboxStatus) || value === props.status) return;
  emit('update:status', value as InboxStatus);
}

function pickWhere(event: Event): void {
  emit('update:where', (event.target as HTMLSelectElement).value);
}

/** Why no page carries the button, and for an admin the way out. */
function nowhereText(reason: NoButton): string {
  const why = t(`feedback.empty.nowhere.${reason}`);
  return props.canAdmin ? `${why} ${t(`feedback.nowhere.${reason}.admin`)}` : why;
}

/** After a row: on with the comma, the way the line goes on after the sender. */
function details(item: FeedbackItem): string {
  return `, ${rowDetails(item).join(', ')}`;
}

/**
 * Puts the focus on the row of this item, or on the first row when it is gone,
 * or on the heading of the list when no row is left: the filters are gone too
 * once the site has no feedback at all.
 */
function focusItem(id: string): void {
  const rows = [...(list.value?.querySelectorAll<HTMLElement>('nldd-list-item') ?? [])];
  const row = rows.find((candidate) => candidate.dataset.itemId === id) ?? rows[0];
  if (row) {
    row.focus();
    return;
  }
  focusHeading();
}

function focusHeading(): void {
  const heading = section.value?.querySelector<HTMLElement>('#heading-feedback-inbox');
  // A heading is no control: it takes the focus only from a script.
  heading?.setAttribute('tabindex', '-1');
  heading?.focus();
}

/**
 * Asks for the list again. The focus goes to the chosen status first: the
 * button pressed goes with the failure it sat in, while the status stays put,
 * outside what loads. Without the filters, the heading takes it.
 */
function retry(): void {
  const chosen = section.value?.querySelector<HTMLElement>(`[data-testid="feedback-status-${props.status}"]`);
  if (chosen) chosen.focus();
  else focusHeading();
  emit('retry');
}

defineExpose({ focusItem });
</script>

<template>
  <section ref="section" aria-labelledby="heading-feedback-inbox">
    <nldd-container layout="stack" gap="16">
      <SectionHeading
        id="heading-feedback-inbox"
        :text="t('feedback.inbox.heading')"
        :intro="t('feedback.inbox.intro')"
      />

      <!-- Outside the indicator below: it makes what it holds inert while a
           page loads, and the focus has to stay on the filter just chosen.
           Toggle buttons rather than a segmented control: they wrap onto a
           second row at 320 px, where a segment cuts its label short. -->
      <nldd-container v-if="loaded && total > 0" layout="stack" gap="8">
        <nldd-toggle-button-group
          type="radio"
          :accessible-label="t('feedback.inbox.status.label')"
          data-testid="feedback-status"
          @change="pickStatus"
        >
          <nldd-toggle-button
            v-for="choice in STATUSES"
            :key="choice"
            :value="choice"
            :text="statusText(choice)"
            :selected="choice === status || undefined"
            :data-testid="`feedback-status-${choice}`"
          ></nldd-toggle-button>
        </nldd-toggle-button-group>

        <!-- Also without a preview that still exists: the list can hold items
             from previews that are gone. -->
        <nldd-form-field :label="t('feedback.inbox.where.label')">
          <nldd-dropdown width="240px">
            <select
              :value="where"
              :aria-label="t('feedback.inbox.where.label')"
              data-testid="feedback-where"
              @change="pickWhere"
            >
              <option value="all">{{ t('feedback.inbox.where.all') }}</option>
              <option value="live">{{ t('feedback.inbox.where.live') }}</option>
              <option value="preview">{{ t('feedback.inbox.where.previews') }}</option>
              <option v-for="previewRef in previewRefs" :key="previewRef" :value="`preview:${previewRef}`">
                {{ t('feedback.inbox.where.preview', { ref: previewRef }) }}
              </option>
            </select>
          </nldd-dropdown>
        </nldd-form-field>
      </nldd-container>

      <nldd-inline-dialog
        v-if="!loaded"
        variant="loading"
        :text="t('feedback.loading')"
        data-testid="feedback-first-load"
      ></nldd-inline-dialog>

      <!-- Stays mounted and switches on complete: every remount restarts the
           second it waits before it shows itself. -->
      <nldd-activity-indicator v-else :text="t('feedback.loading')" :complete="!loading || undefined">
        <nldd-inline-dialog
          v-if="failed"
          variant="alert"
          :text="t('feedback.inbox.failed')"
          :supporting-text="errorText(failed, t('feedback.inbox.failed.detail'))"
          data-testid="feedback-list-failed"
        >
          <nldd-button
            slot="actions"
            appearance="secondary"
            :text="t('admin.retry')"
            data-testid="feedback-retry"
            @click="retry"
          ></nldd-button>
        </nldd-inline-dialog>

        <nldd-inline-dialog
          v-else-if="total === 0 && !enabled"
          icon="message-rectangle-text"
          :text="t('feedback.empty.off')"
          :supporting-text="canAdmin ? t('feedback.empty.off.admin') : t('feedback.empty.off.member')"
          data-testid="feedback-empty-off"
        >
          <nldd-button
            v-if="canAdmin"
            slot="actions"
            appearance="secondary"
            :text="t('feedback.empty.off.action')"
            data-testid="feedback-configure"
            @click="emit('configure', 'enabled')"
          ></nldd-button>
        </nldd-inline-dialog>

        <nldd-inline-dialog
          v-else-if="total === 0 && noButton"
          icon="message-rectangle-text"
          :text="t('feedback.empty.nowhere')"
          :supporting-text="nowhereText(noButton)"
          data-testid="feedback-empty-nowhere"
        >
          <!-- Only where a setting is the way out: a site without a version
               needs a publish, which no switch here does. -->
          <nldd-button
            v-if="canAdmin && noButton === 'previews'"
            slot="actions"
            appearance="secondary"
            :text="t('feedback.empty.off.action')"
            data-testid="feedback-configure-live"
            @click="emit('configure', 'live')"
          ></nldd-button>
        </nldd-inline-dialog>

        <nldd-inline-dialog
          v-else-if="total === 0"
          icon="message-rectangle-text"
          :text="t('feedback.empty.none')"
          :supporting-text="t('feedback.empty.none.detail')"
          data-testid="feedback-empty-none"
        ></nldd-inline-dialog>

        <nldd-inline-dialog
          v-else-if="items.length === 0"
          icon="filter"
          :text="t('feedback.empty.filter')"
          :supporting-text="t('feedback.empty.filter.detail')"
          data-testid="feedback-empty-filter"
        ></nldd-inline-dialog>

        <nldd-list
          v-else
          ref="list"
          :accessible-label="t('feedback.inbox.heading')"
          data-testid="feedback-list"
        >
          <nldd-list-item
            v-for="item in items"
            :key="item.id"
            button
            size="md"
            :data-item-id="item.id"
            :data-testid="`feedback-${item.id}`"
            @click="emit('open', item)"
          >
            <nldd-text-cell>
              <span slot="overline">{{ placeText(item) }}</span>
              {{ excerpt(item.comment) }}
              <!-- On one line: a line break in the template would cost the
                   spaces. The non-breaking one keeps the tag with the name. -->
              <span slot="supporting-text">{{ authorText(item.author) }}<template v-if="nameUnverified(item.author)">&nbsp;<nldd-tag size="sm" :text="t('feedback.author.unverified')"></nldd-tag></template>{{ details(item) }}</span>
            </nldd-text-cell>
            <nldd-spacer-cell size="12"></nldd-spacer-cell>
            <nldd-cell width="fit-content">
              <nldd-badge
                size="sm"
                :color="statusColor(item.status)"
                :text="statusLabel(item.status)"
                :data-testid="`feedback-badge-${item.id}`"
              ></nldd-badge>
            </nldd-cell>
            <nldd-spacer-cell size="8"></nldd-spacer-cell>
            <nldd-icon-cell size="20" icon="chevron-right"></nldd-icon-cell>
          </nldd-list-item>
        </nldd-list>
      </nldd-activity-indicator>

      <!-- Not while a reload failed: the cursor belongs to the list before. -->
      <nldd-button-group v-if="nextCursor && !failed" orientation="horizontal">
        <nldd-button
          appearance="secondary"
          :text="t('feedback.inbox.more')"
          :loading="moreBusy || undefined"
          data-testid="feedback-more"
          @click="emit('more')"
        ></nldd-button>
      </nldd-button-group>
    </nldd-container>
  </section>
</template>

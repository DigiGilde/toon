<script setup lang="ts">
/**
 * One feedback item beside the list, in a sheet: FeedbackItemView with a
 * title and a close button around it, and a way to the page of the item in
 * its toolbar. The
 * sheet keeps the state of the item (feedbackItem.ts) for as long as the tab
 * is open, so a reply typed and not sent outlasts the sheet closing.
 */
import { computed, ref } from 'vue';

import type { FeedbackDetail, FeedbackItem } from '@/api/types';
import FeedbackItemView from '@/components/site/FeedbackItemView.vue';
import Notices from '@/components/site/Notices.vue';
import { useFeedbackItem } from '@/components/site/feedbackItem';
import { feedbackItemHref, type FeedbackFilters } from '@/components/site/feedbackRoute';
import { pageLabel } from '@/components/site/feedbackText';
import { useInAppLink } from '@/composables/inAppLink';
import { useSheetOpen } from '@/composables/sheetOpen';
import { formatTimestamp } from '@/format';
import { t } from '@/i18n';

const props = defineProps<{
  group: string;
  site: string;
  /** The content origin, for the address of the version an item was given on. */
  contentBase: string;
  /** The item to show. Null closes the sheet. */
  item: FeedbackItem | null;
  /** The filters of the list, which the page of the item keeps for the way back. */
  filters: FeedbackFilters;
  canEdit: boolean;
  canAdmin: boolean;
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

const sheet = ref<HTMLElement & { show: () => void; hide: () => void }>();
const notices = ref<InstanceType<typeof Notices> | null>(null);
const follow = useInAppLink();

/** The item last opened. It stays while the sheet closes, so the closing sheet is not empty. */
const shown = ref<FeedbackItem | null>(null);

const state = useFeedbackItem({
  group: () => props.group,
  site: () => props.site,
  openId: () => props.item?.id ?? null,
  notices,
  updated: (detail) => emit('updated', detail),
  replied: (id) => emit('replied', id),
  removed: (id) => emit('removed', id),
});

// The sheet only opens with an item, so there is one to load.
useSheetOpen(
  () => props.item !== null,
  sheet,
  () => {
    shown.value = props.item;
    void state.load(props.item!.id);
  },
);

const page = computed(() => (shown.value ? pageLabel(shown.value.page) : ''));
const title = computed(() => t('feedback.sheet.title', { page: page.value }));
const label = computed(() =>
  shown.value
    ? t('feedback.sheet.label', { page: page.value, date: formatTimestamp(shown.value.createdAt) })
    : title.value,
);

function pageHrefOf(item: FeedbackItem): string {
  return feedbackItemHref(props.group, props.site, item.id, props.filters);
}

// The NLDD Vue types give nldd-button's click a CustomEvent, but it is the
// native MouseEvent of the anchor in its shadow root.
function openPage(event: Event): void {
  follow(event as MouseEvent, pageHrefOf(shown.value!));
}
</script>

<template>
  <Notices ref="notices" />

  <!-- Overlays belong on the document root (NLDD), see PublishSheet. -->
  <Teleport to="body">
    <!-- Wide enough for a screenshot to be read. Below the md breakpoint the
         sheet comes from the bottom at full width whatever this says. -->
    <nldd-sheet
      ref="sheet"
      placement="right"
      width="640px"
      :accessible-label="label"
      data-testid="feedback-sheet"
      @close="emit('close')"
    >
      <!-- nldd-page brings the scroller the sheet expects, as in PublishSheet. -->
      <nldd-page>
        <nldd-container v-if="shown" padding="24" gap="16">
          <nldd-title size="display-4">
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

          <FeedbackItemView
            :state="state"
            :group="group"
            :site="site"
            :content-base="contentBase"
            :can-edit="canEdit"
            :can-admin="canAdmin"
            :enabled="enabled"
            :live="live"
          >
            <template #actions>
              <nldd-toolbar-item slot="end" priority="3">
                <!-- A real link: a click with a modifier opens the page in a new
                     tab and leaves the sheet as it is. -->
                <nldd-button
                  :href="pageHrefOf(shown)"
                  appearance="secondary"
                  :text="t('feedback.sheet.openAsPage')"
                  data-testid="feedback-open-page"
                  @click="openPage"
                ></nldd-button>
                <nldd-menu-item slot="overflow" :href="pageHrefOf(shown)" :text="t('feedback.sheet.openAsPage')"></nldd-menu-item>
              </nldd-toolbar-item>
            </template>
          </FeedbackItemView>
        </nldd-container>
      </nldd-page>
    </nldd-sheet>
  </Teleport>
</template>

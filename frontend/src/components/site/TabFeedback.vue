<script setup lang="ts">
/**
 * Feedback tab of the site page: what visitors sent about the pages of this
 * site, one item at a time in a sheet beside the list, and whether and from
 * whom the site takes it.
 *
 * The settings sit above the list: a site admin changes them right there, and
 * for the readers and editors who make up most of a site team they are one
 * line that says where the site stands. The list is the work.
 *
 * The list stays as it was loaded while the items in it change: an item marked
 * resolved under "Open" keeps its row, with the new status, until the filter
 * changes. The focus then has a row to come back to when the sheet closes, and
 * nothing moves under the reader's eyes. The counts follow at once.
 *
 * The filters are in the address (feedbackRoute.ts): a link from the Previews
 * tab lands on the list of one preview, and a reload keeps what was chosen.
 * Choosing replaces the address rather than adding to the history, so the
 * back button leaves the list rather than undoing a choice.
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import * as toon from '@/api/toon';
import { errorText } from '@/api/client';
import type { FeedbackCounts, FeedbackDetail, FeedbackItem, Me, Preview, Site } from '@/api/types';
import ErrorBanner from '@/components/ErrorBanner.vue';
import FeedbackInbox from '@/components/site/FeedbackInbox.vue';
import FeedbackNotifications from '@/components/site/FeedbackNotifications.vue';
import FeedbackSettings from '@/components/site/FeedbackSettings.vue';
import FeedbackSheet from '@/components/site/FeedbackSheet.vue';
import Notices from '@/components/site/Notices.vue';
import { filtersOf, ONE_PREVIEW, queryOf, takeDeletedMark, type FeedbackFilters } from '@/components/site/feedbackRoute';
import { noButton, type InboxStatus } from '@/components/site/feedbackText';
import { fetchCurrentMember } from '@/composables/currentMember';
import { useLoader } from '@/composables/loader';
import { isSiteAdmin, isSiteEditor } from '@/composables/roles';
import { stayOnNextMove } from '@/composables/routeAnnouncer';
import { useSiteGroup } from '@/composables/siteGroup';
import { t } from '@/i18n';

// See TabOverview.vue for why inheritAttrs is off on every tab.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; site: string; contentBase: string }>();

const emit = defineEmits<{
  changed: [];
}>();

const route = useRoute();
const router = useRouter();
const siteGroup = useSiteGroup();
const notices = ref<InstanceType<typeof Notices> | null>(null);
const inbox = ref<InstanceType<typeof FeedbackInbox> | null>(null);

const canEdit = ref(false);
const canAdmin = ref(false);
/** The roles have arrived. Until then the settings say nothing rather than the wrong thing. */
const ready = ref(false);
/** The site's previews, read with the tab: null while they are not in, or when that read failed. */
const previews = ref<Preview[] | null>(null);
/** Whether a preview that still opens is open to anyone, so its feedback can come from the public. */
const publicPreview = ref(false);
/** Whether the site takes feedback, and on its live site too, as last saved. */
const enabled = ref(siteGroup.site.value.feedbackEnabled);
const live = ref(siteGroup.site.value.feedbackLive);
/** Why no page carries the button although feedback is on, for what the list says. */
const nowhere = computed(() =>
  noButton({ enabled: enabled.value, live: live.value }, siteGroup.site.value),
);

const filters = computed(() => filtersOf(route.query));

/**
 * A new choice of filter, in the address. The focus stays on the filter just
 * chosen: this is no move to another page for the route announcer.
 */
function choose(change: Partial<FeedbackFilters>): void {
  stayOnNextMove();
  void router.replace({ query: queryOf({ ...filters.value, ...change }) });
}

const status = computed<InboxStatus>({
  get: () => filters.value.status,
  set: (value) => choose({ status: value }),
});
const where = computed<string>({
  get: () => filters.value.where,
  set: (value) => choose({ where: value }),
});
/**
 * The refs of the previews for the place filter. The preview the list is
 * narrowed to stays on offer once it is gone, so the filter still says what
 * the list shows.
 */
const previewRefs = computed<string[] | null>(() => {
  if (previews.value === null) return null;
  const refs = previews.value.map((preview) => preview.ref);
  const chosen = where.value.startsWith(ONE_PREVIEW) ? where.value.slice(ONE_PREVIEW.length) : null;
  return chosen !== null && !refs.includes(chosen) ? [...refs, chosen] : refs;
});
const items = ref<FeedbackItem[]>([]);
const counts = ref<FeedbackCounts>({ new: 0, seen: 0, resolved: 0 });
const nextCursor = ref<string | null>(null);
const loaded = ref(false);
const moreBusy = ref(false);

/** The item open in the sheet, and the one last opened, for the focus to come back to. */
const opened = ref<FeedbackItem | null>(null);
let lastOpened = '';
/** Counts the pages that replaced the list, so a page of "more" for an older list is dropped. */
let generation = 0;

function query(): toon.FeedbackQuery {
  const filter: toon.FeedbackQuery = { status: status.value };
  if (where.value === 'live' || where.value === 'preview') {
    filter.target = where.value;
  } else if (where.value.startsWith(ONE_PREVIEW)) {
    filter.target = 'preview';
    filter.ref = where.value.slice(ONE_PREVIEW.length);
  }
  return filter;
}

/**
 * Whether the pages of a preview still open. The list keeps an expired
 * preview until the nightly cleanup, while the gate refuses it from the
 * moment it expires. The API sends no expiry for one that never expires.
 */
function stillOpens(preview: Preview, now: number): boolean {
  return !preview.expiresAt || Date.parse(preview.expiresAt) > now;
}

// Who is asking, and the previews for the place filter and the settings. The
// session is not a precondition: without it the tab only loses what needs a
// role. The previews are an extra: without them the filter and the settings
// leave out what they would add.
useLoader(
  async () => {
    const [member, found] = await Promise.all([
      fetchCurrentMember().catch(() => null),
      toon.previews(props.group, props.site).catch(() => null),
    ]);
    return { member: member as Me | null, found };
  },
  ({ member, found }) => {
    canEdit.value = isSiteEditor(member, props.group, props.site);
    canAdmin.value = isSiteAdmin(member, props.group, props.site);
    previews.value = found;
    const now = Date.now();
    publicPreview.value =
      found?.some((preview) => stillOpens(preview, now) && preview.accessOverride?.base === 'public') ?? false;
    enabled.value = siteGroup.site.value.feedbackEnabled;
    live.value = siteGroup.site.value.feedbackLive;
    ready.value = true;
  },
  () => [props.group, props.site],
);

const { loading, error, reload } = useLoader(
  () => toon.feedbackList(props.group, props.site, query()),
  (page) => {
    generation += 1;
    items.value = page.items;
    counts.value = page.counts;
    nextCursor.value = page.nextCursor;
    loaded.value = true;
  },
  () => [props.group, props.site, status.value, where.value],
);

// A list that fails to come back once the first one is there stays an inbox,
// with the failure in place of the rows (FeedbackInbox), so the filters and
// the focus stay where they were. That place is no live region, so a notice
// says it as well.
watch(error, (failure) => {
  if (failure === null || !loaded.value) return;
  notices.value?.notify('critical', t('feedback.inbox.failed'), errorText(failure, t('feedback.inbox.failed.detail')));
});

async function loadMore(): Promise<void> {
  const cursor = nextCursor.value;
  if (cursor === null || moreBusy.value) return;
  moreBusy.value = true;
  const list = generation;
  try {
    const page = await toon.feedbackList(props.group, props.site, { ...query(), cursor });
    if (list !== generation) return;
    items.value = [...items.value, ...page.items];
    counts.value = page.counts;
    nextCursor.value = page.nextCursor;
    // On to what was added, once it is on screen: that is where someone
    // pressing "Load more" was going.
    const first = page.items[0];
    await nextTick();
    if (first) inbox.value?.focusItem(first.id);
  } catch (f) {
    notices.value?.notify(
      'critical',
      t('feedback.inbox.more.failed'),
      errorText(f, t('feedback.inbox.more.failed.detail')),
    );
  } finally {
    moreBusy.value = false;
  }
}

function openItem(item: FeedbackItem): void {
  lastOpened = item.id;
  opened.value = item;
}

/**
 * The sheet says this twice: when its own close button is pressed, and once it
 * is gone. Behind the sheet the page is inert, so only the second one lands.
 */
function sheetClosed(): void {
  opened.value = null;
  inbox.value?.focusItem(lastOpened);
}

function moveCount(from: FeedbackItem['status'], to: FeedbackItem['status']): void {
  counts.value = { ...counts.value, [from]: counts.value[from] - 1, [to]: counts.value[to] + 1 };
}

function itemUpdated(detail: FeedbackDetail): void {
  const before = items.value.find((item) => item.id === detail.id);
  if (!before || before.status === detail.status) return;
  moveCount(before.status, detail.status);
  items.value = items.value.map((item) =>
    item.id === detail.id
      ? {
          ...item,
          status: detail.status,
          statusChangedAt: detail.statusChangedAt,
          statusChangedBy: detail.statusChangedBy,
        }
      : item,
  );
  // The count beside the name of the tab.
  emit('changed');
}

function replied(id: string): void {
  items.value = items.value.map((item) =>
    item.id === id ? { ...item, replyCount: item.replyCount + 1 } : item,
  );
}

function removed(id: string): void {
  const gone = items.value.find((item) => item.id === id);
  if (gone) counts.value = { ...counts.value, [gone.status]: counts.value[gone.status] - 1 };
  items.value = items.value.filter((item) => item.id !== id);
  opened.value = null;
  notices.value?.notify('success', t('feedback.delete.done'));
  emit('changed');
}

// Back from the page of an item that was deleted there: the list says so, as
// it does when the sheet deletes one.
onMounted(() => {
  if (takeDeletedMark(router)) notices.value?.notify('success', t('feedback.delete.done'));
});

function settingsSaved(site: Site): void {
  enabled.value = site.feedbackEnabled;
  live.value = site.feedbackLive;
  emit('changed');
}
</script>

<template>
  <Notices ref="notices" />

  <nldd-container layout="stack" gap="24">
    <FeedbackSettings
      v-if="ready"
      :group="group"
      :site="site"
      :can-admin="canAdmin"
      :public-preview="publicPreview"
      @saved="settingsSaved"
    />

    <FeedbackNotifications v-if="ready" :group="group" :site="site" :can-admin="canAdmin" />

    <!-- Only while nothing has loaded: after that a failure takes the place of
         the rows, and the filters stay to try again with. -->
    <ErrorBanner v-if="error && !loaded" :error="error" />

    <FeedbackInbox
      v-else
      ref="inbox"
      v-model:status="status"
      v-model:where="where"
      :items="items"
      :counts="counts"
      :preview-refs="previewRefs"
      :loaded="loaded"
      :loading="loading"
      :failed="error"
      :next-cursor="nextCursor"
      :more-busy="moreBusy"
      :enabled="enabled"
      :no-button="nowhere"
      :can-admin="canAdmin"
      @open="openItem"
      @more="loadMore"
      @retry="reload()"
    />
  </nldd-container>

  <FeedbackSheet
    :group="group"
    :site="site"
    :item="opened"
    :filters="filters"
    :can-edit="canEdit"
    :can-admin="canAdmin"
    :content-base="contentBase"
    :enabled="enabled"
    :live="live"
    @close="sheetClosed"
    @updated="itemUpdated"
    @replied="replied"
    @removed="removed"
  />
</template>

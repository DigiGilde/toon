<script setup lang="ts">
/**
 * Feedback tab of the site page: what visitors sent about the pages of this
 * site, one item at a time in a sheet beside the list, and whether and from
 * whom the site takes it.
 *
 * The list comes first. It is the work, and for the readers and editors who
 * make up most of a site team the settings are a single line.
 *
 * The list stays as it was loaded while the items in it change: an item marked
 * resolved under "Open" keeps its row, with the new status, until the filter
 * changes. The focus then has a row to come back to when the sheet closes, and
 * nothing moves under the reader's eyes. The counts follow at once.
 */
import { computed, nextTick, ref, watch } from 'vue';

import * as toon from '@/api/toon';
import { errorText } from '@/api/client';
import type { FeedbackCounts, FeedbackDetail, FeedbackItem, Me, Preview, Site } from '@/api/types';
import ErrorBanner from '@/components/ErrorBanner.vue';
import FeedbackInbox from '@/components/site/FeedbackInbox.vue';
import FeedbackSettings from '@/components/site/FeedbackSettings.vue';
import FeedbackSheet from '@/components/site/FeedbackSheet.vue';
import Notices from '@/components/site/Notices.vue';
import { noButton, type FeedbackSwitch, type InboxStatus } from '@/components/site/feedbackText';
import { fetchCurrentMember } from '@/composables/currentMember';
import { useLoader } from '@/composables/loader';
import { isSiteAdmin, isSiteEditor } from '@/composables/roles';
import { useSiteGroup } from '@/composables/siteGroup';
import { t } from '@/i18n';

// See TabOverview.vue for why inheritAttrs is off on every tab.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; site: string; contentBase: string }>();

const emit = defineEmits<{
  changed: [];
}>();

/** The value of the place filter that narrows the list to one preview, before its ref. */
const ONE_PREVIEW = 'preview:';

const siteGroup = useSiteGroup();
const notices = ref<InstanceType<typeof Notices> | null>(null);
const inbox = ref<InstanceType<typeof FeedbackInbox> | null>(null);
const settings = ref<InstanceType<typeof FeedbackSettings> | null>(null);

const canEdit = ref(false);
const canAdmin = ref(false);
/** The roles have arrived; until then the settings say nothing rather than the wrong thing. */
const ready = ref(false);
/**
 * The refs of the site's previews for the place filter, as read with the list:
 * empty without any, null while no read has worked.
 */
const previewRefs = ref<string[] | null>(null);
/**
 * Those whose pages still open, as read with the list on screen: the way to an
 * item's place goes nowhere on the others. Null when that read failed.
 */
const reachablePreviews = ref<string[] | null>(null);
/** Whether a preview that still opens is open to anyone, so its feedback can come from the public. */
const publicPreview = ref(false);
/** The ref of the preview updated last: a page that carries the button while the live site does not. */
const newestPreview = ref<string | null>(null);
/** Whether the site takes feedback, and on its live site too, as last saved. */
const enabled = ref(siteGroup.site.value.feedbackEnabled);
const live = ref(siteGroup.site.value.feedbackLive);
/** Why no page carries the button although feedback is on, for what the list says. */
const nowhere = computed(() =>
  noButton({ enabled: enabled.value, live: live.value }, siteGroup.site.value),
);

const status = ref<InboxStatus>('open');
const where = ref('all');
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
 * The ref of the preview updated last, or null without previews. The API
 * lists them by ref, not by when they were last updated.
 */
function newestRef(previews: Preview[] | null): string | null {
  const newest = [...(previews ?? [])].sort((a, b) => Date.parse(b.lastUpdatedAt) - Date.parse(a.lastUpdatedAt))[0];
  return newest?.ref ?? null;
}

/**
 * Whether the pages of a preview still open. The list keeps an expired
 * preview until the nightly cleanup, while the gate refuses it from the
 * moment it expires. The API sends no expiry for one that never expires.
 */
function stillOpens(preview: Preview, now: number): boolean {
  return !preview.expiresAt || Date.parse(preview.expiresAt) > now;
}

/**
 * The previews of the site, or null when they cannot be read. Always asked:
 * the count on the site was read with the page, and a preview deployed since
 * is not in it.
 */
function readPreviews(): Promise<Preview[] | null> {
  return toon.previews(props.group, props.site).catch(() => null);
}

/**
 * What the previews read with the list say: the places the filter offers, and
 * which previews still open for "Bekijk op de pagina". A read that fails keeps
 * the filter as it was and leaves the way to the page in place.
 */
function showPreviews(previews: Preview[] | null): void {
  const now = Date.now();
  reachablePreviews.value =
    previews?.filter((preview) => stillOpens(preview, now)).map((preview) => preview.ref) ?? null;
  if (previews === null) return;
  const refs = previews.map((preview) => preview.ref);
  // The preview the list is narrowed to stays on offer once it is gone, so the
  // filter still says what the list shows.
  const chosen = where.value.startsWith(ONE_PREVIEW) ? where.value.slice(ONE_PREVIEW.length) : null;
  previewRefs.value = chosen !== null && !refs.includes(chosen) ? [...refs, chosen] : refs;
}

// Who is asking, and the previews the settings point to. The session is not a
// precondition: without it the tab only loses what needs a role. The previews
// are an extra: without them the settings leave out what they would add.
useLoader(
  async () => {
    const [member, previews] = await Promise.all([fetchCurrentMember().catch(() => null), readPreviews()]);
    return { member: member as Me | null, previews };
  },
  ({ member, previews }) => {
    canEdit.value = isSiteEditor(member, props.group, props.site);
    canAdmin.value = isSiteAdmin(member, props.group, props.site);
    const now = Date.now();
    const reachable = previews?.filter((preview) => stillOpens(preview, now)) ?? null;
    publicPreview.value = reachable?.some((preview) => preview.accessOverride?.base === 'public') ?? false;
    newestPreview.value = newestRef(previews);
    enabled.value = siteGroup.site.value.feedbackEnabled;
    live.value = siteGroup.site.value.feedbackLive;
    ready.value = true;
  },
  () => [props.group, props.site],
);

// The previews are read again with every list, and only after it: every item
// in the list came in before that read, so a preview it lacks is gone rather
// than too new to be in it.
const { loading, error, reload } = useLoader(
  async () => {
    const page = await toon.feedbackList(props.group, props.site, query());
    return { page, previews: await readPreviews() };
  },
  ({ page, previews }) => {
    generation += 1;
    items.value = page.items;
    counts.value = page.counts;
    nextCursor.value = page.nextCursor;
    loaded.value = true;
    showPreviews(previews);
    // The list is in, and a next page that failed belonged to the list it replaces.
    listFailure = dropFailure(listFailure);
    moreFailure = dropFailure(moreFailure);
  },
  () => [props.group, props.site, status.value, where.value],
);

/**
 * The notices of the last list and the last next page that failed to load. A
 * critical notice stays until it is dismissed, so each failure said again
 * takes the place of the one before rather than piling up beside it, and the
 * notice goes once what failed has come in after all.
 */
let listFailure: number | undefined;
let moreFailure: number | undefined;

/** Says a failure in a new critical notice, in place of `previous`, and returns its id. */
function sayFailure(previous: number | undefined, text: string, detail: string): number | undefined {
  if (previous !== undefined) notices.value?.dismiss(previous);
  return notices.value?.notify('critical', text, detail);
}

/** Takes the notice of a failure away, when there is one. */
function dropFailure(previous: number | undefined): undefined {
  if (previous !== undefined) notices.value?.dismiss(previous);
  return undefined;
}

// A list that fails to come back once the first one is there stays an inbox,
// with the failure in place of the rows (FeedbackInbox), so the filters and
// the focus stay where they were. That place is no live region, so a notice
// says it as well.
watch(error, (failure) => {
  if (failure === null || !loaded.value) return;
  listFailure = sayFailure(
    listFailure,
    t('feedback.inbox.failed'),
    errorText(failure, t('feedback.inbox.failed.detail')),
  );
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
    moreFailure = dropFailure(moreFailure);
    // On to what was added, once it is on screen: that is where someone
    // pressing "Meer laden" was going.
    const first = page.items[0];
    await nextTick();
    if (first) inbox.value?.focusItem(first.id);
  } catch (f) {
    moreFailure = sayFailure(
      moreFailure,
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

function settingsSaved(site: Site): void {
  enabled.value = site.feedbackEnabled;
  live.value = site.feedbackLive;
  emit('changed');
}

function configure(which: FeedbackSwitch): void {
  settings.value?.focusSwitch(which);
}
</script>

<template>
  <Notices ref="notices" />

  <nldd-container layout="stack" gap="24">
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
      @configure="configure"
    />

    <FeedbackSettings
      v-if="ready"
      ref="settings"
      :group="group"
      :site="site"
      :content-base="contentBase"
      :can-admin="canAdmin"
      :newest-preview="newestPreview"
      :public-preview="publicPreview"
      @saved="settingsSaved"
    />
  </nldd-container>

  <FeedbackSheet
    :group="group"
    :site="site"
    :item="opened"
    :can-edit="canEdit"
    :can-admin="canAdmin"
    :reachable-previews="reachablePreviews"
    :enabled="enabled"
    :live="live"
    @close="sheetClosed"
    @updated="itemUpdated"
    @replied="replied"
    @removed="removed"
  />
</template>

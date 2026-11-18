<script setup lang="ts">
/**
 * One feedback item on a page of its own (`/{group}/{site}/feedback/{id}`), at
 * the width of the page: what the sheet beside the list shows, with a way back
 * to the list as it was filtered. The address can be kept and passed on.
 *
 * An id that is no item of this site, whatever the reason, gets the same
 * answer as one that is no id at all: the page says nothing about items that
 * exist elsewhere.
 */
import { computed, ref, watch, watchEffect } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { ApiError } from '@/api/client';
import type { FeedbackDetail, Me } from '@/api/types';
import ErrorBanner from '@/components/ErrorBanner.vue';
import SectionHeading from '@/components/SectionHeading.vue';
import FeedbackItemView from '@/components/site/FeedbackItemView.vue';
import Notices from '@/components/site/Notices.vue';
import { useFeedbackItem } from '@/components/site/feedbackItem';
import { feedbackListHref, filtersOf, markDeleted } from '@/components/site/feedbackRoute';
import { pageLabel } from '@/components/site/feedbackText';
import { fetchCurrentMember } from '@/composables/currentMember';
import { useInAppLink } from '@/composables/inAppLink';
import { useLoader } from '@/composables/loader';
import { isSiteAdmin, isSiteEditor } from '@/composables/roles';
import { useSiteGroup } from '@/composables/siteGroup';
import { t } from '@/i18n';
import { setDocumentTitle } from '@/title';

// See TabOverview.vue for why inheritAttrs is off on every tab.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; site: string; contentBase: string }>();

const emit = defineEmits<{
  changed: [];
}>();

/** The ids the API hands out. Anything else is no item, without asking. */
const ITEM_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const route = useRoute();
const router = useRouter();
const follow = useInAppLink();
const siteGroup = useSiteGroup();
const notices = ref<InstanceType<typeof Notices> | null>(null);

const itemId = computed(() => {
  const id = String(route.params.item ?? '');
  return ITEM_ID.test(id) ? id : null;
});
const backHref = computed(() => feedbackListHref(props.group, props.site, filtersOf(route.query)));

/** Set once the item turns out not to be one of this site. */
const notFound = ref<ApiError | null>(null);

function itemNotFound(): ApiError {
  return new ApiError({
    type: 'about:blank',
    title: t('feedback.page.notFound.title'),
    status: 404,
    detail: t('feedback.page.notFound.detail', { site: `${props.group}/${props.site}` }),
  });
}

const state = useFeedbackItem({
  group: () => props.group,
  site: () => props.site,
  openId: () => itemId.value,
  notices,
  // Another item, a gone one included, is the same "not here" as no id at all.
  failed: (reason) => {
    if (reason instanceof ApiError && reason.problem.status === 404) notFound.value = itemNotFound();
  },
  // The count beside the name of the tab.
  updated: () => emit('changed'),
  // Back to the list it came from, which says the item is gone.
  removed: () => {
    emit('changed');
    void markDeleted(router, backHref.value);
  },
});

// Each item the address names starts afresh: the page stays when only the id changes.
watch(
  itemId,
  (id) => {
    notFound.value = id === null ? itemNotFound() : null;
    if (id !== null) void state.load(id);
  },
  { immediate: true },
);

const canEdit = ref(false);
const canAdmin = ref(false);
/** The roles have arrived. Until then the item waits, rather than offering the wrong actions. */
const ready = ref(false);

useLoader(
  async () => (await fetchCurrentMember().catch(() => null)) as Me | null,
  (member) => {
    canEdit.value = isSiteEditor(member, props.group, props.site);
    canAdmin.value = isSiteAdmin(member, props.group, props.site);
    ready.value = true;
  },
  () => [props.group, props.site],
);

const detail = state.detail;

function titleOf(item: FeedbackDetail): string {
  return t('feedback.sheet.title', { page: pageLabel(item.page) });
}

// The name of the item first, then the site, as every page of a site has it.
watchEffect(() => {
  const siteName = siteGroup.site.value.title || props.site;
  if (notFound.value) setDocumentTitle(notFound.value.problem.title, siteName);
  else if (detail.value) setDocumentTitle(titleOf(detail.value), siteName);
});
</script>

<template>
  <Notices ref="notices" />

  <nldd-container layout="stack" gap="16">
    <nldd-link
      :href="backHref"
      start-icon="arrow-left"
      data-testid="feedback-page-back"
      @click="follow($event, backHref)"
      >{{ t('feedback.page.back') }}</nldd-link
    >

    <ErrorBanner v-if="notFound" :error="notFound" />

    <template v-else-if="ready">
      <!-- Only once the item is there: the route announcer puts the focus on
           this heading, and it has to name the item. -->
      <SectionHeading v-if="detail" id="heading-feedback-item" :text="titleOf(detail)" />
      <FeedbackItemView
        :state="state"
        :group="group"
        :site="site"
        :content-base="contentBase"
        :can-edit="canEdit"
        :can-admin="canAdmin"
        :enabled="siteGroup.site.value.feedbackEnabled"
        :live="siteGroup.site.value.feedbackLive"
      />
    </template>

    <nldd-inline-dialog v-else variant="loading" :text="t('feedback.loading')"></nldd-inline-dialog>
  </nldd-container>
</template>

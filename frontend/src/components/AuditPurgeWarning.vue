<script setup lang="ts">
/**
 * Warns a platform administrator on every page but the platform page that
 * the audit purge is paused. Only for platform administrators: the endpoint
 * refuses everyone else. It sits in the shell, outside the router view, so a
 * navigation does not insert it anew and its live region stays quiet until
 * the status itself changes.
 */
import { computed, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { auditPurgeHoldStatus, loadAuditPurgeHold } from '@/composables/auditPurgeHold';
import { currentMemberState, isPlatformAdmin } from '@/composables/currentMember';
import { formatTimestamp } from '@/format';
import { t } from '@/i18n';

const PLATFORM_PATH = '/-/platform';

const route = useRoute();
const router = useRouter();
const { member } = currentMemberState();
const status = auditPurgeHoldStatus();
const admin = computed(() => isPlatformAdmin(member.value));

// Once per session that turns out to be a platform administrator's. A failed
// read is not this warning's to report: the platform page says so itself.
watch(
  admin,
  (isAdmin) => {
    if (isAdmin) void loadAuditPurgeHold().catch(() => undefined);
  },
  { immediate: true },
);

const hold = computed(() =>
  admin.value && route.name !== 'platform' ? (status.value?.hold ?? null) : null,
);

/** A real link, yet a plain left click stays inside the SPA. */
function follow(event: MouseEvent): void {
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
    return;
  }
  event.preventDefault();
  void router.push(PLATFORM_PATH);
}
</script>

<template>
  <nldd-simple-section v-if="hold" padding-bottom="0" data-testid="audit-purge-warning">
    <nldd-banner
      variant="warning"
      size="sm"
      :text="t('admin.purge.warning', { date: formatTimestamp(hold.startedAt) })"
    >
      <nldd-button
        slot="actions"
        appearance="secondary"
        :href="PLATFORM_PATH"
        :text="t('admin.purge.warning.link', { page: t('nav.platform') })"
        data-testid="audit-purge-warning-link"
        @click="follow"
      ></nldd-button>
    </nldd-banner>
  </nldd-simple-section>
</template>

<script setup lang="ts">
/**
 * The pause switch for the nightly purge of expired audit rows. Shown to
 * platform administrators on the platform administration page; the endpoints
 * refuse everyone else.
 *
 * One field and one button serve both states: pausing and resuming ask for the
 * same thing (a reason, then a confirmation), so the button that opened the
 * dialog is still there when it closes.
 */
import { computed, onMounted, ref, useId } from 'vue';

import { ApiError } from '@/api/client';
import { endAuditPurgeHold, startAuditPurgeHold } from '@/api/toon';
import ConfirmModal from '@/components/ConfirmModal.vue';
import ErrorBanner from '@/components/ErrorBanner.vue';
import SectionHeading from '@/components/SectionHeading.vue';
import {
  auditPurgeHoldStatus,
  loadAuditPurgeHold,
  setAuditPurgeHold,
} from '@/composables/auditPurgeHold';
import { formatTimestamp } from '@/format';
import { t } from '@/i18n';

/** The same shape the API refuses in a reason. */
const EMAIL_SHAPE = /[^\s@]+@[^\s@]+\.[^\s@]+/;

type Mode = 'pause' | 'resume';

const status = auditPurgeHoldStatus();
const loading = ref(true);
const loadError = ref<unknown>(null);
const actionError = ref<unknown>(null);

const reason = ref('');
const reasonEmpty = ref(false);
const reasonUnmetId = `audit-purge-reason-short-${useId()}`;
const reasonEmailId = `audit-purge-reason-email-${useId()}`;
const reasonRefusedId = `audit-purge-reason-refused-${useId()}`;
/** The server's refusal of the reason as it is typed; empty once it changes. */
const reasonRefusal = ref('');
const reasonHasEmail = computed(() => EMAIL_SHAPE.test(reason.value));
const mode = ref<Mode>('pause');
const asking = ref(false);
const busy = ref(false);
/** Said once after a resume, because the status sentence is gone by then. */
const resumed = ref(false);

const field = ref<HTMLElement | null>(null);
const action = ref<HTMLElement | null>(null);

async function load(): Promise<void> {
  try {
    await loadAuditPurgeHold();
    loadError.value = null;
  } catch (error) {
    loadError.value = error;
  } finally {
    loading.value = false;
  }
}

function retry(): void {
  loading.value = true;
  loadError.value = null;
  void load();
}

onMounted(load);

function inputValue(event: Event): string {
  return (
    (event as CustomEvent<{ value?: string }>).detail?.value ?? (event.target as HTMLInputElement).value
  );
}

function edit(event: Event): void {
  reason.value = inputValue(event);
  reasonRefusal.value = '';
  if (reasonEmpty.value && reason.value.trim().length > 0) {
    reasonEmpty.value = false;
  }
}

function ask(): void {
  if (reasonHasEmail.value || reasonRefusal.value) {
    field.value?.focus();
    return;
  }
  if (reason.value.trim().length === 0) {
    reasonEmpty.value = true;
    field.value?.focus();
    return;
  }
  mode.value = status.value?.onHold ? 'resume' : 'pause';
  actionError.value = null;
  asking.value = true;
}

/**
 * What waits for the dialog to be gone. The page behind a modal is inert until
 * then, so a focus move made earlier is lost.
 */
let afterClose: (() => void) | null = null;

function closed(): void {
  asking.value = false;
  const next = afterClose;
  afterClose = null;
  next?.();
}

/** A refusal of the reason itself, which belongs next to the field. */
function refusesReason(error: unknown): error is ApiError {
  return (
    error instanceof ApiError &&
    error.problem.status === 422 &&
    (error.problem.code ?? '').startsWith('HOLD_REASON_')
  );
}

async function confirm(): Promise<void> {
  busy.value = true;
  const resuming = mode.value === 'resume';
  try {
    setAuditPurgeHold(
      resuming
        ? await endAuditPurgeHold(reason.value.trim())
        : await startAuditPurgeHold(reason.value.trim()),
    );
    reason.value = '';
    resumed.value = resuming;
    afterClose = () => action.value?.focus();
  } catch (error) {
    if (refusesReason(error)) {
      reasonRefusal.value = error.problem.detail ?? error.problem.title;
      afterClose = () => field.value?.focus();
      return;
    }
    actionError.value = error;
    // Someone else changed the switch meanwhile: show what it is now.
    const conflict = error instanceof ApiError && error.problem.status === 409;
    if (conflict) await load();
    // The reason stays as typed, so a refused one can be corrected.
    afterClose = () => (conflict ? action.value : field.value)?.focus();
  } finally {
    busy.value = false;
    asking.value = false;
  }
}
</script>

<template>
  <section aria-labelledby="audit-purge-heading" data-testid="audit-purge">
    <SectionHeading id="audit-purge-heading" :size="2" :text="t('admin.purge.heading')" />

    <nldd-spacer size="16"></nldd-spacer>

    <nldd-activity-indicator
      v-if="loading"
      show-text
      :text="t('admin.purge.loading')"
    ></nldd-activity-indicator>

    <nldd-container v-else-if="loadError" layout="stack" gap="16">
      <ErrorBanner :error="loadError" />
      <nldd-button
        appearance="secondary"
        :text="t('admin.retry')"
        data-testid="audit-purge-retry"
        @click="retry"
      ></nldd-button>
    </nldd-container>

    <nldd-container v-else layout="stack" gap="16">
      <!-- Always in the page, so that what lands in it is announced. -->
      <div role="status" data-testid="audit-purge-status">
        <nldd-rich-text v-if="status?.hold">
          <p>
            {{
              t('admin.purge.paused', {
                date: formatTimestamp(status.hold.startedAt),
                name: status.hold.startedByName,
              })
            }}
          </p>
          <p class="reason">{{ t('admin.purge.paused.reason', { reason: status.hold.reason }) }}</p>
        </nldd-rich-text>
        <nldd-rich-text v-else-if="resumed">
          <p>{{ t('admin.purge.resumed') }}</p>
        </nldd-rich-text>
      </div>

      <nldd-rich-text v-if="!status?.onHold">
        <p>{{ t('admin.purge.running') }}</p>
      </nldd-rich-text>

      <div role="alert">
        <ErrorBanner v-if="actionError" :error="actionError" data-testid="audit-purge-error" />
      </div>

      <nldd-form @submit.prevent="ask">
        <nldd-form-field
          :label="t(status?.onHold ? 'admin.purge.reason.label.resume' : 'admin.purge.reason.label.pause')"
          :supporting-label="t('admin.purge.reason.hint')"
        >
          <nldd-multi-line-text-field
            ref="field"
            input-id="audit-purge-reason"
            name="reason"
            :rows="3"
            :value="reason"
            :invalid="reasonHasEmail || reasonRefusal !== '' || reasonEmpty || undefined"
            :unmet="
              reasonHasEmail
                ? reasonEmailId
                : reasonRefusal
                  ? reasonRefusedId
                  : reasonEmpty
                    ? reasonUnmetId
                    : undefined
            "
            data-testid="audit-purge-reason"
            @input="edit"
          ></nldd-multi-line-text-field>
          <nldd-validation-list>
            <nldd-validation-item v-if="reasonHasEmail" :id="reasonEmailId">
              {{ t('admin.purge.reason.email') }}
            </nldd-validation-item>
            <nldd-validation-item v-else-if="reasonRefusal" :id="reasonRefusedId">
              {{ reasonRefusal }}
            </nldd-validation-item>
            <nldd-validation-item v-else :id="reasonUnmetId">
              {{ t('admin.purge.reason.empty') }}
            </nldd-validation-item>
          </nldd-validation-list>
        </nldd-form-field>

        <nldd-form-actions>
          <nldd-button-group>
            <nldd-button
              ref="action"
              type="submit"
              appearance="primary"
              :text="status?.onHold ? t('admin.purge.resume.action') : t('admin.purge.pause.action')"
              data-testid="audit-purge-action"
            ></nldd-button>
          </nldd-button-group>
        </nldd-form-actions>
      </nldd-form>
    </nldd-container>

    <ConfirmModal
      :open="asking"
      :title="t(`admin.purge.${mode}.title`)"
      :text="t(`admin.purge.${mode}.text`)"
      :keep-label="t(`admin.purge.${mode}.keep`)"
      :confirm-label="t(`admin.purge.${mode}.action`)"
      :confirm-variant="mode === 'resume' ? 'destructive' : 'secondary'"
      :busy="busy"
      @confirm="confirm"
      @close="closed"
    />
  </section>
</template>

<style scoped>
.reason {
  white-space: pre-line;
}
</style>

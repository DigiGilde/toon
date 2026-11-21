<script setup lang="ts">
/**
 * The Mattermost channel of a group or a site, for the admins who manage it:
 * where posts about new feedback go, a field to set the webhook and a button
 * to remove it. With a `site` it is the channel of that site, without one the
 * channel of the group.
 *
 * The URL of a webhook is a secret, so the field is write-only: the API says
 * which host the posts go to, never the URL, and the field is empty after a
 * save. Nothing is drawn while the installation allows no webhooks.
 */
import { computed, onMounted, ref } from 'vue';

import * as toon from '@/api/toon';
import { errorText } from '@/api/client';
import type { FeedbackWebhook, MemberLanguage } from '@/api/types';
import ConfirmModal from '@/components/ConfirmModal.vue';
import SectionHeading from '@/components/SectionHeading.vue';
import Notices from '@/components/site/Notices.vue';
import { useConfirm } from '@/composables/confirm';
import { currentLocale, t } from '@/i18n';

const props = withDefaults(defineProps<{ group: string; site?: string }>(), { site: '' });

const LANGUAGES: readonly MemberLanguage[] = ['nl', 'en'];

const notices = ref<InstanceType<typeof Notices> | null>(null);
const webhook = ref<FeedbackWebhook | null>(null);
const url = ref('');
/** The language of the posts: what the channel has, else the one the interface is in. */
const language = ref<MemberLanguage>(currentLocale.value);
const saving = ref(false);

const onSite = computed(() => props.site !== '');

function read(): Promise<FeedbackWebhook> {
  return onSite.value
    ? toon.siteFeedbackWebhook(props.group, props.site)
    : toon.groupFeedbackWebhook(props.group);
}

async function load(): Promise<void> {
  try {
    const loaded = await read();
    language.value = loaded.language ?? language.value;
    webhook.value = loaded;
  } catch (f) {
    notices.value?.notify(
      'critical',
      t('notifications.webhook.loadFailed'),
      errorText(f, t('notifications.webhook.loadFailed.detail')),
    );
  }
}

onMounted(load);

function languageName(code: MemberLanguage | null): string {
  return t(`language.${code as MemberLanguage}`);
}

/** Where the posts go now, in one sentence. */
const status = computed(() => {
  const current = webhook.value as FeedbackWebhook;
  if (current.configured) {
    return t('notifications.webhook.status.own', {
      host: String(current.host),
      language: languageName(current.language),
    });
  }
  if (current.groupHost !== null) {
    return t('notifications.webhook.status.group', { host: current.groupHost });
  }
  return t('notifications.webhook.status.none');
});

function inputValue(event: Event): string {
  return (
    (event as CustomEvent<{ value?: string }>).detail?.value ?? (event.target as HTMLInputElement).value
  );
}

async function save(): Promise<void> {
  const typed = url.value.trim();
  if (typed === '' || saving.value) return;
  saving.value = true;
  try {
    const saved = onSite.value
      ? await toon.setSiteFeedbackWebhook(props.group, props.site, typed, language.value)
      : await toon.setGroupFeedbackWebhook(props.group, typed, language.value);
    webhook.value = saved;
    url.value = '';
    notices.value?.notify(
      'success',
      t('notifications.webhook.saved'),
      t('notifications.webhook.saved.detail', { host: String(saved.host) }),
    );
  } catch (f) {
    notices.value?.notify(
      'critical',
      t('notifications.webhook.failed'),
      errorText(f, t('notifications.webhook.failed.detail')),
    );
  } finally {
    saving.value = false;
  }
}

async function remove(): Promise<void> {
  try {
    if (onSite.value) await toon.deleteSiteFeedbackWebhook(props.group, props.site);
    else await toon.deleteGroupFeedbackWebhook(props.group);
    // On a site the group's channel may take over: ask again rather than guess.
    await load();
    notices.value?.notify('success', t('notifications.webhook.removed'));
  } catch (f) {
    notices.value?.notify(
      'critical',
      t('notifications.webhook.removeFailed'),
      errorText(f, t('notifications.webhook.removeFailed.detail')),
    );
  }
}

const { open: removeOpen, busy: removeBusy, ask: askRemove, cancel: cancelRemove, confirm: confirmRemove } =
  useConfirm<true>(remove);
</script>

<template>
  <Notices ref="notices" />

  <section
    v-if="webhook?.available"
    aria-labelledby="heading-feedback-webhook"
    data-testid="feedback-webhook"
  >
    <nldd-container layout="stack" gap="8">
      <SectionHeading
        id="heading-feedback-webhook"
        :text="t('notifications.webhook.heading')"
        :intro="t('notifications.webhook.intro')"
      />

      <nldd-rich-text data-testid="feedback-webhook-status">
        <p>{{ status }}</p>
      </nldd-rich-text>

      <nldd-banner
        variant="warning"
        :text="t('notifications.webhook.warning')"
        :supporting-text="t('notifications.webhook.warning.detail')"
        data-testid="feedback-webhook-warning"
      ></nldd-banner>

      <nldd-form data-testid="feedback-webhook-form" @submit.prevent="save">
        <nldd-form-field :label="t('notifications.webhook.url.label')">
          <nldd-password-field
            name="feedback-webhook-url"
            autocomplete="off"
            :value="url"
            :disabled="saving || undefined"
            data-testid="feedback-webhook-url"
            @input="url = inputValue($event)"
          ></nldd-password-field>
          <nldd-form-field-help-text>
            {{ t('notifications.webhook.url.hint') }}
            <template v-if="webhook.configured">{{ t('notifications.webhook.url.replace') }}</template>
          </nldd-form-field-help-text>
        </nldd-form-field>

        <nldd-list
          type="radiogroup"
          appearance="box-base"
          :accessible-label="t('notifications.webhook.language.label')"
          data-testid="feedback-webhook-language"
        >
          <nldd-list-item
            v-for="code in LANGUAGES"
            :key="code"
            radio
            size="md"
            :checked="code === language || undefined"
            :disabled="saving || undefined"
            :data-testid="`feedback-webhook-language-${code}`"
            @change="language = code"
          >
            <!-- The row is the radio itself. The button only draws the shape
                 (decorative), otherwise there is a control inside a control. -->
            <nldd-cell width="fit-content">
              <nldd-radio-button decorative :checked="code === language || undefined"></nldd-radio-button>
            </nldd-cell>
            <nldd-spacer-cell size="12"></nldd-spacer-cell>
            <nldd-title-cell size="display-6" :text="languageName(code)"></nldd-title-cell>
          </nldd-list-item>
        </nldd-list>

        <nldd-form-actions>
          <nldd-button-group orientation="horizontal">
            <nldd-button
              appearance="primary"
              type="submit"
              :text="t('notifications.webhook.save')"
              :disabled="saving || url.trim() === '' || undefined"
              data-testid="feedback-webhook-save"
            ></nldd-button>
            <nldd-button
              v-if="webhook.configured"
              appearance="destructive"
              :text="t('notifications.webhook.remove')"
              :disabled="saving || undefined"
              data-testid="feedback-webhook-remove"
              @click="askRemove(true)"
            ></nldd-button>
          </nldd-button-group>
        </nldd-form-actions>
      </nldd-form>

      <ConfirmModal
        :open="removeOpen"
        :title="t('notifications.webhook.confirm.title')"
        :text="t('notifications.webhook.confirm.text')"
        :keep-label="t('notifications.webhook.confirm.keep')"
        :confirm-label="t('notifications.webhook.confirm.confirm')"
        :busy="removeBusy"
        @confirm="confirmRemove"
        @close="cancelRemove"
      />
    </nldd-container>
  </section>
</template>

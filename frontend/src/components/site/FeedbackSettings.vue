<script setup lang="ts">
/**
 * The settings of the Feedback tab: whether the pages of the site carry the
 * feedback button, who may use it, and whether the live site does too.
 *
 * A section at the top of the tab. A site admin changes the settings right
 * there, each on change, as on the Access tab. Everyone else reads one line
 * that says where the site stands. Turned off, the two other choices are not
 * greyed out but gone: a control that cannot be used is noise, and the line
 * under the switch says what turning it on brings. The three travel together
 * in one call, so the site is never on a mix of the old and the new value.
 */
import { computed, ref } from 'vue';

import * as toon from '@/api/toon';
import { errorText } from '@/api/client';
import type { FeedbackIdentity, FeedbackSettings, Site } from '@/api/types';
import { FEEDBACK_IDENTITY_VALUES } from '@/api/types';
import SectionHeading from '@/components/SectionHeading.vue';
import Notices from '@/components/site/Notices.vue';
import { noButton } from '@/components/site/feedbackText';
import { useSiteGroup } from '@/composables/siteGroup';
import { t } from '@/i18n';

const props = defineProps<{
  group: string;
  site: string;
  canAdmin: boolean;
  /** Whether a preview of the site is open to anyone through an access of its own. */
  publicPreview: boolean;
}>();

const emit = defineEmits<{
  saved: [Site];
}>();

const siteGroup = useSiteGroup();
const notices = ref<InstanceType<typeof Notices> | null>(null);

function settingsOf(site: Site): FeedbackSettings {
  return { enabled: site.feedbackEnabled, identity: site.feedbackIdentity, live: site.feedbackLive };
}

const settings = ref<FeedbackSettings>(settingsOf(siteGroup.site.value));

const publicSite = computed(() => siteGroup.site.value.access.base === 'public');

/**
 * Feedback from anyone at all, without a login: on a public site, whose
 * previews follow its access whether the live site takes feedback or not, or
 * on a preview that is public by an access of its own.
 */
const fromThePublic = computed(
  () =>
    settings.value.enabled &&
    settings.value.identity !== 'account' &&
    (publicSite.value || props.publicPreview),
);

/** Where it is open: the live site when that takes it and is public, otherwise the previews. */
const publicDetail = computed(() =>
  settings.value.live && publicSite.value
    ? t('feedback.settings.public.detail')
    : t('feedback.settings.public.detail.previews'),
);

/** Why no page carries the button although feedback is on, or null when one does. */
const nowhere = computed(() => noButton(settings.value, siteGroup.site.value));

/** Under the live switch: what it does, and why no page has the button yet with the way out. */
const liveHint = computed(() => {
  const hint = t('feedback.settings.live.hint');
  if (nowhere.value === null) return hint;
  return `${hint} ${t(`feedback.nowhere.${nowhere.value}`)} ${t(`feedback.nowhere.${nowhere.value}.admin`)}`;
});

/** Where the site stands, in one line for those who cannot change it. */
const summary = computed(() => {
  if (!settings.value.enabled) {
    return `${t('feedback.settings.summary.off')} ${t('feedback.settings.summary.off.others')}`;
  }
  const on = onSummary(settings.value);
  return nowhere.value === null ? on : `${on} ${t(`feedback.nowhere.${nowhere.value}`)}`;
});

function onSummary(current: FeedbackSettings): string {
  const where = current.live ? t('feedback.settings.summary.live') : t('feedback.settings.summary.previews');
  return `${where} ${t(`feedback.settings.summary.${current.identity}`)}`;
}

/** A save is on its way: the controls wait for its answer. */
const saving = ref(false);

async function save(next: FeedbackSettings): Promise<void> {
  const previous = settings.value;
  settings.value = next;
  saving.value = true;
  try {
    const updated = await toon.setFeedbackSettings(props.group, props.site, next);
    settings.value = settingsOf(updated);
    notices.value?.notify(
      'success',
      t('feedback.settings.saved'),
      updated.feedbackEnabled ? onSummary(settings.value) : t('feedback.settings.saved.off'),
    );
    emit('saved', updated);
  } catch (f) {
    settings.value = previous;
    notices.value?.notify(
      'critical',
      t('feedback.settings.failed'),
      errorText(f, t('feedback.settings.failed.detail')),
    );
  } finally {
    saving.value = false;
  }
}

function switched(event: Event): boolean {
  return Boolean((event as CustomEvent<{ checked?: boolean }>).detail?.checked);
}

function toggleEnabled(event: Event): void {
  const next = switched(event);
  if (next === settings.value.enabled) return;
  void save({ ...settings.value, enabled: next });
}

function toggleLive(event: Event): void {
  const next = switched(event);
  if (next === settings.value.live) return;
  void save({ ...settings.value, live: next });
}

function chooseIdentity(identity: FeedbackIdentity): void {
  if (identity === settings.value.identity) return;
  void save({ ...settings.value, identity });
}
</script>

<template>
  <Notices ref="notices" />

  <section aria-labelledby="heading-feedback-settings">
    <nldd-container layout="stack" gap="8">
      <SectionHeading
        id="heading-feedback-settings"
        :text="t('feedback.settings.heading')"
        :intro="canAdmin ? t('feedback.settings.intro') : undefined"
      />

      <nldd-rich-text v-if="!canAdmin" data-testid="feedback-summary">
        <p>{{ summary }}</p>
      </nldd-rich-text>

      <template v-else>
        <nldd-form-field>
          <nldd-switch-field
            :label="t('feedback.settings.enabled')"
            :checked="settings.enabled || undefined"
            :disabled="saving || undefined"
            data-testid="feedback-enabled"
            @change="toggleEnabled"
          ></nldd-switch-field>
          <nldd-form-field-help-text v-if="!settings.enabled">
            {{ t('feedback.settings.enabled.off') }}
          </nldd-form-field-help-text>
        </nldd-form-field>

        <nldd-container v-if="settings.enabled" layout="stack" gap="8">
          <SectionHeading
            id="heading-feedback-identity"
            :size="5"
            :level="3"
            :text="t('feedback.settings.identity.heading')"
          />
          <nldd-list
            type="radiogroup"
            appearance="box-base"
            :accessible-label="t('feedback.settings.identity.heading')"
            data-testid="feedback-identity"
          >
            <nldd-list-item
              v-for="identity in FEEDBACK_IDENTITY_VALUES"
              :key="identity"
              radio
              size="md"
              :checked="identity === settings.identity || undefined"
              :disabled="saving || undefined"
              :data-testid="`feedback-identity-${identity}`"
              @change="chooseIdentity(identity)"
            >
              <!-- The row is the radio itself. The button only draws the shape
                   (decorative), otherwise there is a control inside a control. -->
              <nldd-cell width="fit-content">
                <nldd-radio-button
                  decorative
                  :checked="identity === settings.identity || undefined"
                ></nldd-radio-button>
              </nldd-cell>
              <nldd-spacer-cell size="12"></nldd-spacer-cell>
              <nldd-title-cell
                size="display-6"
                :text="t(`feedback.settings.identity.${identity}`)"
                :supporting-text="t(`feedback.settings.identity.${identity}.hint`)"
              ></nldd-title-cell>
            </nldd-list-item>
          </nldd-list>
        </nldd-container>

        <nldd-form-field v-if="settings.enabled">
          <nldd-switch-field
            :label="t('feedback.settings.live')"
            :checked="settings.live || undefined"
            :disabled="saving || undefined"
            data-testid="feedback-live"
            @change="toggleLive"
          ></nldd-switch-field>
          <nldd-form-field-help-text>{{ liveHint }}</nldd-form-field-help-text>
        </nldd-form-field>

        <nldd-banner
          v-if="fromThePublic"
          variant="warning"
          :text="t('feedback.settings.public')"
          :supporting-text="publicDetail"
          data-testid="feedback-public-warning"
        ></nldd-banner>
      </template>
    </nldd-container>
  </section>
</template>

<script setup lang="ts">
/**
 * The settings half of the Feedback tab: whether the pages of the site carry
 * the feedback button, who may use it, and whether the live site does too.
 *
 * Only a site admin changes them; everyone else reads where the site stands
 * in one line. Turned off, the two other choices are not greyed out but gone:
 * a control that cannot be used is noise, and the line under the switch says
 * what turning it on brings. The three travel together in one call, so the
 * site is never on a mix of the old and the new value.
 */
import { computed, ref } from 'vue';

import * as toon from '@/api/toon';
import { errorText } from '@/api/client';
import type { FeedbackIdentity, FeedbackSettings, Site } from '@/api/types';
import { FEEDBACK_IDENTITY_VALUES } from '@/api/types';
import SectionHeading from '@/components/SectionHeading.vue';
import Notices from '@/components/site/Notices.vue';
import { noButton, type FeedbackSwitch } from '@/components/site/feedbackText';
import { useSiteGroup } from '@/composables/siteGroup';
import { contentUrl } from '@/format';
import { t } from '@/i18n';

const props = defineProps<{
  group: string;
  site: string;
  contentBase: string;
  canAdmin: boolean;
  /** The ref of the preview updated last, or null when the site has none (or they could not be read). */
  newestPreview: string | null;
  /** Whether a preview of the site is open to anyone through an access of its own. */
  publicPreview: boolean;
}>();

const emit = defineEmits<{
  saved: [Site];
}>();

const siteGroup = useSiteGroup();
const notices = ref<InstanceType<typeof Notices> | null>(null);
const enabledSwitch = ref<HTMLElement | null>(null);
const liveSwitch = ref<HTMLElement | null>(null);

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

/** Where the site stands, in the words of whoever cannot change it. */
const summary = computed(() => {
  if (!settings.value.enabled) return t('feedback.settings.summary.off');
  const on = onSummary(settings.value);
  return nowhere.value === null ? on : `${on} ${t(`feedback.nowhere.${nowhere.value}`)}`;
});

function onSummary(current: FeedbackSettings): string {
  const where = current.live ? t('feedback.settings.summary.live') : t('feedback.settings.summary.previews');
  return `${where} ${t(`feedback.settings.summary.${current.identity}`)}`;
}

/**
 * A page of this site that carries the button: the live site when it takes
 * feedback, otherwise the newest preview. Null when no page carries it.
 */
const showTarget = computed(() => {
  if (settings.value.live && siteGroup.site.value.hasLiveVersion) return `/${props.group}/${props.site}/`;
  if (props.newestPreview !== null) return `/${props.group}/${props.site}/_preview/${props.newestPreview}/`;
  return null;
});

/**
 * The content host's own page that clears the hide cookie of the whole site
 * and goes on to a page where the button shows again.
 */
const showUrl = computed(() =>
  showTarget.value === null
    ? null
    : `${contentUrl(props.contentBase, '/-/feedback/show')}?returnTo=${encodeURIComponent(showTarget.value)}`,
);

/** The settings as the server last confirmed them: where the switches go back to when a save fails. */
let confirmed = settingsOf(siteGroup.site.value);
/**
 * The saves, one after the other. Two on their way at once could be answered
 * in either order, and the switches would then show what the server does not
 * hold.
 */
let saving: Promise<void> = Promise.resolve();

function sameSettings(a: FeedbackSettings, b: FeedbackSettings): boolean {
  return a.enabled === b.enabled && a.identity === b.identity && a.live === b.live;
}

/** Shows a change at once, and saves it once the saves before it are done. */
function change(next: FeedbackSettings): void {
  settings.value = next;
  saving = saving.then(save);
}

/**
 * Sends the switches as they stand when its turn comes, so a change made
 * while the save before was on its way goes out with that one's result in it.
 */
async function save(): Promise<void> {
  const next = { ...settings.value };
  // Sent already by the save before, or taken back when that one failed.
  if (sameSettings(next, confirmed)) return;
  try {
    const updated = await toon.setFeedbackSettings(props.group, props.site, next);
    confirmed = settingsOf(updated);
    // A change made meanwhile stays on screen: the next save sends it.
    if (sameSettings(settings.value, next)) settings.value = { ...confirmed };
    notices.value?.notify(
      'success',
      t('feedback.settings.saved'),
      updated.feedbackEnabled ? onSummary(confirmed) : t('feedback.settings.saved.off'),
    );
    emit('saved', updated);
  } catch (f) {
    settings.value = { ...confirmed };
    notices.value?.notify(
      'critical',
      t('feedback.settings.failed'),
      errorText(f, t('feedback.settings.failed.detail')),
    );
  }
}

function switched(event: Event): boolean {
  return Boolean((event as CustomEvent<{ checked?: boolean }>).detail?.checked);
}

function toggleEnabled(event: Event): void {
  const next = switched(event);
  if (next === settings.value.enabled) return;
  change({ ...settings.value, enabled: next });
}

function toggleLive(event: Event): void {
  const next = switched(event);
  if (next === settings.value.live) return;
  change({ ...settings.value, live: next });
}

function chooseIdentity(identity: FeedbackIdentity): void {
  if (identity === settings.value.identity) return;
  change({ ...settings.value, identity });
}

/**
 * Where the empty list sends an admin: the switch that turns feedback on, or
 * the one that takes it to the live site too.
 */
function focusSwitch(which: FeedbackSwitch): void {
  (which === 'live' ? liveSwitch : enabledSwitch).value?.focus();
}

defineExpose({ focusSwitch });
</script>

<template>
  <section aria-labelledby="heading-feedback-settings">
    <Notices ref="notices" />

    <nldd-container layout="stack" gap="16">
      <SectionHeading
        id="heading-feedback-settings"
        :text="t('feedback.settings.heading')"
        :intro="t('feedback.settings.intro')"
      />

      <template v-if="canAdmin">
        <nldd-form-field>
          <nldd-switch-field
            ref="enabledSwitch"
            :label="t('feedback.settings.enabled')"
            :checked="settings.enabled || undefined"
            data-testid="feedback-enabled"
            @change="toggleEnabled"
          ></nldd-switch-field>
          <nldd-form-field-help-text v-if="!settings.enabled">
            {{ t('feedback.settings.enabled.off') }}
          </nldd-form-field-help-text>
        </nldd-form-field>

        <nldd-container v-if="settings.enabled" layout="stack" gap="8">
          <nldd-title :size="5">
            <h3 id="heading-feedback-identity">{{ t('feedback.settings.identity.heading') }}</h3>
          </nldd-title>
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
              :data-testid="`feedback-identity-${identity}`"
              @change="chooseIdentity(identity)"
            >
              <!-- The row is the radio itself; the button only draws the shape
                   (decorative), otherwise there is a control inside a control. -->
              <nldd-cell width="fit-content">
                <nldd-radio-button
                  decorative
                  :checked="identity === settings.identity || undefined"
                ></nldd-radio-button>
              </nldd-cell>
              <nldd-spacer-cell size="12"></nldd-spacer-cell>
              <nldd-title-cell
                :size="6"
                :text="t(`feedback.settings.identity.${identity}`)"
                :supporting-text="t(`feedback.settings.identity.${identity}.hint`)"
              ></nldd-title-cell>
            </nldd-list-item>
          </nldd-list>
        </nldd-container>

        <nldd-form-field v-if="settings.enabled">
          <nldd-switch-field
            ref="liveSwitch"
            :label="t('feedback.settings.live')"
            :checked="settings.live || undefined"
            data-testid="feedback-live"
            @change="toggleLive"
          ></nldd-switch-field>
          <nldd-form-field-help-text>{{ liveHint }}</nldd-form-field-help-text>
        </nldd-form-field>

        <nldd-inline-dialog
          v-if="fromThePublic"
          icon="exclamation-triangle"
          icon-color="warning"
          horizontal-alignment="left"
          :text="t('feedback.settings.public')"
          :supporting-text="publicDetail"
          data-testid="feedback-public-warning"
        ></nldd-inline-dialog>
      </template>

      <nldd-rich-text v-else data-testid="feedback-summary">
        <p>{{ summary }}</p>
      </nldd-rich-text>

      <nldd-rich-text v-if="settings.enabled && showUrl !== null" data-testid="feedback-show">
        <p>
          {{ t('feedback.settings.show.hint') }}
          <nldd-link :href="showUrl" target="_blank" data-testid="feedback-show-link">{{
            t('feedback.settings.show')
          }}</nldd-link>
        </p>
      </nldd-rich-text>
    </nldd-container>
  </section>
</template>

<script setup lang="ts">
/**
 * A member's own choice about email on new feedback, at one of three levels:
 * the profile, a group or a site. The narrowest level that is set wins, so
 * the profile is a plain on/off switch (nothing set and off mean the same
 * there), while a group and a site offer a third choice: follow the wider
 * levels, with what that would give named next to it.
 *
 * Nothing is drawn while the installation sends no email at all: a choice
 * that has no effect is noise. Each choice saves on change, optimistically,
 * and rolls back with a notification when the call fails.
 */
import { onMounted, ref } from 'vue';

import * as toon from '@/api/toon';
import { errorText } from '@/api/client';
import type { FeedbackEmailSetting, FeedbackEmailSource } from '@/api/types';
import SectionHeading from '@/components/SectionHeading.vue';
import Notices from '@/components/site/Notices.vue';
import { t } from '@/i18n';

const props = withDefaults(
  defineProps<{
    level: 'profile' | 'group' | 'site';
    group?: string;
    site?: string;
  }>(),
  { group: '', site: '' },
);

type Choice = 'follow' | 'on' | 'off';

/** What following the wider levels gives, and which level says so. */
interface Inherited {
  effective: boolean;
  source: Exclude<FeedbackEmailSource, 'site'>;
}

const CHOICES: readonly Choice[] = ['follow', 'on', 'off'];

const notices = ref<InstanceType<typeof Notices> | null>(null);
const setting = ref<FeedbackEmailSetting | null>(null);
const inherited = ref<Inherited | null>(null);
const saving = ref(false);

function read(): Promise<FeedbackEmailSetting> {
  if (props.level === 'profile') return toon.myFeedbackEmail();
  if (props.level === 'group') return toon.groupFeedbackEmail(props.group);
  return toon.siteFeedbackEmail(props.group, props.site);
}

function write(enabled: boolean | null): Promise<FeedbackEmailSetting> {
  if (props.level === 'profile') return toon.setMyFeedbackEmail(enabled);
  if (props.level === 'group') return toon.setGroupFeedbackEmail(props.group, enabled);
  return toon.setSiteFeedbackEmail(props.group, props.site, enabled);
}

/**
 * What "follow" would give. While this level is not set, the answer for this
 * level says so itself. Once it is set, the wider levels are asked: the group
 * first (a site-only member has none, which is no reason to stop), then the
 * profile. Null when that cannot be told, and the option then names no result.
 */
async function inheritedOf(own: FeedbackEmailSetting): Promise<Inherited | null> {
  if (props.level === 'profile') return null;
  if (own.enabled === null) {
    return { effective: own.effective, source: own.source as Inherited['source'] };
  }
  try {
    if (props.level === 'site') {
      const group = await toon.groupFeedbackEmail(props.group).catch(() => null);
      if (group !== null && group.enabled !== null) {
        return { effective: group.enabled, source: 'group' };
      }
    }
    const profile = await toon.myFeedbackEmail();
    return { effective: profile.effective, source: profile.source as Inherited['source'] };
  } catch {
    return null;
  }
}

async function load(): Promise<void> {
  try {
    const own = await read();
    inherited.value = await inheritedOf(own);
    setting.value = own;
  } catch (f) {
    notices.value?.notify(
      'critical',
      t('notifications.email.loadFailed'),
      errorText(f, t('notifications.email.loadFailed.detail')),
    );
  }
}

onMounted(load);

function stateWord(on: boolean): string {
  return t(on ? 'notifications.email.state.on' : 'notifications.email.state.off');
}

function choiceOf(current: FeedbackEmailSetting): Choice {
  if (current.enabled === null) return 'follow';
  return current.enabled ? 'on' : 'off';
}

function label(choice: Choice): string {
  if (choice === 'follow') {
    return props.level === 'group'
      ? t('notifications.email.group.follow', { state: stateWord(inherited.value?.effective ?? false) })
      : t('notifications.email.site.follow');
  }
  return props.level === 'group'
    ? t(`notifications.email.group.${choice}`)
    : t(`notifications.email.site.${choice}`);
}

function hint(choice: Choice): string | undefined {
  if (choice === 'follow') {
    if (props.level === 'group') return t('notifications.email.group.follow.hint');
    const wider = inherited.value;
    return wider === null
      ? undefined
      : t(`notifications.email.site.follow.${wider.source}`, { state: stateWord(wider.effective) });
  }
  return props.level === 'group'
    ? t(`notifications.email.group.${choice}.hint`)
    : t(`notifications.email.site.${choice}.hint`);
}

async function save(enabled: boolean | null): Promise<void> {
  const previous = setting.value as FeedbackEmailSetting;
  setting.value = { ...previous, enabled };
  saving.value = true;
  try {
    const updated = await write(enabled);
    setting.value = updated;
    if (updated.enabled === null) {
      inherited.value = { effective: updated.effective, source: updated.source as Inherited['source'] };
    }
    notices.value?.notify(
      'success',
      t('notifications.email.saved'),
      t(updated.effective ? 'notifications.email.saved.on' : 'notifications.email.saved.off'),
    );
  } catch (f) {
    setting.value = previous;
    notices.value?.notify(
      'critical',
      t('notifications.email.failed'),
      errorText(f, t('notifications.email.failed.detail')),
    );
  } finally {
    saving.value = false;
  }
}

function choose(choice: Choice): void {
  const current = setting.value as FeedbackEmailSetting;
  if (choice === choiceOf(current)) return;
  void save(choice === 'follow' ? null : choice === 'on');
}

function toggled(event: Event): void {
  const next = Boolean((event as CustomEvent<{ checked?: boolean }>).detail?.checked);
  if (next === ((setting.value as FeedbackEmailSetting).enabled === true)) return;
  void save(next);
}
</script>

<template>
  <Notices ref="notices" />

  <section
    v-if="setting?.available"
    :aria-labelledby="`heading-feedback-email-${level}`"
    :data-testid="`feedback-email-${level}`"
  >
    <nldd-container layout="stack" gap="8">
      <SectionHeading
        :id="`heading-feedback-email-${level}`"
        :text="t('notifications.email.heading')"
        :intro="t('notifications.email.intro')"
      />

      <nldd-form-field v-if="level === 'profile'">
        <nldd-switch-field
          :label="t('notifications.email.profile.label')"
          :checked="setting.enabled === true || undefined"
          :disabled="saving || undefined"
          data-testid="feedback-email-switch"
          @change="toggled"
        ></nldd-switch-field>
        <nldd-form-field-help-text>{{ t('notifications.email.profile.hint') }}</nldd-form-field-help-text>
      </nldd-form-field>

      <nldd-list
        v-else
        type="radiogroup"
        appearance="box-base"
        :accessible-label="t('notifications.email.list.label')"
        data-testid="feedback-email-choice"
      >
        <nldd-list-item
          v-for="choice in CHOICES"
          :key="choice"
          radio
          size="md"
          :checked="choice === choiceOf(setting) || undefined"
          :disabled="saving || undefined"
          :data-testid="`feedback-email-${choice}`"
          @change="choose(choice)"
        >
          <!-- The row is the radio itself. The button only draws the shape
               (decorative), otherwise there is a control inside a control. -->
          <nldd-cell width="fit-content">
            <nldd-radio-button
              decorative
              :checked="choice === choiceOf(setting) || undefined"
            ></nldd-radio-button>
          </nldd-cell>
          <nldd-spacer-cell size="12"></nldd-spacer-cell>
          <nldd-title-cell
            size="display-6"
            :text="label(choice)"
            :supporting-text="hint(choice)"
          ></nldd-title-cell>
        </nldd-list-item>
      </nldd-list>
    </nldd-container>
  </section>
</template>

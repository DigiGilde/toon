<script setup lang="ts">
/**
 * What the Feedback tab offers about being told of new feedback: every member
 * who sees the tab chooses for themselves whether they get email about this
 * site, and an admin of the site also manages its Mattermost channel.
 */
import FeedbackEmailChoice from '@/components/FeedbackEmailChoice.vue';
import FeedbackWebhook from '@/components/FeedbackWebhook.vue';

defineProps<{ group: string; site: string; canAdmin: boolean }>();
</script>

<template>
  <nldd-container layout="stack" gap="24">
    <FeedbackEmailChoice level="site" :group="group" :site="site" />
    <FeedbackWebhook v-if="canAdmin" :group="group" :site="site" />
  </nldd-container>
</template>

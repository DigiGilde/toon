/**
 * English counterpart of notifications.nl.ts. Typed against it, so a key
 * present in one language and missing from the other is a compile error.
 */
import type { notificationsNl } from './notifications.nl';

export const notificationsEn: Record<keyof typeof notificationsNl, string> = {
  // -- Email ----------------------------------------------------------------
  'notifications.email.heading': 'Email about new feedback',
  'notifications.email.intro':
    'The email holds the site, the page, the kind, the time and whether a screenshot came with it, plus a link. It never holds the text of the feedback or who sent it. The email goes to the address from your login.',
  'notifications.email.list.label': 'Email about new feedback',
  'notifications.email.profile.label': 'Email me about new feedback',
  'notifications.email.profile.hint':
    'This applies to every group and site where you have not chosen otherwise.',
  'notifications.email.state.on': 'on',
  'notifications.email.state.off': 'off',
  'notifications.email.group.follow': 'Follow my profile (now: {state})',
  'notifications.email.group.follow.hint': 'Your own choice for a site in this group comes first.',
  'notifications.email.group.on': 'On',
  'notifications.email.group.on.hint':
    'You get email about new feedback on every site in this group where you have not chosen otherwise for the site.',
  'notifications.email.group.off': 'Off',
  'notifications.email.group.off.hint':
    'You get no email about new feedback from this group, unless you choose otherwise for a site.',
  'notifications.email.site.follow': 'Follow the group and my profile',
  'notifications.email.site.follow.group': 'Inherited from the group: {state}',
  'notifications.email.site.follow.profile': 'From your profile: {state}',
  'notifications.email.site.follow.default': 'Not set anywhere: {state}',
  'notifications.email.site.on': 'On',
  'notifications.email.site.on.hint': 'You get email about new feedback on this site.',
  'notifications.email.site.off': 'Off',
  'notifications.email.site.off.hint': 'You get no email about new feedback on this site.',
  'notifications.email.saved': 'Email choice saved',
  'notifications.email.saved.on': 'You now get email about new feedback.',
  'notifications.email.saved.off': 'You now get no email about new feedback.',
  'notifications.email.failed': 'Could not save the email choice',
  'notifications.email.failed.detail': 'Try again.',
  'notifications.email.loadFailed': 'Could not load the email choice',
  'notifications.email.loadFailed.detail': 'Reload the page.',

  // -- Mattermost -----------------------------------------------------------
  'notifications.webhook.heading': 'Mattermost channel',
  'notifications.webhook.intro':
    'Have every new feedback item appear in a Mattermost channel, through an incoming webhook. A site without a channel of its own uses the one of its group.',
  'notifications.webhook.status.own':
    'Posts go to a Mattermost channel on {host}. Language of the posts: {language}.',
  'notifications.webhook.status.group':
    "This site has no channel of its own. Posts go to the group's channel on {host}.",
  'notifications.webhook.status.none': 'No Mattermost channel.',
  'notifications.webhook.warning': 'Everyone in the channel reads the feedback',
  'notifications.webhook.warning.detail':
    'With a webhook, the text of every new feedback item (cut at 500 characters, without the name or email address of who sent it) also lands in the Mattermost channel. It stays there after the feedback is deleted in Toon. Anyone in that channel reads it.',
  'notifications.webhook.url.label': 'Webhook URL',
  'notifications.webhook.url.hint': 'Paste the URL of an incoming webhook. Toon never shows it again.',
  'notifications.webhook.url.replace': 'Saving replaces the current webhook.',
  'notifications.webhook.language.label': 'Language of the posts',
  'notifications.webhook.save': 'Save webhook',
  'notifications.webhook.remove': 'Remove webhook',
  'notifications.webhook.saved': 'Webhook saved',
  'notifications.webhook.saved.detail': 'Posts now go to a Mattermost channel on {host}.',
  'notifications.webhook.failed': 'Could not save the webhook',
  'notifications.webhook.failed.detail': 'Try again.',
  'notifications.webhook.removed': 'Webhook removed',
  'notifications.webhook.removeFailed': 'Could not remove the webhook',
  'notifications.webhook.removeFailed.detail': 'Try again.',
  'notifications.webhook.loadFailed': 'Could not load the Mattermost channel',
  'notifications.webhook.loadFailed.detail': 'Reload the page.',
  'notifications.webhook.confirm.title': 'Remove the webhook?',
  'notifications.webhook.confirm.text':
    'New feedback no longer goes to the channel. What is already there stays.',
  'notifications.webhook.confirm.keep': 'Keep it',
  'notifications.webhook.confirm.confirm': 'Remove webhook',
};

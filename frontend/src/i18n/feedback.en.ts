/**
 * English counterpart of feedback.nl.ts. Typed against it, so a key present in
 * one language and missing from the other is a compile error rather than a
 * raw key on screen.
 */
import type { feedbackNl } from './feedback.nl';

export const feedbackEn: Record<keyof typeof feedbackNl, string> = {
  // -- The list -------------------------------------------------------------
  'feedback.loading': 'Loading feedback',
  'feedback.inbox.heading': 'Feedback received',
  'feedback.inbox.intro':
    'What visitors sent about the pages of this site with the feedback button, newest first.',
  'feedback.inbox.status.label': 'Status',
  'feedback.inbox.status.open': 'Open ({count})',
  'feedback.inbox.status.resolved': 'Resolved ({count})',
  'feedback.inbox.status.all': 'All ({count})',
  'feedback.inbox.status.open.uncounted': 'Open',
  'feedback.inbox.status.resolved.uncounted': 'Resolved',
  'feedback.inbox.status.all.uncounted': 'All',
  'feedback.inbox.where.label': 'Given on',
  'feedback.inbox.where.all': 'Live site and previews',
  'feedback.inbox.where.live': 'The live site',
  'feedback.inbox.where.previews': 'All previews',
  'feedback.inbox.where.preview': 'Preview {ref}',
  'feedback.inbox.more': 'Load more',
  'feedback.inbox.more.failed': 'Could not load more feedback',
  'feedback.inbox.more.failed.detail': 'Try again.',
  'feedback.inbox.failed': 'Could not load the feedback',
  'feedback.inbox.failed.detail': 'Try again.',

  // -- What the list says when it has nothing to show ------------------------
  'feedback.empty.off': 'Feedback is off',
  'feedback.empty.off.admin':
    'Turn it on under Collecting feedback. The previews of this site then get a feedback button.',
  'feedback.empty.off.member': 'An administrator of this site can turn feedback on.',
  'feedback.empty.off.action': 'Set up feedback',
  'feedback.empty.none': 'No feedback yet',
  'feedback.empty.none.detail': 'What visitors send with the feedback button arrives here.',
  'feedback.empty.nowhere': 'No page has the feedback button yet',
  'feedback.empty.nowhere.previews': 'This site has no previews, and feedback is off for the live site.',
  'feedback.empty.nowhere.publish': 'No version is live yet and this site has no previews.',
  'feedback.empty.filter': 'No feedback for this choice',
  'feedback.empty.filter.detail': 'Choose another status or place above.',

  // -- One item, in the list and in the detail -------------------------------
  'feedback.status.new': 'New',
  'feedback.status.seen': 'Seen',
  'feedback.status.resolved': 'Resolved',
  'feedback.kind.page': 'Whole page',
  'feedback.kind.point': 'Point',
  'feedback.kind.text': 'Text',
  'feedback.target.live': 'Live',
  'feedback.target.preview': 'Preview {ref}',
  'feedback.page.root': 'Home page',
  'feedback.place': '{page} ({target})',
  'feedback.author.named': 'By {name}',
  'feedback.author.accountUnnamed': 'By someone with an account',
  'feedback.author.someone': 'Someone with an account',
  'feedback.author.unverified': 'Name not checked',
  'feedback.author.anonymous': 'Without name',
  'feedback.row.replies.one': '1 reply',
  'feedback.row.replies.many': '{count} replies',
  'feedback.row.screenshot': 'with screenshot',
  'feedback.time.now': 'just now',

  // -- The detail sheet ------------------------------------------------------
  'feedback.sheet.title': 'Feedback on {page}',
  'feedback.sheet.label': 'Feedback on {page}, {date}',
  'feedback.sheet.close': 'Close',
  'feedback.sheet.quote': 'About this text',
  'feedback.sheet.view': 'View on the page',
  'feedback.sheet.view.gone':
    'Preview {ref} has expired or no longer exists, so this place can no longer be viewed.',
  'feedback.sheet.view.off': 'Feedback is off for this site, so the page opens without marking this place.',
  'feedback.sheet.view.offLive': 'Feedback is off on the live site, so the page opens without marking this place.',
  'feedback.sheet.screenshot.alt': 'Screenshot with this feedback',
  'feedback.sheet.screenshot.open': 'Screenshot at full size',
  'feedback.meta.label': 'Details of this feedback',
  'feedback.meta.author': 'By',
  'feedback.meta.author.email': '{name}, {email}',
  'feedback.meta.created': 'On',
  'feedback.meta.kind': 'Kind',
  'feedback.meta.page': 'Page',
  'feedback.meta.target': 'Live or preview',
  'feedback.meta.version': 'Version',
  'feedback.meta.version.live': '{date}, live now',
  'feedback.meta.version.gone': 'No longer available',
  'feedback.meta.viewport': 'Window size',
  'feedback.meta.viewport.value': '{width} by {height} pixels',
  'feedback.meta.unknown': 'Unknown',
  'feedback.meta.status': 'Status',
  'feedback.meta.status.changed': '{status}, by {name} on {date}',
  'feedback.meta.status.changedUnnamed': '{status} on {date}',

  // -- The thread and the reply form ------------------------------------------
  'feedback.thread.heading': 'Replies',
  'feedback.thread.empty': 'No replies yet.',
  'feedback.thread.team': 'Site team',
  'feedback.thread.teamNamed': 'Site team, {name}',
  'feedback.thread.giver': 'Submitter',
  'feedback.thread.byline': '{author}, {date}',
  'feedback.reply.label': 'Reply',
  'feedback.reply.help.account':
    'The submitter sees your reply, with your name, in the feedback button under My feedback.',
  'feedback.reply.help.noAccount':
    'This feedback came in without an account, so the submitter does not see your reply.',
  'feedback.reply.required': 'A reply is needed',
  'feedback.reply.submit': 'Reply',
  'feedback.reply.sent': 'Reply posted',
  'feedback.reply.failed': 'Replying did not work. Try again.',
  'feedback.reply.failed.other': 'Reply not posted to the feedback on {page}',
  'feedback.reply.failed.kept': 'When you open that feedback again, your reply is in the field.',
  'feedback.reply.failed.restored': 'Your reply is back in the field.',

  // -- Status and deleting ------------------------------------------------------
  'feedback.action.seen': 'Mark as seen',
  'feedback.action.resolve': 'Mark as resolved',
  'feedback.action.reopen': 'Reopen',
  'feedback.action.done.new': 'Feedback reopened',
  'feedback.action.done.seen': 'Marked as seen',
  'feedback.action.done.resolved': 'Marked as resolved',
  'feedback.action.failed': 'Status not changed',
  'feedback.action.failed.detail': 'Saving did not work. Try again.',
  'feedback.action.failed.other': 'Status not changed for the feedback on {page}',
  'feedback.readOnly': 'Only editors and administrators can reply or change the status.',
  'feedback.delete': 'Delete',
  'feedback.delete.confirm.title': 'Delete this feedback?',
  'feedback.delete.confirm.text':
    'The feedback goes, with its replies and its screenshot. This cannot be undone.',
  'feedback.delete.confirm.keep': 'Keep',
  'feedback.delete.done': 'Feedback deleted',
  'feedback.delete.failed': 'Feedback not deleted',
  'feedback.delete.failed.detail': 'Deleting did not work. Try again.',

  // -- Settings -------------------------------------------------------------
  'feedback.settings.heading': 'Collecting feedback',
  'feedback.settings.intro':
    'With feedback on, Toon adds a Feedback button to the pages of this site. With it, visitors point at a place or a text and send a remark, with a screenshot if they want.',
  'feedback.settings.enabled': 'Allow feedback',
  'feedback.settings.enabled.off': 'Turn this on and the previews of this site get the feedback button.',
  'feedback.settings.identity.heading': 'Who may give feedback',
  'feedback.settings.identity.account': 'Only with an account',
  'feedback.settings.identity.account.hint':
    'Whoever gives feedback signs in. The site team sees the name, editors and administrators the e-mail address too.',
  'feedback.settings.identity.name': 'Also with a name they type themselves',
  'feedback.settings.identity.name.hint': 'Signing in is then not needed. Toon does not check that name.',
  'feedback.settings.identity.anonymous': 'Also without a name',
  'feedback.settings.identity.anonymous.hint':
    'Anyone who can view the page can give feedback without saying who it is from.',
  'feedback.settings.live': 'Also on the live site',
  'feedback.settings.live.hint': 'Otherwise on previews only.',
  'feedback.settings.public': 'You are collecting feedback from the public',
  'feedback.settings.public.detail':
    'The live site is public and giving feedback works without signing in. Expect spam: the number of open items per site has a maximum. Once it is reached, the site takes nothing new. Your organisation has to name this collection in its own privacy statement too.',
  'feedback.settings.public.detail.previews':
    'Anyone with the link to a preview can give feedback without signing in. Expect spam: the number of open items per site has a maximum. Once it is reached, the site takes nothing new. Your organisation has to name this collection in its own privacy statement too.',
  'feedback.settings.saved': 'Feedback setting saved',
  'feedback.settings.saved.off': 'Visitors no longer see the feedback button.',
  'feedback.settings.failed': 'Feedback setting not saved',
  'feedback.settings.failed.detail': 'Saving did not work. Try again.',
  'feedback.settings.summary.off': 'Feedback is off. Only an administrator of this site can change that.',
  'feedback.settings.summary.previews': 'Feedback is on for the previews.',
  'feedback.settings.summary.live': 'Feedback is on for the previews and the live site.',
  'feedback.settings.summary.account': 'It can only be given with an account.',
  'feedback.settings.summary.name': 'It can be given with an account or with a name typed in.',
  'feedback.settings.summary.anonymous': 'It can be given without a name too.',
  'feedback.settings.show': 'Show the feedback button again',
  'feedback.settings.show.hint': 'Did you hide the button on this site until you close the browser?',

  // -- No page carries the button -------------------------------------------
  'feedback.nowhere.previews': 'This site has no previews, so no page has the feedback button now.',
  'feedback.nowhere.publish':
    'No version is live yet and this site has no previews, so no page has the feedback button yet.',
  'feedback.nowhere.previews.admin':
    'Turn feedback on for the live site too, or publish a preview with the CLI or from your repository.',
  'feedback.nowhere.publish.admin': 'Publish a version of the site first.',
};

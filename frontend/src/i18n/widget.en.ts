/**
 * English counterpart of widget.nl.ts. Typed against it, so a key present in
 * one language and missing from the other is a compile error rather than a
 * raw key on a site's page.
 */
import type { widgetNl } from './widget.nl';

export const widgetEn: Record<keyof typeof widgetNl, string> = {
  // -- The button and its landmark ---------------------------------------------
  'widget.region': 'Feedback',
  'widget.trigger': 'Feedback',
  'widget.newTab': '(opens in a new tab)',

  // -- The menu ------------------------------------------------------------------
  'menu.heading': 'Feedback on this page',
  'menu.login': 'Give feedback with your account',
  'menu.login.note':
    'If you sign in, Toon keeps your SSO ID, email address and IP address until 90 days after the last time you signed in. ' +
    'If signing in fails, you stop it, or the sign-in service ends your session early, ' +
    'your IP address stays in the access log for three years.',
  'menu.point': 'Point at a spot',
  'menu.text': 'Feedback on selected text',
  'menu.page': 'Feedback on this page',
  'menu.mine': 'My feedback',
  'menu.account': 'Send with your account',
  'menu.account.note': 'This takes you to a page of Toon. Text you have not sent yet is lost.',
  'menu.stopAccount': 'Stop using your account',
  'menu.hide': 'Hide button',
  'menu.close': 'Close',
  'menu.noSelection': 'Select text on the page first.',
  'menu.floating': 'Feedback on this text',

  // -- Pointing at a spot ----------------------------------------------------------
  'placing.label': 'Point at a spot',
  'placing.bar': 'Click or tap the spot your feedback is about.',
  'placing.keys': 'Arrow keys move the crosshair, faster with Shift. Enter picks the spot.',
  'placing.under': 'Under the crosshair: {description}',
  'placing.nothing': 'There is nothing with text under the crosshair.',
  'placing.done': 'Spot chosen.',
  'placing.cancelled': 'Stopped pointing.',
  'placing.stop': 'Stop',

  // -- Choosing a part or covering something ------------------------------------
  'region.label': 'Choose the part of the screenshot you want to send',
  'region.bar': 'Drag a frame or click two corners of the part you want to send.',
  'region.done': 'Part chosen.',
  'cover.label': 'Choose what you want to cover',
  'cover.bar': 'Drag a frame or click two corners of what you want to cover.',
  'cover.done': 'Covered.',
  'rect.keys': 'Arrow keys move the frame, with Shift they resize it. Enter confirms.',
  'rect.size': 'Frame of {width} by {height} pixels.',
  'rect.cancelled': 'Stopped without a change.',

  // -- Writing the feedback --------------------------------------------------------
  'composer.context.point': 'Your feedback is about the spot you pointed at.',
  'composer.context.text': 'Your feedback is about this text:',
  'composer.context.page': 'Your feedback is about this whole page.',
  'composer.comment': 'Your feedback',
  'composer.comment.hint': 'At most {max} characters.',
  'composer.counter': '{count} of {max}',
  'composer.counter.near': 'You can type {left} more characters.',
  'composer.counter.full': 'You have reached the maximum of {max} characters.',
  'composer.comment.empty': 'Write your feedback first.',
  'composer.comment.long': 'Your feedback is too long. At most {max} characters.',
  'composer.comment.chars':
    'Your feedback contains a character that cannot be sent. Remove it and try again.',
  'composer.name.required': 'Your name',
  'composer.name.optional': 'Your name (optional)',
  'composer.name.empty': 'Fill in your name.',
  'composer.name.long': 'Your name is too long. At most {max} characters.',
  'composer.name.chars': 'Your name contains a character that cannot be sent.',
  'composer.anonymous.note':
    'Without a name the site team does not know who this comes from. Your text or the screenshot can still say something about you.',
  'composer.account':
    'You send this with your account. What the site team sees of you was on the page where you gave consent.',
  'composer.notice.visibility':
    'Everyone with a role on this site in Toon sees your feedback, including people who only look on.',
  'composer.notice.retention': 'Resolved feedback is deleted after {days} days.',
  'composer.notice.retention.one': 'Resolved feedback is deleted after 1 day.',
  'composer.notice.retention.keep': 'Resolved feedback is not deleted automatically.',
  'composer.notice.noAccount': 'You cannot see or delete this feedback later.',
  'composer.privacy': 'Privacy statement',
  'composer.privacy.note': 'The privacy statement opens in this tab. Text you have not sent yet is lost.',
  'composer.send': 'Send',
  'composer.cancel': 'Cancel',
  'composer.sending': 'Sending your feedback.',

  // -- The screenshot --------------------------------------------------------------
  'shot.heading': 'Screenshot',
  'shot.make': 'Take a screenshot',
  'shot.making': 'Taking a screenshot.',
  'shot.ready': 'Screenshot ready.',
  'shot.updating': 'Updating the screenshot.',
  'shot.alt.point': 'Screenshot of what you see now, with the spot you pointed at marked',
  'shot.alt.text': 'Screenshot of what you see now, with the selected text marked',
  'shot.alt.page': 'Screenshot of what you see now',
  'shot.notice':
    'The screenshot shows what is on your screen now, including data about other people or yourself. Check it, cover something or send without it.',
  'shot.placeholders': 'Images on this page appear as grey areas in the screenshot.',
  'shot.region': 'Choose another part',
  'shot.cover': 'Cover something',
  'shot.uncover': 'Remove covering',
  'shot.without': 'Without screenshot',
  'shot.excluded': 'You send this without a screenshot.',
  'shot.include': 'With screenshot after all',
  'shot.failed': 'The screenshot did not work. You can send your feedback without a screenshot.',
  'shot.tooLarge':
    'The screenshot has become too large. Choose a smaller part or send without a screenshot.',
  'shot.retry': 'Try again',

  // -- When sending does not work --------------------------------------------------
  'error.unavailable': 'Sending no longer works from this page. Copy your text and reload the page.',
  'error.unavailable.other': 'This no longer works from this page. Reload the page.',
  'error.account': 'This only works with your account. Copy your text and reload the page.',
  'error.account.other': 'This only works with your account. Reload the page.',
  'error.author':
    'This site no longer takes feedback this way. Copy your text and reload the page.',
  'error.moved': 'The site or group has a new address. Copy your text and reload the page.',
  'error.moved.other': 'The site or group has a new address. Reload the page.',
  'error.invalid': 'Something is wrong with your feedback.',
  'error.shot.large': 'The screenshot is too large for this site.',
  'error.shot.invalid': 'This screenshot cannot be sent.',
  'error.shot.quota': 'There is no room left for screenshots.',
  'error.shot.notReady':
    'The screenshot is not ready yet. Wait a moment or send your feedback without a screenshot.',
  'error.shot.changed': 'Your feedback has not been sent, because the screenshot could not be updated.',
  'error.full': 'This site has too much open feedback already. Try again later.',
  'error.rate': 'A lot of feedback is coming in right now. Try again in {time}.',
  'error.rate.soon': 'A lot of feedback is coming in right now. Try again in a moment.',
  'error.busy': 'Toon is too busy right now. Try again in {time}.',
  'error.busy.soon': 'Toon is too busy right now. Try again in a moment.',
  'error.timeout': 'Sending took too long. Try again.',
  'error.threadFull': 'You can no longer add replies to this feedback.',
  'error.later': 'That did not work. Try again later.',
  'error.copy': 'Copy your text',
  'error.copied': 'Your text has been copied.',
  'error.copyFailed': 'Copying did not work. Your text is selected, copy it yourself.',
  'error.withoutShot': 'Send without screenshot',
  'time.second': '1 second',
  'time.seconds': '{n} seconds',
  'time.minutes': '{n} minutes',

  // -- Sent ----------------------------------------------------------------------------
  'sent.heading': 'Thank you, your feedback has been sent.',
  'sent.account': 'You can find it under My feedback.',
  'sent.noAccount': 'You cannot see replies from the site team.',
  'sent.more': 'Give more feedback',

  // -- My feedback ---------------------------------------------------------------------
  'mine.heading': 'My feedback',
  'mine.loading': 'Loading your feedback.',
  'mine.empty': 'You have not given feedback on this site with your account yet.',
  'mine.back': 'Back',
  'mine.status.new': 'New',
  'mine.status.seen': 'Seen',
  'mine.status.resolved': 'Resolved',
  'mine.kind.page': 'Whole page',
  'mine.kind.point': 'Point',
  'mine.kind.text': 'Text',
  'mine.page': 'Page:',
  'mine.lost': 'This spot can no longer be found on the page.',
  'mine.olderReplies': 'Older replies are not in this list.',
  'mine.olderItems': 'Older feedback is not in this list.',
  'mine.team': 'Site team',
  'mine.teamNamed': 'Site team ({name})',
  'mine.you': 'You',
  'mine.reply': 'Your reply',
  'mine.replySend': 'Reply',
  'mine.replySent': 'Your reply has been sent.',
  'mine.reply.refused': 'Your reply was not posted. {reason}',
  'mine.reply.kept': 'Open My feedback again to find your text there.',
  'mine.reply.empty': 'Write your reply first.',
  'mine.reply.long': 'Your reply is too long. At most {max} characters.',
  'mine.reply.chars':
    'Your reply contains a character that cannot be sent. Remove it and try again.',
  'mine.delete': 'Delete',
  'mine.delete.question':
    'Do you want to delete this feedback? The site team will no longer see it either.',
  'mine.deleted': 'Your feedback has been deleted.',
  'mine.pin': 'Feedback: {words}',

  // -- Hiding the button -----------------------------------------------------------------
  'hide.heading': 'Hide button',
  'hide.minute': 'For a minute',
  'hide.session': 'Until you close the browser',
  'hide.minute.done': 'The feedback button is hidden and comes back in a minute.',
  'hide.back': 'The feedback button is visible again.',
  'hide.confirm':
    'The button stays away on this site until you close the browser, and 12 hours at most. You can bring it back with this link:',
  'hide.confirmButton': 'Hide',

  // -- A place shown from a link ---------------------------------------------------------
  'highlight.label': 'This is what the feedback is about',
  'highlight.shown': 'The spot the feedback is about is marked.',
  'highlight.shownAt': 'Marked: {description}',
  'highlight.lost': 'The spot of this feedback can no longer be found on this page.',
};

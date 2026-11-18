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
  'menu.point': 'Point at a spot or an area',
  'menu.text': 'Feedback on selected text',
  'menu.page': 'General feedback',
  'menu.mine': 'My feedback',
  'menu.account': 'Send with your account',
  'menu.account.note': 'This takes you to a page of Toon. Text you have not sent yet is lost.',
  'menu.stopAccount': 'Stop giving feedback with your account',
  'menu.hide': 'Hide button',
  'menu.close': 'Close',
  'menu.noSelection': 'Select text on the page first.',
  'menu.floating': 'Feedback on this text',

  // -- Pointing at a spot or an area -------------------------------------------------
  'placing.label': 'Point at a spot or an area',
  'placing.bar': 'Click or tap a spot. Drag for an area, or choose Area without dragging.',
  'placing.keys':
    'Arrow keys put a crosshair in the middle and move it, faster with Shift. Enter picks the spot. For an area, choose Area without dragging.',
  'placing.under': 'Under the crosshair: {description}',
  'placing.nothing': 'There is nothing with text under the crosshair.',
  'placing.done': 'Spot chosen.',
  'placing.cancelled': 'Stopped pointing.',
  'placing.stop': 'Stop',
  'placing.area': 'Area without dragging',
  'area.label': 'Point at an area',
  'area.bar': 'Drag a frame or click two corners of the area your feedback is about.',
  'area.done': 'Area chosen.',
  'area.stop': 'Back',
  'area.cancelled': 'Back to pointing.',

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
  'composer.context.area': 'Your feedback is about the area you pointed at.',
  'composer.context.text': 'Your feedback is about this text:',
  'composer.context.page': 'Your feedback is about this whole page.',
  'composer.repoint': 'Choose another spot or area',
  'composer.comment': 'Your feedback',
  'editor.title': 'Text box with formatting',
  'composer.comment.empty': 'Write your feedback first.',
  'composer.comment.long': 'Your feedback cannot be longer than {max} characters.',
  'composer.comment.chars':
    'Your feedback contains a character that cannot be sent. Remove it and try again.',
  'composer.name.required': 'Your name',
  'composer.name.optional': 'Your name (optional)',
  'composer.name.empty': 'Fill in your name.',
  'composer.name.long': 'Your name is too long. At most {max} characters.',
  'composer.name.chars': 'Your name contains a character that cannot be sent.',
  'composer.anonymous.note': 'Without a name the site team does not know who this comes from.',
  'composer.account':
    'You send this with your account. What the site team sees of you was on the page where you gave consent.',
  'composer.privacy': 'Privacy statement',
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
  'shot.alt.area': 'Screenshot of what you see now, with the area you pointed at marked',
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
  'sent.noAccount': 'You sent this without an account. That is why you cannot see replies from the site team.',
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
  'mine.kind.area': 'Area',
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
  'mine.reply.long': 'Your reply cannot be longer than {max} characters.',
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

# TDR-0018: Notify of new feedback by email and in Mattermost

- Status: Accepted
- Date: 2026-11-21

## Context

`docs/design.md` §14 listed notifications of new feedback, by mail, in
Mattermost or otherwise, and email notification in general, as out of
scope. A site team then only learns of feedback by opening the Feedback
tab, so feedback on a preview under review waits until someone looks.

Three questions decided the shape:

- **Who gets an email.** Toon could mail every editor or admin of a site
  by role. A role says what someone may do, not what they want in their
  inbox, and a group admin of twenty sites would get mail about all of
  them.
- **What a message carries.** The comment is what makes a message worth
  reading, and it comes from a visitor, possibly anonymous. An inbox is
  forwarded, searched and kept outside Toon's retention; a team channel
  is where the team already talks about the site.
- **How often.** Feedback comes in bursts, during a review or after a
  link is shared. ZAD's `send-email` service gives a project one budget
  of at most 5000 messages a day, shared by every site of the
  installation, so a burst or an anonymous flood on one site could
  silence email for all of them.

## Decision

Toon notifies of new feedback, on the live site and on previews, by
email and through a Mattermost incoming webhook (`docs/design.md`
§16.16):

- **Each member chooses email for themselves**, on a site, on a group or
  on their profile; the narrowest level that is set wins and nothing set
  is no email. Recipients are decided when a message is sent: active,
  allowed to read the site's feedback, with an address the IdP verified,
  and never about their own feedback.
- **An email carries metadata only** (site, page, kind, time, whether a
  screenshot came with it) and a link per item. No comment, no submitter.
- **A Mattermost post carries the comment**, cut at 500 characters and
  rendered so it can mention, link or format nothing; never the
  submitter. A site admin sets the site's webhook, a group admin the
  group's, and a site without one posts to its group's.
- **Email is batched per site over 10 minutes; Mattermost posts each item
  at once.** Both windows are settings, and 0 is one message per item.
  The daily email cap (2000 by default) is counted in the database.
- **The webhook URL is stored as it is**, write-only in the API, and Toon
  posts only to hosts the operator lists in
  `TOON_FEEDBACK_WEBHOOK_HOSTS`.

## Consequences

- Batching email keeps a burst to one message per member per site per
  window, well under the shared daily budget; the cap stops email, not
  Mattermost, if it is reached anyway.
- One post per item arrives at once, carries its text where the team
  reads, and reads better than a digest. A flood is bounded by the
  widget's rate limits (§16.10); the Mattermost window can be raised
  without a release.
- Visitor text leaves Toon into the channel and stays there after the
  item is deleted or purged. The admin who sets a webhook is told so, and
  `docs/privacy.md` says so.
- The webhook URL is a secret in the database like the rest of its
  content, not encrypted: Toon has no key for it, and the one AES key it
  has (`TOON_AUDIT_IP_KEY`) belongs to the audit log and rotates on its
  own terms. A database dump gives whoever holds it the power to post
  in those channels.
- Email depends on ZAD's relay, which today offers no TLS on its
  submission port, so production runs with `TOON_SMTP_TLS=none` inside the
  cluster until it does.
- `docs/design.md` §14 no longer lists notifications of new feedback as
  out of scope. Inviting someone still sends no mail.

## Revisit when

ZAD's relay offers STARTTLS on its submission port (switch
`TOON_SMTP_TLS` to `starttls`), the daily cap is reached in practice,
or a site team asks for a digest in Mattermost instead of one post per
item.

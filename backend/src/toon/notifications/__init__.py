"""Notifications of new site feedback, by email and in Mattermost
(docs/design.md §16.16, docs/tdr/0018-notify-of-new-feedback.md).

`job.py` takes new items into batches and sends what is due; `recipients.py`
decides who gets an email, `mail.py` and `mattermost.py` write and send the
messages, `webhooks.py` holds a webhook URL to the hosts the operator allows,
and `texts.py` has the words of both, in Dutch and English.
"""

# Notifications

Where and when your reminders go, at `/me/notifications/` (**More**, then
**Notifications**). Everything on this screen is yours alone: nobody else sees your
channels, not even an administrator of the instance.

When the instance has notifications off, the screen says so and you cannot add or test a
channel; its administrator turns them on in the [settings](instance-settings.md).

## Channels

The list shows the name of each channel and the kind of service (`ntfy`, `tgram`,
`mailto`...). **Add a channel** takes a name and an Apprise address, such as
`ntfys://ntfy.sh/my-topic` or `tgram://123456789:bot-token/111222333`: see the
[guide](../guides/notifications.md) for the address of each service. The address is
kept encrypted and never shown again, on this screen or anywhere else.

Under the form, **Services** lists every service accepted, by family (messaging, push
notifications, text messages, email, self-hosted, on-call and alerting). Each one has an
example address, where its messages go (your server, the provider in the EU, the provider
in the region you choose, or the provider outside the EU or without saying), and a link to
its page in the Apprise documentation.

Opening a channel lets you:

- rename it, or turn it off without deleting it (**Enabled**);
- **Send a test message**: a real message to the service, sent in the background so that
  the screen never waits for it ([ADR 0070](../adr/0070-slow-network-calls-in-the-background.md)).
  Under the button the screen says "The test message is being sent." and, in a few seconds,
  the answer: "sent" or "could not be sent", without the service's own message, with the
  time. The service is given eight seconds; a test that has not ended after 30 seconds
  counts as not sent. Without JavaScript, or when the answer takes longer than about 25
  seconds, reload the page to see it. Five tests a minute; asking again while a test is
  running does not send a second message. When the address leads to a private network that
  the instance does not send to, nothing is sent and the screen says so: an administrator
  of the instance can allow it in the [settings](instance-settings.md);
- **Delete the channel**. To change an address, delete the channel and add another.

The link at the top of a channel leads back to **Notifications**.

## On this device

Shown when the instance has [notifications on the device](../guides/notifications.md#on-this-device)
on. The list shows the name of each device and the push service that carries its messages
(Google, Mozilla, Apple or Microsoft). [Today](today.md) offers to add the browser you are
using, with a name of its own; **Receive on this device** does the same with a name you give,
and asks the browser for its permission, and only then. When the browser blocks the
notifications of the site, a line says how to allow them again in the settings of the
browser. Opening a device lets you rename it, **Send a
test notification** (sent by the background worker, five a minute), or **Stop notifications
on this device**. The address of the device and its keys are kept encrypted and never shown.

## Reminders

- **First reminder (days before)**: how many days before the due date the first reminder
  comes, from 0 (the day itself) to 30. One by default.
- **Time of reminders**: reminders and the daily summary go out from this time of day.
  08:00 by default.
- **Quiet hours from** and **until**: nothing is sent in between; what was due goes out at
  their end. They may run past midnight (22:00 to 07:00). Give both or neither.
- **Daily summary**: one message a day for every task to remind, instead of one per task.
- **Time zone** of the times above (empty: the time zone of the instance).

The messages are in the language of your [display preferences](display-preferences.md).

**Download my notification settings** gives your channels (name, kind of service, never
the address) and these preferences as a JSON file. The same is in the
[API](../api/index.md).

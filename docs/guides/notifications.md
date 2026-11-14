# Notifications

HouseCairn reminds you of your tasks through the service you already read, among more than a
hundred that [Apprise](https://github.com/caronc/apprise) can reach: messaging (Telegram,
Signal, Matrix, Microsoft Teams, Rocket.Chat, Zulip...), push notifications (ntfy, Gotify,
Pushover, Pushbullet, Bark...), text messages (Free Mobile, SFR, Twilio...), email, and
services you host yourself.

## Before you start

Notifications are off on a new instance. An instance administrator turns them on in
**Instance administration, Settings, Notifications**, or with the variable
`HOUSECAIRN_NOTIFICATIONS_ENABLED=true` (see [Settings](../administration/settings.md)). The
background worker of the container then looks for reminders every five minutes.

Links to tasks in the messages need the public address of the instance
(`HOUSECAIRN_SITE_URL`). Without it, the message has no link.

## Add a channel

Open **More**, then [Notifications](../screens/notifications.md). Give the channel a name
and its Apprise address. Some examples, with `example.com` standing for your server:

| Service | Address |
| --- | --- |
| ntfy | `ntfys://ntfy.example.com/my-topic` |
| Gotify | `gotifys://gotify.example.com/app-token` |
| Telegram | `tgram://123456789:bot-token/111222333` |
| Signal (signal-cli REST API) | `signals://signal.example.com/+15555550100/+15555550101` |
| Matrix | `matrixs://user:password@matrix.example.com/#room:example.com` |
| Email | `mailtos://user:password@mail.example.com?to=me@example.com` |

For Telegram, put the token that BotFather gave the bot (digits, a colon, then letters)
and the number of the chat; for Signal, the number registered with signal-cli, then the
number to send to, both in international format.

The Apprise documentation lists the options of each service. Then open the channel and
**Send a test message**. If it does not arrive, check the address: the screen does not
repeat the service's error, since it may carry your secrets. The test is sent in the
background: the page says it is being sent, then shows the answer a few seconds later (reload
the page if it does not change). HouseCairn waits at most eight seconds per channel, for a
test as for a reminder: a service slower than that counts as not reached.

The address is encrypted, never shown again and never exported. Some addresses are refused:
services that would run something on the server, write to its logs or open a raw
connection, generic webhooks (`json://`, `xml://`, `form://`), options that make the server
read a file or add headers or fields to the request (`+name`, `-name`, `:name`), and
addresses of the server itself or of a cloud metadata service.

Addresses of a private network, such as a ntfy or a Gotify on your local network, are
refused too, unless an instance administrator turns on **Channels on the local network**
(**Instance administration, Settings, Notifications**; see
[Hardening](../administration/hardening.md)). An address that gives the IP address of such
a server is refused when you add the channel; a name that leads to one is refused when a
message is sent, and **Send a test message** then says so.

## Services

The [Notifications](../screens/notifications.md) screen lists every service accepted, by
family, with an example address and a link to its page in the Apprise documentation:

- **Messaging**: DingTalk, Discord, Feishu, Flock, Fluxer, Google Chat, GroupMe, Guilded,
  KOOK, Lark, LINE, Matrix, Mattermost, Microsoft Teams (through a Power Automate
  workflow), Notifico (IRC), QQ, Revolt, Rocket.Chat, Ryver, Slack, Stackfield, Telegram,
  Threema Gateway, Twist, Viber, Webex, WeChat Work, WeCom bot, WhatsApp Business, Zalo,
  Zoho Cliq, Zoom Team Chat, Zulip.
- **Push notifications**: Bark, Chanify, Dot., IFTTT, Join, Kumulos, Lauther, LFG Alarm,
  Notifiarr, ntfy, OneSignal, Pingram, Prowl, Pushbullet, PushDeer, Pushed, PushMe,
  Pushover, PushPlus, Pushsafer, PushWard, Pushy, ServerChan, Signalgrid, Simplepush, Spug
  Push, Streamlabs, Techulus Push, Trigv, WPush, WxPusher, YouLMK.
- **Text messages (SMS)**: 46elks, 800.com, Africa's Talking, Amazon SNS, BulkSMS, BulkVS,
  Burst SMS, Clickatell, ClickSend, D7 Networks, DAPNET, Exotel, Free Mobile, httpSMS,
  Kavenegar, MessageBird, Mobile Message, MSG91, Notifyre, Octopush, Plivo, RingCentral,
  SerwerSMS, seven, SFR, Sinch, SMS Manager, SMSC, Telnyx, Twilio, VoIP.ms, Vonage.
- **Email**: your own mail server (SMTP), Amazon SES, Brevo, MailerSend, Mailgun,
  Microsoft 365, Postmark, Resend, SendGrid, SendPulse, SMTP2GO, SparkPost.
- **Self-hosted**: Apprise API, Emby, Enigma2, Evolution API, Gorush, Gotify, HumHub,
  Jellyfin, Kodi, LaMetric Time, Nextcloud, Nextcloud Talk, OneBot, Parse Platform,
  Pushjet, Signal (signal-cli REST API), SMSEagle, Synology Chat.
- **On-call and alerting**: Better Stack, GoAlert, ilert, incident.io, PagerDuty,
  PagerTree, SIGNL4, Spike.sh, Splunk On-Call, Squadcast.

**Where your reminders go.** A self-hosted service, or one whose address names its server
(ntfy, Matrix, Mattermost...), sends to that server. Microsoft Teams workflows, Fluxer,
Notifico, PushDeer and Better Stack send to their provider unless the address names a server
of your own. Amazon SNS and SES, Exotel, Mailgun, Pingram, Sinch, SparkPost, PagerDuty and
Squadcast send to the region that the address chooses. Stackfield, Pushsafer, Free
Mobile, SFR, Octopush, seven, 46elks, SerwerSMS, SMS Manager, DAPNET, Brevo and ilert are
providers in the European Union. Every other service sends your reminders to its provider,
outside the European Union or without saying where. The screen says the same for each
service.

Some services are not offered, each for a reason given in the
[decision record](../adr/0064-every-apprise-service-with-a-fixed-format.md): Home Assistant
(it would need a Home Assistant token), generic webhooks, social networks that publish a
public post (Mastodon, Bluesky, X...), and services that need a file on the server or that
do not speak HTTP or SMTP.

## On this device

Reminders and problem reports can also appear on your phone or computer as notifications,
with no other application: HouseCairn sends them through the push service of your browser
(Web Push). An instance administrator turns this on with **Notifications on the device**
(**Instance administration, Settings, Notifications**, or
`HOUSECAIRN_WEBPUSH_ENABLED=true`), on top of notifications themselves.

When Web Push is on, HouseCairn offers it to you: a banner at the top of
[Today](../screens/today.md), **Receive the reminders on this device?**, shown in the browser
and when you open the installed application.

1. Select **Turn on**. The browser asks whether the site may show notifications: allow it. It
   asks only then, never on its own.
2. The device is added with a name made from the browser (*Chrome on Android*, *Firefox on
   Windows*, *iPhone*), and the banner says so. Rename it in
   [Notifications](../screens/notifications.md) if you like.
3. Open the device in the list and **Send a test notification**.

**Later** hides the banner for seven days; **Do not ask again** hides it in this browser for
good (another browser is still offered the device). The banner does not come back in a
browser that is already one of your devices, nor where notifications are blocked. If you
refused the permission, the browser does not ask a second time: allow notifications for the
site in the browser settings (the icon next to the address, or the site settings). The
[Notifications](../screens/notifications.md) screen says so, and still lets you give a name
and select **Receive on this device** yourself.

On an iPhone or an iPad (iOS 16.4 or later), install the application first: in Safari,
**Share**, then **Add to Home Screen**, then open it from the home screen. In Safari, before
that, the banner says so and has no **Turn on**. Android and computers need no installation.

Who carries the message depends on the browser: Google for Chrome, Edge, Brave, Opera and
Samsung Internet, Mozilla for Firefox, Apple for Safari, Microsoft for the former Edge.
Some of these services are outside the EU. The message is encrypted for your device: the
push service sees when a message is sent and its size, not what it says. A notification
holds a title, a line of text and the screen it opens. When the instance asks for
**discreet** notifications (the default), the line says only that a task is due, that tasks
are due, or that a problem was reported: no title of a task and no name of a home, so
nothing shows on a locked screen.

To stop, open the device and select **Stop notifications on this device**. A device the
browser no longer knows (notifications blocked, data cleared) is removed at the next
message. Signing out does not stop them: on a shared computer, stop them first. Deleting
your account removes your devices.

## Choose when

A reminder says the title of the task, the home, the due date and a link: nothing else of
the task leaves HouseCairn. It goes to the person in charge of the task. When nobody is,
or when that person cannot receive it (they only read the home now, have no channel on,
or their account is deactivated), it goes to every member of the home who may change it
and has a channel on. A reader of the home receives nothing.

- The first reminder comes one day before the due date by default, from 08:00 in your
  time zone. Change both in **Reminders**.
- Then a follow-up comes every day for a high priority, every three days for a normal one,
  every week for a low one, until the task is done or skipped. Postponing it stops them
  until the new date.
- **Quiet hours**: nothing is sent between the two times; what was due goes out at their
  end.
- **Daily summary**: one message a day with every task to remind, instead of one each.
  Turned on before the time of reminders, the first one comes at that time.

Each reminder is sent once, even if the worker runs twice at the same time. When a service
fails, the reminder is not sent again; the next follow-up is.

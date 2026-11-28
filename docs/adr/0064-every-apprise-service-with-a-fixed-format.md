# 0064. Every Apprise service with a fixed format, each one reviewed

- Status: accepted
- Date: 2026-11-12
- Amends: [0043](0043-notifications-through-apprise.md) (the list of schemes)

## Context

ADR 0043 accepted eleven services. People use many more (Microsoft Teams, Rocket.Chat,
Zulip, Pushbullet, Bark, the SMS gateways of their operator), and Apprise 2.2.0, the
version of `uv.lock`, has 183 plugins and 255 schemes (2.1.0, which this record first
reviewed, had 171 and 240). A channel makes the server send a
request to an address a person typed, so a service is only added after reading its plugin:
the method, the host, the path, the format of the body, the headers, and the options of
the address that change the request.

## Decision

**Criteria.** A plugin is admitted when all of these hold, read in its code:

1. It speaks HTTP(S) or SMTP, through `requests` or `smtplib`: nothing that runs a
   program, writes to a local log, drives a device of the server or opens a raw socket.
2. The plugin fixes the method and the format of the body of every request. No option of
   the address chooses the method, the whole path, a header or a field: the generic
   webhooks `json`, `xml` and `form` stay out, and `+name`, `-name` and `:name` stay
   refused for every scheme (ADR 0043). A typed path prefix followed by a fixed path, as
   for Gotify, is accepted.
3. A typed host goes through the guard of ADR 0059: private networks refused unless an
   administrator allows them, loopback, link-local and metadata services always refused,
   every name resolved and every connection checked, no redirect followed.
4. No file of the server is read: a plugin whose options name a file (a key, a template)
   is refused, or its URL is when the option is there (ADR 0043).
5. No image from the network in a message (`include_image` off), and the total delay of a
   send (eight seconds) holds, as for every channel.
6. No Home Assistant token (golden rule 3): `hassio` is refused.
7. No public post: a reminder names a task and a home, which must not end up on a public
   timeline or in a public repository.
8. Where a message goes is said: the screen and the guide say, for each service, whether
   it goes to the server of the person, to the provider in the EU, to the provider in the
   region the address chooses, or to the provider outside the EU (or without saying).

**The list is in the code.** `notifications.catalog` holds every admitted service, with its
family (messaging, push, SMS, email, self-hosted, on-call and alerting), its schemes, an
example address, where its messages go and the page of the Apprise documentation.
`outbound.ALLOWED_SCHEMES` is built from it. The screen of notifications lists it by family.

**Tests.** For each admitted service, its example address is accepted, and a send with the
network intercepted makes its first request with the expected method to the expected host.
The rules of ADR 0043 are checked on each plugin (certificates, delays, no retry, no image),
its address is shown nowhere (screens, export, API), and for each service with a typed host
a private address typed as is, or a name that resolves to one, is refused. Every other
scheme of the installed Apprise is refused, and a test reads the table below: every scheme
of Apprise must be in it, the admitted ones must be the list of the code. A new version of
Apprise with a new plugin fails that test until the plugin is reviewed here.

**The Kodi plugin** keeps the version of its protocol in an attribute named `protocol`: the
check that the scheme parsed is the one typed now reads the schemes of the class.

### Inventory of Apprise 2.2.0

"Requests" gives the methods the plugin uses and whether the host is fixed (the API of the
provider, or one of its regions) or typed (the server of the person, under the guard).
Some services have a fixed host by default and take a typed one in another form of the
address: Microsoft Teams (workflows), Fluxer, Notifico, PushDeer and Better Stack. They are
marked typed here, and those forms go through the guard of ADR 0059 like the others.

| Schemes | Service | Verdict | Requests, or why refused |
| --- | --- | --- | --- |
| `tgram` | Telegram | admitted | POST, host fixed |
| `discord` | Discord | admitted | POST, host fixed |
| `slack` | Slack | admitted | GET/REQUEST, host fixed |
| `matrix`, `matrixs` | Matrix | admitted | GET/POST/PUT, host typed |
| `mmost`, `mmosts` | Mattermost | admitted | GET/POST, host typed |
| `rocket`, `rockets` | Rocket.Chat | admitted | POST, host typed |
| `zulip` | Zulip | admitted | POST, host typed |
| `workflow`, `workflows` | Microsoft Teams (Power Automate workflow) | admitted | POST, host typed |
| `webex`, `wxteams` | Webex | admitted | POST, host fixed |
| `gchat` | Google Chat | admitted | POST, host fixed |
| `threema` | Threema Gateway | admitted | POST, host fixed |
| `whatsapp` | WhatsApp (Business API) | admitted | POST, host fixed |
| `line` | LINE | admitted | POST, host fixed |
| `viber` | Viber | admitted | POST, host fixed |
| `revolt` | Revolt | admitted | POST, host fixed |
| `guilded` | Guilded | admitted | HTTPS POST, host fixed |
| `fluxer`, `fluxers` | Fluxer | admitted | POST, host typed |
| `flock` | Flock | admitted | POST, host fixed |
| `ryver` | Ryver | admitted | POST, host fixed |
| `twist` | Twist | admitted | GET/POST, host fixed |
| `cliq`, `zcliq`, `zohocliq` | Zoho Cliq | admitted | POST, host fixed |
| `zoom` | Zoom Team Chat | admitted | POST, host fixed |
| `stackfield` | Stackfield | admitted | POST, host fixed |
| `groupme` | GroupMe | admitted | POST, host fixed |
| `dingtalk` | DingTalk | admitted | POST, host fixed |
| `feishu` | Feishu | admitted | POST, host fixed |
| `lark` | Lark | admitted | POST, host fixed |
| `wechat` | WeChat Work (WeCom) | admitted | GET/POST, host fixed |
| `wecombot` | WeCom bot | admitted | POST, host fixed |
| `kook` | KOOK | admitted | POST, host fixed |
| `qq` | QQ (Qmsg) | admitted | POST, host fixed |
| `zalo` | Zalo | admitted | POST, host fixed |
| `kakaotalk`, `kakao` | KakaoTalk | admitted | POST (token, then message), host fixed |
| `gupshup` | Gupshup (WhatsApp) | admitted | POST, host fixed (the `template` option picks one of two fixed paths) |
| `whapi` | Whapi.Cloud (WhatsApp) | admitted | POST, host fixed |
| `notifico`, `notificos` | Notifico (IRC) | admitted | GET, host typed |
| `ntfy`, `ntfys` | ntfy | admitted | POST, host typed |
| `pover` | Pushover | admitted | POST, host fixed |
| `pbul` | Pushbullet | admitted | POST, host fixed |
| `bark`, `barks` | Bark | admitted | POST, host typed |
| `psafer`, `psafers` | Pushsafer | admitted | POST, host fixed |
| `prowl` | Prowl | admitted | POST, host fixed |
| `simplepush`, `spush` | Simplepush | admitted | GET/POST/PUT, host fixed |
| `join` | Join | admitted | POST, host fixed |
| `pushed` | Pushed | admitted | POST, host fixed |
| `pushy` | Pushy | admitted | POST, host fixed |
| `pushdeer`, `pushdeers` | PushDeer | admitted | POST, host typed |
| `pushme` | PushMe | admitted | POST, host fixed |
| `pushplus`, `wecom` | PushPlus | admitted | POST, host fixed |
| `schan` | ServerChan | admitted | POST, host fixed |
| `chanify` | Chanify | admitted | POST, host fixed |
| `wxpusher` | WxPusher | admitted | POST, host fixed |
| `wpush` | WPush | admitted | POST, host fixed |
| `spugpush` | Spug Push | admitted | POST, host fixed |
| `push` | Techulus Push | admitted | POST, host fixed |
| `ifttt` | IFTTT | admitted | POST, host fixed |
| `onesignal` | OneSignal | admitted | POST, host fixed |
| `kumulos` | Kumulos | admitted | POST, host fixed |
| `notifiarr` | Notifiarr | admitted | POST, host fixed |
| `pushward` | PushWard | admitted | POST, host fixed |
| `pingram` | Pingram | admitted | POST, host fixed |
| `signalgrid` | Signalgrid | admitted | POST, host fixed |
| `lauther` | Lauther | admitted | POST, host fixed |
| `dot` | Dot. | admitted | POST, host fixed |
| `lfg` | LFG Alarm | admitted | POST, host fixed |
| `youlmk` | YouLMK | admitted | POST, host fixed |
| `trigv`, `trigvs` | Trigv | admitted | POST, host fixed |
| `strmlabs` | Streamlabs | admitted | POST, host fixed |
| `freemobile` | Free Mobile | admitted | POST, host fixed |
| `sfr` | SFR | admitted | POST, host fixed |
| `octopush` | Octopush | admitted | POST, host fixed |
| `seven` | seven | admitted | POST, host fixed |
| `46elks`, `elks` | 46elks | admitted | POST, host fixed |
| `serwersms` | SerwerSMS | admitted | POST, host fixed |
| `smsmanager`, `smsmgr` | SMS Manager | admitted | GET, host fixed |
| `twilio` | Twilio | admitted | POST, host fixed |
| `vonage`, `nexmo` | Vonage | admitted | POST, host fixed |
| `sinch` | Sinch | admitted | POST, host fixed |
| `plivo` | Plivo | admitted | POST, host fixed |
| `telnyx` | Telnyx | admitted | POST, host fixed |
| `msgbird` | MessageBird | admitted | POST, host fixed |
| `clicksend` | ClickSend | admitted | POST, host fixed |
| `clickatell` | Clickatell | admitted | GET, host fixed |
| `bulksms` | BulkSMS | admitted | POST, host fixed |
| `bulkvs` | BulkVS | admitted | POST, host fixed |
| `burstsms`, `kudosity` | Burst SMS (Kudosity) | admitted | POST, host fixed |
| `d7sms` | D7 Networks | admitted | POST, host fixed |
| `httpsms` | httpSMS | admitted | POST, host fixed |
| `atalk` | Africa's Talking | admitted | POST, host fixed |
| `exotel` | Exotel | admitted | POST, host fixed |
| `eight00com` | 800.com | admitted | POST, host fixed |
| `kavenegar` | Kavenegar | admitted | POST, host fixed |
| `msg91` | MSG91 | admitted | POST, host fixed |
| `mobilemessage`, `mobilemsg` | Mobile Message | admitted | POST, host fixed |
| `notifyre` | Notifyre | admitted | POST, host fixed |
| `ringc` | RingCentral | admitted | POST, host fixed |
| `smsc` | SMSC | admitted | POST, host fixed |
| `voipms` | VoIP.ms | admitted | GET, host fixed |
| `cm` | CM.com | admitted | POST, host fixed |
| `infobip` | Infobip | admitted | POST, host typed (the account host, under the guard) |
| `kaleyra`, `klr` | Kaleyra | admitted | POST, host fixed by the region (one of three, checked by the plugin) |
| `smstunnel` | SMSTunnel | admitted | POST, host fixed |
| `telesign` | Telesign | admitted | POST, host fixed |
| `sns` | Amazon SNS | admitted | POST, host fixed |
| `dapnet` | DAPNET (amateur radio pager) | admitted | POST, host fixed |
| `mailto`, `mailtos` | Email (SMTP) | admitted | SMTP, host typed |
| `brevo` | Brevo | admitted | POST, host fixed |
| `mailgun` | Mailgun | admitted | POST, host fixed |
| `sparkpost` | SparkPost | admitted | POST, host fixed |
| `ses` | Amazon SES | admitted | POST, host fixed |
| `azure`, `o365` | Microsoft 365 | admitted | GET/POST/PUT, host fixed |
| `sendgrid` | SendGrid | admitted | POST, host fixed |
| `postmark` | Postmark | admitted | POST, host fixed |
| `resend` | Resend | admitted | POST, host fixed |
| `mailersend` | MailerSend | admitted | POST, host fixed |
| `smtp2go` | SMTP2GO | admitted | POST, host fixed |
| `sendpulse` | SendPulse | admitted | POST, host fixed |
| `groundmail` | Groundmail | admitted | POST, host fixed |
| `gotify`, `gotifys` | Gotify | admitted | POST, host typed |
| `signal`, `signals` | Signal (signal-cli REST API) | admitted | POST, host typed |
| `apprise`, `apprises` | Apprise API | admitted | POST, host typed |
| `ncloud`, `nclouds` | Nextcloud | admitted | GET/POST, host typed |
| `nctalk`, `nctalks` | Nextcloud Talk | admitted | POST, host typed |
| `synology`, `synologys` | Synology Chat | admitted | POST, host typed |
| `humhub`, `humhubs` | HumHub | admitted | POST, host typed |
| `smseagle`, `smseagles` | SMSEagle | admitted | POST, host typed |
| `evolution`, `evolutions` | Evolution API (WhatsApp) | admitted | POST, host typed |
| `onebot`, `onebots` | OneBot | admitted | POST, host typed |
| `gorush`, `gorushs` | Gorush | admitted | POST, host typed |
| `parsep`, `parseps` | Parse Platform | admitted | POST, host typed |
| `pjet`, `pjets` | Pushjet | admitted | POST, host typed |
| `lametric`, `lametrics` | LaMetric Time | admitted | POST, host typed |
| `emby`, `embys` | Emby | admitted | GET/POST, host typed |
| `jellyfin`, `jellyfins` | Jellyfin | admitted | HTTP(S) POST, host typed |
| `kodi`, `kodis`, `xbmc`, `xbmcs` | Kodi | admitted | POST, host typed |
| `enigma2`, `enigma2s` | Enigma2 | admitted | GET, host typed |
| `pagerduty` | PagerDuty | admitted | POST, host fixed |
| `splunk`, `victorops` | Splunk On-Call (VictorOps) | admitted | POST, host fixed |
| `squadcast` | Squadcast | admitted | POST, host fixed |
| `ilert` | ilert | admitted | POST, host fixed |
| `incidentio` | incident.io | admitted | POST, host fixed |
| `signl4` | SIGNL4 | admitted | POST, host fixed |
| `pagertree` | PagerTree | admitted | POST, host fixed |
| `spike` | Spike.sh | admitted | POST, host fixed |
| `betterstack` | Better Stack | admitted | POST, host typed |
| `goalert`, `goalerts` | GoAlert | admitted | POST, host typed |
| `firehydrant` | FireHydrant | admitted | POST, host fixed |
| `xmatters` | xMatters | admitted | POST, host typed |
| `amqp`, `amqps` | AMQP | refused | not HTTP(S) or SMTP: its own protocol over a raw socket (AMQP) |
| `aprs` | Aprs | refused | not HTTP(S) or SMTP: its own protocol over a raw socket (APRS-IS) |
| `blink1` | blink(1) | refused | acts on the server itself, not on a service (a USB light of the server) |
| `bluesky`, `bsky` | BlueSky | refused | publishes a public post by default: the reminders would be public |
| `dbus`, `kde`, `qt` | DBus Notification | refused | acts on the server itself, not on a service (D-Bus desktop notification) |
| `deltachat`, `deltachats` | Delta Chat | refused | not admitted: the PGP modes of the plugin were not reviewed |
| `fcm` | Firebase Cloud Messaging | refused | reads a key file on the server (`keyfile=`) |
| `flowtriq`, `flowtriqs` | Flowtriq | refused | posts to a webhook path chosen whole by the address |
| `form`, `forms` | Form | refused | generic webhook: the address chooses the method, the path, the headers and the fields |
| `gio`, `glib` | GLib Notification | refused | acts on the server itself, not on a service (GLib desktop notification) |
| `github` | GitHub | refused | opens an issue in a repository, which may be public |
| `gnome` | Gnome Notification | refused | acts on the server itself, not on a service (GNOME desktop notification) |
| `growl` | Growl | refused | not HTTP(S) or SMTP: its own protocol over a raw socket (GNTP) |
| `hassio`, `hassios` | HomeAssistant | refused | needs a Home Assistant token, which carries every right of its user (golden rule 3) |
| `irc`, `ircs` | IRC | refused | not HTTP(S) or SMTP: its own protocol over a raw socket (IRC) |
| `jira` | Jira | refused | an option (`action=`) chooses the method (DELETE) and the path of the request |
| `json`, `jsons` | JSON | refused | generic webhook: the address chooses the method, the path, the headers and the fields |
| `macosx` | MacOSX Notification | refused | acts on the server itself, not on a service (runs a program) |
| `mastodon`, `mastodons`, `toot`, `toots` | Mastodon | refused | publishes a public post by default: the reminders would be public |
| `misskey`, `misskeys` | Misskey | refused | publishes a public post by default: the reminders would be public |
| `mqtt`, `mqtts` | MQTT Notification | refused | not HTTP(S) or SMTP: its own protocol over a raw socket (MQTT) |
| `nostr` | Nostr | refused | publishes a public note to relays over WebSocket (`wss://`): not HTTP(S) or SMTP, and the reminders would be public |
| `notica`, `noticas` | Notica | refused | its self-hosted form posts to a path chosen whole by the address |
| `opsgenie` | Opsgenie | refused | an option (`action=`) chooses the method (DELETE) and the path of the request |
| `pinglet`, `pinglets` | Pinglet | refused | posts to a path chosen whole by the address (prefix, namespace, topic) |
| `reddit` | Reddit | refused | publishes a public post by default: the reminders would be public |
| `rsyslog` | Remote Syslog | refused | not HTTP(S) or SMTP: its own protocol over a raw socket (syslog over UDP or TCP) |
| `session`, `sessions`, `sogs` | Session Open Group Server | refused | not admitted: no example could be checked in this review (keys of Session) |
| `smpp`, `smpps` | SMPP | refused | not HTTP(S) or SMTP: its own protocol over a raw socket (SMPP) |
| `syslog` | Syslog | refused | acts on the server itself, not on a service (writes to the local log) |
| `tweet`, `twitter`, `x` | Twitter | refused | publishes a public post by default: the reminders would be public |
| `vapid` | Vapid Web Push Notifications | refused | reads a key file and a subscription file on the server (`keyfile=`, `subfile=`) |
| `windows` | Windows Notification | refused | acts on the server itself, not on a service (Windows desktop notification) |
| `xml`, `xmls` | XML | refused | generic webhook: the address chooses the method, the path, the headers and the fields |
| `xmpp`, `xmpps` | XMPP | refused | not HTTP(S) or SMTP: its own protocol over a raw socket (XMPP) |

## Consequences

- 148 services, 195 schemes, are accepted; 35 plugins, 60 schemes, are refused, each for a reason above.
- Apprise 2.2.0 added eleven plugins and one scheme (`kudosity`, an alias of Burst SMS).
  Ten of the plugins are admitted on the criteria above; Nostr is refused.
- A service refused for want of review (Session, Delta Chat) can be admitted by a later
  record, after its review.
- An update of Apprise that adds a plugin, or a scheme to a plugin, fails the tests until
  this table and the list say what to do with it.

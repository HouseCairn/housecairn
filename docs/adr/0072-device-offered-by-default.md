# 0072. The device is offered by default: one offer, one tap

- Status: accepted
- Date: 2026-11-14
- Extends: [0066](0066-notifications-on-the-device-by-web-push.md) (notifications on the
  device by Web Push), [0065](0065-installable-application-and-offline-reading.md)
  (installable application)

## Context

Receiving the reminders on the phone (0066) took a detour: open **Notifications**, name the
device, select the button. Few people do. The maintainer asked that the device be offered, and
added by a single gesture, as soon as the instance sends notifications to devices, in the
installed application and on the web.

The browsers set the limit of "by default". A permission to show notifications can only be
asked after a gesture of the person: Safari refuses a request that does not follow a tap, and
Chrome and Firefox quiet or block one that comes with no gesture (a page that asks on load is
also the one people learn to dismiss). A subscription is useless without the permission. So the
device cannot be added silently: it can be **offered at once and accepted in one tap**.

## Decision

- **An offer on Today, never a request on load.** A banner, "Receive the reminders on this
  device?", with **Turn on**, **Later** and **Do not ask again**, at the top of Today (of one
  home and of all homes together). The installed application opens on Today, so it shows it at
  its first opening (`display-mode: standalone`) with no code of its own.
- **Which side decides.** The server renders the offer (hidden) only when notifications and Web
  Push are both on, a person is signed in and the instance is not a demonstration (nothing is
  sent there, 0071); otherwise the page is what it was. `static/js/webpush.js` reveals it only
  if the browser can receive, has not blocked the notifications of the site, is not already a
  device of the person, and the person did not postpone or refuse it.
- **This browser is a device of the person** when its push subscription exists and the SHA-256
  of its endpoint is among the `endpoint_hash` of the person's devices, which the page gives
  (hashes, never an endpoint nor a key). A browser still subscribed after its device was
  deleted is offered again, and the tap renews it.
- **One tap.** **Turn on** asks the permission as the first thing of the tap, subscribes,
  posts the subscription to the same API as the form of Notifications (0066: session, form
  token, same validation, same limit of five devices), and says so on the banner with a link
  to rename the device in Notifications. A refusal of the browser or of the server says so in
  words and saves nothing. It gives the subscription back only if this tap created it: a
  subscription that the browser already had (a device that works, or one of another account)
  is kept, whatever the server answered (400, 403, 500).
- **A name made from the browser**: "iPhone", "iPad", "Chrome on Android", "Firefox on
  Windows", "Safari on macOS" (the browser and the system read from the user agent; the pattern
  "browser on system" and the fallback "This device" are translated, the names of products are
  not). At most 60 characters, editable afterwards. A user agent is a hint, not an identity:
  the name is only a label.
- **Later and Do not ask again** are kept in the browser (`localStorage`, one key per person),
  not on the server: they are about this browser, they need no migration and no API, and an
  account used on a second phone is offered there. Later is seven days. Where the storage is
  refused (a private window), the offer comes back at the next page. Nothing is sent.
- **An iPhone or an iPad in Safari, not installed**: Web Push exists there only for an
  installed application (iOS 16.4 or later), so the browser has no `PushManager`. The offer
  says to install first (Share, Add to Home Screen), with no **Turn on**, and can be postponed
  or silenced like the other. Opened from the home screen, it is the normal offer.
- **Permission already refused**: no offer. **Notifications** says, on the device section,
  how to turn it back on in the settings of the browser (a page cannot ask again).
- **The form of Notifications shares `enroll`** with the offer: same permission-first tap,
  same refusal rules (including keeping an earlier subscription), and the same line, shown
  when the browser blocks the site. It still takes a name typed by the person.
- **Unchanged**: the discreet content (0066, the default), the settings, the limits and the
  API. No new dependency, no style in the page (design guide).

## Consequences

- A person who wants the reminders on the phone needs two taps: **Turn on**, then **Allow** in
  the prompt of the browser. That is the least the browsers allow.
- The check "is this browser already a device" is made by the script, so a browser with no
  service worker (a private window, storage refused) is never offered a device it could not
  keep.
- The offer is a banner on Today, not a screen of the installation: a person who installs the
  application and never opens Today is not offered, and can still use Notifications.
- What no test of the repository does: the prompt of a real browser, a real push service, and
  Safari on an iPhone with the application installed. The end-to-end checks simulate
  `PushManager` and `Notification` (`tests/e2e/device_offer_checks.py`); the rest is checked by
  hand:
  - on an Android phone with Chrome: the banner, the prompt after one tap, the name, a test
    notification from the device;
  - on an Android phone with the application installed: the banner at its first opening;
  - on an iPhone in Safari: the advice to install, with no button to turn on;
  - on an iPhone with the application installed: the banner, the prompt after one tap, a
    test notification on the locked screen;
  - Firefox and Safari on a computer: the prompt after one tap;
  - "Do not ask again" and "Later" survive closing the browser; blocking the site in the
    browser settings removes the banner and shows the line in Notifications.

# Calendar and feeds

HouseCairn shows your tasks in a [calendar](../screens/calendar.md) and publishes them as an
iCalendar feed (RFC 5545), so that the calendar you already use can show them.

## Create a feed

Open **More**, then **Calendar in another application**, and press **Create** for a home or for all your homes.
Copy the address that appears: it is shown once. It looks like
`https://housecairn.example.com/calendar/feed/<secret>.ics`.

The address needs the public address of the instance (`HOUSECAIRN_SITE_URL`, see
[Settings](../administration/settings.md)); without it the address uses the one of the page you
are on, which may not be the one your calendar application can reach.

The feed is **read only** and **updates by itself** as tasks change, but each application polls
at its own pace: HouseCairn asks for a refresh every six hours, and applications are free to
ignore it. Google Calendar in particular can take many hours to show a change. On the
HouseCairn side a change shows in the feed at once; the document is only kept for five minutes
at most, to spare the server when an application polls often.

## What a feed holds

For each task of the homes in the feed, from 30 days ago to one year ahead: its title, home,
room, date, status (planned, done, postponed, skipped, forecast) and a link to the task. The
date is an all-day event, without a time. It holds no note, cost, procedure, file, person or
email address, and it is not an API token: it opens this feed and nothing else.

An event keeps its identity when the task moves on: the forecast of a repeating task becomes
the real occurrence when the previous one is done, and a postponed task keeps the identity of
the date it was first due, so your calendar application updates the event instead of showing a
new one. One case makes a new event: a task that repeats a set time after the last one, done on
a day other than its due date, gets a next date that was not the forecast one.

It follows the time zone you chose in [Notifications](../screens/notifications.md) (the time
zone of the instance when empty) and the language of your
[display preferences](../screens/display-preferences.md).

## Subscribe

The steps below are those of each application's own documentation. The project has not run
them on every application: what was checked is that the document is valid iCalendar, read back
by the `icalendar` library in the tests.

- **Google Calendar**: in a browser on a computer, **Other calendars**, **+**, **From URL**,
  paste the address. A subscribed calendar is read only and Google refreshes it when it wants.
- **Apple Calendar**: on a Mac, **File**, **New Calendar Subscription**, paste the address, and
  choose how often to refresh. On an iPhone, **Settings**, **Calendar**, **Accounts**, **Add
  Account**, **Other**, **Add Subscribed Calendar**.
- **Thunderbird**: **New Calendar**, **On the Network**, paste the address, format
  **iCalendar (ICS)**. No credentials are needed; the secret is in the address.
- **Home Assistant**: **Settings**, **Devices & services**, **Add integration**, **Remote
  Calendar**, paste the address. What was checked: the page of the integration in the Home
  Assistant documentation says it reads a calendar from a URL and only reads it, and that its
  entity shows the next upcoming event. The same page says that Home Assistant leaves out
  canceled events (a skipped task, which the feed marks `STATUS:CANCELLED`, does not appear and
  triggers nothing) and, after the first fetch, polls the calendar every 24 hours, so a change
  reaches it with that delay, whatever the refresh the feed suggests. A task marked done stays
  in the feed, with `[Done]` in front of its title, and so stays visible to Home Assistant. Whether
  Home Assistant reads this feed was **not** tested.

## Keep it secret

- The address is the only credential: do not post it, and prefer one feed per person.
- **New address** on **Calendar feeds** stops the old one. **Revoke** deletes the feed.
- A feed stops working when the person leaves the home (that home's feed is revoked), when the
  account is deactivated or deleted, or when the home is deleted.
- A home that requires a sign-in provider has no feed, since the address cannot prove which
  provider opened a session.
- The address is in the URL, so it can end up in logs. HouseCairn masks it itself: its access
  log and its warnings write `/calendar/feed/***.ics`. **Your reverse proxy writes it too**, and
  HouseCairn cannot change that: turn off or mask the access log of that path there (nginx
  example in [Reverse proxy](../administration/reverse-proxy.md)). Treat any log that held an
  address as a leak and regenerate the address.
- The address is rate limited (60 requests a minute per client address). An unknown address
  and a revoked one get the same answer.

# 0074. One language per person, everywhere they read HouseCairn

- Status: accepted
- Date: 2026-11-14
- Completes: [0042](0042-rate-limits-quotas-language-and-instance-name.md) (its paragraph
  *Language*)

## Context

ADR 0042 let a person choose a language in their display preferences, ahead of the language
cookie, the browser and the default of the instance. In use, the choice did not hold
everywhere:

- The notification preferences had a language of their own, `English` unless changed, for
  the reminders, the notifications on a device, the test message of a channel and the
  calendar feeds. A person who chose French for the pages still got English reminders; a
  person with no notification preferences got the language of the request that first read
  them, and in a worker, English, whatever the default of the instance.
- The answer to the change itself was in the former language ("Preferences saved."), and so
  was the message of the sign-in ("Successfully signed in as..."), shown on the first page
  in the new one.
- A call with an API token kept the language of `Accept-Language`.
- The pages kept for reading offline (ADR 0065) stayed in the former language until the
  same page was opened again, and the offline page was in the language of the visit that
  installed the service worker.
- The choice was hard to find: **More** described the display preferences as "how the Today
  screen is laid out", and the profile did not mention it. A signed-out visitor had no way
  to choose but the browser's settings.

## Decision

**The rule.** For everything a person reads, online, offline and in what is sent to them:

1. the language they chose in their display preferences;
2. else, for a page or an API answer, the language cookie, then the browser's
   `Accept-Language` (matched as Django does);
3. else the default language of the instance.

What is sent without a request (a reminder, a daily summary, a notification on a device, a
problem report alert, the test message of a channel, a calendar feed) has no browser: it
takes the person's choice, then the default of the instance. `core.language` holds the rule;
every sender calls `language_of(user)`.

**One field.** The language is in the display preferences only (`DisplayPreference.language`,
empty for Automatic). The notification preferences lose theirs: a migration copies it into
the display preferences of each person who had chosen none there, so a person who had
French reminders keeps them, then the field is removed from the model, the form, the API and
the export of the notification settings. The profile shows the language in force and leads
to the display preferences; **More** names the language in their description.

**At once.** Saving the display preferences activates the new language for the answer
itself, so its message and the page it leads to are in it. A chosen language is also written
to the language cookie, so the sign-in page keeps it after signing out; **Automatic**
removes the cookie, and the browser decides again. Signing in activates the person's choice
before the sign-in message is worded. A request authenticated by an API token takes the
person's choice too, since the token authentication runs in the view, after the middleware.

**Offline.** The key under which the service worker keeps a page includes the language: a
page opened in another language replaces the pages kept in the former one. After a change of
language, the next page also carries `HouseCairn-Offline: purge`, once, so nothing in the
former language survives even if Today is not opened again. The offline page is precached in
each language (`/offline/?lang=fr`); the service worker shows the one in the language of the
pages it kept (their `Content-Language`), else of the browser, else the default.

**Signed out.** The signed-out pages end with a small form, **Language** and **Change**, that
posts to Django's `set_language` view and sets the language cookie. Signed in, it is not
shown: the display preferences decide.

## Consequences

- The `language` field disappears from `GET` and `PATCH /api/v1/me/notification-preferences/`
  and from the export of the notification settings; a client that sends it is ignored. The
  language is read and changed at `/api/v1/me/display-preferences/` (`/me/preferences/` is the web page, not the API).
- A person who had chosen a language for the notifications only now has it on the pages too.
  The migration cannot tell a deliberate choice from the language of the request that first
  saved those preferences; both were the person's language when they saved.
- The language cookie now follows a choice made in the display preferences; it stays a
  cookie of the language only, read by Django.
- The admin site follows the same rule: it is a page like the others.

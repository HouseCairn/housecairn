# 0089. An archived home is read only, on the web as in the mobile app

- Status: accepted. That an archived home is read only, on the web as in the app, is the
  maintainer's decision of 2026-11-20; the list of exceptions below and the code of the sync API
  are what this record decides with it.
- Date: 2026-11-20
- Related: [0079](0079-lots-of-the-server-mode-of-the-mobile-app.md) (lot M1, archiving, which
  said "it stays readable and editable when opened": replaced here),
  [0077](0077-sync-api-for-the-mobile-app.md) (the results of a push),
  [0086](0086-a-read-only-member-syncs-their-own-reports.md) (what a member sees),
  mobile app: `_writableHome` and the refusal `homeArchived` of its repository.

## Context

Lot M1 made an archived home listed apart, silent and hidden from Today, but left it editable
when opened by its address: `can_write` read the role only. The mobile app made the other
choice: every change of an archived home is refused there (`homeArchived`), but bringing it
back. A home could then be changed on the web and not on the phone, and a phone's push to an
archived home was applied. The maintainer decided on 2026-11-20 that an archived home is read
only, on the web as on the app.

## Decision

**The rule is in `homes.services.can_write`**: a role that writes, in a home that is not
archived. Every service that checks `can_write` refuses a change to an archived home, whatever
the role, owners included. What a person *sees* does not change: the services that decide it
(every report for a member whose role writes, docs/adr/0086, and so the reports a phone is
given) read the role through `writes_by_role`, archived or not.

**Every route of a home refuses a change before reading it.** `HomeScopedMixin` checks, for any
method but `GET`, `HEAD` and `OPTIONS`, that the home of the address is not archived: first
thing in `dispatch` on the web, in `check_permissions` in the API, so that a body the view would
refuse for another reason, or a row it would not find, still gets this refusal. The web answers
`403` with a page that says the home is archived and offers an owner to bring it back; the API
answers `403` with `"code": "home_archived"`. A route is refused unless its view declares itself
an exception (`archived_home_writes`), so a new route is refused until someone decides
otherwise. A test lists every such route from the URL resolver, with real rows of the home, and
checks each answer and that nothing was written.

**The exceptions** are what the record of the app allows, and what a person must still be able to
do with a home they no longer use:

| Exception | Why |
| --- | --- |
| Bring the home back (owners), and archive it again, which changes nothing | The way out of the archive. |
| Export it, and download the export | Reading the home; an export is how a household keeps a home it sold. |
| Delete it for good (owners), and delete the example home of the guide | An archived home is often one on its way out; the app deletes it as well. |
| Leave it, remove a member, revoke an invitation | Taking access away; a person is never kept in a home they want to leave. |
| Lift a sign-in requirement whose provider the instance no longer offers | The only way to open the home again (docs/adr/0021). |
| Print the labels that exist | Reading the home; no new label is made. |

What gives access waits until the home is back: inviting, sending an invitation again, changing a
role, handing over ownership, requiring a sign-in provider. The services refuse them too
(`check_not_archived`). So does accepting an invitation sent before the home was archived, from
its page or from the API (`403` `home_archived`): its page says that it will work again once the
home is back, creates no account, and the invitation is kept, unused, until then. The
preferences of a person (display, notifications, calendar feeds, API tokens) belong to the
person, not to the home, and are not touched.

**The sync API.** A push to an archived home is answered as before, one result per operation:
each operation of a member whose role writes is `rejected` with the new code `home_archived`,
but the one that brings the home back, a `put` of `homes.home` that sets `archived_at` alone
(owners only, as for archiving: a member who is not an owner keeps `forbidden`, and so does a
read-only member, whatever the operation). The operations after one that brings the home back,
in the same push, are applied; those after one that archives it are rejected. An upload of a
file to an archived home answers `403` `home_archived`. The feed (`GET .../changes`) and the
snapshot stay readable. The apps already released treat a code they do not know as a refusal
and keep the operation aside with it, as the protocol of the app says for any refusal for good,
so the new code needs no new version of the app; the app refuses these changes before sending
them anyway.

**Reports and events.** A report or an event sent to an archived home, by Home Assistant or any
API token, is refused with `403` `home_archived`, never a `500`; `reports.services.create_report`
refuses it too, for every role.

**The screens.** Every page of an archived home starts with the banner **Archived home: read
only**, with **Bring this home back** for an owner. The buttons and links that change something
are hidden, since they read `can_write`; the household page keeps removing a member and revoking
an invitation, and hides inviting, roles and the sign-in requirement. A test opens every page of
an archived home and checks that it offers no change but the exceptions. The French label of the
way back stays **Remettre ce logement en service**, the one the web and the app already use.

**Data.** Nothing is migrated and nothing is deleted: a home archived before this change is
read only from now on, with everything it holds.

## Consequences

- A household that wants to change an archived home brings it back first, then archives it
  again. Its reminders resume in between, as they always did when a home is brought back.
- `can_write` no longer says what a person sees: a check of visibility reads `writes_by_role`.
  A future check that reads `can_write` to decide what to show would hide things in an
  archived home; the tests of the reports and of the sync visibility guard the ones that exist.
- The Home Assistant integration gets `403` `home_archived` for a report to an archived home; it
  may hide such a home, which `GET /api/v1/homes/` lists with `archived_at`, as before.

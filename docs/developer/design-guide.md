# Design guide

The rules every screen of HouseCairn follows: where it goes, how people come back
from it, how its actions behave and how it looks. They apply to every contributor,
human or agent, and the review of a pull request that adds or changes a screen
checks them with the [checklist](#new-screen-checklist) below.

The rules are normative. **Must** and **must not** are requirements; each comes with
its reason in one line. A screen that departs from them is a bug to fix, not a
precedent to follow.

Where the rules come from:

- the navigation decisions of the maintainer, recorded in
  [ADR 0053](../adr/0053-target-navigation-and-return-rule.md), and the visual
  system of [ADR 0017](../adr/0017-visual-system-and-brand.md);
- the navigation prototype validated by the maintainer on 2026-11-09,
  `design/navigation/prototype-navigation.html` in the repository: the reference for
  the **structure** of the screens (sections, tabs, return, actions in place);
- the v2 mock-up, `design/mockup/housecairn-maquette-v2.html`, and the design tokens,
  [`design/tokens.json`](../../design/tokens.json): the reference for the **look**;
- the navigation and design audits of the interface, summed up in the appendix
  [Why these rules](#why-these-rules).

The anatomy of a screen that the web and the mobile app share (the record page and its
actions, label-value rows, the header, forms, filters, empty, loading and error states, More),
the glossary of the shared terms and the platform adaptations of the app are in
[`design/patterns.md`](../../design/patterns.md). A screen follows it as it follows this guide.

The navigation described here is built: every finding of the audit (NAV-01 to NAV-16)
has its change merged. A screen that still differs from it is a bug to fix. A change
that alters one of these behaviours updates the section of this guide that describes
it, in the same pull request.

## Information architecture

### Five sections

The application answers one question: what to do in the home, where, when, with what
and for how much. Each of the first four sections answers one part of it, and More
holds the rest.

| Section | Question | Tabs |
| --- | --- | --- |
| Today | What to do, and when | Today · All · Calendar |
| Rooms | Where, and with what | Rooms · Stock |
| Projects | Which works | In progress · Ideas · Finished |
| Budget | For how much | Summary · Rooms · Projects · Upkeep |
| More | Everything else | none: groups of links |

- The main navigation **must** have these five entries, in this order. Material 3
  and Apple's guidelines put three to five destinations in a bar; a sixth entry
  pushed Projects and Budget out of sight.
- A new entry **must not** be added to the bar without a new decision record. The
  bar is the map of the product; every entry added makes the others harder to find.
- A section **must** have four tabs at most, all visible at 360 px. A tab hidden by
  a horizontal scroll is a screen nobody finds. A label longer than its share of the width
  wraps between words inside its tab (`tabs-fit`), never over the next one; a word that does
  not fit in a tab at 320 px is a label to shorten, in the catalog if needed (the instance
  tabs are "Aperçu" and "SSO" in French, the home audit log tab is "Journal"). Under 360 px
  the padding of a tab is 2 px and its text 13 px. `instance-tabs` measures 320 px and up.
- Tabs **must** switch between sister views of the same content, never lead to
  another section or a setting. Tabs used as a menu hide what they announce.
- A page **must** show one row of tabs at most. Two rows on a phone take half of the
  first screen.
- The history of the tasks is the **Done** filter of All, and each task shows its own
  history; it **must not** become a tab. A fourth tab of Today does not fit at 360 px.
- The calendar is a tab of Today, and the stock a tab of Rooms. The calendar is another
  view of the same occurrences; the stock is what is in the home, like the items.

### One home page: Today

A signed-in person has one start screen, Today, and every way in leads to it.

- The root address `/` (the route `index`; under a [path prefix](#addresses-and-links),
  the prefix itself) **must** lead a signed-in person to Today: Today of the
  [current home](#current-home), or all their homes together on one Today page when
  they chose "All homes" (and before they chose a home). The logo, the Today entry of
  the navigation outside a home, the `start_url` of the web app manifest and
  `LOGIN_REDIRECT_URL` **must** all be this address, never a second home screen.
- The welcome page **must** be shown only to a visitor who is signed out and to an
  account with no home to open, with **Add a home**.
- With several homes, the page of all homes **must** name each home by a heading that
  opens its Today, and each of its rows **must** name its home. It has one row of tabs,
  Today and Calendar (no list of all tasks across homes), and **must not** repeat a
  row of tabs per home: rows with the same name are the same landmark to a screen
  reader.
- **Report a problem** **must** be on Today for every role, a read-only one included:
  signing a problem is often the only thing that person does, and it takes one tap to
  open the form instead of six. The count of the reports that wait **must** follow the
  entry Reports of More, for the members who may write, read once per request.
- A screen of an earlier version that was folded into Today (`/me/`) **must** redirect to
  its successor, keeping the query, so that a saved link or a home-screen icon
  still works.

### Phone: the bar at the bottom

Below 56 rem the sections are a bar fixed at the bottom of the screen. The app bar at
the top carries the logo, the current home (a selector from two homes on), search
and help.

```text
App bar: [logo] Current home ▾ (from 2 homes)                        [search] [?]
Bottom bar: Today · Rooms · Projects · Budget · More

Today                       tabs: Today | All | Calendar
├─ Today (root)             overdue, today, next 7 days; Report a problem; + Task
├─ All                      filters: room, person, state ("Done" is the history)
├─ Calendar                 Week | Month | Year (segmented control)
└─ Task ── Edit · Postpone · Details of completion · Files

Rooms                       tabs: Rooms | Stock
├─ Rooms (root) ── Room     Items · Tasks · Budget of the room · Photos · Memory
│                  └─ Item  Tasks of the item · Compatible stock · Report
└─ Stock ── Article         movements in place · Suppliers · Label

Projects                    tabs: In progress | Ideas | Finished
├─ Projects (root) ── Project   Quotes · Lines · Tasks · Links · Photos
│                               Compare (selection); Annual envelope → Budget
└─ Quote (form) · Line (form)

Budget                      tabs: Summary | Rooms | Projects | Upkeep
├─ Summary (root)           envelope of the year, variance, CSV export
├─ Rooms ── Room            budget detail, link "Open the room"
├─ Projects ── Project      link to the project
└─ Upkeep ── Task

More (root)
├─ Home: Home settings (Your data: export, owner only; then the CSV tables;
│        deletion at the bottom, owner only) · Household and roles
│        · Reports (count) · Contractors · QR labels · My homes
├─ Me: Profile · Notifications · Display preferences
│      · Calendar in another application · API tokens
│      · Security and sign-in (deleting the account at the bottom)
├─ Instance (administrators only): Instance administration ── one page per section
└─ Help: Help · API documentation · About                     [Sign out]
```

### Computer: the column on the left

From 56 rem the same `nav` becomes a column on the left. It lists the first four
sections, then the content of More unfolded under them, without a page in between,
and Sign out last.

```text
[logo]
Current home ▾
─────────────
Today
Rooms
Projects
Budget
─────────────
Home        Home settings · Household and roles · Reports (2) · Contractors · QR labels
Me          Profile · Notifications · API tokens · Security and sign-in
Instance    Instance administration (administrators only)
Help        Help · API documentation · About
─────────────
Sign out
```

The diagram is indicative and does not show every entry; the entries are those of
`housecairn/core/more.py`.

- The phone bar and the computer column **must** be one `nav`, laid out by CSS.
  Two navigations drift apart, and a screen reader would announce both.
- On a computer, More **must not** have its own page: its content is in the column.
  The width is there; a page in between costs a click for nothing.

### What goes in More

More holds four groups, in this order: **Home** (about the home being viewed),
**Me** (about the person), **Instance** (administrators only) and **Help**.

- An entry of More **must** belong to one of these groups. A flat list of eleven
  entries does not read at a glance.
- An action that cannot be taken back (deleting the account, a home) **must** be at
  the bottom of its own page, never on More. A dangerous action does not sit between
  two everyday settings.
- An entry of More **must** be added in `housecairn/core/more.py`, in its group, with
  its routes for the marking of the current entry, never in a template. The page of More
  and the column both show it.
- The bar marks More for every route outside the four sections
  (`section_of` in `core/context_processors.py`). A page of More whose route is in the
  namespace of a section (the contractors, in `tasks`; the calendar feeds, in
  `calendar`) **must** be listed in `_IN_MORE` there, or the bar marks that section.

### Where a new screen goes

Answer these questions in order, and stop at the first yes:

1. **Is it another view of the content of an existing section root** (a filter, a
   period, a grouping)? It is a tab of that section, if the section has fewer than
   four tabs; otherwise a filter of an existing tab.
2. **Is it the page of one record** (a room, an item, a task, a quote)? It goes under
   the page of its parent in the tree: an item under its room, a quote under its
   project.
3. **Is it the list of all the records of a new type of data across the home** (every
   warranty, every contract, every document of the home)? Choose the first place that
   fits, in this order:
   - *The page of a parent*, when each record belongs to one parent and is read from
     it (the warranty of an item, on the page of the item). A list that is only read
     from its parent needs no screen of its own.
   - *A tab of an existing section*, when the list is another view of what the section
     already shows, used often, and the section has fewer than four tabs. The stock is
     a tab of Rooms for this reason.
   - *More, group Home*, otherwise: the list is about the home but looked at now and
     then, not a daily view.

   A section never gets a fifth tab: that needs a new decision record
   ([ADR 0053](../adr/0053-target-navigation-and-return-rule.md)). A record that
   belongs to a parent record (the warranty of its item) keeps that parent in the tree.
   The cross-home list of More is only an origin, and returns to it with `next`. Only
   records with no other parent are pages under the list.
4. **Is it a form that adds or changes a record?** It is opened from the page or the
   list it belongs to, with a `next` address (see the [return rule](#return-rule)).
5. **Is it about the home but not daily use** (household, reports, labels,
   contractors, settings of the home)? More, group Home.
6. **Is it about the person** (profile, notifications, preferences, security)? More,
   group Me.
7. **Is it about the instance**? More, group Instance, shown to administrators only.
8. **Is it help or information about the application**? More, group Help.

A screen of a signed-in person **must** be reachable from the navigation, never only
from an address typed by hand or a link in an email. A page nobody can reach again is
lost after the first visit. The screens of a visitor who is not signed in are outside
the navigation, which they cannot see: they are reached from the sign-in page (sign-up,
and the first administrator screen `/setup/` while it is open,
[ADR 0056](../adr/0056-first-administrator-on-a-web-screen.md)) or from the link of an
email (an invitation).

## Return rule

> **People always come back to the screen they came from. When it is not known, to
> the parent in the tree. An action in place does not move them.**

### The `next` address

- A link that opens a record or a form from another screen **must** carry
  `next=<current path>`. Without it, the page cannot know where the person came from.
- `next` **must** be checked by `core.redirects.is_safe_redirect` before any use,
  through one shared helper, never by a check written again in a view. One rule
  for every page closes open redirects in one place.
- `next` **must** be a path relative to the application, cut to a reasonable length,
  and **must not** be nested: a `next` inside `next` is removed. An address that grows
  with every screen becomes unreadable and unsafe.
- `next` **must not** be shown as it is. The label of the back link comes from the
  route it resolves to. A parameter shown on screen can be forged by anyone who sends
  a link.
- A form **must** keep `next` in a hidden field, so that a form sent with an error
  still knows where to go back.

### In code

The rule has one implementation, `housecairn.core.navigation`, and its template tags,
`{% load navigation %}`. A view or a template **must** use them, never read `next`
itself.

| Helper | What it does |
| --- | --- |
| `back_url(request, default)` | The `next` of the request (the form first, then the query) when it is safe, else `default`. Safe: checked by `is_safe_redirect`, a path starting with one `/` and, under a [path prefix](#addresses-and-links), leading under the prefix once resolved (`core.prefix.in_application`), a nested `next` and `undo` removed, 512 characters at most (past that the query is dropped, then the address is refused). |
| `safe_next(request)` | The same, or `None` without a safe `next`. |
| `after_create(request, record_url, section_list, added=…, saved=…)` | Where a creation goes: `next`, with the message "*added* · Open" linking to `record_url`; the record itself when `next` is missing or is the route `section_list` (the list of its own section). |
| `after_delete(request, parent, gone)` | Where a deletion goes: `next`, unless its route names one of the ids in `gone` (the object and its children), else `parent`. |
| `stay(request, page)` | `page` again after an action in place, still knowing where it was opened from: `page` with the safe `next` of the request, or without one. |
| `breadcrumbs(request, default, default_label)` | The tree from the section down to the page, as a list of `Crumb` (url, label; no url for the page itself). Built from `default`, the parent of the page, and the parents of that parent (`PARENTS`); the last crumb is the title of the page (a record of `LABELS`, or `FORM_TITLES` for a form). Empty when the page or an ancestor has no label, and when the parent is More. |
| `label_for(url, request)` | The title of the screen a path leads to, from `LABELS`: a fixed label by route name, or a field of the record the route shows, read only in the homes the person may open. `None` for a route without a label. |

| Tag | Use |
| --- | --- |
| `{% back_link default %}`, `{% back_link default label %}` | The back link of the page. It leads to the safe `next` when its route has a label, named after it, else to `default`, named `label` or after the route of `default`. A link to More is shown on a phone only. On a computer it also draws the breadcrumbs of the page, and keeps the link only on a form or when the origin is not the parent. |
| `{% back_href default %}`, `{% back_href default as cancel %}` | The same address without the link, for a Cancel button. |
| `{% url_with_next 'rooms:item-create' home_id=home.id room_id=room.id %}` | The address of a route with `next=<this page>`. A keyword prefixed with `q_` goes to the query: `q_item=item.id` gives `?item=…`. |
| `{% next_field %}` | The hidden `next` field of a form, with the safe `next` only. |

A form, then, reads:

```django
{% load i18n navigation %}
{% url 'rooms:detail' home_id=home.id room_id=room.id as parent %}{% back_link parent room.name %}
<form method="post">
  {% csrf_token %}
  {% next_field %}
  …
</form>
```

and its view ends with:

```python
return redirect(
    navigation.after_create(
        self.request, record_url, "rooms:list", added=_("Item added"), saved=_("Item added.")
    )
)
```

A route that should name a back link is added to `LABELS`. Without it, a `next`
to that route is not lost for the redirect after a form, but the back link falls back
to its default: a link must never show an address nor a nameless "Back". Every page of
Budget is named "Budget", so a record opened from any of them says "‹ Budget".

The bar follows the origin too: with a safe `next`, `nav_section` in
`core/context_processors.py` marks the section of the `next` route, not that of the
page. A room opened from the budget of the room keeps Budget marked.

An action in place without JavaScript (a stock movement, the status of a quote, a button
of a report or of the household) posts the `next` of its page with `{% next_field %}`,
and the view sends the person back to the same page with that `next`
(`navigation.with_next(page, navigation.safe_next(request))`), so the back link of the
page still leads to its origin.

### Default parent

A record reachable from two places has one parent in the tree, the section that owns
it, and the other place is only an origin.

- Without `next` (an address typed by hand, a link in an email or a calendar feed), the
  back link **must** lead to the parent in the tree, never to a previous screen
  guessed from the history. A page must not depend on a history the person cannot see.
- The parent **must** be the same wherever the record is opened from: the parent
  record when the record belongs to one (the warranty of its item), otherwise the list
  of its section, as in the tree above. The screen reference page
  of the record names it.
- With `next`, the origin wins and the label comes from its route.

Example: a room is listed under Rooms and also opened from Budget. Its parent is Rooms,
so opened without `next` it says "‹ Rooms". In Budget, a row of the budget by room opens
the budget of that room, a page of Budget; its **Open the room** link opens the room
with `next`, so the room says "‹ Budget", keeps Budget marked in the bar and comes back
to it. The page of the room leads to its budget by its block "Budget of the room" only.

### Back link and breadcrumbs

| Level | Phone | Computer (from 56 rem) |
| --- | --- | --- |
| Section root (Today, Rooms, Projects, Budget, More) | no back link | no breadcrumbs |
| Tab of a section (All, Stock, Ideas, budget by room) | no back link, the tab is marked | no breadcrumbs |
| Record (room, task, article, project) | `‹ <origin>` | `Section › … › Record` |
| Form | `‹ <origin>`, main action at the bottom | breadcrumbs, and the back link `‹ <origin>` |
| Page of More | `‹ More` (or the origin) | `Home › Household and roles` |

- A section root **must not** have a back link. A top-level destination has nowhere
  to go back to, and a back link there contradicts the bar.
- Every other page **must** have one, which names its target: never "‹ Back" alone.
  A person must know where the link leads before tapping it.
- The back link follows the origin; the breadcrumbs follow the tree.
- Breadcrumbs **must** appear on a computer only, as a `nav` with an `aria-label` and
  `aria-current="page"` on the last element. On a phone they take a line, and their
  targets would be too small.
- On a computer, the back link **must** appear only on a form or when the origin is
  not the parent (a room opened from the budget). Otherwise the breadcrumbs already
  say it.
- The `{% back_link %}` tag draws both, so a page does not write breadcrumbs itself.
  A new record page **must** be in `PARENTS` (its parent in the tree) and have a
  `Record` in `LABELS`; a new form **must** be in `FORM_TITLES`. A page without a
  title gets no breadcrumbs rather than a wrong one.
- Breadcrumbs are in the page at every width and hidden below 56 rem by the style
  sheet (`components.css`), so a phone reads the back link alone.
- The entry of the bar that stays marked is the section the person entered by: a room
  opened from the budget keeps Budget marked, and comes back to it. The bar must never
  change tab under the finger.

### After a form

- **Created from a context** (a task from an item, an item from a room): the form
  **must** go back to `next`, with a message "Added · Open" whose link opens the new
  record. The person stays where they were working, and the record is one tap away.
- **Created from the list of its own section** (a project from the project list): the
  form **must** open the new record. The next step is there: adding a quote to a new
  project.
- **Cancel**, the back link of the form or its Cancel button when it has one (a
  confirmation page, the report form), **must** lead to `next` as well, or to the
  parent without it.
- **Changed** (edit form): back to `next`, or to the record without it.

### After a deletion

- A deletion **must** go through a confirmation page: a link to the page, then a post
  from it, whose Cancel link goes back to the object. This includes photos, files and
  the revocation of a label. A tap by mistake must not lose a document.
- After the deletion, the person **must** land on the parent of the deleted object,
  never on `next` if it points to that object or one of its children. That page no
  longer exists.

### The bar, and the browser's back button

- Tapping a section in the bar or the column **must** lead to its root. Tapping the
  section already marked **must** go back to its root and to the top of the page. Both
  are what iOS and Android do, so people expect them.
- The browser's back button, or the system one in the installed application, **must**
  give the same result as the back link. In the installed application on iOS there is
  no system back button: the back link is then the only way back.
- A creation form **must** be sent with `Cache-Control: no-store` and a one-time
  token against a second sending. Going back to a sent form must not create a
  duplicate.
- A transient state (an undo banner, a message) **must not** live in the address.
  Going back would show it again, out of date.

## Actions in place

An action that takes one tap and can be undone **must** keep the person where they are:
Done, Skip, Undo, postponing by one day, a stock movement, the status of a quote.

- With JavaScript, the form **must** post with HTMX (`hx-post`) and replace only the
  fragment concerned (the row of the occurrence). The address does not change. Done is
  the most frequent gesture; a reload sends the page back to its top, and the person
  loses their place.
- The confirmation **must** be an undo banner at the bottom of the screen, above the
  bar, in an `aria-live="polite"` region, visible for at least 8 seconds, with an
  Undo button. Near the finger, announced by screen readers, and long enough to read.
- The banner and the bar **must not** hide the focused element (WCAG 2.2, 2.4.11).
- Without JavaScript, the same form **must** still work: a post, then a redirect to
  `next#<anchor>` (for an occurrence, `#occ-<id>`), which brings the person back to
  the row. The undo banner never lives in the address, with or without script.
- Done **must not** ask for a confirmation: Undo is the safety net
  ([ADR 0033](../adr/0033-done-with-undo.md)). A question on every task is the
  friction that makes people stop using the application.

An action **must** open a screen instead when:

- it needs more than one tap of input: the details of a completion (date, cost,
  note), postponing to a chosen date, a quantity other than the one offered;
- it cannot be taken back: a deletion goes through its confirmation page;
- it changes who may do what: roles, transfer of a home, an invitation sent by email.

### How it is built

Done, Skip and Undo of an occurrence work this way; a new action in place follows the
same pattern.

- **HTMX** is vendored (`static/vendor/htmx/`, version and checksums in its
  `README.md`), loaded by `base.html` with its subresource integrity, under the strict
  CSP: no `hx-on`, no inline script, `allowEval` and `allowScriptTags` off. Nothing
  else is needed on the page.
- **The row** (`templates/tasks/_occurrence_row.html`) is an `li` with the id
  `occ-<id>`. Its form posts to the same view with or without script: `action` and
  `hx-post` give the same address, `hx-target="closest li"` and
  `hx-swap="outerHTML"` replace the row only, `hx-disabled-elt="find button"` stops a
  double tap, and two hidden fields say where it is: `next` (the page, from the
  view's `row_next`) and `in_place=row`.
- **The view** (`OccurrenceActionView`) reads `in_place` only with the `HX-Request`
  header (`tasks.in_place.origin`). Then it answers `templates/tasks/_in_place.html`,
  never a page: the row as it now is, and the banner as an out-of-band swap
  (`hx-swap-oob="innerHTML"`) into `#undo-region`. Errors (a task already closed by a
  second tap) come back in the banner, with the row as it is, in a 200 answer: HTMX
  does not swap an error status.
- **The banner** lives in `#undo-region`, at the end of `base.html` on every signed-in
  page, empty, with `aria-live="polite"`: a live region must be in the page before
  its content changes for a screen reader to hear it. It is fixed at the bottom,
  above the bar, and stays until the next action or page. The room the page keeps
  for it at its end follows its real height (`--hc-undo-height`, set by
  `static/js/undo-region.js`): a long message wraps on four lines at 320 px. Without
  script nothing can measure it, so `<noscript>` loads `static/css/no-script.css`: the
  region becomes a sticky box at the end of the page, in the flow, which takes its own
  room and sticks above the bar (at the corner on a wide screen) while the end of the
  page is out of sight; no height is reserved, whatever the length of the message. Its
  Undo posts in place with `in_place=banner`: the answer is the content of the region
  ("Undone."), and the row back as an out-of-band swap.
- **Focus**: the element that had it is replaced, so the answer gives it back with
  `autofocus`, which HTMX applies after a swap: the row's link after Done or Skip, the
  row's Done button after Undo. `scroll-padding-bottom` keeps a focused element clear
  of the banner (WCAG 2.2, 2.4.11): with script, by the real height of the banner;
  without script, by that of the longest message, four lines at 320 px
  (`no-script.css`).
- **Without script** the same form posts and the view redirects to `next#occ-<id>`.
  The banner is kept in the session for that one page (`tasks.in_place.remember`),
  read once by `{% undo_banner %}` in `base.html`, never in the address: the browser's
  back button cannot show it again. A row that left the list after Done (an open
  list no longer shows it) has no anchor to come back to: the page then opens at its
  top.
- **The task page and the calendar** keep the post and its redirect, with or without
  script: an action there changes the whole card (state, history, materials), or an
  entry that is not an occurrence row. The banner comes from the session, as above,
  and the calendar's entries carry the `occ-<id>` anchor.

To make another one-tap action work in place: give its fragment an id, post with
`hx-post` to the same address as `action`, send `next` and `in_place`, and have the
view answer the fragment and, if the action can be undone, the banner out of band;
keep the redirect to `next#<id>` for the post without script.

### Creation forms and the back button

Every page whose route ends in `new/` (the convention of the creation forms) goes
through `core.middleware.CreationFormMiddleware`:

- it is sent with `Cache-Control: no-store`, so that the back button asks the server
  again instead of showing the form already sent;
- each of its forms gets a hidden `form_token`, next to the CSRF field, new at each
  display (a form sent with an error gets a new one);
- a post that brings back a token already used is refused before the view runs: the
  person goes to `next` (else Today) with "This form was already sent". The tokens
  used are kept in the session and in the cache, whose atomic `add` stops two
  sendings at the same instant (with a cache shared by the workers, across workers
  too). A post without a token is let through as before.

- every form that posts on such a page gets `autocomplete="off"`, so that the browser does
  not put back what was typed when the person comes back to the page.

What the token guarantees: the same page, with the same token, sent twice (a double
tap, or a replay of the post) creates one record. What it does not guarantee: a second
sending from a page asked for again. The back button asks the server again
(`no-store`), the page it gets carries a new token, and sending it creates a second
record. Only the empty fields (`autocomplete="off"`) and the person's attention stand in
the way of that case; the token is not a promise against duplicates.

A new creation form gets all this by its address alone: its route ends in `new/`.

## Current home

Most households have one home; some have two or more (a second home, a rented
flat).

- The current home **must** be remembered for the session. Every section then opens
  on it, without a page that asks which home at each change of section. It is the home
  last chosen in the selector, or the home of the last page of a home opened; before
  either, the first home of the person by name.
- The selector of the current home **must** appear in the app bar only from two homes
  on. A selector with one choice is noise. It names the home of the page when the
  address has one, else the current home.
- Changing the home **must** be a post with its CSRF token, never a link: it changes
  the state of the session. The selector is a disclosure (`details`) whose choices are
  the buttons of one form, so it works without script. It opens the same screen in the
  home chosen when the route needs only a home (a section root, a tab), else the root
  of the section: a room of one home does not exist in the other.
- Today **must** offer "All homes" in the selector; the other sections show one home
  at a time. "All homes" is the page of all homes at `/`, and the calendar of all homes;
  it leaves the current home of the other sections as it was.
- A page of a home **must** keep the home in its address
  ([ADR 0010](../adr/0010-stable-urls.md)). The current home only chooses where a
  section root opens (the entries `/rooms/`, `/tasks/`, `/stock/`, `/projects/`,
  `/budget/`, `/` and the calendar without `?home=`); a link to a page always opens
  the same page.

**Isolation.** Every query **must** be scoped to the homes of the person
([ADR 0006](../adr/0006-home-scoping.md)), and the current home **must not** replace
that check. A value kept in a session is a preference, not a permission. The
selector and "All homes" list only the homes the person belongs to, and a home they
left, deleted or that is locked behind a sign-in provider is dropped from the session:
the next home takes its place, or, with none left, the page that offers to add one.
An API token never reads nor writes it: its homes are those of the token.

### In code

`housecairn.homes.current` is the one implementation.

| Helper | What it does |
| --- | --- |
| `for_request(request)` | A `Current`, read once per request: `homes` (the homes the person may open now, by name), `home` (the current one, or `None` without a home), `all_homes` ("All homes" chosen for Today), `roles` (the role of the person in each), `find(id)` (a home of `homes`, else `None`). The stored id is checked against `homes` at every request. |
| `section_root(request, route)` | The address of a route of one home in the current home, or `None`: what an entry view redirects to. |
| `remember(request, home)` | Makes `home` current. `WebHomeScopedMixin.get_home()` calls it after its own check, so a page of a home needs nothing more. |
| `choose(request, home)` | The choice of the selector (`None` for "All homes"), posted to `homes:current`. |

A new view that needs a home **must** take it from its address through
`WebHomeScopedMixin.get_home()`, which answers 404 for a home that is not the
person's. A new section entry without a home in its address redirects to
`current.section_root(request, "<app>:<root>")`, and renders the page that offers to
add a home when it is `None`. A view **must not** read the session key itself, nor
use `current.for_request(request).home` to read or change data: that home is only
where to go, and the page it leads to checks it again.

## Dates, reminders and alerts

The application already has one way to remind and one way to alert; a new screen uses
them and does not add a third.

- A date that someone must act on **must** be a task with a due date. The task is then
  shown on Today: overdue, today, and up to the person's horizon, 7 days by default
  (the `horizon_days` preference, `Horizon.WEEK`). It is also shown on the Calendar and
  in the calendar feeds, and its reminders go out through the channels of the person
  ([ADR 0043](../adr/0043-notifications-through-apprise.md)), with the rules of
  postponing, Done and Skip that every task has. A reminder written again for one
  screen would ignore the quiet hours and the follow-ups of the person.
- A date that only informs (the end of a warranty) **must** be a list of the agenda
  digest, `housecairn.tasks.agenda.my_home`, shown on Today for all homes (`/`, with
  "All homes" chosen), as the warranties ending soon are today.
  It is read, not acted on, and sends no notification. This digest is not the daily
  summary message of the notifications.
- An event with no due date (a problem was reported) **must** be an alert of
  `housecairn.notifications.alerts`, sent in the background to the members who may
  write, except the person who caused it, with the quiet hours applied. The open
  reports are also a line on Today with a link, for the members who may write.
- A notification **must** carry the title the person typed, the home and a link, and
  nothing else: no amount, note or address leaves the instance.
- A notification **must not** be sent from the screen's own code: it goes through the
  `notifications` app, which sends it once and only to people who have a channel on.

## Screen rules already decided

These rules come from earlier decisions; this guide gathers them so that a new screen
is checked against all of them at once.

- **Amounts** **must** be shown in the format of the language of the reader, with the
  symbol of the currency: `1 250,50 €` in French, `€1,250.50` in English. Every amount
  goes through the one filter, `{% load money %}` then `{{ amount|money:currency }}`
  with the currency of the home (`core/templatetags/money.py`, [ADR 0054](../adr/0054-amounts-with-babel.md)), never
  written by hand. The CSV exports and the API keep the raw number and the ISO code in
  a field of its own. Three formats on three screens read as three different numbers.
- **Deleting** **must** go through a confirmation page (see
  [After a deletion](#after-a-deletion)). Only Done and its siblings rely on Undo.
- **Lists**: wherever a generic list exists (type of room, category of a photo, trade
  of a contractor), the field **must** offer it, with "Other" as its last choice
  ([ADR 0045](../adr/0045-generic-lists-with-other-tabs-and-audit-of-content.md)).
  Free text makes filters impossible and translations arbitrary.
- **People** **must** be shown by their display name (`accounts.names.person_name`),
  never by their email address, except on a page whose subject is the address (the
  card of a household member, a contractor). An address on every screen is personal
  data shown to the whole household.
- **A secret** that the person must keep (an API token) **must** be shown once, on the
  page that answers the `POST` that made it, and never again: that answer is sent with
  `Cache-Control: no-store`, the secret is in a read-only field with a **Copy** button
  (`static/js/copy-secret.js`; without JavaScript the button stays hidden and the field
  can still be selected), and the page says it will not be shown again. The screen of
  the API tokens does it (`apitokens/views.py`, `apitokens/token_created.html`). A secret
  on a page that a link or the back button opens again is readable by whoever uses the
  browser next, and stays in its cache.
- **Sign-in** **must** offer one button per configured identity provider, and passkeys
  through the browser's autofill of the address field, without a passkey button
  ([ADR 0018](../adr/0018-sign-in-page-and-passkeys.md)). The page stays a form and at
  most one button per provider. While the instance has no administrator, it also shows
  a notice with a link to `/setup/`, and never redirects there: people who already have
  an account still sign in ([ADR 0056](../adr/0056-first-administrator-on-a-web-screen.md)).
- **A field that only one choice uses** (the months of a custom season, the date a fixed
  repetition counts from) **must** be hidden by script while that choice is not made, and
  stay visible without script, the server ignoring it then. The task form does it with
  `data-shown-when` (`static/js/task-form.js`). What the fields amount to is said in one
  sentence under them, worked out by the server in the language of the reader. A form
  that shows every option at once reads as harder than it is.
- **A refused form** **must** say why at the top, with the one component
  `components/form_errors.html` (`{% include "components/form_errors.html" with form=form only %}`,
  `formset=` when the block holds one), put inside the `<form>` before the fields, never copied
  into a template. It reads "There are N errors:", then one line per error: the label of the
  field, linked to the field by its `id`, and the message; an error that belongs to no field
  comes first, without a link. It is announced (`role="alert"`) and holds the focus when the
  page opens (`tabindex="-1"` and `autofocus`, which the browser honours on a page and HTMX on a
  swapped fragment), so the person sees it without scrolling. The message stays under each field too
  (`{{ field.errors }}`, which Django links to the field with `aria-invalid` and `aria-describedby`).
  A folded `<details>` that holds a field in error **must** open: by its own `open` condition
  on the server, and `static/js/form-errors.js` opens any that is left. A refused form
  **must** come back as its page, with status 422, even when the page shows a record (the forms of
  the project page, the household, the instance overview). Two forms are an exception, being
  actions in place on a page that already shows the record: the stock movement and the stock item
  added to a task redirect to it, and say why in its banner, each message led by the label of
  its field (`core.form_errors.banner_messages`), never "This field is required." alone.
  `tests/test_form_errors.py` posts an invalid form to each form of the templates (the table
  `TEMPLATE_CASES` names its case) and scans the templates form by form: a `<form>` added without
  the component, or without a case, fails it.
- **Choosing several records of the home** (the equipment a part fits, the rooms of a project)
  **must** be the search field with chips (`core.widgets.ChipSelectMultiple`,
  [ADR 0063](../adr/0063-search-field-with-chips-for-multiple-choices.md)), never a list of boxes,
  which grows with the home. The person types, a list of suggestions narrows, a choice becomes a
  chip with a "Remove ..." button. The field posts the values of a `<select multiple>`, so the
  form and the services do not change. The form page **must** load `static/js/chip-select.js`
  (`{% block extra_head %}`); without script the select shows, its options grouped by
  `group_by` when the records belong to something (the items, to their room). The component is a
  combobox with a listbox popup (arrows, Enter, Escape, Backspace on the last chip), with
  targets of 44 px at least. Boxes stay for a short, fixed list (the permissions of a token,
  the options on or off) and for a list of rows to act on, which carry their own links.
- **Adding to a list in place**: a form field that picks a record of the home the person
  may not have created yet (the contractor, the room, the item or the stock item of a task,
  the provider of the maintenance contract of an item) **must** be a list of the home's
  records whose last choice is "Add a …" (`tasks.forms.fields.AddInPlaceChoiceField`), and
  never a free-text field with suggestions, which reads as an empty field. That choice shows
  the fields of the new record right under the list, in an `add-in-place` fieldset with
  `data-shown-when="<field>=new"`; without script they are always shown and read only with
  that choice. The record is created when the form is saved, in the same transaction, so a
  refused form leaves nothing behind; an empty name, or a name already in the list whatever
  its case, is refused with a message under the field. The person never leaves the form
  for another page and back. Inside a repeated line (the materials of a task) a rule names
  a field of its own line (`data-scope="materials-3-"`), and lines added or removed by script
  are renumbered.
- **A list of lines that grows** (the materials of a task) **must** offer **+** and **−**
  with script, up to the limit of the rule, announced in an `aria-live` region and with **+**
  disabled once reached; without script a named submit button sends the form back with
  more lines, checking and saving nothing. A form that holds such a button **must** put a
  visually hidden copy of its save button first, so that Enter saves.
- **Touch targets** **must** be at least 44 px (`--hc-layout-touch-target`), and the
  Done button 48 px (`--hc-layout-touch-target-lg`). A small link in a table is missed
  by a finger.
- **Focus** **must** always be visible: the focus ring of the tokens, never an
  `outline: none` without a replacement. A keyboard user who cannot see the focus
  cannot use the page.
- **Accessibility**: every screen **must** meet WCAG 2.2 level AA. It is the level the
  project commits to for every screen, not only for the sign-in pages.
- **Widths**: every screen **must** be tried at 375 px and at 1280 px: no horizontal
  scroll of the page (a wide table scrolls in its own container), every tab visible.
  The application is designed for the phone first; the computer layout must not
  break either.
- **Language**: every string **must** go through the translation functions, with
  English as the source ([ADR 0013](../adr/0013-internationalization.md)). The French
  text **must** be written with care: the formal *vous*, agreement with the noun it
  qualifies, and a `pgettext` context when one English word needs two French words.
  French ships with the first version, and a mistake in a list is seen on every visit.
- **Resources**: a screen **must not** load anything from another host, nor use an
  inline style or script ([ADR 0011](../adr/0011-no-external-resources-and-csp.md)).
  The strict content security policy refuses them.

## Addresses and links

An instance may be served under a path prefix, such as `https://example.com/housecairn/`
or Home Assistant's Ingress (`HOUSECAIRN_URL_PREFIX`, or a header from a trusted proxy:
[ADR 0055](../adr/0055-serving-under-a-path-prefix.md)). Every address of the
application then starts with the prefix.

- An address in a template **must** come from `{% url %}`, `{% url_with_next %}` or a
  value built with `reverse` in the view, never be written from the root (`href="/…"`,
  `action="/…"`, `hx-post="/…"`), and a script of `static/js` **must not** hold one
  either. Under a prefix, `/…` leaves the application. `tests/test_url_prefix.py` fails
  on any such address, and the CI runs the navigation tests under a prefix.
- An address sent away (the links of emails and invitations, a QR label, a calendar
  feed and the links of its events, the first-account address in the log) **must** be
  built by `core.links.external_url(reverse(…), site)`, on the public address: the
  scheme and the host of `HOUSECAIRN_SITE_URL` (during a request,
  `homes.scoping.site_url(request)`, which falls back on those of the request), then
  the fixed `HOUSECAIRN_URL_PREFIX`, never the prefix of the request. Without a public
  address, the task worker leaves its links out. Addresses used inside a page keep the
  prefix of the request; the callback addresses of the sign-in providers stay those of
  the request ([ADR 0055](../adr/0055-serving-under-a-path-prefix.md), change of
  2026-11-10; [Serving under a path prefix](../administration/url-prefix.md)).
- A link to an address that a person typed (a purchase link, a contract, the links of an
  item or of a project, a document in Paperless-ngx) **must** carry
  `rel="noopener noreferrer nofollow"`. Its field is a `WebUrlField`
  (`rooms/models.py`), which accepts `http` and `https` only, never `javascript:`. The
  links of an item are a list in a `JSONField` instead: `validate_links`
  (`rooms/models.py`) checks each address with the same validator.

## Labels

One word for one thing, on every screen and in both languages. These rules come from
the review of the labels (NAV-14); a new screen **must** follow them.

- **A term names one concept.** The same word never names two things, and one thing
  never has two names. In French: the *logement* (never "accueil", which is Today
  on a phone), an *élément* of a room (its type is *Équipement* or *Surface*), an
  *article* of the stock, *Matériel* for the stock view of Rooms, *Fournitures* for
  what a task or a project needs.
- **A name shared by two pages is qualified.** Two audit logs are "Home audit log" and
  "Instance audit log", never "Audit log" twice. The tab of the home page that holds
  its settings is "Home settings", like the entry of More; "Overview" is the first
  tab of the instance only (the budget one is "Summary").
- **A bar entry equals the title of its section.** The entry of the bar (or of the
  column) and the `h1` of the root it opens say the same word. A tab names its view and
  may be shorter than the `h1` (All → Tasks, Summary → Budget).
- **A filter reads like the noun it filters.** The filter of finished tasks says
  "Done" in English and *Faites* in French, which agrees with *tâches*; the button on
  one task stays *Fait*. When one English word needs two French words, use a
  `pgettext` context ("task" and "task filter" for Done).
- **A section holds four tabs at most**, so that they fit a phone without scrolling. A
  fifth page is a link from the first tab (the files of the homes, from the instance
  overview).
- A new label goes into the catalog of its app, in both languages, in the same pull
  request, and `check_translations --sources` **must** pass.

## Tables on a phone

A table of 5 or 7 columns is 650 to 850 px wide: on a phone it would make the whole page
scroll sideways. The rule has two renderings, chosen by how the table is read.

- **Read row by row** (a room, a project, a task, a budget line): the table takes the
  class `table-stack`. Under 56 rem each row becomes a card: the row head is the title,
  every value shows its label in front of it and is aligned to the right, as a
  formatted amount. From 56 rem it is the plain table. Every `td` with
  content **must** carry a `data-label` with the translated name of its column; an
  empty cell is not drawn.
- **Read column by column** (a comparison of projects side by side): the table stays a
  table, in a `table-scroll` container with `role="region"`, `tabindex="0"` and an
  `aria-labelledby` that points to the caption of the table, so the page keeps its width
  and a keyboard user can scroll the container.

A new table **must** take one of the two. `tests/e2e/table_widths.py` measures every table
of the screens at 360 and 375 px, in a browser, on a local instance.

## Visual system

The visual system was laid down with the application shell in J1-F
([ADR 0017](../adr/0017-visual-system-and-brand.md)). The values are in
[`design/tokens.json`](../../design/tokens.json) and nowhere else: this guide does not
repeat them.

- A colour, size, spacing, radius or shadow **must** come from a `--hc-*` variable,
  never from a value written in a CSS file. A value written twice drifts.
- A change of value **must** be made in `design/tokens.json`, then
  `manage.py build_design_tokens`, and the generated `tokens.css` committed. The
  contrast test then fails if the change breaks AA.
- A screen **must** use the components of `static/css/components.css` before creating
  one: cards, lists and rows, buttons (primary, secondary, sand, danger, icon), fields,
  pills, alerts and messages, the segmented control, chips and the tab bar
  (`{% tab_bar %}`). A component made twice looks different twice.
- A new component **must** go in `components.css`, built on the tokens, and be usable
  by every screen. A style local to one page becomes the next inconsistency.
- The danger button **must** be kept for the action that destroys, on its confirmation
  page. Red everywhere means nothing.
- Icons **must** come from `templates/components/icon.html`, as inline SVG. One set,
  one look, nothing loaded from elsewhere.
- The v2 mock-up is the visual reference of every screen; a deviation **must** be
  recorded in a decision record with its reason, as ADR 0017 does for contrast. The
  navigation prototype is a reference for structure only: its look does not count.

## New screen checklist

Copy this list into the pull request that adds or changes a screen, and tick what
holds. The review checks the same list.

- [ ] The screen has its place in the tree, found with
  [Where a new screen goes](#where-a-new-screen-goes), and is reachable from the
  navigation.
- [ ] A new entry of More is added in `housecairn/core/more.py`, in its group, with its
  routes for the marking, and in no template; a page of More in the namespace of a
  section is in `_IN_MORE`.
- [ ] A record reachable from two places has one parent, named in its screen reference
  page; without `next`, its back link leads to it.
- [ ] A date to act on is a task; a date that informs is a list of the agenda digest; an event
  is an alert; none has a reminder of its own.
- [ ] The bar still has five entries; the section has four tabs at most, all visible
  at 360 px, and the page shows one row of tabs at most.
- [ ] The marked entry of the bar is the section the person entered by.
- [ ] A section root has no back link; any other page has one that names its target.
- [ ] Every link to a record or a form carries `next` (`{% url_with_next %}`), the
  back link and Cancel use `{% back_link %}` and `{% back_href %}` (or
  `navigation.back_url` in the view, as the confirmation pages do), and the form keeps
  `next` with `{% next_field %}`; a route that a back link must name is in `LABELS`.
- [ ] Every address comes from `{% url %}`, `{% url_with_next %}` or `reverse`, none
  written from `/`; a link to an address a person typed carries
  `rel="noopener noreferrer nofollow"`.
- [ ] After a creation from a context, the person is back in the context with
  "Added · Open"; from the list of its own section, on the new record.
- [ ] A deletion goes through a confirmation page and lands on the parent.
- [ ] A form includes the summary of its errors (`components/form_errors.html`), and a folded
  section with a field in error opens.
- [ ] Each new named web route has its entry in `housecairn/parity.py` (the API route
  that does the same, or an exemption with its reason), and its API endpoint, as
  [ADR 0003](../adr/0003-web-api-parity.md) requires; `tests/test_parity.py` passes.
- [ ] Tests of the return rule, as [ADR 0053](../adr/0053-target-navigation-and-return-rule.md)
  announces: for each new link that carries `next` and each new form, the origin, the
  label of the back link and the target after sending, with and without `next`. They
  fail on the code without the change.
- [ ] One-tap actions stay in place with HTMX, with the undo banner, and work without
  JavaScript.
- [ ] The screen shows the current home and only data of the person's homes.
- [ ] A choice of several records of the home is the search field with chips, not boxes.
- [ ] Amounts through the `money` filter; people by their display name; generic lists
  with "Other".
- [ ] A secret is shown once, in the answer to its `POST`, with `Cache-Control: no-store`
  and a Copy button.
- [ ] Touch targets of 44 px at least, visible focus, never hidden by the bar or the
  banner; WCAG 2.2 AA.
- [ ] Tried at 375 px and 1280 px, with no horizontal scroll of the page.
- [ ] Every string translated, the French text reread.
- [ ] Only `--hc-*` tokens and existing components; no inline style or script, nothing
  from another host.
- [ ] The screen reference page in `docs/screens/` and this guide, if a rule changed,
  updated in the same pull request.

## References

- Material Design 3:
  [navigation bar](https://m3.material.io/components/navigation-bar/guidelines),
  [navigation drawer](https://m3.material.io/components/navigation-drawer/guidelines),
  [tabs](https://m3.material.io/components/tabs/guidelines),
  [snackbar](https://m3.material.io/components/snackbar/guidelines).
- Android, [principles of navigation](https://developer.android.com/guide/navigation/principles)
  (a fixed start destination, Up and Back).
- Apple Human Interface Guidelines:
  [tab bars](https://developer.apple.com/design/human-interface-guidelines/tab-bars),
  [navigation bars](https://developer.apple.com/design/human-interface-guidelines/navigation-bars).
- Nielsen Norman Group:
  [10 usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/),
  [breadcrumbs](https://www.nngroup.com/articles/breadcrumbs/),
  [tabs, used right](https://www.nngroup.com/articles/tabs-used-right/).
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/), in particular
  [2.5.8 Target size (minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html),
  [2.4.7 Focus visible](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible.html),
  [2.4.11 Focus not obscured (minimum)](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html),
  [2.4.6 Headings and labels](https://www.w3.org/WAI/WCAG22/Understanding/headings-and-labels.html),
  [3.2.3 Consistent navigation](https://www.w3.org/WAI/WCAG22/Understanding/consistent-navigation.html),
  [3.2.6 Consistent help](https://www.w3.org/WAI/WCAG22/Understanding/consistent-help.html)
  and [4.1.3 Status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html).
- web.dev, [app design](https://web.dev/learn/pwa/app-design): in the installed
  application, the browser shows no navigation of its own.

## Why these rules

A navigation audit of the interface, on 2026-11-09, walked through the main journeys
at 375 px and 1280 px in French, with three roles (owner, member, read-only). It found
16 problems: 2 blocking, 9 important, 5 minor. The interface itself was sound (touch
targets, skip link, consistent help, confirmation before deleting most objects, a
`next` already checked); the problems were structural. A design audit a few days
earlier had found the same first problem (Projects and Budget out of sight on a
phone), and the rules on amounts, deletions and lists.

| Finding | Severity | What the audit saw | Rule that answers it |
| --- | --- | --- | --- |
| NAV-01 | blocking | Six entries in the bar; Projects and Budget were tabs of Tasks, off screen at 375 px. | [Five sections](#five-sections) |
| NAV-02 | blocking | Every back link pointed to a fixed parent, every form to the new record: a task added from an item left the person three taps from the item. | [Return rule](#return-rule) |
| NAV-03 | important | Three contradictory signs of position on one screen: the back link, the marked entry, the title. | [Back link and breadcrumbs](#back-link-and-breadcrumbs) |
| NAV-04 | important | Three home screens; the installed application opened on the least useful. | [One home page](#one-home-page-today) ([ADR 0053](../adr/0053-target-navigation-and-return-rule.md)) |
| NAV-05 | important | Done reloaded the page and sent it back to the top; the undo banner was far from the finger. | [Actions in place](#actions-in-place) |
| NAV-06 | important | Eight tabs of Tasks mixing views, other sections and a setting. | [Five sections](#five-sections) |
| NAV-07 | important | Six taps for a read-only person to report a problem. | [One home page](#one-home-page-today): Report a problem on Today, the count in More |
| NAV-08 | important | With two homes, every section asked again which home. | [Current home](#current-home) |
| NAV-09 | important | More was a flat list of eleven entries, deleting the account among them. | [What goes in More](#what-goes-in-more) |
| NAV-10 | important | Instance settings on one page of almost ten screens, with nine identical Save buttons. | One index, one page per section |
| NAV-11 | important | The page of a room did not lead to its budget. | Budget of the room on its page |
| NAV-12 | minor | The browser's back button showed out-of-date undo banners and reopened sent forms. | [The bar, and the browser's back button](#the-bar-and-the-browsers-back-button) |
| NAV-13 | minor | No breadcrumbs on a computer. | [Back link and breadcrumbs](#back-link-and-breadcrumbs) |
| NAV-14 | minor | Ambiguous or duplicate labels ("Home" opening "My home", one object with two names). | One term per concept; bar labels equal the titles of their roots |
| NAV-15 | minor | Repetition folded away when adding a task from an item, whose tasks all repeat. | Repetition open by default for a task of an item |
| NAV-16 | minor | The calendar outside the home, with no back link. | The calendar is a tab of Today, on the [current home](#current-home) |

The audit compared the interface with Material 3, Apple's guidelines, the Nielsen
heuristics and WCAG 2.2 (see [References](#references)), and with other home
management applications. The full report stays outside the repository; this table
and [ADR 0053](../adr/0053-target-navigation-and-return-rule.md) keep what was
decided from it.

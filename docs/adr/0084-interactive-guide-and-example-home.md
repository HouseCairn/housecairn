# 0084. An interactive guide on an example home, one text for the web and the app

- Status: proposed. That HouseCairn has an interactive guide with examples of use, in the web
  application and in the mobile app, with the same content; that it is a guided walk on an
  **example home** (disposable, fictional values, real actions, deleted in one gesture); and the
  five scenarios are the maintainer's decisions of 2026-11-16. The rest is what this record
  proposes, with the points it cannot settle alone listed in
  [Open questions](#open-questions).
- Date: 2026-11-16
- Related: [0006](0006-home-scoping.md) (home scoping), [0013](0013-internationalization.md)
  (languages), [0049](0049-task-templates.md) (a file shared with the app),
  [0071](0071-demonstration-instance.md) (demonstration), [0074](0074-one-language-per-person-everywhere.md)
  (language of a person), [0077](0077-sync-api-for-the-mobile-app.md) and
  [0079](0079-lots-of-the-server-mode-of-the-mobile-app.md) (sync), [0048](0048-export-format-and-restore.md)
  (export and backup), [0003](0003-web-api-parity.md) (parity)

## Context

A person who opens HouseCairn for the first time meets an empty home and a screen reference
written for people who already know what a room, a task and a budget line are. The
[Examples](../guides/examples.md) page shows two jobs on the demonstration home, but it is read,
not done. The maintainer decided on 2026-11-16 that the application gets a guide that is done:

- **in the web application and in the mobile app**, with **the same content**, so that a text is
  kept up to date once;
- the form is a **guided walk on an example home**: fictional, disposable data on which the
  person does the actions for real, then deletes the home in one gesture;
- **five scenarios**: (1) create a home, (2) add rooms and equipment, (3) plan a recurring task,
  (4) report a problem, (5) follow a project and its budget. Nothing else in this lot: no stock,
  no sync, no QR labels.

"The site" below is the web application (`HouseCairn/housecairn`); "the app" is the mobile app
(`HouseCairn/housecairn-mobile`). This record only frames the work: it adds no application code.
It adds the content (`design/guide.json`) and its test
(`tests/test_design_guide.py`).

## Decision

### A walk, not a tour

The guide is a list of five scenarios, each a short list of steps (three to six). A step has a
title, one sentence, the screen it points at, and, when the data can say so, the state that ticks
it. The person opens a step, lands on the real screen (the form is pre-filled on request), does
the action, and comes back to a guide page that shows the step ticked. There is no overlay, no
coach mark and no blocked screen: the screens are the ordinary ones, so the guide cannot go out
of date against them, and a person who leaves in the middle loses nothing.

The guide is a **reading of the data of one home**. It stores no "current step": each time its page
opens, the state of the example home decides what is ticked. The only thing it stores is the tick
of a step with no state (below).

### Where it opens

| Where | Entry |
| --- | --- |
| Web | **More › Help › Guide** (a new entry of the Help group); on the welcome page of a person with no home; a card on an empty [Today](../screens/today.md) |
| App | **More › Help › Guide**; the same card on an empty Today of the first home |
| Documentation | a page `docs/getting-started/guide.md` that prints the same file, in the language of the build, with a link to the screen reference page of each step |

The three read `design/guide.json`; none holds a copy of a sentence. The guide is open to every
signed-in person; it needs no setting and sends nothing anywhere.

### The example home

- **A mark that cannot be confused.** A column `Home.is_example` (boolean, default false, with a
  database default), set only by the service that makes the example home. No form, no API field
  and no sync operation writes it; it is read only everywhere. A real home never becomes an
  example and the other way round.
- **Made by the person, in scenario 1.** Step 1 opens the form of [Add a home](../screens/new-home.md)
  pre-filled with the name, currency and hemisphere of the example (the "What the home has" list
  unticked, so that no template task is added yet); the person presses **Save**, and the service
  sees the guide's flag and sets the mark. The example home therefore starts empty and the
  person fills it scenario after scenario, the form of each step pre-filled with the values of
  the file on request ("Fill in for me" is a button; the person still presses **Save**).
- **Catching up.** A person who opens scenario 3 without having done scenario 2 gets "Add what
  the earlier scenarios make": the service `guide.fill_example(home, up_to=n)` adds the rows of the
  file whose `stage` is below `n` that are missing, through the ordinary services, in the language of
  the person (ADR 0074). Never a row of stage `n` or later, so that it ticks nothing ahead (tested).
- **One at a time.** One example home per account. Opening the guide with one present continues
  it; making a second is refused until the first is deleted.
- **Only its maker is in it.** No invitation, no second member, no API token that covers it, no
  calendar feed, no QR label, no notification channel and no reminder for it (it behaves, for
  these, as an archived home does, [0079](0079-lots-of-the-server-mode-of-the-mobile-app.md)).
- **Recognisable on every screen.** A banner "Example home" with the link **Delete the example
  home** on every page of the home, the word "Example" next to its name in the home selector and
  in **My homes**, and on the page of the home. The wording is for the card of the screens; the terms are
  added to the glossary by the first web lot (`guide` and `example-home`).
- **Deleted in one gesture.** One button, **Delete the example home**, on the guide page and in
  the banner. The service `delete_home` waives the typed name and the offer to export for a home
  with the mark, and nothing else (a real home keeps both); it erases the rows, the files and the
  manual ticks (they hang off the home and are deleted with it). One confirmation on the same
  page, not a page of its own ([Open question 2](#open-questions)).
- **Left out of the counts.** The example home is not a home for the rule "the last active home
  cannot be deleted" (the app), not counted in a limit of homes, hidden from **All homes** of Today
  and from search across homes, and does not appear in `GET /api/v1/homes/`. Its guide has no API
  counterpart: the routes are listed in `parity.EXEMPT` as a teaching aid, not data of a home.
- **Demonstration instance.** Nothing changes: the guide works on the public accounts, and the
  nightly reset erases an example home like any other.

### Server mode and sync: the example never leaves its device

**The rule: a row of an example home is never an operation, never in a snapshot and never in an
archive.** It exists on the device where it was made, and on the web the device is the instance.

Web (the instance is the server of the app):

- the function every write path calls to record a change (`sync`, ADR 0079 lot M3a) returns
  without writing when the home of the row has the mark, so the example has no `pos`, no field
  clock and no tombstone;
- `GET /api/v1/sync/homes/` does not list it, and the routes under `/sync/homes/{h}/` (changes,
  snapshot, push, files) answer 404 for it, as for a home the connection never had; a pushed
  `is_example` is `rejected` with `forbidden`;
- **Export all data** and the tables as CSV refuse it, **Restore a home** never makes one
  (an archive carries no `is_example`; the field reads false), and the instance-wide backup
  of ADR 0048 is left as it is, a copy of the database that carries the row like any other
  ([Open question 3](#open-questions));
- the reminders, the web push, Apprise, the calendar feeds, the webhooks and the Home Assistant
  events (`events`) skip it, and the consent page of the app lists only real homes.

App (every way of ADR 0010 of the app):

- the example home lives in the local database with `is_example = 1` and **in every way**: alone,
  family sync through the relay and "My Housecairn server". The repository writes its rows without
  `op_log` and without `field_clock`; the sync engine, the relay snapshot, the encrypted export and
  the upload of "keep this phone's homes" skip it. A phone in server mode keeps this one local
  home beside the server's homes ([Open question 1](#open-questions)).

**The test, in both repositories.** `tests/test_example_home_never_leaves.py` (web) makes an example
home with every row of the file through the services, then asserts, for each way out: no row in
the change tables of `sync`; 404 on the four routes; no home in `GET /sync/homes/`; the export, the
CSV tables and the calendar feed refuse or are empty; no notification sent. A **guard test**, like
the one of ADR 0079, parses the apps `sync`, `notifications`, `calendar`, `events`, `apitokens` and
`labels` and fails on a read of `Home.objects` that does not go through `Home.objects.real()`, so
that a new way out cannot forget the rule. The app adds a property test: after random writes on an
example home and a real one, the `op_log` holds only rows of the real one, and the snapshot and
the export hold nothing of the example.

### How a step ticks by itself

A step has either `done_when` or `manual: true`. `done_when` is a state of the data of the example
home: an entity, a minimum number of rows, and filters on fields. The vocabulary is small so that
the web (Django) and the app (Drift) evaluate it the same way:

- `entity`: `app.model` of the web (`rooms.room`, `tasks.occurrence`...), which the app maps to
  its tables, as it does for the sync operations (ADR 0077, `t`);
- `where`: a field of the model, compared to a value, or to `{"not": v}`, `{"gt": n}` or
  `{"set": true|false}`; a foreign key is named without `_id` (`budget_line`);
- `min`: how many rows must match; the rows are those of the example home only.

Both platforms evaluate it on every open of the guide; the web (`guide.states`) writes the
vectors (`tests/fixtures/guide_states.json`: rows in, steps ticked out) and the app pins them, as
it pins the recurrence vectors. The test of this record already checks every field and value
against the models, that the whole example ticks every step that has a state, and that the rows of
earlier scenarios tick nothing of a later one.

| Step | Screen | Ticked by |
| --- | --- | --- |
| 1.1 Add the example home | [new-home](../screens/new-home.md) | a home with the mark exists |
| 1.2 Look at its settings | [home](../screens/home.md) | by hand |
| 1.3 Find your way around | [today](../screens/today.md) | by hand |
| 2.1 Add a room | [room-form](../screens/room-form.md) | at least 1 room |
| 2.2 Add a second room | [room-form](../screens/room-form.md) | at least 2 rooms |
| 2.3 Add a piece of equipment | [item-form](../screens/item-form.md) | an item of kind `equipment` |
| 2.4 Note its brand and model | [item](../screens/item.md) | an item of kind `equipment` with a brand |
| 3.1 Add a task | [task-form](../screens/task-form.md) | at least 1 task |
| 3.2 Make it come back | [task-form](../screens/task-form.md) | a task whose `repeat_mode` is not `once` |
| 3.3 Mark it done | [today](../screens/today.md) | an occurrence with `status` `done` |
| 3.4 Undo a mistake (optional) | [today](../screens/today.md) | by hand |
| 4.1 Report a problem | [report-form](../screens/report-form.md) | at least 1 report |
| 4.2 Open the report | [report](../screens/report.md) | by hand |
| 4.3 Make it a task, or close it | [report](../screens/report.md) | a report whose `status` is not `open` |
| 5.1 Add a project | [project-form](../screens/project-form.md) | at least 1 project |
| 5.2 Plan the cost | [budget-line-form](../screens/budget-line-form.md) | at least 2 budget lines with `planned` above 0 |
| 5.3 Enter two quotes | [quote-form](../screens/quote-form.md) | at least 2 quotes |
| 5.4 Accept one | [quote-form](../screens/quote-form.md) | a quote `accepted` with a `budget_line` |
| 5.5 Record a payment | [budget-line-form](../screens/budget-line-form.md) | a budget line with `paid` above 0 |
| 5.6 Set the yearly envelope | [budget](../screens/budget.md) | an annual envelope exists |

**Four steps have no state of their own** and carry a checkbox ticked by hand: 1.2, 1.3, 3.4 and
4.2. They are reading steps (look at a screen) or an undo that leaves no trace once done. The tick
is a row `guide.ManualTick(home, step_id)` on the web and a row of a local table in the app, both
tied to the example home so that deleting it deletes them; it is never synced. A state can be
ticked by something other than the guide (a person who already has a repeating task on another
example home is not an issue, since the rows are those of **this** home); the guide never unticks
a step that was ticked and whose state is gone later (a deleted room), so that deleting a row does
not make the walk go backwards.

Two cautions of the step states. The template tasks that "What the home has" adds when a home is
created would tick 3.1, so the form of step 1 leaves the list unticked. And a state ticks as soon
as it is true, whoever made the row: this is a walk, not an exam.

### One source for the content

**A JSON file, `design/guide.json`, with English and French side by side.** It is the format of
`design/glossary.json` and `design/tokens.json`, the two files the app already keeps a copy of:

- the app reads JSON with no new dependency, and the file is data rather than prose to be
  written by hand in a YAML block, so JSON does;
- the web, the documentation build and the app all read the same bytes: no translation catalog,
  no `makemessages`, no string to carry twice. A sentence is edited in one place in both languages;
- it sits in `design/`, with the other files shared with the app, and the app copies it
  **byte for byte** through `tool/web_alignment.py` as a new element, like the task templates, so
  that `check` fails when the copy lags behind a release.

**Labels of screens are never typed in a sentence.** `{{save}}`, `{{done}}`, `{{report-a-problem}}`
cite a term of `design/glossary.json`; each reader writes the term in its language and case.
The test refuses a term that is not in the glossary, a term cited in English and not in French,
and the two languages that cite different terms. A label the glossary does not have is not
cited: the sentence says what to do in plain words. The first web lot adds the terms the guide
needs (the guide, the example home, **Add a home**, **Add a project**, **Add a budget line**).

**The checks of `tests/test_design_guide.py`**: five scenarios in order; step ids numbered by
scenario; every text in both languages, short (60 characters for a title, 130 for a sentence) and
free of the words of a developer; every screen is a page of `docs/screens/`; each step has a state
or is manual; every entity, field and value of a state is a model, a field and a choice of the web
(the field `is_example` is the only one that does not exist yet); the rows of the example point to
rows of the same or an earlier stage; amounts are decimal strings in euros; and the example
reuses the demo home's values wherever it has the same row.

The file has three parts: `example` (the home and its rows), `scenarios` (titles, summaries,
steps) and `version`, raised when the shape changes.

### The example home, defined

It is the end state the five scenarios aim at, in euros, in English and in French. It reuses the
fictional data set of `load_demo_data` (the demonstration home) wherever it has the same row, and
the test pins the equality, so that there is one value in one place:

| Stage | Row | English | French |
| --- | --- | --- | --- |
| 1 | Home | Example house, EUR, northern hemisphere | Maison d'exemple, EUR, hémisphère nord |
| 2 | Room | Kitchen (kitchen) | Cuisine |
| 2 | Room | Bathroom (bathroom) | Salle de bain |
| 2 | Equipment, in the kitchen | Espresso machine, Brewmark BM-200 | Machine expresso, Brewmark BM-200 |
| 3 | Task, kitchen, on the machine | Descaling, 3 months after the last time, 15 minutes | Détartrage, 3 mois après la dernière fois, 15 minutes |
| 3 | Occurrence | done once, the next one planned by the service | faite une fois, la suivante planifiée par le service |
| 4 | Report, kitchen | The kitchen tap drips (resolved) | Le robinet de la cuisine goutte (classé) |
| 5 | Project, bathroom | Redo the bathroom, planned | Refaire la salle de bain, prévu |
| 5 | Budget line, materials | Tiles, planned 1,200.00, paid 480.00 | Carrelage et faïence, prévu 1 200,00, payé 480,00 |
| 5 | Budget line, labour | Plumber (shower), planned 3,000.00, paid 0.00 | Plombier (douche), prévu 3 000,00, payé 0,00 |
| 5 | Quote, accepted on the plumber line | Linden Plumbing, 2,850.00 | Plomberie des Tilleuls, 2 850,00 |
| 5 | Quote, received | Bath Services, 3,400.00 | Bains Services, 3 400,00 |
| 5 | Annual envelope | the current year, 6,000.00 | l'année en cours, 6 000,00 |

The demo home is bigger (six rooms, six tasks, a paint project, stock with a water filter, two
inspiration links) and carries no report. The example keeps what the five scenarios need and
leaves out the stock and the links, which are not in this lot; it adds the report, which the
demo lacks. The demo home is not changed. Dates are relative to the day the row is made (the
file says `due_offset_days`, `issued_offset_days`), like the demo, in the time zone of the home
(ADR 0032). The texts of a row are in the language of the person who makes the home (ADR 0074)
and are data from then on, not translated afterwards.

## Plan: cards, files, order

Web (`HouseCairn/housecairn`), then app (`HouseCairn/housecairn-mobile`). A card that touches the
sync or the export rule is on the default model with a review by another; the others by the
usual rule.

| Card | Content | Files | Depends on |
| --- | --- | --- | --- |
| **W1. Example home, rules** | `Home.is_example` and its migration; `Home.objects.real()`; app `guide` with `services` (`create_example_home`, `fill_example`, `remove_example`), `states` (the evaluator, reads `guide.json`), `ManualTick`; `delete_home` waiver; the rule in `sync`, the export, the CSV, `notifications`, `calendar`, `events`, `apitokens`, All homes, search; the guard test and `test_example_home_never_leaves.py`; the vectors `tests/fixtures/guide_states.json`; glossary terms and catalogs | `homes/models.py`, `homes/services.py`, `guide/`, `sync/`, `core/`, `design/glossary.json`, `design/patterns.md`, `tests/` | this record merged |
| **W2. Guide screens** | `/guide/` and `/guide/<scenario>/`, the pre-filled forms (a query string), the banner, the delete button, the entries (More, welcome page, empty Today), `core/help.py`, `parity.EXEMPT` | `guide/views.py`, `guide/templates/`, `core/help.py`, `core/templates/`, `parity.py`, `docs/screens/` | W1 |
| **W3. Guide in the documentation** | a Sphinx directive that prints `guide.json` in the language of the build; `docs/getting-started/guide.md` | `docs/_ext/`, `docs/getting-started/guide.md`, `docs/locale/fr/` | this record merged (files apart from W1: runs next to it) |
| **A1. Alignment** | `guide.json` and the vectors as an element of `tool/web_alignment.py`; the glossary copy refreshed | `tool/web_alignment.py`, `tool/web_alignment.json`, `design/guide.json`, `test/fixtures/` | a release with W1 |
| **A2. Example home on the phone** | `is_example` column and migration (schema 8); `create_example_home`, `fill_example`, `remove_example`; the evaluator in Dart on the vectors; no `op_log` for the rule; the exclusion in the engine, the relay, the export and the upload; the property test; "last active home" | `lib/data/`, `lib/sync/`, `lib/domain/guide.dart`, `test/` | A1 |
| **A3. Guide screens on the phone** | the guide pages, the pre-filled forms, the entries, the chrome strings (ARB keys, English and French) | `lib/ui/guide_screens.dart`, `lib/ui/sections.dart`, `lib/l10n/`, `test/` | A2 |

Order: this record, then **W1 and W3 side by side** (W3 reads only the file), W2 after W1; A1 after
the first release that carries W1, A2, A3. Parallelism by the project's rule: W1 and W2 both touch
`core/` and the templates, so they do not run together.

**Screens of `docs/screens/`**: added `guide.md` (the five scenarios with their progress and
**Delete the example home**) and `guide-scenario.md` (the steps of one scenario). Changed:
`start-page.md` (the welcome card), `more.md` (Guide in Help), `homes.md` (the mark and the order
of the list), `home.md` (the banner, **Delete the example home** with no typed name, no export),
`new-home.md` (opened from the guide: pre-filled, "What the home has" unticked), `today.md` (the
card of an empty Today), `room-form.md`, `item-form.md`, `task-form.md`, `report-form.md`,
`project-form.md`, `budget-line-form.md`, `quote-form.md` (the pre-fill from the guide); in the
app, `spec.md` (Homes: the example is never synced) and `plan.md`. Release notes: one fragment
**new** with W2 and one with A3. This record changes nothing a person sees, so it has no fragment.

## Consequences

- The guide adds no table to a real home: one column on `Home`, and one small table of ticks
  that hangs off the example home.
- A new way out for a home (a new export, a new integration) must go through `Home.objects.real()`;
  the guard test fails otherwise. This is the cost of the rule.
- The mark is a column of the web model that the sync protocol ignores. A phone that pushes
  `homes.home` with `is_example` is refused; an older phone never sends it.
- The guide text is public, in two languages, in `design/`: it holds no personal data, and every
  name in it is fictional.
- The guide cannot get a new language by a catalog: a third language is a third key in the file,
  and the test asks for it everywhere.

## Open questions

1. **Server mode in the app.** Does the guide exist on a phone connected to a server, given that
   "everything is on the server"? *Recommended*: yes, with the example home local to the phone and
   never uploaded (the rule above); it is the only local home allowed in that way, and it goes
   with one gesture. The alternative, sending the guide to the web, would break the rule.
2. **Deleting in one gesture.** One tap with no confirmation, or one tap and one confirmation?
   *Recommended*: one tap and a confirmation on the same page (a dialog), no typed name; the data
   cannot come back (files are erased), the person made some of it, and a stray tap in a banner
   is easy.
3. **The instance backup.** `backup_instance` copies the database, so an example home rides in it.
   *Recommended*: leave it (a copy of the instance, not an export of real homes) and say so in
   the page of the backup. The alternative, stripping example homes from the copy, adds a step
   to a command that today is one `sqlite3` online backup.
4. **Scope of the mark.** `is_example` on `Home` is the proposal. A separate table of example
   homes would leave `Home` alone but every query of homes would join it. *Recommended*: the
   column.
5. **Catching up.** Is "Add what the earlier scenarios make" wanted, or must the scenarios be
   done in order? *Recommended*: keep it, so that a person who comes for the budget is not
   blocked by the rooms.
6. **The word "site".** This record reads "the site" as the web application and adds the guide
   to the documentation site as a third reader. *Recommended*: keep the third reader, it costs one
   Sphinx directive and no translation.

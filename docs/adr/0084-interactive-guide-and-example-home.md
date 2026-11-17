# 0084. An interactive guide on an example home, one text for the web and the app

- Status: accepted. That HouseCairn has an interactive guide with examples of use, in the web
  application and in the mobile app, with the same content; that it is a guided walk on an
  **example home** (disposable, fictional values, real actions, deleted in one gesture); and the
  five scenarios are the maintainer's decisions of 2026-11-16. So are the answers to the
  questions the first draft left open, in [Decisions of the maintainer](#decisions-of-the-maintainer).
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

The guide is a **reading of the data of one home**. It stores no "current step". What it stores
is the **tick** of each step, once the step is done (below): the state of the example home
decides when a step ticks, and a tick, once stored, stays.

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
  unticked, so that no template task is added yet); the person presses the button that creates the home, and the service
  sees the guide's flag and sets the mark. The example home therefore starts empty and the
  person fills it scenario after scenario, the form of each step pre-filled with the values of
  the file on request ("Fill in for me" is a button; the person still presses the button that
  saves the form).
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
  home** on every page of the home, and the word "Example" next to its name in **My homes** and
  on the page of the home. The home selector does not list it (it lists the homes a section may
  open on). The wording is for the card of the screens; the terms are added to the glossary by
  the first web lot (`guide` and `example-home`).
- **Deleted in one gesture: one press and one confirmation on the same page.** One button,
  **Delete the example home**, on the guide page and in the banner. Pressing it shows a
  confirmation on the same page (in the web, in place of the button, with **Delete** and
  **Cancel**; in the app, a dialog), not a page of its own, and **no name to type**. The service
  `delete_home` hands a home with the mark to `remove_example`, which waives the typed name and the
  offer to export, and nothing else (a real home keeps both, tested). It erases the rows, the
  files and the ticks (they hang off the home), **and the audit entries of its `home_id`**
  (`AuditEvent.home_id` is a plain `UUIDField`, not a foreign key, so the cascade of `Home` does not
  reach it; `erase_home` deletes them by hand, and so does `remove_example`). The entries written
  during the life of the home (`home.created`, `report.created` with the title of the report,
  `invitation.*`, `member.*`) exist until then; what is guaranteed is that none survives the
  deletion. It writes **no** row of erased home and no new audit entry: the home never reached a
  sync, so no connected app has to be told it is gone.
- **Left out of the counts.** The example home is not a home for the rule "the last active home
  cannot be deleted" (the app), not counted in a limit of homes, hidden from **All homes** of Today
  and from search across homes, and does not appear in `GET /api/v1/homes/`. Its guide has no API
  counterpart: the routes are listed in `parity.EXEMPT` as a teaching aid, not data of a home.
- **Demonstration instance.** Nothing changes: the guide works on the public accounts, and the
  nightly reset erases an example home like any other.

### Server mode, sync and backups: the example never leaves its device

**The rule: a row of an example home is never an operation, never in a snapshot, never in an
export and never in a backup of the server.** It exists on the device where it was made, and on
the web the device is the instance. In the app, **the guide is available in server mode too**:
the example home stays **local to the phone and is never sent to the server**; it sits beside
the server's homes and goes with one gesture.

**One point of passage per way out.**

- *Writes:* the rule lives in **`changes.record_change` itself**, the one function every
  recorder of a change goes through. It has several callers (`sync.rows`, `projects.changelog`,
  and in the sync API `sync.pushing`, `sync.repairs` and `sync.retention`), so a rule placed in
  one of them would leave the others open: a retention sweep, for instance, records a change after
  it purges soft-deleted rows of any home. For a row of a home with the mark `record_change`
  returns without recording, so the example has no `pos`, no field clock and no tombstone, whoever
  calls. The guard of ADR 0079 (`tests/test_sync_guard.py`, `COVERED_APPS`) keeps its meaning: it
  asserts that a write of a **real** home records a change. A second pass of the same scenarios on
  an example home asserts that it records none, and one test per other caller (push, repair,
  retention) asserts the same on an example home. `guide.StepTick` is listed in the guard's
  `NOT_SYNCED`, with its reason.
- *Reads of the homes of an account:* `homes.services.homes_for` and
  `homes.scoping.homes_for_request` leave example homes out **by default**. The caller that wants
  them says `include_example=True`, and there are two: the web pages of the example home itself
  (the `/h/<home id>/` views) and the guide. Everything else gets none: the sync API, the API of
  homes, the calendar feeds, the labels, the choice of homes of an API token or of a connected
  app, the exports, All homes. The one filter is `homes.services.real_homes(homes)`; the reads
  that do not go through `homes_for` (`Membership` in `notifications`) use it too.
- *Instance backup:* `backup_instance` is the **single point of passage** of a backup of the
  server. Its `dump_database` and its walk of `/data/media/` are the only places that copy data
  out of the instance, and they leave the example homes out (below).
- *Erasing:* `remove_example` writes no `ErasedHome` (above), whereas `erase_home` of a real home
  keeps writing it (ADR 0077).

Web (the instance is the server of the app):

- `GET /api/v1/sync/homes/` and `GET /api/v1/homes/` do not list it, and the routes under
  `/api/v1/sync/homes/{h}/` (changes, snapshot, push, files) answer 404 for it, as for a home the
  connection never had; a pushed `is_example` is `rejected` with `forbidden`;
- **Export all data**, the tables as CSV, the CSV of every budget page (`?export=csv`) and the
  contact file of a contractor (`.vcf`) refuse it, and their buttons are not shown for it; **Restore
  a home** never makes one (an archive carries no `is_example`; the field reads false);
- the reminders, the web push, Apprise, the calendar feeds, the webhooks, the Home Assistant
  events (`events`), the QR labels and the API tokens skip it, and the consent page of the app
  lists only real homes.

**The instance backup leaves it out.** The first draft of this record let the example home ride
in the copy of the database; the maintainer decided the opposite and this record replaces it. The
exclusion is applied **on the copy**, never on the live database, before the copy enters the
archive: the rows of the example homes are removed from the copy, **with the `AuditEvent` rows of
their `home_id`** (they carry no foreign key, and hold titles such as that of a report), and the files of those homes
are not archived. The files of a home are not in one directory:
`homes.files` keeps them in `MEDIA_ROOT/<folder>/<home id>/` for every folder an app declares
(`MEDIA_FOLDERS`, which holds every folder registered once the apps are loaded), and `home_directories(home_id)` lists them all.
The exclusion goes through `home_directories`, never through a path written in the backup, so a
folder an app declares later is left out too. How it is done on each engine (SQLite's
online backup, `pg_dump`) is settled by the first web lot; what it must satisfy is the two tests
below. A restore of an instance backup, and **Restore a home**, never create an example home:
whatever a file carries, the mark is read as false, and a home that arrives marked is erased
(tested). A backup made before this record cannot carry the mark, since the column does not exist
there.

App (every way of ADR 0010 of the app):

- the example home lives in the local database with `is_example = 1` and **in every way**: alone,
  family sync through the relay and "My Housecairn server". The repository writes its rows without
  `op_log` and without `field_clock`; the sync engine, the relay snapshot, the encrypted export and
  the upload of "keep this phone's homes" skip it. A phone in server mode keeps this one local
  home beside the server's homes.

**The tests, one per rule.** In the web, `tests/test_example_home_never_leaves.py` unless said
otherwise, each test making an example home with every row of the file through the services:

| Rule | Test |
| --- | --- |
| a write records no change | `test_a_write_of_an_example_home_records_no_change` (in `test_sync_guard.py`, the same scenarios on an example home, through `sync.rows` and `projects.changelog`); `test_a_push_records_nothing_for_the_example_home`, `test_a_repair_records_nothing_for_the_example_home` and `test_a_retention_sweep_records_nothing_for_the_example_home`, one per other caller of `record_change` |
| no snapshot, no list | `test_the_sync_homes_list_leaves_out_the_example_home`, `test_the_api_homes_list_leaves_out_the_example_home` |
| four routes closed | `test_the_four_sync_routes_answer_404_for_the_example_home` |
| a pushed mark is refused | `test_a_pushed_is_example_is_rejected_as_forbidden` |
| no erased-home row, no audit entry | `test_removing_the_example_home_leaves_no_erased_home` (creates a report in the example home first, then reads `AuditEvent` after the deletion and expects none for its `home_id`) |
| no export, no CSV | `test_export_and_csv_refuse_the_example_home` |
| no CSV of a budget page | `test_the_csv_of_the_budget_refuses_the_example_home` (one case per page) |
| no contact file | `test_the_contact_file_of_a_contractor_refuses_the_example_home` |
| no calendar | `test_the_calendar_feeds_leave_the_example_home_out` |
| no message | `test_no_reminder_push_apprise_webhook_event_or_alert_is_sent_for_it` |
| no label, no token | `test_labels_and_api_tokens_cannot_reach_the_example_home` |
| nothing writes the mark | `test_no_form_and_no_api_writes_is_example` |
| one per account | `test_one_example_home_per_account` |
| catching up ticks nothing ahead | `test_fill_example_ticks_nothing_ahead` |
| deleting waives only for it | `test_a_real_home_still_needs_its_name_and_offers_its_export` |
| **not in the instance backup** | `tests/test_example_home_backup.py::test_the_instance_backup_leaves_out_the_example_home` |
| **a restore creates none** | `tests/test_example_home_backup.py::test_a_restore_never_creates_an_example_home` |

The backup test makes a database holding a real home and an example home, each with **a file in
every folder in `MEDIA_FOLDERS` once the apps are loaded** (the test loops over `MEDIA_FOLDERS`, so
a folder registered later is covered), and an act that writes an audit entry on each (creating a
report). It runs `backup_instance` and reads the archive: no row, no file and no `AuditEvent` of
the example home, in any folder, the real home whole. The restore
test restores a database in which a home is marked, and expects no example home
afterwards.

A **guard test**, like the one of ADR 0079, parses **every app of the package** (not a list of
apps, so that a new app is covered from its first commit) and fails on a read of `Home.objects`,
`Home.all_objects` or `Membership.objects` that does not go through `homes_for`,
`homes_for_request` or `real_homes`, unless the site is in a list of audited reads in the test,
each with its reason (the way `NOT_SYNCED` says why a table is not synced). It also fails on an
`include_example=True` outside the two callers above. The audit made for this record, on the code
of `main`, sorts the existing reads in three groups:

- *Must filter, and W1 changes them:* `accounts/services.py` (the homes of which the user is the only owner,
  offered for export and checked before an account is deleted: an example home is neither offered
  nor does it block the deletion, and it goes with the account), `notifications` (reminders and
  alerts), `calendar`, `events`, `apitokens`, `labels`, `homes/api.py`, `sync` (lists and routes).
- *Read the members of one home already resolved:* `homes/views.py`, `homes/services.py` (members,
  roles, invitations), `homes/current.py`, `sync/reading.py`, `sync/pushing.py`, `sync/repairs.py` (the `Home.objects.get` on a home already resolved), `tasks/services.py`,
  `audit/services.py` (the owner check on a given home). They take a home from a caller that has
  already filtered, so they are admitted.
- *Must see every home on purpose:* `homes/quotas.py` (the disk really held, example homes
  included; the administrator's list shows identifiers and numbers, never a name),
  `homes/restore.py` (an identifier already taken), `homes/services.py` (erasing a home),
  `sync/changes.py` and `sync/files.py` (a row lock, nothing read out), `instance/providers.py`
  and `instance/views.py` (a provider cannot be removed while any home requires it), `demo/` and
  `load_demo_data` (they make homes).

`homes/jobs.py` and `homes/csv_export.py` read no list of homes: they receive one home, and
the refusal for an example home is in `start_export` and in the CSV export themselves, covered by
`test_export_and_csv_refuse_the_example_home`. Every site of the second and third groups is in
the test's list with its reason, so that **a new read of the homes of an account cannot slip past
the rule unnoticed**: it fails the test until it filters or is audited. The guard cannot see a
way out that reads no `Home` or `Membership`, such as a new file store or a new network call;
that case rests on the named tests above, one per way out, and on the review of the lot. The app
adds a property test: after random writes on an example home and a real one, the `op_log` holds
only rows of the real one, and the snapshot and the export hold nothing of the example.

### How a step ticks by itself

A step has either `done_when` or `manual: true`. `done_when` is a state of the data of the example
home: an entity, a minimum number of rows, and filters on fields. The vocabulary is small so that
the web (Django) and the app (Drift) evaluate it the same way:

- `entity`: `app.model` of the web (`rooms.room`, `tasks.occurrence`...), which the app maps to
  its tables, as it does for the sync operations (ADR 0077, `t`);
- `where`: a field of the model, compared to a value, or to `{"not": v}`, `{"gt": n}` or
  `{"set": true|false}`; a foreign key is named without `_id` (`budget_line`);
- `min`: how many rows must match; the rows are those of the example home only.

**A tick is stored as soon as its state is true, and it stays.** Both platforms evaluate the
states on every page of the example home (the banner is on each of them) and on the guide pages;
each step whose state is true and that has no tick yet gets one. The guide never goes backwards:
a step ticked stays ticked even if its state goes away afterwards, a deleted room, or a task
marked done and then undone. That is what keeps step 3.3 ticked after step 3.4, which the guide
itself proposes. A state made true and undone between two pages is not seen; this is a walk, not
an exam. The web (`guide.states`) writes the vectors (`tests/fixtures/guide_states.json`) and
the app pins them, as it pins the recurrence vectors. A vector is a sequence of snapshots of the
rows, each with the steps ticked after it, so that persistence is pinned too: one case has the
done occurrence of 3.3 planned again in its second snapshot, and 3.3 is still ticked in the
expected output. The test of this record already checks every field and value against the
models, that the whole example ticks every step that has a state, and that the rows of earlier
scenarios tick nothing of a later one. The web adds `tests/test_guide_states.py`:
`test_a_step_ticks_the_first_time_its_state_is_true` and
`test_a_tick_stays_when_its_state_goes_away`, both on the vectors.

| Step | Screen | Ticked by |
| --- | --- | --- |
| 1.1 Add the example home | [new-home](../screens/new-home.md) | a home with the mark exists |
| 1.2 Look at the home's settings | [home](../screens/home.md) | by hand |
| 1.3 Find your way around | [today](../screens/today.md) | by hand |
| 2.1 Add a room | [room-form](../screens/room-form.md) | at least 1 room |
| 2.2 Add a second room | [room-form](../screens/room-form.md) | at least 2 rooms |
| 2.3 Add a piece of equipment | [item-form](../screens/item-form.md) | an item of kind `equipment` |
| 2.4 Note its brand and model | [item-form](../screens/item-form.md) | an item of kind `equipment` with a brand |
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
| 5.6 Set the yearly envelope | [projects](../screens/projects.md) (**Set the envelope**) | an annual envelope exists |

**Four steps have no state of their own** and carry a checkbox ticked by hand: 1.2, 1.3, 3.4 and
4.2. They are reading steps (look at a screen) or an undo that leaves no trace once done. Their tick
is a row `guide.StepTick(home, step_id, ticked_at)` on the web and a row of a local table in the
app, the same row that a state writes, tied to the example home so that deleting it deletes them;
it is never synced. A state can be ticked by something other than the guide (the rows are those
of **this** home, so another home is not an issue).

Two cautions of the step states. The template tasks that "What the home has" adds when a home is
created would tick 3.1, so the form of step 1 leaves the list unticked. And a state ticks as soon
as it is true, whoever made the row.

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
(the field `is_example` is the only one that does not exist yet); a step on a read-only screen
(the budget, the history) carries neither a state nor a pre-fill; the rows of the example point to
rows of the same or an earlier stage; amounts are decimal strings in euros; and the example
reuses the demo home's values wherever it has the same row.

The file has three parts: `example` (the home and its rows), `scenarios` (titles, summaries,
steps) and `version`, raised when the shape changes.

The image of the application has no `design/` directory, so the server reads a copy of the file,
`src/housecairn/guide/guide.json`, which `tests/test_guide_content.py` keeps byte-equal to
`design/guide.json`: a change of the source is copied there in the same pull request.

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
| 4 | Report, kitchen | The kitchen tap drips (resolved) | Le robinet de la cuisine goutte (résolu) |
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

## Plan: lots, files, order

Web (`HouseCairn/housecairn`), then app (`HouseCairn/housecairn-mobile`). The lots that touch the
sync, the export or the backup rule are the sensitive ones.

| Lot | Content | Files | Depends on |
| --- | --- | --- | --- |
| **W1. Example home, rules** | `Home.is_example` and its migration; `real_homes` and the default of `homes_for` and `homes_for_request`; app `guide` with `services` (`create_example_home`, `fill_example`, `remove_example`), `states` (the evaluator, reads `guide.json`), `StepTick`; the waiver of `delete_home`; the rule in `changes.record_change`, the exports and their job, the CSV, the sole-owner list and the deletion of an account, `notifications`, `calendar`, `events`, `apitokens`, `labels`, All homes, search; the exclusion in `backup_instance` and the cleaning of `restore_instance`; the guard tests (every app parsed, the list of audited reads) and the tests of the table above; the vectors `tests/fixtures/guide_states.json`; glossary terms and catalogs | `homes/models.py`, `homes/services.py`, `homes/scoping.py`, `homes/api.py`, `homes/jobs.py`, `homes/csv_export.py`, `homes/exports.py`, `homes/restore.py`, `homes/views.py`, `accounts/services.py`, `audit/`, `guide/`, `sync/` (`changes.py` and its callers), `notifications/`, `calendar/`, `events/`, `apitokens/`, `labels/`, `instance/management/commands/backup_instance.py`, `instance/management/commands/restore_instance.py`, `core/`, `design/glossary.json`, `design/patterns.md`, `tests/`, `docs/administration/backup.md`, `docs/administration/data.md`, `docs/api/`, `docs/screens/home.md` | this record merged |
| **W2. Guide screens** | `/guide/` and `/guide/<scenario>/`, the pre-filled forms (a query string), the banner, the delete button and its confirmation, the entries (More, welcome page, empty Today), `core/help.py`, `parity.EXEMPT` | `guide/views.py`, `homes/views.py`, `templates/guide/`, `templates/homes/`, `templates/` (the start page, More, Today), `core/help.py`, `parity.py`, `docs/screens/` and its catalogs under `docs/locale/fr/LC_MESSAGES/screens/` | W1 |
| **W3. Guide in the documentation** | a Sphinx directive that prints `guide.json` in the language of the build; `docs/getting-started/guide.md` and its entry in the table of contents | `docs/_ext/`, `docs/getting-started/guide.md`, `docs/getting-started/index.md`, `docs/locale/fr/LC_MESSAGES/getting-started/` (`index.po`, `guide.po`) | this record merged (no file in common with W1 or W2: runs next to W1) |
| **A1. Alignment** | `guide.json`, `glossary.json` and the vectors as elements of `tool/web_alignment.py`, copied byte for byte; both JSON files declared as assets of the app | `tool/web_alignment.py`, `tool/web_alignment.json`, `tool/test_web_alignment.py`, `pubspec.yaml`, `design/guide.json`, `design/glossary.json`, `test/fixtures/` | a release with W1 |
| **A2. Example home on the phone** | `is_example` column and migration (schema 8); `create_example_home`, `fill_example`, `remove_example`; the evaluator in Dart on the vectors, with the `StepTick` table; no `op_log` for the rule; the exclusion in the engine, the relay, the export and the upload; the property test; "last active home" | `lib/data/`, `lib/sync/`, `lib/domain/guide.dart`, `test/` | A1 |
| **A3. Guide screens on the phone** | the guide pages, the pre-filled forms, the confirmation dialog, the entries, the chrome strings (ARB keys, English and French) | `lib/ui/guide_screens.dart`, `lib/ui/sections.dart`, `lib/l10n/`, `test/` | A2 |

The app has no release of its own: `design/guide.json` and `design/glossary.json` reach it as
byte-for-byte copies of the web's, so that the glossary terms a text cites exist on both sides.
The guide is read by the app from `design/`, not from its assets, hence the declaration in
`pubspec.yaml`.

Order: this record, then **W1 and W3 side by side** (W3 reads only the file), W2 after W1; A1
after the first release that carries W1, A2, A3. Parallelism by the project's rule: W1 and W2
share `core/`, the `guide/` application (W1 its services and states, W2 its views), and
`docs/screens/home.md` (W1 the mark, W2 the banner and the delete button), and W2 reads what W1
adds to `homes/` and `core/`; they do not run together. W1 and W3 share no file.

**Screens of `docs/screens/`**: added `guide.md` (the five scenarios with their progress and
**Delete the example home**) and `guide-scenario.md` (the steps of one scenario). Changed:
`start-page.md` (the welcome card), `more.md` (Guide in Help), `homes.md` (the mark and the order
of the list), `home.md` (the banner, **Delete the example home** with one confirmation, no typed
name, no export), `new-home.md` (opened from the guide: pre-filled, "What the home has"
unticked), `today.md` (the card of an empty Today), `room-form.md`, `item-form.md`,
`task-form.md`, `report-form.md`, `project-form.md`, `projects.md` (the envelope, pre-filled from
the guide), `budget-line-form.md`, `quote-form.md` (the pre-fill from the guide); in the app,
`spec.md` (Homes: the example is never synced) and `plan.md`. Release notes: one fragment **new**
with W2 and one with A3. This record changes nothing a person sees, so it has no fragment.

**How W2 settled what this record left to it** (the web screens):

- *The query of a filled-in form* names the fields of the form (`?name=Kitchen&kind=kitchen`).
  A view lists the fields it takes (`guide.prefill.PrefillMixin`), and each value goes through
  the `clean` of its field before it is shown: a value the field refuses is left out. The post
  is checked as any other, and the rights of the form are those of its view. Only the forms of
  the example home are filled in (and Add a home when it carries `?guide=1`): on a real home the
  query is ignored, so that no link puts a made-up value in an edit form that saves as it is
  changed ([0082](0082-sheets-saved-field-by-field.md)).
- *A step on a row already there* (the brand of the machine, the repetition of the task, the
  payment of a line) opens the form of that row, found as `fill_example` finds it; otherwise the
  form that adds it. A line or a quote has no link before its project exists.
- *The new home of step 1* is the ordinary form with `?guide=1`, kept in a hidden field: its
  post calls `guide.services.create_example_home`, which sets the mark and refuses a second one.
- *The confirmation in place* of **Delete the example home** is the same page with
  `?delete-example=1`, so that it needs no script; the post goes to `/guide/delete/<home id>/`,
  which answers 404 for any home but the example home owned by the person. The design guide
  records it as the one exception to its confirmation page.
- *A step ticked by hand* has **Mark as done**, and is never unticked, as a state's tick.
- *My homes* shows the example home after the real ones, marked **Example**; the home selector
  does not, since it lists the homes a section may open on.
- *The terms* a text cites are written from the catalog message the glossary names for the
  web (`guide/terms.py`, kept equal to the glossary by its test), since the image has no
  `design/` directory.

## Consequences

- The guide adds no table to a real home: one column on `Home`, and one small table of ticks
  that hangs off the example home.
- A new way out for a home (a new export, a new integration) must go through `homes_for` or
  `real_homes()`; the guard test fails otherwise. This is the cost of the rule. The instance
  backup is no longer one `sqlite3` online backup: it takes one more step on the copy.
- The mark is a column of the web model that the sync protocol ignores. A phone that pushes
  `homes.home` with `is_example` is refused; an older phone never sends it.
- The guide text is public, in two languages, in `design/`: it holds no personal data, and every
  name in it is fictional.
- The guide cannot get a new language by a catalog: a third language is a third key in the file,
  and the test asks for it everywhere.

## Decisions of the maintainer

The first draft of this record asked six questions. The maintainer answered them on 2026-11-16;
the answers are decisions, and the sections above carry them.

1. **Server mode in the app.** The guide is available on a phone connected to a server. The
   example home stays **local to the phone and is never sent to the server**; it is the only
   local home allowed beside the server's homes, and it goes with one gesture.
2. **Deleting in one gesture.** One press of **Delete the example home** and one confirmation on
   the same page, **no name to type**.
3. **The instance backup.** The example home is **left out of the backups of the server**. The
   first draft proposed to leave it in the copy of the database; this is replaced. The single
   point of passage is `backup_instance` (its `dump_database` and its walk of the media), and
   the tests that guarantee it are `test_the_instance_backup_leaves_out_the_example_home` and
   `test_a_restore_never_creates_an_example_home`.
4. **Scope of the mark.** A column **`Home.is_example`** (boolean, default false).
5. **Catching up.** "Add what the earlier scenarios make" is **kept**, so that a person who comes
   for the budget is not blocked by the rooms.
6. **The word "site".** "The site" is the web application, and the documentation reads the same
   file as a third reader (one Sphinx directive, no translation).
7. **A tick is stored as soon as it is true.** The guide never goes backwards, even if the
   action is undone afterwards (`StepTick`, [how a step ticks](#how-a-step-ticks-by-itself)).

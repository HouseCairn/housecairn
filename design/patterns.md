# Screen patterns

The anatomy of the screens that the web application and the mobile app
([HouseCairn/housecairn-mobile](https://github.com/HouseCairn/housecairn-mobile)) share: a
record page, a row of label and value, a header, a form, a filter, an empty, loading or error
state, and More. It is the third reference of the look, next to the tokens and the catalog:

| What | Source of truth | The app follows it by |
| --- | --- | --- |
| Colours, type, spacing, radii, sizes | [`design/tokens.json`](tokens.json) | a copy, checked value for value (`tool/web_alignment.py` of the app) |
| Words | the catalogs of the web (`src/housecairn/*/locale/`), and the [glossary](#glossary) below for the shared terms | a copy of [`design/glossary.json`](glossary.json), checked against its ARB files (`test/glossary_test.dart` of the app) |
| Anatomy of a screen | this document | its [ADR 0011](https://github.com/HouseCairn/housecairn-mobile/blob/main/docs/adr/0011-design-from-the-web-app.md), which points here |

The tokens and the words of the web stay the source of truth (decision of the maintainer,
2026-11-15). The [design guide](../docs/developer/design-guide.md) keeps the rules of the web
alone (navigation, return rule, actions in place, addresses); this document holds what both
must draw the same way. A rule here applies to both, except where the
[platform adaptations](#platform-adaptations) list says otherwise, one control at a time.

Why it exists: a design audit of 2026-11-15 compared every screen of the web at 390 px with
the same screen of the app, in French and English. The atoms were identical (tokens, Manrope,
type scale, pills, cards), measured by a test of the app; the screens still read differently,
because no text fixed the anatomy of a page, a form or a list, and each lot had decided it
alone, on each side. Four patterns had drifted: the task page, the figures, the header and the
filters.

**Must** and **must not** are requirements, as in the design guide. A screen that departs from
them is a bug, not a precedent. Where one side does not follow a rule yet, the
[gaps](#gaps-at-the-time-of-writing) table names the lot that fixes it.

## Record page and its actions

The page of one record (a task, a room, an item, a stock item, a project) opens **to read**.
Its form is behind **Edit**.

- A record page **must** open read-only, never as an editable form. Reading is what a page is
  opened for nine times out of ten; a form opened by default invites a change by mistake, and
  its Save button competes with the actions of the record.
- From the top, in this order: the state pills (state, repetition, priority when high), the
  title (`h1`), the place (Room · Item, each a link), the **action row**, then the sections of
  the record.
- The action row **must** hold one primary action at most, then secondary ones, in one line
  that wraps; never buttons stacked to the full width. Primary: 48 px high; secondary: 44 px.
- The task page has the order of the web: **Done** (primary), **Postpone**, **Skip this
  time**, **Edit the task**; then *When*, *How to do it*, *Details*, *Cost*, *Materials*,
  *Photos and files*, *History*. The app takes the same page, the same actions in the same
  order, and its form opens from Edit (decision of the maintainer, 2026-11-15).
- Actions of context (Report a problem, a QR label) are secondary buttons of the action row,
  the same on both sides; they are not hidden in a menu.
- The order of the sections of the other record pages is the web's. A change of that order is
  made on the web first, in this document, then in the app.
- An action that cannot be taken back (delete) **must** be at the bottom of the page, or of its
  form, never in the action row.

```text
[Overdue] [Every 3 months]                  pills
Descale the espresso machine                h1 24/800
Kitchen · Espresso machine                  place, links
[ Done ] [ Postpone ] [ Skip this time ] [ Edit the task ]
WHEN                                        group heading 13/700, capitals
  Due                         21 Nov 2026   label-value rows
  Repeat                    Every 3 months
HOW TO DO IT
  …
```

## Label and value, and figures

- A short value of a record (a date, an amount, a quantity, a brand, a serial number) **must**
  be a **label-value row**: the label on the left (14/400, muted text), the value on the right
  (16/700, aligned to the right), a line of `border` between rows, 44 px high at least. The web
  draws it in the tables read row by row (`table-stack`) and in the summaries; the app with one
  widget for every screen.
- The value goes under its label only when it does not fit: on the web, when the width runs
  out; in the app, from a text size of 1.3. Never by default: a screen of values stacked under
  their labels is twice as long and is not read at a glance.
- A long text (notes, the goal of a project, an address) is the only value shown under its
  label, as a paragraph.
- An **amount** **must** go through the one formatter of the language of the reader, with the
  currency of the home: `1 250,50 €` in French, `€1,250.50` in English (web: the `money` filter,
  ADR 0054; app: the CLDR formatter). Never written by hand.
- A **key figure** (the quantity of a stock item, what is left of an envelope) is the figure in
  32/800, its unit next to it in muted text, and its state pill on the right (Low stock,
  Over budget).
- A **progress bar** (an envelope, a budget line) is 8 px high, fully rounded, primary on
  `surface-muted`, `warning` beyond 100 %, with its percentage in text on the right. A bar
  without its figure says nothing to a screen reader.

## Header: title, back, actions

- A section root (Today, Rooms, Projects, Budget, More) has no back link. Every other page has
  one that names its target, `‹ Rooms`, never "‹ Back" alone (design guide, Return rule).
- The page title **must** be the `h1` (24/800) at the top of the content, under the back link,
  never truncated: it wraps. A title in a bar shared with actions is cut ("Report a pro…").
- The bar at the top holds the back link and, on the right, one action at most. On the web
  that is the back link above the `h1`; in the app, the compact navigation bar (back on the
  left, the action on the right, no title while the `h1` is visible).
- The action of the bar, when there is one, is **Edit** on a record page, or the save of a form
  presented as a sheet ([platform adaptations](#platform-adaptations)). Any other action is in
  the action row.
- The selector of the current home, from two homes on, is a compact line under the `h1` of a
  section root; it is not shown with one home.

## Forms, in sections

- A label **must** be above its field (14/700, muted), with no colon. Only the title of a task
  is required; a required field says so in its label.
- Fields **must** be in the order of the web. For a task: Title, Room, Item, Due date, then
  Repeat. The place comes before the date.
- What is optional **must** be in folded sections (the `details` of the web: a triangle, a title
  16/700), open when one of their fields has a value or an error: for a task, *Repeat*, *Who*,
  *Duration and budget*, *Materials*.
- What the fields amount to is said in one sentence under them, in a `surface-muted` box (for a
  repetition: "Next time: 3 months after the last time").
- A form **must** end with its primary button (**Save**, or the verb of the form: **Send the
  report**) and **Cancel**, which goes back without saving. The label of the button is the
  web's, word for word, on both sides.
- A date **must** be a field that looks like a field (a frame, 48 px high), with the date in the
  format of the language and a calendar icon; it opens the date picker of the platform. A list of
  choices looks like a field too, and opens the picker of the platform.
- A refused form says why at the top, then under each field (see
  [Error](#empty-loading-and-error-states)).

## Filters and search

- A filter of state (Open, Closed, All, Mine; In progress, Ideas, Finished) **must** be the one
  component of **segmented tabs**: underlined in sand (2 px), 48 px high, label 14/700. Not
  pills, not a list with a Filter button. The same need drawn three ways reads as three
  features.
- Every target of a filter **must** be 44 px at least (`--hc-layout-touch-target`), on both
  sides.
- A **pill** is a label of state, never a control: it is not tapped.
- More than one criterion at a time (a room, a person, a state) goes in a sheet **Filters (2)**,
  the count being the criteria applied, opened by a secondary button next to the search field.
- A **search** field **must** have its magnifier inside, search as the person types, and no
  Search button.
- A filter reads like the noun it filters, in the plural (the reports: *Classés*, *Tous*,
  *Les miens*); the label of a state on one record is singular (*Ouvert*). When one English
  word needs both, the web catalog gives it a `msgctxt`.

## Empty, loading and error states

- **Empty**: a list with nothing in it **must** show a dashed frame with one sentence, "No … yet."
  (*Aucun … pour l'instant.*), and the action that fills it under the frame. Never an empty
  grey tile, never nothing.
- **Loading**: the web draws its pages on the server, and an action in place (HTMX) keeps the
  page and disables its own button until the answer. The app reads its own database: a screen
  that waits shows the progress indicator of the platform in place of its content, never a blank
  screen, and never a skeleton that looks like data.
- **Error of a form**: a summary at the top, "There are N errors:", then one line per error led
  by the label of its field, announced to screen readers and focused; the message stays under
  the field too. A folded section that holds a field in error opens. Web: one component,
  `components/form_errors.html` (design guide, Screen rules already decided); app: the same
  order and the same words.
- **Error of a request** (the instance cannot be reached, a sync fails): a banner at the top of
  the content says what failed in one sentence and offers **Try again**; what the person typed
  is kept. Never a dialog that blocks the screen for an error the person cannot fix.
- **Contrast**: text and the outline of a control **must** reach WCAG 2.2 AA (4.5:1 for text,
  3:1 for an outline). The card border (`#E3DED6`, 1.34:1) and sage (`#8FA39A`, 2.67:1) are
  for decoration only: never text, never the outline of a control.

## More

More holds groups, in the order of the web: **Home**, **Me**, **Instance** (administrators
only, web) and **Help**. The app shows the same groups, in the same order, with the same
entries where it has the feature, then **one group of its own, last** (decision of the
maintainer, 2026-11-15).

| Group | Web (`housecairn/core/more.py`) | App |
| --- | --- | --- |
| Home | Home settings · Household and roles · Reports (count) · Contractors · QR labels · My homes | the same entries it has, in this order: Reports (count) · Contractors · My homes |
| Me | Profile · Notifications · Display preferences · Calendar in another application · API tokens · Security and sign-in | the same entries it has, in this order: Notifications (the screen of the reminders, which holds their switch), then Language and Display preferences when the app has them |
| Instance | Instance administration (administrators only) | none: the instance is administered on the web |
| Help | Help · API documentation · About | About; Help once the app's help is decided |
| How you use Housecairn | none | the group of the app, last: the way the app is used, and the two others |

- An entry **must** have its icon (from the icon set) and its subtitle, on both sides: the
  subtitles are those of `more.py`.
- *Foyer* / *Household* names the members of a home and their roles only, never a group: the
  group is *Logement* / *Home*.
- An entry of the app that the web does not have goes in the app's own group, never in Home,
  Me or Help: the two people of a household, one on the web and one on the phone, must find a
  setting at the same place.
- An action of a list (Add from a template) is not an entry of More: it is on the pages it
  applies to.

## Glossary

One term per concept, in both languages. The source is [`design/glossary.json`](glossary.json);
`tests/test_design_glossary.py` checks that each term is what the web catalogs say, and that
this table lists every term. The app keeps a copy of the file and checks its ARB files against
it. A new shared term is added to the file and to this table in the same pull request.

A mistake of a web catalog found later is kept in the file as `web_fault` until it is fixed:
the test expects it, and fails once the catalog is fixed, so that the fix also removes
`web_fault`. The two found by the audit (**Toutes** for the filter of the reports, **Peinture
restant en stock**) were fixed by lot D6; the filters of the reports carry the `msgctxt`
`reports filter`, so that their plural (*Ouverts*, *Classés*, *Tous*) does not collide with the
state of one report (*Ouvert*).

| Term | English | French | Concept |
| --- | --- | --- | --- |
| `today` | Today | Aujourd'hui | Section: what to do, and when |
| `rooms` | Rooms | Pièces | Section: where, and with what |
| `projects` | Projects | Projets | Section: the works |
| `budget` | Budget | Budget | Section: for how much |
| `more` | More | Plus | Section: everything else |
| `home` | Home | Logement | Group of More about the home being viewed |
| `me` | Me | Moi | Group of More about the person |
| `help` | Help | Aide | Group of More: help and information |
| `about` | About | À propos | Entry of Help |
| `home-settings` | Home settings | Réglages du logement | Entry of Home: name, currency, export |
| `household` | Household and roles | Foyer et rôles | The members of a home and their roles; never a group of More |
| `my-homes` | My homes | Mes logements | The list of the person's homes |
| `contractors` | Contractors | Intervenants | People and companies who work on the home |
| `notifications` | Notifications | Notifications | Entry of Me: where and when reminders go |
| `display-preferences` | Display preferences | Préférences d'affichage | Entry of Me |
| `language` | Language | Langue | Language of the person |
| `room` | Room | Pièce | A room of the home |
| `item` | Item | Élément | Something in a room (its kind is Equipment or Surface) |
| `equipment` | Equipment | Équipement | Kind of item |
| `stock` | Stock | Matériel | Tab of Rooms: what is kept in the home |
| `stock-item` | Stock item | Article | One article of the stock |
| `materials` | Materials | Fournitures | What a task or a project needs |
| `suppliers` | Suppliers | Fournisseurs | Where a stock item is bought |
| `paint-left` | Paint left in stock | Peinture restante en stock | Field of the memory of a room |
| `upkeep` | Upkeep | Entretien | Recurring maintenance (tab of Budget) |
| `done` | Done | Fait | Button: the task is done |
| `done-filter` | Done | Faites | Filter of All: finished tasks |
| `postpone` | Postpone | Reporter | Button: move the task to a later date |
| `skip` | Skip this time | Ignorer cette fois | Button: not this time |
| `undo` | Undo | Annuler | Action of the undo bar |
| `edit` | Edit | Modifier | Opens the form of a record |
| `save` | Save | Enregistrer | Sends a form |
| `cancel` | Cancel | Annuler | Leaves a form without saving |
| `delete` | Delete | Supprimer | Removes a record, after a confirmation |
| `overdue` | Overdue | En retard | Group of Today |
| `coming-up` | Coming up | À venir | Group of Today |
| `no-date` | No date | Sans date | Group: tasks without a due date |
| `in-season` | In season | De saison | Group of Today: undated tasks of the season |
| `repeat` | Repeat | Répétition | Section of the task form |
| `season` | Season | Saison | Field of a task |
| `priority` | Priority | Priorité | Field of a task or a project |
| `add-task` | Add a task | Ajouter une tâche | Add button |
| `add-room` | Add a room | Ajouter une pièce | Add button |
| `add-item` | Add an item | Ajouter un élément | Add button |
| `add-photo` | Add a photo | Ajouter une photo | Add button |
| `reports` | Reports | Signalements | Problems reported in a home |
| `report-a-problem` | Report a problem | Signaler un problème | Opens the report form |
| `reports-closed` | Closed | Classés | Filter of the reports |
| `reports-all` | All | Tous | Filter of the reports: every report (masculine) |
| `reports-mine` | Mine | Les miens | Filter of the reports |
| `what-is-wrong` | What is wrong? | Qu'est-ce qui ne va pas ? | First field of the report form |
| `where` | Where? | Où ? | Place field of the report form |
| `send-report` | Send the report | Envoyer le signalement | Sends the report form |
| `envelope` | Envelope | Enveloppe | Amount set aside for the year |
| `spent` | Spent | Dépensé | Real spending |
| `amount-planned` | Planned | Prévu | Amount: what is planned |
| `committed` | Committed | Engagé | Amount: what is signed |
| `paid` | Paid | Payé | Amount: what is paid |
| `in-progress` | In progress | En cours | Tab of Projects |
| `ideas` | Ideas | Idées | Tab of Projects |
| `finished-tab` | Finished | Terminés | Tab of Projects |
| `quotes` | Quotes | Devis | Quotes of a project |

## Platform adaptations

On an iPhone the app uses the native controls of iOS; on Android it keeps Material (decision of
the maintainer, 2026-11-15). The web, opened in Safari on the same iPhone, already shows the
pickers of iOS: an app drawing Material controls there looked less like an iPhone than the
web. The adaptations below are the only ones allowed, each with its reason, as the table of
[ADR 0011 of the app](https://github.com/HouseCairn/housecairn-mobile/blob/main/docs/adr/0011-design-from-the-web-app.md)
does for the phone. A control not listed here is drawn as the web draws it, on both
platforms.

What never changes with the platform: the tokens (colours, Manrope, type scale, radii), the
cards, rows, pills, buttons, segmented tabs and the bar of the five sections, the words, the
order of a page, and the targets of 44 px at least. They are the look of Housecairn, not of a
platform.

| # | Control | Web | iPhone (app) | Android (app) | Why |
| --- | --- | --- | --- | --- | --- |
| 1 | Choice in a list | `select`: the browser's picker (on Safari, the wheel of iOS) | the field opens a menu of iOS, or a wheel picker in a sheet for a long list | Material menu | The picker people know on their phone; Safari already shows it |
| 2 | Date | `input type=date`: the browser's picker | the field opens the date picker of iOS in a sheet, with Done | Material date picker | The same reason; the field itself stays the field of the [forms](#forms-in-sections) |
| 3 | Confirmation and alert | a confirmation page (delete) | alert of iOS, the destroying action in the danger colour and Cancel in bold | Material dialog | The dialog of the platform; the web keeps its page (design guide, After a deletion) |
| 4 | Switch | a check box | switch of iOS | Material switch | A switch drawn by another platform reads as foreign |
| 5 | Back | the back link `‹ Rooms` | chevron and the title of the previous page in the bar, and the swipe from the edge | back arrow and the system back | What each platform's users do without looking |
| 6 | Title on scroll | the `h1` scrolls away with the page | the `h1` of the content folds into the bar once scrolled (large title of iOS) | the `h1` scrolls away, as on the web | The large title is the form iOS gives to the same rule: the title in the content |
| 7 | Form | a page, with Save and Cancel at the bottom | a sheet: Cancel on the left of its bar, Save (the web's label) on the right | a page: Save in the bar, back for Cancel | A form opened from a record is a modal sheet on iOS; the labels stay the web's |
| 8 | Menu of a row (`…`) | none on the web today | action sheet of iOS | Material menu | The menu of the platform; the row and its Done button stay the same |
| 9 | Progress | none (pages drawn on the server) | activity indicator of iOS | Material circular indicator | The indicator of the platform; see [Loading](#empty-loading-and-error-states) |
| 10 | Search field | field with a magnifier | search field of iOS (magnifier, clear button, Cancel) | Material field with a magnifier | The search field of the platform; the rule (search as you type, no button) stays |
| 11 | Reorder a list | Change the order, with up and down buttons | Edit on the list, then drag handles | drag handles after a long press | The edit mode of iOS lists |
| 12 | Choose several rows (compare projects) | check boxes in the rows | Select in the bar, then check marks | long press, then check marks | The selection mode of each platform |
| 13 | Large text in the bar of the sections | the labels grow with the browser | the labels keep their size; a long press shows the label large (Large Content Viewer) | the labels keep their size; a long press shows it in a tooltip | Five labels side by side at 360 px; the platform's own help for large text |

A new adaptation is added to this table first, in a pull request of this repository, with its
reason; then the app follows it. An app screen that draws a control differently from the web
without a row here is a bug.

## Gaps at the time of writing

What the audit of 2026-11-15 found that does not follow this document yet, with the lot that
fixes it. A lot that closes a gap removes its row.

| Pattern | Web | App | Lot |
| --- | --- | --- | --- |
| Record page and actions | follows it | a task opens as a form, its actions are not on the page | D2 |
| Label and value, figures | follows it | values stacked under their labels (budget, project, stock item, item) | D3 |
| Forms | follows it | dates as text and a button, Material pickers on iOS | D5 |
| Filters and search | follows it | no search on rooms and projects, no sheet "Filters" | D13 |

## The check of the app's labels

The app checks its words against the glossary in its own test, `test/glossary_test.dart`: for
each ARB key that shows a term of the glossary, the English and the French of the app must be
those of the glossary. A key that is known to differ, until its lot, is listed in the test with
the lot, and the test fails once it agrees, so that the list only shrinks. The app reads its
copy of `design/glossary.json`; while the glossary is not in a release of the web, the copy is
made by hand, and it becomes an element of `tool/web_alignment.py` (byte for byte, like the
task templates) with the first release that carries it.

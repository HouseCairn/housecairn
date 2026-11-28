# Release notes

The notes of each version are also published with it on the project's
[releases page](https://github.com/HouseCairn/housecairn/releases).

## Unreleased

### New

```{release-fragments} new
```

### Fixed

```{release-fragments} fixed
```

### Upgrade notes

```{release-fragments} upgrade
```

## 0.6.0

HouseCairn is still alpha software. This version counts in the real cost of a done task the stock it used, at the unit prices of the day it was taken, and, when no amount is typed, what the task planned; the mobile app receives these values, so that it can show the same real cost. It also limits the open reports of read-only members only, restores the archives of 0.1.0 to 0.3.2 again, offers eleven more notification services and carries Debian security fixes in the image. Read the upgrade notes before updating: three migrations run, which change the real cost of the tasks already done, and going back to 0.5.2 needs the backup made before.

### New

- **Eleven more notification services.** With Apprise 2.2.0, the **Notifications** screen offers CM.com, FireHydrant, Groundmail, Gupshup, Infobip, KakaoTalk, Kaleyra, SMSTunnel, Telesign, Whapi.Cloud and xMatters, each with an example address, a link to its page in the Apprise documentation and where its messages go, like the other services. Nostr is not offered: it publishes public notes over WebSocket, not over HTTP(S) or SMTP, so it does not fit the criteria ([Notifications](guides/notifications.md), [ADR 0064](adr/0064-every-apprise-service-with-a-fixed-format.md)).

- **The real cost counts the stock used.** When a task is marked done, the stock it takes now counts in its real cost, each item at its unit price on the day it is taken: two stock items worth 151.00 make a real cost of 151.00 without typing anything. The amount you type is added to it, for the rest (labour, purchases outside the stock): with 40.00 typed, 191.00. A price changed later does not change what was spent, an undo takes the value of the stock out again, and an item without a unit price counts for nothing, as the screens say. The task page, the history, the budget (by nature, the stock is materials), the CSV files and the API follow the same rule. Projects keep their money in their budget lines, as before ([ADR 0090](adr/0090-real-cost-counts-the-stock-used.md), [Task](screens/task.md)).

- **The mobile app receives what the real cost of a done task counts.** The sync API now sends the unit price each stock draw was taken at and the labour and materials a task planned when it was marked done, so that the app can show the same real cost as the web: the stock used plus the amount typed, or with nothing typed what was planned. The server alone sets these values: the app reads them and a change of one from the app is refused. At the update, the values of the tasks done before are sent once to the apps already in step, which read them with their next changes. An app of before ignores them and keeps showing the amount typed only ([ADR 0092](adr/0092-the-phone-reads-the-real-cost-the-server-keeps.md), [Sync API](api/sync.md#the-real-cost)).

- **A task done with nothing typed has paid what it planned.** A task with 120.00 of labour planned, marked done without typing a cost, now has a real cost of 120.00, under labour in the budget, instead of a task done without a cost. The material budget counts the same way, under materials, unless the stock items of the task work the materials out: the stock is then counted once, at its unit prices. With stock worth 151.00 and 80.00 of labour, the task costs 231.00. The plan is kept when the task is marked done, so a budget changed later does not change it, and an undo takes it out. An amount typed in **Details** replaces it, 0 included, and the form shows what is counted. The task page, the history, the budget, the CSV files and the API follow the same rule; a task of a project keeps its money in the budget lines of the project ([ADR 0091](adr/0091-a-task-done-with-nothing-typed-pays-its-plan.md), [Task](screens/task.md)).

### Fixed

- **Container image security fixes.** The image carries the Debian security fixes of glibc (`libc6` 2.41-12+deb13u5), zstd (`libzstd1` 1.5.7+dfsg-1+deb13u1) and shadow (`passwd` 1:4.17.4-2+deb13u1), on amd64 and arm64. Update to the new image to get them.

- **Report limit.** The limit of 20 reports now applies to read-only members only (a tenant, a tradesman), and only the reports still open count: the owner and the members who may write can report as much as they need, and a report made into a task, resolved or closed frees a place.

- **Archives of earlier releases.** An archive made by 0.1.0 to 0.3.2 that holds an item was refused ("items, object 1, ean"): it restores again. A field added since an archive was made now takes, when the archive lacks it, the value the update gives the data of an instance, and the JSON Schema of version 4 no longer requires those fields, so that an archive of 0.4.0 to 0.5.2 meets it again ([Archives of earlier releases](administration/data.md#archives-of-earlier-releases)).

### Upgrade notes

- **Three migrations.** `tasks.0013` adds the unit price each stock draw was taken at, `tasks.0014` the labour and materials a task planned when it was marked done, and `sync.0006` sends these values once to the phones already in step (below). The first two fill the tasks done before this version by the two approximations that follow; the values are set once, at the update, and a price or a budget changed afterwards no longer changes them. No setting, no environment variable and no dependency is new, and the export format stays version 4: an archive of 0.6.0 has three more fields, `unit_price` in `stock_draws` and `planned_labour` and `planned_material` in `occurrences` ([Export and deletion](administration/data.md)).

- **The tasks done before this version count today's budgets.** Nothing kept the budget of a task on the day it was done until now, so the migration gives every time a task was done before this version the labour and material budgets its task has at the update (the material budget only when no stock item with a unit price works the materials out, and none for a task of a project). It counts only where no cost was typed: an approximation, which changes the real cost of those tasks, the History and the Budget of the past years. A task done for less, or for nothing, gets its real amount in **Details**, 0 included. An archive written before this version is restored the same way: its tasks done get the budgets their task has in the archive.

- **The stock taken before this version is valued at today's prices.** Nothing kept the unit price of the day the stock was taken until now, so the migration gives the stock taken by tasks done before this version the unit price each item has at the update: an approximation, which changes the real cost of those tasks, the History and the Budget of the past years. An item without a unit price counts for nothing. If you typed the price of the stock in the cost of a task done before, it is now counted twice: correct the amount in **Details**, where the field of a task that took stock is now **Other costs (labour, purchases outside the stock)**. An archive written before this version is restored the same way: its stock taken gets the unit price its item has in the archive.

- **The API, the CSV files and the phones get the new figures.** In the API, an occurrence adds `stock_cost`, `real_cost`, `planned_labour` and `planned_material`, and each line of its `stock` `unit_price` and `value`; `actual_cost` stays the amount typed, so a script that read it as the real cost should read `real_cost`. The `total_paid` of the budget now counts the stock used and the plan kept, given apart in `total_stock_used` and `total_from_plan`, and `missing_costs` counts only the times done with no amount typed, no plan kept and no stock of value ([API](api/index.md)). The `history` file of the CSV tables has `planned_labour` and `planned_material` after `actual_cost`, and `stock_cost` and `real_cost` after the fields: a spreadsheet that reads its columns by position must follow ([Tables for a spreadsheet](administration/data.md#tables-for-a-spreadsheet)). For the sync, `sync.0006` writes one change of the server for each draw with a price and each done occurrence with a plan: a phone in step reads them in its next page of changes, without starting again from the snapshot, and the version of the sync protocol does not change. The versions of the mobile app already released keep these fields apart, never send them back, and show the amount typed only ([Sync API](api/sync.md#the-real-cost)).

- **Archives of 0.1.0 to 0.3.2 restore again.** An archive of a home made by 0.1.0 to 0.3.2 restores again, and an archive made before 0.6.0 gets at the restore the same prices and plans as an instance updated in place: each draw the unit price its stock item has in the archive, each done task the budgets its task has in it ([Archives of earlier releases](administration/data.md#archives-of-earlier-releases), [ADR 0093](adr/0093-an-archive-of-before-takes-the-values-of-the-migrations.md)).

- **The image.** Python stays 3.13.18, on a newer build of its Debian trixie image, which carries the glibc fix (`libc6` and `libc-bin` 2.41-12+deb13u5); zstd (`libzstd1`) and shadow (`login.defs`, `passwd`) are upgraded from Debian's security source when the image is built, and the build fails if any of these packages is below its fixed version. In the image, Apprise moves from 2.1.0 to 2.2.0 and markdown-it-py from 4.2.0 to 4.3.0. The `postgres` service of `compose.yaml` moves from PostgreSQL 18.6 to 18.7, a minor release of the same major version, which reads the same data directory.

- **Going back to 0.5.2 needs the backup made before the update.** The three migrations write data that 0.5.2 cannot take back (their reverse does nothing), and if 0.5.2 ran on the updated database, the tasks done on it would keep no price and no plan once back on 0.6.0, since the migrations do not run again. [Back up](administration/backup.md) before updating, as each update asks, and to go back, restore that backup on 0.5.2 ([Update](administration/update.md)): what was done since the backup is lost, and the real cost of a done task is again the amount typed only.

## 0.5.2

HouseCairn is still alpha software. This version makes an archived home read only everywhere, on the screens, through the API and from Home Assistant, as the mobile app already kept it, and adds a **Materials** section to the page of an item, to link the stock items it uses. Read the upgrade notes before updating: no migration runs and no setting is new, but the homes already archived become read only.

### New

- **An archived home is read only.** Its pages still open, with the banner **Archived home: read only**, but nothing in it can be changed, by anyone, owners included, from the screens, the API, Home Assistant or the mobile app, until an owner brings it back with **Bring this home back**, in that banner or on the page of the home. It can still be exported or deleted, and you can still leave it, remove a member or revoke an invitation; inviting someone, changing a role, transferring ownership or requiring a sign-in provider waits until the home is back, and so does an invitation sent before. The mobile app already worked this way; nothing is deleted ([ADR 0089](adr/0089-an-archived-home-is-read-only.md), [Home](screens/home.md#archive-a-home)).

- **Materials on the page of an item.** The page of an item has a **Materials** section that lists the stock items that fit it, with the quantity in stock and the unit price when there is one. Members and owners can link stock items to it (**Link a stock item**), add a new stock item already linked (**New stock item**) or take a link away (**Remove**), without going through the stock item; read-only members see the list only. The link is the same as before, the **Compatible items** of the stock item, edited from either side ([Item](screens/item.md)).

### Upgrade notes

- **No migration and no new setting.** This version adds no migration, no setting and no dependency, and the export format stays version 4.

- **The homes already archived become read only.** A home archived before the update is read only as soon as 0.5.2 starts: its pages carry the banner and offer no change but the exceptions above, and the API answers a change to it with `403` and `"code": "home_archived"`, a report or an event sent by Home Assistant included ([API](api/index.md)). A script or an integration that still wrote to an archived home gets that refusal from now on: bring the home back first. An invitation to an archived home that was pending stays pending: its page says it will work again once the home is brought back, and it does, within its expiry ([Invitation](screens/invitation.md)).

- **A new code of the sync API, `home_archived`.** A push to an archived home rejects each operation with `home_archived` (a read-only member keeps `forbidden`), and the upload of a file to it answers `403` `home_archived`; only a `put` of the home that sets `archived_at` alone, by an owner, is taken, and brings the home back ([Sync API](api/sync.md#pushing-changes)). The versions of the mobile app already released do not know this code and keep such an operation aside, as they do any refusal they do not know; since they already keep an archived home read only, it can only be a change made on the phone before the home was archived elsewhere.

- **Going back to 0.5.1.** With no migration in this version, 0.5.1 starts on the same database and needs no restore; [back up](administration/backup.md) before updating all the same, as each update asks ([Update](administration/update.md)). Back on 0.5.1, an archived home can be changed again from the screens, the API and Home Assistant, and a pending invitation to it can be accepted. The links made from the **Materials** of an item stay, as the **Compatible items** of their stock items.

## 0.5.1

HouseCairn is still alpha software. This version adds an interactive guide on an example home that never leaves the instance, filters in tabs and search as you type on the lists, a shortcut to the iOS app beta and finishing touches on the screens. It also fixes the return to the mobile app after its sign-in, and gives the mobile app the photos of reports and of labels and, for a read-only member, only their own reports. Read the upgrade notes before updating: three migrations run, and no setting is new.

### New

- **Finishing touches of the screens.** Each entry of [More](screens/more.md) has its icon. The page of [task templates](screens/task-templates.md) is lists of rows under a heading per equipment, each row one box to tick, and it can be opened from a [room](screens/room.md), with that room already chosen. An [item](screens/item.md) with no photo shows a dashed frame under **Photos**, and the actions of a room and of an item are one row of buttons as wide as their text. The budget lines of a [project](screens/project.md) name the planned amount with the same word as the other budget screens (in French, *Prévu* everywhere instead of *Prévue* on this one). On the pages shown signed out, such as the sign-in page, each language is a button: tapping it applies it, and the **Change** button is gone.

- **Filters in tabs, search as you type, one target a row.** A filter of state is now a tab, with the current one marked: **Open**, **Closed**, **All** and **Mine** on the [reports](screens/reports.md), **To do** and **Done** on the [tasks](screens/tasks.md), **All** and **Low stock** on the [stock](screens/stock.md). The other criteria are folded under **Filters (2)**, a sheet that says how many apply, on the [projects](screens/projects.md), the tasks to do, the [history](screens/history.md) and the [equipment](screens/equipment.md). The search fields have a magnifier and no button: the list follows what you type, after a short pause, and the address keeps the words. The [rooms](screens/rooms.md) search shows its results in place. The year of the [budget](screens/budget.md) follows its choice, without **Show**, and the repeated **By room**, **By project** and **By task of the upkeep** links are gone from its summary. A row is one link over its whole width: the tasks of a [project](screens/project.md) no longer carry a **Take out** button (it is on the page of the task), a budget line opens its form from its name, and the rows of the budget tables open their room, project or task from anywhere. **Compare projects** turns the rows into boxes to tick. The two forms for a project's tasks are two buttons. Without JavaScript, everything still works: Enter sends a search, and a **Filter**, **Search** or **Show** button appears where a choice needs one.

- **Guide in the documentation.** [Guide](getting-started/guide.md), under Getting started, walks through five scenarios on a fictional example home: create a home, add rooms and equipment, plan a recurring task, report a problem, follow a project and its budget. Each step links to the reference page of its screen.

- **Interactive guide.** **More › Help › Guide** (also on the welcome page and on an empty
  Today) walks through five scenarios on an example home with fictional values: create a home,
  add rooms and equipment, plan a recurring task, report a problem, follow a project and its
  budget. Each step opens the real screen, where **Fill in for me** fills in a form with the
  example's values, and ticks itself once done, except a few that you tick with
  **Mark as done**. The example home carries a banner on each of its pages, never
  leaves the instance, and goes with **Delete the example home** and one confirmation
  ([Guide](screens/guide.md)).

- **Shortcut to the iOS app beta.** **More** (under **Help**) and [About](screens/about.md) have an **iOS app (TestFlight beta)** entry that opens the page to join the beta, in a new tab (iPhone, TestFlight required). It is also mentioned in [Install on a phone](getting-started/phone.md). Nothing is loaded from that site until you follow the link. See [More](screens/more.md).

### Fixed

- **Gauges drawn the same everywhere.** Every gauge of the [budget](screens/budget.md) screens, from the overview to the budgets by room, of a room, by project and of the upkeep, is now one bar of 8 px, in the colour of the application on its muted background, and in the warning colour once the spending goes beyond 100 %. The percentage stays in text, on the same line as the bar, instead of wrapping under it.

- **Mobile app sign-in.** After Connect or Cancel on the consent page, the browser now returns to the mobile app. The page's Content Security Policy (`form-action`) blocked the redirect to the app, so the browser stayed on the site, while Connect had already connected the phone and sent its security email. Only the consent page allows the app's scheme; the other pages keep their policy.

- **Photos of reports and label photos in the mobile app.** With a Housecairn server, the [sync API](api/sync.md#files) now serves and takes the photo of a report and the label photo of a stock article, as it does the photos of rooms and items: a photo added on the site reaches the phone, and one taken on the phone reaches the site, re-encoded without its metadata. A report or an article may still have no photo, and a phone can remove or replace it.

- **A read-only member's phone shows their own reports only.** With a Housecairn server, the [sync API](api/sync.md#the-changes) gave the phone of a read-only member every report of the home, with its photo, when the site shows them only the reports they made. It now gives them the same reports as the site. When an owner makes a member read-only, or a member again, their phone starts again from the server's data at its next sync, with the reports their new role shows; a phone that held the reports of others before this version drops them at its next sync too.

### Upgrade notes

- **Three migrations.** `homes.0008` adds the mark of an example home, `guide.0001` adds the table of the steps of the guide that are done, and `sync.0005` adds what each read-only member's phone may see: in each home that has a read-only member, it moves the sync position on by one, so that at their next sync the phones of these members start again from the server's data, with their own reports only. No setting and no dependency are new, and the export format stays version 4. Going back to 0.5.0 after the update needs the [backup](administration/backup.md) made before: 0.5.0 does not know the example home and would treat it as an ordinary home, and would again give a read-only member's phone every report ([Update](administration/update.md)).

- **Backups leave the example home out.** `backup_instance` removes the example homes, with their files, from the copy it archives, and says how many it left out; `restore_instance` erases a home that arrives marked as an example. With PostgreSQL, when the database holds an example home, the backup cleans a scratch database made from the dump: the database role then needs the right to create a database, or the backup stops and says so (delete the example home, or allow `CREATEDB`). See [Data and backups](administration/backup.md).

## 0.5.0

HouseCairn is still alpha software. This version lets the mobile app do more through the sync API: create on the server a home it made, upload and read the files of photos, attachments and project files, and know which member of a home is the connected account. It also shows the version of the application on the About screen and adds **Add an item** to the Equipment list. Read the upgrade notes before updating: one migration runs, and no setting is new.

### New

- **Version on the About screen.** The About screen (More, Help) shows the version of the application on its own line, for example `Version 0.5.0`, easy to select and copy when you report a problem ([About](screens/about.md)).

- **Sync API: homes made on the phone.** `POST /api/v1/sync/homes/` creates on the server a home the mobile app made, with the phone's id, the account of the connection its owner, and adds it to the homes of the connection ([Sync API](api/sync.md#creating-a-home)). It needs the scope `homes.create` and the right **May create homes** given when the phone was connected. Sent again with the same fields, it gives the same home; an id held by anything else is `409` `id_taken`, which does not say what holds it.

- **Sync API: who the connected account is.** Each home of `GET /api/v1/sync/homes/` now carries `member_id`, the `id` of the entry of `members` that is the account of the connection, so that the mobile app can keep the reminders that are its own. The field is added; nothing else changes.

- **Sync API: files of the phone.** The mobile app uploads the file of a photo, an attachment or a file of a project with `PUT /api/v1/sync/homes/{h}/files/{file_id}`, under an id it makes, and reads any file of the home with `GET` ([Sync API](api/sync.md#files)). The server checks and writes an image again as on the web, which drops its metadata, accepts the documents the web accepts, counts the file in the quota of the home and refuses one above the largest file of the instance, which `GET /api/v1/sync/info` now gives (`file_max_bytes`). A photo or an attachment pushed before its file waits for it; a file no row refers to within 7 days is removed by the nightly purge.

### Fixed

- **Add an item from the Equipment list.** The Equipment list (Rooms, Equipment) now ends with **Add an item**, which was missing: the item form opens with the room to choose, or already set when the list is filtered on a room. Read-only members do not see it ([Equipment](screens/equipment.md)).

### Upgrade notes

- **One migration.** `sync.0004` adds the table of the files a phone uploads before a row refers to them. No setting and no dependency are new, and the export format stays version 4. Going back to 0.4.1 after the update needs the [backup](administration/backup.md) made before ([Update](administration/update.md)).

- **Files of the phones, in the data folder.** A file the mobile app uploads waits under `sync/<home id>/` in `MEDIA_ROOT` (`/data/media/` in the image), which no URL serves, until a photo, an attachment or a project file refers to it. It counts in the quota of its home, goes with the home when the home is erased, and is in the backup with the other files. The nightly purge of the sync API, at 03:30, now also removes the files no row referred to within 7 days.

## 0.4.1

HouseCairn is still alpha software. This version lets phones connect with the HouseCairn mobile app: in 0.4.0 the server refused, for want of a CSRF cookie, the requests with which the app exchanges its sign-in code, renews its access and disconnects, so a phone could not finish connecting. Nothing else changes: no migration runs and no setting is new.

### Fixed

- **Mobile app sign-in.** The token and revocation endpoints (`/o/token/` and `/o/revoke_token/`) no longer answer 403 when the mobile app calls them without a CSRF cookie, which kept the app from exchanging its code, refreshing its access or disconnecting. The consent page keeps its CSRF check.

## 0.4.0

HouseCairn is still alpha software. This version opens the server to the HouseCairn mobile app: a phone connects with OAuth 2 and PKCE, sees only the homes it was given, and reads and pushes their changes field by field through a new sync API, checked against the rules the web applies. It also adds barcodes to the stock and the items, a list of every item of the home with its manufacturer's warranty, the archiving of a home and sheets saved as you go, and fixes a sign-in that could sign the person out at once. Read the upgrade notes before updating: eleven migrations of HouseCairn and those of a new dependency run, two settings are new, the export format becomes version 4, and going back to 0.3.2 needs the backup made before.

### New

- **Connected apps.** The HouseCairn mobile app can connect to this server: it opens the
  server's own sign-in page in the phone's browser (OAuth 2 with PKCE), where you choose the
  homes the phone sees and whether it may create homes. Each phone is listed under
  **Connected apps** on the [API tokens](screens/api-tokens.md#connected-apps) page,
  where you rename it, change its homes or revoke it; every new connection sends you an email.
  A connected phone opens only the sync API of the app, starting with
  `GET /api/v1/sync/info`, never the rest of the API.

- **Sync API, reading.** A connected mobile app reads the homes it was given, with their
  members, the changes of a home since its last position and the snapshot of a home, under
  `/api/v1/sync/` ([Sync API](api/sync.md)). An erased home is reported to the phones
  that had it, and a phone away longer than the 400 days changes are kept starts again from the
  snapshot.

- **Sync API, pushing.** A connected mobile app sends what was typed on the phone to a home it
  was given (`POST /api/v1/sync/homes/{id}/push`, [Sync API](api/sync.md)). Each field
  is decided on its own by its clock, so two people who edit two fields of one task both keep
  their change; the app learns which of its values lost, and keeps its long texts that lost as
  an other version. A push sent twice runs once, a read-only member writes nothing, and only an
  owner archives a home.

- **Sync API, rules across rows.** What a phone pushes is checked against the rules of the
  tasks, as the web applies them ([Sync API](api/sync.md)): a task never keeps two open
  occurrences, an undo taken on a phone while someone else postponed the next occurrence gives
  way to that edit, the next date of a task done on a phone is the one its repeat rule gives,
  and the stock follows a task marked done on a phone once. The home's activity log lists the
  tasks marked done or skipped from a phone, with the person who did it.

- **Same ids as the mobile app.** The occurrence that follows a closed one, the materials of a
  task, what a closing takes from the stock and the annual envelope of a year now get the ids
  the mobile app computes for them, so that the same row made on the web and on a phone is one
  row. Closing a task again after an undo brings back the occurrence the undo removed, and a
  restore as a copy computes these ids again. Ids stay UUIDs with no meaning for a client; the
  rows made before keep theirs.

- **Barcodes.** A stock item and an item of a room keep their barcode (EAN-8, EAN-13, UPC-A or GTIN-14, with its check digit), which you can type or scan with the camera, and you can search by it. If the administrator turns the lookup on (it is off by default), scanning a barcode can fill in the empty fields of the form from Open Products Facts, then Open Food Facts, and bring a photo of the product with its credit. See [Stock](guides/stock.md) and [Barcodes](administration/barcodes.md).

- **Every item of the home.** **Equipment**, a new tab of Rooms beside Stock, lists the items of
  every room, filtered by room and by text, with their warranty and their next task. See
  [Equipment](screens/equipment.md).

- **Manufacturer's warranty.** An item has a purchase date and a warranty apart from its
  maintenance contract: a duration in months or years, who gives it (the manufacturer or the
  shop, with its name) and its start, the purchase date by default; its end is worked out and
  shown. The end typed on an item before is kept. See
  [Rooms and items](guides/rooms-and-items.md).

- **Millilitre.** The units of a stock item include the millilitre (mL).

- **One line of materials.** The task form opens with one empty line of materials;
  **+ Add a line** adds the next ones.

- **Materials from the stock.** A task whose stock items have a unit price has its materials
  calculated from them, each time and over the year, with no material budget to type; a material
  budget typed before is kept but no longer added to them, so such a task plans less than before.
  Its page puts what is planned apart from what was really paid. See
  [Budget](guides/budget.md).

- **Saved as you go.** The sheets of an item, a room, a task and a stock item save each field as
  it changes, and say **Saved**; **Save** stays for a browser without script.

- **Archive a home.** An owner can archive a home that is no longer in use, from its page, and bring it back. An archived home is listed apart on My homes, leaves Today, the calendar and the home selector, sends no reminder, and its calendar feeds stay valid but empty. Nothing is deleted: it can still be opened, changed and exported. See [Home](screens/home.md).

- **Two worked examples.** The demo home now holds the descaling of an espresso machine, with its water filter taken from the stock at each Done and its cost a year, and a bathroom project with two quotes compared, one accepted on a budget line, inspiration links and the envelope of the year. The [Examples](guides/examples.md) page follows both, step by step.

- **Links on About.** The About screen shows a row of icons that lead to the project site, the documentation, the application code, the Home Assistant app and integration, and the mobile application. The iOS and Android icons are marked **Coming soon** and are not links until the apps are published. See [About](screens/about.md).

### Fixed

- **Signed out right after signing in.** On a slow server, signing in with the password could
  land back on the sign-in page, under "Successfully signed in": a request the sign-in page had
  started by itself (the passkey offered in the address field, or the offline pages the
  installable application keeps) answered after the sign-in with the session of before. The
  passkey request is now cancelled when the form is sent, and the offline pages are kept
  without the session.

- **Connecting a phone.** A link to the consent page with `approval_prompt=auto` showed an error
  page; it now shows the consent page, which is never skipped. The email that says a phone was
  connected is sent only once the server has given the phone its code.

- **No new calendar feed for an archived home.** Creating a feed of an archived home is now
  refused, from the page as from the API, as the page already offered none; a feed it has keeps
  working as before. See [Calendar and feeds](guides/calendar.md).

- **Pages on a phone.** Below the five sections, a page now starts with its back link and its
  title, without the brand bar, and the button that saves a form stays at the bottom of the
  screen while the form scrolls.

- **Words.** Labels of the forms no longer end with a colon. In French, the filters of the
  reports read *Ouverts*, *Classés* and *Tous*, a planned project is *Prévu*, and the memory of a
  room says *Peinture restante en stock*.

### Upgrade notes

- **Eleven migrations, and a new dependency.** This version adds django-oauth-toolkit (and
  jwcrypto, which it pulls in) to the image. At start-up its own migrations run, then those of
  HouseCairn: `apitokens.0003` (the connected apps) and `apitokens.0004` (it creates the one
  client of the mobile app), `sync.0001` (the change log, and the identifier the server writes
  with, made once and part of the database, so a backup carries it), `sync.0002` (the pushes
  already answered), `sync.0003` (the connected apps of an erased home, moved to a table of
  their own), `homes.0007` (the date a home was archived), `rooms.0006` (the purchase date and
  the warranty of an item), `rooms.0007` and `stock.0007` (the barcode, and the credit of a
  photo), `stock.0006` (the millilitre) and `barcodes.0001` (the answers of the lookup).

- **Two new settings, also in the interface.** **Mobile app**
  (`HOUSECAIRN_MOBILE_APP_ENABLED`), on by default, lets people connect the mobile app; off, no
  phone can connect or sync ([Settings](administration/settings.md#mobile-app)). **Online
  lookup of barcodes** (`HOUSECAIRN_BARCODE_LOOKUP_ONLINE`), off by default, asks Open Products
  Facts, then Open Food Facts, what a scanned code is, and sends them the code only
  ([Barcodes](administration/barcodes.md)). The Coolify file and
  `deploy/production.env.example` name both: left empty, a variable gives the default and leaves
  the field free in the interface ([variables](administration/variables.md)).

- **The change log of the mobile app, and its nightly purge.** A new `sync` application records
  every write of a home, a room, an item, a photo, the stock, a report, a task and a project,
  from the web, the API, a CSV import, a restore or the background reading of a link, field by
  field and only for what changed; the sync API reads it. A nightly task at 03:30, in the time
  zone of the instance, removes what is older than 400 days, with the files of the photos it
  deletes. Taking back a "Done" now keeps the next occurrence as a deleted row, so that a phone
  learns of it, and deleting an account takes it off the tasks of the homes that stay. When a
  home is deleted, the instance keeps its identifier, the time it was deleted and the connected
  apps that were linked to it. Restoring an archive whose "updated at" date of the home or of an
  object is more than a day ahead of the clock of the instance is now refused
  ([Data model of the synchronization](developer/sync-data-model.md)).

- **Export format 4, and `archived_at` in the API.** The archive of a home now carries the date the home was archived (`archived_at` in `data/homes.json`), so its export format is version 4; archives of versions 2 and 3 are still restored, as homes that are not archived. In the API, a home has a read-only `archived_at` (`null` when it is not archived), and `GET /api/v1/homes/` still lists archived homes: nothing changes for an integration. `POST /api/v1/homes/<id>/archive/` and `unarchive/` are new, for owners signed in. See [Export and deletion](administration/data.md).

- **Behind a reverse proxy.** The mobile app needs HTTPS, or plain HTTP between local addresses
  only, and the proxy must pass the original scheme and `Host`. A trusted proxy that sends no
  `X-Forwarded-For` leaves its own address as the client's
  ([Reverse proxy](administration/reverse-proxy.md#the-mobile-app)).

- **The camera, behind a proxy.** The `Permissions-Policy` header of HouseCairn now allows the
  camera to the pages of the instance, `camera=(self)` instead of `camera=()`, for the **Scan**
  button of a barcode field; microphone, location, payment and USB stay off. A proxy that sets
  its own `Permissions-Policy` must allow `camera=(self)` too, or the button only answers "No
  camera could be used. Type the digits under the bars." The browser gives the camera only to a
  page served over HTTPS or from `localhost`: over plain HTTP the button is not shown, and the
  code is typed ([Reverse proxy](administration/reverse-proxy.md)).

- **Going back to 0.3.2 needs the backup.** The 0.3.2 image does not undo these migrations, and
  on the database they changed it can no longer create a stock item, an item or a photo (their
  new columns have no default in the database) nor restore an archive of format 4. To go back,
  restore the [backup](administration/backup.md) made before the update: what was done since is
  lost ([Update](administration/update.md)).

## 0.3.2

HouseCairn is still alpha software. This version makes forms work again in the Home Assistant app, where every form got "Session expired": the app takes the fix with its own version 0.3.2, which runs this image. It also counts every planned amount of a task in the budget, applies the language a person chooses everywhere, shows the photo of a report on the task made from it, and offers on Today to receive the reminders on the device. Read the upgrade notes before updating: the notification settings lose their `language` in the API, three migrations run, and no setting is new.

### New

- **Reminders on your phone in one tap.** When the instance sends notifications to devices,
  Today now offers to receive the reminders on the device you are using: **Turn on** asks the
  browser for its permission and adds the device with a name of its own (*Chrome on Android*,
  *iPhone*), **Later** asks again in seven days, **Do not ask again** stops in this browser.
  The installed application shows it at its first opening; in Safari on an iPhone it says to
  install the application first. Where the browser blocks notifications,
  [Notifications](screens/notifications.md) says how to allow them again
  ([On this device](guides/notifications.md#on-this-device)).

### Fixed

- **Every planned amount of a task is in the budget.** An open task with no date now counts in
  the plan of the current year, and a project plans the estimates of its tasks beyond its
  budget lines, nature by nature, never twice: a project with estimated tasks and no line no
  longer plans zero. **Budget by project** shows what the tasks of each project plan
  ([A task in a project](guides/budget.md#a-task-in-a-project)).

- **Your language, everywhere.** The language chosen in the
  [display preferences](screens/display-preferences.md) now applies to reminders,
  notifications on your devices and calendar feeds (the notification settings no longer have
  a language of their own; a language chosen there becomes your language if you had none),
  to the API with a token, to the message of the change and of the sign-in, and to the pages
  kept for reading offline. It is reached from **Profile** too, and the signed-out pages have
  a language switch ([Language](guides/language.md)).

- **Forms work again behind a proxy that replaces the referrer policy.** Through the Home
  Assistant app, every form, the first-account screen included, got "Session expired": Home
  Assistant gives its pages `Referrer-Policy: no-referrer`, and the browser then posts forms
  with `Origin: null`. Each page now states HouseCairn's policy itself
  ([Reverse proxy](administration/reverse-proxy.md)).

- **The photo of a report is now on the task made from it.** The page of the task shows the
  report it comes from (who reported, when, the text and the photo, which opens full size);
  the task list and Today mark a task whose report has a photo; the API lists the reports of
  a task in `reports` ([Task](screens/task.md)).

### Upgrade notes

- **Migrations, and no new setting.** The update runs three migrations:
  `tasks.0012` copies the language of the notification settings into the display
  preferences of each person who had chosen no display language, `notifications.0004` then
  removes the language from the notification settings, and `reports.0002` changes no data
  (the name under which a task finds its reports). No setting and no environment variable is
  new ([variables](administration/variables.md)).

- **API change: no `language` in the notification settings.** The `language` field is gone
  from `GET` and `PATCH /api/v1/me/notification-preferences/` and from
  `GET /api/v1/me/notifications-export/`; a `PATCH` that still sends it is ignored. The language
  is read and changed at `/api/v1/me/display-preferences/`.

- **Upgrade: two limits of the migration.** A person whose notification language was never
  chosen (it stayed at the default, `en`) and who had no display language now gets `en` as an
  explicit choice: set the display language back to **Automatic** to follow the browser again.
  Rolling the migration back recreates the column with `en` and does not restore the earlier
  notification language.

## 0.3.1

HouseCairn is still alpha software. This version only fixes the demonstration instance: the box listing the public accounts no longer cuts the password, and the public accounts and their place in the demonstration home are protected, with the Django administration site read only. Nothing changes outside the demonstration mode: no migration runs and no setting is new.

### Fixed

- **The demonstration accounts box on the sign-in page no longer breaks the password across lines.** On a demonstration instance, the box that lists the public accounts set its three parts side by side, so that the password `housecairn-demo` could be cut after the hyphen and a visitor could type `housecairndemo`. The box now stacks the sentence, one line for each account and the password, at every width, and an address or the password is never cut.

- **Demonstration instance: the public accounts stay whole.** On a demonstration instance,
  the Django administration site is now read only, and the two public accounts keep their
  place in the demonstration home: nobody can leave it or be removed from it, no role changes,
  and the home cannot be deleted. Off the demonstration mode nothing changes
  ([Demonstration instance](administration/demo.md)).

## 0.3.0

HouseCairn is still alpha software. This version makes it an application to install on a
phone, with Today and the task list readable offline. It adds a unit price to the stock,
counted in the planned cost of a task, a search field with chips to choose several rooms or
items, every reviewed Apprise service for notifications, and notifications on the device by
Web Push. Reading the page of a link and testing a channel now run in the background worker.
The archive of a home can be restored as a copy, and a demonstration mode serves a public
instance. Fixes cover sign-up and the other emails when the mail server fails, a refused
restore that left files behind and tabs on a 320 px screen. Read the upgrade notes before
updating: the test of a channel and the preview of a link change in the API, four
migrations run and three settings are new.

### New

- **Choose several rooms or items by typing.** On the form of a stock item (compatible items,
  rooms) and of a project (rooms), the long list of boxes is replaced by a search field: type to
  narrow the list, pick with the arrows and Enter or with a tap, and each choice becomes a chip
  that its **Remove** button takes away. The items are shown with their room. Without
  JavaScript the form shows a list grouped by room and works as before
  ([stock item form](screens/stock-item-form.md)).

- **Demonstration instance.** `HOUSECAIRN_DEMO=true` makes a public demonstration instance:
  an administrator and a user whose address and password are public, the fictional home of
  `load_demo_data`, and everything erased and loaded again every night at 03:00 (Europe/Paris)
  by the task worker. The settings of the instance are read only, nothing is sent (email,
  notifications, link previews, single sign-on), no account can be created and the public
  accounts keep their password and address; files are held to small limits. A banner says so
  on every page ([Demonstration instance](administration/demo.md)).

- **Install on a phone, read offline.** The browser offers to install HouseCairn (Android)
  or you add it to the home screen (iPhone). Today and the task list of your home stay
  readable without a connection, as they were when last opened, with writes off until the
  connection is back. Signing out deletes what was kept
  ([Install on a phone](getting-started/phone.md)). Behind an authentication
  proxy, the manifest is now fetched with the cookie
  ([reverse proxy](administration/reverse-proxy.md#behind-an-authentication-proxy)).

- **More notification services.** A channel may now use any of 137 services reached
  through Apprise, each one reviewed: Microsoft Teams, Rocket.Chat, Zulip, Pushbullet,
  Bark, Free Mobile, SFR and many more. The Notifications screen lists them by family,
  with an example address, where the messages go and a link to the Apprise page of each
  service ([guide](guides/notifications.md)).

- **Notifications on the device.** Reminders and problem reports can appear as
  notifications on a phone or a computer, through the push service of the browser, with
  no other application (on an iPhone, once the application is installed). Off by default:
  an administrator turns on **Notifications on the device**; messages are encrypted for
  the device and discreet by default
  ([Notifications](guides/notifications.md#on-this-device)).

- **Restore as a copy.** The archive of a home can now be restored as a copy, next to the
  home it was made from: everything it holds gets new identifiers and its own files, and the
  original is not touched. Use it to duplicate a home or to check a backup without deleting
  anything. You own the copy and are its only member; its members and invitations are not
  copied. Also from the API (`as_copy`) and from `manage.py restore_home --copy`
  ([Export and deletion](administration/data.md#restore-as-a-copy)).

- **A unit price for stock items, and the stock in the planned cost of a task.** A stock item
  can have a unit price (a supplier's last price paid fills it when it is empty). The cost a
  task is planned at now adds, to the material budget you typed, the quantity of each stock item
  it lists times its unit price. The part from the stock is shown apart ("of which stock") on the
  page of the task and in the budget of the upkeep, with the number of stock items that have no
  price and count for nothing ([stock](guides/stock.md#give-an-item-a-unit-price),
  [budget](guides/budget.md#the-stock-in-the-cost-of-a-task)).
- **A stock item added from the form of a task fits its equipment.** It is now listed as
  compatible with the equipment of the task, including equipment added by the same form.

### Fixed

- **A refused restore leaves no file behind.** When a file of an archive was refused after
  others had been stored, the restore created nothing but left those files on the disk; they
  are now removed ([Export and deletion](administration/data.md#restore-of-a-home)).

- **Sign-up no longer fails with a server error when the mail server does not answer.** With
  sign-up open, a mail server that was unreachable, slow, or that refused the credentials or
  the message made the sign-up page fail with a server error, after the account had been
  created without its verification message. The sign-up is now undone as a whole and the form
  comes back with a message that says the confirmation email could not be sent; the same
  address can try again at once. The other messages of the sign-in flows are covered the same
  way: a password reset, a sign-in with an unverified address, an added address and a new
  verification message answer with the reason instead of a server error, and the security
  notices (password or primary address changed) are sent on a best-effort basis, so that the
  change itself is never undone by a mail server that fails.

- **Adding a link and testing a channel no longer hold the screen.** Reading the page of an
  inspiration link and sending the test message of a notification channel are now done by the
  background worker: the screen answers at once, shows "Reading the page…" or "The test message
  is being sent.", then the result (reload the page if it does not change by itself). A task
  that does not end within 30 seconds is shown as failed
  ([ADR 0070](adr/0070-slow-network-calls-in-the-background.md)).

- **Tabs fit on a 320 px screen.** On the narrowest phones, the tabs of the instance
  administration cut "Overview" and "providers" in the middle of the word, and the French tab
  of the audit log of a home was cut by hyphenation. The tabs are tighter under 360 px and the
  French tab is now named "Journal" ([design guide](developer/design-guide.md)).

### Upgrade notes

- **Migrations and new settings.** The update runs four migrations, all of them reversible
  and none of them changing existing data: `notifications.0002` (the devices of notifications
  on the device and the push key of the instance), `notifications.0003` (the state of the test
  of a channel), `projects.0004` (the state of the preview of a link) and `stock.0005` (the unit
  price, below). Three settings are new, and none changes what an instance does until it is
  set: `HOUSECAIRN_WEBPUSH_ENABLED` (`false`) and `HOUSECAIRN_WEBPUSH_DISCREET` (`true`), also
  in **Instance administration, Settings, Notifications**, and `HOUSECAIRN_DEMO` (`false`),
  which erases every account, home and file every night and must never be set on an instance
  with real data. The push key is encrypted with the secret key, like the other stored secrets
  ([variables](administration/variables.md),
  [Demonstration instance](administration/demo.md)).

- **API: testing a channel and adding a link answer before the work is done.**
  `POST /api/v1/me/notification-channels/<id>/test/` answers `202` with `{"state": "running",
  "detail": ...}` instead of `{"ok": ..., "detail": ...}`; read `test_state` of the channel
  later (`sent`, `failed` or `private`). A link created with the preview on is returned with
  `preview_state: "running"`, and its `title` and `image_url` are filled when it is `done`.
  The queue is the one of the background worker, which must be running (it is, in the
  container)
  ([API](api/index.md)).

- **Stock: a unit price, without a new export format.** Migration `stock.0005` adds the optional
  field `unit_price` to the stock items (reversible; it drops the field). Archives of the home
  gain the field `unit_price` in `stock_items` and stay in format 3; an archive written before
  restores with no unit price. The planned cost of the tasks that list stock items with a price
  goes up by the cost of those items, which may change the figures of the budget.

## 0.2.0

This version changes two defaults, both for safety: the backup of the instance no longer
holds the secret key, and notification channels on a private network are refused until an
administrator allows them, with a new setting. It also makes the provider of a maintenance
contract a contractor of the home, which updates the data once and moves the archive of a
home to format 3. Read the upgrade notes before updating.

### New

- **A Compose file for Coolify.** `deploy/coolify/docker-compose.yaml` runs the same image,
  with the same hardening, as `compose.yaml`, without a published port, an environment
  file or a fixed network range: Coolify's proxy reaches the container, and the domain set
  in Coolify gives `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and `HOUSECAIRN_SITE_URL`
  ([Deploy with Coolify](administration/coolify.md)).

- **The 400 page of a refused host name says what to do.** An address that is not in
  `ALLOWED_HOSTS` gets a page that tells the administrator to add the name. Whoever hosts
  HouseCairn for someone else can name the exact option with
  `HOUSECAIRN_DISALLOWED_HOST_HINT`; the page never repeats the refused name
  ([settings](administration/settings.md#the-page-of-a-refused-host-name)).

- **A refused form says why, at the top.** When a form is refused, the page opens on a
  summary of the errors ("There are 2 errors"): one line per field, with its label and the
  message, each linked to the field, and the errors that belong to no field. The summary is
  announced by screen readers and holds the focus, a folded section that holds a field in
  error opens, and the field is marked as invalid. Under it, each message stays next to
  its field. This holds for the forms of a project page, of the household and of the instance
  overview too, which used to redirect with the message in the banner. The tabs of the instance
  administration no longer run into one another when a label is long, on a phone and in French
  ([design guide](developer/design-guide.md)).

- **The provider of a maintenance contract is a contractor of the home.** In the form of an
  item, the provider is chosen in the list of the contractors, whose last choice, **Add a
  contractor…**, adds one without leaving the form. The page of the item links to the sheet of
  the contractor, and a task added from the item starts with that contractor. Deleting a
  contractor leaves the contract of the item without a provider, its number and its link kept
  ([item form](screens/item-form.md), [item](screens/item.md)).

- **Add a room, an item or a stock item from the task form, and as many material lines as
  needed.** The last choice of the room, item and stock item lists (**Add a room…**,
  **Add an item…**, **Add a stock item…**) adds one without leaving the form; it is created
  when the task is saved. The materials are no longer limited to two lines: **+** and **−**
  add and remove lines up to 50 (without script, **Add lines** shows three more). The page
  of a task shows the photos of its item or room, and a HEIC photo sent from a phone is
  refused with a clear message ([task form](screens/task-form.md),
  [task](screens/task.md)).

### Fixed

- **One way to write a date.** An English screen could show `Nov. 8, 2026` beside
  `02/08/2027`, which reads as 2 August or as 8 February. Every date now has its month in
  letters, in one format per language: `Nov 8, 2026` in English, `8 nov. 2026` in French
  ([how dates are shown](guides/dates.md)).
- **A comma in a number tells what to type.** In English `12,5` is still refused, but the
  message now says "Enter a number with a point as the decimal mark, for example 12.5." instead
  of "Enter a number." The quantities of stock and of materials, the quantity used when a task
  is marked done, the threshold of a stock item and the last price paid to a supplier follow
  the rule of the amounts, which was only that of money fields: in French they accept the
  comma (`12,5`) ([entering an amount](guides/amounts.md)).
- **The tests no longer read your `.env`.** Following the quick start, `cp .env.example .env`
  sets `DEBUG=true`, which made `test_debug_is_off_by_default` fail. The tests that import the
  settings do it from a copy of the sources, without any `.env`, and the contributor guide
  lists the `data` folder the quick start needed.

- **Rotating the secret key keeps the stored secrets.** `SECRET_KEY_FALLBACKS`, which the
  documentation advised for a rotation, was not read: the SMTP password, the secrets of
  the sign-in providers and the notification channels became unreadable with the new key.
  It is now read, comma-separated, and the steps are in
  [Rotate the key](administration/settings.md#rotate-the-key).
- **Every connection of a notification is checked.** While a message is sent, the address
  of each connection is checked as the names were, IPv4, IPv6 and IPv4 in IPv6, so a
  service that would skip name resolution reaches no refused address either
  ([ADR 0059](adr/0059-notifications-refuse-private-networks.md)).

### Upgrade notes

- **The provider of the maintenance contract of an item is now a contractor.** The update
  changes the data of every home, once, and can be undone (`manage.py migrate rooms 0004`):
  for each item whose provider was a text, items in the trash included, the contractor of the
  same home with that name (case and extra spaces ignored) becomes its provider; when the home has none, a contractor
  of that name is created, with the trade **Other** said as **Maintenance** (change it on its
  sheet), and shared by the items that name the same provider. A text longer than 120
  characters, the length of a name, is cut for the name and kept whole in the note of the
  contractor. Going back puts the name of the contractor into the text again; the contractors
  made by the update stay. Nothing is asked and nothing is lost.
- **Export format 3.** The archive of a home and the table of items name the provider of a
  contract by the id of its contractor (`contract_contractor`) in place of the text
  (`contract_provider`), and the archive says version 3. A version 2 archive, and a table of
  items with the old column, are still restored and imported, by the rule above. API
  clients that read or write `contract_provider` on an item use `contract_contractor`, the id
  of a contractor of the home, instead
  ([export](administration/data.md), [API](api/index.md),
  [ADR 0058](adr/0058-contract-provider-is-a-contractor.md)).

- **The backup of the instance leaves the secret key out.** `backup_instance` no longer puts
  `/data/secret_key` in the archive: with the key, a copy of the archive gave every stored
  secret. Keep the key apart, best as `SECRET_KEY` in `deploy/production.env`, and add
  `--include-secret-key` if a backup must restore alone. A restore without the key says what
  to do ([Data and backups](administration/backup.md#the-secret-key),
  [ADR 0060](adr/0060-secret-key-apart-from-the-backups.md)).
- **Notification channels on a private network are refused unless allowed.** Addresses of
  private networks (such as a ntfy or a Gotify on the local network) are no longer sent to
  by default, and their test says why. If your people use one, turn on **Channels on the
  local network** in **Instance administration, Settings, Notifications**, or set
  `HOUSECAIRN_NOTIFICATIONS_PRIVATE_NETWORKS=true`
  ([Hardening](administration/hardening.md),
  [ADR 0059](adr/0059-notifications-refuse-private-networks.md)).

## 0.1.0

The first version of HouseCairn. It is a test version: it has not been used in a
real home for any length of time yet, and what it stores can still change shape
from one 0.x version to the next, with the upgrade notes of each version saying
what to do. The entries below were written along the development. The upgrade
notes concern instances that ran an image built from `main` (`edge` or
`sha-<commit>`) before this version; a new instance has nothing to do.

### New

- **Longer estimated durations, and a contractor chosen in a list.** The estimated duration
  of a task takes weeks, months and years as well as minutes, hours and days, up to one
  year instead of thirty days (a week is 7 days, a month 30 days, a year 365 days), and the
  number is readable next to its unit on a phone. The contractor of a task is chosen in the
  list of the contractors of the home; its last choice, **Add a contractor…**, adds one
  (name and trade) without leaving the form, with or without script
  ([task form](screens/task-form.md)).

- **Done without losing your place.** On [Today](screens/today.md) and in
  [all the tasks](screens/tasks.md), **Done** no longer reloads the page nor sends
  it back to the top: the row shows the task as done, and a banner at the bottom of the
  screen, above the bar, offers **Undo**, announced by screen readers. Without
  JavaScript, the page comes back to the row. The browser's back button no longer shows
  an old **Undo** banner, and going back to a creation form no longer creates the
  record twice.

- **API tokens for a home that requires a sign-in provider.** Such a home can now be given
  to a token, for example for Home Assistant, from a session signed in through its
  provider: on the [API tokens](screens/api-tokens.md) form, it is listed and
  **Continue with** the provider unlocks it. The token remembers the provider, and stops
  opening the home if an owner requires another one. **Create a token** is shown as soon
  as you belong to a home.

- **API tokens from the application.** You create, list and revoke your
  [API tokens](screens/api-tokens.md) in **More**, **API tokens**, without a command
  on the server: a name, the rights (**Read** by default), the homes it opens and an expiry
  of 30 days, 90 days (the default) or 1 year. The value is shown once, with a **Copy**
  button, and never again. The same operations are in the
  [API](api/index.md): `GET` and `POST /api/v1/me/tokens/`, `DELETE
  /api/v1/me/tokens/<id>/`, for a person signed in only (a token gets `403`). Creating and
  revoking a token go to the audit log of the instance.

- **Tasks without a date in the API.** `GET /api/v1/homes/<id>/tasks/?due=none` returns only
  the active tasks that have no due date, so an integration no longer has to read the whole
  list to find them. It combines with the other filters and with the cursor; any other value
  of `due` is a 400. See the [API guide](api/index.md).

- **Import tables from CSV files.** Owners and members add rooms, items, tasks, stock and
  contractors to a home from the CSV files of **Tables for a spreadsheet**, edited in a
  spreadsheet: the preview shows each row accepted or refused with the reason and writes
  nothing, then **Import** adds every row in one go, all or nothing: while a single row is
  refused, nothing is imported. At most 1,000 rows and 2 MB per import. A row already in
  the home is refused, never merged, and a reference to another home is refused. The API does the
  same with `POST /api/v1/homes/<id>/import/` and a token with the `write` scope
  ([Import tables](screens/home-import.md)).
- **Opening a table in Excel or LibreOffice** is explained step by step, French menus
  included: every CSV of HouseCairn stays separated by commas
  ([Export and deletion](administration/data.md#opening-a-table-in-a-spreadsheet)).

- **Current home.** With two homes or more, the app bar shows the current home with a
  selector, and **Rooms**, **Projects**, **Budget** and **Today** open on it instead of
  asking which home at each change of section. Today and the calendar also offer
  **All homes**, where each row names its home. The current home is a preference of
  the session, never a permission: only your own homes are listed, and a home you
  leave is replaced by another ([the current home](screens/my-home.md#the-current-home)).
- The **Budget** entry opens the budget of the current home.

- **Design guide completed.** The [design guide](developer/design-guide.md)
  now says where the list of a new type of data goes, how dates, reminders and alerts
  reuse what tasks already do, what the default parent of a record reachable from two
  places is, and what a new screen must test. The repository also has an `AGENTS.md`
  for coding agents, which points to the contribution rules.

- **Design guide for contributors.** The [design guide](developer/design-guide.md)
  sets the rules every new or changed screen follows: the five sections and their
  tabs, where a screen goes, the return rule, actions in place, the current home and
  the visual system, with a checklist for review. The decision is
  [ADR 0053](adr/0053-target-navigation-and-return-rule.md); the navigation
  prototype and the v2 mock-up are in the repository, under `design/`.

- **Restore a home.** **Homes > Restore a home** makes the archive of **Export all data**
  a new home, on this instance or another, with every room, task, history, stock item,
  project and file, under the same identifiers. A changed, foreign or unsafe archive is
  refused with the reason, and nothing is written
  ([Export and deletion](administration/data.md)).
- **Tables for a spreadsheet.** Every member downloads the rooms, items, tasks, history,
  stock, contractors and reports of a home as CSV files that Excel opens with their
  accents, and that cannot run a formula.
- **Large homes are exported in the background**, then downloaded from the page of the
  export, at most three times within a day.
- **Back up the whole instance in one command**, `backup_instance`, and restore it with
  `restore_instance`, which checks every part first. With PostgreSQL, whose `pg_dump` the
  image does not contain, the guide gives the commands of the `postgres` service instead
  ([Data and backups](administration/backup.md)).

- **First administrator on a web screen.** While an instance has no administrator,
  HouseCairn writes a one-time code in its log at each start. The
  [first-account screen](screens/first-account.md) (`/setup/`) takes that code, a
  name, an address and a password, creates the administrator and signs them in, without
  a command on the server. The code is random, works once and expires after one hour;
  the screen answers 404 as soon as an administrator exists, or when
  `HOUSECAIRN_SETUP_SCREEN=false`. Single sign-on and `createsuperuser` work as before; the
  first one to create an administrator closes the others. See
  [First start](getting-started/first-start.md).

- **Behind Home Assistant's Ingress.** `HOUSECAIRN_ALLOW_SAME_ORIGIN_FRAMING=true` lets a page
  of the same origin show HouseCairn in a frame, as the Ingress panel does; it is off by
  default. Links made to leave the application (emails, invitations, QR labels, calendar
  feeds) are now always on `HOUSECAIRN_SITE_URL` and `HOUSECAIRN_URL_PREFIX`, never on a
  prefix given by a proxy header. See
  [Serving under a path prefix](administration/url-prefix.md).

- **Instance settings in sections.** The settings of the instance are no longer one long page with
  nine Save buttons: **Instance administration, Settings** is now an index, and each section (public
  address, sign-up, outgoing email, rate limits and the others) has its own page with one button
  that names what it saves. A variable still locks its field and shows the value in force. The old
  addresses, `/instance/settings/?section=<section>`, open the page of the section. See [Instance
  settings](screens/instance-settings.md).

- **Breadcrumbs on a computer.** From 56 rem wide, a record or a form shows the path
  from its section down to the page (Rooms › Living room › Boiler). A phone keeps the
  back link alone.
- **One word per thing.** Screens, buttons and tabs use the same term for the same
  thing, in English and in French. The two audit logs say whether they are about the
  home or the instance, the filter of finished tasks agrees with "tasks" in French, and
  the administration of the instance fits in four tabs.
- **Repeat opens by itself** when a task is created from an item.

- **Five sections in the navigation.** The bar at the bottom of a phone now
  holds Today, Rooms, Projects, Budget and More; on a computer the column on the
  left shows the four sections with the content of More unfolded under them. Each
  section has its own tabs, all visible on a phone: Today, All and Calendar;
  Rooms and Stock; In progress, Ideas and Finished; Summary, Rooms, Projects and
  Upkeep. The history is the **Done** filter of All, and its former address
  leads there. The root of a section has no back link, and the entry marked as
  current is that of the section the page belongs to: Budget marks Budget. More
  comes in four groups, Home, Me, Instance and Help; deleting your account is at
  the bottom of the new [Security and sign-in](screens/security-and-sign-in.md)
  page.

- **One home page: Today.** The logo, signing in and the installed application now
  all open on Today: your only home, or all your homes together if you have
  several. "My home" is gone as a separate screen and its old address redirects. The
  welcome page stays for visitors and for accounts with no home. **Report a problem**
  is under the title of Today for every member, a read-only one included, and the
  number of reports that wait is shown next to **Reports** in More.

- **Repetition in one sentence.** The task form says the repetition as you would: **Repeat
  every** 3 months, **Count from** the due date (with the calendar of the device) or the day
  it is done, and a summary in plain words that follows what you type ("Next time: 3 months
  after the last time"). The fields a choice does not use are hidden, the months of a
  custom season among them; without script they stay visible and are ignored. See the
  [task form](screens/task-form.md).

- **Back to where you came from, everywhere.** Stock, Projects, Budget, Reports, QR labels
  and the pages of a home follow the same rule as Rooms and Tasks: the link at the top
  leads back to the screen a page was opened from, and names it. A project opened from
  the budget says **Budget**, and Budget stays marked in the bar. A stock movement, the
  status of a quote or a button of a report keeps that way back.
- **The budget of a room.** The page of a room shows what is planned and used for it this
  year, with a link to its detail in Budget. In Budget, a room of the budget by room opens
  that detail, project by project and task by task, with **Open the room**.

- **Back to where you came from.** In Rooms and Tasks, the link at the top of a
  page leads back to the screen it was opened from, and names it: a task opened
  from an item goes back to the item. A task or an item added from a room or an item
  comes back to it, with **Added · Open** to open the new record; added from the
  list of its own section, the new record opens. A deletion never leads back to
  the page of what was deleted. The pages of More no longer show a link back to More on a
  computer, where the column already shows it.

- **Task templates.** About twenty common tasks of a home come ready, in English and in
  French, each with an indicative interval and the public source it comes from: the
  battery of the smoke alarms, the service of the boiler, the sweeping of the chimney, the
  vents of the ventilation, the gutters, the hot water tank and more. **Tasks › Add from a
  template** adds them in two taps, and the equipment ticked when a home is created adds
  theirs. A rule of one country, such as the yearly service of a boiler in France, is
  shown as such ([Add from a template](screens/task-templates.md)).
- **A guide of the API**, with `curl` commands that the tests run against an instance:
  token, reads, writes, pages of a list and errors ([Using the API](api/guide.md)).

- **Design guide: a secret is shown once.** The rule that the screen of the API tokens
  follows is now written for every screen: in the answer to the `POST` that made it, with
  `Cache-Control: no-store` and a Copy button
  ([Design guide](developer/design-guide.md)).

- **Served under a path prefix.** An instance can live under a path, such as
  `https://example.com/housecairn/`, with `HOUSECAIRN_URL_PREFIX`: pages, static files,
  cookies, the installed application, the API and the links of emails, QR labels and
  calendar feeds follow it. A trusted proxy can also give the prefix of each request in a
  header named by `HOUSECAIRN_URL_PREFIX_HEADER`, as Home Assistant's Ingress does. See
  [Serving under a path prefix](administration/url-prefix.md).

- **Calendar and calendar feeds.** See your tasks by week, month or year, with filters by home,
  room, person and priority, **Done** and **Undo** in place, and the next dates of a task that
  repeats marked as forecast. **Calendar feeds** (**More**, **Calendar in another application**)
  gives a secret address per home, or for all your homes,
  that Google Calendar, Apple Calendar, Thunderbird or Home Assistant can subscribe to: it shows
  titles, home, room, date and status, never notes, costs or files. You can get a new address or
  revoke it at any time; leaving a home revokes your feed of it. See
  [Calendar](screens/calendar.md) and [Calendar and feeds](guides/calendar.md). New dependency:
  `icalendar`.
- **Reports.** Every member of a home, a read-only one included (a tenant, a tradesman), can
  **Report a problem** with a title, a place and a photo, from a form or from a scanned
  sticker. Members who may write see the open reports on Today, make one into a task, close
  it or reopen it, and are told on their notification channels; the person who reported
  keeps a list of their own reports and sees nothing of the others. Reports are in the API,
  in the export of a home and are erased with it. See [QR labels and reports](guides/qr-labels.md)
  and [ADR 0047](adr/0047-qr-labels.md).
- **QR labels.** Print stickers that open a room, an item or a stock item: a PDF made by
  the server, as an A4 sheet of 3 by 8 labels or a 62 mm roll for a label printer. The
  code on a sticker is a random identifier of 128 bits that names nothing; a sticker
  opens the page of the object for the members of the home and answers the same "Page
  not found" to everyone else, and it can be revoked or replaced. Labels are in the API,
  in the export of a home and are erased with it. Set `HOUSECAIRN_SITE_URL` before
  printing: the address is on the stickers. See [QR labels](guides/qr-labels.md). New
  dependency: ReportLab ([ADR 0047](adr/0047-qr-labels.md)).

- **Budget.** A new **Budget** tab shows the money of a home by room, by project and for the
  upkeep: planned, committed, paid and the gap, in words and with labelled gauges, for the
  envelope of a year, by nature, by year and over the last 12 months. A task now shows its
  cost, with what it comes to in a year when it repeats. Every page downloads as CSV, and
  the API has the same figures (read only), with your homes added up by currency, never
  converted. Amounts are typed the same way everywhere, with the comma in French: see
  [Budget](guides/budget.md) and [Entering an amount](guides/amounts.md). Nothing new is
  stored, so there is nothing more to export or erase. *Still to pay* on a project is now
  worked out line by line, as the gap is.

- **Notifications.** Each person adds their own channels (ntfy, Telegram, Signal, Matrix,
  Gotify, email and other Apprise services) on **More, Notifications**, tests them, and
  chooses when reminders come: days before the due date, time of day, quiet hours, a daily
  summary. Follow-ups come until the task is done, skipped or postponed, more often for a
  high priority. Addresses are encrypted and never shown again. Off by default: an
  administrator turns them on with `HOUSECAIRN_NOTIFICATIONS_ENABLED` or in the settings.
  See [Notifications](guides/notifications.md).

- **Verify an address from the server.** On an instance that sends no email, `manage.py
  verify_email <address> [--primary]` marks an account's address verified, so that the account
  can accept an invitation handed over by hand. It is recorded in the audit log of the
  instance. See [Email](administration/email.md).

- **Rooms, items and photos.** Describe the rooms of a home, what was used in
  them (the room memory), the equipment and surfaces they hold, and add photos.
  Screens, API and a guide: see [Rooms and items](guides/rooms-and-items.md).
  Photos are stored in `/data/media/photos/`, checked by their content and
  stripped of their metadata. The new variable `HOUSECAIRN_PHOTO_MAX_BYTES` sets
  the size limit of a photo (10 MB by default).

- **Instance settings in the interface.** Sign-up, invitations, outgoing email and the
  public address can be changed by an instance administrator, in the interface and by the
  API, each paired with its environment variable: a variable that is set wins and locks the
  field. Secrets are encrypted in the database. See [Settings](administration/settings.md).

- **Sign-in providers in the interface.** An instance administrator can declare OpenID
  Connect providers on **Instance administration, Sign-in providers**, or by the API, next to
  the one the `OIDC_*` variables set, which stays read only. A **Test the issuer** button
  reads the provider's configuration. A new issuer clears the client secret unless it is
  entered again. The administrator cannot remove nor change their own last way to sign in,
  and the only administrator cannot be switched off in the administration site. See
  [Single sign-on](administration/oidc.md).

- **Rate limits, quotas, language, time zone and name of the instance.** An instance
  administrator can change the rate limits, the quotas of files of each home, the default
  language, the time zone and the name of the instance in the interface, each paired with
  its variable like the other settings. Quotas apply to every way to add a photo, an
  attachment or a label, and **Files of the homes** shows what each home takes, without its
  content. See [Settings](administration/settings.md).

- **Stock.** Keep track of spare parts and supplies: items with a threshold, suppliers
  and an append-only ledger of movements from which the quantity is computed, a photo of
  the label, and the equipment each item fits. Screens, API and a guide: see
  [Stock and supplies](guides/stock.md). Label photos are stored in `/data/media/stock/`.

- **Projects and quotes.** Plan a piece of work such as redoing the bathroom: a project
  with a status, a priority, rooms, a duration and a season, its steps as tasks created
  inside it, quotes from contractors, and a budget of lines with a planned, a committed
  (the quotes you accept) and a paid amount, whose gap is said in words. Compare projects
  side by side and set a budget envelope for the year. Screens, API and a guide: see
  [Projects and quotes](guides/projects.md). Photos and quote files are stored in
  `/data/media/projects/` and are part of the export of the home. Add links of inspiration to
  a project; if the administrator turns on **Preview of inspiration links** (off by
  default), the server reads each page once, when the link is added, for its title and its
  picture, which it keeps and serves itself. Only public web addresses are read: private
  networks and cloud metadata services are refused, on every redirect, with limits on size
  and time.

- **Feedback from the first tests.** Each person can choose their language in the display
  preferences; the sub-navigation of the tasks, of a home and of the instance administration
  is one set of tabs; the audit log of a home now records what people do to rooms, items,
  tasks, photos and files, by title only, and explains itself when it is empty; a room is
  created from its type, which fills in the name; the room memory explains each field; the
  photos of a room are one list with a category (general, behind the walls, before, after,
  other) that can be filtered; the brand of an item suggests the brands already entered in
  the home, and its maintenance contract is a provider and a number. See
  [Display preferences](screens/display-preferences.md), [Room](screens/room.md) and
  [Audit log of a home](screens/audit.md).

- **Seasons, display name, duration and contractors.** A task can be limited to a predefined season
  (spring, summer, autumn or winter) that follows the hemisphere of the home, which you set on the
  home page, or to custom months; existing windows become the matching season or a custom one. Each
  person can choose a display name on the new [profile](screens/profile.md) screen and in the API
  (`/api/v1/me/`), and the person in charge is shown by that name on every page, never by an
  address. The estimated duration is a number and a unit (minutes, hours, days, weeks, months or
  years). A task has a single contractor field, and each contractor has a
  [sheet](screens/contractor.md): trade from a list (plus Other), phone, email address, address,
  note and the tasks that name them, downloadable as a contact file (.vcf). Existing trades are
  matched to the list, otherwise kept under Other. See [Tasks and
  recurrence](guides/tasks-and-recurrence.md).

- **The stock follows the tasks and the rooms.** A task lists the stock items it needs, with
  a quantity (the **Materials** section of its form and its page, and `task-materials` in the
  API). Marking the task **Done** takes them from the stock, in the same gesture; **Undo**
  puts them back with new entries, nothing is erased from the ledger; the quantity really
  used is entered afterwards in the details of what was done. **Done** is never refused for
  lack of stock: the stock stops at zero and a message (`short` in the API) says what was
  missing. A room shows the stock items that concern it, and its paint left over is a link
  to a stock item, with its quantity and place. The unit of a stock item is a list (piece,
  litre, metre, kilogram, roll, box) plus Other. See [Stock and supplies](guides/stock.md),
  [Tasks and recurrence](guides/tasks-and-recurrence.md) and
  [ADR 0046](adr/0046-stock-linked-to-tasks-and-rooms.md).
- **Display name from the sign-in provider.** An account linked to a single sign-on provider
  takes its display name from the provider at each sign-in through it (`name`, else the
  given and family names, the username or the nickname), by the same rules as a typed name;
  a claim that would be refused is ignored and never stops the sign-in. A name typed on the
  [profile](screens/profile.md) is kept and no provider overwrites it, until **Use the
  provider's name**. Display names typed before this version are kept; accounts without one
  take the provider's at their next sign-in. See
  [Single sign-on](administration/oidc.md#display-name).

### Fixed

- **Amounts with the symbol of the currency.** Every amount now reads the same way on every
  screen, with the symbol of the currency when your language has one: `1 250,50 €` in
  French, `€1,250.50` in English, and the ISO code (`SEK`) when it has none. The cost of a
  task, its budgets and the last price paid to a supplier, which used to show the code
  next to the number, follow the same rule. Exports and the API are unchanged.

- **Back links on every screen.** The link at the top of a confirmation of the household
  names the screen it leads to instead of saying "Cancel", and the confirmation, the
  calendar feeds, a notification channel, the deletion of an account, the photo form, a
  sign-in provider, a section of the instance settings and a home that asks for a provider
  now lead back to the screen they were opened from, like every other screen. The calendar
  feeds lead back to More, where their entry is, instead of the calendar. About and
  **My homes**, renamed from "Homes" to match their entry of More, now have their link back
  to More on a phone. On a computer, the forms of a quote, a budget line, the annual
  envelope, a contractor, a supplier and a photo show their breadcrumbs.

- **A budget summary you read at a glance.** The top of the Budget page showed up to nine
  figures. It now shows three (what is set aside, **Spent** and **Left**) and a gauge with the
  share spent in words; a spending past the limit says **Over by ...** and *over budget*. The
  other figures (planned against the envelope, left to plan, paid, projects and upkeep apart,
  Budget in Sure) are under **Details of the year**. "Used, projects and upkeep" and "Left to
  spend" are gone: they were the same figures as **Spent** and **Left**.

- **The provider of a maintenance contract is preselected.** **Add a task** from an item
  whose maintenance contract names a provider now starts with that provider as the contractor
  of the task, when the home has a contractor of that name (whatever the case), and the
  **Who** section is open so that you see it. A provider that is not a contractor of the
  home is left alone: nothing is created until you save.

- **Design guide matches the code.** The [design guide](developer/design-guide.md)
  now lists the entries of More as they are, describes the back link of a form as it
  is drawn, and gathers the rules the code already follows: amounts through one filter,
  addresses built for a path prefix, links to typed addresses, and the first
  administrator screen reached from the sign-in page.

- **Tables that fit a phone.** The four views of Budget (summary, rooms, projects, upkeep),
  the budget of a room and the budget lines of a project no longer make the page scroll
  sideways: under 56 rem each row is a card, with the label of every value and the amount
  on the right. The comparison of projects keeps its columns in a scrolling area of its own.

- **Display preferences in the API.** Changing one preference no longer fails because the
  stored language is one the instance no longer ships: only the fields sent are checked.
- **Hidden text in names and titles.** Tag characters that spell a regional flag stay
  only inside the flag, joiners stay only between two visible characters, and the line and
  paragraph separators go; a name made only of such characters is empty, not invisible.
- **Room names in the API.** A room created or changed with an empty or invisible name takes
  the label of its type, as on the screen, instead of an error.

- **Return addresses with a control character.** A `next` address such as `//` followed by
  a line break or a tab was accepted after sign-in and sent to the browser as `//%0D`, an
  address whose host browsers refuse: the redirect failed. Every `next` (sign-in, sign-up,
  invitations, display preferences, task buttons) now refuses a control or space character
  and an address that starts with `//` or `\`, and falls back to the default page.
- **Inspiration links.** The title field of the form to add a link has its own label and
  identifier: touching "Title" no longer puts the cursor in the field of the new task, and a
  screen reader names it. Adding a link while the preview is on is limited, per person (10 a
  minute) and per home (30 a minute), on the web and in the API (429), two new settings in
  the **Rate limits** section; a refused link is not added and no page is read. The guard of
  outgoing requests now also refuses the IPv6 site-local range `fec0::/10`. See
  [Projects](guides/projects.md) and [Rate limits](administration/rate-limits.md).

### Upgrade notes

- **The archive of a home changes format.** **Export all data** now gives format 2: a
  `manifest.json` with the digest of every file, one `data/<app>.json` per part of the
  application with every field of each table, and the files; its JSON Schema is published
  with the documentation. A script that read `export.json` must read the new documents.
  Archives of format 1 cannot be restored: export the homes again after the update
  ([Export and deletion](administration/data.md)).

- **The heating season becomes custom months.** It is no longer offered as a season: the
  update gives every task and project that had it a custom season of the same months
  (October to April in the north, April to October in the south), so nothing changes for
  them. An archive or a CSV file of before that holds `heating`, and an API client that
  still sends it, get the same months ([task form](screens/task-form.md)).

- **The paint left in stock and the unit of a stock item change shape.** The field
  `paint_stock` of a room is replaced by `paint_article` (a stock item of the home); the
  old text is kept, word for word, as a line `Paint left in stock: ...` of the memory
  notes, which can then exceed the 4,000 characters usual for notes until it is shortened.
  `unit` of a stock item now accepts the codes of the list (`piece`, `litre`, `metre`,
  `kilogram`, `roll`, `box`, `other`) with `unit_other`; usual spellings (pieces, litres,
  kg, rouleau) become the matching code, anything else is kept under Other. Both migrations
  are reversible: going back writes the text again (the plural of the list for a unit).
  Clients of the API must use the new names.

- **Photo categories and the contract of an item change shape.** The category `open_walls` of a
  photo is now `behind_walls`, in the API and in exports; the field `contract` of an item is now
  `contract_provider` (the old text, whole) and `contract_number`, in the API and in exports. The
  migrations are reversible, but going back turns the categories `before`, `after` and `other` into
  `general`, and joins the two fields of the contract into the one text again and cuts it at 200
  characters: the contract number is lost when the provider alone is longer than that. Clients of
  the API must use the new names.

- **Each home now has quotas of files**: 5 GB and 5000 files by default, deleted files
  included until they are purged. Raise them in **Instance administration, Settings, Files of
  a home**, or with `HOUSECAIRN_HOME_STORAGE_MB` and `HOUSECAIRN_HOME_MAX_FILES`.
  `HOUSECAIRN_PHOTO_MAX_BYTES` and `TIME_ZONE` keep their meaning; set, they lock their field.

- **Environment variables are checked a little more strictly.** A boolean variable such as
  `HOUSECAIRN_SIGNUP_OPEN` or `EMAIL_USE_TLS` must be `true` or `false` (also `1`, `0`, `yes`,
  `no`, `on`, `off`); another word used to count as `false` and now stops the start-up with a
  message. `EMAIL_PORT` and `EMAIL_TIMEOUT` must be in range, and the domain lists must hold
  domain names. A variable set to an empty value now counts as not set.

- **Reverse proxy: set `HOUSECAIRN_FORWARDED_ALLOW_IPS`.** HouseCairn used to
  accept forwarding headers (`X-Forwarded-Proto`, `X-Forwarded-For`) from any
  peer. It now accepts them only from the addresses in
  `HOUSECAIRN_FORWARDED_ALLOW_IPS`, by default the container's own loopback
  (`127.0.0.1,::1`). An instance behind a reverse proxy must set it to the
  address or network the proxy connects from; otherwise HouseCairn ignores the
  proxy's headers, every request is redirected to HTTPS again, and every client
  is counted under the proxy's address for the sign-in limits. With the example
  `compose.yaml` and a proxy on the host, the value is `192.0.2.1`, the gateway
  of its now fixed network. See [Reverse proxy](administration/reverse-proxy.md).

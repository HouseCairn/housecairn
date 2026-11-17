# Projects

The projects of a home, at `/h/<home id>/projects/`. **Projects** in the navigation opens
it; `/projects/` opens the [current home](my-home.md#the-current-home) when you belong to
several.

A project is a piece of work with an end, such as redoing the bathroom. Three tabs split the
projects by status: **In progress** (in progress and planned, the root of the section, at
`/h/<home id>/projects/`), **Ideas** (`?view=ideas`) and **Finished** (finished and
abandoned, `?view=finished`). Within a tab the list groups the projects by status. It is a plain list, not a board. A row shows the title, the planned
budget and, in words, where the budget stands: **... left**, **Over budget by ...** or
**On budget**. A row opens the [project](project.md).

- **The search field** at the head looks in the title and the goal. It has a magnifier and no
  button: the list follows what you type, after a short pause, and the address keeps the
  words (`/h/<home id>/projects/?q=<text>`), so that Back and a bookmark find them. Without
  JavaScript, press Enter.
- **Filters** holds the status, the room and the priority, and says how many are applied, as in
  **Filters (2)**: `/h/<home id>/projects/?status=in_progress&room=<room id>`. The list follows
  each choice; without JavaScript, a **Filter** button appears in the sheet. A filter that is
  not valid is ignored.
- **Compare projects** (shown when the list has two projects or more) turns the rows into
  boxes: tick a row anywhere to select it, then **Compare the ticked projects** opens the
  [comparison](project-compare.md); **Cancel** goes back to the plain list.
- **Add a project** opens the [project form](project-form.md).
- **The budget envelope** at the top is what the household wants to spend on work this year.
  It sets it against the planned budget of the projects that are neither finished nor
  abandoned, shows what is paid so far and what is left, and says **over the envelope**
  when the plans exceed it. **Set the envelope** (owners and members) asks for a year and
  an amount in the currency of the home, at `/h/<home id>/projects/envelope/`. The last
  step of the [guide](guide-scenario.md) opens it in the example home with **Fill in for me**,
  filled in with the current year and 6,000; the form checks both before it shows them, and
  saves nothing until you press its button.

The [budget](budget.md) sets the envelope against the projects and the upkeep of the year
together.

Read-only members see the same list and none of the buttons that change it.

# Today

What to do in a home, at `/h/<home id>/today/`. It is the screen to open every
day, and the one the application opens on: the [start page](start-page.md) leads to
it, with several homes on the [current home](my-home.md#the-current-home) or on all your
homes together. **Today** is
also linked from the [task list](tasks.md).

The page is made of sections, in this order:

- **Overdue**: the arrears, oldest first, whatever the layout you chose. They are
  shown in red.
- What falls between today and your horizon, laid out by the **Group by** you chose
  in the [display preferences](display-preferences.md): by **urgency** (today, then
  coming up), by **room**, by **person** (who is in charge, with *Nobody yet* last)
  or by **calendar** (one section per day).
- **In season**: the tasks of the current season that are due beyond the horizon or
  have no date.

Each row shows the task, its room and item, and its state; a task made from a
report that has a photo also carries a **Photo** label. **Done**, on the right
of the row, closes it in one tap, for every task. The page does not reload nor
move: the row shows the task as done, and a banner at the bottom of the screen,
above the bar, offers **Undo**, which puts the row back as it was. The banner
stays until the next action or page, and a screen reader announces it. A row
opens the [task](task.md).

- The **tabs** above the list switch between the three views of the Today section:
  **Today**, **All** (the [task list](tasks.md)) and **Calendar** (the
  [calendar](calendar.md) of the home). The current tab is marked; all three stay
  visible on a phone. Today is the root of its section: it has no back link.
- **Report a problem**, under the title, opens the [form](report-form.md) for this
  home. Every member has it, a read-only one included: it is often the only thing
  they do here. When reports are open, a line next to it says how many, with **See
  the reports**; the line is shown to the members who may write, never to read-only
  members (see [Reports](reports.md)).
- When the instance sends [notifications to devices](../guides/notifications.md#on-this-device)
  and this browser is not one of yours yet, a banner under the title asks **Receive the
  reminders on this device?**. **Turn on** asks the browser for its permission and adds the
  device with a name of its own, in one tap; **Later** hides the banner for seven days; **Do
  not ask again** hides it in this browser. Nothing is asked until you tap. In Safari on an
  iPhone or an iPad the banner says to install the application first. It is not shown where
  the browser blocks notifications, on a demonstration instance, or where nothing can be
  received.
- A task hidden until a few days before its date does not appear until then.
- Each section shows at most 200 rows; the task list holds the rest.

Read-only members see the same page and none of the **Done** buttons.

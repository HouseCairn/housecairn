# Report

One report, at `/h/<home id>/reports/<report id>/`: its state (**Open**, **Made into a
task**, **Resolved** or **Closed without action**), what is wrong, where (a link to the
room, item or stock item when it still exists), who reported it and when, the task made
from it (a link, kept even after the task is done, whose page shows this report and its
photo in turn) and the photo, which opens full size.

The person who reported sees their own report and no other. Members who may write see
every report and have the buttons:

- **Make it a task** creates a task with the same title, in the same room or item, with
  no date, and keeps the link. Only an open report can be made into a task.
- **Mark as resolved** and **Close without action** close the report, from open or from
  task. The date and the person are kept.
- **Reopen** brings a closed report back: to *Made into a task* when it has a task that
  still exists, else to *Open*.

Creation, conversion into a task and closing are recorded in the [audit log](audit.md)
with the title and nothing else. An address that belongs to another home, or to a report
you may not see, answers "Page not found".

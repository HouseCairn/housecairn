# Add from a template

The page **Add from a template**, at `/h/<home id>/tasks/templates/`, adds common tasks of
a home from the library that comes with HouseCairn
([ADR 0049](../adr/0049-task-templates.md)). Owners and members open it from the list of
tasks, or from the page of a [room](room.md), under **Add a task**; opened from a room, it
has that room already chosen. Read-only members do not see it. Its back link and
**Cancel** lead back to where it was opened.

The templates are grouped by equipment, each group a list under its heading: smoke
alarms, carbon monoxide alarms, boiler, fireplace or stove, heat pump, forced-air heating,
mechanical ventilation, hot water tank, water softener, gutters, bathroom, tumble dryer,
electrical installation, septic tank. Each line is one template, a row you tap anywhere
to tick it; it gives the interval of the template and a link to its **source**, the public
page the interval comes from. A template that follows the rule of one country says so, *rule of
FR* for France, and its task explains what to do elsewhere.

Tick the templates you need, choose a **Room** and a **First date** if you like, then
**Add the tasks**. Each template becomes a task of this home, in your language:

- its title, its duration, its season when it has one, and its procedure;
- it repeats after the last time it was done, at the interval of the template;
- its notes give the materials, the regional rule if any, and the source.

From then on it is an ordinary task, which you change or delete as any other; a later
version of the library does not touch it. Without a first date, the task waits for one:
give it on the task, or mark it done the first time you do it. The page then goes back to
the list of tasks with the tasks added.

The battery of a smoke alarm, for instance: **Tasks › Add from a template**, tick
*Replace the battery of the smoke alarms*, **Add the tasks**.

The API gives the same library, `GET /api/v1/task-templates/`, and adds templates with
`POST /api/v1/homes/<home id>/tasks/from-templates/` ([Using the API](../api/guide.md)).
When a home is created, the equipment ticked on its form adds the tasks of its templates
([Add a home](new-home.md)).

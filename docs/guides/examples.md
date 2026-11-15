# Examples

Two jobs from the fictional demo home, from start to end. You can follow them on the
[public demo](../administration/demo.md) or on your own instance with
`manage.py load_demo_data`.

## Descale the espresso machine

A small job that comes back every three months and uses a part each time.

1. **The task.** *Descaling*, repeated three months after the last time.
2. **The equipment.** The task belongs to the *Espresso machine* of the kitchen, and the
   page of the machine lists it.
3. **The part.** The task lists one *Water filter*, a stock item at 7.90 each. Marking
   the task **Done** takes one filter from the stock.
4. **The cost.** The budget plans 7.90 each time, four times a year: the task shows
   **Cost a year** 31.60, and **Budget**, **Maintenance** lists it with the same figure.
5. **The stock.** Two filters were bought; after one Done there is one left, the
   threshold of the item, and the stock list marks it **Low** before it runs out.

```{image} ../assets/examples/descaling.png
:alt: The Descaling task after it was done once: due again in three months, in the kitchen, on the espresso machine. Cost section with 7.90 each time, all of it from the stock, and 31.60 a year. Materials section with one water filter needed and one left in stock.
:width: 480px
```

The [stock guide](stock.md) and the [budget guide](budget.md) explain each step.

## Redo the bathroom

A project with an end, in the same home.

- **Its tasks.** *Choose the tiles* and *Remove the old bathtub*, each with a date, show
  on Today like any other task.
- **Two quotes compared.** Two plumbers sent a quote; the one at 2,850 is accepted on the
  *Plumber (shower)* budget line, the other one, at 3,400, stays received.
- **Inspiration links.** Two pages kept on the project, with their titles.
- **The envelope of the year.** 6,000 for all the projects and the upkeep of the year.
- **Planned, committed, paid.** The project plans 4,200 on two lines; the accepted quote
  commits 2,850 and 480 of tiles are paid.

The [projects guide](projects.md) and the [budget guide](budget.md) explain each step.

# Budget line form

Adds a budget line to a [project](project.md)
(`/h/<home id>/projects/<project id>/lines/new/`) or changes one
(`/h/<home id>/projects/lines/<line id>/edit/`).

- **Label** is what it pays for ("Paint", "Plumber").
- **Nature** is materials, labour, rental or other.
- **Planned** is what you plan to spend; **Paid** is what has been paid so far. Both are
  amounts in the currency of the home, from 0 to 9999999999.99, with at most two
  decimals ([how to type one](../guides/amounts.md)). The committed amount is not typed: it is the sum of the quotes accepted on the
  line.

**Delete the budget line** asks for confirmation. The quotes accepted on it go back to
*received*: they commit nothing any more.

## Opened from the guide

A step of the [guide](guide-scenario.md) opens this form in the example home, filled in from its
address by **Fill in for me** with the values of the example (the label, the nature and the planned and paid amounts of a line). The form shows them and
saves nothing until you press its button; each value is checked by its field first, and one the
field refuses (a choice that does not exist, a text too long) is left out, as if you had
not typed it. Saved, the form comes back to the guide.

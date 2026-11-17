# Quote form

Adds a quote to a [project](project.md) (`/h/<home id>/projects/<project id>/quotes/new/`)
or changes one (`/h/<home id>/projects/quotes/<quote id>/edit/`).

- **Contractor** is one field: pick a contractor of the home from the suggestions, or type
  a new name, which joins the [contractors](contractors.md) when you save. Case does not
  matter; empty means nobody.
- **Amount** is in the currency of the home, from 0 to 9999999999.99 ([how to type one](../guides/amounts.md)).
- **Date** and **Valid until** are optional; the validity cannot end before the date.
- **Status** is requested, received, accepted or rejected. An accepted quote is **committed
  on** a budget line of the project, which you choose; with any other status the line is
  left empty.
- **File of the quote** takes an image, or a PDF, text, Word, Excel or OpenDocument file,
  checked by its content (up to 5 files for a quote). **Delete** removes one.
- **Document in Paperless-ngx** keeps a document number and a link for a later connection:
  nothing reads them yet.

**Delete the quote** asks for confirmation and releases what it committed.

The link at the top and **Cancel** lead back to the screen the form was opened from,
usually the project, and so does the form once sent.

## Opened from the guide

A step of the [guide](guide-scenario.md) opens this form in the example home, filled in from its
address by **Fill in for me** with the values of the example (the contractor, the amount, the dates, the status and the budget line of a quote). The form shows them and
saves nothing until you press its button; each value is checked by its field first, and one the
field refuses (a choice that does not exist, a line of another project, a text too long) is left out, as if you had
not typed it. Saved, the form comes back to the guide.

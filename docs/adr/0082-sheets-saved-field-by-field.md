# 0082. The sheets of records are saved field by field, through their own form

- Status: accepted
- Date: 2026-11-15
- Related: [0077](0077-sync-api-for-the-mobile-app.md) (change log)

## Context

The maintainer asked on 2026-11-15, after using the application, that the sheets of an item, a
room, a task and a stock item save each field as it is changed, with a discreet *Saved* that a
screen reader announces, the error on its field without losing what was typed, creations and
deletions keeping their buttons, the button *Save* kept without script, every write through the
services and the change log, one write per field changed, and the same CSRF protection and rate
limits as the other forms.

## Decision

- **The same POST as the button.** A form marked `data-autosave` (`static/js/autosave.js`) sends
  itself with HTMX (`htmx.ajax`) when a field changes, or 800 ms after the last key typed, with
  `_autosave=1`. The view, the form, the CSRF token, the services and the change log are those of
  the button; `core.autosave.AutosaveMixin` changes only the answer: a status in
  `#autosave-status` (`role="status"`, `aria-live="polite"`) and, by out-of-band swaps, the error
  of each field in a place under it, empty when the field is right (every field has one, the
  duration and the unit of a warranty included). Nothing the user types in is replaced, so nothing
  typed is lost; the one exception is a figure the form calculates (the material budget of a task,
  from its priced stock items), swapped in place when the stock items of the task changed.
- **One write per field changed.** A form whose values are those stored (`has_changed`, and the
  lines of the materials of a task) writes nothing; the change log records only the fields whose
  value changed (`sync.rows`). The timer of the typing is cancelled by the change of the same
  field, and a request with nothing new costs one read.
- **Creations keep their button.** A choice that adds a record in place (a contractor, a room, an
  item, a stock item) is not saved on the fly: the status asks to press *Save*. A file chosen is
  sent by the button only. The forms that create a record and the pages that delete one are not
  changed.
- **Without script** the form is the plain form it was: the button saves and leads back.
- **Rate limits.** The forms of the web have no limit of their own (the limits are those of the
  sign-in, the invitations and the API); a save on the fly is a request of that form and has the
  same. Alternative rejected: a separate endpoint per field, which would need its own rules,
  checks and tests for each record.

## Consequences

- The edit forms of the four records answer a fragment to a request that carries `HX-Request`
  and `_autosave=1`; any other request keeps its answer.
- A form of a record added later joins by putting `AutosaveMixin` first on its edit view and
  `data-autosave` on its form.

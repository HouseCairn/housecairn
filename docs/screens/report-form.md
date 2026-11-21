# Report a problem

The form at `/h/<home id>/reports/new/`. Every member of the home may use it, a read-only
one included; it is the only thing a read-only member writes. It is one tap from
[Today](today.md), where **Report a problem** is under the title.

- **What is wrong?** is the only required field: one line, 200 characters at most.
- **Where?** is optional: a room, an item or a stock item of the home. Coming from the
  page of a room, an item or a stock item, or from a QR label, it is already filled in.
- **Photo** is optional: a JPEG, PNG or WebP image, up to the size limit of the instance.
  The file is checked by its content, not by its name; the location and every other
  piece of information hidden in it are removed; it counts in the storage of the home.

**Send the report** shows the report and thanks you. A read-only member may have 20 open
reports at the same time; wait for some to be dealt with before sending more. The owner and
the members who may write have no limit, and a report made into a task no longer counts. The members who may write
and have a notification channel are told (see the [guide](../guides/qr-labels.md)).

Opened with **Report a problem** from a room, an item or a stock item, the form comes back
to that page once sent, with **Problem reported · Open** to open the report. Opened from
the reports, it opens the new report.

## Opened from the guide

A step of the [guide](guide-scenario.md) opens this form in the example home, filled in from its
address by **Fill in for me** with the values of the example (what is wrong and the room). The form shows them and
saves nothing until you press its button; each value is checked by its field first, and one the
field refuses (a choice that does not exist, a text too long) is left out, as if you had
not typed it. Saved, the form comes back to the guide.

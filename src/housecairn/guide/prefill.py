"""Forms opened from the guide, filled in from their address (docs/adr/0084).

A step of the guide that names a row of the example (`suggest`) opens its form with the values
of that row in the query: `?name=Kitchen&kind=kitchen`. The form only shows them; the person
still presses the button that saves it, and the post is checked as any other.

What the query says is input, never trusted: each value goes through the `clean` of its field
before it is shown, and a value the field refuses (a choice that does not exist, a room of
another home, a text too long, a date that is not one) is left out, as if the query did not
carry it. Only the fields a view names are read, and a page that is not a form ignores the
query altogether.
"""

from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.http import QueryDict

# A value of the query is at most this long before its field even sees it.
MAX_LENGTH = 2000
# How many values a field of several choices takes from the query.
MAX_VALUES = 20


def _raw(query: QueryDict, name: str, field: forms.Field) -> Any:
    if isinstance(field.widget, forms.SelectMultiple | forms.CheckboxSelectMultiple):
        return [value[:MAX_LENGTH] for value in query.getlist(name)[:MAX_VALUES]]
    return query.get(name, "")[:MAX_LENGTH]


def apply(form: forms.BaseForm, query: QueryDict, names: tuple[str, ...]) -> list[str]:
    """Show in `form` the values of `query` for the fields `names` that their field accepts.

    Returns the names filled in. A value refused by its field, or empty, is left out.
    """
    filled = []
    for name in names:
        field = form.fields.get(name)
        if field is None or name not in query or field.disabled:
            continue
        raw = _raw(query, name, field)
        try:
            value = field.clean(raw)
        except ValidationError:
            continue
        if value in (None, "", [], ()) or (hasattr(value, "exists") and not value.exists()):
            continue
        form.initial[name] = value
        filled.append(name)
    return filled


class PrefillMixin:
    """For a form view: on a GET, `prefill_fields` are filled in from the query.

    The guide's links carry the values (`guide.links`); any other link may too, and the
    values are checked by the fields of the form before they are shown.
    """

    prefill_fields: tuple[str, ...] = ()
    request: Any

    def get_form(self, form_class: Any = None) -> Any:
        form = super().get_form(form_class)  # type: ignore[misc]
        if self.request.method == "GET" and self.prefill_fields:
            apply(form, self.request.GET, self.prefill_fields)
        return form

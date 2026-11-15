"""Saving the sheet of a record field by field, as it is edited (docs/adr/0082).

The edit form of a room, an item, a task and a stock item sends itself again, with script, when
a field changes or after a pause in typing (`static/js/autosave.js`, through HTMX). The request is
the same POST as the button **Save**, with `_autosave=1` and the header `HX-Request`: the same
view, form, CSRF check, services and change log. Only its answer differs: a short status in place
of the redirect, and the errors of each field next to it, so that nothing typed is lost.

- Nothing changed since the last save: nothing is written, the status says it is saved.
- A choice that adds a record in place (a new contractor, room, item or stock item) is not
  saved on the fly: a creation keeps its button, the status asks for it.
- Without script the form is a plain form, and its button saves and leads back as before.
"""

from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils.translation import gettext as _

AUTOSAVE_KEY = "_autosave"
# Fields whose value makes a record in place: a form that holds one waits for its button.
CREATING_KEYS = ("new_contractor", "new_room", "new_item", "new_article")


def is_autosave(request: HttpRequest) -> bool:
    return (
        request.method == "POST"
        and request.headers.get("HX-Request") == "true"
        and request.POST.get(AUTOSAVE_KEY) == "1"
    )


class AutosaveMixin:
    """For the edit view of a record: put it first, before the form view it changes."""

    request: HttpRequest

    @property
    def autosaving(self) -> bool:
        return is_autosave(self.request)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context: dict[str, Any] = super().get_context_data(**kwargs)  # type: ignore[misc]
        context["autosave"] = True
        return context

    # ---------- what a view may refine ----------

    def autosave_forms(self, form: Any) -> list[Any]:
        """The forms whose fields are checked and answered: the form, and a formset's forms."""
        return [form]

    def autosave_changed(self, form: Any) -> bool:
        return bool(form.has_changed())

    def autosave_creates(self, form: Any) -> bool:
        cleaned = [getattr(f, "cleaned_data", {}) or {} for f in self.autosave_forms(form)]
        return any(data.get(key) for data in cleaned for key in CREATING_KEYS)

    # ---------- the answers ----------

    def _answer(self, form: Any, status: str, *, failed: bool = False) -> HttpResponse:
        fields = []
        for each in self.autosave_forms(form):
            errors = each.errors if each.is_bound else {}
            for name in each.fields:
                fields.append((each.add_prefix(name), " ".join(errors.get(name, []))))
        general = " ".join(form.non_field_errors()) if form.is_bound else ""
        return render(
            self.request,
            "components/autosave_result.html",
            {"status": status, "failed": failed, "fields": fields, "general": general},
        )

    def form_invalid(self, form: Any) -> HttpResponse:
        if not self.autosaving:
            return super().form_invalid(form)  # type: ignore[misc,no-any-return]
        return self._answer(form, _("Not saved: see the field."), failed=True)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Wrap the `form_valid` of each edit view: the view writes, the wrapper answers."""
        super().__init_subclass__(**kwargs)
        own = cls.__dict__.get("form_valid")
        if own is None or getattr(own, "autosave", False):
            return

        def form_valid(self: "AutosaveMixin", form: Any) -> HttpResponse:
            return self.autosave_valid(form, lambda: own(self, form))

        form_valid.autosave = True  # type: ignore[attr-defined]
        cls.form_valid = form_valid  # type: ignore[attr-defined]

    def autosave_valid(self, form: Any, write: Any) -> HttpResponse:
        if not self.autosaving:
            return write()  # type: ignore[no-any-return]
        if self.autosave_creates(form):
            return self._answer(form, _("Press Save to add the new record."))
        if not self.autosave_changed(form):
            return self._answer(form, _("Saved"))
        response: HttpResponse = write()
        if not isinstance(response, HttpResponseRedirect):
            return response  # refused by a service: the answer of `form_invalid` above
        # The message of the button is for a page that is not shown: it goes.
        list(messages.get_messages(self.request))
        return self._answer(form, _("Saved"))

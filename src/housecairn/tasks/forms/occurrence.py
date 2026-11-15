"""Forms of an occurrence: completion, postponement, attachments."""

from collections.abc import Iterable
from decimal import Decimal
from typing import Any
from uuid import UUID

from django import forms
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from housecairn.core.forms import LabelAboveMixin, MoneyField, QuantityField
from housecairn.tasks import files
from housecairn.tasks.forms.fields import (
    _DATE,
    MultipleFileField,
    MultipleFileInput,
)
from housecairn.tasks.models import MaterialDraw, Occurrence


class CompletionForm(LabelAboveMixin, forms.ModelForm[Occurrence]):
    """The details of a task that is done: when, what it cost, a note."""

    actual_cost = MoneyField(label=_("Actual cost"), required=False)

    class Meta:
        model = Occurrence
        fields = ["completed_on", "actual_cost", "completion_note"]
        widgets = {
            "completed_on": _DATE,
            "completion_note": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {"completion_note": _("Note")}

    USED_PREFIX = "used_"

    def __init__(self, *args: Any, draws: Iterable[MaterialDraw] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # One field per stock item the closing took: the quantity really used.
        for draw in draws:
            name = f"{self.USED_PREFIX}{draw.article_id}"
            label = gettext("Used: %(name)s") % {"name": draw.article.name}
            if draw.article.unit_label:
                label = f"{label} ({draw.article.unit_label})"
            self.fields[name] = QuantityField(
                label=label,
                min_value=Decimal("0"),
                initial=draw.wanted,
                required=False,
                help_text=gettext("Taken from the stock when the task was marked done.")
                if not self.used_fields()
                else "",
            )

    def used_fields(self) -> list[str]:
        return [name for name in self.fields if name.startswith(self.USED_PREFIX)]

    def used(self) -> dict[UUID, Decimal]:
        """The quantities entered for the stock items, by article; an empty field is left alone."""
        found = {}
        for name in self.used_fields():
            value = self.cleaned_data.get(name)
            if value is not None:
                found[UUID(name.removeprefix(self.USED_PREFIX))] = value
        return found

    def completion(self) -> dict[str, Any]:
        """The details of the occurrence itself, without the stock items."""
        return {k: v for k, v in self.cleaned_data.items() if k not in self.used_fields()}


class PostponeForm(LabelAboveMixin, forms.Form):
    due_on = forms.DateField(label=_("New date"), widget=_DATE)


class AttachmentForm(LabelAboveMixin, forms.Form):
    uploads = MultipleFileField(
        label=_("Photos and files"),
        widget=MultipleFileInput(attrs={"accept": files.ACCEPT_ATTRIBUTE}),
    )

    def clean(self) -> dict[str, Any]:
        data: dict[str, Any] = super().clean() or {}
        if not data.get("uploads") and "uploads" not in self.errors:
            self.add_error("uploads", _("Choose at least one file."))
        return data

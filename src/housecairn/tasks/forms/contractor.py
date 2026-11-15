"""The contractor form."""

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from housecairn.core.forms import LabelAboveMixin
from housecairn.tasks import services
from housecairn.tasks.forms.fields import NEW
from housecairn.tasks.models import (
    Contractor,
    Trade,
)


class ContractorForm(LabelAboveMixin, forms.ModelForm[Contractor]):
    """A contractor: only the name is required (docs/adr/0045)."""

    class Meta:
        model = Contractor
        fields = ["name", "trade", "trade_other", "phone", "email", "address", "note"]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "note": forms.Textarea(attrs={"rows": 3}),
            "phone": forms.TextInput(attrs={"type": "tel", "autocomplete": "off"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        trade = self.fields["trade"]
        assert isinstance(trade, forms.ChoiceField)  # noqa: S101 - narrows the type
        trade.choices = [("", "—"), *Trade.choices]
        self.fields["trade"].required = False


def new_contractor_fields() -> dict[str, forms.Field]:
    """The fields of a contractor added in place, read only with the choice Add a contractor….

    Shared by every form that picks a contractor (the task, the maintenance contract of an
    item), so that they ask the same things in the same words.
    """
    return {
        "new_contractor_name": forms.CharField(
            label=_("Name of the new contractor"), max_length=120, required=False
        ),
        "new_contractor_trade": forms.ChoiceField(
            label=_("Trade"), choices=[("", "—"), *Trade.choices], required=False
        ),
        "new_contractor_trade_other": forms.CharField(
            label=_("Which trade"), max_length=120, required=False
        ),
    }


def clean_new_contractor(
    form: forms.BaseForm, data: dict[str, Any], home: Any, *, field: str
) -> None:
    """Add a contractor… chosen in `field`: the fields of the new one, checked.

    `new_contractor` ends up in `data` holding what to create, made by the view with the
    record being saved; it is None when the choice was not made. A name already in the
    home, whatever its case, is refused: that one is in the list.
    """
    name = " ".join((data.pop("new_contractor_name", "") or "").split())
    trade = data.pop("new_contractor_trade", "") or ""
    other = (data.pop("new_contractor_trade_other", "") or "").strip()
    data["new_contractor"] = None
    if data.get(field) != NEW:
        return
    data[field] = None
    if not name:
        form.add_error("new_contractor_name", _("Give the name of the new contractor."))
        return
    if any(c.name.casefold() == name.casefold() for c in services.contractors_of(home)):
        form.add_error(
            "new_contractor_name",
            _("%(name)s is already a contractor of this home: choose it in the list.")
            % {"name": name},
        )
        return
    if trade == Trade.OTHER and not other:
        form.add_error("new_contractor_trade_other", _("Say which trade."))
        return
    data["new_contractor"] = {"name": name, "trade": trade, "trade_other": other}

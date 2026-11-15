"""Form of Add from a template (docs/adr/0049). Validation only; the tasks are made in services."""

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from housecairn.core.forms import LabelAboveMixin
from housecairn.homes.models import Home
from housecairn.rooms.models import Room
from housecairn.tasks import task_templates


def template_choices() -> list[tuple[str, list[tuple[str, str]]]]:
    """The templates grouped by equipment, in the language of the request."""
    found = task_templates.library()
    return [
        (
            equipment.name.get(),
            [(t.id, t.title.get()) for t in found.of_equipment(equipment.id)],
        )
        for equipment in found.equipment
    ]


class TemplateForm(LabelAboveMixin, forms.Form):
    templates = forms.MultipleChoiceField(
        label=_("Templates"),
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": _("Choose at least one template.")},
    )
    room = forms.ModelChoiceField(
        label=_("Room"), queryset=Room.objects.none(), required=False, empty_label=_("No room")
    )
    due_on = forms.DateField(
        label=_("First date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        help_text=_("Left empty, the tasks have no date until you give them one."),
    )

    def __init__(self, *args: Any, home: Home, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["templates"].choices = template_choices()  # type: ignore[attr-defined]
        self.fields["room"].queryset = Room.objects.filter(home=home)  # type: ignore[attr-defined]


def equipment_field() -> forms.MultipleChoiceField:
    """The equipment checked when a home is created: each adds the tasks of its templates."""
    return forms.MultipleChoiceField(
        label=_("What the home has"),
        help_text=_(
            "Each one checked adds its usual maintenance tasks. You can change them later."
        ),
        choices=[(e.id, e.name.get()) for e in task_templates.library().equipment],
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

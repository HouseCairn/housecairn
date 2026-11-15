"""The display preferences form."""

from typing import Any

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from housecairn.core.forms import LabelAboveMixin
from housecairn.tasks.models import (
    DisplayPreference,
)


class PreferencesForm(LabelAboveMixin, forms.ModelForm[DisplayPreference]):
    """The Today screen, as one person wants it."""

    class Meta:
        model = DisplayPreference
        fields = [
            "today_group",
            "horizon_days",
            "show_overdue",
            "show_upcoming",
            "show_season",
            "language",
        ]
        widgets = {"today_group": forms.RadioSelect}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["language"] = forms.ChoiceField(
            label=_("Language"),
            required=False,
            choices=[("", _("Automatic (browser, then instance default)")), *settings.LANGUAGES],
            help_text=_("Applies to every page you open, on every device you sign in from."),
        )

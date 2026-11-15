"""Forms of the instance screens. Validation only; rules live in `services`."""

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from housecairn.core.forms import LabelAboveMixin
from housecairn.instance import config, registry


class InstanceInviteForm(LabelAboveMixin, forms.Form):
    email = forms.EmailField(label=_("Email address"), max_length=254)


class ProviderForm(LabelAboveMixin, forms.Form):
    """A sign-in provider of the interface (docs/adr/0041). The rules are in `providers`."""

    slug = forms.CharField(
        label=_("Identifier"),
        max_length=32,
        help_text=_(
            "In the redirect URI; lowercase letters, digits and hyphens. It never changes "
            "and is never given to another provider."
        ),
    )
    name = forms.CharField(
        label=_("Name"), max_length=64, help_text=_("On the button, such as Company SSO.")
    )
    issuer = forms.CharField(
        label=_("Issuer"),
        max_length=255,
        help_text=_("https:// address; its OpenID configuration is read below it."),
    )
    client_id = forms.CharField(label=_("Client identifier"), max_length=255)
    secret = forms.CharField(
        label=_("Client secret"),
        required=False,
        max_length=1024,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text=_(
            "Write only: it is never shown again. Empty: unchanged, but a new issuer clears it."
        ),
    )
    # A box has its label beside it, without a colon, as on the settings screen.
    pkce = forms.BooleanField(
        label=_("PKCE"),
        label_suffix="",
        required=False,
        initial=True,
        help_text=_("Recommended; on by default."),
    )
    enabled = forms.BooleanField(
        label=_("Enabled"),
        label_suffix="",
        required=False,
        help_text=_("Shown on the sign-in page. Test the issuer before you enable it."),
    )

    def __init__(self, *args: Any, editing: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if editing:
            del self.fields["slug"]

    def data_for_service(self) -> dict[str, Any]:
        """The values to save; an empty secret means unchanged."""
        data = dict(self.cleaned_data)
        if not data.get("secret"):
            data.pop("secret", None)
        return data


def _field(setting: registry.Setting) -> forms.Field:
    """The form field of an editable setting."""
    common: dict[str, Any] = {
        "label": setting.label,
        "help_text": setting.help,
        "required": False,
    }
    kind = setting.kind
    if kind is registry.Kind.BOOL:
        return forms.BooleanField(label_suffix="", **common)
    if kind is registry.Kind.INT:
        return forms.IntegerField(
            min_value=setting.minimum, max_value=setting.maximum, **{**common, "required": True}
        )
    if kind is registry.Kind.CHOICE:
        return forms.ChoiceField(choices=[(c, c) for c in setting.choices], **common)
    if kind is registry.Kind.SECRET:
        return forms.CharField(
            widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
            max_length=1024,
            **common,
        )
    return forms.CharField(max_length=1024, **common)


class SettingsSectionForm(LabelAboveMixin, forms.Form):
    """The fields of one section that the environment does not set.

    A setting that a variable sets is not a field: the screen shows its value and the
    variable. Only what changed is sent on (`changes`).
    """

    def __init__(self, section: registry.Section, *args: Any, **kwargs: Any) -> None:
        self.section = section
        super().__init__(*args, **kwargs)
        self.states: dict[str, config.State] = {}
        for setting in registry.in_section(section):
            state = config.describe(setting.key)
            self.states[setting.key] = state
            if state.locked:
                continue
            field = _field(setting)
            if state.value is not None:
                field.initial = (
                    ", ".join(state.value) if isinstance(state.value, list) else state.value
                )
            self.fields[setting.key] = field

    def changes(self) -> dict[str, Any]:
        """New values of the fields that were changed. An empty secret means unchanged."""
        out: dict[str, Any] = {}
        for key in self.changed_data:
            value = self.cleaned_data[key]
            if registry.SETTINGS[key].secret and not value:
                continue
            out[key] = value
        return out

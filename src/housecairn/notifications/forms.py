from django import forms
from django.utils.translation import gettext_lazy as _

from housecairn.notifications.models import Preference

_TIME = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


class ChannelForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=60)
    # Never rendered back: the address carries the secrets of the channel.
    url = forms.CharField(
        label=_("Apprise address"),
        max_length=2000,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        help_text=_("Such as ntfys://ntfy.sh/my-topic or tgram://123456789:bot-token/111222333."),
    )


class ChannelEditForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=60)
    enabled = forms.BooleanField(label=_("Enabled"), required=False)


class PreferenceForm(forms.ModelForm[Preference]):
    class Meta:
        model = Preference
        fields = [
            "days_before",
            "send_at",
            "quiet_start",
            "quiet_end",
            "digest",
            "time_zone",
        ]
        widgets = {"send_at": _TIME, "quiet_start": _TIME, "quiet_end": _TIME}


class DeviceForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=60)

"""The form of a new API token."""

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from oauth2_provider.forms import AllowForm

from housecairn.accounts.models import User
from housecairn.apitokens import services
from housecairn.apitokens.models import Scope
from housecairn.core.forms import LabelAboveMixin
from housecairn.homes.models import Home
from housecairn.homes.sso import Provider


class HomeChoiceField(forms.ModelMultipleChoiceField[Home]):
    def label_from_instance(self, obj: Home) -> str:
        return obj.name


class HomesWidget(forms.CheckboxSelectMultiple):
    """Boxes of homes; one this session cannot bind is disabled and names its provider."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Home id: the provider it requires; `declared`: the providers this instance offers.
        self.locked: dict[str, Provider] = {}
        self.declared: set[str] = set()

    def create_option(self, name: str, value: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        option: dict[str, Any] = super().create_option(name, value, *args, **kwargs)
        provider = self.locked.get(str(value))
        option["provider"] = provider
        option["provider_declared"] = provider is not None and provider.id in self.declared
        if provider is not None:
            attrs = {key: v for key, v in option["attrs"].items() if key != "checked"}
            attrs["disabled"] = True
            attrs["aria-describedby"] = f"{attrs.get('id', name)}_locked"
            option["attrs"] = attrs
            option["selected"] = False
        return option


def _lifetime_label(days: int) -> str:
    """Worded in the language of the request: called when the form is built, not at import."""
    if days % 365 == 0:
        return ngettext("%(n)d year", "%(n)d years", days // 365) % {"n": days // 365}
    return ngettext("%(n)d day", "%(n)d days", days) % {"n": days}


class TokenForm(LabelAboveMixin, forms.Form):
    name = forms.CharField(
        label=_("Name"),
        max_length=100,
        help_text=_("What the token is for, for example Home Assistant."),
    )
    scopes = forms.MultipleChoiceField(
        label=_("What it may do"),
        choices=[
            (Scope.READ.value, _("Read: see the homes, tasks and stock")),
            (Scope.WRITE.value, _("Write: change them, mark a task done")),
            (Scope.EVENTS.value, _("Post events: receive events from another tool")),
        ],
        initial=[Scope.READ.value],
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Write does not include read: tick both for an integration that does both."),
    )
    homes = HomeChoiceField(
        label=_("Homes"),
        queryset=Home.objects.none(),
        widget=HomesWidget,
        help_text=_("The token opens these homes and no other."),
    )
    lifetime_days = forms.TypedChoiceField(
        label=_("Expires after"),
        coerce=int,
        choices=[],
        widget=forms.RadioSelect,
        help_text=_("There is no token without an expiry: you create a new one before it ends."),
    )

    def __init__(
        self,
        *args: Any,
        user: User,
        signed_in_with: frozenset[str],
        providers: list[Provider],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        # Every home of the person is listed. One that requires a provider this session did
        # not sign in through is shown disabled, with the provider that unlocks it; the
        # service refuses it if a forged request sends it anyway (docs/adr/0057).
        homes = list(services.token_homes(user))
        issuable = {home.id for home in services.issuable_homes(user, signed_in_with)}
        declared = {provider.id: provider for provider in providers}
        locked = {
            str(home.id): declared.get(home.required_provider)
            or Provider(id=home.required_provider, name=home.required_provider)
            for home in homes
            if home.id not in issuable
        }
        self.fields["homes"].queryset = services.token_homes(user)  # type: ignore[attr-defined]
        widget = self.fields["homes"].widget
        assert isinstance(widget, HomesWidget)  # noqa: S101 - declared above
        widget.locked = locked
        widget.declared = set(declared)
        if not self.is_bound and len(issuable) == 1:
            self.fields["homes"].initial = [home for home in homes if home.id in issuable]
        # The declared providers that would unlock a disabled home, once each: the page
        # offers to continue with each of them.
        self.sign_in_providers = list(
            {p.id: p for p in locked.values() if p.id in declared}.values()
        )
        lifetime = self.fields["lifetime_days"]
        lifetime.choices = [  # type: ignore[attr-defined]
            (days, _lifetime_label(days)) for days in services.LIFETIME_CHOICES
        ]
        lifetime.initial = services.default_lifetime_days()

    def clean_name(self) -> str:
        return " ".join(self.cleaned_data["name"].split())


# ---------- connected apps (docs/adr/0078) ----------


class ConnectionHomesMixin(LabelAboveMixin, forms.Form):
    """The homes of a connection, with the rules of the token form."""

    homes = HomeChoiceField(
        label=_("Homes"),
        queryset=Home.objects.none(),
        widget=HomesWidget,
        help_text=_("The phone sees and changes these homes, and no other."),
    )
    may_create_homes = forms.BooleanField(
        label=_("May create homes"),
        required=False,
        help_text=_("The phone may send its own homes to this server, which adds them here."),
    )

    def setup_homes(
        self, user: User, signed_in_with: frozenset[str], providers: list[Provider]
    ) -> None:
        homes = list(services.token_homes(user))
        issuable = {home.id for home in services.issuable_homes(user, signed_in_with)}
        declared = {provider.id: provider for provider in providers}
        locked = {
            str(home.id): declared.get(home.required_provider)
            or Provider(id=home.required_provider, name=home.required_provider)
            for home in homes
            if home.id not in issuable
        }
        self.fields["homes"].queryset = services.token_homes(user)  # type: ignore[attr-defined]
        widget = self.fields["homes"].widget
        assert isinstance(widget, HomesWidget)  # noqa: S101 - declared above
        widget.locked = locked
        widget.declared = set(declared)
        if not self.is_bound and len(issuable) == 1 and "homes" not in self.initial:
            self.initial["homes"] = [home for home in homes if home.id in issuable]
        self.sign_in_providers = list(
            {p.id: p for p in locked.values() if p.id in declared}.values()
        )


class ConsentForm(AllowForm, ConnectionHomesMixin):  # type: ignore[misc]
    """The consent page: the hidden fields of django-oauth-toolkit, the homes, the right."""

    # The name the phone gave, carried from the page to its answer: a label only.
    device_name = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(
        self,
        *args: Any,
        user: User,
        signed_in_with: frozenset[str],
        providers: list[Provider],
        asks_homes_create: bool,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.setup_homes(user, signed_in_with, providers)
        if asks_homes_create:
            self.fields["may_create_homes"].initial = True
        else:
            del self.fields["may_create_homes"]

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        # Cancel needs no home; Connect does.
        if not cleaned.get("allow"):
            self.errors.pop("homes", None)
        return cleaned


class ConnectionForm(ConnectionHomesMixin):
    name = forms.CharField(
        label=_("Name"),
        max_length=100,
        required=False,
        help_text=_("How you recognise this phone in the list."),
    )
    field_order = ["name", "homes", "may_create_homes"]

    def __init__(
        self,
        *args: Any,
        user: User,
        signed_in_with: frozenset[str],
        providers: list[Provider],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.setup_homes(user, signed_in_with, providers)

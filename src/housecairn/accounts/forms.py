"""allauth form overrides (docs/adr/0018)."""

from typing import Any

from allauth.account.forms import ChangePasswordForm as AllauthChangePasswordForm
from allauth.account.forms import LoginForm as AllauthLoginForm
from allauth.account.forms import ReauthenticateForm as AllauthReauthenticateForm
from allauth.account.forms import SignupForm as AllauthSignupForm
from django import forms
from django.conf import settings
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from housecairn.accounts import antibot, names, services
from housecairn.core.forms import LabelAboveMixin
from housecairn.homes.scoping import allow


class LoginForm(LabelAboveMixin, AllauthLoginForm):  # type: ignore[misc]
    """Sign-in form whose address field also offers the browser's passkeys.

    The `webauthn` autofill token (and nothing else) is what lets a browser list
    the passkeys saved for this site in the address field's suggestion list; the
    script `js/passkey-login.js` completes the request.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if settings.MFA_PASSKEY_LOGIN_ENABLED:
            self.fields["login"].widget.attrs["autocomplete"] = "username webauthn"
        if not services.email_is_configured():
            # allauth links "Forgot your password?" under the field; the reset
            # mail could not be sent, so the link would lead nowhere useful.
            self.fields["password"].help_text = ""


class ChangePasswordForm(LabelAboveMixin, AllauthChangePasswordForm):  # type: ignore[misc]
    """Password change form without the reset link on an instance that cannot send mail.

    allauth puts "Forgot your password?" under the current password; without outgoing
    mail the reset page does not exist (404), as for `LoginForm`.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not services.email_is_configured():
            self.fields["oldpassword"].help_text = ""


class ReauthenticateForm(LabelAboveMixin, AllauthReauthenticateForm):  # type: ignore[misc]
    """Password confirmation form without the reset link on an instance that cannot send mail.

    The page is reached before sensitive actions (instance settings, for example);
    its field carries the same "Forgot your password?" link as the sign-in form.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not services.email_is_configured():
            self.fields["password"].help_text = ""


class ProfileForm(LabelAboveMixin, forms.Form):
    """The only thing a person edits of their profile: how they are named (docs/adr/0045)."""

    display_name = forms.CharField(
        label=_("Display name"),
        max_length=names.DISPLAY_NAME_MAX_LENGTH,
        required=False,
        help_text=_(
            "How the other members of your homes see you, instead of your address. Optional."
        ),
    )


class DeleteAccountForm(LabelAboveMixin, forms.Form):
    """Confirmation of the erasure of an account: the address, and the password if there is one."""

    confirm_email = forms.EmailField(label=_("Type your email address to confirm"))
    password = forms.CharField(
        label=_("Your password"),
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def __init__(self, *args: Any, user: Any, request: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.user = user
        self.request = request
        if not user.has_usable_password():
            # An account that signs in through a provider has no password to ask for.
            del self.fields["password"]

    def clean(self) -> dict[str, Any]:
        data: dict[str, Any] = super().clean() or {}
        # Every attempt counts, before the password is looked at: a wrong guess is
        # what the limit is for.
        if not allow(self.request, "housecairn_delete_account"):
            raise forms.ValidationError(_("Too many attempts. Try again later."), code="throttled")
        email = data.get("confirm_email")
        if email and not services.same_address(email, self.user.email):
            self.add_error("confirm_email", _("This is not the address of your account."))
        if "password" in self.fields and not self.user.check_password(data.get("password") or ""):
            self.add_error("password", _("The password is not correct."))
        return data


class SignupForm(LabelAboveMixin, AllauthSignupForm):  # type: ignore[misc]
    """Open sign-up: the allowed and blocked domains, and the proof of work (docs/adr/0024)."""

    pow_solution = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args: Any, request: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.request = request
        if request is not None and not self.is_bound:
            self._arm(antibot.issue(request))

    def renew_challenge(self) -> None:
        """A new proof of work for the page that comes back, the session having changed."""
        if self.request is not None:
            self._arm(antibot.issue(self.request))

    def _arm(self, challenge: dict[str, Any]) -> None:
        """Hand the challenge to the script that solves it, through the hidden field."""
        self.fields["pow_solution"].widget.attrs.update(
            {"data-challenge": challenge["challenge"], "data-bits": str(challenge["bits"])}
        )

    def clean_email(self) -> str:
        value: str = super().clean_email()
        if value and not services.signup_domain_allowed(value):
            raise forms.ValidationError(
                _("Sign-up is not open to addresses of this domain."), code="domain"
            )
        return value

    def clean(self) -> dict[str, Any]:
        data: dict[str, Any] = super().clean()
        if self.request is not None:
            solved = antibot.check(self.request, data.get("pow_solution") or "")
            # Used up, solved or not: the page that comes back has a new one.
            self._arm(antibot.issue(self.request))
            if not solved:
                self.add_error(
                    None,
                    _(
                        "The check that you are not a robot did not complete. "
                        "Wait a few seconds for it on the page, with JavaScript on, then try again."
                    ),
                )
        return data


class _CodeInput(forms.TextInput):
    """The code field never writes its value back into the page, whatever happened."""

    def format_value(self, value: Any) -> None:
        return None


class SetupForm(LabelAboveMixin, forms.Form):
    """The first-account screen: the code of the server log, then the administrator (ADR 0056)."""

    code = forms.CharField(
        label=_("Setup code"),
        max_length=64,
        strip=True,
        widget=_CodeInput(
            attrs={
                "autocomplete": "off",
                "autocapitalize": "characters",
                "spellcheck": "false",
                "inputmode": "text",
            }
        ),
        help_text=_("Written in the log of HouseCairn when it started."),
    )
    name = forms.CharField(
        label=_("Your name"),
        max_length=names.DISPLAY_NAME_MAX_LENGTH,
        help_text=_("How the members of your homes will see you."),
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    email = forms.EmailField(
        label=_("Email address"),
        widget=forms.EmailInput(attrs={"autocomplete": "username"}),
    )
    password1 = forms.CharField(
        label=_("Password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label=_("Password (again)"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean_name(self) -> str:
        name = services.clean_display_name(self.cleaned_data["name"])
        if not name:
            raise ValidationError(_("This field is required."), code="required")
        return name

    def clean(self) -> dict[str, Any]:
        data: dict[str, Any] = super().clean() or {}
        first, second = data.get("password1"), data.get("password2")
        if first and second and first != second:
            self.add_error("password2", _("The two passwords differ."))
        elif first:
            from housecairn.accounts.models import User

            candidate = User(email=data.get("email") or "", display_name=data.get("name") or "")
            try:
                password_validation.validate_password(first, user=candidate)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return data

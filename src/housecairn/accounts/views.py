"""Guards on allauth views that need outgoing email (docs/adr/0007)."""

from collections.abc import Callable
from functools import wraps
from typing import Any

from allauth.account import views as allauth_views
from allauth.account.adapter import get_adapter
from allauth.core import ratelimit
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as django_login
from django.contrib.auth import logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import FormView, View

from housecairn.accounts import forms, provider_names, services, setup
from housecairn.accounts.adapters import MailDeliveryError
from housecairn.accounts.models import DisplayNameSource, User
from housecairn.core.language import chosen_language
from housecairn.homes.scoping import allow


def needs_email(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Answer 404 when the instance cannot send email, otherwise run the view."""

    @wraps(view)
    def guarded(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not services.email_is_configured():
            raise Http404("Outgoing email is not configured.")
        return view(request, *args, **kwargs)

    return guarded


class LoginView(allauth_views.LoginView):  # type: ignore[misc]
    """allauth's sign-in page, which opens the session before the page can ask for anything.

    The page fetches a passkey challenge as soon as it loads (docs/adr/0018), and that
    request writes the challenge in the session. Without a session yet, it would create
    one: if the person signs in with a password before the challenge answers, the late
    answer would hand the browser that new, signed-out session instead of the one of the
    sign-in (tests/test_session_race.py). With the session opened here, the late answer
    finds it replaced by the sign-in and sets no cookie.
    """

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if request.session.session_key is None:
            request.session.create()
        response: HttpResponse = super().get(request, *args, **kwargs)
        return response

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context: dict[str, Any] = super().get_context_data(**kwargs)
        context["setup_available"] = setup.is_available()
        return context


class EmailView(allauth_views.EmailView):  # type: ignore[misc]
    """allauth's address page, where only a verified address can become primary.

    The rule is the adapter's (`AccountAdapter.can_mark_as_primary`, docs/adr/0020).
    """

    def _action_primary(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        address = self._get_email_address(request)
        adapter = get_adapter(request)
        if address is not None and not adapter.can_mark_as_primary(address):
            adapter.add_message(
                request, messages.ERROR, "account/messages/unverified_primary_email.txt"
            )
            return None
        return super()._action_primary(request, *args, **kwargs)


login = LoginView.as_view()
email_addresses = needs_email(EmailView.as_view())
password_reset = needs_email(allauth_views.password_reset)
password_reset_done = needs_email(allauth_views.password_reset_done)


class AccountDeleteView(LoginRequiredMixin, FormView[forms.DeleteAccountForm]):
    """Erase the account: the right to erasure, with the last-owner rule (docs/adr/0023)."""

    template_name = "accounts/delete.html"
    form_class = forms.DeleteAccountForm

    def get_form_kwargs(self) -> dict[str, Any]:
        return {**super().get_form_kwargs(), "user": self.request.user, "request": self.request}

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["blocking"] = services.homes_blocking_erasure(user)  # type: ignore[arg-type]
        context["last_admin"] = services.is_last_instance_admin(user)  # type: ignore[arg-type]
        return context

    def form_valid(self, form: forms.DeleteAccountForm) -> HttpResponse:
        try:
            services.delete_account(self.request.user)  # type: ignore[arg-type]
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)
        logout(self.request)
        messages.success(self.request, _("Your account was deleted."))
        return redirect("account_login")


class ProfileView(LoginRequiredMixin, FormView[forms.ProfileForm]):
    """How the person is named to the others (docs/adr/0045)."""

    template_name = "accounts/profile.html"
    form_class = forms.ProfileForm

    def get_initial(self) -> dict[str, Any]:
        return {"display_name": self.request.user.display_name}  # type: ignore[union-attr]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from housecairn.instance.providers import provider_label

        context = super().get_context_data(**kwargs)
        user: User = self.request.user  # type: ignore[assignment]
        by_provider = user.display_name_source == DisplayNameSource.OIDC
        context["linked"] = provider_names.has_provider(user)
        # The language is set in the display preferences; the profile says which one applies
        # and leads there (docs/adr/0074).
        chosen = chosen_language(user.pk)
        context["language_name"] = dict(settings.LANGUAGES).get(chosen, "")
        context["by_provider"] = by_provider
        context["taken_from"] = (
            provider_label(user.display_name_provider)
            if by_provider and user.display_name_provider and user.display_name
            else ""
        )
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if "use_provider" not in request.POST:
            return super().post(request, *args, **kwargs)
        user: User = request.user  # type: ignore[assignment]
        if not provider_names.has_provider(user):
            return redirect("profile")
        if provider_names.use_provider_name(user):
            messages.success(request, _("Your display name is now the one of your provider."))
        else:
            messages.success(
                request,
                _("Your display name will be taken from your provider at your next sign-in."),
            )
        return redirect("profile")

    def form_valid(self, form: forms.ProfileForm) -> HttpResponse:
        try:
            services.set_display_name(
                self.request.user,  # type: ignore[arg-type]
                form.cleaned_data["display_name"],
            )
        except ValidationError as exc:
            form.add_error("display_name", exc)
            return self.form_invalid(form)
        messages.success(self.request, _("Profile saved."))
        return redirect("profile")


class SignupView(allauth_views.SignupView):  # type: ignore[misc]
    """allauth's sign-up page, whose form also gets the request (for the proof of work)."""

    def get_form_kwargs(self) -> dict[str, Any]:
        return {**super().get_form_kwargs(), "request": self.request}

    def form_valid(self, form: forms.SignupForm) -> HttpResponse:
        """Create the account and send its verification, or neither.

        allauth saves the account, then sends the message. A mail server that does not answer,
        or refuses, must not leave an account without its message (nor a server error): the
        whole sign-up is undone, the session with it (it may already hold the sign-in or the
        pending verification of an account that no longer exists), and the form comes back
        with the reason, and the address free to be tried again.
        """
        try:
            with transaction.atomic():
                response: HttpResponse = super().form_valid(form)
                return response
        except MailDeliveryError:
            # allauth spends the address's one verification message per cooldown before it
            # sends: without this, signing up again would create the account and skip the mail.
            ratelimit.clear(
                self.request, action="confirm_email", key=form.cleaned_data["email"].lower()
            )
            self.request.session.flush()
            form.renew_challenge()
            form.add_error(
                None,
                _(
                    "The confirmation email could not be sent, so the account was not "
                    "created. Try again later, or ask the administrator to check the "
                    "outgoing mail settings."
                ),
            )
            return self.form_invalid(form)  # type: ignore[no-any-return]


class SetupView(View):
    """Create the first administrator with the code of the process log (docs/adr/0056).

    A page for whoever has no account yet: 404 once an administrator exists or when the
    screen is switched off. Every request counts against a limit per client address, the
    code is never written back into a page, and a wrong, expired or used code gets the
    same answer.
    """

    http_method_names = ["get", "post"]

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not setup.is_available():
            raise Http404
        if not allow(request, "housecairn_setup"):
            response: HttpResponse = TemplateResponse(request, "429.html", status=429).render()
            return response
        result: HttpResponse = super().dispatch(request, *args, **kwargs)  # type: ignore[assignment]
        return result

    def render(self, form: forms.SetupForm, status: int = 200) -> HttpResponse:
        response: HttpResponse = TemplateResponse(
            self.request,
            "accounts/setup.html",
            {"form": form},
            status=status,
        ).render()
        # Neither a cache nor the browser's history keeps this page.
        response["Cache-Control"] = "no-store"
        return response

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return self.render(forms.SetupForm())

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = forms.SetupForm(request.POST)
        if not form.is_valid():
            return self.render(form, status=400)
        data = form.cleaned_data
        try:
            user = setup.create_first_admin(
                code=data["code"],
                email=data["email"],
                name=data["name"],
                password=data["password1"],
            )
        except setup.SetupCodeInvalidError:
            form.add_error(
                "code",
                _(
                    "This code is not valid: it is wrong, it has expired or it was used. "
                    "Restart HouseCairn, or run “manage setup_code”, to get a new one."
                ),
            )
            return self.render(form, status=400)
        except setup.SetupClosedError as exc:
            raise Http404 from exc
        except ValidationError as exc:
            form.add_error("email", exc)
            return self.render(form, status=400)
        django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, _("Welcome to HouseCairn. Your administrator account is ready."))
        return redirect(reverse("instance:overview"))

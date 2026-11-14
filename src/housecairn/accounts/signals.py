"""Keeps `InstanceSetup` in step with accounts created outside OIDC sign-in, and takes the
display name from the sign-in provider (docs/adr/0045)."""

from typing import Any

from allauth.account.signals import user_logged_in as allauth_logged_in
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.http import HttpRequest

from housecairn.accounts import services
from housecairn.accounts.models import User
from housecairn.core.language import activate_for


@receiver(post_save, sender=User, dispatch_uid="housecairn.accounts.record_first_account")
def record_first_account(sender: type[User], instance: User, created: bool, **kwargs: Any) -> None:
    if created and not kwargs.get("raw", False):
        services.record_first_account(instance)


@receiver(user_logged_in, dispatch_uid="housecairn.accounts.note_sign_in")
def note_sign_in(sender: Any, request: HttpRequest | None, user: User, **kwargs: Any) -> None:
    if request is not None:
        # The sign-in request was anonymous until now: its message ("Signed in as...") and
        # the page it leads to take the person's language (docs/adr/0073).
        activate_for(request, user)
        secret = services.note_sign_in(request, user)
        if secret:
            # The middleware sets the cookie on the response of this request.
            request.housecairn_new_device = secret  # type: ignore[attr-defined]


@receiver(allauth_logged_in, dispatch_uid="housecairn.accounts.name_from_provider")
def name_from_provider(sender: Any, request: HttpRequest | None, user: Any, **kwargs: Any) -> None:
    # allauth passes the sign-in of a provider for a sign-in or sign-up through one only.
    sociallogin = kwargs.get("sociallogin")
    if sociallogin is not None:
        from housecairn.accounts import provider_names

        provider_names.on_sign_in(user, sociallogin)

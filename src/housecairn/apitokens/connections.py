"""Connected apps: the phones of an account, each revocable on its own (docs/adr/0078)."""

import logging
from collections.abc import Iterable
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _
from oauth2_provider.models import AccessToken, Grant, RefreshToken

from housecairn.accounts.models import User
from housecairn.apitokens.models import (
    CONNECTION_NAME_MAX_LENGTH,
    AppConnection,
    ConnectionAccessToken,
    ConnectionGrant,
    ConnectionRefreshToken,
)
from housecairn.audit import services as audit
from housecairn.core.text import without_control_characters
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.instance.config import get_setting

mail_logger = logging.getLogger("housecairn.mail")


def mobile_app_enabled() -> bool:
    """The instance setting Mobile app, on by default."""
    return bool(get_setting("mobile_app_enabled"))


def clean_name(value: str) -> str:
    """The phone's name as a label: one line, no control or format character, 60 at most."""
    return " ".join(without_control_characters(value).split())[:CONNECTION_NAME_MAX_LENGTH]


def connections_of(user: User) -> QuerySet[AppConnection]:
    """The connections of a person that have not been revoked, newest first."""
    return (
        AppConnection.objects.filter(user=user, revoked_at__isnull=True)
        .prefetch_related("homes")
        .order_by("-created_at")
    )


def _check_homes(
    user: User, homes: Iterable[Home], signed_in_with: frozenset[str]
) -> tuple[list[Home], str]:
    """The homes as the token form checks them: the person's, open, one provider at most."""
    mine = {home.id for home in home_services.homes_for(user)}
    home_list = list(homes)
    if not home_list:
        raise ValidationError(_("Choose at least one home."))
    if any(home.id not in mine for home in home_list):
        raise ValidationError(_("Choose one of your homes."))
    for home in home_list:
        home_services.check_open(home, signed_in_with)
    required = {home.required_provider for home in home_list if home.required_provider}
    if len(required) > 1:
        raise ValidationError(
            _(
                "These homes require different sign-in providers: connect the phone with the "
                "homes of one provider."
            )
        )
    return home_list, required.pop() if required else ""


def create_connection(
    user: User,
    *,
    name: str,
    homes: Iterable[Home],
    may_create_homes: bool,
    signed_in_with: frozenset[str],
) -> AppConnection:
    """The connection a consent makes, with its audit entry.

    The security email waits for oauthlib's own checks: the consent page sends it with
    `send_security_email` once a code was given for the connection.
    """
    home_list, provider = _check_homes(user, homes, signed_in_with)
    with transaction.atomic():
        connection = AppConnection.objects.create(
            user=user,
            name=clean_name(name),
            may_create_homes=may_create_homes,
            signed_in_provider=provider,
        )
        connection.homes.set(home_list)
        audit.record("appconnection.created", actor=user, title=connection.name or _("Mobile app"))
    made: AppConnection = connection
    return made


def send_security_email(request: HttpRequest, user: User) -> None:
    """Tell the person by email that a phone was connected to their account."""
    from allauth.account.adapter import get_adapter

    from housecairn.accounts.services import email_is_configured

    if not email_is_configured() or not user.email:
        return
    try:
        get_adapter(request).send_notification_mail(
            "account/email/phone_connected", user, email=user.email
        )
    except Exception as exc:
        mail_logger.warning("connection alert not sent: %s", type(exc).__name__)


def update_connection(
    user: User,
    connection: AppConnection,
    *,
    name: str,
    homes: Iterable[Home],
    may_create_homes: bool,
    signed_in_with: frozenset[str],
) -> AppConnection:
    if connection.user_id != user.pk:
        raise ValidationError(_("Choose one of your connected apps."))
    home_list, provider = _check_homes(user, homes, signed_in_with)
    if connection.signed_in_provider and provider and provider != connection.signed_in_provider:
        raise ValidationError(_("These homes require another sign-in provider."))
    with transaction.atomic():
        connection.name = clean_name(name)
        connection.may_create_homes = may_create_homes
        connection.signed_in_provider = provider or connection.signed_in_provider
        connection.save(
            update_fields=["name", "may_create_homes", "signed_in_provider", "updated_at"]
        )
        connection.homes.set(home_list)
        audit.record("appconnection.changed", actor=user, title=connection.name)
    return connection


@transaction.atomic
def revoke(connection: AppConnection, *, actor: User | None = None, reason: str = "") -> None:
    """Revoke a connection and every token of it, in one transaction.

    `reason`: "reuse" when a refresh token came back after its rotation, "app" when the app
    disconnected itself; empty when a person revoked it from the web.
    """
    locked = AppConnection.objects.select_for_update().get(pk=connection.pk)
    refresh_ids = list(
        ConnectionRefreshToken.objects.filter(connection=locked).values_list(
            "refresh_token_id", flat=True
        )
    )
    access_ids = list(
        ConnectionAccessToken.objects.filter(connection=locked).values_list(
            "access_token_id", flat=True
        )
    )
    grant_ids = list(
        ConnectionGrant.objects.filter(connection=locked).values_list("grant_id", flat=True)
    )
    now = timezone.now()
    RefreshToken.objects.filter(pk__in=refresh_ids, revoked__isnull=True).update(revoked=now)
    AccessToken.objects.filter(pk__in=access_ids).delete()
    Grant.objects.filter(pk__in=grant_ids).delete()
    if locked.revoked_at is None:
        locked.revoked_at = now
        locked.save(update_fields=["revoked_at", "updated_at"])
        audit.record(
            "appconnection.revoked",
            actor=actor or locked.user,
            target=None if actor is None or actor.pk == locked.user_id else locked.user,
            title=locked.name or _("Mobile app"),
        )
    connection.revoked_at = locked.revoked_at


def revoke_connection(actor: User, connection: AppConnection) -> None:
    """A person revokes one of their connections from the web."""
    if connection.user_id != actor.pk:
        raise ValidationError(_("Choose one of your connected apps."))
    revoke(connection, actor=actor)


def touch(connection: AppConnection) -> None:
    """Record the last use, at most once a minute."""
    now = timezone.now()
    if connection.last_used_at is None or now - connection.last_used_at > timedelta(minutes=1):
        AppConnection.objects.filter(pk=connection.pk).update(last_used_at=now)

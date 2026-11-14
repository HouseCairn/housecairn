"""Notification channels, reminder preferences and the record of what was sent (docs/adr/0043).

Everything here belongs to one person: no home owns a channel, and nobody else, not even
the administrator of the instance, reads it. The address of a channel (an Apprise URL)
carries its secrets; it is stored encrypted and never shown again.
"""

import datetime

from django.conf import settings
from django.core.validators import MaxValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from housecairn.core.models import BaseModel

MAX_CHANNELS = 10


class SendState(models.TextChoices):
    """Where the last test message of a channel stands (docs/adr/0070)."""

    NONE = "", _("Test never sent")
    RUNNING = "running", _("Test being sent")
    SENT = "sent", _("Test sent")
    FAILED = "failed", _("Test failed")
    PRIVATE = "private", _("Test refused: private network")


class Channel(BaseModel):
    """Where a person receives reminders: one Apprise URL (ntfy, Telegram, email...)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_channels"
    )
    name = models.CharField(_("name"), max_length=60)
    # `instance.crypto` (docs/adr/0035): never read outside `notifications.outbound`.
    url = models.TextField(editable=False)
    # What may be shown of the address: the scheme and the name of the service, never a host
    # (the "host" of a Telegram URL is part of its token).
    scheme = models.CharField(max_length=16, editable=False)
    service = models.CharField(max_length=60, editable=False)
    enabled = models.BooleanField(_("enabled"), default=True)
    # The last test message, sent by the worker (docs/adr/0070). `running` ends in one of the
    # others, at the latest `services.TEST_DEADLINE_SECONDS` after `test_started_at`.
    test_state = models.CharField(
        max_length=8, choices=SendState.choices, default=SendState.NONE, blank=True, editable=False
    )
    test_started_at = models.DateTimeField(null=True, blank=True, editable=False)
    test_finished_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("notification channel")
        verbose_name_plural = _("notification channels")
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.name} ({self.scheme})"


class Preference(models.Model):
    """When a person wants reminders. No row: the defaults below."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="notification_preference",
    )
    days_before = models.PositiveSmallIntegerField(
        _("first reminder (days before)"),
        default=1,
        validators=[MaxValueValidator(30)],
        help_text=_("0 reminds on the day itself."),
    )
    send_at = models.TimeField(
        _("time of reminders"),
        default=datetime.time(8, 0),
        help_text=_("Reminders and the daily summary go out from this time of day."),
    )
    quiet_start = models.TimeField(_("quiet hours from"), null=True, blank=True)
    quiet_end = models.TimeField(_("quiet hours until"), null=True, blank=True)
    digest = models.BooleanField(
        _("daily summary"),
        default=False,
        help_text=_("One message a day for every task to remind, instead of one per task."),
    )
    time_zone = models.CharField(
        _("time zone"),
        max_length=64,
        blank=True,
        help_text=_("Empty: the time zone of the instance."),
    )

    class Meta:
        verbose_name = _("notification preference")
        verbose_name_plural = _("notification preferences")
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(quiet_start__isnull=True, quiet_end__isnull=True)
                    | models.Q(quiet_start__isnull=False, quiet_end__isnull=False)
                ),
                name="notification_quiet_both_or_none",
            )
        ]

    def __str__(self) -> str:
        return str(self.user)


class Kind(models.TextChoices):
    REMINDER = "reminder", _("Reminder")
    DIGEST = "digest", _("Daily summary")


class Delivery(models.Model):
    """One message to one person, claimed before it is sent so that it is sent once.

    `key` names the message (an occurrence, its due date and the follow-up number, or the day
    of a summary); the unique constraint makes a second worker's claim fail.
    """

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_deliveries"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    key = models.CharField(max_length=120)
    occurrence = models.ForeignKey(
        "tasks.Occurrence", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    claimed_at = models.DateTimeField(auto_now_add=True)
    sent = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("notification sent")
        verbose_name_plural = _("notifications sent")
        constraints = [
            models.UniqueConstraint(fields=["user", "kind", "key"], name="one_delivery_per_key")
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.key}"


MAX_DEVICES = 5


class PushKey(models.Model):
    """The VAPID key pair of the instance (docs/adr/0066): one row, made at first use.

    The private key is sealed by `instance.crypto` like every stored secret, so it is not
    readable from a backup without the secret key (docs/adr/0060). A new key pair makes
    every device subscribed with the old public key unreachable: they are deleted with it.
    """

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    private_key = models.TextField(editable=False)
    public_key = models.CharField(max_length=100, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("push key of the instance")
        verbose_name_plural = _("push keys of the instance")

    def __str__(self) -> str:
        return self.public_key[:12]


class Device(BaseModel):
    """A browser of a person that receives notifications by Web Push (docs/adr/0066).

    The endpoint is the address of the push service for that browser: whoever has it can
    push to the device, so it is sealed and never shown. The two keys of the browser are
    sealed too; `endpoint_hash` finds the device again when the same browser subscribes.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_devices"
    )
    name = models.CharField(_("name"), max_length=60)
    endpoint = models.TextField(editable=False)
    endpoint_hash = models.CharField(max_length=64, editable=False)
    p256dh = models.TextField(editable=False)
    auth = models.TextField(editable=False)
    # Who carries the messages: the name of the push service, from the host of the endpoint.
    service = models.CharField(max_length=40, editable=False)
    last_sent_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("device")
        verbose_name_plural = _("devices")
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["endpoint_hash"], name="one_device_per_endpoint")
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.service})"

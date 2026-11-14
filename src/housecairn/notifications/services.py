"""Rules of notification channels and reminder preferences (docs/adr/0002, 0043).

Web views and API views call these functions. A channel is read and changed by its owner
only: the views look it up among `channels_of(request.user)`, so anyone else, an
administrator of the instance included, gets the same 404.
"""

import datetime
import logging
import time
import zoneinfo
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from housecairn.accounts.models import User
from housecairn.core.language import language_of
from housecairn.demo import mode
from housecairn.instance.config import get_setting
from housecairn.notifications import outbound
from housecairn.notifications.models import MAX_CHANNELS, Channel, Preference, SendState

logger = logging.getLogger("housecairn.notifications")

AnyUser = User | AnonymousUser

PREFERENCE_FIELDS = frozenset(
    {"days_before", "send_at", "quiet_start", "quiet_end", "digest", "time_zone"}
)
CHANNEL_FIELDS = frozenset({"name", "enabled"})
TESTS_PER_MINUTE = 5
# A test that has not ended this long after it started is a failure, whatever became of the
# worker (docs/adr/0070). The send itself gives up after `outbound.SEND_SECONDS`.
TEST_DEADLINE_SECONDS = 30
# The task is stopped by the worker a little before the deadline.
TEST_TASK_SECONDS = 25

REFUSALS = {
    "invalid": gettext_lazy("This is not an Apprise address, such as ntfys://ntfy.sh/topic."),
    "scheme": gettext_lazy(
        "This kind of channel is not offered. Use ntfy, Gotify, Telegram, Signal, Matrix, "
        "email, Discord, Slack, Pushover, Mattermost or Apprise API."
    ),
    "options": gettext_lazy("These options make the instance read a file: remove them."),
    "fields": gettext_lazy(
        "Options that add headers or fields to the request (+name, -name, :name) are not "
        "accepted: remove them."
    ),
    "address": gettext_lazy("The instance does not send to this address."),
    "private": gettext_lazy(
        "This address is on a private network, which this instance does not send to. An "
        "administrator of the instance can allow it: Settings, Notifications, Channels on "
        "the local network."
    ),
}
ADDRESS_FIXED = gettext_lazy(
    "The address of a channel cannot change: delete the channel and add another."
)
FIELD_FIXED = gettext_lazy("This field cannot be changed.")
ADDED = gettext_lazy("Channel added. Send a test to check it.")
SAVED = gettext_lazy("Saved.")
DELETED = gettext_lazy("Channel deleted.")
TEST_RESULTS = {
    True: gettext_lazy("The test message was sent."),
    False: gettext_lazy("The test message could not be sent: check the address and the service."),
}
TEST_RUNNING = gettext_lazy("The test message is being sent.")


def is_enabled() -> bool:
    return bool(get_setting("notifications_enabled"))


def _person(actor: AnyUser) -> User:
    if not isinstance(actor, User) or not actor.is_authenticated:
        raise PermissionDenied
    return actor


def _require_owner(actor: AnyUser, channel: Channel) -> User:
    user = _person(actor)
    if channel.user_id != user.pk:
        raise PermissionDenied
    return user


def _require_enabled() -> None:
    if not is_enabled():
        raise ValidationError(_("Notifications are turned off on this instance."))


def _clean_name(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > 60 or any(not c.isprintable() for c in text):
        raise ValidationError({"name": [_("Give a name of 1 to 60 characters.")]})
    return text


# ---------- channels ----------


def channels_of(user: AnyUser) -> models.QuerySet[Channel]:
    if not isinstance(user, User) or not user.is_authenticated:
        return Channel.objects.none()
    return Channel.objects.filter(user=user)


@transaction.atomic
def add_channel(actor: AnyUser, *, name: Any, url: Any) -> Channel:
    """A new channel of the person signed in; the address is checked, then sealed."""
    user = _person(actor)
    _require_enabled()
    clean_name = _clean_name(name)
    list(User.objects.select_for_update().filter(pk=user.pk))  # counted once at a time
    if channels_of(user).count() >= MAX_CHANNELS:
        raise ValidationError(_("At most %(n)d channels.") % {"n": MAX_CHANNELS})
    try:
        parsed = outbound.parse(url)
    except outbound.RefusedError as refused:
        raise ValidationError({"url": [REFUSALS[str(refused)]]}) from None
    channel: Channel = Channel.objects.create(
        user=user,
        name=clean_name,
        url=outbound.seal(url),
        scheme=parsed.scheme,
        service=parsed.service,
    )
    return channel


def update_channel(actor: AnyUser, channel: Channel, **changes: Any) -> Channel:
    """Rename, turn off or on. The address cannot change: delete the channel and add another."""
    _require_owner(actor, channel)
    unknown = set(changes) - CHANNEL_FIELDS
    if unknown:
        raise ValueError(f"Cannot change: {', '.join(sorted(unknown))}")
    if "name" in changes:
        channel.name = _clean_name(changes["name"])
    if "enabled" in changes:
        channel.enabled = bool(changes["enabled"])
    channel.save(update_fields=[*changes, "updated_at"])
    return channel


def delete_channel(actor: AnyUser, channel: Channel) -> None:
    """For good, its sealed address with it."""
    _require_owner(actor, channel)
    channel.delete()


def _consume_test(user: User) -> bool:
    key = f"housecairn:notifications:test:{user.pk}:{int(time.time() // 60)}"
    cache.add(key, 0, timeout=120)
    try:
        return cache.incr(key) <= TESTS_PER_MINUTE
    except ValueError:  # expired in between
        return False


def expire_test(channel: Channel) -> Channel:
    """Mark a test that has run past its deadline as failed; the channel, up to date.

    A test is never left "running": the worker may have stopped, restarted or lost the task.
    The update is conditional, so a result that arrives in the meantime is not overwritten.
    """
    started = channel.test_started_at
    if channel.test_state == SendState.RUNNING and (
        started is None
        or timezone.now() - started > datetime.timedelta(seconds=TEST_DEADLINE_SECONDS)
    ):
        Channel.objects.filter(
            pk=channel.pk, test_state=SendState.RUNNING, test_started_at=started
        ).update(test_state=SendState.FAILED, test_finished_at=timezone.now())
        channel.refresh_from_db()
    return channel


def test_result(channel: Channel) -> str:
    """The sentence that says where the last test stands; empty when there was none."""
    if channel.test_state == SendState.RUNNING:
        return str(TEST_RUNNING)
    if channel.test_state == SendState.SENT:
        return str(TEST_RESULTS[True])
    if channel.test_state == SendState.FAILED:
        return str(TEST_RESULTS[False])
    if channel.test_state == SendState.PRIVATE:
        return str(REFUSALS["private"])
    return ""


def start_test(actor: AnyUser, channel: Channel) -> Channel:
    """Queue a real test message for the worker; the request does not wait for the service.

    The answer is read later from `test_state` (`expire_test` first): one of fixed sentences,
    never the service's own (`test_result`). A test already running is not started twice and
    does not count against the limit. An address on a private network that the instance does
    not allow ends as `private` (docs/adr/0059): nothing was sent, and the person can do
    something about it.
    """
    from django_q.tasks import async_task

    user = _require_owner(actor, channel)
    _require_enabled()
    mode.refuse(mode.nothing_sent())
    # The state in the database, not the one the caller read earlier: a worker may have ended it.
    channel.refresh_from_db(fields=["test_state", "test_started_at", "test_finished_at"])
    expire_test(channel)
    if channel.test_state == SendState.RUNNING:
        return channel
    if not _consume_test(user):
        raise ValidationError(_("Too many tests: wait a minute."))
    now = timezone.now()
    Channel.objects.filter(pk=channel.pk).update(
        test_state=SendState.RUNNING, test_started_at=now, test_finished_at=None
    )
    async_task(
        "housecairn.notifications.tasks.send_test",
        str(channel.pk),
        language_of(user),
        timeout=TEST_TASK_SECONDS,
        # A task stopped at its timeout is not given again: the deadline ends the test.
        ack_failure=True,
    )
    channel.refresh_from_db()
    return channel


def send_test(channel_id: str, language: str) -> None:
    """Task of the worker: send the test message, then record how it ended."""
    channel = Channel.objects.filter(pk=channel_id, test_state=SendState.RUNNING).first()
    if channel is None:
        return
    # A task can come late: the worker gives it again after `retry` if it was stopped at its
    # timeout. The deadline applies to the row as it is now, and a test past it sends nothing.
    if expire_test(channel).test_state != SendState.RUNNING:
        return
    started = channel.test_started_at
    try:
        with translation.override(language):
            title = _("HouseCairn test")
            body = _("This channel receives the reminders of your tasks.")
        outcome = outbound.deliver(channel.url, title=title, body=body)
        if outcome is outbound.Outcome.PRIVATE:
            state = SendState.PRIVATE
        else:
            state = SendState.SENT if outcome is outbound.Outcome.SENT else SendState.FAILED
    except Exception:
        logger.exception("A test message could not be sent: %s", channel_id)
        state = SendState.FAILED
    Channel.objects.filter(
        pk=channel.pk, test_state=SendState.RUNNING, test_started_at=started
    ).update(test_state=state, test_finished_at=timezone.now())


# ---------- preferences ----------


def preferences_of(user: User) -> Preference:
    """The person's preferences; the defaults, not saved, if they never changed any."""
    found: Preference | None = Preference.objects.filter(user=user).first()
    return found or Preference(user=user)


def update_preferences(actor: AnyUser, **changes: Any) -> Preference:
    user = _person(actor)
    unknown = set(changes) - PREFERENCE_FIELDS
    if unknown:
        raise ValueError(f"Cannot change: {', '.join(sorted(unknown))}")
    prefs = preferences_of(user)
    before = (prefs.pk is not None and prefs.digest, prefs.send_at, prefs.time_zone)
    for field, value in changes.items():
        setattr(prefs, field, value)
    errors: dict[str, list[str]] = {}
    if (prefs.quiet_start is None) != (prefs.quiet_end is None):
        errors["quiet_end"] = [_("Give the start and the end of the quiet hours, or neither.")]
    if prefs.time_zone and prefs.time_zone not in _zones():
        errors["time_zone"] = [_("Choose a time zone such as Europe/Paris.")]
    if errors:
        raise ValidationError(errors)
    prefs.full_clean(exclude=["user"])
    prefs.save()
    if prefs.digest and before != (True, prefs.send_at, prefs.time_zone):
        from housecairn.notifications import reminders  # it imports this module

        reminders.skip_pending_digest(user, prefs)
    return prefs


def _zones() -> set[str]:
    return set(zoneinfo.available_timezones()) | {"UTC"}


def export_of(actor: AnyUser) -> dict[str, Any]:
    """What the account keeps for notifications: never the address of a channel."""
    user = _person(actor)
    prefs = preferences_of(user)
    from housecairn.notifications import devices  # it imports this module

    return {
        "devices": devices.export_of(user),
        "channels": [
            {
                "id": str(c.id),
                "name": c.name,
                "scheme": c.scheme,
                "service": c.service,
                "enabled": c.enabled,
                "created_at": c.created_at.isoformat(),
            }
            for c in channels_of(user)
        ],
        "preferences": {
            field: (str(v) if (v := getattr(prefs, field)) is not None else None)
            if field in {"send_at", "quiet_start", "quiet_end"}
            else getattr(prefs, field)
            for field in sorted(PREFERENCE_FIELDS)
        },
    }

"""Notifications on the device, by Web Push (docs/adr/0066).

A person subscribes a browser from the screen of notifications; the browser gives an
endpoint (the address of its push service) and two keys. Messages are encrypted for the
browser (`webpush.encrypt`) and posted to the push service by the background worker, with
the guard of `core.netguard.post`: HTTPS, port 443, public addresses only, and only to the
push services of the browsers people use (`PUSH_SERVICES`).

Off by default (`webpush_enabled`); on top of `notifications_enabled`. A device is read and
changed by its owner only, as a channel is (`services`).
"""

import hashlib
import json
import logging
import time
from typing import Any
from urllib.parse import urlsplit

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, models, transaction
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from housecairn.accounts.models import User
from housecairn.core import netguard
from housecairn.core.language import language_of
from housecairn.demo import mode as demo_mode
from housecairn.instance import crypto
from housecairn.instance.config import get_setting
from housecairn.notifications import services, webpush
from housecairn.notifications.models import MAX_DEVICES, Device, PushKey

logger = logging.getLogger("housecairn.notifications")

AnyUser = User | AnonymousUser

# The push services of the browsers people use, by the end of the host of an endpoint:
# Chrome, Edge (Chromium), Brave, Opera and Samsung Internet use Google's; Firefox uses
# Mozilla's; Safari, on macOS and on iOS 16.4 or later once the application is installed,
# uses Apple's; the former Edge used Microsoft's. Any other host is refused.
PUSH_SERVICES: tuple[tuple[str, str], ...] = (
    ("fcm.googleapis.com", "Google"),
    ("push.services.mozilla.com", "Mozilla"),
    ("push.apple.com", "Apple"),
    ("notify.windows.com", "Microsoft"),
)
MAX_ENDPOINT_LENGTH = 1024
TESTS_PER_MINUTE = 5
GONE = frozenset({404, 410})

ADDED = gettext_lazy("This device receives your notifications.")
DELETED = gettext_lazy("This device no longer receives your notifications.")
TEST_QUEUED = gettext_lazy("A test notification is on its way to this device.")
OFF = gettext_lazy("Notifications on the device are turned off on this instance.")
REFUSED = gettext_lazy(
    "The push service of this browser is not one this instance sends to: use Chrome, "
    "Edge, Firefox or Safari."
)


def is_enabled() -> bool:
    return services.is_enabled() and bool(get_setting("webpush_enabled"))


def is_discreet() -> bool:
    return bool(get_setting("webpush_discreet"))


def _person(actor: AnyUser) -> User:
    if not isinstance(actor, User) or not actor.is_authenticated:
        raise PermissionDenied
    return actor


def _require_owner(actor: AnyUser, device: Device) -> User:
    user = _person(actor)
    if device.user_id != user.pk:
        raise PermissionDenied
    return user


def _require_enabled() -> None:
    if not is_enabled():
        raise ValidationError(OFF)


def devices_of(user: AnyUser) -> models.QuerySet[Device]:
    if not isinstance(user, User) or not user.is_authenticated:
        return Device.objects.none()
    return Device.objects.filter(user=user)


# ---------- the key of the instance ----------


def _make_key() -> PushKey:
    private = webpush.new_key()
    key = PushKey(
        id=1,
        private_key=crypto.encrypt(webpush.b64url(webpush.private_bytes(private))),
        public_key=webpush.b64url(webpush.public_bytes(private.public_key())),
    )
    try:
        with transaction.atomic():
            key.save(force_insert=True)
    except IntegrityError:  # made by another process in between
        found: PushKey = PushKey.objects.get(pk=1)
        return found
    return key


def instance_key() -> PushKey:
    """The key pair of the instance, made at first use.

    If its private key cannot be read (the secret key of the instance changed with no
    fallback), a new pair is made and every device is deleted: they were subscribed with
    the old public key, so no message of the new one would reach them.
    """
    found: PushKey | None = PushKey.objects.filter(pk=1).first()
    if found is None:
        return _make_key()
    try:
        crypto.decrypt(found.private_key)
    except crypto.SecretUnreadableError:
        logger.warning("The push key could not be read: a new one is made, devices are removed.")
        return rotate_key()
    return found


def public_key() -> str:
    return instance_key().public_key


@transaction.atomic
def rotate_key() -> PushKey:
    """A new key pair; every device is deleted (each person subscribes again)."""
    Device.objects.all().delete()
    PushKey.objects.filter(pk=1).delete()
    return _make_key()


def _signing_key(key: PushKey) -> Any:
    return webpush.private_from_bytes(webpush.unb64url(crypto.decrypt(key.private_key)))


# ---------- subscribing ----------


def service_of(endpoint: Any) -> str:
    """The name of the push service of an endpoint; `ValidationError` if it is refused.

    HTTPS, port 443, no user name nor password, a host among `PUSH_SERVICES` (an IP
    address never is). Sending checks the addresses of the name again (`netguard.post`).
    """
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > MAX_ENDPOINT_LENGTH:
        raise ValidationError({"endpoint": [REFUSED]})
    try:
        parts = urlsplit(endpoint)
        host, port = (parts.hostname or ""), parts.port
    except ValueError:
        raise ValidationError({"endpoint": [REFUSED]}) from None
    if (
        parts.scheme != "https"
        or parts.username is not None
        or parts.password is not None
        or (port or 443) != 443
        or any(c.isspace() for c in endpoint)
    ):
        raise ValidationError({"endpoint": [REFUSED]})
    for suffix, name in PUSH_SERVICES:
        if host == suffix or host.endswith("." + suffix):
            return name
    raise ValidationError({"endpoint": [REFUSED]})


def _key_bytes(value: Any, size: int, field: str) -> bytes:
    try:
        raw = webpush.unb64url(value) if isinstance(value, str) and len(value) <= 200 else b""
    except ValueError:
        raw = b""
    if len(raw) != size:
        raise ValidationError({field: [_("This key is not valid.")]})
    return raw


def _clean_name(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > 60 or any(not c.isprintable() for c in text):
        raise ValidationError({"name": [_("Give a name of 1 to 60 characters.")]})
    return text


def _hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()


@transaction.atomic
def subscribe(actor: AnyUser, *, name: Any, endpoint: Any, p256dh: Any, auth: Any) -> Device:
    """Add the browser of the person signed in, or renew it if it subscribed before.

    One device per endpoint: the same browser subscribing again replaces its keys, and a
    browser that another account had subscribed (signed out, then someone else signed in)
    now belongs to this person only.
    """
    user = _person(actor)
    _require_enabled()
    clean_name = _clean_name(name)
    service = service_of(endpoint)
    ua_public = _key_bytes(p256dh, 65, "p256dh")
    try:
        webpush.load_public(ua_public)
    except ValueError:
        raise ValidationError({"p256dh": [_("This key is not valid.")]}) from None
    secret = _key_bytes(auth, 16, "auth")
    instance_key()  # the browser subscribed with it; made before the first device
    digest = _hash(str(endpoint))
    list(User.objects.select_for_update().filter(pk=user.pk))  # counted once at a time
    Device.all_objects.filter(endpoint_hash=digest).exclude(user=user).delete()
    found: Device | None = Device.all_objects.filter(endpoint_hash=digest, user=user).first()
    if found is None and devices_of(user).count() >= MAX_DEVICES:
        raise ValidationError(_("At most %(n)d devices.") % {"n": MAX_DEVICES})
    device = found or Device(user=user, endpoint_hash=digest)
    device.name = clean_name
    device.endpoint = crypto.encrypt(str(endpoint))
    device.p256dh = crypto.encrypt(webpush.b64url(ua_public))
    device.auth = crypto.encrypt(webpush.b64url(secret))
    device.service = service
    device.deleted_at = None
    device.save()
    return device


def rename(actor: AnyUser, device: Device, *, name: Any) -> Device:
    _require_owner(actor, device)
    device.name = _clean_name(name)
    device.save(update_fields=["name", "updated_at"])
    return device


def revoke(actor: AnyUser, device: Device) -> None:
    """For good: the endpoint and the keys go with it."""
    _require_owner(actor, device)
    device.delete()


# ---------- sending ----------


def _payload(title: str, body: str, path: str) -> bytes:
    """JSON for the service worker, cut to what a push service must accept."""
    for size in (len(body), 1500, 600, 0):
        data = json.dumps(
            {"title": title, "body": body[:size], "url": path}, ensure_ascii=False
        ).encode()
        if len(data) <= webpush.MAX_PAYLOAD:
            return data
    return json.dumps({"title": title[:100], "body": "", "url": path}).encode()


def _subject() -> str:
    site = str(get_setting("site_url") or "")
    return site if site.startswith("https://") else "mailto:noreply@example.com"


def send(device: Device, *, title: str, body: str, path: str) -> bool:
    """Push one message to one device; True when the push service took it. Never raises.

    A device the push service says is gone (404, 410) is deleted. Nothing is logged of
    the endpoint, the keys or the message. Nothing leaves a demonstration instance
    (docs/adr/0071).
    """
    if demo_mode.is_on():
        logger.info("Demonstration instance: a notification to a device was not sent.")
        return False
    try:
        key = instance_key()
        endpoint = crypto.decrypt(device.endpoint)
        service_of(endpoint)  # the list may have changed since the device subscribed
        message = webpush.encrypt(
            _payload(title, body, path),
            ua_public=webpush.unb64url(crypto.decrypt(device.p256dh)),
            auth_secret=webpush.unb64url(crypto.decrypt(device.auth)),
        )
        authorization = webpush.vapid_header(endpoint, _signing_key(key), _subject())
        status = webpush.post(endpoint, body=message, authorization=authorization)
    except netguard.RefusedError as exc:
        logger.warning("A notification to a device was not sent (%s).", exc.reason)
        return False
    except Exception as exc:  # a key unreadable, an endpoint refused, anything else
        logger.warning("A notification to a device was not sent (%s).", type(exc).__name__)
        return False
    if status in GONE:
        Device.all_objects.filter(pk=device.pk).delete()
        return False
    if 200 <= status < 300:
        Device.all_objects.filter(pk=device.pk).update(last_sent_at=timezone.now())
        return True
    logger.warning("A notification to a device was not sent (status %d).", status)
    return False


def message_for(kind: str, title: str, body: str) -> tuple[str, str]:
    """What a device shows: the same text as a channel, or a fixed sentence when the
    instance asks for discreet content (`webpush_discreet`): no title of a task, no name of
    a home, only what kind of message it is. The link opens the screen either way."""
    if not is_discreet():
        return title, body
    sentences = {
        "reminder": _("A task is due."),
        "digest": _("Tasks are due."),
        "report": _("A problem was reported."),
        "test": _("This device receives your notifications."),
    }
    return "HouseCairn", sentences.get(kind, sentences["reminder"])


def send_all(devices: list[Device], *, kind: str, title: str, body: str, path: str) -> list[bool]:
    """Send to each device; a fixed sentence is worded in the language of the person of the
    device (as the emails and the channels are), not in that of the worker. `title` and
    `body` come already worded by the caller in the language of the person."""
    results: list[bool] = []
    for device in devices:
        with translation.override(language_of(device.user)):
            shown_title, shown_body = message_for(kind, title, body)
        results.append(send(device, title=shown_title, body=shown_body, path=path))
    return results


def reachable() -> dict[Any, list[Device]]:
    """Every device of an active account, by person, when the feature is on."""
    found: dict[Any, list[Device]] = {}
    if not is_enabled():
        return found
    for device in Device.objects.select_related("user"):
        if device.user.is_active:
            found.setdefault(device.user_id, []).append(device)
    return found


def _consume_test(user: User) -> bool:
    key = f"housecairn:notifications:device-test:{user.pk}:{int(time.time() // 60)}"
    cache.add(key, 0, timeout=120)
    try:
        return cache.incr(key) <= TESTS_PER_MINUTE
    except ValueError:
        return False


def test(actor: AnyUser, device: Device) -> None:
    """Queue a test message to the device: the background worker sends it."""
    user = _require_owner(actor, device)
    _require_enabled()
    demo_mode.refuse(demo_mode.nothing_sent())
    if not _consume_test(user):
        raise ValidationError(_("Too many tests: wait a minute."))
    from django_q.tasks import async_task

    async_task("housecairn.notifications.devices.send_test", str(device.pk))


def send_test(device_id: str) -> bool:
    """Run by the worker for `test`."""
    if not is_enabled():
        return False
    device = Device.objects.select_related("user").filter(pk=device_id).first()
    if device is None:
        return False
    with translation.override(language_of(device.user)):
        title, body = message_for(
            "test", _("HouseCairn test"), _("This device receives the reminders of your tasks.")
        )
    return send(device, title=title, body=body, path=reverse("notifications:index"))


def export_of(user: User) -> list[dict[str, Any]]:
    """What the account export says of the devices: never an endpoint nor a key."""
    return [
        {
            "id": str(d.id),
            "name": d.name,
            "service": d.service,
            "created_at": d.created_at.isoformat(),
        }
        for d in devices_of(user)
    ]

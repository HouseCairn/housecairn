"""The demonstration mode of an instance (`HOUSECAIRN_DEMO`, docs/adr/0071).

A public demonstration instance has two fictional accounts whose addresses and password are
published, and comes back to a known state every night (`reset_demo`). Off by default: with
`HOUSECAIRN_DEMO` false, nothing here changes what the application does.

On, it keeps what a visitor signed in with a public account could otherwise do to the
instance or to the outside world:

- the settings of the instance and its sign-in providers are shown, never changed;
- nothing leaves the server: no email, no notification, no preview of a link, no sign-in
  through a provider;
- no account is created, and the two accounts keep their password, their address, their
  second factors and their existence;
- files are held to small limits, for each file, each home and the whole instance.

Every refusal raises `DemoRefusedError`, a `PermissionDenied`: the web screens turn it
into a message (`demo.middleware`), the API into a 403 with the code `demo`.
"""

import contextlib
import contextvars
from collections.abc import Iterator
from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext as _

MB = 1024 * 1024

# The two public accounts. The domain is reserved for examples (RFC 2606): no mail is ever
# delivered there, and nobody owns an address in it.
ADMIN_EMAIL = "admin@example.com"
USER_EMAIL = "user@example.com"
# Public on purpose: written in the README and on the sign-in screen.
PASSWORD = "housecairn-demo"  # noqa: S105
ACCOUNTS = (ADMIN_EMAIL, USER_EMAIL)

# When the instance comes back to its known state, in this time zone, whatever the time
# zone of the instance.
RESET_TIME_ZONE = "Europe/Paris"
RESET_HOUR = 3

# What the settings hold on a demonstration instance, whatever the variables and the
# database say: closed sign-up, no mail server, no preview of links, small files.
FILE_MAX_BYTES = 2 * MB
HOME_STORAGE_MB = 20
HOME_MAX_FILES = 50
INSTANCE_STORAGE_BYTES = 200 * MB
# The data document of a restored archive (homes.restore), apart from its files.
DOCUMENT_MAX_BYTES = 4 * MB
# Archives of an export (homes.jobs): kept one hour, and at most one for each home and this
# many for the whole instance, so that they cannot fill the disk (20 MB each at most).
EXPORT_KEEP_HOURS = 1
EXPORT_MAX_ACTIVE = 5
FORCED: dict[str, Any] = {
    "signup_open": False,
    "oidc_signup": "closed",
    "email_host": "",
    "link_preview_enabled": False,
    "barcode_lookup_online": False,
    "barcode_catalogue_enabled": False,
    "file_max_bytes": FILE_MAX_BYTES,
    "home_storage_mb": HOME_STORAGE_MB,
    "home_max_files": HOME_MAX_FILES,
}

_resetting: contextvars.ContextVar[bool] = contextvars.ContextVar("demo_resetting", default=False)


class DemoRefusedError(PermissionDenied):
    """Refused because the instance is a demonstration; the message says what is off."""

    def __init__(self, message: str | None = None) -> None:
        self.message = message or _("Not available on the demonstration instance.")
        super().__init__(self.message)


def is_on() -> bool:
    return bool(getattr(settings, "HOUSECAIRN_DEMO", False))


def refuse(message: str | None = None) -> None:
    """Raise `DemoRefusedError` on a demonstration instance; nothing otherwise."""
    if is_on():
        raise DemoRefusedError(message)


def settings_read_only() -> str:
    return _("This is a demonstration instance: its settings can be read, not changed.")


def nothing_sent() -> str:
    return _("This is a demonstration instance: nothing is sent from it.")


def is_demo_address(email: str | None) -> bool:
    return (email or "").strip().lower() in ACCOUNTS


def resetting() -> bool:
    """Whether `reset_demo` is running in this context: it alone creates the accounts."""
    return _resetting.get()


@contextlib.contextmanager
def reset_context() -> Iterator[None]:
    token = _resetting.set(True)
    try:
        yield
    finally:
        _resetting.reset(token)


def guarded() -> bool:
    """Whether the guards of the accounts apply: demonstration on, outside a reset."""
    return is_on() and not resetting()

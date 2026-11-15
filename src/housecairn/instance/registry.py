"""The settings an administrator can change in the interface (docs/adr/0035).

Each one is paired with an environment variable: the variable wins and locks the
field, otherwise the value stored in the database, otherwise the default. This
module only describes the settings and parses their values; it imports no model,
so that `housecairn.settings` can use it before the database exists. Reading a
value anywhere else goes through `housecairn.instance.config.get_setting`.
"""

import functools
import os
import unicodedata
import zoneinfo
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import Promise
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

# django-environ's own list of true strings, so that an existing environment keeps its meaning.
_TRUE = frozenset({"true", "on", "ok", "y", "yes", "1"})
_FALSE = frozenset({"false", "off", "n", "no", "0"})


class Kind(StrEnum):
    BOOL = "boolean"
    INT = "integer"
    STR = "string"
    CHOICE = "choice"
    LIST = "list"
    SECRET = "secret"  # noqa: S105 - a kind, not a password


class Reload(StrEnum):
    """When a change takes effect."""

    LIVE = "live"  # at once here, within a few seconds on the other processes
    RESTART = "restart"


class Section(StrEnum):
    ADDRESS = "address"
    INSTANCE = "instance"
    SIGNUP = "signup"
    INVITATIONS = "invitations"
    EMAIL = "email"
    RATE_LIMITS = "rate-limits"
    FILES = "files"
    NOTIFICATIONS = "notifications"
    PROJECTS = "projects"
    BARCODES = "barcodes"
    MOBILE_APP = "mobile-app"


SECTION_LABELS: dict[Section, Promise] = {
    Section.ADDRESS: _("Public address"),
    Section.INSTANCE: _("Name, language and time zone"),
    Section.SIGNUP: _("Sign-up"),
    Section.INVITATIONS: _("Invitations"),
    Section.EMAIL: _("Outgoing email"),
    Section.RATE_LIMITS: _("Rate limits"),
    Section.FILES: _("Files of a home"),
    Section.NOTIFICATIONS: _("Notifications"),
    Section.PROJECTS: _("Projects"),
    Section.BARCODES: _("Barcodes"),
    Section.MOBILE_APP: _("Mobile app"),
}

# The one button of a section's page: it names what it saves.
SECTION_SAVE_LABELS: dict[Section, Promise] = {
    Section.ADDRESS: _("Save the public address"),
    Section.INSTANCE: _("Save the name, language and time zone"),
    Section.SIGNUP: _("Save the sign-up settings"),
    Section.INVITATIONS: _("Save the invitation settings"),
    Section.EMAIL: _("Save the outgoing email settings"),
    Section.RATE_LIMITS: _("Save the rate limits"),
    Section.FILES: _("Save the file limits"),
    Section.NOTIFICATIONS: _("Save the notification settings"),
    Section.PROJECTS: _("Save the project settings"),
    Section.BARCODES: _("Save the barcode settings"),
    Section.MOBILE_APP: _("Save the mobile app setting"),
}

# The product name: the default name of an instance, and what an empty name shows.
PRODUCT_NAME = "HouseCairn"
INSTANCE_NAME_MAX_LENGTH = 64
LANGUAGES = ("en", "fr")  # the codes of settings.LANGUAGES; checked by a test


@dataclass(frozen=True)
class Setting:
    key: str
    env: str  # the environment variable that pairs with it
    django_name: str  # the Django setting that holds the value read from the environment
    kind: Kind
    section: Section
    label: Promise
    help: Promise
    default: Any
    reload: Reload = Reload.LIVE
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    check: Callable[[Any], None] | None = field(default=None, compare=False)
    # False: the check applies to what is typed in the interface, not to an existing variable.
    strict_env: bool = True

    @property
    def secret(self) -> bool:
        return self.kind is Kind.SECRET

    def parse_environment(self, raw: str) -> Any:
        """The value of the variable, as a Python value; ValueError if it is not valid."""
        text = raw.strip()
        if self.kind is Kind.BOOL:
            if text.lower() in _TRUE:
                return True
            if text.lower() in _FALSE:
                return False
            raise ValueError(_("Expected true or false."))
        return self.coerce(text, check=self.strict_env)

    def coerce(self, value: Any, *, check: bool = True) -> Any:
        """A valid value, from a form, the API or storage; ValueError with a message if not."""
        kind = self.kind
        if kind is Kind.BOOL:
            if isinstance(value, bool):
                return value
            raise ValueError(_("Expected true or false."))
        if kind is Kind.INT:
            if isinstance(value, bool) or not isinstance(value, int | str):
                raise ValueError(_("Expected a whole number."))
            try:
                number = int(str(value).strip())
            except ValueError:
                raise ValueError(_("Expected a whole number.")) from None
            if self.minimum is not None and number < self.minimum:
                raise ValueError(format_lazy(_("Must be at least {n}."), n=self.minimum))
            if self.maximum is not None and number > self.maximum:
                raise ValueError(format_lazy(_("Must be at most {n}."), n=self.maximum))
            return number
        if kind is Kind.LIST:
            if isinstance(value, str):
                value = value.replace("\n", ",").split(",")
            if not isinstance(value, list | tuple):
                raise ValueError(_("Expected a list of domains."))
            items = [str(v).strip().lower().lstrip("@") for v in value if str(v).strip()]
            for item in items:
                if not _is_domain(item):
                    raise ValueError(format_lazy(_("{item} is not a domain name."), item=item))
            return list(dict.fromkeys(items))
        if not isinstance(value, str):
            raise ValueError(_("Expected text."))
        text = value.strip()
        if "\n" in text or "\r" in text or len(text) > 1024:
            raise ValueError(_("Expected a single line of text."))
        if kind is Kind.CHOICE and text not in self.choices:
            raise ValueError(
                format_lazy(_("Must be one of: {choices}."), choices=", ".join(self.choices))
            )
        if check and self.check is not None and text:
            self.check(text)
        return text.rstrip("/") if self.key == "site_url" else text


def _is_domain(name: str) -> bool:
    labels = name.split(".")
    return 0 < len(name) <= 253 and all(
        label
        and len(label) <= 63
        and not label.startswith("-")
        and not label.endswith("-")
        and all(c.isalnum() or c == "-" for c in label)
        for label in labels
    )


def _check_site_url(value: str) -> None:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.path.strip("/"):
        raise ValueError(_("Must be an http:// or https:// address without a path."))
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError(_("Must be an http:// or https:// address without a path."))


def _check_host(value: str) -> None:
    if not all(c.isalnum() or c in ".-_:[]" for c in value):
        raise ValueError(_("Must be a host name or an address."))


def _check_instance_name(value: str) -> None:
    if len(value) > INSTANCE_NAME_MAX_LENGTH or any(
        unicodedata.category(ch) in {"Cc", "Cf", "Cs", "Co", "Cn"} for ch in value
    ):
        raise ValueError(
            format_lazy(
                _("At most {n} characters, without control characters."),
                n=INSTANCE_NAME_MAX_LENGTH,
            )
        )


def _check_time_zone(value: str) -> None:
    if value not in _time_zones():
        raise ValueError(_("Must be a time zone of the IANA database, such as Europe/Paris."))


@functools.cache
def _time_zones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones()) | {"UTC"}


def _check_from(value: str) -> None:
    if "@" not in value or ("<" in value) != (">" in value):
        raise ValueError(_("Must be an address, such as HouseCairn <noreply@example.com>."))


_SETTINGS: tuple[Setting, ...] = (
    Setting(
        "site_url",
        "HOUSECAIRN_SITE_URL",
        "HOUSECAIRN_SITE_URL",
        Kind.STR,
        Section.ADDRESS,
        _("Public address"),
        _(
            "Scheme and host of the instance, such as https://home.example.com, used for links "
            "in emails. Empty: the address of the request."
        ),
        "",
        check=_check_site_url,
    ),
    Setting(
        "signup_open",
        "HOUSECAIRN_SIGNUP_OPEN",
        "HOUSECAIRN_SIGNUP_OPEN",
        Kind.BOOL,
        Section.SIGNUP,
        _("Open sign-up"),
        _("Anyone may create an account from the sign-up page. Needs outgoing email."),
        False,
    ),
    Setting(
        "oidc_signup",
        "HOUSECAIRN_OIDC_SIGNUP",
        "HOUSECAIRN_OIDC_SIGNUP",
        Kind.CHOICE,
        Section.SIGNUP,
        _("Sign-up through single sign-on"),
        _(
            "closed: only invited people and the first administrator; "
            "open: anyone the provider accepts."
        ),
        "closed",
        choices=("closed", "open"),
    ),
    Setting(
        "signup_allowed_domains",
        "HOUSECAIRN_SIGNUP_ALLOWED_DOMAINS",
        "HOUSECAIRN_SIGNUP_ALLOWED_DOMAINS",
        Kind.LIST,
        Section.SIGNUP,
        _("Allowed domains"),
        _("Comma-separated. If set, only these domains may sign up."),
        [],
    ),
    Setting(
        "signup_blocked_domains",
        "HOUSECAIRN_SIGNUP_BLOCKED_DOMAINS",
        "HOUSECAIRN_SIGNUP_BLOCKED_DOMAINS",
        Kind.LIST,
        Section.SIGNUP,
        _("Blocked domains"),
        _("Comma-separated. These domains may not sign up."),
        [],
    ),
    Setting(
        "signup_pow_bits",
        "HOUSECAIRN_SIGNUP_POW_BITS",
        "HOUSECAIRN_SIGNUP_POW_BITS",
        Kind.INT,
        Section.SIGNUP,
        _("Anti-robot work"),
        _("Zero bits the browser must find before signing up, from 8 to 24; 16 takes a second."),
        16,
        minimum=8,
        maximum=24,
    ),
    Setting(
        "invitation_lifetime_days",
        "HOUSECAIRN_INVITATION_LIFETIME_DAYS",
        "HOUSECAIRN_INVITATION_LIFETIME_DAYS",
        Kind.INT,
        Section.INVITATIONS,
        _("Invitation lifetime (days)"),
        _("Days an invitation link stays valid, from 1 to 90. Applies to new links."),
        7,
        minimum=1,
        maximum=90,
    ),
    Setting(
        "email_host",
        "EMAIL_HOST",
        "EMAIL_HOST",
        Kind.STR,
        Section.EMAIL,
        _("SMTP server"),
        _("Host name of the outgoing mail server. Empty: email is off."),
        "",
        check=_check_host,
        strict_env=False,
    ),
    Setting(
        "email_port",
        "EMAIL_PORT",
        "EMAIL_PORT",
        Kind.INT,
        Section.EMAIL,
        _("SMTP port"),
        _("587 for STARTTLS, 465 for TLS from the first byte."),
        587,
        minimum=1,
        maximum=65535,
    ),
    Setting(
        "email_use_tls",
        "EMAIL_USE_TLS",
        "EMAIL_USE_TLS",
        Kind.BOOL,
        Section.EMAIL,
        _("STARTTLS"),
        _("Upgrade the connection with STARTTLS. Ignored when TLS from the first byte is on."),
        True,
    ),
    Setting(
        "email_use_ssl",
        "EMAIL_USE_SSL",
        "EMAIL_USE_SSL",
        Kind.BOOL,
        Section.EMAIL,
        _("TLS from the first byte"),
        _("Usually with port 465."),
        False,
    ),
    Setting(
        "email_host_user",
        "EMAIL_HOST_USER",
        "EMAIL_HOST_USER",
        Kind.STR,
        Section.EMAIL,
        _("SMTP user name"),
        _("Empty: no authentication."),
        "",
    ),
    Setting(
        "email_host_password",
        "EMAIL_HOST_PASSWORD",
        "EMAIL_HOST_PASSWORD",
        Kind.SECRET,
        Section.EMAIL,
        _("SMTP password"),
        _("Write only: it is never shown again."),
        "",
    ),
    Setting(
        "email_timeout",
        "EMAIL_TIMEOUT",
        "EMAIL_TIMEOUT",
        Kind.INT,
        Section.EMAIL,
        _("SMTP timeout (seconds)"),
        _("How long to wait for the mail server, from 1 to 120."),
        15,
        minimum=1,
        maximum=120,
    ),
    Setting(
        "default_from_email",
        "DEFAULT_FROM_EMAIL",
        "DEFAULT_FROM_EMAIL",
        Kind.STR,
        Section.EMAIL,
        _("Sender address"),
        _("Address emails come from, such as HouseCairn <noreply@example.com>."),
        "HouseCairn <noreply@localhost>",
        check=_check_from,
        strict_env=False,
    ),
)


def _rate(
    key: str, env: str, label: Promise, help_text: Promise, default: int, top: int
) -> Setting:
    """A rate limit (docs/adr/0042): never 0 (always refused), never unbounded."""
    return Setting(
        key,
        env,
        env,
        Kind.INT,
        Section.RATE_LIMITS,
        label,
        help_text,
        default,
        minimum=1,
        maximum=top,
    )


_SETTINGS += (
    Setting(
        "instance_name",
        "HOUSECAIRN_INSTANCE_NAME",
        "HOUSECAIRN_INSTANCE_NAME",
        Kind.STR,
        Section.INSTANCE,
        _("Name of the instance"),
        _(
            "In the app bar, the title of the pages and the emails, at most 64 characters. "
            "Empty: HouseCairn."
        ),
        PRODUCT_NAME,
        check=_check_instance_name,
    ),
    Setting(
        "default_language",
        "HOUSECAIRN_DEFAULT_LANGUAGE",
        "HOUSECAIRN_DEFAULT_LANGUAGE",
        Kind.CHOICE,
        Section.INSTANCE,
        _("Default language"),
        _(
            "en or fr. For people whose browser asks for no language the instance offers; "
            "the language a browser asks for always wins."
        ),
        "en",
        choices=LANGUAGES,
    ),
    Setting(
        "time_zone",
        "TIME_ZONE",
        "TIME_ZONE",
        Kind.STR,
        Section.INSTANCE,
        _("Time zone"),
        _(
            "Of the households, such as Europe/Paris: it decides what today is, and so what "
            "is late. Times are stored in UTC."
        ),
        "UTC",
        check=_check_time_zone,
    ),
    _rate(
        "ratelimit_login",
        "HOUSECAIRN_RATELIMIT_LOGIN",
        _("Sign-ins a minute"),
        _("Per client address, from 1 to 1000."),
        30,
        1000,
    ),
    _rate(
        "ratelimit_login_failures",
        "HOUSECAIRN_RATELIMIT_LOGIN_FAILURES",
        _("Failed sign-ins in five minutes"),
        _("Per account, from 1 to 100; then the account waits."),
        5,
        100,
    ),
    _rate(
        "ratelimit_signup",
        "HOUSECAIRN_RATELIMIT_SIGNUP",
        _("Sign-ups a minute"),
        _("Per client address, from 1 to 1000."),
        10,
        1000,
    ),
    _rate(
        "ratelimit_signup_day",
        "HOUSECAIRN_RATELIMIT_SIGNUP_DAY",
        _("Sign-ups a day"),
        _("Per client address, from 1 to 100000; not less than a minute's."),
        60,
        100_000,
    ),
    _rate(
        "ratelimit_password_reset",
        "HOUSECAIRN_RATELIMIT_PASSWORD_RESET",
        _("Password reset requests a minute"),
        _("Per client address, from 1 to 1000; an address gets at most 5 a minute."),
        20,
        1000,
    ),
    _rate(
        "ratelimit_invitations",
        "HOUSECAIRN_RATELIMIT_INVITATIONS",
        _("Invitations sent a minute"),
        _("Per person, to a home or to the instance, from 1 to 1000."),
        10,
        1000,
    ),
    _rate(
        "ratelimit_invitations_day",
        "HOUSECAIRN_RATELIMIT_INVITATIONS_DAY",
        _("Invitations sent a day"),
        _("Per person, from 1 to 100000; not less than a minute's."),
        50,
        100_000,
    ),
    _rate(
        "ratelimit_invitation_links",
        "HOUSECAIRN_RATELIMIT_INVITATION_LINKS",
        _("Invitation links opened a minute"),
        _("Per client address, from 1 to 1000."),
        30,
        1000,
    ),
    _rate(
        "ratelimit_tests",
        "HOUSECAIRN_RATELIMIT_TESTS",
        _("Tests of the mail server or of an issuer a minute"),
        _("Per administrator, from 1 to 60."),
        5,
        60,
    ),
    _rate(
        "ratelimit_link_previews",
        "HOUSECAIRN_RATELIMIT_LINK_PREVIEWS",
        _("Link previews a minute, per person"),
        _(
            "Links added while previews are on, from 1 to 1000; each one makes the server "
            "read a web page."
        ),
        10,
        1000,
    ),
    _rate(
        "ratelimit_link_previews_home",
        "HOUSECAIRN_RATELIMIT_LINK_PREVIEWS_HOME",
        _("Link previews a minute, per home"),
        _("All the members of a home together, from 1 to 1000."),
        30,
        1000,
    ),
    Setting(
        "file_max_bytes",
        "HOUSECAIRN_PHOTO_MAX_BYTES",
        "HOUSECAIRN_PHOTO_MAX_BYTES",
        Kind.INT,
        Section.FILES,
        _("Largest file (bytes)"),
        _(
            "A photo or an attachment, as sent, from 10240 (10 KB) to 104857600 (100 MB); "
            "10485760 is 10 MB."
        ),
        10 * 1024 * 1024,
        minimum=10 * 1024,
        maximum=100 * 1024 * 1024,
    ),
    Setting(
        "home_storage_mb",
        "HOUSECAIRN_HOME_STORAGE_MB",
        "HOUSECAIRN_HOME_STORAGE_MB",
        Kind.INT,
        Section.FILES,
        _("Space for the files of a home (MB)"),
        _(
            "Photos, attachments and their thumbnails, deleted ones included until they are "
            "purged; from 1 to 1048576 (1 TB)."
        ),
        5120,
        minimum=1,
        maximum=1024 * 1024,
    ),
    Setting(
        "home_max_files",
        "HOUSECAIRN_HOME_MAX_FILES",
        "HOUSECAIRN_HOME_MAX_FILES",
        Kind.INT,
        Section.FILES,
        _("Number of files of a home"),
        _("Photos and attachments, deleted ones included until they are purged; 1 to 1000000."),
        5000,
        minimum=1,
        maximum=1_000_000,
    ),
)

SETTINGS: dict[str, Setting] = {s.key: s for s in _SETTINGS}


def in_section(section: Section) -> list[Setting]:
    return [s for s in _SETTINGS if s.section is section]


# What stays in the environment: needed before there is a database, or able to lock the
# administrator out. Shown, without secrets, on the read-only "Server" part of the screen.
SERVER_VARIABLES: tuple[tuple[str, Promise], ...] = (
    ("SECRET_KEY", _("Secret key of the instance")),
    ("ALLOWED_HOSTS", _("Host names the instance answers to")),
    ("CSRF_TRUSTED_ORIGINS", _("Trusted origins for forms")),
    ("DATABASE_URL", _("Database")),
    ("HOUSECAIRN_DATA_DIR", _("Data directory")),
    ("HOUSECAIRN_BEHIND_TLS_PROXY", _("Behind a TLS reverse proxy")),
    ("HOUSECAIRN_FORWARDED_ALLOW_IPS", _("Trusted reverse proxies")),
    ("DEBUG", _("Debug mode")),
    ("HOUSECAIRN_TASK_WORKERS", _("Task worker processes")),
    ("HOUSECAIRN_BOOTSTRAP_ADMIN_EMAIL", _("First administrator")),
    ("HOUSECAIRN_SETUP_SCREEN", _("First-account screen")),
    ("HOUSECAIRN_DEMO", _("Demonstration instance")),
)


def _expected(setting: Setting) -> str:
    """What the variable should hold, in plain English: it is shown while Django starts."""
    if setting.kind is Kind.BOOL:
        return "true or false"
    if setting.kind is Kind.INT:
        return f"a whole number from {setting.minimum} to {setting.maximum}"
    if setting.kind is Kind.CHOICE:
        return "one of " + ", ".join(setting.choices)
    if setting.kind is Kind.LIST:
        return "domain names separated by commas"
    if setting.key == "site_url":
        return "an http:// or https:// address without a path"
    if setting.key == "time_zone":
        return "a time zone of the IANA database, such as Europe/Paris"
    if setting.key == "instance_name":
        return f"a name of at most {INSTANCE_NAME_MAX_LENGTH} characters"
    return "a single line of text"


@dataclass(frozen=True)
class Environment:
    values: dict[str, Any]
    locked: frozenset[str]


def read_environment(environ: dict[str, str] | None = None) -> Environment:
    """Parse the variables of every setting, once, when Django starts.

    A variable that is set and not empty wins and locks its field. An invalid one stops
    the start-up, as before: an operator must see the mistake.
    """
    source = os.environ if environ is None else environ
    values: dict[str, Any] = {}
    locked: set[str] = set()
    for setting in _SETTINGS:
        raw = source.get(setting.env, "")
        if raw.strip():
            try:
                values[setting.key] = setting.parse_environment(raw)
            except ValueError as exc:
                # No translated message here: Django is still loading its settings.
                raise ImproperlyConfigured(
                    f"{setting.env} is not valid: expected {_expected(setting)}."
                ) from exc
            locked.add(setting.key)
        else:
            values[setting.key] = setting.default
    return Environment(values, frozenset(locked))


def boolean_variable(name: str, default: bool, environ: dict[str, str] | None = None) -> bool:
    """A true-or-false variable that only the environment sets, read as strictly as those of
    the settings above: empty gives `default`, any other value than true or false stops the
    start-up."""
    raw = (os.environ if environ is None else environ).get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ImproperlyConfigured(f"{name} is not valid: expected true or false.")


# Settings of other apps, declared next to their code (docs/adr/0043).
from housecairn.notifications.conf import SETTINGS as _NOTIFICATIONS  # noqa: E402

_SETTINGS += _NOTIFICATIONS
SETTINGS.update({s.key: s for s in _NOTIFICATIONS})

from housecairn.projects.conf import SETTINGS as _PROJECTS  # noqa: E402

_SETTINGS += _PROJECTS
SETTINGS.update({s.key: s for s in _PROJECTS})

from housecairn.apitokens.conf import SETTINGS as _APITOKENS  # noqa: E402

_SETTINGS += _APITOKENS
SETTINGS.update({s.key: s for s in _APITOKENS})

from housecairn.barcodes.conf import SETTINGS as _BARCODES  # noqa: E402

_SETTINGS += _BARCODES
SETTINGS.update({s.key: s for s in _BARCODES})

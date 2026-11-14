"""The content of More, in four groups: Home, Me, Instance and Help (docs/adr/0017).

One list for the More page of a phone and for the column of a computer, which shows it
unfolded: both always offer the same destinations. The actions that cannot be taken
back (delete an account, delete or export a home) are not here: they are at the bottom
of their own page.
"""

from dataclasses import dataclass, field
from typing import Any

from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from housecairn.core.help import docs_page_url

HOME, ME, INSTANCE, HELP = "home", "me", "instance", "help"


@dataclass(frozen=True)
class Entry:
    label: Any
    url: str
    text: Any = ""
    # Route names whose pages are this entry, to mark it as current in the column; a
    # name ending in "*" is a prefix.
    routes: tuple[str, ...] = ()
    # A count shown next to the label, "Reports (2)"; 0 shows nothing.
    badge: int = 0


@dataclass(frozen=True)
class Group:
    key: str
    label: Any
    entries: list[Entry] = field(default_factory=list)


def is_administrator(user: Any) -> bool:
    """Who administers the instance (docs/adr/0022): staff and superuser."""
    return bool(getattr(user, "is_staff", False) and getattr(user, "is_superuser", False))


def groups(user: Any, home_id: Any = None, reports_waiting: int = 0) -> list[Group]:
    """The groups of More for this person; the Home group is about `home_id`, if any.

    `reports_waiting` is the number of reports waiting for them in that home.
    """
    home: list[Entry] = []
    if home_id:
        kwargs = {"home_id": home_id}
        home = [
            Entry(
                _("Home settings"),
                reverse("homes:detail", kwargs=kwargs),
                _("Name, currency, hemisphere, export"),
                ("homes:detail", "homes:edit", "homes:export", "homes:delete", "homes:audit"),
            ),
            Entry(
                _("Household and roles"),
                reverse("homes:household", kwargs=kwargs),
                _("Who shares this home, and with which rights"),
                ("homes:household", "homes:member", "homes:invite"),
            ),
            Entry(
                _("Reports"),
                reverse("reports:list", kwargs=kwargs),
                _("Problems reported in this home"),
                ("reports:*",),
                reports_waiting,
            ),
            Entry(
                _("Contractors"),
                reverse("tasks:contractors", kwargs=kwargs),
                _("The people and companies who work on the home"),
                ("tasks:contractor*",),
            ),
            Entry(
                _("QR labels"),
                reverse("labels:list", kwargs=kwargs),
                _("Stickers that open a room, an item or an article"),
                ("labels:*",),
            ),
        ]
    home.append(
        Entry(
            _("My homes"),
            reverse("homes:list"),
            _("Add a home or open another one"),
            ("homes:list", "homes:create"),
        )
    )
    me = [
        Entry(_("Profile"), reverse("profile"), _("Your display name"), ("profile",)),
        Entry(
            _("Notifications"),
            reverse("notifications:index"),
            _("Where and when your reminders go"),
            ("notifications:*",),
        ),
        Entry(
            _("Display preferences"),
            reverse("display-preferences"),
            _("Language, and how the Today screen is laid out for you"),
            ("display-preferences",),
        ),
        Entry(
            _("Calendar in another application"),
            reverse("calendar:feeds"),
            _("Secret calendar feeds"),
            ("calendar:feeds", "calendar:regenerate", "calendar:revoke"),
        ),
        Entry(
            _("API tokens"),
            reverse("api-tokens:list"),
            _("Let Home Assistant or another tool read, change or send events to your homes"),
            ("api-tokens:*",),
        ),
        Entry(
            _("Security and sign-in"),
            reverse("more-security"),
            _("Passkeys, password, email addresses, deleting your account"),
            ("more-security", "mfa_*", "account_*"),
        ),
    ]
    result = [Group(HOME, pgettext_lazy("group of More", "Home"), home), Group(ME, _("Me"), me)]
    if is_administrator(user):
        result.append(
            Group(
                INSTANCE,
                _("Instance"),
                [
                    Entry(
                        _("Instance administration"),
                        reverse("instance:overview"),
                        _("Accounts, invitations, settings, audit log"),
                        ("instance:*",),
                    )
                ],
            )
        )
    result.append(
        Group(
            HELP,
            _("Help"),
            [
                Entry(
                    _("Help"),
                    docs_page_url("", translation.get_language()),
                    _("A guide to every screen"),
                ),
                Entry(
                    _("API documentation"),
                    reverse("api-docs"),
                    _("OpenAPI schema and test interface"),
                ),
                Entry(_("About"), reverse("about"), "", ("about",)),
            ],
        )
    )
    return result


def current(entry: Entry, view_name: str) -> bool:
    """Whether the page `view_name` is that of the entry."""
    return bool(view_name) and any(
        view_name.startswith(route[:-1]) if route.endswith("*") else view_name == route
        for route in entry.routes
    )


def for_page(
    user: Any, home_id: Any, view_name: str, reports_waiting: int = 0
) -> list[dict[str, Any]]:
    """The groups as templates show them, each entry with whether it is the current page."""
    return [
        {
            "key": group.key,
            "label": group.label,
            "entries": [
                {
                    "label": e.label,
                    "url": e.url,
                    "text": e.text,
                    "badge": e.badge,
                    "current": current(e, view_name),
                }
                for e in group.entries
            ],
        }
        for group in groups(user, home_id, reports_waiting)
    ]

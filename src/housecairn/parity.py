"""Web/API parity registry (docs/adr/0003).

Every named web route maps to the API route that offers the same function, or
is listed in EXEMPT with the reason it has no API counterpart. The test
`tests/test_parity.py` fails when a web route is in neither.
"""

from collections.abc import Iterator

from django.urls import URLPattern, URLResolver, get_resolver

# Web route name -> API route name(s).
PARITY: dict[str, tuple[str, ...]] = {
    "homes:list": ("api-v1:home-list",),
    "homes:create": ("api-v1:home-list",),
    "homes:detail": ("api-v1:home-detail",),
    "homes:edit": ("api-v1:home-detail",),
    "homes:household": ("api-v1:home-member-list", "api-v1:home-invitation-list"),
    "homes:invite": ("api-v1:home-invitation-list",),
    "homes:invitation-resend": ("api-v1:home-invitation-resend",),
    "homes:invitation-revoke": ("api-v1:home-invitation-detail",),
    "homes:required-provider": ("api-v1:home-detail",),
    "homes:leave": ("api-v1:home-leave",),
    "homes:unlock": ("api-v1:home-unlock",),
    "homes:audit": ("api-v1:home-audit",),
    "homes:export": ("api-v1:home-export",),
    "homes:export-job": ("api-v1:home-export-job",),
    "homes:export-download": ("api-v1:home-export-download",),
    "homes:export-csv": ("api-v1:home-export-csv",),
    "homes:restore": ("api-v1:home-restore",),
    "homes:import": ("api-v1:home-import",),
    "homes:delete": ("api-v1:home-delete-home",),
    "account_delete": ("api-v1:account-delete",),
    "profile": ("api-v1:me",),
    "instance:overview": ("api-v1:instance-invitation-list", "api-v1:instance-audit-list"),
    "instance:invite": ("api-v1:instance-invitation-list",),
    "instance:invitation-resend": ("api-v1:instance-invitation-resend",),
    "instance:invitation-revoke": ("api-v1:instance-invitation-detail",),
    "instance:unlock": ("api-v1:instance-unlock",),
    "instance:audit": ("api-v1:instance-audit-list",),
    "instance:usage": ("api-v1:instance-usage",),
    "instance:settings": ("api-v1:instance-settings",),
    "instance:providers": ("api-v1:instance-provider-list", "api-v1:instance-provider-check"),
    "instance:provider": ("api-v1:instance-provider-detail", "api-v1:instance-provider-check"),
    # Creating the account of an instance invitation is account management, which the
    # API leaves to sign-in (see EXEMPT_MODULE_PREFIXES).
    "instance-invitation": ("api-v1:instance-invitation-list",),
    "homes:member": ("api-v1:home-member-detail",),
    "homes:member-role": ("api-v1:home-member-detail",),
    "homes:member-remove": ("api-v1:home-member-detail",),
    "homes:transfer": ("api-v1:home-member-make-owner",),
    # Creating an account from an invitation is account management, which the
    # API leaves to sign-in (see EXEMPT_MODULE_PREFIXES); accepting is in it.
    "invitation": ("api-v1:invitation-accept",),
    # Rooms, items and photos (docs/adr/0029, 0030). The entry page only leads to
    # the rooms of a home: choosing among the homes is the list of homes.
    "rooms": ("api-v1:home-list",),
    "rooms:list": ("api-v1:home-room-list",),
    "rooms:create": ("api-v1:home-room-list",),
    "rooms:detail": ("api-v1:home-room-detail",),
    "rooms:edit": ("api-v1:home-room-detail",),
    "rooms:memory": ("api-v1:home-room-detail",),
    "rooms:delete": ("api-v1:home-room-detail",),
    "rooms:move": ("api-v1:home-room-reorder",),
    "rooms:item-create": ("api-v1:home-item-list",),
    "rooms:item-detail": ("api-v1:home-item-detail",),
    "rooms:item-edit": ("api-v1:home-item-detail",),
    "rooms:item-delete": ("api-v1:home-item-detail",),
    "rooms:room-photo-add": ("api-v1:home-photo-list",),
    "rooms:item-photo-add": ("api-v1:home-photo-list",),
    "rooms:photo": ("api-v1:home-photo-content",),
    "rooms:photo-thumbnail": ("api-v1:home-photo-thumbnail",),
    "rooms:photo-delete": ("api-v1:home-photo-detail",),
    "rooms:search": ("api-v1:home-search",),
    # Tasks, occurrences and attachments (docs/adr/0031 to 0034). The entry page only leads
    # to the tasks of a home, like the rooms one.
    "tasks": ("api-v1:home-list",),
    "tasks:list": ("api-v1:home-task-list", "api-v1:home-occurrence-list"),
    "tasks:create": ("api-v1:home-task-list",),
    "tasks:templates": ("api-v1:task-template-list", "api-v1:home-task-from-templates"),
    "tasks:detail": ("api-v1:home-task-detail", "api-v1:home-occurrence-list"),
    "tasks:edit": ("api-v1:home-task-detail",),
    "tasks:delete": ("api-v1:home-task-detail",),
    "tasks:material-save": ("api-v1:home-task-material-list", "api-v1:home-task-material-detail"),
    "tasks:material-remove": ("api-v1:home-task-material-detail",),
    "tasks:contractors": ("api-v1:home-contractor-list",),
    "tasks:contractor-create": ("api-v1:home-contractor-list",),
    "tasks:contractor-detail": ("api-v1:home-contractor-detail", "api-v1:home-task-list"),
    "tasks:contractor-edit": ("api-v1:home-contractor-detail",),
    "tasks:contractor-delete": ("api-v1:home-contractor-detail",),
    "tasks:contractor-vcard": ("api-v1:home-contractor-vcard",),
    "tasks:complete": ("api-v1:home-occurrence-complete",),
    "tasks:undo": ("api-v1:home-occurrence-undo",),
    "tasks:skip": ("api-v1:home-occurrence-skip",),
    "tasks:postpone": ("api-v1:home-occurrence-postpone",),
    "tasks:occurrence-edit": ("api-v1:home-occurrence-detail",),
    # Today, My home, History and the display preferences (docs/adr/0037).
    "tasks:today": ("api-v1:home-today",),
    "tasks:history": ("api-v1:home-occurrence-list", "api-v1:home-occurrence-cost"),
    "my-home": ("api-v1:my-home",),
    "display-preferences": ("api-v1:display-preferences",),
    # Stock items, suppliers and movements (docs/adr/0038). The entry page only leads
    # to the stock of a home, like the rooms one.
    "stock": ("api-v1:home-list",),
    "stock:list": ("api-v1:home-article-list",),
    "stock:create": ("api-v1:home-article-list",),
    "stock:detail": (
        "api-v1:home-article-detail",
        "api-v1:home-supplier-list",
        "api-v1:home-movement-list",
    ),
    "stock:edit": ("api-v1:home-article-detail",),
    "stock:delete": ("api-v1:home-article-detail",),
    "stock:label": ("api-v1:home-article-label",),
    "stock:movement-add": ("api-v1:home-movement-list",),
    "stock:supplier-create": ("api-v1:home-supplier-list",),
    "stock:supplier-edit": ("api-v1:home-supplier-detail",),
    "stock:supplier-delete": ("api-v1:home-supplier-detail",),
    # QR labels (docs/adr/0047). A sticker opens /l/<code>/; the API resolves the
    # same code.
    "label-open": ("api-v1:label-resolve",),
    "labels:list": ("api-v1:home-label-list",),
    "labels:create": ("api-v1:home-label-list",),
    "labels:print": ("api-v1:home-label-print",),
    "labels:detail": ("api-v1:home-label-detail",),
    "labels:regenerate": ("api-v1:home-label-regenerate",),
    "labels:revoke": ("api-v1:home-label-detail",),
    "reports:list": ("api-v1:home-report-list",),
    "reports:create": ("api-v1:home-report-list",),
    "reports:detail": ("api-v1:home-report-detail",),
    "reports:photo": ("api-v1:home-report-photo",),
    "reports:photo-thumbnail": ("api-v1:home-report-thumbnail",),
    "reports:to-task": ("api-v1:home-report-task",),
    "reports:close": ("api-v1:home-report-close",),
    "reports:reopen": ("api-v1:home-report-reopen",),
    # Projects, budget lines, quotes, photos and the annual envelope (docs/adr/0050). The entry
    # page only leads to the projects of a home, like the rooms one.
    "projects": ("api-v1:home-list",),
    "projects:list": ("api-v1:home-project-list",),
    "projects:create": ("api-v1:home-project-list",),
    "projects:detail": (
        "api-v1:home-project-detail",
        "api-v1:home-budget-line-list",
        "api-v1:home-quote-list",
        "api-v1:home-project-task-list",
        "api-v1:home-project-file-list",
        "api-v1:home-project-link-list",
    ),
    "projects:edit": ("api-v1:home-project-detail",),
    "projects:delete": ("api-v1:home-project-detail",),
    "projects:compare": ("api-v1:home-project-list", "api-v1:home-budget-line-list"),
    "projects:envelope": ("api-v1:home-envelope",),
    "projects:task-add": ("api-v1:home-project-task-list",),
    "projects:task-attach": ("api-v1:home-project-task-list",),
    "projects:task-detach": ("api-v1:home-project-task-detail",),
    "projects:line-create": ("api-v1:home-budget-line-list",),
    "projects:line-edit": ("api-v1:home-budget-line-detail",),
    "projects:line-delete": ("api-v1:home-budget-line-detail",),
    "projects:quote-create": ("api-v1:home-quote-list",),
    "projects:quote-edit": ("api-v1:home-quote-detail",),
    "projects:quote-accept": ("api-v1:home-quote-accept",),
    "projects:quote-status": ("api-v1:home-quote-detail",),
    "projects:quote-delete": ("api-v1:home-quote-detail",),
    "projects:link-add": ("api-v1:home-project-link-list",),
    "projects:link-delete": ("api-v1:home-project-link-detail",),
    "projects:link-status": ("api-v1:home-project-link-detail",),
    "projects:file-add": ("api-v1:home-project-file-list",),
    "projects:file": ("api-v1:home-project-file-content",),
    "projects:file-thumbnail": ("api-v1:home-project-file-thumbnail",),
    "projects:file-delete": ("api-v1:home-project-file-detail",),
    # The budget of a home and of all of them (docs/adr/0051). The entry page adds the homes
    # up by currency.
    "budget": ("api-v1:budget-combined",),
    "budget:overview": ("api-v1:home-budget",),
    "budget:rooms": ("api-v1:home-budget-rooms",),
    # The budget of one room: its row of the budget by room, line by line from the
    # projects and tasks of the room.
    "budget:room": ("api-v1:home-budget-rooms",),
    "budget:projects": ("api-v1:home-budget-projects",),
    "budget:tasks": ("api-v1:home-budget-tasks",),
    "notifications:index": ("api-v1:notification-channel-list", "api-v1:notification-preferences"),
    "notifications:add": ("api-v1:notification-channel-list",),
    "notifications:channel": ("api-v1:notification-channel-detail",),
    "notifications:test": ("api-v1:notification-channel-test",),
    "notifications:delete": ("api-v1:notification-channel-detail",),
    "notifications:test-status": ("api-v1:notification-channel-detail",),
    "notifications:preferences": ("api-v1:notification-preferences",),
    "notifications:export": ("api-v1:notification-export",),
    "notifications:device": ("api-v1:push-device-detail",),
    "notifications:device-test": ("api-v1:push-device-test",),
    "notifications:device-delete": ("api-v1:push-device-detail",),
    "api-tokens:list": ("api-v1:token-list",),
    "api-tokens:new": ("api-v1:token-list",),
    "api-tokens:revoke": ("api-v1:token-detail",),
    # Calendar and its feeds (docs/adr/0052). The feed itself is a document for a calendar
    # application, at a secret address (see EXEMPT).
    "calendar:index": ("api-v1:calendar",),
    "calendar:week": ("api-v1:calendar",),
    "calendar:month": ("api-v1:calendar",),
    "calendar:year": ("api-v1:calendar",),
    "calendar:feeds": ("api-v1:calendar-feed-list",),
    "calendar:regenerate": ("api-v1:calendar-feed-regenerate",),
    "calendar:revoke": ("api-v1:calendar-feed-detail",),
    "tasks:attachment-add": ("api-v1:home-attachment-list",),
    "tasks:attachment": ("api-v1:home-attachment-content",),
    "tasks:attachment-thumbnail": ("api-v1:home-attachment-thumbnail",),
    "tasks:attachment-delete": ("api-v1:home-attachment-detail",),
}

# Web routes without business function, with the reason.
EXEMPT: dict[str, str] = {
    "index": "landing page, no data",
    "healthz": "probe for the container runtime",
    "api-schema": "API description itself",
    "api-docs": "API documentation page",
    "manifest": "web app manifest read by browsers",
    "service-worker": "script of the installed application, read by browsers (docs/adr/0065)",
    "offline": "page shown offline for a page that was not kept, holds no data",
    "set_language": "language cookie of the signed-out pages; the API reads the person's choice",
    "more": "navigation page that links to other screens",
    "homes:current": "choice of the home a section opens on, kept in the web session only",
    "more-security": "navigation page that links to the account security screens",
    "about": "static information page",
    "setup": "first-account screen opened by a code of the server log, before any API token exists",
    "calendar:feed": "iCalendar document read by a calendar application, at a secret address",
    "tasks:repeat-summary": "sentence of the task form while it is typed; the API has the fields",
}

# Third-party route trees: sign-in, administration. Their functions are either
# account management, which the API does not expose to tokens, or operator tools.
EXEMPT_NAMESPACES: frozenset[str] = frozenset({"admin", "api-v1"})
EXEMPT_MODULE_PREFIXES: tuple[str, ...] = ("allauth.",)


def _walk(
    patterns: list[URLPattern | URLResolver], namespace: str = ""
) -> Iterator[tuple[str, str]]:
    for entry in patterns:
        if isinstance(entry, URLResolver):
            ns = entry.namespace or ""
            full = f"{namespace}:{ns}" if namespace and ns else (ns or namespace)
            yield from _walk(entry.url_patterns, full)
        elif entry.name:
            name = f"{namespace}:{entry.name}" if namespace else entry.name
            module = getattr(entry.callback, "__module__", "") or ""
            yield name, module


def all_routes() -> dict[str, str]:
    """Every named route, with the module of its view."""
    return dict(_walk(get_resolver().url_patterns))


def web_routes() -> set[str]:
    routes = set()
    for name, module in all_routes().items():
        if name.split(":", 1)[0] in EXEMPT_NAMESPACES and ":" in name:
            continue
        if module.startswith(EXEMPT_MODULE_PREFIXES):
            continue
        routes.add(name)
    return routes


def missing_api_counterparts() -> list[str]:
    """Web routes neither mapped to the API nor exempted."""
    return sorted(r for r in web_routes() if r not in PARITY and r not in EXEMPT)


def unknown_api_routes() -> list[str]:
    """API names in PARITY that do not exist."""
    names = set(all_routes())
    return sorted(api for apis in PARITY.values() for api in apis if api not in names)

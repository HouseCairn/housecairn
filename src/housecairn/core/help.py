"""Help link of each screen: the page of the documentation site that describes it.

Every named web route is either given a documentation page here or listed as
not being a screen; `tests/test_help_links.py` enforces it, and checks that the
pages exist in the built site (docs/adr/0016).
"""

from django.conf import settings
from django.http import HttpRequest
from django.utils import translation

# Route name -> page of the documentation site (a directory, as published).
HELP_PAGES: dict[str, str] = {
    "index": "screens/start-page/",
    "account_login": "screens/sign-in/",
    "setup": "screens/first-account/",
    "homes:list": "screens/homes/",
    "homes:create": "screens/new-home/",
    "homes:detail": "screens/home/",
    "homes:edit": "screens/home/",
    "homes:household": "screens/household/",
    "homes:invite": "screens/household/",
    "homes:invitation-resend": "screens/household/",
    "homes:invitation-revoke": "screens/household/",
    "homes:required-provider": "screens/household/",
    "homes:leave": "screens/member/",
    "homes:unlock": "screens/household/",
    "homes:audit": "screens/audit/",
    "homes:export": "screens/home/",
    "homes:export-job": "administration/data/",
    "homes:export-download": "administration/data/",
    "homes:export-csv": "administration/data/",
    "homes:restore": "administration/data/",
    "homes:import": "screens/home-import/",
    "homes:delete": "screens/home/",
    "account_delete": "screens/account/",
    "profile": "screens/profile/",
    "instance:overview": "screens/instance/",
    "instance:invite": "screens/instance/",
    "instance:invitation-resend": "screens/instance/",
    "instance:invitation-revoke": "screens/instance/",
    "instance:unlock": "screens/instance/",
    "instance:audit": "screens/instance/",
    "instance:usage": "screens/instance-usage/",
    "instance:settings": "screens/instance-settings/",
    "instance:providers": "screens/sign-in-providers/",
    "instance:provider": "screens/sign-in-providers/",
    "instance-invitation": "screens/instance-invitation/",
    "homes:member": "screens/member/",
    "homes:member-role": "screens/member/",
    "homes:member-remove": "screens/member/",
    "homes:transfer": "screens/member/",
    "invitation": "screens/invitation/",
    "rooms": "screens/rooms/",
    "rooms:list": "screens/rooms/",
    "rooms:create": "screens/room-form/",
    "rooms:detail": "screens/room/",
    "rooms:edit": "screens/room-form/",
    "rooms:memory": "screens/room-memory/",
    "rooms:delete": "screens/room/",
    "rooms:move": "screens/rooms/",
    "rooms:item-create": "screens/item-form/",
    "rooms:item-detail": "screens/item/",
    "rooms:item-edit": "screens/item-form/",
    "rooms:item-delete": "screens/item/",
    "rooms:room-photo-add": "screens/photo-form/",
    "rooms:item-photo-add": "screens/photo-form/",
    "rooms:photo": "screens/photo-form/",
    "rooms:photo-thumbnail": "screens/photo-form/",
    "rooms:photo-delete": "screens/photo-form/",
    "rooms:search": "screens/search/",
    "my-home": "screens/my-home/",
    "display-preferences": "screens/display-preferences/",
    "tasks:today": "screens/today/",
    "tasks:history": "screens/history/",
    "tasks": "screens/tasks/",
    "tasks:list": "screens/tasks/",
    "tasks:create": "screens/task-form/",
    "tasks:templates": "screens/task-templates/",
    "tasks:detail": "screens/task/",
    "tasks:edit": "screens/task-form/",
    "tasks:repeat-summary": "screens/task-form/",
    "tasks:delete": "screens/task/",
    "tasks:complete": "screens/task/",
    "tasks:undo": "screens/task/",
    "tasks:skip": "screens/task/",
    "tasks:postpone": "screens/task/",
    "tasks:occurrence-edit": "screens/task/",
    "stock": "screens/stock/",
    "stock:list": "screens/stock/",
    "stock:create": "screens/stock-item-form/",
    "stock:detail": "screens/stock-item/",
    "stock:edit": "screens/stock-item-form/",
    "stock:delete": "screens/stock-item/",
    "stock:label": "screens/stock-item/",
    "stock:movement-add": "screens/stock-item/",
    "stock:supplier-create": "screens/supplier-form/",
    "stock:supplier-edit": "screens/supplier-form/",
    "stock:supplier-delete": "screens/supplier-form/",
    "projects": "screens/projects/",
    "projects:list": "screens/projects/",
    "projects:create": "screens/project-form/",
    "projects:detail": "screens/project/",
    "projects:edit": "screens/project-form/",
    "projects:delete": "screens/project/",
    "projects:compare": "screens/project-compare/",
    "projects:envelope": "screens/projects/",
    "projects:task-add": "screens/project/",
    "projects:task-attach": "screens/project/",
    "projects:task-detach": "screens/project/",
    "projects:line-create": "screens/budget-line-form/",
    "projects:line-edit": "screens/budget-line-form/",
    "projects:line-delete": "screens/budget-line-form/",
    "projects:quote-create": "screens/quote-form/",
    "projects:quote-edit": "screens/quote-form/",
    "projects:quote-accept": "screens/project/",
    "projects:quote-status": "screens/project/",
    "projects:quote-delete": "screens/quote-form/",
    "projects:link-add": "screens/project/",
    "projects:link-delete": "screens/project/",
    "projects:link-status": "screens/project/",
    "projects:file-add": "screens/project/",
    "projects:file": "screens/project/",
    "projects:file-thumbnail": "screens/project/",
    "projects:file-delete": "screens/project/",
    "budget": "screens/budget/",
    "budget:overview": "screens/budget/",
    "budget:rooms": "screens/budget-rooms/",
    "budget:room": "screens/budget-rooms/",
    "budget:projects": "screens/budget-projects/",
    "budget:tasks": "screens/budget-tasks/",
    "tasks:contractors": "screens/contractors/",
    "tasks:contractor-detail": "screens/contractor/",
    "tasks:contractor-vcard": "screens/contractor/",
    "tasks:contractor-create": "screens/contractor-form/",
    "tasks:contractor-edit": "screens/contractor-form/",
    "tasks:contractor-delete": "screens/contractor/",
    "tasks:attachment-add": "screens/task-files/",
    "tasks:attachment": "screens/task-files/",
    "tasks:attachment-thumbnail": "screens/task-files/",
    "tasks:attachment-delete": "screens/task-files/",
    "tasks:material-save": "screens/task/",
    "tasks:material-remove": "screens/task/",
    "label-open": "screens/qr-labels/",
    "labels:list": "screens/qr-labels/",
    "labels:create": "screens/qr-labels/",
    "labels:print": "screens/qr-labels/",
    "labels:detail": "screens/qr-label/",
    "labels:regenerate": "screens/qr-label/",
    "labels:revoke": "screens/qr-label/",
    "reports:list": "screens/reports/",
    "reports:create": "screens/report-form/",
    "reports:detail": "screens/report/",
    "reports:photo": "screens/report/",
    "reports:photo-thumbnail": "screens/report/",
    "reports:to-task": "screens/report/",
    "reports:close": "screens/report/",
    "reports:reopen": "screens/report/",
    "more": "screens/more/",
    "more-security": "screens/security-and-sign-in/",
    "api-tokens:list": "screens/api-tokens/",
    "api-tokens:new": "screens/api-tokens/",
    "api-tokens:revoke": "screens/api-tokens/",
    "about": "screens/about/",
}
# Other routes of django-allauth (sign-up, password, email addresses,
# two-factor authentication, single sign-on): the account pages.
HELP_PREFIXES: dict[str, str] = {
    "account_": "screens/account/",
    "mfa_": "screens/account/",
    "socialaccount_": "screens/account/",
    "openid_connect_": "screens/account/",
    "notifications:": "screens/notifications/",
    "calendar:": "screens/calendar/",
}
# Routes that are not screens of the interface: the API, its schema and
# explorer, the health check, the web app manifest, Django's administration site, and
# the POST-only route that sets the current home (it redirects, it has no page), the
# service worker and the page it shows offline (docs/adr/0065).
NOT_SCREENS: tuple[str, ...] = (
    "api-",
    "healthz",
    "manifest",
    "admin:",
    "homes:current",
    "service-worker",
    "offline",
    # The language of the signed-out pages: a form's target, not a screen (docs/adr/0074).
    "set_language",
)

# Languages published by the documentation site, and their path.
DOCS_LANGUAGES: dict[str, str] = {"en": "", "fr": "fr/"}


def help_page(route: str) -> str | None:
    """Documentation page of the route, or None if it is not a screen."""
    if route in HELP_PAGES:
        return HELP_PAGES[route]
    for prefix, page in HELP_PREFIXES.items():
        if route.startswith(prefix):
            return page
    return None


def help_url(route: str, language: str | None) -> str | None:
    page = help_page(route)
    if page is None:
        return None
    code = (language or settings.LANGUAGE_CODE).split("-")[0].lower()
    prefix = DOCS_LANGUAGES.get(code, "")
    return f"{settings.HOUSECAIRN_DOCS_URL}/{prefix}{page}"


def docs_page_url(page: str, language: str | None) -> str:
    """Address of a page of the documentation site, in the language of the interface."""
    code = (language or settings.LANGUAGE_CODE).split("-")[0].lower()
    return f"{settings.HOUSECAIRN_DOCS_URL}/{DOCS_LANGUAGES.get(code, '')}{page}"


def help_link(request: HttpRequest) -> dict[str, str | None]:
    """Template context: `help_url`, in the language of the interface."""
    match = request.resolver_match
    if match is None or not match.view_name:
        return {"help_url": None}
    return {"help_url": help_url(match.view_name, translation.get_language())}

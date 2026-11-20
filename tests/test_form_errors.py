"""A refused form says why at the top, and the summary links to the fields in error.

One component (`components/form_errors.html`) is included by every template that draws a
form. The parametrized test posts an invalid form to each screen and reads the page back;
the scan test makes a form added without the component fail (design guide, "Forms").
"""

import re
import time
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from housecairn.accounts import setup
from housecairn.homes import services as home_services
from housecairn.homes.models import Home, Membership
from housecairn.instance import services as instance_services
from housecairn.notifications.models import Device
from housecairn.notifications.services import add_channel
from housecairn.projects.models import BudgetLine, Project, Quote
from housecairn.rooms.models import Item, Room
from housecairn.stock.models import Article
from housecairn.tasks.models import Occurrence, Task

pytest_plugins = (
    "tests.rooms_fixtures",
    "tests.tasks_fixtures",
    "tests.projects_fixtures",
    "tests.stock_fixtures",
)

pytestmark = pytest.mark.django_db

PROVIDERS = {
    "openid_connect": {
        "APPS": [
            {
                "provider_id": "company",
                "name": "Company SSO",
                "client_id": "housecairn",
                "secret": "not-a-secret",
                "settings": {"server_url": "https://id.example.com"},
            }
        ]
    }
}

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "housecairn" / "templates"


class _Page(HTMLParser):
    """The summary, the ids of the page, the fields in error and the folded sections."""

    def __init__(self) -> None:
        super().__init__()
        self.summary: dict[str, str | None] | None = None
        self.links: list[str] = []
        self.ids: set[str] = set()
        self.invalid = 0
        self.invalid_in_closed_section = 0
        self._in_summary = 0
        self._sections: list[bool] = []  # one flag per open <details>: is it unfolded?

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"] or "")
        if tag == "details":
            self._sections.append("open" in a)
        if "data-form-errors" in a:
            self.summary = a
            self._in_summary = 1
        elif self._in_summary and tag == "div":
            self._in_summary += 1
        if self._in_summary and tag == "a" and (a.get("href") or "").startswith("#"):
            self.links.append((a["href"] or "")[1:])
        if a.get("aria-invalid") == "true":
            self.invalid += 1
            if self._sections and not all(self._sections):
                self.invalid_in_closed_section += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "details" and self._sections:
            self._sections.pop()
        if tag == "div" and self._in_summary:
            self._in_summary -= 1


def read(html: str) -> _Page:
    page = _Page()
    page.feed(html)
    return page


Case = tuple[str, Callable[[dict[str, Any]], str], dict[str, Any]]


# Every screen that shows a form in the page, with an invalid post. A form sent with an
# error comes back as the page itself (200, or 422), never as a redirect.
def _link_page(c: dict[str, Any]) -> str:
    """The page that links stock items to an item: it has a form once one is left to link."""
    from housecairn.stock import services as stock_services

    stock_services.create_article(c["alice"], c["home"], name="Not linked yet")
    return reverse("stock:item-link", args=[c["home"].id, c["item"].id])


CASES: list[Case] = [
    ("room-create", lambda c: reverse("rooms:create", args=[c["home"].id]), {"kind": "other"}),
    (
        "room-edit",
        lambda c: reverse("rooms:edit", args=[c["home"].id, c["room"].id]),
        {"kind": "other", "name": ""},
    ),
    (
        "item-create",
        lambda c: reverse("rooms:item-create", args=[c["home"].id, c["room"].id]),
        {"name": ""},
    ),
    (
        "item-edit",
        lambda c: reverse("rooms:item-edit", args=[c["home"].id, c["item"].id]),
        {"name": ""},
    ),
    (
        "room-photo",
        lambda c: reverse("rooms:room-photo-add", args=[c["home"].id, c["room"].id]),
        {},
    ),
    ("task-create", lambda c: reverse("tasks:create", args=[c["home"].id]), {"title": ""}),
    (
        "task-edit",
        lambda c: reverse("tasks:edit", args=[c["home"].id, c["task"].id]),
        {"title": ""},
    ),
    (
        "task-edit-folded",
        lambda c: reverse("tasks:edit", args=[c["home"].id, c["task"].id]),
        {"title": "A task", "repeats": "on", "repeat_interval": "0", "repeat_unit": "months"},
    ),
    (
        # Only `repeat_unit` is wrong, and `repeats` is off: nothing else opens the section.
        "task-edit-folded-unit",
        lambda c: reverse("tasks:edit", args=[c["home"].id, c["task"].id]),
        {
            "title": "A task",
            "priority": "normal",
            "hide_days_before": "0",
            "repeat_interval": "2",
            "repeat_unit": "fortnights",
            "materials-TOTAL_FORMS": "0",
            "materials-INITIAL_FORMS": "0",
        },
    ),
    ("task-templates", lambda c: reverse("tasks:templates", args=[c["home"].id]), {}),
    (
        "task-attachment",
        lambda c: reverse("tasks:attachment-add", args=[c["home"].id, c["task"].id]),
        {},
    ),
    (
        "task-postpone",
        lambda c: reverse("tasks:postpone", args=[c["home"].id, c["task"].id, c["occurrence"].id]),
        {"until": "not a date"},
    ),
    (
        "task-completion",
        lambda c: reverse(
            "tasks:occurrence-edit", args=[c["home"].id, c["task"].id, c["occurrence"].id]
        ),
        {"completed_on": "not a date"},
    ),
    (
        "contractor-create",
        lambda c: reverse("tasks:contractor-create", args=[c["home"].id]),
        {"name": ""},
    ),
    ("preferences", lambda c: reverse("display-preferences"), {"horizon_days": "-4"}),
    ("project-create", lambda c: reverse("projects:create", args=[c["home"].id]), {"title": ""}),
    (
        "project-edit-folded",
        lambda c: reverse("projects:edit", args=[c["home"].id, c["project"].id]),
        {"title": "A project", "season": "custom"},
    ),
    # Forms of the project page: a refused one comes back as the page itself, not as a redirect.
    (
        "project-task-add",
        lambda c: reverse("projects:task-add", args=[c["home"].id, c["project"].id]),
        {"title": ""},
    ),
    (
        "project-link-add",
        lambda c: reverse("projects:link-add", args=[c["home"].id, c["project"].id]),
        {"url": "javascript:alert(1)"},
    ),
    (
        "project-photo-add",
        lambda c: reverse("projects:file-add", args=[c["home"].id, c["project"].id]),
        {},
    ),
    (
        # Only `duration_unit` is wrong: nothing else opens the section of the form.
        "project-edit-folded-unit",
        lambda c: reverse("projects:edit", args=[c["home"].id, c["project"].id]),
        {"title": "A project", "duration_value": "2", "duration_unit": "fortnights"},
    ),
    (
        "line-create",
        lambda c: reverse("projects:line-create", args=[c["home"].id, c["project"].id]),
        {"label": ""},
    ),
    (
        "quote-create",
        lambda c: reverse("projects:quote-create", args=[c["home"].id, c["project"].id]),
        {"contractor_name": ""},
    ),
    ("envelope", lambda c: reverse("projects:envelope", args=[c["home"].id]), {"amount": "x"}),
    ("article-create", lambda c: reverse("stock:create", args=[c["home"].id]), {"name": ""}),
    (
        "item-link",
        lambda c: _link_page(c),
        {},
    ),
    (
        "supplier-create",
        lambda c: reverse("stock:supplier-create", args=[c["home"].id, c["article"].id]),
        {"name": ""},
    ),
    ("report-create", lambda c: reverse("reports:create", args=[c["home"].id]), {"title": ""}),
    ("home-create", lambda c: reverse("homes:create"), {"name": ""}),
    ("home-edit", lambda c: reverse("homes:edit", args=[c["home"].id]), {"name": ""}),
    (
        "household-invite",
        lambda c: reverse("homes:invite", args=[c["home"].id]),
        {"email": "not an address", "role": "member"},
    ),
    (
        "household-provider",
        lambda c: reverse("homes:required-provider", args=[c["home"].id]),
        {"provider": "unknown"},
    ),
    ("instance-invite", lambda c: reverse("instance:invite"), {"email": "not an address"}),
    (
        "member-role",
        lambda c: reverse("homes:member-role", args=[c["home"].id, c["membership"].id]),
        {"role": "nobody"},
    ),
    ("home-delete", lambda c: reverse("homes:delete", args=[c["home"].id]), {"confirmation": "x"}),
    ("account-delete", lambda c: reverse("account_delete"), {"confirm_email": "other@example.com"}),
    (
        "room-memory",
        lambda c: reverse("rooms:memory", args=[c["home"].id, c["room"].id]),
        {"paint_colour": "x" * 500},
    ),
    (
        "project-task-attach",
        lambda c: reverse("projects:task-attach", args=[c["home"].id, c["project"].id]),
        {"task": "not a task"},
    ),
    ("instance-provider", lambda c: reverse("instance:providers"), {"name": ""}),
    (
        "instance-settings",
        lambda c: reverse("instance:settings", kwargs={"section": "email"}),
        {"email_port": "70000", "email_timeout": "15"},
    ),
    ("home-import", lambda c: reverse("homes:import", args=[c["home"].id]), {}),
    ("home-restore", lambda c: reverse("homes:restore"), {}),
    ("profile", lambda c: reverse("profile"), {"display_name": "x" * 300}),
    (
        "notification-channel-edit",
        lambda c: reverse("notifications:channel", args=[c["channel"].id]),
        {"name": ""},
    ),
    (
        "notification-device-edit",
        lambda c: reverse("notifications:device", args=[c["device"].id]),
        {"name": ""},
    ),
    (
        "notification-preferences",
        lambda c: reverse("notifications:preferences"),
        {"days_before": "-4", "send_at": "x"},
    ),
    ("notification-channel", lambda c: reverse("notifications:add"), {"name": "", "url": ""}),
    ("api-token", lambda c: reverse("api-tokens:new"), {"name": ""}),
    (
        "connected-app",
        lambda c: reverse("api-tokens:connection-edit", args=[_connection(c).id]),
        {"name": "Phone", "homes": []},
    ),
    # Over HTTPS, as the transport rule of the consent page requires (docs/adr/0078).
    (
        "app-consent",
        lambda c: "https://testserver" + reverse("oauth2_provider:authorize"),
        {
            "client_id": "housecairn-mobile",
            "redirect_uri": "app.housecairn.mobile:/oauth/callback",
            "response_type": "code",
            "scope": "sync",
            "state": "s",
            "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            "code_challenge_method": "S256",
            "allow": "1",
        },
    ),
]


def _connection(c: dict[str, Any]) -> Any:
    from housecairn.apitokens.models import AppConnection

    connection = AppConnection.objects.create(user=c["alice"], name="Phone")
    connection.homes.set([c["home"]])
    return connection


# Refused for a reason that belongs to no field (the files together): the summary says it,
# with no link.
NO_FIELD = {"home-import"}


@pytest.fixture
def context(
    signed_in: Client,
    alice: Any,
    alice_home: Home,
    owner_room: Room,
    owner_item: Item,
    owner_task: Task,
    owner_occurrence: Occurrence,
    living_room_project: Project,
    article: Article,
    labour_line: BudgetLine,
    quote: Quote,
    settings: Any,
) -> dict[str, Any]:
    settings.HOUSECAIRN_NOTIFICATIONS_ENABLED = True  # the screen shows its forms
    settings.SOCIALACCOUNT_PROVIDERS = PROVIDERS  # the household offers the sign-in rule
    settings.EMAIL_HOST = "smtp.example.com"  # the instance can invite
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    alice.is_staff = alice.is_superuser = True  # an instance administrator...
    alice.save()
    session = signed_in.session  # ...who has just typed the password (settings ask for it)
    session["account_authentication_methods"] = [{"method": "password", "at": time.time()}]
    session["account_authentication_methods_user"] = str(alice.pk)
    session.save()
    return {
        "home": alice_home,
        "room": owner_room,
        "item": owner_item,
        "task": owner_task,
        "occurrence": owner_occurrence,
        "project": living_room_project,
        "article": article,
        "membership": Membership.objects.get(home=alice_home, user=alice),
        "channel": add_channel(
            alice,
            name="Phone",
            url="tgram://123456789:AAAA-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/987654",
        ),
        "device": Device.objects.create(
            user=alice,
            name="Phone",
            endpoint="sealed",
            endpoint_hash="0" * 64,
            p256dh="sealed",
            auth="sealed",
            service="Google",
        ),
        "alice": alice,
    }


def assert_summary(name: str, response: Any, statuses: tuple[int, ...] = (200, 422)) -> None:
    assert response.status_code in statuses, f"{name}: the form was not refused"
    page = read(response.content.decode())
    assert page.summary is not None, f"{name}: no summary of the errors"
    assert page.summary.get("role") == "alert"
    assert page.summary.get("tabindex") == "-1"
    assert "autofocus" in page.summary
    if name in NO_FIELD:
        assert not page.links
        return
    assert page.links, f"{name}: the summary links to no field"
    missing = [target for target in page.links if target not in page.ids]
    assert not missing, f"{name}: the summary links to ids the page does not have: {missing}"
    assert page.invalid, f"{name}: no field is marked aria-invalid"
    assert page.invalid_in_closed_section == 0, f"{name}: a field in error is in a folded section"


@pytest.mark.parametrize(("name", "url", "data"), CASES, ids=[c[0] for c in CASES])
def test_a_refused_form_gets_the_summary(
    name: str,
    url: Callable[[dict[str, Any]], str],
    data: dict[str, Any],
    signed_in: Client,
    context: dict[str, Any],
) -> None:
    target = url(context)
    # A case whose address starts with https:// is posted over HTTPS (docs/adr/0078).
    secure = target.startswith("https://")
    assert_summary(
        name, signed_in.post(target.removeprefix("https://testserver"), data, secure=secure)
    )


def _home_invitation(c: dict[str, Any]) -> str:
    sent: list[str] = []
    delivery = home_services.Delivery(send=lambda _invitation, raw: sent.append(raw))
    home_services.invite(
        c["alice"], c["home"], email="new@example.com", role="member", delivery=delivery
    )
    return reverse("invitation", kwargs={"token": sent[0]})


def _instance_invitation(c: dict[str, Any]) -> str:
    sent: list[str] = []
    instance_services.invite(
        c["alice"], email="other@example.com", send=lambda _invitation, raw: sent.append(raw)
    )
    return reverse("instance-invitation", kwargs={"token": sent[0]})


# Pages of someone who has no account yet: no password typed.
ANONYMOUS: list[Case] = [
    ("home-invitation", _home_invitation, {"password1": "", "password2": ""}),
    ("instance-invitation", _instance_invitation, {"password1": "", "password2": ""}),
]


@pytest.mark.parametrize(("name", "url", "data"), ANONYMOUS, ids=[c[0] for c in ANONYMOUS])
def test_a_refused_form_of_someone_signed_out_gets_the_summary(
    name: str,
    url: Callable[[dict[str, Any]], str],
    data: dict[str, Any],
    context: dict[str, Any],
) -> None:
    assert_summary(name, Client().post(url(context), data))


def test_the_setup_screen_gets_the_summary() -> None:
    """The first administrator: a refused code or field is a 400, and says why."""
    code = setup.issue_code()
    data = {
        "code": code,
        "name": "",
        "email": "sam@example.com",
        "password1": "a",
        "password2": "b",
    }
    assert_summary("setup", Client().post(reverse("setup"), data), statuses=(400,))


@pytest.mark.parametrize("language", ["en", "fr"])
def test_the_summary_counts_the_errors(language: str, signed_in: Client, alice_home: Home) -> None:
    signed_in.post(reverse("profile"), {"language": language})
    response = signed_in.post(reverse("rooms:create", args=[alice_home.id]), {"kind": "other"})
    html = response.content.decode()
    title = re.search(r'class="form-errors-title">([^<]*)<', html)
    assert title is not None
    assert re.search(r"\d", title.group(1))


def test_nothing_is_drawn_for_a_form_without_error(signed_in: Client, alice_home: Home) -> None:
    html = signed_in.get(reverse("rooms:create", args=[alice_home.id])).content.decode()
    assert "data-form-errors" not in html


BANNER_CASES = [
    ("movement", "stock:movement-add", lambda c: [c["home"].id, c["article"].id], {"kind": "in"}),
    (
        "material",
        "tasks:material-save",
        lambda c: [c["home"].id, c["task"].id],
        {"quantity": "1"},
    ),
]


@pytest.mark.parametrize(
    ("name", "view", "args", "data"), BANNER_CASES, ids=[c[0] for c in BANNER_CASES]
)
def test_a_banner_form_names_the_field_and_says_it_once(
    name: str,
    view: str,
    args: Callable[[dict[str, Any]], list[Any]],
    data: dict[str, Any],
    signed_in: Client,
    context: dict[str, Any],
) -> None:
    """An action in place says why in the banner: the field first, never the bare message."""
    response = signed_in.post(reverse(view, args=args(context)), data, follow=True)
    shown = re.findall(r'<li class="message[^"]*">([^<]*)</li>', response.content.decode())
    assert shown, f"{name}: no message"
    assert all(": " in text for text in shown), f"{name}: a message without its field: {shown}"
    assert len(shown) == len(set(shown)), f"{name}: the same message twice: {shown}"
    assert not any(text.startswith("This field") for text in shown), shown


# The forms that report an error in the banner of the page they redirect to (an action in
# place, on a page that already shows the record), by the name of the address they post to.
BANNER_FORMS = {"stock:movement-add", "tasks:material-save"}

FORM_RENDERING = re.compile(
    r"\.as_p\b|\{\{\s*field\b|_field\.html|label_tag|\{\{\s*\w*form\.\w+\s*\}\}"
)
COMPONENT = "components/form_errors.html"

# The case of CASES (or ANONYMOUS, or the setup test) that refuses each form of a template.
TEMPLATE_CASES: dict[str, list[str]] = {
    "accounts/delete.html": ["account-delete"],
    "accounts/profile.html": ["profile"],
    "accounts/setup.html": ["setup"],
    "apitokens/token_form.html": ["api-token"],
    "apitokens/connection_form.html": ["connected-app"],
    "apitokens/consent.html": ["app-consent"],
    "homes/delete.html": ["home-delete"],
    "homes/home_form.html": ["home-create", "home-edit"],
    "homes/household.html": ["household-invite", "household-provider"],
    "homes/import.html": ["home-import"],
    "homes/invitation.html": ["home-invitation"],
    "homes/member.html": ["member-role"],
    "homes/restore.html": ["home-restore"],
    "instance/invitation.html": ["instance-invitation"],
    "instance/overview.html": ["instance-invite"],
    "instance/settings_section.html": ["instance-settings"],
    "notifications/channel.html": ["notification-channel-edit"],
    "notifications/device.html": ["notification-device-edit"],
    "notifications/index.html": ["notification-channel", "notification-preferences"],
    "projects/envelope_form.html": ["envelope"],
    "projects/line_form.html": ["line-create"],
    "projects/project_detail.html": [
        "project-task-add",
        "project-task-attach",
        "project-link-add",
        "project-photo-add",
    ],
    "projects/project_form.html": ["project-create", "project-edit-folded"],
    "projects/quote_form.html": ["quote-create"],
    "reports/report_form.html": ["report-create"],
    "rooms/item_form.html": ["item-create", "item-edit"],
    "rooms/photo_form.html": ["room-photo"],
    "rooms/room_form.html": ["room-create", "room-edit"],
    "rooms/room_memory_form.html": ["room-memory"],
    "stock/article_form.html": ["article-create"],
    "stock/item_link_form.html": ["item-link"],
    "stock/supplier_form.html": ["supplier-create"],
    "tasks/attachment_form.html": ["task-attachment"],
    "tasks/completion_form.html": ["task-completion"],
    "tasks/contractor_form.html": ["contractor-create"],
    "tasks/postpone_form.html": ["task-postpone"],
    "tasks/preferences.html": ["preferences"],
    "tasks/task_form.html": ["task-create", "task-edit"],
    "tasks/task_templates.html": ["task-templates"],
}


def _forms_of(text: str) -> list[tuple[str, str]]:
    """The `<form>` of a template that draw fields: (name of the address it posts to, body)."""
    found = []
    for chunk in re.split(r"(?=<form\b)", text)[1:]:
        body = chunk.split("</form>")[0]
        if not FORM_RENDERING.search(body):
            continue
        action = re.search(r"\{% url '([^']+)'", body.split(">")[0])
        found.append((action.group(1) if action else "", body))
    return found


def test_every_form_of_every_template_includes_the_summary() -> None:
    """Each `<form>` that draws fields includes the component itself, not just its file."""
    problems = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        name = path.relative_to(TEMPLATES).as_posix()
        if name.startswith("components/"):
            continue
        for action, body in _forms_of(path.read_text(encoding="utf-8")):
            if action not in BANNER_FORMS and COMPONENT not in body:
                problems.append(f"{name} ({action or 'no address'})")
    assert not problems, f"these forms draw fields without the summary: {problems}"
    # Included in the middle of a form that carries the summary: the partial has no `<form>`.
    assert COMPONENT in (TEMPLATES / "instance/_provider_form.html").read_text()


def test_every_form_has_a_case_that_refuses_it() -> None:
    """A form added to a template needs a case above: the summary is shown, not only included."""
    known = {c[0] for c in CASES} | {c[0] for c in ANONYMOUS} | {"setup"}
    problems = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        name = path.relative_to(TEMPLATES).as_posix()
        forms_in_page = [a for a, _ in _forms_of(path.read_text(encoding="utf-8"))]
        count = len([a for a in forms_in_page if a not in BANNER_FORMS])
        if not count:
            continue
        cases = TEMPLATE_CASES.get(name, [])
        if len(cases) < count or not set(cases) <= known:
            problems.append(f"{name}: {count} form(s), cases {cases}")
    assert not problems, f"forms without a case that refuses them: {problems}"


def test_the_sign_in_forms_of_allauth_get_the_summary(alice: Any) -> None:
    """Allauth draws its fields through one element, overridden to include the summary."""
    response = Client().post(
        reverse("account_login"), {"login": "alice@example.com", "password": "x"}
    )
    page = read(response.content.decode())
    assert page.summary is not None

"""An archived home is read only, on every way in (docs/adr/0089).

Every route of a home that changes something (any method but GET, HEAD and OPTIONS) is listed
from the URL resolver, pages, HTMX and API alike, with the ids of real rows of the home: each
answers 403 and writes nothing, unless it is one of the exceptions of the record (bringing the
home back, exporting it, deleting it, leaving it, taking access away). A new route is refused
until it is declared an exception, never let through. Accepting an invitation, whose routes name
no home, waits until the home is back and keeps the invitation. The pages of an archived home
offer no change but those, the sync API refuses each operation of a push with `home_archived`
but the one that brings the home back, and a report or an event sent to it is refused without a
500.
"""

import datetime
import hashlib
import html
import json
import re
import uuid
from collections.abc import Callable, Iterator
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.apps import apps
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, resolve
from django.urls.exceptions import Resolver404
from django.utils import timezone
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.apitokens import services as token_services
from housecairn.apitokens.models import Scope
from housecairn.audit.models import AuditEvent
from housecairn.calendar import services as calendar_services
from housecairn.events.models import Event
from housecairn.homes import services as home_services
from housecairn.homes.management.commands.load_demo_data import load
from housecairn.homes.models import Home, Membership, Role
from housecairn.labels import services as label_services
from housecairn.projects.models import BudgetLine, Project, ProjectLink, Quote
from housecairn.reports import services as report_services
from housecairn.reports.models import Report
from housecairn.rooms.models import Item, Room
from housecairn.stock import services as stock_services
from housecairn.stock.models import Article, Movement
from housecairn.sync import clock
from housecairn.sync.models import Change
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Occurrence, TaskMaterial
from tests.oauth_support import Connected, connect

pytestmark = pytest.mark.django_db

UNSAFE = ("post", "put", "patch", "delete")

# The changes an archived home still takes (docs/adr/0089), as "<route> <method>". Anything else
# that changes a home is refused; this list is the whole of the exceptions.
EXCEPTIONS = frozenset(
    {
        # Bringing it back, and archiving it again, which changes nothing.
        "homes:unarchive post",
        "homes:archive post",
        "api-v1:home-unarchive post",
        "api-v1:home-archive post",
        # Exporting it, and downloading the export (a POST, so that no prefetch uses it).
        "homes:export post",
        "homes:export-download post",
        "api-v1:home-export post",
        "api-v1:home-export-download post",
        # Deleting it for good (and the example home of the guide, which is never archived).
        "homes:delete post",
        "homes:delete put",
        "api-v1:home-delete-home post",
        "guide:delete post",
        # Leaving it, and taking access away: removing a member, revoking an invitation.
        "homes:leave post",
        "homes:member-remove post",
        "homes:invitation-revoke post",
        "api-v1:home-leave post",
        "api-v1:home-member-detail delete",
        "api-v1:home-invitation-detail delete",
        # The way out of a sign-in provider the instance no longer offers.
        "homes:unlock post",
        "api-v1:home-unlock post",
        # Printing the labels that exist reads the home; no new label is made.
        "labels:print post",
        "api-v1:home-label-print post",
    }
)

# The routes that give access to a home by the secret of an invitation: they name no home in their
# address, so the routes of a home leave them out; `TestInvitations` covers them (docs/adr/0089).
JOINS = frozenset({"invitation post", "api-v1:invitation-accept post"})


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


# ---------- the routes ----------


def _segment(entry: Any) -> str:
    text = str(entry.pattern).removeprefix("^")
    return text.removesuffix("\\Z").removesuffix("$")


def _walk(patterns: list[Any], prefix: str = "", namespace: str = "") -> Iterator[Any]:
    for entry in patterns:
        if isinstance(entry, URLResolver):
            inner = namespace + (f"{entry.namespace}:" if entry.namespace else "")
            yield from _walk(entry.url_patterns, prefix + _segment(entry), inner)
        elif isinstance(entry, URLPattern) and entry.name:
            yield prefix + _segment(entry), namespace + entry.name, entry.callback


def _methods(callback: Any, wanted: tuple[str, ...]) -> list[str]:
    view = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    if view is None:
        return []
    allowed = getattr(view, "http_method_names", wanted)
    actions = getattr(callback, "actions", None)
    if actions:
        return [m for m in actions if m in wanted and m in allowed]
    return [m for m in wanted if hasattr(view, m) and m in allowed]


def home_routes(wanted: tuple[str, ...]) -> list[tuple[str, str, str]]:
    """(name, pattern, method) of every route of one home, outside the sync API."""
    found = []
    for route, name, callback in _walk(get_resolver().url_patterns):
        if "home_id" not in route or route.startswith("api/v1/sync/"):
            continue
        found.extend((name, route, method) for method in _methods(callback, wanted))
    return found


WRITES = home_routes(UNSAFE)
WRITE_NAMES = {name for name, _route, _method in WRITES}
REFUSED = [entry for entry in WRITES if f"{entry[0]} {entry[2]}" not in EXCEPTIONS]
OPEN = [entry for entry in WRITES if f"{entry[0]} {entry[2]}" in EXCEPTIONS]
EXCEPTION_NAMES = {key.split()[0] for key in EXCEPTIONS}


def _id(entry: tuple[str, str, str]) -> str:
    return f"{entry[0]} {entry[2]}"


def concrete(route: str, ids: dict[str, str]) -> str:
    """A path for a route pattern, with the ids of real rows, else made-up ones."""

    def value(name: str) -> str:
        return ids.get(name) or str(uuid.uuid4())

    path = re.sub(r"<(?:uuid|slug|str|int):([a-z_]+)>", lambda m: value(m.group(1)), route)
    path = re.sub(r"\(\?P<([a-z_]+)>((?:[^()]|\([^()]*\))*)\)", lambda m: value(m.group(1)), path)
    return "/" + path.replace("\\.", ".")


# ---------- an archived home with something in each table ----------


@pytest.fixture
def bob_member(bob: User, add_member: Callable[..., Membership], home: Home) -> Membership:
    return add_member(home, bob, Role.MEMBER)


@pytest.fixture
def home(alice: User) -> Home:
    """The demonstration home of Alice (rooms, items, tasks, stock, projects), and more."""
    return load(alice, "en", task_services.today())


@pytest.fixture
def ids(alice: User, home: Home, bob_member: Membership) -> dict[str, str]:
    """The id of a real row for each argument of the routes, made before the home is archived."""
    room = Room.objects.filter(home=home).first()
    item = Item.objects.filter(home=home).first()
    article = Article.objects.filter(home=home).first()
    assert room is not None
    assert item is not None
    assert article is not None
    contractor = task_services.create_contractor(alice, home, name="Plumber")
    supplier = stock_services.create_supplier(alice, article, name="Shop")
    label, _made = label_services.create_label(alice, home, kind="room", target_id=room.id)
    report = report_services.create_report(alice, home, title="A leak", room=room)
    invitation, _raw = home_services.invite(
        alice, home, email="carol@example.com", role=Role.MEMBER, delivery=home_services.Delivery()
    )
    occurrence = Occurrence.objects.filter(home=home, status="planned").first()
    assert occurrence is not None
    found = {
        "home_id": home.id,
        "room_id": room.id,
        "item_id": item.id,
        "article_id": article.id,
        "supplier_id": supplier.id,
        "contractor_id": contractor.id,
        "label_id": label.id,
        "report_id": report.id,
        "invitation_id": invitation.id,
        "member_id": bob_member.id,
        "occurrence_id": occurrence.id,
        "task_id": occurrence.task_id,
        "material_id": getattr(TaskMaterial.objects.filter(home=home).first(), "id", None),
        "project_id": Project.objects.filter(home=home).values_list("id", flat=True).first(),
        "line_id": BudgetLine.objects.filter(home=home).values_list("id", flat=True).first(),
        "quote_id": Quote.objects.filter(home=home).values_list("id", flat=True).first(),
        "link_id": ProjectLink.objects.filter(home=home).values_list("id", flat=True).first(),
        "movement_id": Movement.objects.filter(home=home).values_list("id", flat=True).first(),
    }
    home_services.archive_home(alice, home)
    home.refresh_from_db()
    return {name: str(value) for name, value in found.items() if value is not None}


def snapshot() -> dict[str, Any]:
    """What a write would change: the rows of every table of the app, the change log, the
    audit log, and the home itself."""
    counts: dict[str, Any] = {
        model._meta.label: model._base_manager.count()
        for model in apps.get_models()
        if model.__module__.startswith("housecairn.")
    }
    counts["changes"] = list(Change.objects.order_by("pk").values_list("pk", flat=True))
    counts["audit"] = AuditEvent.objects.count()
    counts["homes"] = list(Home.all_objects.order_by("pk").values_list("pk", "updated_at"))
    return counts


def client_for(user: User, route: str) -> Any:
    if route.startswith("api/"):
        api = APIClient()
        api.force_authenticate(user)
        return api
    web = Client()
    web.force_login(user)
    return web


def refused(answer: Any, route: str) -> bool:
    """The refusal of an archived home, and not another 403 or a 404: its code or its page."""
    if answer.status_code != 403:
        return False
    if route.startswith("api/"):
        return bool(answer.json().get("code") == home_services.HOME_ARCHIVED)
    return "homes/archived_refused.html" in [t.name for t in answer.templates]


# ---------- the rule ----------


class TestTheRule:
    @pytest.mark.parametrize("role", [Role.OWNER, Role.MEMBER, Role.READ_ONLY])
    def test_nobody_writes_in_an_archived_home(
        self, make_user: Callable[..., User], alice_home: Home, role: Role
    ) -> None:
        user = make_user()
        Membership.objects.create(home=alice_home, user=user, role=role)
        alice_home.archived_at = timezone.now()
        assert not home_services.can_write(user, alice_home)
        assert home_services.writes_by_role(user, alice_home) == (role != Role.READ_ONLY)

    def test_the_owner_writes_again_once_it_is_back(self, alice: User, alice_home: Home) -> None:
        home_services.archive_home(alice, alice_home)
        assert not home_services.can_write(alice, alice_home)
        with pytest.raises(PermissionDenied):
            home_services.update_home(alice, alice_home, name="Renamed")
        home_services.unarchive_home(alice, alice_home)
        assert home_services.can_write(alice, alice_home)
        assert home_services.update_home(alice, alice_home, name="Renamed").name == "Renamed"

    def test_who_may_open_it_waits_until_it_is_back(
        self, alice: User, alice_home: Home, bob: User, add_member: Callable[..., Membership]
    ) -> None:
        bob_membership = add_member(alice_home, bob, Role.MEMBER)
        home_services.archive_home(alice, alice_home)
        with pytest.raises(home_services.HomeArchivedError):
            home_services.invite(
                alice,
                alice_home,
                email="carol@example.com",
                role=Role.MEMBER,
                delivery=home_services.Delivery(),
            )
        with pytest.raises(home_services.HomeArchivedError):
            home_services.change_role(alice, bob_membership, Role.OWNER)
        with pytest.raises(home_services.HomeArchivedError):
            home_services.transfer_ownership(alice, alice_home, bob_membership)
        # Taking access away does not wait.
        home_services.remove_member(alice, bob_membership)
        assert not Membership.objects.filter(home=alice_home, user=bob).exists()


class TestEveryRoute:
    def test_the_exceptions_are_routes(self) -> None:
        assert {_id(entry) for entry in OPEN} == EXCEPTIONS

    def test_every_kind_of_route_is_covered(self) -> None:
        """Pages, HTMX actions and the REST API: the list is not empty on any side."""
        assert any(route.startswith("api/v1/") for _name, route, _method in REFUSED)
        assert any(not route.startswith("api/") for _name, route, _method in REFUSED)
        assert len(REFUSED) > 100

    def test_the_routes_that_join_a_home_are_covered(self) -> None:
        """Accepting an invitation names no home in its address: `TestInvitations` covers it."""
        found = {
            f"{name} {method}"
            for route, name, callback in _walk(get_resolver().url_patterns)
            if "home_id" not in route
            and (name == "invitation" or name.startswith("api-v1:invitation"))
            for method in _methods(callback, UNSAFE)
        }
        assert found == JOINS

    @pytest.mark.parametrize("entry", REFUSED, ids=_id)
    def test_a_change_is_refused_and_writes_nothing(
        self, alice: User, ids: dict[str, str], entry: tuple[str, str, str]
    ) -> None:
        name, route, method = entry
        path = concrete(route, ids)
        before = snapshot()
        client = client_for(alice, route)
        if route.startswith("api/"):
            answer = getattr(client, method)(path, {}, format="json")
        else:
            answer = getattr(client, method)(path, {})
        assert refused(answer, route), (name, method, answer.status_code, answer.content[:300])
        assert snapshot() == before

    @pytest.mark.parametrize("entry", OPEN, ids=_id)
    def test_an_exception_is_not_refused_for_being_archived(
        self, alice: User, ids: dict[str, str], entry: tuple[str, str, str]
    ) -> None:
        name, route, method = entry
        client = client_for(alice, route)
        path = concrete(route, ids)
        if route.startswith("api/"):
            answer = getattr(client, method)(path, {}, format="json")
        else:
            answer = getattr(client, method)(path, {})
        assert not refused(answer, route), (name, method)
        assert answer.status_code < 500


class TestTheWayBack:
    def test_an_owner_brings_it_back_from_the_screen(self, alice: User, alice_home: Home) -> None:
        home_services.archive_home(alice, alice_home)
        client = Client()
        client.force_login(alice)
        answer = client.post(f"/h/{alice_home.id}/unarchive/")
        assert answer.status_code == 302
        alice_home.refresh_from_db()
        assert alice_home.archived_at is None

    def test_an_owner_brings_it_back_from_the_api(
        self, api_client: APIClient, alice: User, alice_home: Home
    ) -> None:
        home_services.archive_home(alice, alice_home)
        api_client.force_authenticate(alice)
        answer = api_client.post(f"/api/v1/homes/{alice_home.id}/unarchive/")
        assert answer.status_code == 200, answer.content
        alice_home.refresh_from_db()
        assert alice_home.archived_at is None

    def test_a_member_who_is_not_an_owner_does_not(
        self, api_client: APIClient, alice: User, alice_home: Home, bob: User
    ) -> None:
        Membership.objects.create(home=alice_home, user=bob, role=Role.MEMBER)
        home_services.archive_home(alice, alice_home)
        api_client.force_authenticate(bob)
        answer = api_client.post(f"/api/v1/homes/{alice_home.id}/unarchive/")
        assert answer.status_code == 403
        alice_home.refresh_from_db()
        assert alice_home.archived_at is not None


# ---------- invitations sent before the home was archived ----------

WAITS = "This home is archived; the invitation will work again once it is brought back."
WAITS_FR = "Ce logement est archivé ; l'invitation reprendra quand il sera remis en service."


@pytest.fixture
def pending(alice: User, alice_home: Home) -> str:
    """The secret of an invitation that went by email, then the home was archived."""
    sent: list[str] = []
    home_services.invite(
        alice,
        alice_home,
        email="carol@example.com",
        role=Role.MEMBER,
        delivery=home_services.Delivery(send=lambda _invitation, raw: sent.append(raw)),
    )
    home_services.archive_home(alice, alice_home)
    return sent[0]


def accept(way: str, user: User, raw: str) -> Any:
    """Accept the invitation of `raw` as `user`, from the page of its link or from the API."""
    if way == "web":
        web = Client()
        web.force_login(user)
        return web.post(f"/invitations/{raw}/")
    api = APIClient()
    api.force_authenticate(user)
    return api.post("/api/v1/invitations/accept/", {"token": raw}, format="json")


class TestInvitations:
    """What gives access waits until the home is back (docs/adr/0089): the routes of `JOINS`."""

    def test_the_service_refuses_and_keeps_the_invitation(
        self, make_user: Callable[..., User], alice_home: Home, pending: str
    ) -> None:
        carol = make_user("carol@example.com")
        with pytest.raises(home_services.HomeArchivedError):
            home_services.accept_invitation(carol, pending)
        assert home_services.find_invitation(pending) is not None
        assert home_services.role_of(carol, alice_home) is None

    @pytest.mark.parametrize("way", ["web", "api"])
    def test_accepting_is_refused_then_works_once_the_home_is_back(
        self, make_user: Callable[..., User], alice: User, alice_home: Home, pending: str, way: str
    ) -> None:
        carol = make_user("carol@example.com")
        answer = accept(way, carol, pending)
        assert answer.status_code == 403, answer.content[:300]
        if way == "api":
            assert answer.json()["code"] == home_services.HOME_ARCHIVED
        else:
            assert WAITS in html.unescape(answer.content.decode())
        assert home_services.role_of(carol, alice_home) is None
        # Kept, unused: the same link works once an owner brings the home back.
        assert home_services.find_invitation(pending) is not None
        home_services.unarchive_home(alice, alice_home)
        answer = accept(way, carol, pending)
        assert answer.status_code in (200, 302), answer.content[:300]
        assert home_services.role_of(carol, alice_home) == Role.MEMBER
        assert home_services.find_invitation(pending) is None

    def test_the_page_says_it_waits_and_offers_nothing(
        self, make_user: Callable[..., User], pending: str
    ) -> None:
        client = Client()
        client.force_login(make_user("carol@example.com"))
        page = html.unescape(client.get(f"/invitations/{pending}/").content.decode())
        assert WAITS in page
        assert "Join the home" not in page
        french = client.get(f"/invitations/{pending}/", HTTP_ACCEPT_LANGUAGE="fr")
        assert WAITS_FR in html.unescape(french.content.decode())

    def test_no_account_is_made_from_the_link(self, alice_home: Home, pending: str) -> None:
        client = Client()
        page = html.unescape(client.get(f"/invitations/{pending}/").content.decode())
        assert WAITS in page
        assert "Create the account and join" not in page
        password = "a-long-and-unusual-passphrase"
        answer = client.post(
            f"/invitations/{pending}/", {"password1": password, "password2": password}
        )
        assert answer.status_code == 403
        assert not User.objects.filter(email="carol@example.com").exists()
        assert home_services.find_invitation(pending) is not None


# ---------- the pages ----------


class _Targets(HTMLParser):
    """The addresses a page posts to, or links to."""

    def __init__(self, page: str) -> None:
        super().__init__()
        self.page = page
        self.posts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "form" and (found.get("method") or "").lower() == "post":
            self.posts.append(found.get("action") or self.page)
        for verb in ("hx-post", "hx-put", "hx-patch", "hx-delete"):
            if found.get(verb):
                self.posts.append(found[verb] or self.page)
        if tag == "a" and found.get("href"):
            self.links.append(found["href"] or "")


def _route_name(address: str) -> str | None:
    path = urlsplit(address).path
    if not path.startswith("/"):
        return None
    try:
        return resolve(path).view_name
    except Resolver404:
        return None


READS = [
    entry
    for entry in home_routes(("get",))
    if not entry[1].startswith("api/")
    and not any(part in entry[1] for part in ("photo", "file", "thumbnail", "csv", ".ics"))
]


class TestThePages:
    @pytest.mark.parametrize("entry", READS, ids=lambda entry: entry[0])
    def test_a_page_offers_no_change_but_the_exceptions(
        self, alice: User, ids: dict[str, str], entry: tuple[str, str, str]
    ) -> None:
        name, route, _method = entry
        client = Client()
        client.force_login(alice)
        page = concrete(route, ids)
        answer = client.get(page)
        if answer.status_code != 200 or "text/html" not in answer.get("Content-Type", ""):
            return
        found = _Targets(page)
        found.feed(answer.content.decode())
        offered = {
            target
            for target in (*found.posts, *found.links)
            if (route_name := _route_name(target)) in WRITE_NAMES
            and route_name not in EXCEPTION_NAMES
        }
        assert offered == set(), name

    def test_the_banner_says_read_only_and_offers_the_way_back_to_an_owner(
        self, alice: User, bob: User, ids: dict[str, str]
    ) -> None:
        owner = Client()
        owner.force_login(alice)
        html = owner.get(f"/h/{ids['home_id']}/rooms/").content.decode()
        assert "Archived home: read only" in html
        assert f"/h/{ids['home_id']}/unarchive/" in html
        member = Client()
        member.force_login(bob)
        html = member.get(f"/h/{ids['home_id']}/rooms/").content.decode()
        assert "Archived home: read only" in html
        assert f"/h/{ids['home_id']}/unarchive/" not in html

    def test_the_banner_is_in_french(self, alice: User, ids: dict[str, str]) -> None:
        client = Client()
        client.force_login(alice)
        html = client.get(f"/h/{ids['home_id']}/rooms/", HTTP_ACCEPT_LANGUAGE="fr").content.decode()
        assert "Logement archivé : lecture seule" in html

    def test_a_home_that_is_not_archived_has_no_banner(self, alice: User, alice_home: Home) -> None:
        client = Client()
        client.force_login(alice)
        html = client.get(f"/h/{alice_home.id}/rooms/").content.decode()
        assert "Archived home: read only" not in html

    def test_the_owner_still_sees_every_report(
        self, alice: User, bob: User, ids: dict[str, str]
    ) -> None:
        """Archiving changes what may be written, not what a member sees (docs/adr/0086)."""
        home = Home.objects.get(pk=ids["home_id"])
        assert report_services.sees_every_report(bob, home)
        assert report_services.visible_reports(bob, home).count() == 1


class TestTheCalendar:
    def test_no_entry_of_an_archived_home_may_be_written(self, alice: User, home: Home) -> None:
        """`entries` says it itself, whatever homes its caller passes (its callers drop them)."""
        today = task_services.today()
        span = (today - datetime.timedelta(days=60), today + datetime.timedelta(days=300))
        assert any(e.can_write for e in calendar_services.entries(alice, [home], *span).entries)
        home_services.archive_home(alice, home)
        home.refresh_from_db()
        after = calendar_services.entries(alice, [home], *span).entries
        assert after
        assert not any(e.can_write for e in after)


# ---------- reports and events: Home Assistant ----------


class TestReportsAndEvents:
    def test_a_report_is_refused_by_the_service(self, alice: User, alice_home: Home) -> None:
        home_services.archive_home(alice, alice_home)
        with pytest.raises(home_services.HomeArchivedError):
            report_services.create_report(alice, alice_home, title="A leak")
        assert not Report.objects.filter(home=alice_home).exists()

    @pytest.mark.parametrize("role", [Role.OWNER, Role.READ_ONLY])
    def test_a_report_by_token_is_refused_cleanly(
        self, make_user: Callable[..., User], alice: User, alice_home: Home, role: Role
    ) -> None:
        """What Home Assistant sends: a token of the home, a report; 403, never a 500."""
        user = alice if role == Role.OWNER else make_user()
        if role != Role.OWNER:
            Membership.objects.create(home=alice_home, user=user, role=role)
        _token, raw = token_services.create_token(
            user, name="ha", scopes=[Scope.WRITE], homes=[alice_home], lifetime_days=30
        )
        home_services.archive_home(alice, alice_home)
        answer = APIClient().post(
            f"/api/v1/homes/{alice_home.id}/reports/",
            {"title": "Water leak under the sink"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        )
        assert answer.status_code == 403
        assert answer.json()["code"] == home_services.HOME_ARCHIVED
        assert not Report.objects.filter(home=alice_home).exists()

    def test_an_event_by_token_is_refused_cleanly(self, alice: User, alice_home: Home) -> None:
        _token, raw = token_services.create_token(
            alice, name="ha", scopes=[Scope.EVENTS], homes=[alice_home], lifetime_days=30
        )
        home_services.archive_home(alice, alice_home)
        answer = APIClient().post(
            f"/api/v1/homes/{alice_home.id}/events/",
            {"kind": "leak", "payload": {"sensor": "kitchen"}},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        )
        assert answer.status_code == 403
        assert answer.json()["code"] == home_services.HOME_ARCHIVED
        assert not Event.objects.filter(home=alice_home).exists()

    def test_the_form_of_a_report_is_not_offered(self, alice: User, alice_home: Home) -> None:
        home_services.archive_home(alice, alice_home)
        client = Client()
        client.force_login(alice)
        answer = client.get(f"/h/{alice_home.id}/reports/new/")
        assert answer.status_code == 403
        assert "homes/archived_refused.html" in [t.name for t in answer.templates]


# ---------- the sync API ----------

DEVICE = clock.device_to_b64(bytes(range(16)))


def stamp(offset_ms: int = 0) -> str:
    return clock.format_clock(clock.make_clock(clock.wall_ms() + offset_ms, 0))


def op(home: Any, entity: str, row_id: Any, fields: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {
        "t": entity,
        "h": str(home),
        "id": str(row_id),
        "k": kw.pop("k", "put"),
        "c": kw.pop("c", stamp()),
        "f": fields,
    }


def push(token: str, home_id: Any, ops: list[Any], number: int = 1) -> Any:
    raw = json.dumps({"push": number, "sv": 1, "device": DEVICE, "ops": ops}).encode()
    return Client().post(
        f"/api/v1/sync/homes/{home_id}/push",
        raw,
        content_type="application/json",
        secure=True,
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


@pytest.fixture
def phone(alice: User, alice_home: Home) -> Connected:
    return connect(alice, [alice_home])


class TestSync:
    def test_each_operation_is_refused_with_its_code(
        self, phone: Connected, alice: User, alice_home: Home
    ) -> None:
        home_services.archive_home(alice, alice_home)
        before = Change.objects.count()
        room_id = uuid.uuid4()
        answer = push(
            phone.access,
            alice_home.pk,
            [
                op(alice_home.pk, "rooms.room", room_id, {"name": "Attic", "kind": "other"}),
                op(alice_home.pk, "homes.home", alice_home.pk, {"name": "Renamed"}),
            ],
        )
        assert answer.status_code == 200, answer.content
        results = answer.json()["results"]
        assert [r["status"] for r in results] == ["rejected", "rejected"]
        assert [r["code"] for r in results] == ["home_archived", "home_archived"]
        assert not Room.objects.filter(pk=room_id).exists()
        assert Home.objects.get(pk=alice_home.pk).name == "Main house"
        assert Change.objects.count() == before

    def test_the_operation_that_brings_it_back_is_taken_then_the_others(
        self, phone: Connected, alice: User, alice_home: Home
    ) -> None:
        home_services.archive_home(alice, alice_home)
        room_id = uuid.uuid4()
        answer = push(
            phone.access,
            alice_home.pk,
            [
                op(alice_home.pk, "homes.home", alice_home.pk, {"archived_at": None}),
                op(alice_home.pk, "rooms.room", room_id, {"name": "Attic", "kind": "other"}),
            ],
        )
        results = answer.json()["results"]
        assert [r["status"] for r in results] == ["applied", "applied"]
        assert Home.objects.get(pk=alice_home.pk).archived_at is None
        assert Room.objects.filter(pk=room_id).exists()

    def test_a_push_that_archives_makes_the_next_operations_refused(
        self, phone: Connected, alice_home: Home
    ) -> None:
        room_id = uuid.uuid4()
        answer = push(
            phone.access,
            alice_home.pk,
            [
                op(alice_home.pk, "homes.home", alice_home.pk, {"archived_at": stamp()}),
                op(alice_home.pk, "rooms.room", room_id, {"name": "Attic", "kind": "other"}),
            ],
        )
        results = answer.json()["results"]
        assert results[0]["status"] == "applied"
        assert results[1] == {"i": 1, "status": "rejected", "code": "home_archived"}
        assert not Room.objects.filter(pk=room_id).exists()

    def test_bringing_it_back_with_another_change_is_refused(
        self, phone: Connected, alice: User, alice_home: Home
    ) -> None:
        """Only a `put` that sets `archived_at` alone brings it back, not one that renames too."""
        home_services.archive_home(alice, alice_home)
        answer = push(
            phone.access,
            alice_home.pk,
            [
                op(
                    alice_home.pk,
                    "homes.home",
                    alice_home.pk,
                    {"archived_at": None, "name": "Renamed"},
                )
            ],
        )
        assert answer.json()["results"] == [{"i": 0, "status": "rejected", "code": "home_archived"}]
        found = Home.objects.get(pk=alice_home.pk)
        assert found.name == "Main house"
        assert found.archived_at is not None

    def test_a_member_who_is_not_an_owner_does_not_bring_it_back(
        self, alice: User, alice_home: Home, bob: User
    ) -> None:
        Membership.objects.create(home=alice_home, user=bob, role=Role.MEMBER)
        bobs_phone = connect(bob, [alice_home])
        home_services.archive_home(alice, alice_home)
        answer = push(
            bobs_phone.access,
            alice_home.pk,
            [op(alice_home.pk, "homes.home", alice_home.pk, {"archived_at": None})],
        )
        assert answer.json()["results"][0]["code"] == "forbidden"
        assert Home.objects.get(pk=alice_home.pk).archived_at is not None

    def test_a_read_only_member_keeps_forbidden(
        self, make_user: Callable[..., User], alice: User, alice_home: Home
    ) -> None:
        reader = make_user()
        Membership.objects.create(home=alice_home, user=reader, role=Role.READ_ONLY)
        readers_phone = connect(reader, [alice_home])
        home_services.archive_home(alice, alice_home)
        answer = push(
            readers_phone.access,
            alice_home.pk,
            [op(alice_home.pk, "rooms.room", uuid.uuid4(), {"name": "Attic", "kind": "other"})],
        )
        assert answer.json()["results"][0]["code"] == "forbidden"

    def test_a_file_is_refused(self, phone: Connected, alice: User, alice_home: Home) -> None:
        home_services.archive_home(alice, alice_home)
        data = b"%PDF-1.4\n%%EOF\n"
        answer = Client().put(
            f"/api/v1/sync/homes/{alice_home.pk}/files/{uuid.uuid4()}",
            data,
            content_type="application/octet-stream",
            secure=True,
            HTTP_AUTHORIZATION=f"Bearer {phone.access}",
            HTTP_HC_SHA256=hashlib.sha256(data).hexdigest(),
        )
        assert answer.status_code == 403
        assert answer.json()["code"] == "home_archived"

    def test_the_changes_stay_readable(
        self, phone: Connected, alice: User, alice_home: Home
    ) -> None:
        home_services.archive_home(alice, alice_home)
        answer = Client().get(
            f"/api/v1/sync/homes/{alice_home.pk}/changes",
            secure=True,
            HTTP_AUTHORIZATION=f"Bearer {phone.access}",
        )
        assert answer.status_code == 200, answer.content
        assert answer.json()["changes"]

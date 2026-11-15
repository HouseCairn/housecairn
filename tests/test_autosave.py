"""A sheet saves itself field by field, with script, through the same view (ADR 0082)."""

import datetime
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from django.test import Client
from django.urls import reverse

from housecairn.accounts.models import User
from housecairn.audit.models import AuditEvent
from housecairn.homes.models import Home
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Item, Room
from housecairn.stock import services as stock_services
from housecairn.sync.models import Change
from housecairn.tasks import services as task_services

pytest_plugins = ("tests.rooms_fixtures", "tests.stock_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root")]

HTMX = {"HTTP_HX_REQUEST": "true"}
TODAY = datetime.date(2026, 12, 1)


class _Form(HTMLParser):
    """The values a browser sends for the form marked `data-autosave` of a page."""

    def __init__(self) -> None:
        super().__init__()
        self.inside = False
        self.values: dict[str, str] = {}
        self.select: str | None = None
        self.textarea: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "form" and "data-autosave" in a:
            self.inside = True
        if not self.inside or "name" not in a or "disabled" in a:
            if tag == "option" and self.select and "selected" in a:
                self.values[self.select] = a.get("value", "")
            return
        if tag == "input":
            kind = a.get("type", "text")
            if kind in ("checkbox", "radio") and "checked" not in a:
                return
            if kind not in ("submit", "button", "file"):
                self.values[a["name"]] = a.get("value", "on" if kind == "checkbox" else "")
        elif tag == "select":
            self.select = a["name"]
            if "multiple" not in a:
                self.values.setdefault(self.select, "")
        elif tag == "textarea":
            self.textarea = a["name"]
            self.values[self.textarea] = ""

    def handle_data(self, data: str) -> None:
        if self.textarea:
            self.values[self.textarea] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self.select = None
        elif tag == "textarea":
            self.textarea = None
        elif tag == "form":
            self.inside = False


def sent_by(client: Client, url: str) -> dict[str, str]:
    parser = _Form()
    parser.feed(client.get(url).text)
    assert parser.values, "the page has no form marked data-autosave"
    return parser.values


def autosave(client: Client, url: str, **changes: str) -> tuple[int, str]:
    data = {**sent_by(client, url), **changes, "_autosave": "1"}
    response = client.post(url, data, **HTMX)
    return response.status_code, response.text


def item_url(home: Home, item: Item) -> str:
    return reverse("rooms:item-edit", kwargs={"home_id": home.id, "item_id": item.id})


class TestTheItemSheet:
    def test_the_edit_form_saves_itself_and_the_new_one_does_not(
        self, signed_in: Client, alice_home: Home, owner_room: Room, owner_item: Item
    ) -> None:
        page = signed_in.get(item_url(alice_home, owner_item)).text
        assert "data-autosave" in page
        assert "js/autosave.js" in page
        status = page.split('id="autosave-status"')[1].split(">")[0]
        assert 'aria-live="polite"' in status
        assert 'role="status"' in status
        # The button stays, for a browser without script.
        assert '<button type="submit" class="button-block">Save</button>' in page
        create = reverse(
            "rooms:item-create", kwargs={"home_id": alice_home.id, "room_id": owner_room.id}
        )
        assert "data-autosave" not in signed_in.get(create).text

    def test_a_field_changed_is_saved_once_through_the_change_log(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        url = item_url(alice_home, owner_item)
        before = Change.objects.filter(row_id=owner_item.pk).count()
        code, text = autosave(signed_in, url, brand="Example")
        assert code == 200
        assert "Saved" in text
        owner_item.refresh_from_db()
        assert owner_item.brand == "Example"
        changes = Change.objects.filter(row_id=owner_item.pk).order_by("pos")
        assert changes.count() == before + 1
        assert set(changes.last().fields) == {"brand"}
        assert AuditEvent.objects.filter(action="item.updated").count() == 1
        # The same state sent again (a change after an input): nothing more is written.
        code, text = autosave(signed_in, url)
        assert code == 200
        assert "Saved" in text
        assert Change.objects.filter(row_id=owner_item.pk).count() == before + 1
        assert AuditEvent.objects.filter(action="item.updated").count() == 1

    def test_an_error_is_shown_on_its_field_and_nothing_is_written(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        code, text = autosave(signed_in, item_url(alice_home, owner_item), name="")
        assert code == 200
        assert "Not saved" in text
        error = text.split('id="autosave-error-name"')[1].split("</span>")[0]
        assert "This field is required." in error
        assert "hidden" not in error.split(">")[0]
        # The other fields have their place cleared.
        assert 'id="autosave-error-brand" class="autosave-error" hx-swap-oob="true" hidden' in text
        owner_item.refresh_from_db()
        assert owner_item.name == "Boiler"

    @pytest.mark.parametrize("duration", ["0", "100"])
    def test_a_warranty_duration_out_of_bounds_is_shown_under_its_field(
        self, signed_in: Client, alice_home: Home, owner_item: Item, duration: str
    ) -> None:
        url = item_url(alice_home, owner_item)
        page = signed_in.get(url).text
        # The page holds a place for the error of the duration and of the unit.
        assert 'id="autosave-error-warranty_duration"' in page
        assert 'id="autosave-error-warranty_duration_unit"' in page
        code, text = autosave(signed_in, url, warranty_duration=duration)
        assert code == 200
        assert "Not saved" in text
        error = text.split('id="autosave-error-warranty_duration"')[1].split("</span>")[0]
        assert "hidden" not in error.split(">")[0]
        assert "Ensure this value is" in error
        owner_item.refresh_from_db()
        assert owner_item.warranty_duration is None

    def test_a_new_contractor_waits_for_the_button(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        _code, text = autosave(
            signed_in,
            item_url(alice_home, owner_item),
            contract_contractor="new",
            new_contractor_name="Acme Heating",
            new_contractor_trade="plumber",
        )
        assert "Press Save to add the new record." in text
        owner_item.refresh_from_db()
        assert owner_item.contract_contractor is None

    def test_without_script_the_button_saves_and_leads_back(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        url = item_url(alice_home, owner_item)
        data = {**sent_by(signed_in, url), "brand": "Example"}
        response = signed_in.post(url, data)
        assert response.status_code == 302
        owner_item.refresh_from_db()
        assert owner_item.brand == "Example"

    def test_the_flag_alone_without_htmx_is_a_plain_save(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        url = item_url(alice_home, owner_item)
        data = {**sent_by(signed_in, url), "brand": "Example", "_autosave": "1"}
        assert signed_in.post(url, data).status_code == 302

    def test_csrf_is_checked(self, alice: User, alice_home: Home, owner_item: Item) -> None:
        client = Client(enforce_csrf_checks=True)
        client.force_login(alice)
        url = item_url(alice_home, owner_item)
        data = {**sent_by(client, url), "brand": "Example", "_autosave": "1"}
        data.pop("csrfmiddlewaretoken")
        assert client.post(url, data, **HTMX).status_code == 403
        owner_item.refresh_from_db()
        assert owner_item.brand == "Acme"

    def test_a_reader_cannot_save(
        self, client: Client, reader: User, alice_home: Home, owner_item: Item
    ) -> None:
        client.force_login(reader)
        url = item_url(alice_home, owner_item)
        response = client.post(url, {"name": "X", "_autosave": "1"}, **HTMX)
        assert response.status_code == 403


class TestTheOtherSheets:
    def test_a_room(self, signed_in: Client, alice_home: Home, owner_room: Room) -> None:
        url = reverse("rooms:edit", kwargs={"home_id": alice_home.id, "room_id": owner_room.id})
        code, text = autosave(signed_in, url, notes="North wall")
        assert code == 200
        assert "Saved" in text
        owner_room.refresh_from_db()
        assert owner_room.notes == "North wall"

    def test_a_task(self, signed_in: Client, alice: User, alice_home: Home) -> None:
        task = task_services.create_task(
            alice, alice_home, title="Descale", due_on=datetime.date(2026, 12, 1)
        )
        url = reverse("tasks:edit", kwargs={"home_id": alice_home.id, "task_id": task.id})
        code, text = autosave(signed_in, url, notes="Use the white vinegar")
        assert code == 200
        assert "Saved" in text, text
        task.refresh_from_db()
        assert task.notes == "Use the white vinegar"
        before = Change.objects.filter(row_id=task.pk).count()
        autosave(signed_in, url)
        assert Change.objects.filter(row_id=task.pk).count() == before

    def test_the_calculated_material_budget_follows_a_priced_line(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = task_services.create_task(alice, alice_home, title="Descale", due_on=TODAY)
        url = reverse("tasks:edit", kwargs={"home_id": alice_home.id, "task_id": task.id})
        page = signed_in.get(url).text
        assert 'id="material-budget"' in page
        assert "data-material-from-stock" not in page
        article = stock_services.create_article(
            alice, alice_home, name="Water filter", unit="piece", unit_price=Decimal("7.90")
        )
        code, text = autosave(
            signed_in, url, **{"materials-0-article": str(article.pk), "materials-0-quantity": "1"}
        )
        assert code == 200
        assert "Saved" in text, text
        block = text.split('id="material-budget"')[1].split("</div>")[0]
        assert 'hx-swap-oob="true"' in text.split('id="material-budget"')[1][:60]
        assert "data-material-from-stock" in block
        assert "€7.90 each time, calculated from the stock items of the task." in block
        # Another field changed: the block is left alone (a figure being typed keeps its focus).
        _code, text = autosave(signed_in, url, notes="Use the white vinegar")
        assert 'id="material-budget"' not in text

    def test_a_stock_item(self, signed_in: Client, alice: User, alice_home: Home) -> None:
        article = stock_services.create_article(alice, alice_home, name="Water filter")
        url = reverse("stock:edit", kwargs={"home_id": alice_home.id, "article_id": article.id})
        code, text = autosave(signed_in, url, unit_price="7.90")
        assert code == 200
        assert "Saved" in text, text
        article.refresh_from_db()
        assert article.unit_price == Decimal("7.90")

    def test_a_room_name_too_long_is_refused_on_its_field(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        url = reverse("rooms:edit", kwargs={"home_id": alice_home.id, "room_id": owner_room.id})
        _code, text = autosave(signed_in, url, name="x" * 200)
        assert "Not saved" in text
        assert "at most 120 characters" in text.split('id="autosave-error-name"')[1][:300]
        room_services.rooms_of(alice_home).get(pk=owner_room.pk)  # still there
        owner_room.refresh_from_db()
        assert owner_room.name == "Living room"

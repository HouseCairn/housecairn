"""Every item of the home, all rooms together: a tab of Rooms, beside Stock."""

import datetime

import pytest
from django.test import Client
from django.urls import reverse

from housecairn.accounts.models import User
from housecairn.homes.models import Home
from housecairn.rooms import services
from housecairn.rooms.models import Room
from housecairn.tasks import services as task_services

pytest_plugins = ("tests.rooms_fixtures",)
pytestmark = pytest.mark.django_db

D = datetime.date


def url(home: Home) -> str:
    return reverse("rooms:items", kwargs={"home_id": home.id})


@pytest.fixture
def two_rooms(alice: User, alice_home: Home, owner_room: Room) -> tuple[Room, Room]:
    kitchen = services.create_room(alice, alice_home, name="Kitchen", kind="kitchen")
    services.create_item(alice, owner_room, name="Television", brand="Acme")
    services.create_item(
        alice,
        kitchen,
        name="Espresso machine",
        brand="Example",
        serial_number="SN-42",
        purchased_on=D(2026, 3, 15),
        warranty_duration=2,
        warranty_duration_unit="year",
    )
    return owner_room, kitchen


class TestTheList:
    def test_every_item_of_every_room(
        self, signed_in: Client, alice_home: Home, two_rooms: tuple[Room, Room]
    ) -> None:
        page = signed_in.get(url(alice_home)).text
        assert "Television" in page
        assert "Espresso machine" in page
        assert "Living room" in page and "Kitchen" in page

    def test_it_is_a_tab_of_rooms_beside_stock(
        self, signed_in: Client, alice_home: Home, two_rooms: tuple[Room, Room]
    ) -> None:
        page = signed_in.get(url(alice_home)).text
        tabs = page.split('class="tabs-list"')[1].split("</ul>")[0]
        assert tabs.index(">Rooms<") < tabs.index(">Equipment<") < tabs.index(">Stock<")
        assert 'aria-current="page">Equipment<' in tabs
        rooms = signed_in.get(reverse("rooms:list", kwargs={"home_id": alice_home.id})).text
        assert url(alice_home) in rooms
        page = signed_in.get(url(alice_home), HTTP_ACCEPT_LANGUAGE="fr").text
        assert ">Équipements<" in page

    def test_filtered_by_room(
        self, signed_in: Client, alice_home: Home, two_rooms: tuple[Room, Room]
    ) -> None:
        _living, kitchen = two_rooms
        page = signed_in.get(url(alice_home), {"room": str(kitchen.id)}).text
        assert "Espresso machine" in page
        assert "Television" not in page

    @pytest.mark.parametrize("query", ["espresso", "EXAMPLE", "sn-42"])
    def test_filtered_by_text(
        self, signed_in: Client, alice_home: Home, two_rooms: tuple[Room, Room], query: str
    ) -> None:
        page = signed_in.get(url(alice_home), {"q": query}).text
        assert "Espresso machine" in page
        assert "Television" not in page

    def test_nothing_matches(
        self, signed_in: Client, alice_home: Home, two_rooms: tuple[Room, Room]
    ) -> None:
        page = signed_in.get(url(alice_home), {"q": "zzz"}).text
        assert "No item matches." in page

    def test_a_room_of_another_home_filters_nothing(
        self, signed_in: Client, alice_home: Home, bob: User, bob_home: Home, two_rooms: object
    ) -> None:
        theirs = services.create_room(bob, bob_home, name="Theirs")
        page = signed_in.get(url(alice_home), {"room": str(theirs.id)}).text
        assert "Television" in page and "Espresso machine" in page

    def test_the_warranty_and_the_next_task_of_each_item(
        self, signed_in: Client, alice: User, alice_home: Home, two_rooms: tuple[Room, Room]
    ) -> None:
        _living, kitchen = two_rooms
        machine = kitchen.items.get(name="Espresso machine")
        task_services.create_task(
            alice,
            alice_home,
            title="Descale the espresso machine",
            room=kitchen,
            item=machine,
            due_on=D(2030, 1, 6),
        )
        rows = services.item_rows(alice_home, today=D(2026, 11, 15))
        by_name = {row.item.name: row for row in rows}
        assert by_name["Espresso machine"].warranty == "active"
        assert by_name["Espresso machine"].next_task.task.title == "Descale the espresso machine"
        assert by_name["Television"].next_task is None
        page = signed_in.get(url(alice_home)).text
        row = page.split("Espresso machine")[1].split("</li>")[0]
        assert "Descale the espresso machine" in row
        assert "warranty until" in row
        assert "No task to come" in page.split("Television")[1].split("</li>")[0]

    def test_another_home_is_not_reachable(self, signed_in: Client, bob_home: Home) -> None:
        assert signed_in.get(url(bob_home)).status_code == 404

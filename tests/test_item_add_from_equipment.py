"""Add an item from the Equipment list: the button, the room chosen in the form, the way back."""

from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse

from housecairn.accounts.models import User
from housecairn.homes.models import Home
from housecairn.rooms import services
from housecairn.rooms.models import Item, Room

pytest_plugins = ("tests.rooms_fixtures",)

pytestmark = pytest.mark.django_db


def listing(home: Home) -> str:
    return reverse("rooms:items", kwargs={"home_id": home.id})


def add_url(home: Home) -> str:
    return reverse("rooms:item-add", kwargs={"home_id": home.id})


class TestTheButton:
    def test_a_member_who_can_write_sees_it(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        html = signed_in.get(listing(alice_home)).content.decode()
        assert f'href="{add_url(alice_home)}?next=' in html
        assert "Add an item" in html

    def test_a_reader_does_not(self, client: Client, reader: User, alice_home: Home) -> None:
        client.force_login(reader)
        response = client.get(listing(alice_home))
        assert response.status_code == 200
        assert add_url(alice_home) not in response.content.decode()
        assert client.get(add_url(alice_home)).status_code == 403

    def test_a_home_with_no_room_offers_the_room_first(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        html = signed_in.get(listing(alice_home)).content.decode()
        assert add_url(alice_home) not in html
        assert reverse("rooms:create", kwargs={"home_id": alice_home.id}) in html

    def test_a_list_filtered_on_a_room_carries_it(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        html = signed_in.get(listing(alice_home), {"room": str(owner_room.id)}).content.decode()
        assert f"room={owner_room.id}" in html.split(add_url(alice_home))[1].split('"')[0]


class TestTheForm:
    def test_the_room_is_a_choice_of_the_form(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        html = signed_in.get(add_url(alice_home)).content.decode()
        assert 'name="room"' in html
        assert f'value="{owner_room.id}"' in html
        assert f'value="{owner_room.id}" selected' not in html

    def test_a_filtered_room_is_preselected(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        html = signed_in.get(add_url(alice_home), {"room": str(owner_room.id)}).content.decode()
        assert f'value="{owner_room.id}" selected' in html

    def test_an_unknown_room_is_ignored(self, signed_in: Client, alice_home: Home) -> None:
        response = signed_in.get(
            add_url(alice_home), {"room": "00000000-0000-0000-0000-000000000000"}
        )
        assert response.status_code == 200

    def test_a_room_of_another_home_is_refused(
        self, signed_in: Client, alice_home: Home, bob: User, bob_home: Home
    ) -> None:
        theirs = services.create_room(bob, bob_home, name="Hall")
        response = signed_in.post(
            add_url(alice_home), {"room": theirs.id, "name": "Lamp", "kind": "equipment"}
        )
        assert response.status_code == 200
        assert not Item.objects.filter(name="Lamp").exists()


class TestTheWayBack:
    def test_the_new_item_opens_and_the_list_has_it(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        response = signed_in.post(
            add_url(alice_home),
            {
                "room": owner_room.id,
                "name": "Dishwasher",
                "kind": "equipment",
                "next": listing(alice_home),
            },
        )
        item = Item.objects.get(name="Dishwasher")
        assert item.room == owner_room
        assert response["Location"] == reverse(
            "rooms:item-detail", kwargs={"home_id": alice_home.id, "item_id": item.id}
        )
        assert "Dishwasher" in signed_in.get(listing(alice_home)).content.decode()

    def test_from_another_context_the_person_goes_back_there(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        elsewhere = reverse("rooms:list", kwargs={"home_id": alice_home.id})
        response = signed_in.post(
            add_url(alice_home),
            {"room": owner_room.id, "name": "Kettle", "kind": "equipment", "next": elsewhere},
        )
        assert urlsplit(response["Location"]).path == elsewhere
        assert parse_qs(urlsplit(response["Location"]).query) == {}

    def test_a_reader_cannot_post(
        self, client: Client, reader: User, alice_home: Home, owner_room: Room
    ) -> None:
        client.force_login(reader)
        response = client.post(
            add_url(alice_home), {"room": owner_room.id, "name": "Nope", "kind": "equipment"}
        )
        assert response.status_code == 403
        assert not Item.objects.filter(name="Nope").exists()

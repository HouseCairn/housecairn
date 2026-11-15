"""The warranty of the maker or of the shop, apart from the maintenance contract (ADR 0080)."""

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from housecairn.accounts.models import User
from housecairn.homes.models import Home
from housecairn.rooms import services
from housecairn.rooms.models import Item, Room, warranty_end
from housecairn.sync.models import Change
from housecairn.tasks import agenda

pytest_plugins = ("tests.rooms_fixtures",)
pytestmark = pytest.mark.django_db

D = datetime.date


class TestTheEnd:
    @pytest.mark.parametrize(
        ("start", "duration", "unit", "end"),
        [
            (D(2026, 3, 15), 2, "year", D(2028, 3, 15)),
            (D(2026, 3, 15), 6, "month", D(2026, 9, 15)),
            (D(2026, 1, 31), 1, "month", D(2026, 2, 28)),
            (D(2024, 2, 29), 1, "year", D(2025, 2, 28)),
            (None, 2, "year", None),
            (D(2026, 3, 15), None, "year", None),
            (D(2026, 3, 15), 2, "", None),
        ],
    )
    def test_worked_out_from_the_start_and_the_duration(
        self, start: D | None, duration: int | None, unit: str, end: D | None
    ) -> None:
        assert warranty_end(start, duration, unit) == end

    def test_the_start_is_the_purchase_date_by_default(self, alice: User, owner_room: Room) -> None:
        item = services.create_item(
            alice,
            owner_room,
            name="Espresso machine",
            purchased_on=D(2026, 3, 15),
            warranty_duration=2,
            warranty_duration_unit="year",
            warranty_provider="shop",
            warranty_provider_name="Example Store",
        )
        assert item.warranty_ends_on == D(2028, 3, 15)
        services.update_item(alice, item, warranty_starts_on=D(2026, 4, 1))
        assert item.warranty_ends_on == D(2028, 4, 1)
        services.update_item(alice, item, warranty_duration=18, warranty_duration_unit="month")
        assert item.warranty_ends_on == D(2027, 10, 1)

    def test_an_end_typed_without_a_duration_is_kept(self, alice: User, owner_room: Room) -> None:
        item = services.create_item(
            alice, owner_room, name="Fridge", warranty_ends_on=D(2027, 5, 1)
        )
        services.update_item(alice, item, purchased_on=D(2025, 5, 1))
        item.refresh_from_db()
        assert item.warranty_ends_on == D(2027, 5, 1)

    def test_a_duration_out_of_bounds_is_refused(self, alice: User, owner_room: Room) -> None:
        for wrong in (0, 100):
            with pytest.raises(ValidationError):
                services.create_item(alice, owner_room, name="Oven", warranty_duration=wrong)

    def test_the_provider_is_one_of_the_list(self, alice: User, owner_room: Room) -> None:
        with pytest.raises(ValidationError):
            services.create_item(alice, owner_room, name="Oven", warranty_provider="friend")


class TestTheChangeLog:
    def test_the_new_fields_are_in_the_feed_for_the_phone(
        self, alice: User, owner_room: Room
    ) -> None:
        item = services.create_item(
            alice,
            owner_room,
            name="Dishwasher",
            purchased_on=D(2026, 1, 10),
            warranty_duration=3,
            warranty_duration_unit="year",
            warranty_provider="manufacturer",
            warranty_provider_name="Acme",
        )
        fields = Change.objects.filter(row_id=item.pk).order_by("pos").last().fields
        assert fields["purchased_on"] == "2026-01-10"
        assert fields["warranty_duration"] == 3
        assert fields["warranty_duration_unit"] == "year"
        assert fields["warranty_provider"] == "manufacturer"
        assert fields["warranty_provider_name"] == "Acme"
        assert fields["warranty_starts_on"] is None
        assert fields["warranty_ends_on"] == "2029-01-10"
        services.update_item(alice, item, warranty_starts_on=D(2026, 2, 1))
        fields = Change.objects.filter(row_id=item.pk).order_by("pos").last().fields
        # Only what changed: the start, and the end it moves.
        assert set(fields) == {"warranty_starts_on", "warranty_ends_on"}


class TestTheScreens:
    def test_the_form_has_the_warranty_apart_from_the_contract(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        page = signed_in.get(
            reverse("rooms:item-edit", kwargs={"home_id": alice_home.id, "item_id": owner_item.id})
        ).text
        warranty = page.split("Manufacturer's warranty")[1].split("</fieldset>")[0]
        for name in (
            "warranty_duration",
            "warranty_duration_unit",
            "warranty_provider",
            "warranty_provider_name",
            "warranty_starts_on",
            "warranty_ends_on",
        ):
            assert f'name="{name}"' in warranty
        assert 'name="contract_number"' not in warranty
        unit = page.split('name="warranty_duration_unit"')[1].split("</select>")[0]
        assert '<option value="">' not in unit

    def test_the_sheet_shows_the_end_the_duration_and_who_gives_it(
        self, signed_in: Client, alice: User, alice_home: Home, owner_room: Room
    ) -> None:
        item = services.create_item(
            alice,
            owner_room,
            name="Espresso machine",
            purchased_on=D(2026, 3, 15),
            warranty_duration=2,
            warranty_duration_unit="year",
            warranty_provider="shop",
            warranty_provider_name="Example Store",
        )
        url = reverse("rooms:item-detail", kwargs={"home_id": alice_home.id, "item_id": item.id})
        page = signed_in.get(url).text
        assert "Manufacturer's warranty" in page
        assert "until 15/03/2028" in page or "until 03/15/2028" in page or "2028" in page
        assert "2 years" in page
        assert "The shop: Example Store" in page
        page = signed_in.get(url, HTTP_ACCEPT_LANGUAGE="fr").text
        assert "Garantie constructeur" in page
        assert "2 ans" in page

    def test_a_form_without_a_unit_counts_in_years(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        url = reverse(
            "rooms:item-edit", kwargs={"home_id": alice_home.id, "item_id": owner_item.id}
        )
        response = signed_in.post(
            url,
            {
                "room": str(owner_item.room_id),
                "name": "Boiler",
                "kind": "equipment",
                "purchased_on": "2026-01-01",
                "warranty_duration": "2",
            },
        )
        assert response.status_code == 302
        owner_item.refresh_from_db()
        assert owner_item.warranty_duration_unit == "year"
        assert owner_item.warranty_ends_on == D(2028, 1, 1)


def test_warranties_that_end_still_use_the_computed_end(alice: User, owner_room: Room) -> None:
    today = timezone.localdate()
    services.create_item(
        alice,
        owner_room,
        name="Kettle",
        purchased_on=today - datetime.timedelta(days=355),
        warranty_duration=12,
        warranty_duration_unit="month",
    )
    (digest,) = agenda.my_home(alice, [owner_room.home], day=today)
    assert [item.name for item in digest.warranties] == ["Kettle"]


@pytest.mark.django_db(transaction=True)
def test_the_migration_keeps_every_end_typed_before() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate([("rooms", "0005_item_contract_contractor")])
    old = executor.loader.project_state([("rooms", "0005_item_contract_contractor")]).apps
    home = old.get_model("homes", "Home").objects.create(name="Old house")
    room = old.get_model("rooms", "Room").objects.create(home=home, name="Kitchen")
    old.get_model("rooms", "Item").objects.create(
        home=home, room=room, name="Fridge", warranty_ends_on=D(2027, 5, 1)
    )
    call_command("migrate", verbosity=0)
    item = Item.objects.get(name="Fridge")
    assert item.warranty_ends_on == D(2027, 5, 1)
    assert item.warranty_duration is None
    assert item.purchased_on is None
    assert item.warranty_provider == ""

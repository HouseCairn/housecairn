"""The barcode of a stock item and of an item: checked, synced, searched (docs/adr/0083)."""

import pytest
from django.core.exceptions import ValidationError
from django.test import Client

from housecairn.accounts.models import User
from housecairn.barcodes import gtin
from housecairn.homes.models import Home
from housecairn.rooms import services as rooms
from housecairn.rooms.forms import ItemForm
from housecairn.rooms.models import Item, Room
from housecairn.stock import services as stock
from housecairn.sync.models import Change

pytest_plugins = ("tests.rooms_fixtures",)

# Real check digits: an EAN-13, the UPC-A printed as it, an EAN-8 and a GTIN-14.
EAN13 = "4006381333931"
UPCA = "036000291452"
EAN8 = "96385074"
GTIN14 = "10012345678902"


class TestTheCode:
    @pytest.mark.parametrize("code", [EAN13, UPCA, EAN8, GTIN14, "0" + UPCA])
    def test_valid_codes(self, code: str) -> None:
        assert gtin.is_valid(code)

    @pytest.mark.parametrize(
        "code",
        ["4006381333932", "40063813339", "123", "400638133393\uff11", "4006381333931x", ""],
    )
    def test_wrong_check_digit_length_or_characters(self, code: str) -> None:
        assert not gtin.is_valid(code)

    def test_spaces_and_dashes_are_taken_out(self) -> None:
        assert gtin.clean(" 4 006381-333931 ") == EAN13

    def test_a_upc_a_and_its_ean_13_are_the_same_product(self) -> None:
        assert gtin.as_gtin14(UPCA) == gtin.as_gtin14("0" + UPCA) == "00" + UPCA
        assert {UPCA, "0" + UPCA, "00" + UPCA} <= gtin.variants(UPCA)

    def test_the_validator_explains(self) -> None:
        with pytest.raises(ValidationError) as caught:
            gtin.validate("4006381333932")
        assert caught.value.code == "invalid_ean"
        gtin.validate("")


@pytest.mark.django_db
class TestOnTheRecords:
    def test_an_item_keeps_its_code_and_the_change_is_recorded(
        self, alice: User, owner_room: Room
    ) -> None:
        item = rooms.create_item(alice, owner_room, name="Dishwasher", ean=EAN13)
        assert Item.objects.get(pk=item.pk).ean == EAN13
        rooms.update_item(alice, item, ean=GTIN14)
        change = Change.objects.filter(row_id=item.pk).order_by("-pos").first()
        assert change is not None
        assert "ean" in change.fields

    def test_a_wrong_code_is_refused_by_the_service(self, alice: User, owner_room: Room) -> None:
        with pytest.raises(ValidationError):
            rooms.create_item(alice, owner_room, name="Dishwasher", ean="4006381333932")

    def test_the_form_takes_a_code_with_spaces(
        self, alice: User, alice_home: Home, owner_room: Room
    ) -> None:
        form = ItemForm(
            data={
                "room": owner_room.pk,
                "name": "Oven",
                "kind": "equipment",
                "ean": "4006 3813 33931",
            },
            rooms=Room.objects.filter(home=alice_home),
            home=alice_home,
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["ean"] == EAN13

    def test_the_form_says_what_is_wrong(
        self, alice: User, alice_home: Home, owner_room: Room
    ) -> None:
        form = ItemForm(
            data={"room": owner_room.pk, "name": "Oven", "kind": "equipment", "ean": "12345"},
            rooms=Room.objects.filter(home=alice_home),
            home=alice_home,
        )
        assert not form.is_valid()
        assert "ean" in form.errors

    def test_a_stock_item_keeps_its_code(self, alice: User, alice_home: Home) -> None:
        article = stock.create_article(alice, alice_home, name="AA batteries", ean=UPCA)
        article.refresh_from_db()
        assert article.ean == UPCA


@pytest.mark.django_db
class TestSearch:
    def test_the_stock_is_searched_by_code_with_or_without_spaces(
        self, alice: User, alice_home: Home
    ) -> None:
        wanted = stock.create_article(alice, alice_home, name="Filter", ean=EAN13)
        stock.create_article(alice, alice_home, name="Other", ean=EAN8)
        assert list(stock.search_articles(alice_home, EAN13)) == [wanted]
        assert list(stock.search_articles(alice_home, "4006 3813 33931")) == [wanted]

    def test_a_upc_a_is_found_by_its_ean_13(self, alice: User, alice_home: Home) -> None:
        wanted = stock.create_article(alice, alice_home, name="Bulb", ean=UPCA)
        assert list(stock.search_articles(alice_home, "0" + UPCA)) == [wanted]

    def test_the_list_of_items_is_searched_by_code(
        self, alice: User, alice_home: Home, owner_room: Room
    ) -> None:
        wanted = rooms.create_item(alice, owner_room, name="Fridge", ean=EAN13)
        rooms.create_item(alice, owner_room, name="Oven")
        assert [row.item for row in rooms.item_rows(alice_home, query=EAN13)] == [wanted]
        assert rooms.search(alice_home, EAN13).items == [wanted]

    def test_the_list_page_finds_the_code(
        self, signed_in: Client, alice: User, alice_home: Home, owner_room: Room
    ) -> None:
        rooms.create_item(alice, owner_room, name="Fridge", ean=EAN13)
        response = signed_in.get(f"/h/{alice_home.pk}/items/", {"q": EAN13})
        assert response.status_code == 200
        assert b"Fridge" in response.content

    def test_the_api_carries_the_code(
        self, signed_in: Client, alice: User, alice_home: Home, owner_room: Room
    ) -> None:
        item = rooms.create_item(alice, owner_room, name="Fridge", ean=EAN13)
        response = signed_in.get(f"/api/v1/homes/{alice_home.pk}/items/{item.pk}/")
        assert response.status_code == 200
        assert response.json()["ean"] == EAN13

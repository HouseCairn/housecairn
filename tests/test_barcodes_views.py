"""The lookup of a scanned code from the forms and the API, and the photo it brings (ADR 0083).

No test calls a real server: the online lookup is never queued, its answers are rows of the
cache written by the test.
"""

import io
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.barcodes import services, views
from housecairn.barcodes.models import BarcodeLookup
from housecairn.homes.models import Home
from housecairn.rooms.models import Item, Photo, Room
from housecairn.stock.models import Article

pytest_plugins = ("tests.rooms_fixtures",)
pytestmark = pytest.mark.django_db

CODE = "4006381333931"


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "blue").save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def found() -> BarcodeLookup:
    """What Open Products Facts said of CODE, with its photo, as the task keeps it."""
    now = timezone.now()
    row: BarcodeLookup = BarcodeLookup.objects.create(
        code="0" + CODE,
        state="done",
        started_at=now,
        finished_at=now,
        source="openproductsfacts",
        name="Dishwasher 60 cm",
        brand="Acme",
        category="Dishwashers",
        product_url=f"https://world.openproductsfacts.org/product/{CODE}",
        author="example-contributor",
        image=_jpeg(),
    )
    return row


@pytest.fixture
def no_queue(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    queued: list[Any] = []
    monkeypatch.setattr(services, "_queue", queued.append)
    return queued


def _lookup(client: Client, home: Home, code: str, target: str = "item") -> str:
    response = client.post(f"/h/{home.pk}/barcodes/lookup/", {"ean": code, "target": target})
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    return response.content.decode()


class TestLookupFragment:
    def test_off_by_default_the_code_is_only_kept(
        self, signed_in: Client, alice_home: Home, no_queue: list[Any]
    ) -> None:
        html = _lookup(signed_in, alice_home, CODE)
        assert "is off on this server" in html
        assert not no_queue
        assert not BarcodeLookup.objects.exists()

    def test_the_administrator_is_offered_to_turn_it_on(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        assert "settings/barcodes/" not in _lookup(signed_in, alice_home, CODE)
        User.objects.filter(pk=alice.pk).update(is_staff=True, is_superuser=True)
        assert "/instance/settings/barcodes/" in _lookup(signed_in, alice_home, CODE)

    def test_an_invalid_code_is_said(self, signed_in: Client, alice_home: Home) -> None:
        assert "not a valid barcode" in _lookup(signed_in, alice_home, "4006381333932")

    def test_a_reader_may_not_ask(self, client: Client, reader: User, alice_home: Home) -> None:
        client.force_login(reader)
        response = client.post(f"/h/{alice_home.pk}/barcodes/lookup/", {"ean": CODE})
        assert response.status_code == 403

    def test_another_home_is_not_found(self, signed_in: Client, bob_home: Home) -> None:
        response = signed_in.post(f"/h/{bob_home.pk}/barcodes/lookup/", {"ean": CODE})
        assert response.status_code == 404

    def test_a_running_lookup_asks_again_then_stops(
        self,
        signed_in: Client,
        alice_home: Home,
        monkeypatch: pytest.MonkeyPatch,
        no_queue: list[Any],
    ) -> None:
        monkeypatch.setattr(services, "online_enabled", lambda: True)
        html = _lookup(signed_in, alice_home, CODE)
        assert len(no_queue) == 1
        assert "Looking this code up" in html
        assert "n=1" in html
        assert 'hx-trigger="load delay:2s"' in html
        last = signed_in.get(
            f"/h/{alice_home.pk}/barcodes/lookup/",
            {"ean": CODE, "target": "item", "n": views.POLLS},
        ).content.decode()
        assert "hx-get" not in last
        assert "taking long" in last

    def test_the_online_lookups_are_limited(
        self,
        signed_in: Client,
        alice_home: Home,
        monkeypatch: pytest.MonkeyPatch,
        no_queue: list[Any],
    ) -> None:
        monkeypatch.setattr(services, "online_enabled", lambda: True)
        monkeypatch.setattr(views, "allow", lambda *args, **kwargs: False)
        assert "Too many codes" in _lookup(signed_in, alice_home, CODE)
        assert not no_queue

    def test_a_found_photo_is_shown_with_its_credit(
        self, signed_in: Client, alice_home: Home, found: BarcodeLookup
    ) -> None:
        html = _lookup(signed_in, alice_home, CODE)
        assert f"/h/{alice_home.pk}/barcodes/0{CODE}/photo/" in html
        assert "CC BY-SA 3.0" in html
        assert "example-contributor" in html
        assert 'name="barcode_photo"' in html
        photo = signed_in.get(f"/h/{alice_home.pk}/barcodes/0{CODE}/photo/")
        assert photo.status_code == 200
        assert photo["Content-Type"] == "image/jpeg"


class TestFillValues:
    def test_an_article_takes_the_brand_in_its_name(self, found: BarcodeLookup) -> None:
        answer = services.state(CODE)
        assert views.fill_values(answer, "article") == {
            "name": "Acme Dishwasher 60 cm",
            "notes": "Dishwashers",
        }
        assert views.fill_values(answer, "item")["brand"] == "Acme"


class TestPhotoKept:
    def test_a_new_item_gets_the_photo_and_its_credit(
        self,
        signed_in: Client,
        alice_home: Home,
        owner_room: Room,
        found: BarcodeLookup,
        media_root: Any,
    ) -> None:
        response = signed_in.post(
            f"/h/{alice_home.pk}/rooms/{owner_room.pk}/items/new/",
            {
                "name": "Dishwasher",
                "kind": "equipment",
                "room": owner_room.pk,
                "ean": CODE,
                "barcode_photo": CODE,
            },
        )
        assert response.status_code == 302, [
            (f, e) for f, e in response.context["form"].errors.items()
        ]
        item = Item.objects.get(name="Dishwasher")
        photo = Photo.objects.get(item=item)
        assert photo.attribution["license"] == "CC BY-SA 3.0"
        assert photo.attribution["author"] == "example-contributor"
        page = signed_in.get(f"/h/{alice_home.pk}/items/{item.pk}/").content.decode()
        assert "creativecommons.org/licenses/by-sa/3.0/" in page

    def test_an_item_with_a_photo_keeps_it(
        self,
        signed_in: Client,
        alice: User,
        alice_home: Home,
        owner_item: Item,
        found: BarcodeLookup,
        media_root: Any,
    ) -> None:
        from django.core.files.uploadedfile import SimpleUploadedFile

        from housecairn.rooms import services as rooms_services

        rooms_services.add_photo(
            alice, upload=SimpleUploadedFile("a.jpg", _jpeg()), item=owner_item
        )
        from housecairn.barcodes import photos

        assert photos.attach_to_item(alice, owner_item, CODE) is False
        assert Photo.objects.filter(item=owner_item).count() == 1
        assert not Photo.objects.get(item=owner_item).attribution

    def test_a_photo_of_another_code_is_not_kept(
        self,
        signed_in: Client,
        alice_home: Home,
        owner_room: Room,
        found: BarcodeLookup,
        media_root: Any,
    ) -> None:
        signed_in.post(
            f"/h/{alice_home.pk}/rooms/{owner_room.pk}/items/new/",
            {
                "name": "Other",
                "kind": "equipment",
                "room": owner_room.pk,
                "ean": "96385074",
                "barcode_photo": CODE,
            },
        )
        assert not Photo.objects.filter(item__name="Other").exists()

    def test_a_new_stock_item_gets_the_photo_as_its_label(
        self, signed_in: Client, alice_home: Home, found: BarcodeLookup, media_root: Any
    ) -> None:
        response = signed_in.post(
            f"/h/{alice_home.pk}/stock/new/",
            {"name": "Dishwasher salt", "ean": CODE, "barcode_photo": CODE},
        )
        assert response.status_code == 302, [
            (f, e) for f, e in response.context["form"].errors.items()
        ]
        article = Article.objects.get(name="Dishwasher salt")
        assert article.label
        assert article.label_attribution["source"] == "Open Products Facts"
        page = signed_in.get(f"/h/{alice_home.pk}/stock/{article.pk}/").content.decode()
        assert "CC BY-SA 3.0" in page


class TestApi:
    def test_lookup_off(self, api_client: APIClient, alice: User, alice_home: Home) -> None:
        api_client.force_authenticate(alice)
        response = api_client.post(
            f"/api/v1/homes/{alice_home.pk}/barcodes/lookup/", {"ean": CODE}, format="json"
        )
        assert response.status_code == 200
        assert response.json()["state"] == "off"
        assert response.json()["online_enabled"] is False

    def test_found_and_its_photo(
        self, api_client: APIClient, alice: User, alice_home: Home, found: BarcodeLookup
    ) -> None:
        api_client.force_authenticate(alice)
        data = api_client.get(f"/api/v1/homes/{alice_home.pk}/barcodes/{CODE}/").json()
        assert data["state"] == "done"
        assert data["has_photo"] is True
        assert data["license_url"] == "https://creativecommons.org/licenses/by-sa/3.0/"
        photo = api_client.get(f"/api/v1/homes/{alice_home.pk}/barcodes/{CODE}/photo/")
        assert photo.status_code == 200

    def test_invalid_code_and_reader(
        self, api_client: APIClient, alice: User, reader: User, alice_home: Home
    ) -> None:
        api_client.force_authenticate(alice)
        bad = api_client.post(
            f"/api/v1/homes/{alice_home.pk}/barcodes/lookup/", {"ean": "123"}, format="json"
        )
        assert bad.status_code == 400
        api_client.force_authenticate(reader)
        refused = api_client.get(f"/api/v1/homes/{alice_home.pk}/barcodes/{CODE}/")
        assert refused.status_code == 403

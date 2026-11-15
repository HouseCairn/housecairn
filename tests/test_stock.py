"""Stock items, suppliers, movements: services, web screens, roles, isolation (docs/adr/0038)."""

import datetime
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, RequestFactory
from django.urls import resolve, reverse
from django.utils import timezone
from PIL import Image

from housecairn.accounts.models import User
from housecairn.core.context_processors import nav_section, stock_url
from housecairn.homes.models import Home
from housecairn.rooms import conf
from housecairn.rooms.models import Item
from housecairn.stock import services
from housecairn.stock.models import Article, Movement, Supplier
from tests.rooms_support import image_bytes, noise_image, upload

pytest_plugins = ("tests.rooms_fixtures", "tests.stock_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root")]


def url(name: str, home: Home, **kwargs: object) -> str:
    return reverse(f"stock:{name}", kwargs={"home_id": home.id, **kwargs})


def entry(actor: User, article: Article, quantity: str, **kwargs: object) -> Movement:
    return services.record_movement(
        actor,
        article,
        kind="entry",
        quantity=Decimal(quantity),
        **kwargs,  # type: ignore[arg-type]
    )


class TestQuantity:
    def test_the_quantity_is_the_sum_of_the_movements(
        self, alice: User, alice_home: Home, article: Article
    ) -> None:
        assert services.quantity_of(article) == 0
        entry(alice, article, "5")
        services.record_movement(alice, article, kind="exit", quantity=Decimal("2"))
        services.record_movement(alice, article, kind="inventory", quantity=Decimal("10"))
        services.record_movement(alice, article, kind="inventory", quantity=Decimal("0.5"))
        deltas = list(
            Movement.objects.filter(article=article)
            .order_by("created_at", "id")
            .values_list("delta", flat=True)
        )
        assert deltas == [Decimal("5"), Decimal("-2"), Decimal("7"), Decimal("-9.5")]
        assert services.quantity_of(article) == sum(deltas) == Decimal("0.5")
        listed = services.articles_of(alice_home).get(id=article.id)
        assert listed.quantity == Decimal("0.5")  # type: ignore[attr-defined]

    def test_an_article_without_movement_has_zero(self, alice_home: Home, article: Article) -> None:
        assert services.articles_of(alice_home).get(id=article.id).quantity == 0  # type: ignore[attr-defined]

    def test_an_exit_never_takes_more_than_there_is(self, alice: User, article: Article) -> None:
        entry(alice, article, "3")
        with pytest.raises(ValidationError, match="Only 3 in stock"):
            services.record_movement(alice, article, kind="exit", quantity=Decimal("3.5"))
        assert services.quantity_of(article) == 3
        services.record_movement(alice, article, kind="exit", quantity=Decimal("3"))
        assert services.quantity_of(article) == 0

    def test_entries_and_exits_need_a_positive_quantity(
        self, alice: User, article: Article
    ) -> None:
        for kind in ("entry", "exit"):
            for quantity in ("0", "-1"):
                with pytest.raises(ValidationError):
                    services.record_movement(alice, article, kind=kind, quantity=Decimal(quantity))
        services.record_movement(alice, article, kind="inventory", quantity=Decimal("0"))
        with pytest.raises(ValidationError):
            services.record_movement(alice, article, kind="inventory", quantity=Decimal("-1"))
        with pytest.raises(ValidationError):
            services.record_movement(alice, article, kind="transfer", quantity=Decimal("1"))

    def test_too_many_decimals_are_refused(self, alice: User, article: Article) -> None:
        with pytest.raises(ValidationError):
            entry(alice, article, "1.234")

    def test_the_date_defaults_to_today_and_can_be_set(self, alice: User, article: Article) -> None:
        today = entry(alice, article, "1")
        past = entry(alice, article, "1", occurred_on=datetime.date(2026, 1, 5), note="bought")
        assert today.occurred_on == timezone.localdate()
        assert (past.occurred_on, past.note) == (datetime.date(2026, 1, 5), "bought")

    def test_the_low_filter_includes_the_threshold(
        self, alice: User, alice_home: Home, article: Article
    ) -> None:
        other = services.create_article(alice, alice_home, name="No threshold")
        assert list(services.search_articles(alice_home, low=True)) == [article]  # 0 <= 2
        entry(alice, article, "2")
        assert list(services.search_articles(alice_home, low=True)) == [article]  # 2 <= 2
        entry(alice, article, "1")
        assert list(services.search_articles(alice_home, low=True)) == []
        assert other.threshold is None

    def test_a_ledger_line_cannot_be_given_a_quantity_of_zero_by_the_database(
        self, alice: User, article: Article
    ) -> None:
        from django.db import IntegrityError, transaction

        with pytest.raises(IntegrityError), transaction.atomic():
            Movement.objects.create(
                home=article.home, article=article, kind="entry", quantity=0, delta=0
            )


class TestArticles:
    def test_only_the_name_is_required(self, alice: User, alice_home: Home) -> None:
        created = services.create_article(alice, alice_home, name="Screws")
        assert (created.unit, created.threshold, created.notes) == ("", None, "")
        with pytest.raises(ValidationError):
            services.create_article(alice, alice_home, name="")

    def test_unknown_fields_are_refused(self, alice: User, article: Article) -> None:
        with pytest.raises(ValueError, match="label_content_type"):
            services.update_article(alice, article, label_content_type="image/png")

    def test_a_negative_threshold_is_refused(self, alice: User, article: Article) -> None:
        with pytest.raises(ValidationError):
            services.update_article(alice, article, threshold=Decimal("-1"))

    def test_search_by_name_manufacturer_reference_or_supplier(
        self, alice: User, alice_home: Home, article: Article
    ) -> None:
        services.create_supplier(alice, article, name="Acme Parts", reference="ZX-9")
        services.create_article(alice, alice_home, name="Paint")

        def found(query: str) -> list[str]:
            return [a.name for a in services.search_articles(alice_home, query)]

        assert found("filter") == ["Boiler filter"]
        assert found("bf-2") == ["Boiler filter"]
        assert found("acme") == ["Boiler filter"]
        assert found("zx-9") == ["Boiler filter"]
        assert found("nothing") == []
        assert found("") == ["Boiler filter", "Paint"]

    def test_search_by_compatible_item(
        self, alice: User, alice_home: Home, article: Article, owner_item: Item
    ) -> None:
        services.create_article(alice, alice_home, name="Paint")
        assert list(services.search_articles(alice_home, item=owner_item.id)) == [article]
        assert list(services.search_articles(alice_home, item=uuid.uuid4())) == []

    def test_an_item_of_another_home_is_refused(
        self, alice: User, alice_home: Home, article: Article, foreign_item: Item
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            services.create_article(alice, alice_home, name="x", compatible_items=[foreign_item])
        assert "compatible_items" in caught.value.message_dict
        with pytest.raises(ValidationError):
            services.update_article(alice, article, compatible_items=[foreign_item])
        assert not Article.objects.filter(name="x").exists()
        assert article.compatible_items.count() == 1

    def test_compatible_items_are_replaced_and_kept_when_not_given(
        self, alice: User, article: Article, owner_item: Item
    ) -> None:
        services.update_article(alice, article, name="Filter")
        assert list(article.compatible_items.all()) == [owner_item]
        services.update_article(alice, article, compatible_items=[])
        assert article.compatible_items.count() == 0

    def test_a_deleted_item_is_no_longer_compatible(
        self, alice: User, article: Article, owner_item: Item
    ) -> None:
        owner_item.soft_delete()
        assert list(article.compatible_items.all()) == []

    def test_deleting_an_article_deletes_what_hangs_from_it(
        self, alice: User, alice_home: Home, article: Article
    ) -> None:
        services.create_supplier(alice, article, name="Acme")
        entry(alice, article, "1")
        services.delete_article(alice, article)
        assert not Article.objects.filter(id=article.id).exists()
        assert not Supplier.objects.filter(home=alice_home).exists()
        assert not Movement.objects.filter(home=alice_home).exists()
        assert Article.all_objects.filter(id=article.id).exists()

    def test_read_only_members_and_strangers_write_nothing(
        self, reader: User, bob: User, alice: User, alice_home: Home, article: Article
    ) -> None:
        for actor in (reader, bob):
            with pytest.raises(PermissionDenied):
                services.create_article(actor, alice_home, name="x")
            with pytest.raises(PermissionDenied):
                services.update_article(actor, article, name="x")
            with pytest.raises(PermissionDenied):
                services.delete_article(actor, article)
            with pytest.raises(PermissionDenied):
                services.create_supplier(actor, article, name="x")
            with pytest.raises(PermissionDenied):
                services.record_movement(actor, article, kind="entry", quantity=Decimal("1"))

    def test_a_member_writes(self, member: User, alice_home: Home) -> None:
        assert services.create_article(member, alice_home, name="x").name == "x"


class TestSuppliers:
    def test_create_update_delete(self, alice: User, article: Article) -> None:
        supplier = services.create_supplier(
            alice,
            article,
            name="Acme",
            reference="A-1",
            url="https://shop.example.com/a-1",
            last_price=Decimal("12.50"),
        )
        services.update_supplier(alice, supplier, last_price=Decimal("13"))
        supplier.refresh_from_db()
        assert supplier.last_price == Decimal("13.00")
        assert article.suppliers.count() == 1
        services.delete_supplier(alice, supplier)
        assert article.suppliers.count() == 0

    @pytest.mark.parametrize(
        "link",
        [
            "javascript:alert(1)",
            "JaVaScRiPt:alert(1)",
            "data:text/html,<script>1</script>",
            "file:///etc/passwd",
            "ftp://example.com/x",
            "not a link",
        ],
    )
    def test_only_web_links_are_accepted(self, alice: User, article: Article, link: str) -> None:
        with pytest.raises(ValidationError) as caught:
            services.create_supplier(alice, article, name="Acme", url=link)
        assert "url" in caught.value.message_dict

    def test_the_price_cannot_be_negative(self, alice: User, article: Article) -> None:
        with pytest.raises(ValidationError):
            services.create_supplier(alice, article, name="Acme", last_price=Decimal("-1"))


class TestLabel:
    def stored(self, article: Article) -> Path:
        return Path(article.label.path)

    def test_the_photo_is_written_again_without_its_location(
        self, alice: User, alice_home: Home, media_root: Path
    ) -> None:
        article = services.create_article(
            alice, alice_home, name="Tin", label=upload(image_bytes(gps=True, comment=True))
        )
        assert article.label_content_type == "image/jpeg"
        assert self.stored(article).parent == media_root / "stock" / str(alice_home.id)
        with Image.open(self.stored(article)) as stored:
            assert not stored.getexif().get(0x8825)
            assert b"secret comment" not in self.stored(article).read_bytes()
        assert self.stored(article).name == f"{article.id}.jpg"

    @pytest.mark.parametrize(
        ("data", "name"),
        [
            (b"<script>alert(1)</script>", "label.jpg"),
            (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "label.png"),
            (b"GIF89a\x01\x00\x01\x00", "label.gif"),
            (b"", "label.jpg"),
        ],
    )
    def test_a_file_that_is_not_an_image_is_refused(
        self, alice: User, alice_home: Home, data: bytes, name: str, media_root: Path
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            services.create_article(alice, alice_home, name="Tin", label=upload(data, name))
        assert "label" in caught.value.message_dict
        assert not Article.objects.filter(name="Tin").exists()
        assert not (media_root / "stock").exists()

    def test_a_file_over_the_limit_is_refused(
        self, alice: User, alice_home: Home, settings: object
    ) -> None:
        settings.HOUSECAIRN_PHOTO_MAX_BYTES = conf.MIN_PHOTO_MAX_BYTES  # type: ignore[attr-defined]
        data = noise_image(side=96)
        assert len(data) > conf.MIN_PHOTO_MAX_BYTES
        with pytest.raises(ValidationError, match="too large"):
            services.create_article(alice, alice_home, name="Tin", label=upload(data))

    def test_replacing_and_removing_the_photo_removes_the_file(
        self, alice: User, article: Article
    ) -> None:
        services.update_article(alice, article, label=upload(image_bytes()))
        first = self.stored(article)
        assert first.is_file()
        services.update_article(alice, article, label=upload(image_bytes("PNG")))
        assert not first.exists()
        second = self.stored(article)
        assert (second.is_file(), article.label_content_type) == (True, "image/png")
        services.update_article(alice, article, name="Renamed")
        assert second.is_file()
        services.update_article(alice, article, clear_label=True)
        assert not second.exists()
        assert (article.label.name, article.label_content_type) == ("", "")

    def test_a_refused_photo_leaves_the_article_unchanged(
        self, alice: User, article: Article
    ) -> None:
        with pytest.raises(ValidationError):
            services.update_article(alice, article, name="Changed", label=upload(b"nope"))
        article.refresh_from_db()
        assert article.name == "Boiler filter"


class TestScreens:
    def test_sign_in_is_required(self, client: Client, alice_home: Home, article: Article) -> None:
        for address in (
            url("list", alice_home),
            url("detail", alice_home, article_id=article.id),
            url("label", alice_home, article_id=article.id),
            reverse("stock"),
        ):
            assert client.get(address).status_code == 302, address

    def test_the_entry_opens_the_only_home_or_the_current_one(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        assert signed_in.get(reverse("stock")).headers["Location"] == url("list", alice_home)
        from housecairn.homes import services as home_services

        signed_in.get(url("list", alice_home))
        home_services.create_home(alice, name="Holiday flat")
        # The current home, the one last opened: no page that asks.
        assert signed_in.get(reverse("stock")).headers["Location"] == url("list", alice_home)

    def test_the_list_shows_quantity_and_flags_low_stock(
        self, signed_in: Client, alice: User, alice_home: Home, article: Article
    ) -> None:
        page = signed_in.get(url("list", alice_home)).content.decode()
        assert "Boiler filter" in page
        assert "pill-warning" in page
        entry(alice, article, "5")
        page = signed_in.get(url("list", alice_home)).content.decode()
        assert "5 pieces" in page
        assert "pill-warning" not in page

    def test_the_list_searches_and_filters(
        self, signed_in: Client, alice: User, alice_home: Home, article: Article
    ) -> None:
        services.create_article(alice, alice_home, name="Paint")
        entry(alice, article, "9")
        found = signed_in.get(url("list", alice_home), {"q": "paint"}).content.decode()
        assert "Paint" in found
        assert "Boiler filter" not in found
        nothing = signed_in.get(url("list", alice_home), {"q": "zzz"}).content.decode()
        assert "No stock item matches." in nothing
        low = signed_in.get(url("list", alice_home), {"low": "1"}).content.decode()
        assert "Boiler filter" not in low

    def test_an_empty_list_says_so(self, signed_in: Client, alice_home: Home) -> None:
        assert "No stock item yet." in signed_in.get(url("list", alice_home)).content.decode()

    def test_create_with_a_compatible_item_and_a_label(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        response = signed_in.post(
            url("create", alice_home),
            {
                "name": "Anode",
                "compatible_items": [str(owner_item.id)],
                "label": upload(image_bytes(gps=True)),
            },
        )
        assert response.status_code == 302
        article = Article.objects.get(name="Anode")
        assert list(article.compatible_items.all()) == [owner_item]
        assert article.label
        page = signed_in.get(url("detail", alice_home, article_id=article.id)).content.decode()
        assert "Boiler" in page
        assert url("label", alice_home, article_id=article.id) in page

    def test_a_form_refuses_an_item_of_another_home(
        self, signed_in: Client, alice_home: Home, foreign_item: Item
    ) -> None:
        response = signed_in.post(
            url("create", alice_home),
            {"name": "Anode", "compatible_items": [str(foreign_item.id)]},
        )
        assert response.status_code == 200
        assert not Article.objects.filter(name="Anode").exists()

    def test_a_form_shows_the_error_of_a_refused_photo(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        response = signed_in.post(
            url("create", alice_home), {"name": "Anode", "label": upload(b"nope", "label.jpg")}
        )
        assert response.status_code == 200
        assert "not a valid JPEG, PNG or WebP image" in response.content.decode()
        assert not Article.objects.filter(name="Anode").exists()

    def test_an_oversized_body_is_refused_before_it_is_read(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        response = signed_in.post(
            url("create", alice_home),
            {"name": "Anode"},
            CONTENT_LENGTH=str(conf.photo_max_bytes() + conf.MULTIPART_OVERHEAD + 1),
        )
        assert response.status_code == 413

    def test_edit_and_remove_the_label(
        self, signed_in: Client, alice: User, alice_home: Home, article: Article
    ) -> None:
        address = url("edit", alice_home, article_id=article.id)
        assert "clear_label" not in signed_in.get(address).content.decode()
        services.update_article(alice, article, label=upload(image_bytes()))
        assert "clear_label" in signed_in.get(address).content.decode()
        response = signed_in.post(address, {"name": "Filter", "clear_label": "on"})
        assert response.status_code == 302
        article.refresh_from_db()
        assert (article.name, article.label.name) == ("Filter", "")
        assert article.compatible_items.count() == 0  # the form had none ticked

    def test_the_detail_page(
        self, signed_in: Client, alice: User, alice_home: Home, article: Article
    ) -> None:
        services.create_supplier(
            alice, article, name="Acme", url="https://shop.example.com/x", last_price=Decimal("9.9")
        )
        entry(alice, article, "4", note="bought in store")
        page = signed_in.get(url("detail", alice_home, article_id=article.id)).content.decode()
        for text in ("Boiler filter", "BF-200", "Garage", "Acme", "€9.90", "bought in store"):
            assert text in page
        assert 'href="https://shop.example.com/x"' in page
        assert 'rel="noopener noreferrer nofollow"' in page

    def test_delete_asks_then_deletes(
        self, signed_in: Client, alice_home: Home, article: Article
    ) -> None:
        address = url("delete", alice_home, article_id=article.id)
        assert signed_in.get(address).status_code == 200
        assert Article.objects.filter(id=article.id).exists()
        assert signed_in.post(address).status_code == 302
        assert not Article.objects.filter(id=article.id).exists()

    def test_quick_movements_with_the_three_buttons(
        self, signed_in: Client, alice_home: Home, article: Article
    ) -> None:
        address = url("movement-add", alice_home, article_id=article.id)

        def press(kind: str, quantity: str = "1") -> None:
            response = signed_in.post(address, {"kind": kind, "quantity": quantity, "note": "n"})
            assert response.status_code == 302

        press("entry", "6")
        press("exit", "2")
        assert services.quantity_of(article) == 4
        press("inventory", "3")
        assert services.quantity_of(article) == 3
        press("exit", "10")  # more than there is: nothing recorded
        assert services.quantity_of(article) == 3
        assert Movement.objects.filter(article=article).count() == 3
        page = signed_in.get(url("detail", alice_home, article_id=article.id)).content.decode()
        assert "Only 3 in stock." in page
        assert signed_in.get(address).status_code == 405

    def test_a_movement_with_a_bad_quantity_records_nothing(
        self, signed_in: Client, alice_home: Home, article: Article
    ) -> None:
        address = url("movement-add", alice_home, article_id=article.id)
        for data in ({"kind": "entry", "quantity": "x"}, {"kind": "entry", "quantity": "0"}):
            assert signed_in.post(address, data).status_code == 302
        assert Movement.objects.count() == 0

    def test_supplier_screens(self, signed_in: Client, alice_home: Home, article: Article) -> None:
        create = url("supplier-create", alice_home, article_id=article.id)
        assert signed_in.get(create).status_code == 200
        bad = signed_in.post(create, {"name": "Acme", "url": "javascript:alert(1)"})
        assert bad.status_code == 200
        assert not Supplier.objects.exists()
        ok = signed_in.post(create, {"name": "Acme", "url": "shop.example.com/x"})
        assert ok.status_code == 302
        supplier = Supplier.objects.get()
        assert supplier.url == "https://shop.example.com/x"
        edit = url("supplier-edit", alice_home, supplier_id=supplier.id)
        assert signed_in.post(edit, {"name": "Acme 2"}).status_code == 302
        supplier.refresh_from_db()
        assert supplier.name == "Acme 2"
        delete = url("supplier-delete", alice_home, supplier_id=supplier.id)
        assert signed_in.get(delete).status_code == 200
        assert signed_in.post(delete).status_code == 302
        assert not Supplier.objects.exists()

    def test_the_label_is_served_as_an_image_and_nothing_else(
        self, signed_in: Client, alice: User, alice_home: Home, article: Article
    ) -> None:
        address = url("label", alice_home, article_id=article.id)
        assert signed_in.get(address).status_code == 404  # no label yet
        services.update_article(alice, article, label=upload(image_bytes("PNG")))
        response = signed_in.get(address)
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["Content-Security-Policy"] == "default-src 'none'; sandbox"
        assert response["Content-Disposition"] == f'inline; filename="{article.id}.png"'
        Path(article.label.path).unlink()
        assert signed_in.get(address).status_code == 404


class TestRolesAndIsolation:
    def test_read_only_members_read_and_change_nothing(
        self, client: Client, reader: User, alice_home: Home, article: Article
    ) -> None:
        client.force_login(reader)
        for name, kwargs in (("list", {}), ("detail", {"article_id": article.id})):
            assert client.get(url(name, alice_home, **kwargs)).status_code == 200
        page = client.get(url("detail", alice_home, article_id=article.id)).content.decode()
        assert "Record a movement" not in page
        assert "Edit the stock item" not in page
        supplier = Supplier.objects.create(home=alice_home, article=article, name="a")
        writes = [
            ("get", url("create", alice_home)),
            ("get", url("edit", alice_home, article_id=article.id)),
            ("get", url("delete", alice_home, article_id=article.id)),
            ("post", url("movement-add", alice_home, article_id=article.id)),
            ("get", url("supplier-create", alice_home, article_id=article.id)),
            ("get", url("supplier-edit", alice_home, supplier_id=supplier.id)),
            ("post", url("supplier-delete", alice_home, supplier_id=supplier.id)),
        ]
        for method, address in writes:
            assert (
                getattr(client, method)(address, {"kind": "entry", "quantity": "1"}).status_code
                == 403
            ), address
        assert Movement.objects.count() == 0

    def test_a_member_writes(
        self, client: Client, member: User, alice_home: Home, article: Article
    ) -> None:
        client.force_login(member)
        assert client.post(url("create", alice_home), {"name": "Paint"}).status_code == 302
        address = url("movement-add", alice_home, article_id=article.id)
        assert client.post(address, {"kind": "entry", "quantity": "1"}).status_code == 302

    def test_another_home_is_a_404_everywhere(
        self, client: Client, bob: User, alice_home: Home, article: Article
    ) -> None:
        supplier = Supplier.objects.create(home=alice_home, article=article, name="Acme")
        client.force_login(bob)
        pages = [
            ("get", url("list", alice_home)),
            ("get", url("create", alice_home)),
            ("get", url("detail", alice_home, article_id=article.id)),
            ("get", url("edit", alice_home, article_id=article.id)),
            ("get", url("delete", alice_home, article_id=article.id)),
            ("get", url("label", alice_home, article_id=article.id)),
            ("post", url("movement-add", alice_home, article_id=article.id)),
            ("get", url("supplier-create", alice_home, article_id=article.id)),
            ("get", url("supplier-edit", alice_home, supplier_id=supplier.id)),
            ("post", url("supplier-delete", alice_home, supplier_id=supplier.id)),
        ]
        for method, address in pages:
            assert getattr(client, method)(address).status_code == 404, address

    def test_an_article_of_another_home_is_not_reachable_through_my_home(
        self, signed_in: Client, alice_home: Home, foreign_article: Article
    ) -> None:
        for name in ("detail", "edit", "delete", "label"):
            address = url(name, alice_home, article_id=foreign_article.id)
            assert signed_in.get(address).status_code == 404, name
        assert (
            signed_in.post(
                url("movement-add", alice_home, article_id=foreign_article.id),
                {"kind": "entry", "quantity": "1"},
            ).status_code
            == 404
        )
        assert Movement.objects.count() == 0

    def test_a_supplier_of_another_home_is_not_reachable_through_my_home(
        self, signed_in: Client, bob_home: Home, alice_home: Home, foreign_article: Article
    ) -> None:
        theirs = Supplier.objects.create(home=bob_home, article=foreign_article, name="Theirs")
        assert (
            signed_in.get(url("supplier-edit", alice_home, supplier_id=theirs.id)).status_code
            == 404
        )

    def test_the_list_shows_only_my_home(
        self, signed_in: Client, alice_home: Home, article: Article, foreign_article: Article
    ) -> None:
        page = signed_in.get(url("list", alice_home)).content.decode()
        assert "Boiler filter" in page
        assert "Foreign filter" not in page


class TestNavigation:
    def test_the_rooms_entry_is_highlighted_on_its_screens(
        self, alice_home: Home, article: Article
    ) -> None:
        factory = RequestFactory()
        for address in (url("list", alice_home), url("detail", alice_home, article_id=article.id)):
            request = factory.get(address)
            request.resolver_match = resolve(address)
            assert nav_section(request) == "rooms"
            assert stock_url(request) == url("list", alice_home)
        request = factory.get(reverse("stock"))
        request.resolver_match = resolve(reverse("stock"))
        assert (nav_section(request), stock_url(request)) == ("rooms", reverse("stock"))


class TestLedgerRules:
    def test_a_movement_locks_the_article_before_reading_its_stock(
        self, alice: User, article: Article, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`record_movement` asks for `SELECT ... FOR UPDATE` on the article, then sums.

        On PostgreSQL two movements at once wait for each other (the next test); SQLite has no
        row lock, so what can be checked everywhere is that the lock is requested first.
        """
        from typing import Any

        from django.db.models import QuerySet

        order: list[str] = []
        original_lock = QuerySet.select_for_update
        original_sum = services.quantity_of

        def spy_lock(self: QuerySet[Any], *args: Any, **kwargs: Any) -> QuerySet[Any]:
            order.append(f"lock {self.model.__name__}")
            return original_lock(self, *args, **kwargs)

        def spy_sum(target: Article) -> Decimal:
            order.append("sum")
            return original_sum(target)

        monkeypatch.setattr(QuerySet, "select_for_update", spy_lock)
        monkeypatch.setattr(services, "quantity_of", spy_sum)
        entry(alice, article, "3")
        services.record_movement(alice, article, kind="exit", quantity=Decimal("1"))
        # The change log locks the row of the home as well, once a movement is written.
        assert [step for step in order if step != "lock Home"] == [
            "lock Article",
            "sum",
            "lock Article",
            "sum",
        ]

    @pytest.mark.django_db(transaction=True)
    def test_two_exits_at_once_cannot_both_take_the_last_unit(
        self, alice: User, article: Article
    ) -> None:
        """On PostgreSQL, two connections: the second waits for the lock, then sees zero."""
        import threading

        from django.db import connection, connections

        if connection.vendor != "postgresql":
            pytest.skip("a row lock needs PostgreSQL; the request for it is checked above")
        entry(alice, article, "1")
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def take() -> None:
            try:
                barrier.wait(timeout=10)
                services.record_movement(alice, article, kind="exit", quantity=Decimal("1"))
                outcomes.append("taken")
            except ValidationError:
                outcomes.append("refused")
            finally:
                connections.close_all()

        threads = [threading.Thread(target=take) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert sorted(outcomes) == ["refused", "taken"]
        assert services.quantity_of(article) == Decimal("0")

    def test_a_count_far_from_the_stock_is_an_error_of_the_quantity(
        self, alice: User, article: Article
    ) -> None:
        for _unused in range(10):
            entry(alice, article, "99999999.99")
        with pytest.raises(ValidationError) as caught:
            services.record_movement(alice, article, kind="inventory", quantity=Decimal("0"))
        assert set(caught.value.message_dict) == {"quantity"}
        assert "too large" in caught.value.message_dict["quantity"][0]
        assert Movement.objects.filter(article=article, kind="inventory").count() == 0

    def test_the_api_carries_that_error_on_the_quantity(
        self, alice: User, alice_home: Home, article: Article
    ) -> None:
        from rest_framework.test import APIClient

        for _unused in range(10):
            entry(alice, article, "99999999.99")
        client = APIClient()
        client.force_authenticate(alice)
        response = client.post(
            reverse("api-v1:home-movement-list", kwargs={"home_id": alice_home.id}),
            {"article": str(article.id), "kind": "inventory", "quantity": "0"},
        )
        assert response.status_code == 400
        assert set(response.json()) == {"quantity"}

    def test_a_backdated_count_is_worked_out_against_the_stock_now(
        self, alice: User, article: Article
    ) -> None:
        """The date only places the line in the history; the difference is with today's stock."""
        entry(alice, article, "10", occurred_on=datetime.date(2026, 1, 10))
        services.record_movement(
            alice,
            article,
            kind="exit",
            quantity=Decimal("4"),
            occurred_on=datetime.date(2026, 3, 1),
        )
        counted = services.record_movement(
            alice,
            article,
            kind="inventory",
            quantity=Decimal("5"),
            occurred_on=datetime.date(2026, 2, 1),
        )
        # 10 - 4 = 6 in stock now; a count of 5, dated before the exit, still costs -1.
        assert counted.delta == Decimal("-1.00")
        assert services.quantity_of(article) == Decimal("5.00")


class TestLabelFileSwap:
    def test_a_save_that_fails_keeps_the_old_label_and_leaves_no_new_file(
        self,
        alice: User,
        alice_home: Home,
        media_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        article = services.create_article(
            alice, alice_home, name="Tin", label=upload(image_bytes())
        )
        folder = media_root / "stock" / str(alice_home.id)
        before = sorted(p.name for p in folder.iterdir())

        def boom(self: Article, *args: object, **kwargs: object) -> None:
            raise RuntimeError("database down")

        monkeypatch.setattr(Article, "save", boom)
        with pytest.raises(RuntimeError):
            services.update_article(alice, article, label=upload(image_bytes("PNG")))
        assert sorted(p.name for p in folder.iterdir()) == before

    def test_a_creation_that_fails_leaves_no_file(
        self,
        alice: User,
        alice_home: Home,
        media_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def boom(self: Article, *args: object, **kwargs: object) -> None:
            raise RuntimeError("database down")

        monkeypatch.setattr(Article, "save", boom)
        with pytest.raises(RuntimeError):
            services.create_article(alice, alice_home, name="Tin", label=upload(image_bytes()))
        folder = media_root / "stock" / str(alice_home.id)
        assert not folder.exists() or not list(folder.iterdir())

    def test_the_old_file_goes_only_once_the_article_is_saved(
        self,
        alice: User,
        alice_home: Home,
        media_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        article = services.create_article(
            alice, alice_home, name="Tin", label=upload(image_bytes())
        )
        old = Path(article.label.path)
        seen: list[bool] = []
        original = Article.save

        def watch(self: Article, *args: object, **kwargs: object) -> None:
            seen.append(old.is_file())  # still there while the row is written
            original(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Article, "save", watch)
        services.update_article(alice, article, label=upload(image_bytes("PNG")))
        assert seen == [True]
        assert not old.exists()
        assert Path(article.label.path).is_file()

    def test_removing_the_label_keeps_the_file_until_the_save(
        self,
        alice: User,
        alice_home: Home,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        article = services.create_article(
            alice, alice_home, name="Tin", label=upload(image_bytes())
        )
        stored = Path(article.label.path)

        def boom(self: Article, *args: object, **kwargs: object) -> None:
            raise RuntimeError("database down")

        monkeypatch.setattr(Article, "save", boom)
        with pytest.raises(RuntimeError):
            services.update_article(alice, article, clear_label=True)
        assert stored.is_file()

"""The stock on the page of an item: link, create in place, take out (docs/adr/0088).

The link is the `compatible_items` of the article, edited from the other side. Nothing is stored
differently: the tests also check that the sync and the audit see the same change as with the
form of the article.
"""

import re
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from housecairn.accounts.models import User
from housecairn.audit.models import AuditEvent
from housecairn.homes.models import Home
from housecairn.rooms.models import Item
from housecairn.stock import services
from housecairn.stock.models import Article
from housecairn.sync.models import Change
from tests.htmlscan import inline_code_problems

pytest_plugins = ("tests.rooms_fixtures", "tests.stock_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root")]


def stock_url(name: str, home: Home, item: Item, **kwargs: object) -> str:
    return reverse(f"stock:{name}", kwargs={"home_id": home.id, "item_id": item.id, **kwargs})


def item_page(home: Home, item: Item) -> str:
    return reverse("rooms:item-detail", kwargs={"home_id": home.id, "item_id": item.id})


@pytest.fixture
def filter_article(alice: User, alice_home: Home) -> Article:
    return services.create_article(alice, alice_home, name="Descaler", unit_price=Decimal("7.90"))


def linked(article: Article) -> set[Item]:
    return set(article.compatible_items.all())


class TestTheSection:
    def test_it_lists_the_linked_articles_with_quantity_and_price(
        self, signed_in: Client, alice: User, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        services.record_movement(alice, article, kind="entry", quantity=Decimal("3"))
        services.update_article(alice, article, unit_price=Decimal("12.50"))
        page = signed_in.get(item_page(alice_home, owner_item)).content.decode()
        assert 'id="stock"' in page
        assert "Boiler filter" in page
        assert "3 pieces" in page
        assert "12.50" in page
        detail = reverse(
            "stock:detail", kwargs={"home_id": alice_home.id, "article_id": article.id}
        )
        assert detail in page

    def test_an_article_without_price_shows_none(
        self, signed_in: Client, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        page = signed_in.get(item_page(alice_home, owner_item)).content.decode()
        assert "Boiler filter" in page
        assert "€" not in page

    def test_an_article_of_another_item_is_not_listed(
        self,
        signed_in: Client,
        alice: User,
        alice_home: Home,
        owner_item: Item,
        filter_article: Article,
    ) -> None:
        page = signed_in.get(item_page(alice_home, owner_item)).content.decode()
        assert "Descaler" not in page
        assert "No stock item linked to this yet." in page

    def test_a_member_sees_the_three_actions_and_a_reader_none(
        self,
        client: Client,
        member: User,
        reader: User,
        alice_home: Home,
        owner_item: Item,
        article: Article,
    ) -> None:
        for user, sees in ((member, True), (reader, False)):
            client.force_login(user)
            page = client.get(item_page(alice_home, owner_item)).content.decode()
            assert "Boiler filter" in page  # a reader reads the section
            assert (stock_url("item-link", alice_home, owner_item) in page) is sees
            assert (stock_url("item-create", alice_home, owner_item) in page) is sees
            unlink = stock_url("item-unlink", alice_home, owner_item, article_id=article.id)
            assert (unlink in page) is sees

    def test_without_script_every_action_is_a_link_or_a_form(
        self, signed_in: Client, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        page = signed_in.get(item_page(alice_home, owner_item)).content.decode()
        assert 'method="post"' in page
        assert 'class="row-actions"' in page
        assert not inline_code_problems(page)
        assert "hx-" not in page.split('id="stock"')[1].split("<h2")[0]


class TestLinking:
    def test_the_link_page_offers_the_articles_not_linked_yet(
        self,
        signed_in: Client,
        alice_home: Home,
        owner_item: Item,
        article: Article,
        filter_article: Article,
    ) -> None:
        page = signed_in.get(stock_url("item-link", alice_home, owner_item)).content.decode()
        assert f'<option value="{filter_article.id}">' in page
        assert str(article.id) not in page  # already linked
        assert "chip-select.js" in page
        assert "<select" in page  # the native select shows without script

    def test_it_links_several_articles_at_once(
        self, signed_in: Client, alice: User, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        one = services.create_article(alice, alice_home, name="Gasket")
        two = services.create_article(alice, alice_home, name="Valve")
        response = signed_in.post(
            stock_url("item-link", alice_home, owner_item), {"articles": [one.id, two.id]}
        )
        assert response.status_code == 302
        assert response["Location"] == item_page(alice_home, owner_item)
        assert linked(one) == linked(two) == {owner_item}
        assert linked(article) == {owner_item}

    def test_it_keeps_the_other_items_of_the_article(
        self, signed_in: Client, alice: User, alice_home: Home, owner_item: Item, owner_room: object
    ) -> None:
        from housecairn.rooms import services as room_services

        other = room_services.create_item(alice, owner_item.room, name="Stove")
        shared = services.create_article(alice, alice_home, name="Gasket", compatible_items=[other])
        signed_in.post(stock_url("item-link", alice_home, owner_item), {"articles": [shared.id]})
        assert linked(shared) == {owner_item, other}

    def test_nothing_chosen_is_refused_and_nothing_changes(
        self, signed_in: Client, alice_home: Home, owner_item: Item, filter_article: Article
    ) -> None:
        response = signed_in.post(stock_url("item-link", alice_home, owner_item), {})
        assert response.status_code == 200
        assert linked(filter_article) == set()

    def test_an_article_of_another_home_is_refused(
        self,
        signed_in: Client,
        alice_home: Home,
        owner_item: Item,
        foreign_article: Article,
    ) -> None:
        response = signed_in.post(
            stock_url("item-link", alice_home, owner_item), {"articles": [foreign_article.id]}
        )
        assert response.status_code == 200  # the form is shown again, with its error
        assert linked(foreign_article) == set()
        assert foreign_article.compatible_items.count() == 0

    def test_the_service_refuses_an_article_of_another_home(
        self, alice: User, owner_item: Item, foreign_article: Article
    ) -> None:
        with pytest.raises(ValidationError):
            services.link_articles(alice, owner_item, [foreign_article])
        assert linked(foreign_article) == set()

    def test_a_home_that_is_not_the_one_of_the_item_is_a_404(
        self, client: Client, bob: User, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        client.force_login(bob)
        for method, name, extra in (
            ("get", "item-link", {}),
            ("post", "item-link", {}),
            ("get", "item-create", {}),
            ("post", "item-unlink", {"article_id": article.id}),
        ):
            address = stock_url(name, alice_home, owner_item, **extra)
            assert getattr(client, method)(address).status_code == 404, address

    def test_an_item_of_another_home_is_a_404_through_the_own_home(
        self,
        client: Client,
        bob: User,
        bob_home: Home,
        owner_item: Item,
        article: Article,
        foreign_article: Article,
    ) -> None:
        # Bob is a member of his own home and asks for the item of Alice through it: the home
        # is fine, the item is not in it.
        client.force_login(bob)
        link = stock_url("item-link", bob_home, owner_item)
        create = stock_url("item-create", bob_home, owner_item)
        unlink = stock_url("item-unlink", bob_home, owner_item, article_id=foreign_article.id)
        assert client.get(link).status_code == 404
        assert client.post(link, {"articles": [foreign_article.id]}).status_code == 404
        assert client.get(create).status_code == 404
        assert client.post(create, {"name": "Gasket"}).status_code == 404
        assert client.post(unlink).status_code == 404
        assert linked(foreign_article) == set()
        assert linked(article) == {owner_item}
        assert not Article.objects.filter(name="Gasket").exists()

    def test_the_link_is_a_sync_change_like_the_one_of_the_form(
        self, alice: User, alice_home: Home, owner_item: Item, filter_article: Article
    ) -> None:
        before = Change.objects.filter(home=alice_home).count()
        services.link_articles(alice, owner_item, [filter_article])
        added = Change.objects.filter(home=alice_home, entity="stock.article_compatible_items")
        assert added.latest("pk").fields == {
            "article_id": str(filter_article.pk),
            "item_id": str(owner_item.pk),
        }
        assert Change.objects.filter(home=alice_home).count() > before
        assert AuditEvent.objects.filter(home_id=alice_home.id, action="article.updated").exists()

    def test_linking_again_changes_nothing(
        self, alice: User, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        before = Change.objects.filter(home=alice_home).count()
        services.link_articles(alice, owner_item, [article])
        assert Change.objects.filter(home=alice_home).count() == before
        assert linked(article) == {owner_item}


class TestCreatingInPlace:
    def test_the_form_has_the_item_chosen(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        page = signed_in.get(stock_url("item-create", alice_home, owner_item)).content.decode()
        assert f'<option value="{owner_item.id}" selected>' in page
        assert "It will be linked to Boiler." in page
        assert 'name="unit_price"' in page  # the form of the creation of an article

    def test_the_new_article_is_linked_and_the_person_comes_back_to_the_item(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        next_page = item_page(alice_home, owner_item)
        response = signed_in.post(
            stock_url("item-create", alice_home, owner_item),
            {"name": "Gasket", "unit_price": "2.50", "next": next_page},
        )
        assert response.status_code == 302
        assert response["Location"] == next_page
        created = Article.objects.get(home=alice_home, name="Gasket")
        assert linked(created) == {owner_item}
        assert created.unit_price == Decimal("2.50")

    def test_the_item_is_linked_even_if_the_form_leaves_it_out(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        # The select was emptied by hand: the article still fits the item it was started from.
        signed_in.post(stock_url("item-create", alice_home, owner_item), {"name": "Gasket"})
        assert linked(Article.objects.get(name="Gasket")) == {owner_item}

    def test_the_validation_is_the_one_of_the_article_form(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        address = stock_url("item-create", alice_home, owner_item)
        assert signed_in.post(address, {"name": ""}).status_code == 200
        assert signed_in.post(address, {"name": "x", "unit": "other"}).status_code == 200
        assert Article.objects.filter(home=alice_home).count() == 0

    def test_it_cannot_link_an_item_of_another_home(
        self, signed_in: Client, alice_home: Home, owner_item: Item, foreign_item: Item
    ) -> None:
        response = signed_in.post(
            stock_url("item-create", alice_home, owner_item),
            {"name": "Gasket", "compatible_items": [foreign_item.id]},
        )
        assert response.status_code == 200
        assert Article.objects.filter(home=alice_home).count() == 0


class TestTakingOut:
    def test_the_link_goes_and_the_article_stays_with_its_stock(
        self, signed_in: Client, alice: User, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        services.record_movement(alice, article, kind="entry", quantity=Decimal("4"))
        response = signed_in.post(
            stock_url("item-unlink", alice_home, owner_item, article_id=article.id)
        )
        assert response.status_code == 302
        assert response["Location"].endswith("#stock")
        assert linked(article) == set()
        kept = services.articles_of(alice_home).get(id=article.id)
        assert kept.deleted_at is None
        assert kept.quantity == Decimal("4")  # type: ignore[attr-defined]

    def test_the_other_links_of_the_article_stay(
        self, signed_in: Client, alice: User, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        from housecairn.rooms import services as room_services

        other = room_services.create_item(alice, owner_item.room, name="Stove")
        services.link_articles(alice, other, [article])
        signed_in.post(stock_url("item-unlink", alice_home, owner_item, article_id=article.id))
        assert linked(article) == {other}

    def test_it_is_a_post_only(
        self, signed_in: Client, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        address = stock_url("item-unlink", alice_home, owner_item, article_id=article.id)
        assert signed_in.get(address).status_code == 405
        assert linked(article) == {owner_item}

    def test_the_removal_is_a_sync_delete(
        self, alice: User, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        services.unlink_article(alice, owner_item, article)
        last = Change.objects.filter(
            home=alice_home, entity="stock.article_compatible_items"
        ).latest("pk")
        assert last.kind == "delete"

    def test_an_article_of_another_home_is_a_404(
        self, signed_in: Client, alice_home: Home, owner_item: Item, foreign_article: Article
    ) -> None:
        address = stock_url("item-unlink", alice_home, owner_item, article_id=foreign_article.id)
        assert signed_in.post(address).status_code == 404


class TestWhoMayWrite:
    def writes(self, home: Home, item: Item, article: Article) -> list[tuple[str, str]]:
        return [
            ("get", stock_url("item-link", home, item)),
            ("post", stock_url("item-link", home, item)),
            ("get", stock_url("item-create", home, item)),
            ("post", stock_url("item-create", home, item)),
            ("post", stock_url("item-unlink", home, item, article_id=article.id)),
        ]

    def test_a_read_only_member_changes_nothing(
        self, client: Client, reader: User, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        client.force_login(reader)
        others = services.create_article(
            User.objects.get(email="alice@example.com"), alice_home, name="Other"
        )
        for method, address in self.writes(alice_home, owner_item, article):
            data = {"name": "New", "articles": [others.id]}
            assert getattr(client, method)(address, data).status_code == 403, address
        assert linked(article) == {owner_item}
        assert linked(others) == set()
        assert not Article.objects.filter(name="New").exists()

    def test_the_services_refuse_a_read_only_member(
        self, reader: User, owner_item: Item, article: Article
    ) -> None:
        with pytest.raises(PermissionDenied):
            services.link_articles(reader, owner_item, [article])
        with pytest.raises(PermissionDenied):
            services.unlink_article(reader, owner_item, article)

    def test_the_service_refuses_a_read_only_member_even_when_there_is_nothing_to_remove(
        self, alice: User, reader: User, alice_home: Home, owner_item: Item
    ) -> None:
        not_linked = services.create_article(alice, alice_home, name="Spare")
        with pytest.raises(PermissionDenied):
            services.unlink_article(reader, owner_item, not_linked)

    def test_a_member_writes(
        self, client: Client, member: User, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        client.force_login(member)
        address = stock_url("item-unlink", alice_home, owner_item, article_id=article.id)
        assert client.post(address).status_code == 302
        assert linked(article) == set()

    def test_an_archived_home_is_written_like_everywhere_else(
        self,
        signed_in: Client,
        alice_home: Home,
        owner_item: Item,
        article: Article,
        filter_article: Article,
    ) -> None:
        # An archived home stays readable and editable (docs/adr/0079): the same here as on the
        # form of the article.
        Home.objects.filter(pk=alice_home.pk).update(archived_at=timezone.now())
        edit = reverse("stock:edit", kwargs={"home_id": alice_home.id, "article_id": article.id})
        assert signed_in.get(edit).status_code == 200
        link = stock_url("item-link", alice_home, owner_item)
        assert signed_in.get(link).status_code == 200
        assert signed_in.post(link, {"articles": [filter_article.id]}).status_code == 302
        assert linked(filter_article) == {owner_item}

    def test_the_demonstration_mode_does_not_change_the_stock_rules(
        self,
        settings: object,
        signed_in: Client,
        alice_home: Home,
        owner_item: Item,
        filter_article: Article,
    ) -> None:
        settings.HOUSECAIRN_DEMO = True  # type: ignore[attr-defined]
        page = signed_in.get(item_page(alice_home, owner_item)).content.decode()
        assert stock_url("item-link", alice_home, owner_item) in page
        response = signed_in.post(
            stock_url("item-link", alice_home, owner_item), {"articles": [filter_article.id]}
        )
        assert response.status_code == 302
        assert linked(filter_article) == {owner_item}


class TestFrench:
    def test_the_section_and_its_actions_are_translated(
        self, signed_in: Client, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        page = signed_in.get(item_page(alice_home, owner_item), HTTP_ACCEPT_LANGUAGE="fr").text
        for text in ("Matériel", "Lier un article", "Nouvel article", "Retirer"):
            assert text in page, text
        link = signed_in.get(
            stock_url("item-link", alice_home, owner_item), HTTP_ACCEPT_LANGUAGE="fr"
        ).text
        assert "Lier un article" in link


class TestRequestSafety:
    def test_taking_out_needs_the_csrf_token(
        self, alice: User, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        client = Client(enforce_csrf_checks=True)
        client.force_login(alice)
        address = stock_url("item-unlink", alice_home, owner_item, article_id=article.id)
        assert client.post(address).status_code == 403
        assert linked(article) == {owner_item}

    def test_the_form_to_take_out_carries_the_token(
        self, alice: User, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        client = Client(enforce_csrf_checks=True)
        client.force_login(alice)
        page = client.get(item_page(alice_home, owner_item)).content.decode()
        form = page.split("row-actions")[1].split("</form>")[0]
        assert "csrfmiddlewaretoken" in form
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', form)
        assert match
        address = stock_url("item-unlink", alice_home, owner_item, article_id=article.id)
        response = client.post(address, {"csrfmiddlewaretoken": match.group(1)})
        assert response.status_code == 302
        assert linked(article) == set()

    def test_a_hostile_next_does_not_leave_the_site_when_taking_out(
        self, signed_in: Client, alice_home: Home, owner_item: Item, article: Article
    ) -> None:
        address = stock_url("item-unlink", alice_home, owner_item, article_id=article.id)
        response = signed_in.post(address, {"next": "https://evil.example/"})
        assert response.status_code == 302
        assert response["Location"] == item_page(alice_home, owner_item) + "#stock"
        assert linked(article) == set()

    def test_a_hostile_next_does_not_leave_the_site_when_linking(
        self, signed_in: Client, alice_home: Home, owner_item: Item, filter_article: Article
    ) -> None:
        address = stock_url("item-link", alice_home, owner_item)
        response = signed_in.post(
            address, {"articles": [filter_article.id], "next": "//evil.example/"}
        )
        assert response.status_code == 302
        assert response["Location"] == item_page(alice_home, owner_item)
        assert linked(filter_article) == {owner_item}

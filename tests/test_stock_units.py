"""The unit of a stock item is a list plus Other, like the trade of a contractor (ADR 0046)."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import translation
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.homes.models import Home
from housecairn.rooms.services import items_of, rooms_of
from housecairn.stock import services
from housecairn.stock.forms import ArticleForm
from housecairn.stock.models import Article, Unit, unit_text

pytest_plugins = ("tests.rooms_fixtures", "tests.stock_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root")]

D = Decimal


def url(name: str, home: Home, **kwargs: object) -> str:
    return reverse(f"stock:{name}", kwargs={"home_id": home.id, **kwargs})


class TestTheList:
    def test_the_list_is_the_one_of_the_cahier(self) -> None:
        assert [value for value, _label in Unit.choices] == [
            "piece",
            "litre",
            "millilitre",
            "metre",
            "kilogram",
            "roll",
            "box",
            "other",
        ]

    @pytest.mark.parametrize(
        ("quantity", "expected"),
        [(D("1"), "litre"), (D("2"), "litres"), (D("0"), "litres"), (D("1.5"), "litres")],
    )
    def test_a_quantity_reads_with_the_right_number(self, quantity: Decimal, expected: str) -> None:
        with translation.override("en"):
            assert unit_text("litre", "", quantity) == expected

    def test_in_french_a_fraction_below_one_is_singular(self) -> None:
        with translation.override("fr"):
            assert unit_text("litre", "", D("0.5")) == "litre"
            assert unit_text("litre", "", D("2")) == "litres"
            assert unit_text("box", "", D("2")) == "boîtes"

    def test_a_millilitre_reads_ml_in_both_languages(self) -> None:
        for language in ("en", "fr"):
            with translation.override(language):
                assert unit_text("millilitre", "", D("1")) == "mL"
                assert unit_text("millilitre", "", D("250")) == "mL"
        with translation.override("fr"):
            assert Unit.MILLILITRE.label == "Millilitre (mL)"

    def test_a_millilitre_is_kept_and_read_back(self, alice: User, alice_home: Home) -> None:
        article = services.create_article(alice, alice_home, name="Descaler", unit="millilitre")
        article.refresh_from_db()
        assert article.unit == "millilitre"
        assert article.unit_for(D("500")) == "mL"

    def test_other_says_its_own(self) -> None:
        assert unit_text("other", "bag", D("3")) == "bag"
        assert unit_text("", "", D("3")) == ""


class TestRules:
    def test_a_unit_of_the_list(self, alice: User, alice_home: Home) -> None:
        article = services.create_article(alice, alice_home, name="Cable", unit="metre")
        assert (article.unit, article.unit_other, article.unit_label) == ("metre", "", "metre")

    def test_other_needs_its_text(self, alice: User, alice_home: Home) -> None:
        with pytest.raises(ValidationError) as caught:
            services.create_article(alice, alice_home, name="Sand", unit="other")
        assert "unit_other" in caught.value.message_dict
        article = services.create_article(
            alice, alice_home, name="Sand", unit="other", unit_other="  bag \n"
        )
        assert article.unit_other == "bag"

    def test_the_text_is_dropped_with_a_unit_of_the_list(
        self, alice: User, alice_home: Home
    ) -> None:
        article = services.create_article(
            alice, alice_home, name="Sand", unit="other", unit_other="bag"
        )
        services.update_article(alice, article, unit="box")
        assert (article.unit, article.unit_other) == ("box", "")

    def test_a_free_text_unit_is_refused(self, alice: User, alice_home: Home) -> None:
        with pytest.raises(ValidationError) as caught:
            services.create_article(alice, alice_home, name="Sand", unit="bags")
        assert "unit" in caught.value.message_dict

    def test_no_unit_is_allowed(self, alice: User, alice_home: Home) -> None:
        article = services.create_article(alice, alice_home, name="Sand")
        assert article.unit_label == ""


class TestScreens:
    def test_the_form_offers_the_list(self, signed_in: Client, alice_home: Home) -> None:
        page = signed_in.get(url("create", alice_home))
        for label in ("Piece", "Litre", "Metre", "Kilogram", "Roll", "Box", "Other"):
            assert f">{label}</option>" in page.text

    def test_the_form_keeps_the_text_of_other(self, signed_in: Client, alice_home: Home) -> None:
        response = signed_in.post(
            url("create", alice_home), {"name": "Sand", "unit": "other", "unit_other": "bag"}
        )
        assert response.status_code == 302
        assert Article.objects.get(name="Sand").unit_label == "bag"

    def test_the_form_asks_which_unit(self, signed_in: Client, alice_home: Home) -> None:
        response = signed_in.post(url("create", alice_home), {"name": "Sand", "unit": "other"})
        assert response.status_code == 200
        assert "Say which unit." in response.text
        assert not Article.objects.filter(name="Sand").exists()

    def test_the_form_refuses_a_unit_outside_the_list(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        response = signed_in.post(url("create", alice_home), {"name": "Sand", "unit": "pallets"})
        assert response.status_code == 200
        assert not Article.objects.filter(name="Sand").exists()

    def test_pages_write_the_quantity_with_its_unit(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        paint = services.create_article(alice, alice_home, name="Paint", unit="litre")
        services.record_movement(alice, paint, kind="entry", quantity=D("2.5"))
        services.record_movement(alice, paint, kind="exit", quantity=D("1.5"))
        page = signed_in.get(url("detail", alice_home, article_id=paint.id))
        assert "<strong>1</strong> litre" in page.text
        assert "Entry · +2.50 litres" in page.text
        assert "Exit · -1.50 litres" in page.text
        assert ">1 litre<" in signed_in.get(url("list", alice_home)).text

    def test_the_form_can_be_built_for_the_ledger(self, alice_home: Home) -> None:
        form = ArticleForm(items=items_of(alice_home), rooms=rooms_of(alice_home))
        assert form.fields["unit"].choices[0] == ("", "—")  # type: ignore[attr-defined]


class TestApi:
    @pytest.fixture
    def client_of_alice(self, api_client: APIClient, alice: User) -> APIClient:
        api_client.force_authenticate(alice)
        return api_client

    def test_a_unit_of_the_list_and_other(
        self, client_of_alice: APIClient, alice_home: Home
    ) -> None:
        address = reverse("api-v1:home-article-list", kwargs={"home_id": alice_home.id})
        created = client_of_alice.post(address, {"name": "Cable", "unit": "metre"}, format="json")
        assert created.status_code == 201, created.content
        assert (created.json()["unit"], created.json()["unit_label"]) == ("metre", "metre")
        other = client_of_alice.post(
            address, {"name": "Sand", "unit": "other", "unit_other": "bag"}, format="json"
        )
        assert (other.json()["unit"], other.json()["unit_other"], other.json()["unit_label"]) == (
            "other",
            "bag",
            "bag",
        )

    @pytest.mark.parametrize(
        "payload", [{"unit": "pallets"}, {"unit": "other"}, {"unit": "other", "unit_other": "  "}]
    )
    def test_a_bad_unit_is_a_400(
        self, client_of_alice: APIClient, alice_home: Home, payload: dict[str, str]
    ) -> None:
        address = reverse("api-v1:home-article-list", kwargs={"home_id": alice_home.id})
        response = client_of_alice.post(address, {"name": "Sand", **payload}, format="json")
        assert response.status_code == 400
        assert not Article.objects.exists()

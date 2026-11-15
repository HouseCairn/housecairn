"""The unit price of a stock item and the planned cost of a task (docs/adr/0062)."""

import datetime
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.budget import services as budget
from housecairn.homes import restore
from housecairn.homes import services as data_services
from housecairn.homes.models import Home, Role
from housecairn.stock import services
from housecairn.stock.models import Article
from housecairn.tasks import materials
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Occurrence, Task
from tests.data_support import archive_bytes, export_data, fill_home
from tests.test_restore_security import edit, entries, rebuild

pytest_plugins = ("tests.rooms_fixtures", "tests.stock_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root")]

D = Decimal
TODAY = datetime.date(2026, 11, 9)


def stock_url(name: str, home: Home, **kwargs: object) -> str:
    return reverse(f"stock:{name}", kwargs={"home_id": home.id, **kwargs})


def api(name: str, home: Home, **kwargs: object) -> str:
    return reverse(f"api-v1:home-{name}", kwargs={"home_id": home.id, **kwargs})


def make_article(alice: User, home: Home, name: str, price: str | None = None) -> Article:
    return services.create_article(
        alice, home, name=name, unit_price=None if price is None else D(price)
    )


def make_task(alice: User, home: Home, **fields: object) -> Task:
    return task_services.create_task(alice, home, title="Repaint", due_on=TODAY, **fields)


def plan_of(home: Home, task: Task) -> budget.TaskRow:
    row = next(r for r in budget.load(home, TODAY).tasks if r.task.id == task.id)
    return row


class TestTheField:
    def test_it_is_optional_and_never_negative(self, alice: User, alice_home: Home) -> None:
        assert make_article(alice, alice_home, "Screws").unit_price is None
        assert make_article(alice, alice_home, "Free", "0").unit_price == D("0")
        with pytest.raises(ValidationError) as refused:
            make_article(alice, alice_home, "Odd", "-0.01")
        assert "unit_price" in refused.value.message_dict

    def test_it_is_bounded_like_the_other_amounts(self, alice: User, alice_home: Home) -> None:
        with pytest.raises(ValidationError):
            make_article(alice, alice_home, "Gold", "100000000.00")
        with pytest.raises(ValidationError):
            make_article(alice, alice_home, "Fine", "1.005")

    def test_it_can_be_changed_and_emptied(self, alice: User, alice_home: Home) -> None:
        article = make_article(alice, alice_home, "Paint", "20")
        services.update_article(alice, article, unit_price=D("22.5"))
        assert Article.objects.get(id=article.id).unit_price == D("22.5")
        services.update_article(alice, article, unit_price=None)
        assert Article.objects.get(id=article.id).unit_price is None

    def test_a_reader_cannot_write_it(self, reader: User, article: Article) -> None:
        with pytest.raises(PermissionDenied):
            services.update_article(reader, article, unit_price=D("1"))


class TestPrefilledBySuppliers:
    def test_a_first_supplier_price_fills_an_empty_unit_price(
        self, alice: User, alice_home: Home
    ) -> None:
        article = make_article(alice, alice_home, "Paint")
        services.create_supplier(alice, article, name="Shop", last_price=D("19.90"))
        assert Article.objects.get(id=article.id).unit_price == D("19.90")

    def test_without_a_supplier_price_it_stays_empty(self, alice: User, alice_home: Home) -> None:
        article = make_article(alice, alice_home, "Paint")
        services.create_supplier(alice, article, name="Shop")
        assert Article.objects.get(id=article.id).unit_price is None

    def test_a_price_already_there_is_never_replaced(self, alice: User, alice_home: Home) -> None:
        article = make_article(alice, alice_home, "Paint", "18")
        supplier = services.create_supplier(alice, article, name="Shop", last_price=D("19.90"))
        services.update_supplier(alice, supplier, last_price=D("25"))
        assert Article.objects.get(id=article.id).unit_price == D("18")
        assert supplier.last_price == D("25")

    def test_a_price_added_later_to_a_supplier_fills_it(
        self, alice: User, alice_home: Home
    ) -> None:
        article = make_article(alice, alice_home, "Paint")
        supplier = services.create_supplier(alice, article, name="Shop")
        services.update_supplier(alice, supplier, last_price=D("7"))
        assert Article.objects.get(id=article.id).unit_price == D("7")

    def test_a_supplier_saved_with_the_same_price_does_not_fill_it_again(
        self, alice: User, alice_home: Home
    ) -> None:
        article = make_article(alice, alice_home, "Paint")
        supplier = services.create_supplier(alice, article, name="Shop", last_price=D("19.90"))
        services.update_article(alice, article, unit_price=None)  # emptied on purpose
        services.update_supplier(alice, supplier, name="Big shop", last_price=D("19.90"))
        assert Article.objects.get(id=article.id).unit_price is None
        services.update_supplier(alice, supplier, last_price=D("21"))
        assert Article.objects.get(id=article.id).unit_price == D("21")

    def test_the_supplier_keeps_its_own_price_and_the_unit_price_can_be_corrected(
        self, alice: User, alice_home: Home
    ) -> None:
        article = make_article(alice, alice_home, "Paint")
        supplier = services.create_supplier(alice, article, name="Shop", last_price=D("19.90"))
        services.update_article(alice, article, unit_price=D("15"))
        assert Article.objects.get(id=article.id).unit_price == D("15")
        supplier.refresh_from_db()
        assert supplier.last_price == D("19.90")

    def test_through_the_screen_of_the_supplier(
        self, signed_in: Client, alice_home: Home, alice: User
    ) -> None:
        article = make_article(alice, alice_home, "Paint")
        response = signed_in.post(
            stock_url("supplier-create", alice_home, article_id=article.id),
            {"name": "Shop", "last_price": "12.30"},
        )
        assert response.status_code == 302
        assert Article.objects.get(id=article.id).unit_price == D("12.30")


class TestPlannedCostOfATask:
    def listed(self, alice: User, task: Task, *lines: tuple[Article, str]) -> None:
        for article, quantity in lines:
            materials.add_material(alice, task, article, D(quantity))

    def test_priced_stock_replaces_the_typed_budget_which_is_kept(  # ADR 0081
        self, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home, material_budget=D("10"), labour_budget=D("5"))
        self.listed(
            alice,
            task,
            (make_article(alice, alice_home, "Paint", "20"), "2"),
            (make_article(alice, alice_home, "Tape", "1.50"), "3"),
        )
        row = plan_of(alice_home, task)
        assert row.material == D("10")  # what was typed, untouched but not counted
        assert row.stock.amount == D("44.50")
        assert row.material_from_stock
        assert row.material_planned == D("44.50")
        assert row.estimate == D("49.50")
        assert row.stock.unpriced == 0

    def test_a_decimal_quantity_is_rounded_once_to_the_cent(
        self, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home)
        self.listed(
            alice,
            task,
            (make_article(alice, alice_home, "Cord", "0.33"), "1.5"),
            (make_article(alice, alice_home, "Wire", "0.33"), "1.5"),
        )
        # 0.495 + 0.495 = 0.99: rounding each line first would give 1.00.
        assert plan_of(alice_home, task).stock.amount == D("0.99")

    def test_an_item_with_no_price_counts_for_nothing_and_is_counted(
        self, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home)
        self.listed(
            alice,
            task,
            (make_article(alice, alice_home, "Paint", "20"), "1"),
            (make_article(alice, alice_home, "Brush"), "1"),
            (make_article(alice, alice_home, "Roller"), "2"),
        )
        stock = plan_of(alice_home, task).stock
        assert (stock.amount, stock.priced, stock.unpriced) == (D("20.00"), 1, 2)

    def test_a_price_of_zero_is_a_price(self, alice: User, alice_home: Home) -> None:
        task = make_task(alice, alice_home)
        self.listed(alice, task, (make_article(alice, alice_home, "Offcut", "0"), "4"))
        row = plan_of(alice_home, task)
        assert (row.stock.priced, row.stock.unpriced, row.has_estimate) == (1, 0, True)

    def test_an_empty_typed_budget_is_not_an_estimate_without_priced_stock(
        self, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home)
        assert plan_of(alice_home, task).has_estimate is False
        self.listed(alice, task, (make_article(alice, alice_home, "Brush"), "1"))
        assert plan_of(alice_home, task).has_estimate is False
        self.listed(alice, task, (make_article(alice, alice_home, "Paint", "9"), "1"))
        row = plan_of(alice_home, task)
        assert (row.has_estimate, row.material, row.estimate) == (True, D("0"), D("9.00"))

    def test_the_plan_follows_the_price_and_the_deleted_lines_do_not_count(
        self, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home)
        paint = make_article(alice, alice_home, "Paint", "20")
        gone = make_article(alice, alice_home, "Gone", "100")
        self.listed(alice, task, (paint, "1"), (gone, "1"))
        services.delete_article(alice, gone)
        services.update_article(alice, paint, unit_price=D("30"))
        assert plan_of(alice_home, task).stock.amount == D("30.00")

    def test_the_stock_is_planned_every_time_the_task_repeats(
        self, alice: User, alice_home: Home
    ) -> None:
        task = make_task(
            alice, alice_home, repeat_mode="after_last", repeat_unit="year", repeat_interval=2
        )
        self.listed(alice, task, (make_article(alice, alice_home, "Filter", "40"), "1"))
        row = plan_of(alice_home, task)
        assert row.annual_cost == D("20.00")
        report = budget.by_task(budget.load(alice_home, TODAY), 2026)
        (line,) = report.lines
        assert line.extra["stock_estimate"] == D("40.00")
        assert line.amounts.planned == D("40.00")

    def test_by_nature_counts_the_stock_with_the_materials(
        self, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home, material_budget=D("10"))
        self.listed(alice, task, (make_article(alice, alice_home, "Paint", "20"), "1"))
        natures = {
            line.label: line.amounts
            for line in budget.by_nature(budget.load(alice_home, TODAY), 2026)
        }
        assert natures["material"].planned == D("20.00")  # the stock, not 10 + 20

    def test_a_task_of_another_home_is_not_counted(
        self, alice: User, bob: User, alice_home: Home, bob_home: Home
    ) -> None:
        task = make_task(alice, alice_home)
        theirs = make_task(bob, bob_home)
        materials.add_material(bob, theirs, make_article(bob, bob_home, "Paint", "50"), D("1"))
        assert plan_of(alice_home, task).stock.amount == D("0")

    def test_the_number_of_queries_does_not_grow_with_the_lines(
        self, alice: User, alice_home: Home
    ) -> None:
        def count() -> int:
            with CaptureQueriesContext(connection) as queries:
                budget.load(alice_home, TODAY)
            return len(queries)

        first = make_task(alice, alice_home)
        self.listed(alice, first, (make_article(alice, alice_home, "A", "1"), "1"))
        before = count()
        for number in range(5):
            task = make_task(alice, alice_home)
            self.listed(alice, task, (make_article(alice, alice_home, f"B{number}", "1"), "1"))
        assert count() == before


def done_once(alice: User, task: Task, cost: str | None = None) -> None:
    occurrence = Occurrence.objects.get(task=task, status="planned")
    task_services.complete(
        alice, occurrence, on=TODAY, actual_cost=None if cost is None else D(cost)
    )


class TestEstimateOfADoneOccurrence:
    def page(self, signed_in: Client, home: Home, task: Task) -> str:
        occurrence = Occurrence.objects.get(task=task, status="done")
        url = reverse(
            "tasks:occurrence-edit",
            kwargs={"home_id": home.id, "task_id": task.id, "occurrence_id": occurrence.id},
        )
        return signed_in.get(url).content.decode()

    def test_the_estimate_is_the_stock_in_place_of_the_typed_materials(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home, material_budget=D("10"))
        materials.add_material(alice, task, make_article(alice, alice_home, "Paint", "20"), D("2"))
        done_once(alice, task)
        assert "Estimated at €40.00 each time" in self.page(signed_in, alice_home, task)

    def test_the_stock_alone_is_an_estimate(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home)
        materials.add_material(alice, task, make_article(alice, alice_home, "Paint", "20"), D("2"))
        done_once(alice, task)
        assert "Estimated at €40.00 each time" in self.page(signed_in, alice_home, task)

    def test_nothing_typed_and_nothing_priced_says_no_estimate(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home)
        materials.add_material(alice, task, make_article(alice, alice_home, "Brush"), D("1"))
        done_once(alice, task)
        assert "Estimated at" not in self.page(signed_in, alice_home, task)


class TestPaidByNature:
    def test_the_real_cost_is_shared_as_the_estimate_with_the_stock_in_the_materials(
        self, alice: User, alice_home: Home
    ) -> None:
        # Estimate 30 = 20 of stock (materials, the 10 typed not counted) + 10 of labour.
        task = make_task(alice, alice_home, material_budget=D("10"), labour_budget=D("10"))
        materials.add_material(alice, task, make_article(alice, alice_home, "Paint", "20"), D("1"))
        done_once(alice, task, "80")
        natures = {
            line.label: line.amounts
            for line in budget.by_nature(budget.load(alice_home, TODAY), 2026)
        }
        # 80 paid, shared 20/30 and 10/30: 53.33 in materials, 26.67 in labour.
        assert natures["material"].paid == D("53.33")
        assert natures["labour"].paid == D("26.67")
        assert natures["other"].paid == D("0")


class TestOnTheScreens:
    def test_the_page_of_a_task_shows_the_stock_apart_and_the_items_without_price(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home, material_budget=D("10"))
        materials.add_material(alice, task, make_article(alice, alice_home, "Paint", "20"), D("2"))
        materials.add_material(alice, task, make_article(alice, alice_home, "Brush"), D("1"))
        materials.add_material(alice, task, make_article(alice, alice_home, "Roller"), D("1"))
        page = signed_in.get(
            reverse("tasks:detail", kwargs={"home_id": alice_home.id, "task_id": task.id})
        ).content.decode()
        assert "€40.00" in page  # 2 x 20, the 10 typed not counted
        assert "Materials, from the stock" in page
        assert "€50.00" not in page
        assert "2 stock items without a price are not counted" in page

    def test_one_item_without_price_is_said_in_the_singular(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home, material_budget=D("10"))
        materials.add_material(alice, task, make_article(alice, alice_home, "Brush"), D("1"))
        page = signed_in.get(
            reverse("tasks:detail", kwargs={"home_id": alice_home.id, "task_id": task.id})
        ).content.decode()
        assert "1 stock item without a price is not counted" in page
        assert "Materials, from the stock" not in page
        assert "€10.00" in page  # no priced stock item: the budget typed counts

    def test_the_budget_of_the_upkeep_shows_the_part_of_the_stock(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home, material_budget=D("10"))
        materials.add_material(alice, task, make_article(alice, alice_home, "Paint", "20"), D("1"))
        materials.add_material(alice, task, make_article(alice, alice_home, "Brush"), D("1"))
        page = signed_in.get(
            reverse("budget:tasks", kwargs={"home_id": alice_home.id}) + "?year=2026"
        ).content.decode()
        assert "materials from the stock: €20.00" in page
        assert "1 stock item without a price" in page

    def test_the_items_without_price_are_said_even_without_an_estimate(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home)
        materials.add_material(alice, task, make_article(alice, alice_home, "Brush"), D("1"))
        done_once(alice, task, "12")  # listed for what was paid, with no estimate
        page = signed_in.get(
            reverse("budget:tasks", kwargs={"home_id": alice_home.id}) + "?year=2026"
        ).content.decode()
        assert "Repaint" in page
        assert "1 stock item without a price" in page

    def test_the_page_of_an_item_shows_its_unit_price(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        priced = make_article(alice, alice_home, "Paint", "20")
        bare = make_article(alice, alice_home, "Brush")
        page = signed_in.get(stock_url("detail", alice_home, article_id=priced.id)).content.decode()
        assert "Unit price" in page
        assert "€20.00" in page
        page = signed_in.get(stock_url("detail", alice_home, article_id=bare.id)).content.decode()
        assert "Unit price" not in page

    def test_the_form_of_an_item_takes_the_price(self, signed_in: Client, alice_home: Home) -> None:
        response = signed_in.post(
            stock_url("create", alice_home), {"name": "Paint", "unit_price": "21.5"}
        )
        assert response.status_code == 302
        assert Article.objects.get(name="Paint").unit_price == D("21.5")
        refused = signed_in.post(
            stock_url("create", alice_home), {"name": "Odd", "unit_price": "-3"}
        )
        assert refused.status_code == 200
        assert not Article.objects.filter(name="Odd").exists()

    def test_the_csv_of_the_upkeep_has_the_stock_columns(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home)
        materials.add_material(alice, task, make_article(alice, alice_home, "Paint", "20"), D("1"))
        materials.add_material(alice, task, make_article(alice, alice_home, "Brush"), D("1"))
        body = signed_in.get(
            reverse("budget:tasks", kwargs={"home_id": alice_home.id}) + "?year=2026&export=csv"
        ).content.decode()
        head, row = body.splitlines()[:2]
        assert "Of which stock" in head
        assert "Stock items without a price" in head
        assert ",20.00,1," in row

    def test_a_reader_sees_the_price_and_cannot_change_it(
        self, client: Client, reader: User, alice_home: Home, alice: User
    ) -> None:
        article = make_article(alice, alice_home, "Paint", "20")
        client.force_login(reader)
        page = client.get(stock_url("detail", alice_home, article_id=article.id)).content.decode()
        assert "€20.00" in page
        edit_url = stock_url("edit", alice_home, article_id=article.id)
        assert client.post(edit_url, {"name": "Paint", "unit_price": "1"}).status_code == 403
        assert Article.objects.get(id=article.id).unit_price == D("20")

    def test_an_item_of_another_home_is_a_404(
        self, signed_in: Client, alice_home: Home, foreign_article: Article
    ) -> None:
        address = stock_url("edit", alice_home, article_id=foreign_article.id)
        assert signed_in.post(address, {"name": "x", "unit_price": "1"}).status_code == 404
        assert Article.objects.get(id=foreign_article.id).unit_price is None


class TestApi:
    @pytest.fixture
    def client_of_alice(self, api_client: APIClient, alice: User) -> APIClient:
        api_client.force_authenticate(alice)
        return api_client

    def test_read_and_write(self, client_of_alice: APIClient, alice_home: Home) -> None:
        created = client_of_alice.post(
            api("article-list", alice_home), {"name": "Paint", "unit_price": "21.50"}
        )
        assert created.status_code == 201, created.content
        assert created.json()["unit_price"] == "21.50"
        address = api("article-detail", alice_home, article_id=created.json()["id"])
        assert client_of_alice.get(address).json()["unit_price"] == "21.50"
        patched = client_of_alice.patch(address, {"unit_price": "19"}, format="json")
        assert patched.json()["unit_price"] == "19.00"
        emptied = client_of_alice.patch(address, {"unit_price": None}, format="json")
        assert emptied.status_code == 200
        assert emptied.json()["unit_price"] is None

    def test_an_item_without_price_says_null(
        self, client_of_alice: APIClient, alice_home: Home
    ) -> None:
        body = client_of_alice.post(api("article-list", alice_home), {"name": "Brush"}).json()
        assert body["unit_price"] is None

    @pytest.mark.parametrize("price", ["-1", "abc", "1.234", "100000000"])
    def test_a_wrong_price_is_refused(
        self, client_of_alice: APIClient, alice_home: Home, price: str
    ) -> None:
        response = client_of_alice.post(
            api("article-list", alice_home), {"name": "Paint", "unit_price": price}
        )
        assert response.status_code == 400
        assert "unit_price" in str(response.json())
        assert not Article.objects.filter(name="Paint").exists()

    def test_a_supplier_price_fills_it_through_the_api(
        self, client_of_alice: APIClient, alice_home: Home, alice: User
    ) -> None:
        article = make_article(alice, alice_home, "Paint")
        response = client_of_alice.post(
            api("supplier-list", alice_home),
            {"article": str(article.id), "name": "Shop", "last_price": "8.40"},
        )
        assert response.status_code == 201
        detail = api("article-detail", alice_home, article_id=article.id)
        assert client_of_alice.get(detail).json()["unit_price"] == "8.40"

    def test_a_reader_reads_and_writes_nothing(
        self, api_client: APIClient, reader: User, alice_home: Home, alice: User
    ) -> None:
        article = make_article(alice, alice_home, "Paint", "20")
        api_client.force_authenticate(reader)
        address = api("article-detail", alice_home, article_id=article.id)
        assert api_client.get(address).json()["unit_price"] == "20.00"
        assert api_client.patch(address, {"unit_price": "1"}, format="json").status_code == 403
        assert Article.objects.get(id=article.id).unit_price == D("20")

    def test_another_home_is_a_404(
        self, api_client: APIClient, bob: User, alice_home: Home, alice: User
    ) -> None:
        article = make_article(alice, alice_home, "Paint", "20")
        api_client.force_authenticate(bob)
        address = api("article-detail", alice_home, article_id=article.id)
        assert api_client.get(address).status_code == 404
        assert api_client.patch(address, {"unit_price": "1"}, format="json").status_code == 404
        assert Article.objects.get(id=article.id).unit_price == D("20")

    def test_the_budget_api_gives_the_part_of_the_stock(
        self, client_of_alice: APIClient, alice: User, alice_home: Home
    ) -> None:
        task = make_task(alice, alice_home, material_budget=D("10"))
        materials.add_material(alice, task, make_article(alice, alice_home, "Paint", "20"), D("1"))
        materials.add_material(alice, task, make_article(alice, alice_home, "Brush"), D("1"))
        body = client_of_alice.get(api("budget-tasks", alice_home), {"year": 2026}).json()
        (line,) = body["lines"]
        assert (line["estimate"], line["stock_estimate"], line["unpriced_materials"]) == (
            "20.00",
            "20.00",
            1,
        )


class TestArchive:
    def test_the_price_goes_into_the_export_and_back(
        self, client: Client, alice: User, alice_home: Home
    ) -> None:
        article = make_article(alice, alice_home, "Paint", "21.50")
        make_article(alice, alice_home, "Brush")
        data = export_data(archive_bytes(alice_home))
        prices = {row["name"]: row["unit_price"] for row in data["stock_items"]}
        assert prices == {"Paint": "21.50", "Brush": None}
        assert article.id

    def test_an_archive_without_the_field_restores_with_no_price(
        self,
        media_root: Path,
        alice: User,
        alice_home: Home,
        make_user: Callable[..., User],
        add_member: Callable[..., Any],
    ) -> None:
        member = make_user("member@example.com")
        add_member(alice_home, member, Role.MEMBER)
        fill_home(alice, member, alice_home)
        files = entries(archive_bytes(alice_home))
        assert Article.objects.get(home=alice_home, name="Filter").unit_price == D("3.40")

        def drop(document: dict[str, Any]) -> None:
            for row in document["sections"]["stock_items"]:
                del row["unit_price"]

        old = rebuild(edit(files, "stock", drop))
        data_services.erase_home(alice_home)
        restore.restore_bytes(alice, old, match_people=True)
        assert Article.objects.get(home=alice_home, name="Filter").unit_price is None

    def test_a_filled_archive_gives_the_price_back(
        self,
        media_root: Path,
        alice: User,
        alice_home: Home,
        make_user: Callable[..., User],
        add_member: Callable[..., Any],
    ) -> None:
        member = make_user("member@example.com")
        add_member(alice_home, member, Role.MEMBER)
        fill_home(alice, member, alice_home)
        archive = archive_bytes(alice_home)
        data_services.erase_home(alice_home)
        assert not Article.objects.filter(home=alice_home).exists()
        restore.restore_bytes(alice, archive, match_people=True)
        assert Article.objects.get(home=alice_home, name="Filter").unit_price == D("3.40")

    def test_a_wrong_price_in_an_archive_is_refused_and_writes_nothing(
        self, media_root: Path, alice: User, alice_home: Home
    ) -> None:
        make_article(alice, alice_home, "Paint", "5")
        files = entries(archive_bytes(alice_home))

        def corrupt(document: dict[str, Any]) -> None:
            document["sections"]["stock_items"][0]["unit_price"] = "-4.00"

        bad = rebuild(edit(files, "stock", corrupt))
        data_services.erase_home(alice_home)
        with pytest.raises(restore.ArchiveRefusedError):
            restore.restore_bytes(alice, bad)
        assert not Article.objects.filter(home=alice_home).exists()

    def test_erasing_the_home_erases_the_prices(self, alice: User, alice_home: Home) -> None:
        make_article(alice, alice_home, "Paint", "5")
        data_services.erase_home(alice_home)
        assert not Article.all_objects.filter(home_id=alice_home.id).exists()


class TestTheCommaInFrench:
    @pytest.fixture(autouse=True)
    def _restore_the_language(self) -> Iterator[None]:
        """A request in French activates it for the thread: put the language back after."""
        with translation.override("en"):
            yield

    def test_the_form_takes_a_comma_in_french_and_says_the_format_in_english(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        french = signed_in.post(
            stock_url("create", alice_home),
            {"name": "Peinture", "unit_price": "21,5"},
            headers={"Accept-Language": "fr"},
        )
        assert french.status_code == 302
        assert Article.objects.get(name="Peinture").unit_price == D("21.5")
        english = signed_in.post(
            stock_url("create", alice_home),
            {"name": "Paint", "unit_price": "21,5"},
            headers={"Accept-Language": "en"},
        )
        assert english.status_code == 200
        assert "decimal mark" in english.content.decode()
        assert not Article.objects.filter(name="Paint").exists()

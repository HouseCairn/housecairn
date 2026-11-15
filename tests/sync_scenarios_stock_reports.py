"""Scenarios of the guard of the change log for the apps `stock` and `reports` (lot M3d).

Each scenario calls the code the application runs for that write. A row it needs before the
write is made straight in the database, so that only the code under test records a change.
`tests/test_sync_guard.py` loads every `sync_scenarios_*.py` of this folder.
"""

from collections.abc import Callable
from decimal import Decimal

from housecairn.accounts.models import User
from housecairn.homes.models import Home, Role
from housecairn.reports import services as report_services
from housecairn.reports.models import Report
from housecairn.rooms.models import Item, Room
from housecairn.stock import services
from housecairn.stock.models import Article, Movement, Supplier

Scenario = Callable[[Home], None]

OWNER_ONLY = ("stock", "reports")
ARTICLE = Article._meta.label
ROOMS = Article.rooms.through._meta.label
ITEMS = Article.compatible_items.through._meta.label


def owner_of(home: Home) -> User:
    membership = home.memberships.filter(role=Role.OWNER).select_related("user").first()  # type: ignore[attr-defined]
    assert membership is not None
    user: User = membership.user
    return user


# Rows are made with the ORM, not with the services: the change that the guard looks for is the
# one of the write under test.


def article_in(home: Home, name: str = "Filter") -> Article:
    return Article.objects.create(home=home, name=name)


def room_in(home: Home) -> Room:
    return Room.objects.create(home=home, name="Hall")


def item_in(home: Home) -> Item:
    return Item.objects.create(home=home, room=room_in(home), name="Boiler")


def report_in(home: Home) -> Report:
    return Report.objects.create(home=home, reporter=owner_of(home), title="The tap drips")


def _create_article(home: Home) -> None:
    services.create_article(owner_of(home), home, name="Filter", unit_price=Decimal("3"))


def _delete_article(home: Home) -> None:
    services.delete_article(owner_of(home), article_in(home))


def _create_supplier(home: Home) -> None:
    services.create_supplier(owner_of(home), article_in(home), name="Shop")


def _delete_supplier(home: Home) -> None:
    supplier = Supplier.objects.create(home=home, article=article_in(home), name="Shop")
    services.delete_supplier(owner_of(home), supplier)


def _record_movement(home: Home) -> None:
    services.record_movement(owner_of(home), article_in(home), kind="entry", quantity=Decimal("2"))


def _delete_movements(home: Home) -> None:
    article = article_in(home)
    Movement.objects.create(
        home=home, article=article, kind="entry", quantity=Decimal("1"), delta=Decimal("1")
    )
    services.delete_article(owner_of(home), article)


def _add_rooms(home: Home) -> None:
    services.update_article(owner_of(home), article_in(home), rooms=[room_in(home)])


def _remove_rooms(home: Home) -> None:
    article = article_in(home)
    article.rooms.add(room_in(home))
    services.update_article(owner_of(home), article, rooms=[])


def _add_items(home: Home) -> None:
    services.update_article(owner_of(home), article_in(home), compatible_items=[item_in(home)])


def _remove_items(home: Home) -> None:
    article = article_in(home)
    article.compatible_items.add(item_in(home))
    services.update_article(owner_of(home), article, compatible_items=[])


def _create_report(home: Home) -> None:
    report_services.create_report(owner_of(home), home, title="The tap drips")


def _report_updates(home: Home) -> None:
    report = report_in(home)
    report_services.make_task(owner_of(home), report)
    report_services.close_report(owner_of(home), report, resolution="resolved")
    report_services.reopen_report(owner_of(home), report)


SCENARIOS: dict[tuple[str, str], Scenario] = {
    (ARTICLE, "save"): _create_article,
    (ARTICLE, "soft_delete"): _delete_article,
    ("stock.Supplier", "save"): _create_supplier,
    ("stock.Supplier", "soft_delete"): _delete_supplier,
    ("stock.Movement", "save"): _record_movement,
    ("stock.Movement", "soft_delete"): _delete_movements,
    (ROOMS, "save"): _add_rooms,
    (ROOMS, "hard_delete"): _remove_rooms,
    (ITEMS, "save"): _add_items,
    (ITEMS, "hard_delete"): _remove_items,
    ("reports.Report", "save"): _create_report,
    ("reports.Report", "queryset_update"): _report_updates,
}

_HARD = (
    "leaves only by the retention purge and the erasure of a home, which remove its clocks "
    "(housecairn.stock.cascades)"
)
NOT_APPLICABLE: dict[tuple[str, str], str] = {
    (ARTICLE, "queryset_update"): "no code updates stock items by queryset",
    (ARTICLE, "hard_delete"): f"a stock item {_HARD}",
    ("stock.Supplier", "queryset_update"): "no code updates suppliers by queryset",
    ("stock.Supplier", "hard_delete"): f"a supplier {_HARD}",
    (
        "stock.Movement",
        "queryset_update",
    ): "the ledger is never edited, a mistake is a new line",
    ("stock.Movement", "hard_delete"): f"a movement {_HARD}",
    (ROOMS, "queryset_update"): "a link row has no field to update",
    (ITEMS, "queryset_update"): "a link row has no field to update",
    ("reports.Report", "soft_delete"): "a report is closed, never deleted",
    ("reports.Report", "hard_delete"): f"a report {_HARD}",
}

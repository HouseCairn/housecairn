"""The real cost of a done occurrence (docs/adr/0090): the one rule, read everywhere.

The real cost is the value of the stock the occurrence used plus the amount typed for the rest
(labour, purchases outside the stock). The value of the stock is, for each live draw of the
occurrence, the quantity taken times the unit price of the article when it was taken
(`MaterialDraw.unit_price`), added and rounded once to the cent, half up. A draw whose
article had no price counts for nothing and is counted apart (`unpriced`), so the screen
says so. An undo ends the draws, and their value with them.

The sums are made in Python, in `Decimal`, never by SQL `SUM`, which SQLite computes in
floating point. The task page, the Budget, the History, the files and the API all read the
figures from here.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from django.db import models

from housecairn.tasks.models import MaterialDraw, Occurrence, Status

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
CHUNK = 500


@dataclass(frozen=True)
class StockUsed:
    """The stock a done occurrence used, at the prices of when it was taken."""

    amount: Decimal = ZERO
    unpriced: int = 0  # draws whose article had no unit price: counted for nothing


NOTHING = StockUsed()


def _draws(**filters: object) -> Iterable[tuple[UUID, Decimal, Decimal | None]]:
    rows: Iterable[tuple[UUID, Decimal, Decimal | None]] = MaterialDraw.objects.filter(
        occurrence__status=Status.DONE, occurrence__deleted_at__isnull=True, **filters
    ).values_list("occurrence_id", "drawn", "unit_price")
    return rows


def stock_used(occurrence_ids: Iterable[UUID] | models.QuerySet[Any]) -> dict[UUID, StockUsed]:
    """The stock used by each done occurrence of `occurrence_ids`, in one query.

    `occurrence_ids` may be a queryset of ids (`values("id")`), read as a subquery. An
    occurrence that used nothing is not in the result: read it with `NOTHING` as default.
    """
    if isinstance(occurrence_ids, models.QuerySet):
        return _add_up(_draws(occurrence_id__in=occurrence_ids))
    wanted = list(occurrence_ids)
    found: dict[UUID, StockUsed] = {}
    for start in range(0, len(wanted), CHUNK):  # within the parameters a database takes
        found.update(_add_up(_draws(occurrence_id__in=wanted[start : start + CHUNK])))
    return found


def stock_used_in(home_id: UUID) -> dict[UUID, StockUsed]:
    """The stock used by every done occurrence of a home, in one query (the Budget)."""
    return _add_up(_draws(home_id=home_id))


def _add_up(rows: Iterable[tuple[UUID, Decimal, Decimal | None]]) -> dict[UUID, StockUsed]:
    sums: dict[UUID, Decimal] = {}
    unpriced: dict[UUID, int] = {}
    for occurrence_id, drawn, price in rows:
        sums.setdefault(occurrence_id, ZERO)
        if price is None:
            unpriced[occurrence_id] = unpriced.get(occurrence_id, 0) + 1
        else:
            sums[occurrence_id] += drawn * price
    return {
        pk: StockUsed(total.quantize(CENT, rounding=ROUND_HALF_UP), unpriced.get(pk, 0))
        for pk, total in sums.items()
    }


def from_draws(occurrence: Occurrence, draws: Iterable[MaterialDraw]) -> StockUsed:
    """The stock used by an occurrence, from its live draws already loaded (a prefetch)."""
    if occurrence.status != Status.DONE:
        return NOTHING
    rows = ((occurrence.pk, d.drawn, d.unit_price) for d in draws if d.deleted_at is None)
    return _add_up(rows).get(occurrence.pk, NOTHING)


def unpriced_draws_of(task_id: UUID) -> int:
    """The draws of the done occurrences of a task taken without a unit price."""
    return MaterialDraw.objects.filter(
        occurrence__task_id=task_id,
        occurrence__status=Status.DONE,
        occurrence__deleted_at__isnull=True,
        unit_price__isnull=True,
    ).count()


def stock_used_by(occurrence: Occurrence) -> StockUsed:
    return stock_used([occurrence.pk]).get(occurrence.pk, NOTHING)


def real_cost(typed: Decimal | None, stock: Decimal) -> Decimal | None:
    """The real cost: the stock used plus the amount typed; None when neither says anything.

    A done occurrence with no amount typed and no stock of value is "done without a cost",
    as before; with stock of value, its cost is known without typing anything.
    """
    if typed is None and not stock:
        return None
    return (typed or ZERO) + stock


@dataclass(frozen=True)
class OccurrenceCost:
    """The real cost of one occurrence, its two parts apart."""

    typed: Decimal | None
    stock: StockUsed

    @property
    def total(self) -> Decimal | None:
        return real_cost(self.typed, self.stock.amount)


def cost_of(occurrence: Occurrence) -> OccurrenceCost:
    return OccurrenceCost(occurrence.actual_cost, stock_used_by(occurrence))


def costs_of(occurrences: Iterable[Occurrence]) -> dict[UUID, OccurrenceCost]:
    """The real cost of each occurrence, in one query for the stock."""
    rows = list(occurrences)
    used = stock_used(o.pk for o in rows)
    return {o.pk: OccurrenceCost(o.actual_cost, used.get(o.pk, NOTHING)) for o in rows}


def attach(occurrences: Iterable[Occurrence]) -> list[Occurrence]:
    """The occurrences, each with its real cost as `cost` (`OccurrenceCost`), for a list."""
    rows = list(occurrences)
    found = costs_of(rows)
    for row in rows:
        row.cost = found[row.pk]  # type: ignore[attr-defined]
    return rows

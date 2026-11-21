"""One table of a home as a CSV file, for a spreadsheet (docs/adr/0048).

UTF-8 with a byte order mark, so that Excel reads the accents; the columns are the fields
of the section of the archive, a person is named by their display name and never by
their address, an amount is a number with a point and the currency of the home is a
column of its own. A cell of text that a spreadsheet would run as a formula (it starts
with `=`, `+`, `-`, `@`, a tab or a carriage return) is written with a leading `'`, the
neutralisation OWASP recommends; numbers are left as they are.

Each app adds its tables to `ENTITIES` (`register`); the routes take any name listed there.
"""

import csv
import io
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from housecairn.accounts.names import person_name
from housecairn.homes import exports
from housecairn.homes.models import Home

FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
BOM = "\ufeff"


@dataclass(frozen=True)
class CsvEntity:
    """A CSV file: the rows of one section of the archive, `money` its amounts.

    `extra` adds columns worked out from the rows, after their fields: their names, and a
    function that gives the cells of every row by its identifier, in one go (the real cost of
    the history, docs/adr/0090).
    """

    name: str
    section: str
    money: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    cells: Callable[[list[models.Model]], dict[Any, list[str]]] | None = None


ENTITIES: dict[str, CsvEntity] = {}


def register(entity: CsvEntity) -> None:
    ENTITIES[entity.name] = entity


def neutralise(value: str) -> str:
    """The text of a cell, made inert for a spreadsheet if it would start a formula."""
    if value.startswith(FORMULA_STARTS):
        return "'" + value
    return value


def _section(name: str) -> exports.Section:
    for _app, section in exports.all_sections():
        if section.name == name:
            return section
    raise KeyError(name)  # pragma: no cover - checked by tests/test_csv_export.py


def _columns(section: exports.Section) -> list[models.Field[Any, Any]]:
    return [f for f in section.fields() if not isinstance(f, models.FileField)]


def _cell(f: models.Field[Any, Any], row: models.Model, people: dict[Any, Any]) -> str:
    value = getattr(row, f.attname)
    if exports.person_reference(f):
        person = people.get(value)
        return neutralise(person_name(person)) if person is not None else ""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(f, models.CharField | models.TextField):
        return neutralise(str(value))
    if isinstance(f, models.JSONField):
        return neutralise(json.dumps(value, ensure_ascii=False))
    return str(exports._scalar(value))


def rows(home: Home, entity: CsvEntity) -> Iterator[list[str]]:
    exports.refuse_example(home)
    section = _section(entity.section)
    columns = _columns(section)
    header = [*(f.name for f in columns), *entity.extra]
    if entity.money:
        header.append("currency")
    yield header
    # The rows the archive holds: none whose task, say, is deleted.
    found = exports.collect(home).rows[section.name]
    references = [f.attname for f in columns if exports.person_reference(f)]
    people = get_user_model().objects.in_bulk(
        {getattr(row, name) for row in found for name in references} - {None}
    )
    extra = entity.cells(list(found)) if entity.cells is not None else {}
    for row in found:
        cells = [_cell(f, row, people) for f in columns]
        if entity.extra:
            cells.extend(extra.get(row.pk, [""] * len(entity.extra)))
        if entity.money:
            cells.append(home.currency)
        yield cells


def build_csv(home: Home, entity: CsvEntity) -> tuple[bytes, str]:
    """The CSV of one table of the home, and its file name.

    The example home of the guide is refused (docs/adr/0084).
    """
    exports.refuse_example(home)
    out = io.StringIO()
    out.write(BOM)
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerows(rows(home, entity))
    stamp = timezone.now().strftime("%Y%m%d")
    name = f"housecairn-{slugify(home.name) or 'home'}-{entity.name}-{stamp}.csv"
    return out.getvalue().encode("utf-8"), name


for _entity in (
    CsvEntity("rooms", "rooms"),
    CsvEntity("items", "items"),
    CsvEntity("tasks", "tasks", money=("labour_budget", "material_budget")),
    # "history" is registered by `tasks.exports`, with the columns of the real cost.
    CsvEntity("stock", "stock_items"),
    CsvEntity("contractors", "contractors"),
    CsvEntity("reports", "reports"),
):
    register(_entity)

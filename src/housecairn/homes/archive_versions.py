"""What an archive of an earlier release lacks, and the value a restore gives it (docs/adr/0048).

The export writes every concrete field of every section, so a field added to a model is
in the archives made after it, and missing from those made before. A field added without
a new `exports.FORMAT_VERSION` is optional at restore and in the published schema: an
archive of that version made by an earlier release lacks it (docs/administration/data.md).

`ADDED` lists, per version of the format, the fields an archive of that version may lack,
section by section, each with what the migration that added the field gave to the rows
already there: a restore of an archive of before then holds what an upgrade of the
instance that made it would hold. A field absent from an archive and not listed for its
version is refused, as any value that is not valid. A new field of a section goes into
the table of the current version, and of every older one, in the same pull request as its
migration; `tests/test_restore_versions.py` restores a real archive of each release.
"""

from collections.abc import Callable
from functools import cached_property
from typing import Any

Objects = dict[str, list[dict[str, Any]]]


def _key(value: Any) -> str | None:
    """An identifier the fills may look up: a text, and nothing else.

    The archive is not checked yet when it is filled: any other value is left out, and the
    row that holds it is refused when it is checked, as any value that is not valid.
    """
    return value if isinstance(value, str) else None


class Rows:
    """The objects of an archive, with what the fills read of them, worked out once."""

    def __init__(self, objects: Objects) -> None:
        self.objects = objects

    def by_id(self, section: str) -> dict[str, dict[str, Any]]:
        keyed = ((_key(row.get("id")), row) for row in self.objects.get(section, []))
        return {key: row for key, row in keyed if key is not None}

    def get(self, section: str, value: Any) -> dict[str, Any] | None:
        """The row of `section` whose identifier is `value`, if it is a text."""
        key = _key(value)
        return None if key is None else self.by_section[section].get(key)

    @cached_property
    def by_section(self) -> dict[str, dict[str, dict[str, Any]]]:
        return {"stock_items": self.stock_items, "tasks": self.tasks}

    @cached_property
    def stock_items(self) -> dict[str, dict[str, Any]]:
        return self.by_id("stock_items")

    @cached_property
    def tasks(self) -> dict[str, dict[str, Any]]:
        return self.by_id("tasks")

    @cached_property
    def in_a_project(self) -> set[str]:
        """The tasks of a project; an archive holds only live rows, so a live project."""
        tasks = (_key(link.get("task")) for link in self.objects.get("project_tasks", []))
        return {task for task in tasks if task is not None}

    @cached_property
    def priced_from_stock(self) -> set[str]:
        """The tasks whose materials the stock works out: a material of a priced stock item.

        Read once the stock items have their own fills: their unit price comes first.
        """
        tasks = (
            _key(link.get("task"))
            for link in self.objects.get("task_materials", [])
            if (self.get("stock_items", link.get("article")) or {}).get("unit_price") is not None
        )
        return {task for task in tasks if task is not None}


# The JSON value of a missing field, from its row and the other objects of the archive.
Fill = Callable[[dict[str, Any], Rows], Any]


def _constant(value: Any) -> Fill:
    """The default of the field, given to the rows already there (`dict` makes a new one)."""

    def fill(row: dict[str, Any], rows: Rows) -> Any:
        return value() if callable(value) else value

    return fill


def _draw_price(row: dict[str, Any], rows: Rows) -> Any:
    """tasks 0013: a draw of before gets the unit price its stock item has (docs/adr/0090)."""
    article = rows.get("stock_items", row.get("article"))
    return None if article is None else article.get("unit_price")


def _planned(name: str) -> Fill:
    """tasks 0014: a done occurrence of before keeps the plan its task has (docs/adr/0091).

    The labour planned, and the material budget when the stock does not work the materials
    out; nothing for a task of a project, whose money is in its budget lines, nor for an
    occurrence that is not done.
    """

    def fill(row: dict[str, Any], rows: Rows) -> Any:
        task_id = row.get("task")
        task = rows.get("tasks", task_id)
        if row.get("status") != "done" or task is None or task_id in rows.in_a_project:
            return None  # a task the archive does not hold is refused when it is checked
        if name == "planned_labour":
            return task.get("labour_budget")
        return None if task_id in rows.priced_from_stock else task.get("material_budget")

    return fill


# Section -> field -> its value, by the release that added the field. The migration named is
# the one whose value the field takes.
_ADDED_IN_4: dict[str, dict[str, Fill]] = {
    # rooms 0006_item_warranty and 0007_ean, in 0.4.0
    "items": {
        "ean": _constant(""),
        "purchased_on": _constant(None),
        "warranty_duration": _constant(None),
        "warranty_duration_unit": _constant("year"),
        "warranty_provider": _constant(""),
        "warranty_provider_name": _constant(""),
        "warranty_starts_on": _constant(None),
    },
    # rooms 0007_ean, in 0.4.0
    "photos": {"attribution": _constant(dict)},
    # stock 0007_ean, in 0.4.0
    "stock_items": {"ean": _constant(""), "label_attribution": _constant(dict)},
    # tasks 0013_unit_price_of_draws, after 0.5.2
    "stock_draws": {"unit_price": _draw_price},
    # tasks 0014_plan_kept_when_done, after 0.5.2
    "occurrences": {
        "planned_labour": _planned("planned_labour"),
        "planned_material": _planned("planned_material"),
    },
}

_ADDED_IN_3: dict[str, dict[str, Fill]] = {
    # stock 0005_article_unit_price, in 0.3.0
    "stock_items": {"unit_price": _constant(None)},
}

_ADDED_IN_2: dict[str, dict[str, Fill]] = {
    # rooms 0005_item_contract_contractor, format 3: `restore._upgrade_v2` names the
    # contractor of an item that had a provider; an item that had none has none.
    "items": {"contract_contractor": _constant(None)},
}

# The order of the fills: the stock items before the tasks, whose fills read their price.
_ORDER = ("items", "photos", "stock_items", "stock_draws", "occurrences")


def _merge(*tables: dict[str, dict[str, Fill]]) -> dict[str, dict[str, Fill]]:
    merged: dict[str, dict[str, Fill]] = {}
    for table in tables:
        for section, fields in table.items():
            merged.setdefault(section, {}).update(fields)
    return {section: merged[section] for section in _ORDER if section in merged}


# Version of the format -> the fields its archives may lack: those added while it was the
# current version, and after.
ADDED: dict[int, dict[str, dict[str, Fill]]] = {
    4: _merge(_ADDED_IN_4),
    3: _merge(_ADDED_IN_3, _ADDED_IN_4),
    2: _merge(_ADDED_IN_2, _ADDED_IN_3, _ADDED_IN_4),
}


def optional(version: int, section: str) -> frozenset[str]:
    """The fields of a section that an archive of `version` may lack."""
    return frozenset(ADDED.get(version, {}).get(section, {}))


def fill_missing(version: int, objects: Objects) -> None:
    """Give every row of the archive the fields its version may lack, where it lacks them.

    Rewritten in memory, before the objects are checked like those of any other archive; a
    field the archive holds is never changed.
    """
    rows = Rows(objects)
    for section, fields in ADDED.get(version, {}).items():
        for row in objects.get(section, []):
            for name, fill in fields.items():
                if name not in row:
                    row[name] = fill(row, rows)

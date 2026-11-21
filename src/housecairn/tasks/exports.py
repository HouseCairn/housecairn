"""Tasks, their history, materials and files in the export of their home (docs/adr/0048).

The files of the attachments go into `files/attachments/`, as stored: images already
stripped of their metadata at upload (docs/adr/0030), documents untouched. Thumbnails are
not exported; a restore makes them again from the image.
"""

from typing import Any

from housecairn.homes import csv_export, exports
from housecairn.tasks import costs, recurrence
from housecairn.tasks.models import (
    Attachment,
    Contractor,
    MaterialDraw,
    Occurrence,
    Task,
    TaskMaterial,
)


def heating_to_custom(row: dict[str, Any], home: Any) -> dict[str, Any]:
    """The heating season of an archive of before: a custom season of the same months.

    The archive holds the months of the task (its window in the hemisphere of the home);
    without them, those of the heating season in the hemisphere of the restored home.
    """
    window = recurrence.legacy_season(str(row.get("season") or ""), home.hemisphere)
    if window is not None:
        row["season"] = recurrence.CUSTOM
        if row.get("season_start_month") is None or row.get("season_end_month") is None:
            row["season_start_month"], row["season_end_month"] = window
    return row


def history_costs(rows: list[Any]) -> dict[Any, list[str]]:
    """The two columns of the real cost of the history file: the stock used, the total.

    The archive keeps what is stored (the amount typed, and the unit price of each draw);
    the spreadsheet gets the figures worked out by the one rule (docs/adr/0090).
    """
    found = costs.costs_of(rows)
    cells = {}
    for pk, cost in found.items():
        total = cost.total
        cells[pk] = [f"{cost.stock.amount:.2f}", "" if total is None else f"{total:.2f}"]
    return cells


def register() -> None:
    csv_export.register(
        csv_export.CsvEntity(
            "history",
            "occurrences",
            money=("actual_cost", "stock_cost", "real_cost"),
            extra=("stock_cost", "real_cost"),
            cells=history_costs,
        )
    )
    exports.register(
        "tasks",
        exports.Section("contractors", Contractor),
        exports.Section("tasks", Task, on_restore=heating_to_custom),
        exports.Section("task_materials", TaskMaterial),
        exports.Section("occurrences", Occurrence),
        exports.Section("stock_draws", MaterialDraw),
        exports.Section(
            "attachments",
            Attachment,
            files=(exports.FileSpec("file", "attachments", thumbnail="thumbnail", documents=True),),
        ),
    )

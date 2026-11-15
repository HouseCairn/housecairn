"""Projects in the export of their home (docs/adr/0050, 0048).

Amounts are text with a point, never a float. The photos, the pictures of links and the
files of quotes go into `files/projects/`, as stored. What is committed on a budget line
is not a field: it is the sum of its accepted quotes.
"""

from typing import Any

from housecairn.homes import exports
from housecairn.projects import changelog
from housecairn.projects.models import (
    AnnualEnvelope,
    BudgetLine,
    Project,
    ProjectFile,
    ProjectLink,
    ProjectTask,
    Quote,
)
from housecairn.tasks import recurrence


def heating_to_custom(row: dict[str, Any], home: Any) -> dict[str, Any]:
    """The heating season of an archive of before: custom months, those of the hemisphere.

    A project on a predefined season kept no months: the custom season takes the months
    of the heating season in the hemisphere of the restored home, so it reads the same.
    """
    window = recurrence.legacy_season(str(row.get("season") or ""), home.hemisphere)
    if window is not None:
        row["season"] = recurrence.CUSTOM
        row["season_start_month"], row["season_end_month"] = window
    return row


def register() -> None:
    exports.register(
        "projects",
        exports.Section(
            "projects", Project, on_restore=heating_to_custom, on_written=changelog.restored
        ),
        exports.Section("project_budget_lines", BudgetLine),
        exports.Section("project_quotes", Quote),
        exports.Section(
            "project_files",
            ProjectFile,
            files=(exports.FileSpec("file", "projects", thumbnail="thumbnail", documents=True),),
        ),
        exports.Section(
            "project_links",
            ProjectLink,
            # Where the reading of the page stands is not data of the home (docs/adr/0070).
            exclude=("preview_state", "preview_started_at"),
        ),
        exports.Section("project_tasks", ProjectTask),
        exports.Section(
            "project_envelopes",
            AnnualEnvelope,
            order_by=("year", "id"),
        ),
    )

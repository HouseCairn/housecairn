"""The plan an occurrence keeps when it is marked done (docs/adr/0091).

With no amount typed, the real cost of a done task now counts what its plan said when it was
marked done: the labour planned, and the material budget typed when the stock does not work
the materials out (a stock item with a unit price, ADR 0081). The occurrences done before this
version kept nothing: they get the plan their task has now, by the same rule, an
approximation the release notes say. A task of a project keeps none: its money is in the
budget lines of the project (ADR 0062). Only the done occurrences get one; it counts where no
amount is typed.
"""

import uuid
from typing import Any

import django.core.validators
from django.db import connection, migrations, models


def _tasks_in_a_project(db: Any) -> set[uuid.UUID]:
    """The tasks in a live project, read in SQL: `tasks` does not depend on `projects`.

    A dependency would tie the two apps' migrations together, and `projects` is migrated
    back and forth on its own. Before `projects` has a table, no task is in a project.
    """
    if "projects_projecttask" not in db.introspection.table_names():
        return set()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT link.task_id FROM projects_projecttask link"
            " JOIN projects_project project ON project.id = link.project_id"
            " WHERE link.deleted_at IS NULL AND project.deleted_at IS NULL"
        )
        return {uuid.UUID(str(task_id)) for (task_id,) in cursor.fetchall()}


def keep_the_plan_of_now(apps: Any, schema_editor: Any) -> None:
    Occurrence = apps.get_model("tasks", "Occurrence")
    Task = apps.get_model("tasks", "Task")
    TaskMaterial = apps.get_model("tasks", "TaskMaterial")
    in_project = _tasks_in_a_project(
        connection if schema_editor is None else schema_editor.connection
    )
    from_stock = set(
        TaskMaterial._base_manager.filter(
            deleted_at__isnull=True,
            article__deleted_at__isnull=True,
            article__unit_price__isnull=False,
        ).values_list("task_id", flat=True)
    )
    done = Occurrence._base_manager.filter(status="done").values_list("task_id", flat=True)
    for task_id, labour, material in Task._base_manager.filter(
        pk__in=set(done)
    ).values_list("id", "labour_budget", "material_budget"):
        if task_id in in_project:
            continue
        kept = None if task_id in from_stock else material
        if labour is None and kept is None:
            continue
        Occurrence._base_manager.filter(task_id=task_id, status="done").update(
            planned_labour=labour, planned_material=kept
        )


def _amount(label: str) -> Any:
    return models.DecimalField(
        blank=True,
        decimal_places=2,
        editable=False,
        max_digits=12,
        null=True,
        validators=[django.core.validators.MinValueValidator(0)],
        verbose_name=label,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0013_unit_price_of_draws"),
        ("stock", "0005_article_unit_price"),
    ]

    operations = [
        migrations.AddField(
            model_name="occurrence",
            name="planned_labour",
            field=_amount("labour planned when done"),
        ),
        migrations.AddField(
            model_name="occurrence",
            name="planned_material",
            field=_amount("materials planned when done"),
        ),
        migrations.RunPython(keep_the_plan_of_now, migrations.RunPython.noop),
    ]

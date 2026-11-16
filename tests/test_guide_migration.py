"""`Home.is_example` and the table of ticks come and go with their migrations (docs/adr/0084)."""

import uuid

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("homes", "0007_home_archived_at")]


def columns() -> set[str]:
    with connection.cursor() as cursor:
        return {
            c.name for c in connection.introspection.get_table_description(cursor, "homes_home")
        }


def tables() -> set[str]:
    return set(connection.introspection.table_names())


def test_the_mark_and_the_ticks_are_added_and_taken_away_again() -> None:
    latest = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        assert "is_example" not in columns()
        assert "guide_steptick" not in tables()
        old = MigrationExecutor(connection).loader.project_state(BEFORE).apps
        kept = old.get_model("homes", "Home").objects.create(id=uuid.uuid4(), name="Before")

        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        assert "is_example" in columns()
        assert "guide_steptick" in tables()
        now = MigrationExecutor(connection).loader.project_state(latest).apps
        home = now.get_model("homes", "Home")
        assert home.objects.get(pk=kept.pk).is_example is False  # a home that was there is real
        # The database holds the default too: a row written without the column is real.
        moment = timezone.now()
        raw = uuid.uuid4()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO homes_home (id, created_at, updated_at, name, currency, hemisphere,"
                " required_provider) VALUES (%s, %s, %s, 'Raw', 'EUR', 'north', '')",
                [raw.hex if connection.vendor == "sqlite" else raw, moment, moment],
            )
        assert home.objects.get(pk=raw).is_example is False

        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        assert "is_example" not in columns()
        assert "guide_steptick" not in tables()
        back = MigrationExecutor(connection).loader.project_state(BEFORE).apps
        assert back.get_model("homes", "Home").objects.filter(pk__in=[kept.pk, raw]).count() == 2
    finally:
        call_command("migrate", verbosity=0)

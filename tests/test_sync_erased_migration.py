"""The ids of the connections told of an erased home move from a list to a table (0003)."""

import uuid

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_lists_become_rows_and_come_back() -> None:
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes("sync")
    try:
        executor.migrate([("sync", "0002_push")])
        old = MigrationExecutor(connection).loader.project_state([("sync", "0002_push")]).apps
        erased_home = old.get_model("sync", "ErasedHome")
        first, second, kept = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        home = erased_home.objects.create(
            id=uuid.uuid4(), connection_ids=[str(first), str(second), str(first)]
        )
        empty = erased_home.objects.create(id=uuid.uuid4(), connection_ids=[])
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes("sync"))
        new = MigrationExecutor(connection).loader.project_state(latest).apps
        link = new.get_model("sync", "ErasedHomeConnection")
        assert sorted(
            link.objects.filter(erased_id=home.pk).values_list("connection_id", flat=True)
        ) == sorted([first, second])
        assert not link.objects.filter(erased_id=empty.pk).exists()
        link.objects.create(erased_id=empty.pk, connection_id=kept)
        executor = MigrationExecutor(connection)
        executor.migrate([("sync", "0002_push")])
        back = (
            MigrationExecutor(connection)
            .loader.project_state([("sync", "0002_push")])
            .apps.get_model("sync", "ErasedHome")
        )
        assert back.objects.get(pk=home.pk).connection_ids == sorted([str(first), str(second)])
        assert back.objects.get(pk=empty.pk).connection_ids == [str(kept)]
    finally:
        call_command("migrate", verbosity=0)

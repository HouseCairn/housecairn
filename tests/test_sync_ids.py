"""The deterministic ids of docs/adr/0077 (need 2), lot M3f.

The vectors of `tests/fixtures/sync_ids.json` are those the mobile repository pins: each gives
a home, the inputs, the exact name and the expected id. They are checked here three ways: by
an RFC 4122 version 5 written out by hand (SHA-1 of the namespace bytes and the UTF-8 name),
by `sync.ids`, and by the rows the web writes: the successor of an occurrence, a material line,
a draw, an annual envelope and the rows of the link tables, before and after a restore as a
copy, which names them again from the new ids of their parents (docs/adr/0069).
"""

import hashlib
import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from housecairn.accounts.models import User
from housecairn.homes import restore
from housecairn.homes.models import Home
from housecairn.projects import services as project_services
from housecairn.projects.models import AnnualEnvelope, Project
from housecairn.rooms.models import Item, Room
from housecairn.stock import services as stock_services
from housecairn.stock.models import Article
from housecairn.sync import ids, rows
from housecairn.sync.models import Change, RowClocks
from housecairn.tasks import materials, services
from housecairn.tasks.models import MaterialDraw, Occurrence, Task, TaskMaterial
from tests.data_support import archive_bytes

pytestmark = pytest.mark.django_db
pytest_plugins = ("tests.rooms_fixtures", "tests.tasks_fixtures")

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "sync_ids.json").read_text())


def by_hand(namespace: str, name: str) -> str:
    """RFC 4122 section 4.3, version 5: what the uuid package of the phone computes."""
    # SHA-1 is what RFC 4122 version 5 is made of, not a protection.
    digest = hashlib.sha1(
        uuid.UUID(namespace).bytes + name.encode("utf-8"), usedforsecurity=False
    ).digest()
    raw = bytearray(digest[:16])
    raw[6] = (raw[6] & 0x0F) | 0x50
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def computed(vector: dict[str, Any]) -> uuid.UUID:
    home, given = vector["home"], vector["inputs"]
    row = vector["row"]
    if row == "successor":
        return ids.successor(home, given["occurrence"])
    if row == "material":
        return ids.material(home, given["task"], given["article"])
    if row == "draw":
        return ids.draw(home, given["occurrence"], given["article"])
    if row == "envelope":
        return ids.envelope(home, given["year"])
    assert row == "link"
    return ids.link(home, given["entity"], given["owner"], given["other"])


# ---------- the formula ----------


def test_the_hand_written_version_5_is_that_of_rfc_4122() -> None:
    # The example of the Python documentation, also given by every RFC 4122 implementation.
    assert by_hand(str(uuid.NAMESPACE_DNS), "python.org") == "886313e1-3b8a-5372-9b90-0c9aee199e5d"


def test_the_vectors_cover_every_row_of_the_table_in_two_homes() -> None:
    vectors = VECTORS["vectors"]
    assert {v["row"] for v in vectors} == {"successor", "material", "draw", "envelope", "link"}
    assert {v["inputs"]["entity"] for v in vectors if v["row"] == "link"} == {
        "projects.project_rooms",
        "stock.article_rooms",
        "stock.article_compatible_items",
    }
    assert len({v["home"] for v in vectors}) == 2


@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=lambda v: v["name"][:40])
def test_every_vector_gives_its_id(vector: dict[str, Any]) -> None:
    assert by_hand(vector["home"], vector["name"]) == vector["id"]
    made = computed(vector)
    assert str(made) == vector["id"]
    assert (made.version, made.variant) == (5, uuid.RFC_4122)


def test_the_names_are_those_of_the_table() -> None:
    home, occurrence, article = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert ids.successor(home, occurrence) == uuid.uuid5(home, f"next:{occurrence}")
    assert ids.draw(home, occurrence, article) == uuid.uuid5(home, f"draw:{occurrence}:{article}")
    assert ids.envelope(home, 987) == uuid.uuid5(home, "envelope:0987")
    # Ids are read in their canonical lower-case text, whatever form they are given in.
    assert ids.successor(str(home).upper(), str(occurrence).upper()) == ids.successor(
        home, occurrence
    )


def test_the_same_name_in_two_homes_gives_two_ids() -> None:
    occurrence = uuid.uuid4()
    assert ids.successor(uuid.uuid4(), occurrence) != ids.successor(uuid.uuid4(), occurrence)


def test_a_year_has_four_digits() -> None:
    with pytest.raises(ValueError, match="four decimal digits"):
        ids.envelope(uuid.uuid4(), 10000)


# ---------- the rows the web writes ----------


@pytest.fixture
def article(alice_home: Home) -> Article:
    return Article.objects.create(home=alice_home, name="AA battery")


def test_the_successor_of_an_occurrence_has_its_id(
    alice: User, owner_occurrence: Occurrence, frozen_today: Any
) -> None:
    closing = services.complete(alice, owner_occurrence)
    assert closing.next is not None
    assert closing.next.pk == ids.successor(owner_occurrence.home_id, owner_occurrence.pk)
    put = Change.objects.get(row_id=closing.next.pk)
    assert (put.kind, put.fields["follows_id"]) == ("put", str(owner_occurrence.pk))


def test_a_material_line_has_its_id_and_comes_back_when_listed_again(
    alice: User, owner_task: Task, article: Article
) -> None:
    line = materials.add_material(alice, owner_task, article, Decimal("2"))
    assert line.pk == ids.material(owner_task.home_id, owner_task.pk, article.pk)
    materials.remove_material(alice, line)
    again = materials.add_material(alice, owner_task, article, Decimal("3"))
    assert again.pk == line.pk
    stored = TaskMaterial.all_objects.get(pk=line.pk)
    assert (stored.deleted_at, stored.quantity) == (None, Decimal("3.00"))
    kinds = [(c.kind, c.fields) for c in Change.objects.filter(row_id=line.pk).order_by("pos")]
    assert kinds[-2:] == [("restore", {}), ("put", {"quantity": "3.00"})]
    assert RowClocks.objects.get(row_id=line.pk).tombstoned_at is None


def test_a_draw_has_its_id_and_comes_back_when_closed_again(
    alice: User, owner_task: Task, owner_occurrence: Occurrence, article: Article, frozen_today: Any
) -> None:
    materials.add_material(alice, owner_task, article, Decimal("2"))
    closing = services.complete(alice, owner_occurrence)
    draw = MaterialDraw.objects.get(occurrence=closing.occurrence)
    assert draw.pk == ids.draw(owner_occurrence.home_id, owner_occurrence.pk, article.pk)
    services.undo(alice, closing.occurrence)
    assert MaterialDraw.all_objects.get(pk=draw.pk).deleted_at is not None
    services.complete(alice, Occurrence.objects.get(pk=owner_occurrence.pk))
    assert MaterialDraw.objects.get(occurrence=owner_occurrence).pk == draw.pk
    assert Change.objects.filter(row_id=draw.pk, kind="restore").count() == 1


def test_an_annual_envelope_has_its_id(alice: User, alice_home: Home) -> None:
    envelope = project_services.set_envelope(alice, alice_home, year=2027, amount="5000")
    assert envelope.pk == ids.envelope(alice_home.pk, 2027)
    assert Change.objects.get(row_id=envelope.pk).fields == {"year": 2027, "amount": "5000.00"}
    again = project_services.set_envelope(alice, alice_home, year=2027, amount="6000")
    assert again.pk == envelope.pk
    assert AnnualEnvelope.objects.filter(home=alice_home).count() == 1


def test_the_link_rows_are_named_by_the_table(
    alice: User, alice_home: Home, owner_room: Room, owner_item: Item
) -> None:
    article = stock_services.create_article(
        alice, alice_home, name="Filter", rooms=[owner_room], compatible_items=[owner_item]
    )
    project = project_services.create_project(alice, alice_home, title="Kitchen")
    project_services.update_project(alice, project, rooms=[owner_room])
    for entity, owner, other in [
        ("stock.article_rooms", article.pk, owner_room.pk),
        ("stock.article_compatible_items", article.pk, owner_item.pk),
        ("projects.project_rooms", project.pk, owner_room.pk),
    ]:
        row_id = ids.link(alice_home.pk, entity, owner, other)
        assert Change.objects.filter(entity=entity, row_id=row_id, kind="put").exists(), entity


# ---------- a restore as a copy ----------


def test_a_restore_as_a_copy_names_the_rows_again_from_their_new_parents(
    alice: User,
    alice_home: Home,
    owner_task: Task,
    owner_occurrence: Occurrence,
    owner_room: Room,
    article: Article,
    frozen_today: Any,
    media_root: Path,
) -> None:
    materials.add_material(alice, owner_task, article, Decimal("2"))
    services.complete(alice, owner_occurrence)
    project_services.set_envelope(alice, alice_home, year=2027, amount="5000")
    project = project_services.create_project(alice, alice_home, title="Kitchen")
    project_services.update_project(alice, project, rooms=[owner_room])
    copy = restore.restore_bytes(alice, archive_bytes(alice_home), as_copy=True).home
    task = Task.objects.get(home=copy)
    new_article = Article.objects.get(home=copy)
    first = Occurrence.objects.get(home=copy, follows__isnull=True)
    successor = Occurrence.objects.get(home=copy, follows=first)
    assert first.pk != owner_occurrence.pk
    assert successor.pk == ids.successor(copy.pk, first.pk)
    line = TaskMaterial.objects.get(home=copy)
    assert line.pk == ids.material(copy.pk, task.pk, new_article.pk)
    draw = MaterialDraw.objects.get(home=copy)
    assert draw.pk == ids.draw(copy.pk, first.pk, new_article.pk)
    envelope = AnnualEnvelope.objects.get(home=copy)
    assert envelope.pk == ids.envelope(copy.pk, 2027)
    new_project = Project.objects.get(home=copy)
    new_room = Room.objects.get(home=copy, name=owner_room.name)
    link = ids.link(copy.pk, "projects.project_rooms", new_project.pk, new_room.pk)
    assert Change.objects.filter(home=copy, row_id=link).exists()
    # Every row of the copy is recorded under the id it has.
    for row in (successor, line, draw, envelope):
        assert Change.objects.filter(home=copy, row_id=row.pk, kind="put").exists()


def test_a_chain_of_successors_is_named_from_its_start(
    alice: User, owner_occurrence: Occurrence, frozen_today: Any, media_root: Path
) -> None:
    second = services.complete(alice, owner_occurrence).next
    assert second is not None
    third = services.complete(alice, second).next
    assert third is not None
    home = owner_occurrence.home
    copy = restore.restore_bytes(alice, archive_bytes(home), as_copy=True).home
    first = Occurrence.objects.get(home=copy, follows__isnull=True)
    new_second = Occurrence.objects.get(home=copy, follows=first)
    new_third = Occurrence.objects.get(home=copy, follows=new_second)
    assert new_second.pk == ids.successor(copy.pk, first.pk)
    assert new_third.pk == ids.successor(copy.pk, new_second.pk)


# ---------- one read before a queryset update ----------


def test_a_queryset_update_reads_its_rows_once(alice: User, alice_home: Home) -> None:
    for number in range(5):
        services.create_task(alice, alice_home, title=f"Task {number}")
    table = Task._meta.db_table
    with CaptureQueriesContext(connection) as queries:
        written = rows.update_queryset(Task.objects.filter(home=alice_home), notes="Seen")
    assert written == 5
    reads = [
        q["sql"]
        for q in queries.captured_queries
        if q["sql"].lstrip().upper().startswith("SELECT") and f'FROM "{table}"' in q["sql"]
    ]
    assert len(reads) == 1, reads
    assert Change.objects.filter(entity="tasks.task", fields__notes="Seen").count() == 5

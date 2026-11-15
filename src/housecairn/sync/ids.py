"""The deterministic ids of the rows that have a uniqueness rule (docs/adr/0077, need 2).

The web makes these rows with the ids of mobile ADR 0003, so that the web and a phone that
make the same row (two closings of one occurrence, the same article listed on a task) make it
under one id, whose fields then merge. Each id is a UUID version 5 in the namespace of the home
the row belongs to, from the exact names of the table of ADR 0077: UTF-8, ids in the lower-case
canonical text of a UUID, the year in four decimal digits.

| Row | Name |
| --- | --- |
| successor of an occurrence | `next:<occurrence>` |
| material line of a task | `material:<task>:<article>` |
| draw of a material | `draw:<occurrence>:<article>` |
| annual envelope | `envelope:<year>` |
| row of a link table | `<entity type>:<owner>:<other>` |

`tests/fixtures/sync_ids.json` holds vectors of every row of the table, which the mobile
repository pins. A restore as a copy (docs/adr/0069) computes these ids again from the new ids
of the parents (`homes.restore`).
"""

import uuid
from typing import Any


def _text(value: Any) -> str:
    """An id as the names write it: the lower-case canonical text of a UUID."""
    return str(uuid.UUID(str(value)))


def _id(home: Any, name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.UUID(_text(home)), name)


def successor(home: Any, occurrence: Any) -> uuid.UUID:
    """The occurrence that follows `occurrence` (`_spawn_next`)."""
    return _id(home, "next:" + _text(occurrence))


def material(home: Any, task: Any, article: Any) -> uuid.UUID:
    """The material line of `article` on `task`."""
    return _id(home, "material:" + _text(task) + ":" + _text(article))


def draw(home: Any, occurrence: Any, article: Any) -> uuid.UUID:
    """What the closing of `occurrence` took of `article`."""
    return _id(home, "draw:" + _text(occurrence) + ":" + _text(article))


def envelope(home: Any, year: int) -> uuid.UUID:
    """The annual envelope of `year`."""
    if not 0 <= year <= 9999:
        raise ValueError("A year of an envelope has four decimal digits.")
    return _id(home, f"envelope:{year:04d}")


def link(home: Any, entity: str, owner: Any, other: Any) -> uuid.UUID:
    """The row of the link table `entity` that links `owner` to `other`.

    `entity` is the entity type of the link table (`projects.project_rooms`), `owner` the id of
    the row that holds the link (the project), `other` the id of the row it links to (the room).
    """
    return _id(home, entity + ":" + _text(owner) + ":" + _text(other))

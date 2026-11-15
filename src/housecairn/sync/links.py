"""The rows of a link table in the change log: their entity type and their id (docs/adr/0077).

A link table (`projects.project_rooms`) has no id of its own that a phone could share: its
row is named by the table of need 2 of ADR 0077, `uuid5(home, t + ":" + owner + ":" + other)`,
where `t` is the entity type, `owner` the id of the row that holds the link (the project) and
`other` the id of the row it links to (the room), each in the lower-case text of a UUID.
"""

import uuid
from typing import Any

from django.db import models


def entity_of(model: type[models.Model]) -> str:
    """The entity type of the protocol: `<app>.<model>` in lower case."""
    return model._meta.label_lower


def link_row_id(
    home_id: uuid.UUID, through: type[models.Model], owner: Any, other: Any
) -> uuid.UUID:
    """The id of the row of a link table that links `owner` to `other` in a home."""
    name = f"{entity_of(through)}:{uuid.UUID(str(owner))}:{uuid.UUID(str(other))}"
    return uuid.uuid5(uuid.UUID(str(home_id)), name)


def link_fields(relation: models.ManyToManyField[Any, Any]) -> tuple[str, str]:
    """The column names of the two sides of a link table: the owner's, then the other's."""
    through: Any = relation.remote_field.through
    owner = through._meta.get_field(relation.m2m_field_name()).attname
    other = through._meta.get_field(relation.m2m_reverse_field_name()).attname
    return owner, other

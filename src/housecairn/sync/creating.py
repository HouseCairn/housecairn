"""A home created by a phone (lot M5c of docs/adr/0079): `POST /api/v1/sync/homes/`.

The phone made the home, with its own id, before it knew the server. Sending it is repeatable:
the same id with the same fields gives the home again; any other use of the id is a conflict,
which says nothing of who holds it (docs/adr/0077).
"""

import uuid
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import QuerySet

from housecairn.accounts.models import User
from housecairn.apitokens.models import AppConnection
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.sync import reading
from housecairn.sync.models import ErasedHome, RowClocks

# The fields of the body, as the web form of a home has them.
FIELDS = ("name", "currency", "hemisphere")


class IdTakenError(Exception):
    """The id is held: by a home with other fields, another home, a row, an erased home."""


@dataclass(frozen=True)
class Created:
    home: Home
    created: bool  # False: the same home, sent again


def _held(home_id: uuid.UUID) -> bool:
    """Whether any row of the server holds the id, deleted or erased ones included."""
    if ErasedHome.objects.filter(pk=home_id).exists():
        return True
    if RowClocks.objects.filter(row_id=home_id).exists():
        return True
    for entity in reading.ENTITIES:
        if reading.is_link(entity):
            continue
        if reading.model_of(entity)._base_manager.filter(pk=home_id).exists():
            return True
    return False


def create(
    user: User,
    connection: AppConnection,
    readable: QuerySet[Home],
    home_id: uuid.UUID,
    fields: dict[str, str],
) -> Created:
    """Create the home `home_id` for the phone, its account the owner, bound to the connection.

    `readable`: the homes this connection reads now. One of them with the same id and fields is
    the same home sent again; any other holder of the id raises `IdTakenError`. The creation
    writes the home's first change in its log (`create_home`), which `changes` and `snapshot`
    then give.
    """
    with transaction.atomic():
        # One creation of a connection at a time: the same home sent twice at once is made once.
        AppConnection.objects.select_for_update().only("pk").get(pk=connection.pk)
        existing = readable.filter(pk=home_id).first()
        if existing is not None:
            if all(getattr(existing, name) == fields[name] for name in FIELDS):
                return Created(existing, created=False)
            raise IdTakenError
        if _held(home_id):
            raise IdTakenError
        try:
            with transaction.atomic():
                home = home_services.create_home(user, home_id=home_id, **fields)
        except IntegrityError:
            # Taken meanwhile by another connection: a conflict like any other.
            raise IdTakenError from None
        except ValidationError as error:
            # The same, seen first by the check of the model (a live home holds the id).
            if "id" in getattr(error, "error_dict", {}):
                raise IdTakenError from None
            raise
        connection.homes.add(home)
    return Created(home, created=True)

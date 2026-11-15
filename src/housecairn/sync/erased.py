"""Erased homes: the one row that outlives a home (docs/adr/0077, 'A home that was erased')."""

import uuid

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from housecairn.sync.models import ErasedHome


def connection_ids_of(home_id: uuid.UUID) -> list[str]:
    """The ids of the connected apps (ADR 0078) bound to a home, as text.

    None while the instance has no connected apps; the model arrives with the OAuth lot.
    """
    try:
        model = apps.get_model("apitokens", "AppConnection")
    except LookupError:
        return []
    return sorted(str(pk) for pk in model.all_objects.filter(homes=home_id).values_list("pk", flat=True))


def record_erased(home_id: uuid.UUID) -> ErasedHome:
    """Write the row of an erased home, with the connections that were bound to it.

    Called in the transaction that erases the home, before its rows go, so that both happen or
    neither does.
    """
    with transaction.atomic():
        erased, _created = ErasedHome.objects.update_or_create(
            id=home_id,
            defaults={"erased_at": timezone.now(), "connection_ids": connection_ids_of(home_id)},
        )
    return erased

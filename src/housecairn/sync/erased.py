"""Erased homes: the one row that outlives a home (docs/adr/0077, 'A home that was erased')."""

import uuid

from django.db import transaction
from django.utils import timezone

from housecairn.apitokens.models import AppConnection
from housecairn.sync.models import ErasedHome, ErasedHomeConnection


def connection_ids_of(home_id: uuid.UUID) -> list[str]:
    """The ids of the connected apps (ADR 0078) to tell that a home was erased, as text, in order.

    Those bound to the home whose account is still a member of it now: a member removed from the
    home keeps a connection bound to it (it only stops reading it), and must not learn of an
    erasure that came after the removal.
    """
    members = AppConnection.all_objects.filter(
        homes=home_id,
        user__memberships__home_id=home_id,
        user__memberships__deleted_at__isnull=True,
    )
    return sorted({str(pk) for pk in members.values_list("pk", flat=True)})


def record_erased(home_id: uuid.UUID) -> ErasedHome:
    """Write the row of an erased home, with the connections that were bound to it.

    Called in the transaction that erases the home, before its rows go, so that both happen or
    neither does.
    """
    with transaction.atomic():
        erased, _created = ErasedHome.objects.update_or_create(
            id=home_id, defaults={"erased_at": timezone.now()}
        )
        ErasedHomeConnection.objects.bulk_create(
            [
                ErasedHomeConnection(erased=erased, connection_id=uuid.UUID(connection_id))
                for connection_id in connection_ids_of(home_id)
            ],
            ignore_conflicts=True,
        )
    return erased

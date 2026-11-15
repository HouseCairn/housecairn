"""Record a change of a row of a home: the one function every write path calls (docs/adr/0077).

`record_change` is called in the transaction that writes the row. It locks the row of the home,
decides field by field which writes win (last writer wins by clock, then device), and adds one
entry to the change feed with the next position of the home. Positions have no gap and no
repeat: the position is taken under the lock and committed, or rolled back, with the write.
"""

import datetime
import os
import re
import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.utils import timezone

from housecairn.homes.models import Home
from housecairn.sync import clock
from housecairn.sync.models import Change, HomeHead, RowClocks, ServerDevice

PUT = Change.Kind.PUT.value
DELETE = Change.Kind.DELETE.value
RESTORE = Change.Kind.RESTORE.value
# `delete` and `restore` write the field `deleted_at` (mobile protocol, section 2.2).
DELETED_AT = "deleted_at"


class _WriteClock:
    """The value of a field that reads as the clock of its own write (a web archive)."""

    def __repr__(self) -> str:
        return "WRITE_CLOCK"


# Pass it as the value of a field that reads as an instant: the write is then given the clock it
# gets, as 16 hexadecimal digits (docs/adr/0077, need 9: an archive made on the web).
WRITE_CLOCK = _WriteClock()

_ENTITY = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")


@dataclass(frozen=True)
class Recorded:
    """What `record_change` did.

    `change` is the entry added to the feed, or None when every field lost (a repeat, or an
    older write). `lost` maps each field that lost to the clock and the device (base64url) of
    the stamp that won, so that a push can tell the phone who won.
    """

    change: Change | None
    lost: dict[str, tuple[str, str]] = field(default_factory=dict)


def server_device() -> bytes:
    """The 16 bytes the server writes with, made by the first migration of the app.

    A database emptied by hand (the tests flush theirs) gets a new one, rather than failing.
    """
    row, _created = ServerDevice.objects.get_or_create(
        pk=1, defaults={"device": os.urandom(clock.DEVICE_BYTES)}
    )
    return bytes(row.device)


def record_change(
    home: Home | uuid.UUID,
    entity: str,
    row_id: uuid.UUID,
    kind: str = PUT,
    fields: Mapping[str, Any] | None = None,
    *,
    stamp: tuple[str, bytes] | None = None,
    known: Collection[str] | None = None,
    updated_at: datetime.datetime | None = None,
    wall: int | None = None,
) -> Recorded:
    """Record the write of `fields` (or the deletion, or the restoration) of one row.

    - `entity`: `<app>.<model>` in lower case; `row_id`: the id of the row.
    - `fields`: only the fields whose value changed, with their new values (JSON values).
      A `delete` or a `restore` carries none: it writes `deleted_at`.
    - `stamp`: the clock (16 hexadecimal digits) and device (16 bytes) of a write made on a
      phone. Without it the write gets a clock of the server's hybrid clock and the server's
      device id: the server writes as one device.
    - `known`: the fields the row has a column for. Any other field is kept in `extra` with its
      clock, and is merged and given back like the others. None: every field is known.
    - `updated_at`: the `updated_at` of the row before this write. A known field with no clock
      of its own reads as that instant with the server's device id; nothing is migrated.
    - `wall`: the wall clock in milliseconds (tests).
    """
    if kind not in Change.Kind.values:
        raise ValueError(f"Unknown kind of change: {kind!r}.")
    if _ENTITY.fullmatch(entity) is None:
        raise ValueError("An entity type is <app>.<model>, in lower case.")
    if kind != PUT and fields:
        raise ValueError("A deletion or a restoration carries no field.")
    if fields and DELETED_AT in fields:
        raise ValueError("`deleted_at` is written by a deletion or a restoration, not as a field.")
    written: dict[str, Any] = {DELETED_AT: None} if kind != PUT else dict(fields or {})
    if not written:
        raise ValueError("A change writes at least one field.")
    now_ms = clock.wall_ms() if wall is None else wall
    home_id = home.pk if isinstance(home, Home) else home

    with transaction.atomic():
        # The lock of the home's row, held until the commit: the order of the positions is
        # the order of the commits. SQLite writes one transaction at a time.
        Home.all_objects.select_for_update().only("pk").get(pk=home_id)
        head, _created = HomeHead.objects.get_or_create(home_id=home_id)
        last = clock.parse_clock(head.clock)
        server_stamp = (
            (clock.clock_of_instant(updated_at), server_device())
            if updated_at is not None
            else None
        )
        if stamp is None:
            device = server_device()
            # After the `updated_at` the row had before this write too, so that a write of
            # the server never loses against a field that has no clock of its own.
            floor = last if server_stamp is None else max(last, server_stamp[0])
            made = clock.tick(floor, now_ms)
        else:
            made, device = clock.stamp_of(*stamp)
        new: clock.Stamp = (made, device)

        row, _created = RowClocks.objects.get_or_create(
            home_id=home_id, entity=entity, row_id=row_id
        )
        won: dict[str, Any] = {}
        lost: dict[str, tuple[str, str]] = {}
        for name, value in written.items():
            if isinstance(value, _WriteClock):
                value = clock.format_clock(made)
            is_known = known is None or name in known or name == DELETED_AT
            stored = _stored_stamp(row, name, is_known, server_stamp)
            if stored is not None and not clock.wins(new, stored):
                lost[name] = (clock.format_clock(stored[0]), clock.device_to_b64(stored[1]))
                continue
            won[name] = value
            encoded = clock.device_to_b64(device)
            if is_known:
                row.clocks[name] = [clock.format_clock(made), encoded]
            else:
                row.extra[name] = [value, clock.format_clock(made), encoded]

        # The clock moves on even when nothing is written: the server has read this one.
        head.clock = clock.format_clock(
            made if stamp is None else clock.receive(last, made, now_ms)
        )
        if not won:
            head.save(update_fields=["clock"])
            return Recorded(None, lost)

        if kind == DELETE and DELETED_AT in won:
            row.tombstoned_at = timezone.now()
        elif kind == RESTORE and DELETED_AT in won:
            row.tombstoned_at = None
        row.save()
        head.pos += 1
        head.save(update_fields=["pos", "clock"])
        change = Change.objects.create(
            home_id=home_id,
            pos=head.pos,
            entity=entity,
            row_id=row_id,
            kind=kind,
            clock=clock.format_clock(made),
            device=device,
            fields={} if kind != PUT else won,
        )
        return Recorded(change, lost)


def _stored_stamp(
    row: RowClocks, name: str, is_known: bool, from_updated_at: clock.Stamp | None
) -> clock.Stamp | None:
    """The stamp now on a field of a row, or None when it never had one."""
    if is_known:
        entry = row.clocks.get(name)
        if entry is not None:
            return clock.parse_clock(entry[0]), clock.device_from_b64(entry[1])
        return from_updated_at
    entry = row.extra.get(name)
    if entry is None:
        return None
    return clock.parse_clock(entry[1]), clock.device_from_b64(entry[2])

"""What the sync API reads: the homes of a connection, the feed of a home, its snapshot.

Lot M4 of docs/adr/0079, shapes of docs/adr/0077 and of the mobile protocol (sections 2.2, 2.4
and 2.10). Nothing here writes. The views (`sync.api`) check the credential and the home; these
functions take a home the request may read.

What leaves the server is the data of the protocol and nothing else: the fields a model sends
(`sync.rows`), their clocks and devices, the position of a change. Never the columns the server
keeps for itself (`created_at` of the log, `tombstoned_at`, the `floor` and the clock of a
home), never the name of a stored file: a file field that the change log keeps by the name of
its file (the label of an article, the photo of a report) is given as a file reference named by
the id of its row. A photo, an attachment and a file of a project send their reference
themselves (`sync.files`, lot M6).
"""

import datetime
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.db import models, transaction

from housecairn.accounts.models import User
from housecairn.accounts.names import person_name
from housecairn.homes import services as home_services
from housecairn.homes.models import Home, Membership
from housecairn.sync import changes as sync_changes
from housecairn.sync import clock, retention
from housecairn.sync import files as sync_files
from housecairn.sync import rows as sync_rows
from housecairn.sync.models import Change, ErasedHome, ErasedHomeConnection, HomeHead, RowClocks

# The entity types of the protocol a server carries (mobile protocol, section 2.2), in the order
# of the snapshot: a row comes after the rows it refers to whenever that order exists. The family
# types (`family.member`, `family.settings`) are not rows of a server (docs/adr/0077).
ENTITIES: tuple[str, ...] = (
    "homes.home",
    "rooms.room",
    "rooms.item",
    "rooms.photo",
    "tasks.contractor",
    "stock.article",
    "stock.supplier",
    "stock.article_rooms",
    "stock.article_compatible_items",
    "tasks.task",
    "tasks.taskmaterial",
    "tasks.occurrence",
    "tasks.materialdraw",
    "tasks.attachment",
    "stock.movement",
    "projects.project",
    "projects.project_rooms",
    "projects.budgetline",
    "projects.quote",
    "projects.projectfile",
    "projects.projectlink",
    "projects.projecttask",
    "projects.annualenvelope",
    "reports.report",
)

# A page of the feed: what a phone gets without asking, and the most it may ask for.
DEFAULT_LIMIT = 500
MAX_LIMIT = 1000

DELETED_AT = sync_changes.DELETED_AT


# ---------- models ----------


@dataclass(frozen=True)
class Link:
    """A link table of the protocol: its model, its owner's model and the two columns."""

    through: type[models.Model]
    owner: type[models.Model]
    owner_column: str
    other_column: str


def model_of(entity: str) -> type[models.Model]:
    """The model an entity type names; for a link table, its `through` model."""
    app_label, _dot, name = entity.partition(".")
    if "_" in name:  # a model name has none: `<owner>_<relation>` is a link table
        return link_of(entity).through
    return apps.get_model(app_label, name)


def link_of(entity: str) -> Link:
    """The link table `entity` (`projects.project_rooms`): `<app>.<owner>_<relation>`."""
    app_label, _dot, name = entity.partition(".")
    owner_name, _sep, relation = name.partition("_")
    owner = apps.get_model(app_label, owner_name)
    field: Any = owner._meta.get_field(relation)
    owner_column, other_column = sync_rows.link_fields(field)
    return Link(field.remote_field.through, owner, owner_column, other_column)


def is_link(entity: str) -> bool:
    return bool(model_of(entity)._meta.auto_created)


# ---------- values as they leave the server ----------


def file_reference(
    model: type[models.Model], name: str, row: models.Model | None, row_id: Any, stored: str
) -> Any:
    """A file the change log keeps by the name of its stored file, as a reference.

    The photo of a report and the label of an article (docs/adr/0085). The reference is named
    by the file id the stored name holds: the id of the row for a file made on the web, as a
    photo of a room is (`rooms.syncing`), the phone's id for a file a phone sent, with the
    digest of what it sent. The name of the stored file never leaves the server. Its type and
    size are those the row keeps for its file now, when it keeps them.
    """
    holder = sync_files.holder_of(model)
    if holder is None or holder.field != name:  # pragma: no cover - every file field holds
        raise ValueError(f"{model._meta.label}.{name} holds no file.")
    reference = sync_files.stored_reference(row, holder, stored, row_id)
    for key in ("size", "type"):
        reference[key] = reference[key] or None  # a column of a row without a file: none
    return reference


def public_fields(
    entity: str, row_id: Any, fields: dict[str, Any], row: models.Model | None
) -> dict[str, Any]:
    """The fields of a change as a phone reads them: a stored file name becomes a reference."""
    if is_link(entity):
        return dict(fields)
    model = model_of(entity)
    file_names = sync_rows.spec_of(model).file_names
    return {
        name: (
            file_reference(model, name, row, row_id, value)
            # A stored file name; a reference a phone pushed is given as it came.
            if name in file_names and isinstance(value, str)
            else value
        )
        for name, value in fields.items()
    }


# ---------- homes of a connection ----------


def _instant_text(entry: list[str] | None, instant: datetime.datetime | None) -> str | None:
    """An instant field of the protocol (`archived_at`): the HLC kept with its clock.

    The web keeps a date and time; the server gives back the clock string of the write, and an
    instant written before the change log existed as the clock of that instant.
    """
    if instant is None:
        return None
    if entry is not None:
        return entry[0]
    return clock.format_clock(clock.clock_of_instant(instant))


def members_of(home: Home) -> list[dict[str, str]]:
    """The members of a home: their account id, the name shown for them and their role.

    Never their address (docs/adr/0077, need 9): the phone shows who did what and assigns a
    task by the account id, which the rows hold (`assignee_id`, `completed_by_id`).
    """
    return [
        {
            "id": str(membership.user_id),
            "name": person_name(membership.user),
            "role": membership.role,
        }
        for membership in home_services.members(home)
    ]


def head_of(home_id: uuid.UUID) -> HomeHead:
    """The head of the log of a home, a fresh one (position 0) when nothing was recorded."""
    head = HomeHead.objects.filter(home_id=home_id).first()
    return head if head is not None else HomeHead(home_id=home_id)


def home_entry(home: Home, user: User) -> dict[str, Any]:
    archived = RowClocks.objects.filter(
        home_id=home.pk, entity="homes.home", row_id=home.pk
    ).first()
    entry = archived.clocks.get("archived_at") if archived is not None else None
    role = Membership.objects.filter(home=home, user=user).values_list("role", flat=True).first()
    return {
        "id": str(home.pk),
        "name": home.name,
        "role": role,
        "archived_at": _instant_text(entry, home.archived_at),
        "head": head_of(home.pk).pos,
        # The `id` of the entry of `members` that is the account of the connection: the phone
        # picks its own reminders by it (docs/adr/0077, decision of 2026-11-16).
        "member_id": str(user.pk),
        "members": members_of(home),
    }


def gone_for(connection_id: uuid.UUID) -> list[dict[str, str]]:
    """The erased homes that were told to a connection: bound to it, its account a member."""
    erased = ErasedHome.objects.filter(connections__connection_id=connection_id)
    return [
        {"id": str(row.pk), "at": row.erased_at.isoformat()}
        for row in erased.order_by("erased_at", "id")
    ]


def was_bound_when_erased(home_id: uuid.UUID, connection_id: uuid.UUID) -> bool:
    return ErasedHomeConnection.objects.filter(
        erased_id=home_id, connection_id=connection_id
    ).exists()


# ---------- the feed ----------


class CursorExpiredError(Exception):
    """The cursor is below the oldest change kept: the phone starts again from the snapshot."""


class CursorOutOfRangeError(Exception):
    """The cursor is beyond the head of the home: no change of this home ever had it."""


@dataclass(frozen=True)
class Page:
    changes: list[dict[str, Any]]
    head: int
    more: bool


def operation_of(change: Change, row: models.Model | None = None) -> dict[str, Any]:
    """A change as the operation of the protocol (section 2.2): `t`, `h`, `id`, `k`, `c`, `f`."""
    return {
        "t": change.entity,
        "h": str(change.home_id),
        "id": str(change.row_id),
        "k": change.kind,
        "c": change.clock,
        "f": public_fields(change.entity, change.row_id, dict(change.fields), row),
    }


def changes_after(home_id: uuid.UUID, after: int, limit: int) -> Page:
    """The changes of a home after the position `after`, at most `limit`, in position order.

    `after` and `limit` are checked by the caller to be integers, `after` at least 0 and
    `limit` between 1 and `MAX_LIMIT`; this checks them against the log of the home.
    """
    if after < 0 or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("A cursor is at least 0 and a page holds 1 to MAX_LIMIT changes.")
    head = head_of(home_id)
    if after > head.pos:
        raise CursorOutOfRangeError
    if after < head.floor:
        raise CursorExpiredError
    # Up to the head read above: a change committed since then is in the next page.
    found = list(
        Change.objects.filter(home_id=home_id, pos__gt=after, pos__lte=head.pos).order_by("pos")[
            : limit + 1
        ]
    )
    page, more = found[:limit], len(found) > limit
    files = _rows_with_files(page)
    return Page(
        [
            {
                "pos": change.pos,
                "device": clock.device_to_b64(bytes(change.device)),
                "op": operation_of(change, files.get((change.entity, change.row_id))),
            }
            for change in page
        ],
        head.pos,
        more,
    )


def _rows_with_files(page: list[Change]) -> dict[tuple[str, uuid.UUID], models.Model]:
    """The rows of the changes that carry a stored file name, read once per entity type."""
    wanted: dict[str, set[uuid.UUID]] = {}
    for change in page:
        if is_link(change.entity):
            continue
        names = sync_rows.spec_of(model_of(change.entity)).file_names
        if names & set(change.fields):
            wanted.setdefault(change.entity, set()).add(change.row_id)
    found: dict[tuple[str, uuid.UUID], models.Model] = {}
    for entity, ids in wanted.items():
        model = model_of(entity)
        for row in model._base_manager.filter(pk__in=ids):
            found[(entity, row.pk)] = row
    return found


# ---------- the snapshot ----------


def snapshot_lines(home: Home) -> Iterator[dict[str, Any]]:
    """The state of a home, one row per line, then its members, then `{"covers": pos}`.

    A line is `{"t", "h", "id", "f": {field: [value, clock, device]}}` (protocol section 2.4),
    the tombstones within the retention included: a deleted row holds `deleted_at`, an HLC.
    Read in one transaction, in this order: the head, then for each entity type the clocks, then
    the rows. A write committed meanwhile is then either after `covers`, and the feed gives it
    again (applying it twice changes nothing), or not seen at all; a value is never older than
    the clock given with it.
    """
    lines: list[dict[str, Any]] = []
    with transaction.atomic():
        covers = head_of(home.pk).pos
        device = clock.device_to_b64(sync_changes.server_device())
        for entity in ENTITIES:
            lines.extend(_entity_lines(home, entity, device))
        members = members_of(home)
    yield from lines
    yield {"members": members}
    yield {"covers": covers}


def _clocks_of(home_id: uuid.UUID, entity: str) -> dict[uuid.UUID, RowClocks]:
    return {
        rc.row_id: rc for rc in RowClocks.objects.filter(home_id=home_id, entity=entity).iterator()
    }


def _stamp(entry: list[str] | None, fallback: tuple[str, str]) -> tuple[str, str]:
    return (entry[0], entry[1]) if entry is not None else fallback


def _entity_lines(home: Home, entity: str, server_device: str) -> list[dict[str, Any]]:
    clocks = _clocks_of(home.pk, entity)
    if is_link(entity):
        return _link_lines(home, entity, clocks, server_device)
    model = model_of(entity)
    queryset = (
        model._base_manager.filter(pk=home.pk)
        if model is Home
        else model._base_manager.filter(home_id=home.pk)
    )
    expired = retention.limit()
    lines = []
    seen = set()
    for row in queryset.order_by("pk").iterator():
        seen.add(row.pk)
        rc = clocks.get(row.pk)
        deleted_at = getattr(row, "deleted_at", None)
        tombstone = rc is not None and rc.tombstoned_at is not None
        if deleted_at is not None and not tombstone and deleted_at < expired:
            continue  # deleted before the log, longer ago than the retention
        lines.append(_row_line(home, entity, model, row, rc, server_device))
    # A row a phone deleted before its put reached the server: only its tombstone is here,
    # given as in the feed, so that a phone starting from the snapshot keeps it deleted.
    for row_id in sorted((r for r in clocks if r not in seen), key=str):
        rc = clocks[row_id]
        entry = rc.clocks.get(DELETED_AT)
        if rc.tombstoned_at is None or entry is None:
            continue
        fields: dict[str, list[Any]] = {DELETED_AT: [entry[0], entry[0], entry[1]]}
        for name, (value, made, device) in sorted(rc.extra.items()):
            fields[name] = [value, made, device]
        lines.append({"t": entity, "h": str(home.pk), "id": str(row_id), "f": fields})
    return lines


def _row_line(
    home: Home,
    entity: str,
    model: type[models.Model],
    row: models.Model,
    rc: RowClocks | None,
    server_device: str,
) -> dict[str, Any]:
    known = rc.clocks if rc is not None else {}
    updated_at = getattr(row, "updated_at", None)
    fallback = (
        clock.format_clock(clock.clock_of_instant(updated_at)) if updated_at else clock.ZERO_CLOCK,
        server_device,
    )
    write_clock = sync_rows.spec_of(model).write_clock
    values = dict(sync_rows.values_of(row))
    for name in write_clock:
        if name in values:
            values[name] = _instant_text(known.get(name), getattr(row, name))
    values = public_fields(entity, row.pk, values, row)
    fields: dict[str, list[Any]] = {}
    for name, value in values.items():
        fields[name] = [value, *_stamp(known.get(name), fallback)]
    deleted_at = getattr(row, "deleted_at", None)
    entry = known.get(DELETED_AT)
    if deleted_at is not None:
        stamp = _stamp(
            entry,
            (clock.format_clock(clock.clock_of_instant(deleted_at)), server_device),
        )
        fields[DELETED_AT] = [stamp[0], *stamp]
    elif entry is not None:  # restored: the restoration must beat an older deletion
        fields[DELETED_AT] = [None, entry[0], entry[1]]
    if rc is not None:
        for name, (value, made, device) in sorted(rc.extra.items()):
            fields[name] = [value, made, device]
    return {"t": entity, "h": str(home.pk), "id": str(row.pk), "f": fields}


def _link_lines(
    home: Home, entity: str, clocks: dict[uuid.UUID, RowClocks], server_device: str
) -> list[dict[str, Any]]:
    """The rows of a link table: the live links, then the removed ones within the retention.

    A link row has no id of its own: it is named as the change log names it
    (`sync.rows.link_row_id`). A link made before the change log has no clock: it reads as the
    zero clock, so that any write of it wins.
    """
    link = link_of(entity)
    fallback = (clock.ZERO_CLOCK, server_device)
    live: dict[uuid.UUID, dict[str, Any]] = {}
    found = link.through._default_manager.filter(
        **{f"{link.owner_column.removesuffix('_id')}__home_id": home.pk}
    ).values_list(link.owner_column, link.other_column)
    for owner, other in found:
        row_id = sync_rows.link_row_id(home.pk, link.through, owner, other)
        known = clocks[row_id].clocks if row_id in clocks else {}
        fields: dict[str, list[Any]] = {
            link.owner_column: [str(owner), *_stamp(known.get(link.owner_column), fallback)],
            link.other_column: [str(other), *_stamp(known.get(link.other_column), fallback)],
        }
        entry = known.get(DELETED_AT)
        if entry is not None:
            fields[DELETED_AT] = [None, entry[0], entry[1]]
        live[row_id] = {"t": entity, "h": str(home.pk), "id": str(row_id), "f": fields}
    removed = [rc for row_id, rc in clocks.items() if row_id not in live and rc.tombstoned_at]
    for rc in removed:
        put = (
            Change.objects.filter(
                home_id=home.pk, entity=entity, row_id=rc.row_id, kind=sync_changes.PUT
            )
            .order_by("-pos")
            .first()
        )
        fields = {}
        for name, value in (put.fields if put is not None else {}).items():
            entry = rc.clocks.get(name)
            if entry is not None:
                fields[name] = [value, entry[0], entry[1]]
        entry = rc.clocks.get(DELETED_AT)
        if entry is not None:
            fields[DELETED_AT] = [entry[0], entry[0], entry[1]]
        live[rc.row_id] = {"t": entity, "h": str(home.pk), "id": str(rc.row_id), "f": fields}
    return [live[row_id] for row_id in sorted(live, key=str)]

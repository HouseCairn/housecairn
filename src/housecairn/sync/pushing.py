"""Apply the push of a phone: its operations, field by field (lot M5a of docs/adr/0079).

The rules are those of docs/adr/0077 (needs 1, 5, 7, 9 and 11, "Results of a push") and of the
mobile protocol (section 2.2): an operation `{t, h, id, k, c, f}` writes each field of `f` only
if its clock and device beat the ones the field holds; a field the server has no column for is
kept in `extra`; each operation gets one result. The whole push is one transaction: an
operation that is malformed makes the push a 400 with its index, and nothing is written.

Every write goes through `sync.rows` and `sync.changes`, the helpers of the change log: the
change is recorded first, which decides the fields that win, then only those are written.

Not here (lot M5b): the repair rules across rows and the effects the web runs once per new
state. Not here either: the creation of a home (M5c) and the files (M6). A file reference in an
operation is kept as a field the server does not know yet, in `extra`, until M6 maps it.
"""

import datetime
import decimal
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils.translation import gettext as _

from housecairn.accounts.models import User
from housecairn.homes.models import Home, Membership, Role
from housecairn.sync import changes, clock, reading
from housecairn.sync import rows as sync_rows
from housecairn.sync.models import Change, HomeHead, RowClocks

# The limits of a push (docs/adr/0077, need 11).
MAX_OPS = 500
MAX_BODY_BYTES = 1024 * 1024
# How deep a value of a field may nest lists and objects (the fields object itself is 0).
MAX_DEPTH = 32
# The lowest schema version that writes: a phone below it reads but does not write.
MIN_SV = 1

APPLIED = "applied"
IGNORED = "ignored"
REJECTED = "rejected"

FORBIDDEN = "forbidden"
UNKNOWN_TYPE = "unknown_type"
INVALID_REFERENCE = "invalid_reference"
WRONG_HOME = "wrong_home"
ID_TAKEN = "id_taken"

KINDS = (changes.PUT, changes.DELETE, changes.RESTORE)
ARCHIVED_AT = "archived_at"
# The field the server writes itself: the account of the phone, whatever the operation says.
COMPLETED_BY = "completed_by_id"

_ENTITY = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")
_FIELD = re.compile(r"[a-z][a-z0-9_]{0,63}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class MalformedError(Exception):
    """An operation the server cannot read: the push is refused with 400 and its index."""

    def __init__(self, index: int, message: str) -> None:
        super().__init__(message)
        self.index = index
        self.message = message


class _RejectedError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Pusher:
    """Who pushes: the account of the connection, its role in the home, the phone's device."""

    user: User
    role: str
    device: bytes


@dataclass(frozen=True)
class Operation:
    index: int
    entity: str
    home_id: uuid.UUID
    row_id: uuid.UUID
    kind: str
    clock: str
    fields: dict[str, Any]


# ---------- reading the operations ----------


def _uuid(index: int, value: Any, what: str) -> uuid.UUID:
    if isinstance(value, str) and len(value) == 36:
        try:
            parsed = uuid.UUID(value)
        except ValueError:
            pass
        else:
            if str(parsed) == value.lower():
                return parsed
    raise MalformedError(
        index, _("%(name)s is a UUID in its canonical text form.") % {"name": what}
    )


def _text_ok(index: int, value: str) -> None:
    """A text the databases store: no NUL character, and nothing UTF-8 cannot encode.

    PostgreSQL refuses a NUL in text and JSON. JSON may also carry a lone surrogate
    (`"\\ud800"`, half of an emoji cut by a phone), which no database encodes: kept, it would
    fail the write, or fail every later read of the feed if it went to `extra`.
    """
    if "\x00" in value:
        raise MalformedError(index, _("A text holds a NUL character."))
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise MalformedError(index, _("A text is not valid Unicode.")) from None


def _check_values(index: int, fields: dict[str, Any]) -> None:
    """Every text of the fields of an operation, keys included, as `_text_ok` wants it, and
    values nested at most `MAX_DEPTH` deep (a JSON column and a field kept in `extra` alike).

    Walked with a stack, not by recursion: a value nested a thousand deep is a 400, not a
    `RecursionError`.
    """
    stack: list[tuple[Any, int]] = [(fields, 0)]
    while stack:
        value, depth = stack.pop()
        if isinstance(value, str):
            _text_ok(index, value)
        elif isinstance(value, dict | list):
            if depth > MAX_DEPTH:
                raise MalformedError(
                    index, _("A value is nested more than %(depth)s deep.") % {"depth": MAX_DEPTH}
                )
            if isinstance(value, dict):
                for key, item in value.items():
                    stack.append((key, depth))
                    stack.append((item, depth + 1))
            else:
                stack.extend((item, depth + 1) for item in value)


def parse(index: int, raw: Any) -> Operation:
    """An operation of the protocol (section 2.2), checked for its form only."""
    if not isinstance(raw, dict):
        raise MalformedError(index, _("An operation is a JSON object."))
    entity = raw.get("t")
    if not isinstance(entity, str) or _ENTITY.fullmatch(entity) is None or len(entity) > 64:
        raise MalformedError(index, _("t is an entity type, <app>.<model> in lower case."))
    home_id = _uuid(index, raw.get("h"), "h")
    row_id = _uuid(index, raw.get("id"), "id")
    kind = raw.get("k")
    if kind not in KINDS:
        raise MalformedError(index, _("k is put, delete or restore."))
    made = raw.get("c")
    try:
        clock.parse_clock(made if isinstance(made, str) else "")
    except ValueError:
        raise MalformedError(index, _("c is a clock, 16 lower-case hexadecimal digits.")) from None
    fields = raw.get("f")
    if kind == changes.PUT:
        if not isinstance(fields, dict) or not fields:
            raise MalformedError(index, _("A put sets at least one field in f."))
    elif fields not in (None, {}):
        raise MalformedError(index, _("A delete or a restore sets no field."))
    fields = dict(fields or {})
    for name in fields:
        if not isinstance(name, str) or _FIELD.fullmatch(name) is None:
            raise MalformedError(index, _("A field is named in lower case, at most 64 characters."))
        if name == changes.DELETED_AT:
            raise MalformedError(index, _("deleted_at is written by a delete or a restore."))
    _check_values(index, fields)
    return Operation(index, entity, home_id, row_id, kind, str(made), fields)


# ---------- values ----------


def _instant(index: int, value: Any) -> datetime.datetime:
    """An instant of the protocol, an HLC: the web keeps its physical part (docs/adr/0077)."""
    try:
        ms = clock.physical_ms(clock.parse_clock(value))
    except ValueError:
        raise MalformedError(index, _("An instant is an HLC, 16 hexadecimal digits.")) from None
    try:
        return datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC) + datetime.timedelta(
            milliseconds=ms
        )
    except OverflowError:
        raise MalformedError(index, _("An instant is beyond the dates the server keeps.")) from None


def decode(index: int, field: models.Field[Any, Any], value: Any) -> Any:
    """A value of the protocol as the column holds it, checked as the model checks it.

    Amounts and quantities are decimal strings, dates `YYYY-MM-DD`, instants HLC strings,
    booleans and whole numbers JSON ones. A choice the server does not know is kept as it is
    (mobile ADR 0003: \"Other (update the app)\"), so choices are not checked.
    """
    name = field.attname
    if value is None:
        if field.null:
            return None
        if field.empty_strings_allowed and isinstance(field, models.CharField | models.TextField):
            return ""
        raise MalformedError(index, _("%(name)s cannot be empty.") % {"name": name})
    if isinstance(field, models.ForeignKey):
        return _uuid(index, value, name)
    if isinstance(field, models.BooleanField):
        if not isinstance(value, bool):
            raise MalformedError(index, _("%(name)s is true or false.") % {"name": name})
        return value
    if isinstance(field, models.DecimalField):
        if not isinstance(value, str):
            raise MalformedError(
                index, _("%(name)s is an amount, a decimal string.") % {"name": name}
            )
        try:
            python: Any = decimal.Decimal(value)
        except decimal.InvalidOperation:
            raise MalformedError(
                index, _("%(name)s is an amount, a decimal string.") % {"name": name}
            ) from None
        if not python.is_finite():  # NaN cannot be compared by the validators of the field
            raise MalformedError(
                index, _("%(name)s is an amount, a decimal string.") % {"name": name}
            )
    elif isinstance(field, models.DateTimeField):
        python = _instant(index, value)
    elif isinstance(field, models.DateField):
        if not isinstance(value, str) or _DATE.fullmatch(value) is None:
            raise MalformedError(index, _("%(name)s is a date, YYYY-MM-DD.") % {"name": name})
        try:
            python = datetime.date.fromisoformat(value)
        except ValueError:
            raise MalformedError(
                index, _("%(name)s is a date, YYYY-MM-DD.") % {"name": name}
            ) from None
    elif isinstance(field, models.IntegerField):
        if isinstance(value, bool) or not isinstance(value, int):
            raise MalformedError(index, _("%(name)s is a whole number.") % {"name": name})
        python = value
    elif isinstance(field, models.JSONField):
        python = value
    elif isinstance(field, models.CharField | models.TextField):
        if not isinstance(value, str):
            raise MalformedError(index, _("%(name)s is a text.") % {"name": name})
        python = value
    else:  # pragma: no cover - every column of the rows is one of the kinds above
        raise MalformedError(index, _("%(name)s cannot be written by a push.") % {"name": name})
    try:
        field.run_validators(python)
    except (ValidationError, decimal.InvalidOperation):
        raise MalformedError(index, _("%(name)s is not a valid value.") % {"name": name}) from None
    return python


# ---------- what a model lets a phone write ----------


@dataclass(frozen=True)
class Columns:
    """The columns of a model a phone writes, and the names it may not write."""

    writable: dict[str, models.Field[Any, Any]]
    server_owned: frozenset[str]


def columns_of(model: type[models.Model]) -> Columns:
    spec = sync_rows.spec_of(model)
    synced = [
        f
        for f in sync_rows._fields(model)
        if f.name not in spec.file_names and not isinstance(f, models.FileField)
    ]
    writable = {f.attname: f for f in synced}
    files = {f.attname for f in sync_rows._fields(model) if f.attname not in writable}
    owned = set()
    for f in model._meta.concrete_fields:
        if f.attname not in writable and f.attname not in files:
            owned.update({f.attname, f.name})
    owned |= sync_rows.BOOKKEEPING - set(spec.send)
    owned |= sync_rows.derived(model)
    return Columns(writable, frozenset(owned - {changes.DELETED_AT} - files))


def _required(model: type[models.Model], columns: Columns) -> list[str]:
    """The columns a new row cannot go without: no default, not nullable, not text."""
    return [
        name
        for name, f in columns.writable.items()
        if not f.null
        and not f.has_default()
        and not f.empty_strings_allowed
        and not getattr(f, "auto_now_add", False)
        and not getattr(f, "auto_now", False)
    ]


# ---------- the push ----------


def _holders(ops: Sequence[Operation]) -> dict[uuid.UUID, set[tuple[str, uuid.UUID]]]:
    """Who holds each id of the push now: (entity type, home), in every table of the rows."""
    wanted = {op.row_id for op in ops}
    found: dict[uuid.UUID, set[tuple[str, uuid.UUID]]] = {}
    for entity in reading.ENTITIES:
        if reading.is_link(entity):
            continue
        model = reading.model_of(entity)
        column = "pk" if model is Home else "home_id"
        for pk, home_id in model._base_manager.filter(pk__in=wanted).values_list("pk", column):
            found.setdefault(pk, set()).add((entity, home_id))
    for row_id, entity, home_id in RowClocks.objects.filter(row_id__in=wanted).values_list(
        "row_id", "entity", "home_id"
    ):
        found.setdefault(row_id, set()).add((entity, home_id))
    return found


def apply(home: Home, pusher: Pusher, raw_ops: Sequence[Any]) -> dict[str, Any]:
    """Apply the operations of a push to `home`, in one transaction; the answer of the push.

    Raises `MalformedError` (nothing written) when an operation cannot be read, or when a write
    breaks a rule of the database: the repair rules that would prevent it are lot M5b.
    """
    ops = [parse(index, raw) for index, raw in enumerate(raw_ops)]
    with transaction.atomic():
        holders = _holders(ops)
        results = []
        for op in ops:
            try:
                with transaction.atomic():
                    result = _apply_one(home, pusher, op, holders)
            except _RejectedError as rejected:
                result = {"i": op.index, "status": REJECTED, "code": rejected.code}
            except IntegrityError:
                raise MalformedError(
                    op.index, _("This operation breaks a rule of the data.")
                ) from None
            results.append(result)
        head = HomeHead.objects.filter(home_id=home.pk).values_list("pos", flat=True).first()
    return {"head": head or 0, "results": results}


def _apply_one(
    home: Home,
    pusher: Pusher,
    op: Operation,
    holders: dict[uuid.UUID, set[tuple[str, uuid.UUID]]],
) -> dict[str, Any]:
    if pusher.role not in (Role.OWNER, Role.MEMBER):
        raise _RejectedError(FORBIDDEN)  # a read-only member: nothing is accepted
    if op.entity not in reading.ENTITIES:
        raise _RejectedError(UNKNOWN_TYPE)  # `family.*` included: a server has no such rows
    if op.home_id != home.pk or (op.entity == "homes.home" and op.row_id != home.pk):
        raise _RejectedError(WRONG_HOME)
    held = holders.get(op.row_id, set())
    if held - {(op.entity, home.pk)}:
        raise _RejectedError(ID_TAKEN)  # in another home or another table: it says not where
    stamp = (op.clock, pusher.device)
    if reading.is_link(op.entity):
        result = _apply_link(home, op, stamp)
    else:
        result = _apply_row(home, pusher, op, stamp)
    holders.setdefault(op.row_id, set()).add((op.entity, home.pk))
    return result


def _result(
    op: Operation, recorded: changes.Recorded | None, lost: dict[str, Any]
) -> dict[str, Any]:
    if recorded is None or recorded.change is None:
        result: dict[str, Any] = {"i": op.index, "status": IGNORED}
    else:
        result = {"i": op.index, "status": APPLIED}
    if lost:
        result["lost"] = lost
    return result


def _apply_row(
    home: Home, pusher: Pusher, op: Operation, stamp: tuple[str, bytes]
) -> dict[str, Any]:
    model = reading.model_of(op.entity)
    columns = columns_of(model)
    stored = model._base_manager.filter(pk=op.row_id).first()
    if op.kind != changes.PUT:
        if model is Home:
            raise _RejectedError(FORBIDDEN)  # a home is erased on the web, by an owner only
        if stored is None:
            # The row has not reached the server yet (its put comes later, from another
            # phone): the deletion is kept as a tombstone, which the put then finds
            # (`rows._create_stamped`), so that the order of the pushes does not matter.
            recorded = changes.record_change(home.pk, op.entity, op.row_id, op.kind, stamp=stamp)
            return _result(op, recorded, _lost(op, model, recorded))
        recorded = sync_rows.apply_stamped(
            model, home.pk, op.row_id, op.kind, None, {}, stamp=stamp, known=columns.writable
        )
        return _result(op, recorded, _lost(op, model, recorded))

    if columns.server_owned & set(op.fields):
        raise _RejectedError(FORBIDDEN)  # a column of the server, a derived field
    if model is Home and ARCHIVED_AT in op.fields and pusher.role != Role.OWNER:
        raise _RejectedError(FORBIDDEN)  # owners only archive (docs/adr/0077, need 9)
    fields = dict(op.fields)
    if COMPLETED_BY in columns.writable and fields.get(COMPLETED_BY) is not None:
        fields[COMPLETED_BY] = str(pusher.user.pk)  # the phone acts as its account
    values: dict[str, Any] = {}
    logged: dict[str, Any] = {}
    for name, value in fields.items():
        field = columns.writable.get(name)
        if field is None:
            logged[name] = value  # unknown to the server, or a file until M6: `extra`
            continue
        values[name] = decode(op.index, field, value)
        logged[name] = (
            value
            if isinstance(field, models.DateTimeField) or values[name] is None
            else sync_rows.encode(model, name, values[name])
        )
    if stored is None:
        missing = [name for name in _required(model, columns) if values.get(name) is None]
        if missing:
            raise MalformedError(
                op.index, _("A new row needs %(fields)s.") % {"fields": ", ".join(sorted(missing))}
            )
    _check_references(home, columns, values)
    recorded = sync_rows.apply_stamped(
        model, home.pk, op.row_id, changes.PUT, logged, values, stamp=stamp, known=columns.writable
    )
    result = _result(op, recorded, _lost(op, model, recorded))
    texts = other_versions(model, fields, recorded.lost)
    if texts:
        result["other_version"] = texts
    return result


def _check_references(home: Home, columns: Columns, values: Mapping[str, Any]) -> None:
    """A reference names a row of this home, or a member of it: else `invalid_reference`."""
    for name, value in values.items():
        field = columns.writable[name]
        if value is None or not isinstance(field, models.ForeignKey):
            continue
        target: Any = field.related_model
        if target is User:
            ok = Membership.objects.filter(home=home, user_id=value).exists()
        elif target is Home:
            ok = value == home.pk
        else:
            ok = target._base_manager.filter(pk=value, home_id=home.pk).exists()
        if not ok:
            raise _RejectedError(INVALID_REFERENCE)


def _lost(
    op: Operation,
    model: type[models.Model],
    recorded: changes.Recorded,
) -> dict[str, Any]:
    """For each field that lost: the value, the clock and the device that won.

    A long text that lost is the phone's to keep as \"other version\" (docs/adr/0077, need 5):
    its own text is given back in `other_version` by the caller.
    """
    if not recorded.lost:
        return {}
    row = model._base_manager.filter(pk=op.row_id).first()
    rc = RowClocks.objects.filter(home_id=op.home_id, entity=op.entity, row_id=op.row_id).first()
    now = {} if row is None else dict(sync_rows.raw_values(row))
    lost: dict[str, Any] = {}
    for name, (made, device) in recorded.lost.items():
        lost[name] = [_value_now(model, row, rc, now, name), made, device]
    return lost


def _value_now(
    model: type[models.Model],
    row: models.Model | None,
    rc: RowClocks | None,
    now: Mapping[str, Any],
    name: str,
) -> Any:
    if name == changes.DELETED_AT:
        entry = rc.clocks.get(name) if rc is not None else None
        deleted = row is not None and getattr(row, "deleted_at", None) is not None
        return entry[0] if deleted and entry is not None else None
    if rc is not None and name in rc.extra:
        return rc.extra[name][0]
    if name not in now:
        return None
    if name in sync_rows.spec_of(model).write_clock:
        entry = rc.clocks.get(name) if rc is not None else None
        return reading._instant_text(entry, getattr(row, name))
    value = sync_rows.encode(model, name, now[name])
    return reading.public_fields(
        model._meta.label_lower, getattr(row, "pk", None), {name: value}, row
    )[name]


def other_versions(
    model: type[models.Model], sent: Mapping[str, Any], lost: Mapping[str, Any]
) -> dict[str, str]:
    """The long texts of the phone that lost: notes, procedure, goal (the text columns)."""
    texts = {f.attname for f in model._meta.concrete_fields if isinstance(f, models.TextField)}
    return {name: sent[name] for name in lost if name in texts and isinstance(sent.get(name), str)}


# ---------- link tables ----------


def _apply_link(home: Home, op: Operation, stamp: tuple[str, bytes]) -> dict[str, Any]:
    link = reading.link_of(op.entity)
    entity = op.entity
    owner_model: Any = link.owner
    other_model: Any = link.through._meta.get_field(link.other_column).related_model
    known = (link.owner_column, link.other_column)
    rc = RowClocks.objects.filter(home_id=home.pk, entity=entity, row_id=op.row_id).first()
    if op.kind == changes.PUT:
        unknown_names = set(op.fields) - set(known)
        if any(name not in op.fields for name in known):
            raise MalformedError(op.index, _("A link names both of its rows."))
        owner = _uuid(op.index, op.fields[link.owner_column], link.owner_column)
        other = _uuid(op.index, op.fields[link.other_column], link.other_column)
        if sync_rows.link_row_id(home.pk, link.through, owner, other) != op.row_id:
            raise _RejectedError(INVALID_REFERENCE)  # the id of a link names its two rows
        if not owner_model._base_manager.filter(pk=owner, home_id=home.pk).exists():
            raise _RejectedError(INVALID_REFERENCE)
        if not other_model._base_manager.filter(pk=other, home_id=home.pk).exists():
            raise _RejectedError(INVALID_REFERENCE)
        logged = {link.owner_column: str(owner), link.other_column: str(other)}
        logged.update({name: op.fields[name] for name in unknown_names})
        recorded = changes.record_change(
            home.pk, entity, op.row_id, changes.PUT, logged, stamp=stamp, known=known
        )
        rc = RowClocks.objects.get(home_id=home.pk, entity=entity, row_id=op.row_id)
        if recorded.change is not None and rc.tombstoned_at is None:
            sync_rows.write_link(link.through, {link.owner_column: owner, link.other_column: other})
        lost = {name: [logged.get(name), *won] for name, won in recorded.lost.items()}
        if changes.DELETED_AT in lost:  # pragma: no cover - a put writes no deleted_at
            del lost[changes.DELETED_AT]
        return _result(op, recorded, _link_lost(rc, lost))

    pair = _link_pair(home, op, link)
    # A link the server never had gets its tombstone too: a put of it that comes later stays
    # removed (the check of `tombstoned_at` above).
    recorded = changes.record_change(home.pk, entity, op.row_id, op.kind, stamp=stamp)
    if recorded.change is not None and pair is not None:
        columns = {link.owner_column: pair[0], link.other_column: pair[1]}
        if op.kind == changes.DELETE:
            sync_rows.remove_link(link.through, columns)
        else:
            sync_rows.write_link(link.through, columns)
    rc = RowClocks.objects.get(home_id=home.pk, entity=entity, row_id=op.row_id)
    lost = {
        name: [None if rc.tombstoned_at is None else won[0], *won]
        for name, won in recorded.lost.items()
    }
    return _result(op, recorded, lost)


def _link_lost(rc: RowClocks, lost: dict[str, Any]) -> dict[str, Any]:
    for name in lost:
        if name in rc.extra:
            lost[name][0] = rc.extra[name][0]
    return lost


def _link_pair(home: Home, op: Operation, link: reading.Link) -> tuple[uuid.UUID, uuid.UUID] | None:
    """The two rows a link row names: from its last `put`, or from the table itself."""
    put = (
        Change.objects.filter(home_id=home.pk, entity=op.entity, row_id=op.row_id, kind=changes.PUT)
        .order_by("-pos")
        .first()
    )
    if put is not None and link.owner_column in put.fields:
        return (
            uuid.UUID(put.fields[link.owner_column]),
            uuid.UUID(put.fields[link.other_column]),
        )
    owner_lookup = f"{link.owner_column.removesuffix('_id')}__home_id"
    for owner, other in link.through._default_manager.filter(**{owner_lookup: home.pk}).values_list(
        link.owner_column, link.other_column
    ):
        if sync_rows.link_row_id(home.pk, link.through, owner, other) == op.row_id:
            return owner, other
    return None

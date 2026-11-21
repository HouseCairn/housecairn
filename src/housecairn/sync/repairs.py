"""The repair rules and the effects of a push (lot M5b of docs/adr/0079).

A push writes data, never actions (docs/adr/0077, needs 6 and 7). Once its operations are
applied, in the same transaction, the server runs on the merged state:

- the repair rules of mobile ADR 0003, each repair a change of its own with the server's clock:
  - **an undo whose successor was edited elsewhere** is reverted: the successor comes back,
    the occurrence is closed again with the values it had before the undo (the last write of
    each field whose clock is before the undo's), and its draws come back with them;
  - **two open occurrences of one task**: the one that follows the other (through any number
    of `follows`) stays open, else the one made last (the clock of its `task_id`, then the
    device, then the id); the other is closed as done if `completed_on` is filled, else
    skipped. A database allows one open occurrence per task, so a write that would open a
    second one is held closed until the end of the push, and the rule decides then;
  - **the date of a successor** (a `put` that sets `follows_id` and `due_on`) is the one the
    web's own recurrence code gives (`recurrence.next_due`); another date is repaired to it.
    The day of a closing is `completed_on`, or for a skip the day of the clock of its
    `status`, in the time zone of the instance;
- the values the web derives: `visible_from` of the occurrences, the months of the tasks on a
  predefined season when the hemisphere of the home changed, the effect (`delta`) of each
  stock movement, in the order of their clocks (mobile ADR 0003, "Derived");
- the effects the web runs once per new state, keyed by ids made from the row, so that an
  effect that happened is never done again: an audit entry for each occurrence that becomes
  done or skipped (`occurrence.done`, `occurrence.skipped`, the account of the connection);
  for one that becomes done, the draws of its materials that the phone did not push
  (`draw:<occurrence>:<article>` neither live nor deleted); for an undo reverted, an exit of
  each draw the undo gave back, under an id made from the draw and the clock of the undo;
- the unit price a draw is taken at, the server's own field (docs/adr/0090): the article's
  when a phone's draw is new or comes back, kept as it is by any other write;
- the plan an occurrence keeps, the server's own fields as well (docs/adr/0091): the plan of
  its task when a push makes it done, none when a push makes it not done.

An operation whose row a repair then changed gets the result `repaired`.
"""

import datetime
import functools
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.db import models

from housecairn.accounts.models import User
from housecairn.audit import services as audit
from housecairn.audit.models import AuditEvent
from housecairn.homes.models import Home
from housecairn.instance.config import instance_zone
from housecairn.stock import services as stock_services
from housecairn.stock.models import Article, Movement, MovementKind
from housecairn.sync import changes, clock, ids
from housecairn.sync import rows as sync_rows
from housecairn.sync.models import Change, RowClocks
from housecairn.tasks import costs, recurrence
from housecairn.tasks import services as task_services
from housecairn.tasks.models import (
    CLOSED_STATUSES,
    OPEN_STATUSES,
    Attachment,
    MaterialDraw,
    Occurrence,
    Status,
    Task,
    TaskMaterial,
)

HOME = "homes.home"
TASK = "tasks.task"
OCCURRENCE = "tasks.occurrence"
DRAW = "tasks.materialdraw"
MOVEMENT = "stock.movement"

REPAIRED = "repaired"

# The fields of an occurrence an undo clears, and a reverted undo gives back.
UNDONE = (
    "status",
    "status_before",
    "completed_on",
    "completion_note",
    "actual_cost",
    "completed_by_id",
)
# How far up the `follows` of an occurrence the rule of two open ones looks.
MAX_CHAIN = 1000
_MISSING = object()


def day_of(made: str) -> datetime.date:
    """The day of a clock, in the time zone of the instance (the day a skip was made)."""
    instant = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC) + datetime.timedelta(
        milliseconds=clock.physical_ms(clock.parse_clock(made))
    )
    return instant.astimezone(instance_zone()).date()


@dataclass
class Session:
    """What a push did, for the rules and the effects that follow it."""

    home: Home
    user: User
    # The status of each occurrence before the push wrote it (None: the row was not there).
    start: dict[uuid.UUID, str | None] = field(default_factory=dict)
    # Occurrences written closed for the time of the push: their status, and their task.
    held: dict[uuid.UUID, tuple[str, uuid.UUID]] = field(default_factory=dict)
    touched: dict[str, set[uuid.UUID]] = field(default_factory=dict)
    successors: set[uuid.UUID] = field(default_factory=set)
    hemisphere: bool = False
    articles: set[uuid.UUID] = field(default_factory=set)
    repaired: set[tuple[str, uuid.UUID]] = field(default_factory=set)
    # Exits to write for the draws of reverted undos: (draw, occurrence, movement id).
    again: list[tuple[MaterialDraw, Occurrence, uuid.UUID]] = field(default_factory=list)

    # ---------- while the operations are applied ----------

    def begin(self, occurrence_ids: Iterable[uuid.UUID]) -> None:
        for pk, status in Occurrence.all_objects.filter(
            pk__in=set(occurrence_ids), home_id=self.home.pk
        ).values_list("pk", "status"):
            self.start.setdefault(pk, status)

    def _note_start(self, row: Occurrence) -> None:
        self.start.setdefault(row.pk, self._status(row))

    def settle_for(self, entity: str, pk: uuid.UUID) -> Any:
        """The `settle` of `sync_rows.apply_stamped` for a row of `entity`, or None."""
        if entity == OCCURRENCE:
            return functools.partial(self._settle_occurrence, pk)
        if entity == MOVEMENT:
            return self._settle_movement
        if entity == DRAW:
            return self._settle_draw
        return None

    def _settle_occurrence(
        self, pk: uuid.UUID, stored: Occurrence | None, columns: dict[str, Any], kind: str
    ) -> dict[str, Any]:
        self.start.setdefault(pk, None if stored is None else stored.status)
        if "status" in columns:
            status = columns["status"]
        elif pk in self.held:
            status = self.held[pk][0]
        else:
            status = Status.PLANNED if stored is None else stored.status
        if kind == changes.DELETE:
            deleted = True
        elif kind == changes.RESTORE:
            deleted = False
        elif stored is not None:
            deleted = stored.deleted_at is not None
        else:
            deleted = RowClocks.objects.filter(
                home_id=self.home.pk, entity=OCCURRENCE, row_id=pk, tombstoned_at__isnull=False
            ).exists()
        task_id = columns.get("task_id", getattr(stored, "task_id", None))
        if status in OPEN_STATUSES and not deleted and task_id is not None:
            others = Occurrence.objects.filter(task_id=task_id, status__in=OPEN_STATUSES)
            if others.exclude(pk=pk).exists():
                self.held[pk] = (status, task_id)
                return {"status": Status.SKIPPED}
        if self.held.pop(pk, None) is not None and "status" not in columns:
            return {"status": status}  # held before, free now: its own status again
        return {}

    def _settle_movement(
        self, stored: Movement | None, columns: dict[str, Any], kind: str
    ) -> dict[str, Any]:
        article = columns.get("article_id", getattr(stored, "article_id", None))
        if article is not None:
            self.articles.add(article)
        if stored is not None:
            return {}
        # A new row needs its effect at once (the column is required); `_movement_effects`
        # sets it in the order of the clocks once the push is applied.
        quantity = columns.get("quantity") or Decimal(0)
        kind_of = columns.get("kind")
        return {"delta": -quantity if kind_of == MovementKind.EXIT else quantity}

    def _settle_draw(
        self, stored: MaterialDraw | None, columns: dict[str, Any], kind: str
    ) -> dict[str, Any]:
        """The unit price of a draw a phone made, or brought back: the article's, now.

        The server's own field (docs/adr/0090): what was taken is valued at the price of when
        it was taken, so a later change of the price, or of the quantity used, keeps it.
        """
        coming_back = kind == changes.RESTORE and stored is not None and stored.deleted_at
        if stored is not None and not coming_back:
            return {}
        article = columns.get("article_id", getattr(stored, "article_id", None))
        price = (
            Article.all_objects.filter(pk=article, home_id=self.home.pk)
            .values_list("unit_price", flat=True)
            .first()
        )
        return {"unit_price": price}

    def note(self, entity: str, row_id: uuid.UUID, kind: str, sent: Any, won: Any) -> None:
        """An operation whose change won some fields: `sent` its fields, `won` those that won."""
        self.touched.setdefault(entity, set()).add(row_id)
        if entity == OCCURRENCE and kind == changes.PUT:
            if "follows_id" in sent and "due_on" in won:
                self.successors.add(row_id)
        elif entity == HOME and "hemisphere" in won:
            self.hemisphere = True

    # ---------- once they are ----------

    def finish(self, results: list[dict[str, Any]], ops: list[Any]) -> None:
        self._revert_undos()
        self._settle_open()
        self._done_when_marked()
        self._successor_dates()
        if self.hemisphere:
            home = Home.objects.get(pk=self.home.pk)
            task_services.resync_seasons(home)
        self._visible_from()
        self._movement_effects()
        self._effects()
        self._kept_plans()
        for result, op in zip(results, ops, strict=True):
            if result.get("status") == "applied" and (op.entity, op.row_id) in self.repaired:
                result["status"] = REPAIRED

    def _status(self, row: Occurrence) -> str:
        held = self.held.get(row.pk)
        return held[0] if held is not None else row.status

    def _occurrences(self) -> set[uuid.UUID]:
        return set(self.touched.get(OCCURRENCE, set())) | {
            pk for entity, pk in self.repaired if entity == OCCURRENCE
        }

    def _repair(self, row: models.Model, values: dict[str, Any]) -> None:
        """Write `values` on a row as a repair: a change of its own, with the server's clock."""
        model: Any = type(row)
        entity = sync_rows.entity_of(model)
        if isinstance(row, Occurrence):
            self._note_start(row)
        now = dict(sync_rows.raw_values(row))
        held = self.held.pop(row.pk, None)
        if held is not None:
            now["status"] = held[0]
        changed = {name: value for name, value in values.items() if now.get(name) != value}
        updated_at = getattr(row, "updated_at", None)
        for name, value in values.items():
            setattr(row, name, value)
        row.save(update_fields=[*values, "updated_at"])
        if changed:
            changes.record_change(
                self.home.pk,
                entity,
                row.pk,
                changes.PUT,
                {name: sync_rows.encode(model, name, value) for name, value in changed.items()},
                updated_at=updated_at,
            )
            self.repaired.add((entity, row.pk))

    def _stamp(self, row: models.Model, name: str) -> clock.Stamp:
        """The clock and device of a field of a row, as the snapshot gives them."""
        entity = sync_rows.entity_of(type(row))
        rc = RowClocks.objects.filter(home_id=self.home.pk, entity=entity, row_id=row.pk).first()
        entry = None if rc is None else rc.clocks.get(name)
        if entry is not None:
            return clock.parse_clock(entry[0]), clock.device_from_b64(entry[1])
        updated_at: datetime.datetime = row.updated_at  # type: ignore[attr-defined]
        return clock.clock_of_instant(updated_at), changes.server_device()

    # ---------- an undo whose successor was edited elsewhere ----------

    def _revert_undos(self) -> None:
        touched = self.touched.get(OCCURRENCE, set())
        wanted = set(touched) | {ids.successor(self.home.pk, pk) for pk in touched}
        gone = Occurrence.all_objects.filter(
            pk__in=wanted, home_id=self.home.pk, deleted_at__isnull=False
        ).order_by("pk")
        for successor in gone:
            if Task.objects.filter(pk=successor.task_id).first() is None:
                continue  # the task itself was deleted: so were its occurrences
            closed = self._predecessor(successor)
            if closed is None or closed.deleted_at is not None:
                continue
            if self._status(closed) not in OPEN_STATUSES or self._untouched(successor):
                continue
            self._revert(closed, successor)

    def _predecessor(self, successor: Occurrence) -> Occurrence | None:
        if successor.follows_id is not None:
            found: Occurrence | None = Occurrence.all_objects.filter(
                pk=successor.follows_id
            ).first()
            return found
        # The web's undo lets go of `follows` (`tasks.services._withdraw`): the id names it.
        rows: Iterable[Occurrence] = Occurrence.all_objects.filter(
            task_id=successor.task_id
        ).order_by("pk")
        for row in rows:
            if ids.successor(self.home.pk, row.pk) == successor.pk:
                return row
        return None

    def _untouched(self, row: Occurrence) -> bool:
        """As `tasks.services._untouched`: nobody worked on the occurrence since it was made."""
        return (
            self._status(row) == Status.PLANNED
            and row.postponed_from is None
            and not row.actual_cost
            and not row.completion_note
            and not Attachment.objects.filter(occurrence=row).exists()
        )

    def _revert(self, closed: Occurrence, successor: Occurrence) -> None:
        undo = self._stamp(closed, "status")
        values: dict[str, Any] = {}
        for name in UNDONE:
            value = self._value_before(closed, name, undo)
            if value is not _MISSING:
                values[name] = value
        if values.get("status") not in CLOSED_STATUSES:
            values["status"] = Status.DONE if values.get("completed_on") else Status.SKIPPED
        self._repair(closed, values)
        gone_at = self._stamp(successor, changes.DELETED_AT)
        self._note_start(successor)
        if successor.follows_id is None:
            self._repair(successor, {"follows_id": closed.pk})
        sync_rows.restore(successor)
        self.repaired.add((OCCURRENCE, successor.pk))
        made = clock.format_clock(gone_at[0])
        for draw in (
            MaterialDraw.all_objects.filter(occurrence=closed, deleted_at__isnull=False)
            .select_related("article")
            .order_by("article_id")
        ):
            sync_rows.restore(draw)
            self.repaired.add((DRAW, draw.pk))
            if draw.article.deleted_at is None:
                movement = uuid.uuid5(self.home.pk, f"undo-reverted:{draw.pk}:{made}")
                self.again.append((draw, closed, movement))

    def _value_before(self, row: Occurrence, name: str, undo: clock.Stamp) -> Any:
        """The value of a field by the last write of it whose clock is before `undo`."""
        best: tuple[clock.Stamp, Any] | None = None
        for made, device, written in Change.objects.filter(
            home_id=self.home.pk, entity=OCCURRENCE, row_id=row.pk, kind=changes.PUT
        ).values_list("clock", "device", "fields"):
            if name not in written:
                continue
            stamp = (clock.parse_clock(made), bytes(device))
            if stamp < undo and (best is None or stamp > best[0]):
                best = (stamp, written[name])
        if best is None:
            return _MISSING
        column = next(f for f in Occurrence._meta.concrete_fields if f.attname == name)
        return None if best[1] is None else column.to_python(best[1])

    # ---------- two open occurrences of one task ----------

    def _settle_open(self) -> None:
        for task_id in sorted({task for _status, task in self.held.values()}, key=str):
            rows = list(Occurrence.objects.filter(task_id=task_id).order_by("pk"))
            open_rows = [row for row in rows if self._status(row) in OPEN_STATUSES]
            if open_rows:
                winner = functools.reduce(self._later, open_rows)
                for row in open_rows:
                    if row is not winner:
                        closed = Status.DONE if row.completed_on else Status.SKIPPED
                        self._repair(row, {"status": closed})
            # What is still held (the winner, or a row deleted since) takes its own status.
            for pk, (status, task) in list(self.held.items()):
                if task == task_id:
                    Occurrence.all_objects.filter(pk=pk).update(status=status)
                    del self.held[pk]

    def _later(self, one: Occurrence, other: Occurrence) -> Occurrence:
        """Of two open occurrences, the one that stays open."""
        if self._follows(one, other):
            return one
        if self._follows(other, one):
            return other
        first = (*self._stamp(one, "task_id"), str(one.pk))
        second = (*self._stamp(other, "task_id"), str(other.pk))
        return one if first > second else other

    def _follows(self, row: Occurrence, earlier: Occurrence) -> bool:
        """Whether `row` follows `earlier`, through any number of `follows`."""
        current = row.follows_id
        for _step in range(MAX_CHAIN):
            if current is None:
                return False
            if current == earlier.pk:
                return True
            current = (
                Occurrence.all_objects.filter(pk=current)
                .values_list("follows_id", flat=True)
                .first()
            )
        return False

    def _done_when_marked(self) -> None:
        """A row a repair closed as skipped whose `completed_on` came after: done, as the rule
        says of a row someone marked done, whatever the order the two arrived in."""
        server = changes.server_device()
        for row in Occurrence.objects.filter(
            pk__in=self._occurrences(), status=Status.SKIPPED, completed_on__isnull=False
        ).order_by("pk"):
            if self._stamp(row, "status")[1] == server:
                self._repair(row, {"status": Status.DONE})

    # ---------- the date of a successor ----------

    def _successor_dates(self) -> None:
        for row in (
            Occurrence.objects.filter(pk__in=self.successors, follows__isnull=False)
            .select_related("task", "follows")
            .order_by("pk")
        ):
            closed = row.follows
            if closed is None or closed.deleted_at is not None:
                continue
            if closed.status not in CLOSED_STATUSES or not closed.status_before:
                # Not closed, or closed by the rule of two open occurrences, which keeps no
                # status before: no closing gave this successor its date.
                continue
            if closed.status == Status.DONE and closed.completed_on is not None:
                done_on = closed.completed_on
            else:
                done_on = day_of(clock.format_clock(self._stamp(closed, "status")[0]))
            task = row.task
            due = recurrence.next_due(
                task.rule, anchor=task.series_start, due_on=closed.due_on, done_on=done_on
            )
            if due is not None and row.due_on != due:
                self._repair(row, {"due_on": due})

    # ---------- what the web derives ----------

    def _visible_from(self) -> None:
        wanted = self._occurrences()
        tasks = self.touched.get(TASK, set())
        rows = Occurrence.all_objects.filter(home_id=self.home.pk).select_related("task")
        found = list(rows.filter(pk__in=wanted)) + list(
            rows.filter(task_id__in=tasks, status__in=OPEN_STATUSES, deleted_at__isnull=True)
        )
        for row in found:
            day = task_services.visible_from_of(row.task, row.due_on)
            if row.visible_from != day:
                Occurrence.all_objects.filter(pk=row.pk).update(visible_from=day)
                row.visible_from = day

    def _movement_effects(self) -> None:
        """The effect of each movement of the articles a push wrote, in the order of clocks.

        An entry adds its quantity, an exit takes it, an inventory makes the stock the count:
        its effect is the count minus the sum of the movements before it (mobile ADR 0003).
        """
        for article in sorted(self.articles, key=str):
            rows = list(Movement.objects.filter(article_id=article))
            clocks = {
                rc.row_id: rc.clocks
                for rc in RowClocks.objects.filter(
                    home_id=self.home.pk, entity=MOVEMENT, row_id__in=[row.pk for row in rows]
                )
            }

            def order(row: Movement, clocks: dict[Any, Any] = clocks) -> tuple[Any, ...]:
                entry = clocks.get(row.pk, {}).get("quantity")
                if entry is None:
                    return (clock.clock_of_instant(row.updated_at), changes.server_device(), "")
                return (clock.parse_clock(entry[0]), clock.device_from_b64(entry[1]), str(row.pk))

            stock = Decimal(0)
            for row in sorted(rows, key=order):
                if row.kind == MovementKind.ENTRY:
                    delta = row.quantity
                elif row.kind == MovementKind.EXIT:
                    delta = -row.quantity
                elif row.kind == MovementKind.INVENTORY:
                    delta = row.quantity - stock
                else:  # a kind of a newer app: no effect until the server knows it
                    delta = Decimal(0)
                stock += delta
                if row.delta != delta:
                    Movement.all_objects.filter(pk=row.pk).update(delta=delta)

    # ---------- the effects of the web, once ----------

    def _effects(self) -> None:
        rows = (
            Occurrence.objects.filter(pk__in=self._occurrences(), status__in=CLOSED_STATUSES)
            .select_related("task")
            .order_by("pk")
        )
        for row in rows:
            if self.start.get(row.pk, _MISSING) == row.status:
                continue
            action = f"occurrence.{row.status}"
            entry = uuid.uuid5(self.home.pk, f"audit:{action}:{row.pk}")
            if not AuditEvent.objects.filter(pk=entry).exists():
                audit.record_object(
                    action,
                    actor=self.user,
                    home_id=self.home.pk,
                    title=row.task.title,
                    event_id=entry,
                )
            if row.status == Status.DONE:
                self._draw(row)
        for draw, closed, movement in self.again:
            closed.refresh_from_db()
            stock_services.take_again_for_task(
                self.user,
                draw.article,
                draw.drawn,
                occurrence=closed,
                movement_id=movement,
                occurred_on=closed.completed_on or task_services.today(),
            )

    def _kept_plans(self) -> None:
        """The plan of the task on each occurrence the push made done; none on one it reopened.

        The server's own fields (docs/adr/0091), as `complete` and `undo` set them on the web:
        kept when the occurrence becomes done, so that a budget changed later does not rewrite
        it, and left as they are by any other write of a done occurrence.
        """
        rows = (
            Occurrence.all_objects.filter(pk__in=self._occurrences(), home_id=self.home.pk)
            .select_related("task")
            .order_by("pk")
        )
        for row in rows:
            if row.deleted_at is not None:
                continue
            if row.status == Status.DONE:
                if self.start.get(row.pk) == Status.DONE:
                    continue  # done before the push: its plan is the one of its closing
                planned = costs.plan_when_done(row.task)
            elif costs.planned_of(row).known:
                planned = costs.NO_PLAN
            else:
                continue
            Occurrence.all_objects.filter(pk=row.pk).update(
                planned_labour=planned.labour, planned_material=planned.material
            )

    def _draw(self, row: Occurrence) -> None:
        """The draws of the materials of a closing that no phone pushed (`draw_for_done`)."""
        if row.completed_on is not None:
            day = row.completed_on
        else:
            day = day_of(clock.format_clock(self._stamp(row, "status")[0]))
        lines = (
            TaskMaterial.objects.filter(task_id=row.task_id, article__deleted_at__isnull=True)
            .select_related("article")
            .order_by("article_id")
        )
        for line in lines:
            draw_id = ids.draw(self.home.pk, row.pk, line.article_id)
            if MaterialDraw.all_objects.filter(pk=draw_id).exists():
                continue
            if RowClocks.objects.filter(home_id=self.home.pk, entity=DRAW, row_id=draw_id).exists():
                continue  # a deletion of it came first: the phone handled it
            taken = stock_services.draw_for_task(
                self.user, line.article, line.quantity, occurrence=row, occurred_on=day
            )
            sync_rows.create_or_revive(
                MaterialDraw(
                    id=draw_id,
                    home_id=self.home.pk,
                    occurrence=row,
                    article=line.article,
                    wanted=line.quantity,
                    drawn=taken,
                    unit_price=line.article.unit_price,
                )
            )

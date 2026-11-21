"""Materials of a task: the articles of the stock it needs, with a quantity (docs/adr/0046).

Called by `services` (and by the web and API views through it); the rules of the stock
itself stay in `housecairn.stock.services`. An article can be listed once per task, only
if it is in the stock of the task's own home.
"""

import datetime
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils.translation import gettext as _

from housecairn.accounts.models import User
from housecairn.audit import services as audit
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.stock import services as stock_services
from housecairn.stock.models import Article
from housecairn.sync import ids
from housecairn.sync import rows as changelog
from housecairn.tasks.models import MaterialDraw, Occurrence, Task, TaskMaterial

AnyUser = User | AnonymousUser

MAX_MATERIALS_PER_TASK = 50


def _require_write(actor: AnyUser, home: Home) -> None:
    if not home_services.can_write(actor, home):
        raise PermissionDenied


# ---------- reading ----------


def materials_of_home(home: Home) -> models.QuerySet[TaskMaterial]:
    """Material lines of the home's tasks, each with `stock_quantity`: what is in stock now."""
    lines: models.QuerySet[TaskMaterial] = (
        TaskMaterial.objects.filter(
            home=home, task__deleted_at__isnull=True, article__deleted_at__isnull=True
        )
        .select_related("article", "task")
        .annotate(stock_quantity=stock_services.quantity_expression("article_id"))
        .order_by("article__name", "created_at", "id")
    )
    return lines


def materials_of(task: Task) -> models.QuerySet[TaskMaterial]:
    return materials_of_home(task.home).filter(task=task)


def tasks_using(article: Article) -> models.QuerySet[Task]:
    """The tasks that list the article, for its page."""
    return Task.objects.filter(
        home=article.home_id,
        materials__article=article,
        materials__deleted_at__isnull=True,
    ).distinct()


# ---------- writing ----------


def _check_article(home: Home, article: Article) -> None:
    if article.home_id != home.id or article.deleted_at is not None:
        raise ValidationError({"article": _("This stock item is not in this home.")})


def _changed(actor: AnyUser, task: Task) -> None:
    audit.record_object(
        "task.updated", actor=actor, home_id=task.home_id, title=task.title, fields=["materials"]
    )


def _positive(quantity: Decimal) -> None:
    if quantity <= 0:
        raise ValidationError({"quantity": _("The quantity must be more than zero.")})


@transaction.atomic
def add_material(actor: AnyUser, task: Task, article: Article, quantity: Decimal) -> TaskMaterial:
    """List an article on the task; refused if it is already listed (change its quantity)."""
    _require_write(actor, task.home)
    _check_article(task.home, article)
    _positive(quantity)
    if TaskMaterial.objects.filter(task=task, article=article).exists():
        raise ValidationError({"article": _("This stock item is already listed on the task.")})
    if TaskMaterial.objects.filter(task=task).count() >= MAX_MATERIALS_PER_TASK:
        raise ValidationError(
            _("At most %(max)d stock items for a task."), params={"max": MAX_MATERIALS_PER_TASK}
        )
    # The id of ADR 0077 (need 2), the same on a phone; a line removed before comes back.
    line = TaskMaterial(
        id=ids.material(task.home_id, task.pk, article.pk),
        home=task.home,
        task=task,
        article=article,
        quantity=quantity,
    )
    line.full_clean(validate_unique=False)  # the id may be that of the removed line
    written: TaskMaterial = changelog.create_or_revive(line)
    _changed(actor, task)
    return written


@transaction.atomic
def update_material(actor: AnyUser, line: TaskMaterial, *, quantity: Decimal) -> TaskMaterial:
    _require_write(actor, line.home)
    _positive(quantity)
    before = changelog.before(line)
    line.quantity = quantity
    line.full_clean()
    line.save()
    changelog.record_saved(line, before)
    _changed(actor, line.task)
    return line


@transaction.atomic
def save_material(actor: AnyUser, task: Task, article: Article, quantity: Decimal) -> TaskMaterial:
    """The quantity of an article on the task: added if it is not listed, else changed."""
    line = TaskMaterial.objects.filter(task=task, article=article).first()
    if line is None:
        return add_material(actor, task, article, quantity)
    return update_material(actor, line, quantity=quantity)


@transaction.atomic
def remove_material(actor: AnyUser, line: TaskMaterial) -> None:
    _require_write(actor, line.home)
    changelog.soft_delete(line)
    _changed(actor, line.task)


@transaction.atomic
def set_materials(
    actor: AnyUser, task: Task, lines: Iterable[tuple[Article, Decimal]]
) -> list[TaskMaterial]:
    """Make the materials of the task exactly `lines`: add, change and remove as needed.

    What the task form sends: the whole list at once. An article given twice is refused.
    """
    _require_write(actor, task.home)
    wanted: dict[Article, Decimal] = {}
    for article, quantity in lines:
        if article in wanted:
            raise ValidationError({"article": _("A stock item is listed once for a task.")})
        wanted[article] = quantity
    existing = {m.article_id: m for m in TaskMaterial.objects.filter(task=task)}
    for article, quantity in wanted.items():
        line = existing.get(article.id)
        if line is None:
            add_material(actor, task, article, quantity)
        elif line.quantity != quantity:
            update_material(actor, line, quantity=quantity)
    for article_id, line in existing.items():
        if article_id not in {a.id for a in wanted}:
            remove_material(actor, line)
    return list(materials_of(task))


# ---------- taking the stock when a task is done (docs/adr/0046) ----------


@dataclass(frozen=True)
class Shortage:
    """An article the stock could not give in full when a task was marked done."""

    article: Article
    wanted: Decimal
    drawn: Decimal

    @property
    def missing(self) -> Decimal:
        return self.wanted - self.drawn


def draws_of(occurrence: Occurrence) -> models.QuerySet[MaterialDraw]:
    lines: models.QuerySet[MaterialDraw] = MaterialDraw.objects.filter(
        occurrence=occurrence, article__deleted_at__isnull=True
    ).select_related("article")
    return lines.order_by("article__name", "id")


def draw_for_done(actor: AnyUser, occurrence: Occurrence, *, on: datetime.date) -> list[Shortage]:
    """Take the planned quantity of each material from the stock; return what was short.

    Called inside the transaction that marks the occurrence done, once its row is locked,
    so that two closings at once cannot both draw. The articles are locked in the order of
    their identifiers: two tasks needing the same articles cannot wait for each other.
    """
    lines = (
        TaskMaterial.objects.filter(task=occurrence.task_id, article__deleted_at__isnull=True)
        .select_related("article")
        .order_by("article_id")
    )
    short: list[Shortage] = []
    for line in lines:
        taken = stock_services.draw_for_task(
            actor, line.article, line.quantity, occurrence=occurrence, occurred_on=on
        )
        # The id of ADR 0077 (need 2): closing again after an undo brings back its draw, at
        # the unit price of now (docs/adr/0090).
        changelog.create_or_revive(
            MaterialDraw(
                id=ids.draw(occurrence.home_id, occurrence.pk, line.article_id),
                home=occurrence.home,
                occurrence=occurrence,
                article=line.article,
                wanted=line.quantity,
                drawn=taken,
                unit_price=line.article.unit_price,
            )
        )
        if taken < line.quantity:
            short.append(Shortage(line.article, line.quantity, taken))
    return short


def return_draws(actor: AnyUser, occurrence: Occurrence, *, on: datetime.date) -> None:
    """Undo the closing: put back what was taken, by entries, and end the draws."""
    for draw in MaterialDraw.objects.filter(occurrence=occurrence).order_by("article_id"):
        if draw.article.deleted_at is None:
            stock_services.give_back_for_task(
                actor, draw.article, draw.drawn, occurrence=occurrence, occurred_on=on
            )
        changelog.soft_delete(draw)


def adjust_draws(
    actor: AnyUser,
    occurrence: Occurrence,
    used: Mapping[UUID, Decimal],
    *,
    on: datetime.date,
) -> list[Shortage]:
    """Set the quantity really used of the articles the closing took (docs/adr/0046).

    Less than what was taken puts the difference back; more takes it, as far as the stock
    allows. An article the closing did not take is refused. Returns what is still short.
    The unit price of each draw stays the one of the closing (docs/adr/0090).
    """
    draws = {d.article_id: d for d in draws_of(occurrence).order_by("article_id")}
    unknown = set(used) - set(draws)
    if unknown:
        raise ValidationError({"used": _("This stock item was not taken by this task.")})
    for quantity in used.values():
        if quantity < 0:
            raise ValidationError({"used": _("The quantity cannot be negative.")})
    short: list[Shortage] = []
    for article_id in sorted(used, key=str):
        draw, wanted = draws[article_id], used[article_id]
        before = changelog.before(draw)
        if wanted < draw.drawn:
            stock_services.give_back_for_task(
                actor, draw.article, draw.drawn - wanted, occurrence=occurrence, occurred_on=on
            )
            draw.drawn = wanted
        elif wanted > draw.drawn:
            draw.drawn += stock_services.draw_for_task(
                actor, draw.article, wanted - draw.drawn, occurrence=occurrence, occurred_on=on
            )
        draw.wanted = wanted
        draw.full_clean()
        draw.save()
        changelog.record_saved(draw, before)
        if draw.drawn < draw.wanted:
            short.append(Shortage(draw.article, draw.wanted, draw.drawn))
    return short

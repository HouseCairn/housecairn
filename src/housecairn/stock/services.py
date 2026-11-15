"""Business rules of stock items, suppliers and movements (docs/adr/0002, 0038).

Web views and API views call these functions; neither holds rules of its own.
Reading is limited to the user's homes by the views (`housecairn.homes.scoping`);
writing is checked here, by role: owners and members write, read-only reads.
"""

import datetime
import logging
from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, SuspiciousOperation, ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.db import models, transaction
from django.db.models.functions import Coalesce
from django.template.defaultfilters import filesizeformat
from django.utils.translation import gettext as _

from housecairn.accounts.models import User
from housecairn.audit import services as audit
from housecairn.core.text import without_control_characters
from housecairn.homes import quotas
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.rooms import conf, images
from housecairn.rooms.models import Item, Room
from housecairn.stock.models import Article, Movement, MovementKind, Supplier, Unit
from housecairn.stock.storage import stock_storage
from housecairn.sync import rows as changelog

if TYPE_CHECKING:
    from housecairn.tasks.models import Occurrence

logger = logging.getLogger(__name__)

AnyUser = User | AnonymousUser

ARTICLE_FIELDS = frozenset(
    {
        "name",
        "manufacturer_reference",
        "ean",
        "dimensions",
        "storage_location",
        "unit",
        "unit_other",
        "threshold",
        "unit_price",
        "notes",
    }
)
SUPPLIER_FIELDS = frozenset({"name", "reference", "url", "last_price"})
_ZERO = Decimal("0")


def _ean_matches(query: str) -> models.Q:
    """A code typed with or without its spaces, and a UPC-A found by its EAN-13 (docs/adr/0083)."""
    from housecairn.barcodes import gtin

    code = gtin.clean(query)
    if not code.isdigit():
        return models.Q(pk__in=[])
    return models.Q(ean__in=sorted(gtin.variants(code))) | models.Q(ean__contains=code)


def _require_write(actor: AnyUser, home: Home) -> None:
    if not home_services.can_write(actor, home):
        raise PermissionDenied


def _tidy_unit(article: Article) -> None:
    """The text of Other is one clean line, and only kept with the unit Other."""
    article.unit_other = (
        " ".join(without_control_characters(article.unit_other).split())
        if article.unit == Unit.OTHER
        else ""
    )


def _apply(instance: models.Model, allowed: frozenset[str], changes: dict[str, Any]) -> None:
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"Cannot change: {', '.join(sorted(unknown))}")
    for field, value in changes.items():
        setattr(instance, field, value)


def plain(quantity: Decimal) -> str:
    """The quantity without trailing zeros: `3`, `0.5`."""
    text = f"{quantity:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


# ---------- reading ----------


def quantity_expression(article: str = "pk") -> Coalesce:
    """The stock of an article as a query expression: the sum of its movements.

    `article` names the column that holds the article's identifier in the query it is
    used in (`pk` on an article, `article_id` on a row that points to one).
    """
    total = (
        Movement.objects.filter(article=models.OuterRef(article))
        .order_by()
        .values("article")
        .annotate(total=models.Sum("delta"))
        .values("total")
    )
    decimal: models.DecimalField[Decimal, Decimal] = models.DecimalField(
        max_digits=12, decimal_places=2
    )
    return Coalesce(
        models.Subquery(total, output_field=decimal),
        models.Value(_ZERO, output_field=decimal),
    )


def articles_of(home: Home) -> models.QuerySet[Article]:
    """Articles of the home, each with `quantity`: the sum of its movements."""
    articles: models.QuerySet[Article] = Article.objects.filter(home=home).annotate(
        quantity=quantity_expression()
    )
    return articles


def articles_of_room(home: Home, room: UUID) -> models.QuerySet[Article]:
    """The articles that concern a room: kept there, fitting its equipment, or its paint.

    Three ways, in this order of meaning: the room is listed on the article, the article is
    compatible with an item of the room, or it is the tin of paint left over in the memory
    of the room (docs/adr/0046).
    """
    concerned = (
        models.Q(rooms=room)
        | models.Q(compatible_items__room=room, compatible_items__deleted_at__isnull=True)
        | models.Q(pk__in=Room.objects.filter(pk=room).values("paint_article"))
    )
    return articles_of(home).filter(concerned).distinct()


def search_articles(
    home: Home,
    query: str = "",
    *,
    low: bool = False,
    item: UUID | None = None,
    room: UUID | None = None,
) -> models.QuerySet[Article]:
    """Articles whose name, manufacturer reference, barcode or supplier matches `query`.

    `low` keeps those at or below their threshold; `item` those compatible with an item;
    `room` those that concern a room (see `articles_of_room`).
    """
    articles = articles_of(home)
    query = query.strip()
    if query:
        suppliers = Supplier.objects.filter(article=models.OuterRef("pk")).filter(
            models.Q(name__icontains=query) | models.Q(reference__icontains=query)
        )
        articles = articles.filter(
            models.Q(name__icontains=query)
            | models.Q(manufacturer_reference__icontains=query)
            | _ean_matches(query)
            | models.Exists(suppliers)
        )
    if low:
        articles = articles.filter(
            threshold__isnull=False,
            quantity__lte=models.F("threshold"),  # type: ignore[misc]
        )
    if item is not None:
        articles = articles.filter(compatible_items=item)
    if room is not None:
        articles = articles.filter(pk__in=articles_of_room(home, room).values("pk"))
    return articles


def is_low(article: Article, quantity: Decimal) -> bool:
    return article.threshold is not None and quantity <= article.threshold


def quantity_of(article: Article) -> Decimal:
    """The stock of the article: the sum of the effects of its movements."""
    total: Decimal | None = Movement.objects.filter(article=article).aggregate(
        total=models.Sum("delta")
    )["total"]
    return total if total is not None else _ZERO


def suppliers_of(home: Home) -> models.QuerySet[Supplier]:
    return Supplier.objects.filter(home=home).select_related("article")


def movements_of(home: Home) -> models.QuerySet[Movement]:
    return Movement.objects.filter(home=home).select_related("article")


# ---------- label photo ----------


def _read_upload(upload: UploadedFile) -> bytes:
    limit = conf.photo_max_bytes()
    too_large = ValidationError(
        _("This file is too large: the limit is %(limit)s."),
        params={"limit": filesizeformat(limit)},
    )
    if upload.size is not None and upload.size > limit:
        raise too_large
    data: bytes = upload.read(limit + 1)
    if len(data) > limit:
        raise too_large
    if not data:
        raise ValidationError(_("This file is empty."))
    return data


def _check_label(upload: UploadedFile) -> images.ProcessedPhoto:
    """Decode and re-encode the upload: nothing of the file sent survives (docs/adr/0030)."""
    try:
        return images.process(_read_upload(upload))
    except ValidationError as exc:
        raise ValidationError({"label": exc.messages}) from exc


def _forget_label(article: Article) -> str:
    """Detach the label from the article; return the name of the file it was."""
    previous = article.label.name if article.label else ""
    article.label = ""
    article.label_content_type = ""
    return previous


def _erase_label_file(name: str) -> None:
    """Remove a label file from the disk. A failure is logged, never raised: it is leftover."""
    if not name:
        return
    try:
        stock_storage.delete(name)
    except (OSError, ValueError, SuspiciousOperation):
        logger.error("Could not erase a label file of the stock: %s", name)


def _write_label(article: Article, processed: images.ProcessedPhoto) -> str:
    """Write the new label file; return the name of the one it replaces ("" if none).

    The old file is not touched here: it goes once the article is saved, so that a save
    that fails leaves the old label in place, and the new file is removed.
    """
    previous = article.label.name if article.label else ""
    try:
        # A new label replaces the old one: one more file only when there was none.
        with quotas.room_for(
            article.home, size=len(processed.data), new_files=0 if previous else 1
        ):
            article.label.save(processed.extension, ContentFile(processed.data), save=False)
    except ValidationError as exc:
        raise ValidationError({"label": exc.messages}) from exc
    article.label_content_type = processed.content_type
    return previous


# ---------- articles ----------


def _check_items(home: Home, items: Iterable[Item]) -> list[Item]:
    chosen = list(items)
    if any(item.home_id != home.id for item in chosen):
        raise ValidationError({"compatible_items": _("This item is not in this home.")})
    return chosen


def _check_rooms(home: Home, rooms: Iterable[Room]) -> list[Room]:
    chosen = list(rooms)
    if any(room.home_id != home.id for room in chosen):
        raise ValidationError({"rooms": _("This room is not in this home.")})
    return chosen


@transaction.atomic
def create_article(
    actor: AnyUser,
    home: Home,
    *,
    compatible_items: Iterable[Item] = (),
    rooms: Iterable[Room] = (),
    label: UploadedFile | None = None,
    **fields: Any,
) -> Article:
    _require_write(actor, home)
    article = Article(home=home)
    _apply(article, ARTICLE_FIELDS, fields)
    _tidy_unit(article)
    article.full_clean(exclude=["label"])
    items = _check_items(home, compatible_items)
    chosen_rooms = _check_rooms(home, rooms)
    processed = _check_label(label) if label is not None else None
    if processed is not None:
        _write_label(article, processed)
    try:
        article.save()
        changelog.record_save(Article, article.pk, None)
        changelog.set_links(article, "compatible_items", items)
        changelog.set_links(article, "rooms", chosen_rooms)
    except Exception:
        if processed is not None:
            _erase_label_file(article.label.name)
        raise
    audit.record_object("article.created", actor=actor, home_id=home.id, title=article.name)
    return article


@transaction.atomic
def update_article(
    actor: AnyUser,
    article: Article,
    *,
    compatible_items: Iterable[Item] | None = None,
    rooms: Iterable[Room] | None = None,
    label: UploadedFile | None = None,
    clear_label: bool = False,
    label_attribution: dict[str, str] | None = None,
    **changes: Any,
) -> Article:
    """Change the article; `label` replaces the photo, `clear_label` removes it.

    `label_attribution` credits a label photo that came from an open database (ADR 0083).
    """
    _require_write(actor, article.home)
    before = changelog.read(Article, article.pk)
    _apply(article, ARTICLE_FIELDS, changes)
    _tidy_unit(article)
    article.full_clean(exclude=["label"])
    items = None if compatible_items is None else _check_items(article.home, compatible_items)
    chosen_rooms = None if rooms is None else _check_rooms(article.home, rooms)
    processed = _check_label(label) if label is not None else None
    previous = ""
    if processed is not None:
        previous = _write_label(article, processed)
        article.label_attribution = dict(label_attribution or {})
    elif clear_label:
        previous = _forget_label(article)
        article.label_attribution = {}
    try:
        article.save()
        changelog.record_save(Article, article.pk, before)
        if items is not None:
            changelog.set_links(article, "compatible_items", items)
        if chosen_rooms is not None:
            changelog.set_links(article, "rooms", chosen_rooms)
    except Exception:
        if processed is not None:
            _erase_label_file(article.label.name)
        raise
    _erase_label_file(previous)
    audit.record_object(
        "article.updated",
        actor=actor,
        home_id=article.home_id,
        title=article.name,
        fields=sorted(
            [
                *changes,
                *(["compatible_items"] if items is not None else []),
                *(["rooms"] if chosen_rooms is not None else []),
                *(["label"] if processed is not None or clear_label else []),
            ]
        ),
    )
    return article


@transaction.atomic
def delete_article(actor: AnyUser, article: Article) -> None:
    """Soft-delete the article with its suppliers and movements."""
    _require_write(actor, article.home)
    changelog.soft_delete_all(Supplier.objects.filter(article=article))
    changelog.soft_delete_all(Movement.objects.filter(article=article))
    changelog.soft_delete(article)
    audit.record_object("article.deleted", actor=actor, home_id=article.home_id, title=article.name)


# ---------- suppliers ----------


def _prefill_unit_price(article: Article, supplier: Supplier) -> None:
    """A stock item with no unit price takes the last price paid at a supplier (docs/adr/0062).

    Only a price that is empty is filled: one the person typed is never replaced. It is a
    first value to correct, not a link: the supplier keeps its own price.
    """
    current: Decimal | None = article.unit_price
    paid: Decimal | None = supplier.last_price
    if current is None and paid is not None:
        before = changelog.read(Article, article.pk)
        article.unit_price = paid
        article.save(update_fields=["unit_price", "updated_at"])
        changelog.record_save(Article, article.pk, before)


@transaction.atomic
def create_supplier(actor: AnyUser, article: Article, **fields: Any) -> Supplier:
    _require_write(actor, article.home)
    supplier = Supplier(home=article.home, article=article)
    _apply(supplier, SUPPLIER_FIELDS, fields)
    supplier.full_clean()
    supplier.save()
    changelog.record_save(Supplier, supplier.pk, None)
    _prefill_unit_price(article, supplier)
    audit.record_object(
        "supplier.created", actor=actor, home_id=supplier.home_id, title=supplier.name
    )
    return supplier


@transaction.atomic
def update_supplier(actor: AnyUser, supplier: Supplier, **changes: Any) -> Supplier:
    _require_write(actor, supplier.home)
    before: Decimal | None = supplier.last_price
    state = changelog.read(Supplier, supplier.pk)
    _apply(supplier, SUPPLIER_FIELDS, changes)
    supplier.full_clean()
    supplier.save()
    changelog.record_save(Supplier, supplier.pk, state)
    if supplier.last_price != before:  # only a last price that really changed prefills
        _prefill_unit_price(supplier.article, supplier)
    audit.record_object(
        "supplier.updated",
        actor=actor,
        home_id=supplier.home_id,
        title=supplier.name,
        fields=sorted(changes),
    )
    return supplier


@transaction.atomic
def delete_supplier(actor: AnyUser, supplier: Supplier) -> None:
    _require_write(actor, supplier.home)
    changelog.soft_delete(supplier)
    audit.record_object(
        "supplier.deleted", actor=actor, home_id=supplier.home_id, title=supplier.name
    )


# ---------- movements ----------


def _lock_stock(article: Article) -> Decimal:
    """Lock the article row and read its stock, so that no two movements read the same one."""
    Article.objects.select_for_update().get(pk=article.pk)
    return quantity_of(article)


def _append(
    article: Article,
    *,
    kind: str,
    quantity: Decimal,
    delta: Decimal,
    occurred_on: datetime.date | None,
    note: str = "",
    occurrence: "Occurrence | None" = None,
) -> Movement:
    movement = Movement(
        home=article.home,
        article=article,
        kind=kind,
        quantity=quantity,
        delta=delta,
        note=note,
        occurrence=occurrence,
    )
    if occurred_on is not None:
        movement.occurred_on = occurred_on
    try:
        movement.full_clean()
    except ValidationError as exc:
        # `delta` is worked out, not typed: a count too far from the stock to be recorded
        # is the person's quantity to correct.
        errors = exc.message_dict
        if "delta" in errors:
            del errors["delta"]
            errors.setdefault("quantity", []).append(
                _("The difference with the stock is too large to be recorded.")
            )
        raise ValidationError(errors) from exc
    movement.save()
    changelog.record_save(Movement, movement.pk, None)
    return movement


@transaction.atomic
def record_movement(
    actor: AnyUser,
    article: Article,
    *,
    kind: str,
    quantity: Decimal,
    occurred_on: datetime.date | None = None,
    note: str = "",
) -> Movement:
    """Add a line to the ledger of the article.

    An entry adds `quantity`, an exit takes it (never more than there is), an inventory
    states the quantity counted and records the difference with the stock until then.
    """
    _require_write(actor, article.home)
    if kind not in MovementKind.values:
        raise ValidationError({"kind": _("Choose entry, exit or inventory.")})
    if kind != MovementKind.INVENTORY and quantity <= _ZERO:
        raise ValidationError({"quantity": _("The quantity must be more than zero.")})
    current = _lock_stock(article)
    if kind == MovementKind.ENTRY:
        delta = quantity
    elif kind == MovementKind.EXIT:
        if quantity > current:
            raise ValidationError(
                {"quantity": _("Only %(available)s in stock.") % {"available": plain(current)}}
            )
        delta = -quantity
    else:
        delta = quantity - current
    return _append(
        article, kind=kind, quantity=quantity, delta=delta, occurred_on=occurred_on, note=note
    )


@transaction.atomic
def draw_for_task(
    actor: AnyUser,
    article: Article,
    wanted: Decimal,
    *,
    occurrence: "Occurrence",
    occurred_on: datetime.date | None = None,
) -> Decimal:
    """Take up to `wanted` of the article for a task that is done; return what was taken.

    The one place where an exit is not refused for lack of stock (docs/adr/0046): marking a
    task done is never blocked, so the stock goes down to zero and the caller says how much
    is missing. Locks the article like `record_movement`.
    """
    _require_write(actor, article.home)
    if wanted <= _ZERO:
        return _ZERO
    available = _lock_stock(article)
    taken = min(wanted, max(available, _ZERO))
    if taken > _ZERO:
        _append(
            article,
            kind=MovementKind.EXIT,
            quantity=taken,
            delta=-taken,
            occurred_on=occurred_on,
            occurrence=occurrence,
        )
    return taken


@transaction.atomic
def give_back_for_task(
    actor: AnyUser,
    article: Article,
    quantity: Decimal,
    *,
    occurrence: "Occurrence",
    occurred_on: datetime.date | None = None,
) -> None:
    """Put back what a task took (it was undone, or less was used): an entry, never an erasure."""
    _require_write(actor, article.home)
    if quantity <= _ZERO:
        return
    _lock_stock(article)
    _append(
        article,
        kind=MovementKind.ENTRY,
        quantity=quantity,
        delta=quantity,
        occurred_on=occurred_on,
        occurrence=occurrence,
    )

"""Rules of QR labels (docs/adr/0002, 0047).

Reading and printing are open to every member of the home; making, revoking and
regenerating a label need a role that writes. Opening a label by its code answers one
way only, "nothing here", for a code that was never issued, one that was revoked, one of
an object since deleted, and one of a home the person is not in (`resolve`).
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from housecairn.accounts.models import User
from housecairn.audit import services as audit
from housecairn.core.links import external_url
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.labels import printing
from housecairn.labels.models import Label, LabelKind, is_code_shaped
from housecairn.rooms.models import Item, Room
from housecairn.stock.models import Article

AnyUser = User | AnonymousUser

# Related fields to load with a label, so that its name and place cost no query.
_RELATED = ("room", "item__room", "article")


def _require_write(actor: AnyUser, home: Home) -> None:
    if home.is_example:
        raise PermissionDenied  # the example home of the guide has no QR label (docs/adr/0084)
    if not home_services.can_write(actor, home):
        raise PermissionDenied


# ---------- reading ----------


def labels_of(home: Home) -> models.QuerySet[Label]:
    """The active labels of the home whose object still exists."""
    return (
        Label.objects.filter(home=home, revoked_at__isnull=True)
        .filter(
            models.Q(room__deleted_at__isnull=True, kind=LabelKind.ROOM)
            | models.Q(item__deleted_at__isnull=True, kind=LabelKind.ITEM)
            | models.Q(article__deleted_at__isnull=True, kind=LabelKind.ARTICLE)
        )
        .select_related(*_RELATED)
        .order_by("kind", "created_at", "id")
    )


@dataclass(frozen=True)
class Entry:
    """One object of the home that can carry a label, with its active label if it has one."""

    kind: str
    id: uuid.UUID
    name: str
    place: str
    label: Label | None

    @property
    def key(self) -> str:
        """What a checkbox of the print screen carries."""
        return f"{self.kind}:{self.id}"


def entries_of(home: Home) -> list[Entry]:
    """Every room, item and stock item of the home, in that order, with its label."""
    active = {(label.kind, label.target_id): label for label in labels_of(home)}
    rows: list[Entry] = []
    for room in Room.objects.filter(home=home).order_by("position", "created_at", "id"):
        rows.append(Entry(LabelKind.ROOM, room.id, room.name, "", active.get(("room", room.id))))
    for item in (
        Item.objects.filter(home=home)
        .select_related("room")
        .order_by("room__position", "room__name", "name", "id")
    ):
        rows.append(
            Entry(LabelKind.ITEM, item.id, item.name, item.room.name, active.get(("item", item.id)))
        )
    for article in Article.objects.filter(home=home).order_by("name", "id"):
        rows.append(
            Entry(
                LabelKind.ARTICLE,
                article.id,
                article.name,
                article.storage_location,
                active.get(("article", article.id)),
            )
        )
    return rows


def get_label(home: Home, label_id: uuid.UUID) -> Label | None:
    return labels_of(home).filter(id=label_id).first()


def target_of(home: Home, kind: str, target_id: uuid.UUID) -> Room | Item | Article:
    """The room, item or stock item of this home; ValidationError when there is none."""
    model: Any = {"room": Room, "item": Item, "article": Article}.get(kind)
    found = model.objects.filter(home=home, id=target_id).first() if model else None
    if found is None:
        raise ValidationError({"target": _("Nothing with this identifier in this home.")})
    return found  # type: ignore[no-any-return]


def active_label_for(target: Room | Item | Article) -> Label | None:
    kind = _kind_of(target)
    return (
        Label.objects.filter(**{kind: target}, revoked_at__isnull=True)
        .select_related(*_RELATED)
        .first()
    )


def _kind_of(target: Room | Item | Article) -> str:
    return {Room: "room", Item: "item", Article: "article"}[type(target)]


# ---------- writing ----------


def create_label(
    actor: AnyUser, home: Home, *, kind: str, target_id: uuid.UUID
) -> tuple[Label, bool]:
    """The active label of an object, made if it has none: `(label, created)`."""
    _require_write(actor, home)
    target = target_of(home, kind, target_id)
    existing = active_label_for(target)
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic():
            label = Label.objects.create(home=home, kind=kind, **{kind: target})
    except IntegrityError:  # another request made it first
        found = active_label_for(target)
        if found is None:  # pragma: no cover - the constraint that fired is gone again
            raise
        return found, False
    return Label.objects.select_related(*_RELATED).get(pk=label.pk), True


@transaction.atomic
def revoke_label(actor: AnyUser, label: Label) -> None:
    """Stop the sticker from opening anything. The row stays, so its code is never reused."""
    _require_write(actor, label.home)
    Label.objects.filter(pk=label.pk, revoked_at__isnull=True).update(revoked_at=timezone.now())
    audit.record_object("label.revoked", actor=actor, home_id=label.home_id, title=label.name)


@transaction.atomic
def regenerate_label(actor: AnyUser, label: Label) -> Label:
    """Revoke the label and make another for the same object, with a new code."""
    _require_write(actor, label.home)
    kind, target = label.kind, label.target
    Label.objects.filter(pk=label.pk, revoked_at__isnull=True).update(revoked_at=timezone.now())
    fresh = Label.objects.create(home=label.home, kind=kind, **{kind: target})
    audit.record_object("label.regenerated", actor=actor, home_id=label.home_id, title=label.name)
    result: Label = Label.objects.select_related(*_RELATED).get(pk=fresh.pk)
    return result


# ---------- opening a label ----------


def resolve(code: str, homes: models.QuerySet[Home]) -> Label | None:
    """The active label with this code, if its home is one of `homes`; else None.

    None is the only answer for every reason there is not one, so that nobody learns, from
    the answer, whether a code exists, was revoked, or belongs to a home they are not in.
    """
    if not is_code_shaped(code):
        return None
    label: Label | None = (
        Label.objects.filter(code=code, revoked_at__isnull=True, home__in=homes)
        .select_related("home", *_RELATED)
        .first()
    )
    if label is None or not label.target_alive:
        return None
    return label


def target_url(label: Label) -> str:
    """The page of the object, which every member of the home may open."""
    if label.kind == LabelKind.ROOM:
        return reverse("rooms:detail", kwargs={"home_id": label.home_id, "room_id": label.room_id})
    if label.kind == LabelKind.ITEM:
        return reverse(
            "rooms:item-detail", kwargs={"home_id": label.home_id, "item_id": label.item_id}
        )
    return reverse(
        "stock:detail", kwargs={"home_id": label.home_id, "article_id": label.article_id}
    )


def address(base_url: str, label: Label) -> str:
    """What the QR code holds: the public address of the instance and the code, nothing else."""
    return external_url(reverse("label-open", kwargs={"code": label.code}), base_url)


# ---------- printing ----------


def labels_to_print(
    actor: AnyUser, home: Home, selection: Iterable[tuple[str, uuid.UUID]]
) -> list[Label]:
    """The label of each selected object. A person who writes gets one made for an object
    that has none; a reader only prints labels that exist."""
    can_write = home_services.can_write(actor, home)
    found: list[Label] = []
    wanted = list(dict.fromkeys(selection))
    # Before any label is made: a selection that cannot be printed makes none.
    if len(wanted) > printing.MAX_LABELS:
        raise ValidationError(
            {"labels": _("Choose between 1 and %(max)d labels.") % {"max": printing.MAX_LABELS}}
        )
    for kind, target_id in wanted:
        if can_write:
            label, _created = create_label(actor, home, kind=kind, target_id=target_id)
        else:
            label = active_label_for(target_of(home, kind, target_id))  # type: ignore[assignment]
            if label is None:
                raise PermissionDenied
        found.append(label)
    return found


def render_pdf(base_url: str, labels: Iterable[Label], layout_code: str) -> bytes:
    """The PDF of these labels. ValidationError for an unknown layout or a bad number."""
    layout = printing.LAYOUTS.get(layout_code)
    if layout is None:
        raise ValidationError({"layout": _("Choose one of the layouts offered.")})
    stickers = [
        printing.Sticker(
            name=printing.printable(label.name, limit=printing.MAX_NAME_CHARACTERS),
            place=printing.printable(label.place, limit=printing.MAX_NAME_CHARACTERS),
            url=address(base_url, label),
        )
        for label in labels
    ]
    if not 1 <= len(stickers) <= printing.MAX_LABELS:
        raise ValidationError(
            {"labels": _("Choose between 1 and %(max)d labels.") % {"max": printing.MAX_LABELS}}
        )
    return printing.render(stickers, layout)

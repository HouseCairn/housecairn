"""Rooms, their memory, the items in them and their photos (docs/adr/0029, 0030)."""

import datetime
from typing import Any

from django.core.exceptions import ValidationError
from dateutil.relativedelta import relativedelta
from django.core.validators import MaxLengthValidator, MaxValueValidator, MinValueValidator
from django.core.validators import URLValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from housecairn.homes.models import HomeOwnedModel
from housecairn.rooms.storage import photo_storage

NOTES_MAX_LENGTH = 4000
MAX_LINKS = 10
_WEB_URL = URLValidator(schemes=["http", "https"])


class WebUrlField(models.URLField[str, str]):
    """A link to a web page: only http and https, never `javascript:` or `file:`."""

    default_validators = [_WEB_URL]

    def formfield(
        self, form_class: Any = None, choices_form_class: Any = None, **kwargs: Any
    ) -> Any:
        # Django 6 assumes https for an address typed without a scheme; say so now.
        return super().formfield(**{"assume_scheme": "https", **kwargs})


def notes_field(label: Any) -> models.TextField[str, str]:
    return models.TextField(label, blank=True, validators=[MaxLengthValidator(NOTES_MAX_LENGTH)])


def validate_links(value: object) -> None:
    """A list of at most `MAX_LINKS` objects `{"url": ..., "label": ...}` (label optional)."""
    if not isinstance(value, list):
        raise ValidationError(_("Links must be a list."))
    if len(value) > MAX_LINKS:
        raise ValidationError(_("At most %(max)d links."), params={"max": MAX_LINKS})
    for entry in value:
        if not isinstance(entry, dict) or set(entry) - {"url", "label"} or "url" not in entry:
            raise ValidationError(_("Each link has a url and, optionally, a label."))
        url, label = entry["url"], entry.get("label", "")
        if not isinstance(url, str) or not isinstance(label, str):
            raise ValidationError(_("Each link has a url and, optionally, a label."))
        if len(url) > 500 or len(label) > 100:
            raise ValidationError(_("A link or its label is too long."))
        _WEB_URL(url)


class RoomKind(models.TextChoices):
    LIVING = "living", _("Living room")
    KITCHEN = "kitchen", _("Kitchen")
    BATHROOM = "bathroom", _("Bathroom")
    BEDROOM = "bedroom", _("Bedroom")
    HALLWAY = "hallway", _("Hallway")
    OFFICE = "office", _("Office")
    UTILITY = "utility", _("Utility room")
    GARAGE = "garage", _("Garage")
    CELLAR = "cellar", _("Cellar")
    ATTIC = "attic", _("Attic")
    OUTDOOR = "outdoor", _("Outdoor")
    OTHER = "other", _("Other")


class Room(HomeOwnedModel):
    """A room, or an area such as the garden: the place things are attached to."""

    name = models.CharField(_("name"), max_length=120)
    kind = models.CharField(_("type"), max_length=16, choices=RoomKind.choices, default="other")
    position = models.PositiveIntegerField(_("position"), default=0)
    notes = notes_field(_("notes"))
    # Room memory (what was used and fitted here); everything is optional.
    paint_colour = models.CharField(_("paint colour"), max_length=120, blank=True)
    paint_brand = models.CharField(_("paint brand"), max_length=120, blank=True)
    paint_reference = models.CharField(_("paint reference"), max_length=120, blank=True)
    paint_batch = models.CharField(_("paint batch"), max_length=120, blank=True)
    # The tin left over is an item of the stock (docs/adr/0046): its quantity and where it is
    # are read from there, so they are never out of date.
    paint_article = models.ForeignKey(
        "stock.Article",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("paint left in stock"),
    )
    floor_covering = models.CharField(_("floor covering"), max_length=200, blank=True)
    wall_covering = models.CharField(_("wall covering"), max_length=200, blank=True)
    memory_notes = notes_field(_("memory notes"))

    class Meta:
        verbose_name = _("room")
        verbose_name_plural = _("rooms")
        ordering = ["position", "created_at", "id"]
        indexes = [models.Index(fields=["home", "position"], name="room_home_position")]

    def __str__(self) -> str:
        return self.name

    @property
    def has_memory(self) -> bool:
        return any(
            getattr(self, name)
            for name in (
                "paint_colour",
                "paint_brand",
                "paint_reference",
                "paint_batch",
                "paint_article_id",
                "floor_covering",
                "wall_covering",
                "memory_notes",
            )
        )


class ItemKind(models.TextChoices):
    EQUIPMENT = "equipment", _("Equipment")
    SURFACE = "surface", _("Surface")


class WarrantyUnit(models.TextChoices):
    MONTH = "month", _("months")
    YEAR = "year", _("years")


class WarrantyProvider(models.TextChoices):
    """Who gives the warranty of an item (docs/adr/0080)."""

    MANUFACTURER = "manufacturer", _("The manufacturer")
    SHOP = "shop", _("The shop")


# A warranty of more than 99 months or years is a typing error.
MAX_WARRANTY_DURATION = 99


def warranty_end(start: datetime.date | None, duration: int | None, unit: str) -> datetime.date | None:
    """The day a warranty of `duration` months or years from `start` ends; None without one.

    The same date that many months or years later, the last day of the month when that month is
    shorter (31 January plus one month ends on 28 or 29 February).
    """
    if start is None or not duration or unit not in WarrantyUnit.values:
        return None
    if unit == WarrantyUnit.YEAR:
        return start + relativedelta(years=duration)
    return start + relativedelta(months=duration)


class Item(HomeOwnedModel):
    """A piece of equipment or a surface (a wall, a floor) in a room."""

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(_("name"), max_length=120)
    kind = models.CharField(
        _("type"), max_length=16, choices=ItemKind.choices, default=ItemKind.EQUIPMENT
    )
    brand = models.CharField(_("brand"), max_length=120, blank=True)
    model = models.CharField(_("model"), max_length=120, blank=True)
    serial_number = models.CharField(_("serial number"), max_length=120, blank=True)
    purchased_on = models.DateField(_("purchased on"), null=True, blank=True)
    installed_on = models.DateField(_("installed on"), null=True, blank=True)
    # The warranty of the maker or of the shop, apart from the maintenance contract
    # (docs/adr/0080). With a duration, its end is worked out from its start, or from the
    # purchase date; without one, the end typed is kept as it is (the items made before).
    warranty_duration = models.PositiveSmallIntegerField(
        _("warranty: duration"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_WARRANTY_DURATION)],
    )
    warranty_duration_unit = models.CharField(
        _("warranty: in"),
        max_length=8,
        choices=WarrantyUnit.choices,
        blank=True,
        default=WarrantyUnit.YEAR,
    )
    warranty_provider = models.CharField(
        _("warranty: given by"), max_length=16, choices=WarrantyProvider.choices, blank=True
    )
    warranty_provider_name = models.CharField(
        _("warranty: name of the manufacturer or shop"), max_length=120, blank=True
    )
    warranty_starts_on = models.DateField(_("warranty: starts on"), null=True, blank=True)
    warranty_ends_on = models.DateField(_("warranty ends on"), null=True, blank=True)
    # Who services it: one of the contractors of the home (docs/adr/0058). Deleting the
    # contractor clears the link (`tasks.services.delete_contractor`), the number and the
    # link of the contract stay.
    contract_contractor = models.ForeignKey(
        "tasks.Contractor",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="maintained_items",
        verbose_name=_("maintenance contract: provider"),
        help_text=_("Who services it, from the contractors of the home. Example: Acme Heating."),
    )
    contract_number = models.CharField(
        _("maintenance contract: number"),
        max_length=120,
        blank=True,
        help_text=_("The number to quote when you call them. Example: C-2024-0187."),
    )
    contract_url = WebUrlField(_("contract link"), max_length=500, blank=True)
    links = models.JSONField(_("links"), default=list, blank=True, validators=[validate_links])
    notes = notes_field(_("notes"))

    class Meta:
        verbose_name = _("item")
        verbose_name_plural = _("items")
        ordering = ["name", "created_at", "id"]
        indexes = [models.Index(fields=["room", "name"], name="item_room_name")]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.room_id and self.home_id and self.room.home_id != self.home_id:
            raise ValidationError({"room": _("This room is not in this home.")})
        provider = self.contract_contractor if self.contract_contractor_id else None
        # A form checks the contractor against the list of its home; the home is known here
        # once a service has set it.
        if provider is not None and (
            provider.is_deleted or (self.home_id and provider.home_id != self.home_id)
        ):
            raise ValidationError(
                {"contract_contractor": _("This contractor is not in this home.")}
            )

    @property
    def warranty_start(self) -> datetime.date | None:
        """The start of the warranty: the date given, else the purchase date."""
        return self.warranty_starts_on or self.purchased_on

    def computed_warranty_end(self) -> datetime.date | None:
        """The end worked out from the start and the duration, or None when one is missing."""
        return warranty_end(
            self.warranty_start, self.warranty_duration, self.warranty_duration_unit
        )

    def warranty_state(self, today: datetime.date) -> str:
        """`none`, `active` or `expired`, at the date `today`."""
        if self.warranty_ends_on is None:
            return "none"
        return "active" if self.warranty_ends_on >= today else "expired"


class PhotoCategory(models.TextChoices):
    """The categories of a photo, one list for everything (docs/adr/0045)."""

    GENERAL = "general", _("General")
    BEHIND_WALLS = "behind_walls", _("Behind the walls")
    BEFORE = "before", _("Before the work")
    AFTER = "after", _("After the work")
    OTHER = "other", _("Other")


# What "behind the walls" is for, shown wherever a photo gets that category.
BEHIND_WALLS_HELP = _(
    "Pipes and cables photographed before the wall is closed, to know where not to drill."
)


def photo_path(photo: "Photo", filename: str) -> str:
    """Storage name: home and photo identifiers only, never a name chosen by the sender."""
    return f"{photo.home_id}/{photo.id}{filename}"


class Photo(HomeOwnedModel):
    """A photo of a room or of an item, re-encoded on upload (docs/adr/0030)."""

    room = models.ForeignKey(
        Room, null=True, blank=True, on_delete=models.CASCADE, related_name="photos"
    )
    item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.CASCADE, related_name="photos"
    )
    category = models.CharField(
        _("category"), max_length=16, choices=PhotoCategory.choices, default=PhotoCategory.GENERAL
    )
    caption = models.CharField(_("caption"), max_length=200, blank=True)
    image = models.FileField(
        storage=photo_storage, upload_to=photo_path, max_length=200, editable=False
    )
    thumbnail = models.FileField(
        storage=photo_storage, upload_to=photo_path, max_length=200, editable=False
    )
    content_type = models.CharField(max_length=32, editable=False)
    size_bytes = models.PositiveIntegerField(editable=False)
    width = models.PositiveIntegerField(editable=False)
    height = models.PositiveIntegerField(editable=False)

    class Meta:
        verbose_name = _("photo")
        verbose_name_plural = _("photos")
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(room__isnull=False, item__isnull=True)
                    | models.Q(room__isnull=True, item__isnull=False)
                ),
                name="photo_room_xor_item",
            )
        ]

    def __str__(self) -> str:
        return self.caption or str(self.id)

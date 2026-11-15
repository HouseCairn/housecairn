"""Forms of the room and item screens. Validation only; rules live in `services`."""

from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from housecairn.core.forms import LabelAboveMixin
from housecairn.core.text import without_control_characters
from housecairn.rooms.models import (
    Item,
    PhotoCategory,
    Room,
    RoomKind,
    WarrantyUnit,
    validate_links,
)
from housecairn.stock.models import Article
from housecairn.tasks import services as tasks_services
from housecairn.tasks.forms.contractor import clean_new_contractor, new_contractor_fields
from housecairn.tasks.forms.fields import AddInPlaceChoiceField
from housecairn.tasks.models import Contractor

BRAND_LIST_ID = "brand-suggestions"
_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class RoomForm(LabelAboveMixin, forms.ModelForm[Room]):
    """The type comes first and fills the name in, which stays editable (docs/adr/0045).

    `static/js/room-form.js` does the filling as the type is chosen; without script, a name
    left empty takes the label of the type. "Other" has no label to offer: the name is then
    the room's whole description.
    """

    class Meta:
        model = Room
        fields = ["kind", "name", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}
        help_texts = {
            "kind": _(
                "Sets the icon and the filters of the room and, later, the task templates "
                "offered for it. Choose Other if none fits."
            ),
            "name": _("Fills in by itself from the type; change it to name the room."),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["name"].required = False
        if self.instance._state.adding:
            kind_field = self.fields["kind"]
            assert isinstance(kind_field, forms.ChoiceField)  # noqa: S101 - narrows the type
            kind_field.choices = [("", _("Choose a type")), *RoomKind.choices]
            self.initial["kind"] = ""

    def clean(self) -> dict[str, Any]:
        data: dict[str, Any] = super().clean() or {}
        kind = data.get("kind")
        # Clean the name first: one made only of invisible characters is an empty one.
        if "name" in data:
            data["name"] = without_control_characters(data["name"] or "").strip()
        if not data.get("name") and kind and "name" not in self.errors:
            if kind == RoomKind.OTHER:
                self.add_error("name", _("Give the room a name."))
            else:
                data["name"] = str(RoomKind(kind).label)
        return data


class RoomMemoryForm(LabelAboveMixin, forms.ModelForm[Room]):
    class Meta:
        model = Room
        fields = [
            "paint_colour",
            "paint_brand",
            "paint_reference",
            "paint_batch",
            "paint_article",
            "floor_covering",
            "wall_covering",
            "memory_notes",
        ]
        widgets = {"memory_notes": forms.Textarea(attrs={"rows": 4})}
        help_texts = {
            "paint_colour": _("The colour as you would ask for it. Example: warm white."),
            "paint_brand": _("The make of the paint. Example: Acme Paints."),
            "paint_reference": _(
                "The reference or the colour code on the tin, to order the same one. "
                "Example: AC-1042."
            ),
            "paint_batch": _(
                "The batch number printed on the tin; shades can differ from one batch to "
                "another. Example: L23-0457."
            ),
            "paint_article": _(
                "The tin left over, from your stock: its quantity and where it is are shown "
                "here. Example: Sage wall paint, 2 litres. Add it to the stock first if it is "
                "not there yet."
            ),
            "floor_covering": _(
                "What covers the floor, with its make and reference if you know them. "
                "Example: oak parquet, matt varnish."
            ),
            "wall_covering": _(
                "What covers the walls, under the paint if any. Example: lining paper, then paint."
            ),
            "memory_notes": _(
                "What the walls and floor hide, or anything else to remember about the work "
                "done. Example: water pipe along the north wall, 30 cm above the floor."
            ),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        paint = self.fields["paint_article"]
        assert isinstance(paint, forms.ModelChoiceField)  # noqa: S101 - narrows the type
        paint.queryset = Article.objects.filter(home=self.instance.home_id)
        paint.empty_label = "—"


def links_to_text(links: list[dict[str, str]]) -> str:
    return "\n".join(f"{link['url']} {link.get('label', '')}".strip() for link in links)


def text_to_links(text: str) -> list[dict[str, str]]:
    """One link per line: the address, then an optional label."""
    links: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        url, _sep, label = line.partition(" ")
        links.append({"url": url, "label": label.strip()} if label.strip() else {"url": url})
    return links


_NEW_CONTRACTOR = new_contractor_fields()


class ItemForm(LabelAboveMixin, forms.ModelForm[Item]):
    """The item's own fields; `room` is a choice among the rooms of its home."""

    room = forms.ModelChoiceField(
        label=_("Room"),
        queryset=Room.objects.none(),
        help_text=_("Where the item is. Choose another room to move it."),
    )
    # The contractors of the home, the last choice adding one in place (the fields below,
    # read only with that choice; docs/developer/design-guide.md).
    contract_contractor = AddInPlaceChoiceField(
        label=_("Maintenance contract: provider"),
        queryset=Contractor.objects.none(),
        required=False,
        new_label=_("Add a contractor…"),
        help_text=_("Who services it, from the contractors of the home. Example: Acme Heating."),
    )
    new_contractor_name = _NEW_CONTRACTOR["new_contractor_name"]
    new_contractor_trade = _NEW_CONTRACTOR["new_contractor_trade"]
    new_contractor_trade_other = _NEW_CONTRACTOR["new_contractor_trade_other"]
    links = forms.CharField(
        label=_("Links"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("One per line: the address, then a label if you like."),
    )

    class Meta:
        model = Item
        fields = [
            "room",
            "name",
            "kind",
            "brand",
            "model",
            "serial_number",
            "ean",
            "purchased_on",
            "installed_on",
            "warranty_duration",
            "warranty_duration_unit",
            "warranty_provider",
            "warranty_provider_name",
            "warranty_starts_on",
            "warranty_ends_on",
            "contract_contractor",
            "contract_number",
            "contract_url",
            "links",
            "notes",
        ]
        widgets = {
            "purchased_on": _DATE,
            "installed_on": _DATE,
            "warranty_starts_on": _DATE,
            "warranty_ends_on": _DATE,
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "warranty_duration": _(
                "The warranty of the manufacturer or of the shop, in months or years."
            ),
            "warranty_provider_name": _("Example: Acme, or the shop where you bought it."),
            "warranty_starts_on": _("Empty: from the purchase date."),
            "warranty_ends_on": _(
                "Worked out from the start and the duration. "
                "Type it only when you do not know them."
            ),
        }

    field_order = [
        "room",
        "name",
        "kind",
        "brand",
        "model",
        "serial_number",
        "ean",
        "purchased_on",
        "installed_on",
        "warranty_duration",
        "warranty_duration_unit",
        "warranty_provider",
        "warranty_provider_name",
        "warranty_starts_on",
        "warranty_ends_on",
        "contract_contractor",
        "new_contractor_name",
        "new_contractor_trade",
        "new_contractor_trade_other",
        "contract_number",
        "contract_url",
        "links",
        "notes",
    ]

    def __init__(self, *args: Any, rooms: Any, home: Any, brands: Any = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.home = home
        provider = self.fields["contract_contractor"]
        assert isinstance(provider, forms.ModelChoiceField)  # noqa: S101 - narrows the type
        provider.queryset = tasks_services.contractors_of(home)
        provider.empty_label = "—"
        # The brands already entered in the home are suggested; any other text is kept.
        self.brands = list(brands)
        self.fields["brand"].widget.attrs.update({"list": BRAND_LIST_ID, "autocomplete": "off"})
        room_field = self.fields["room"]
        assert isinstance(room_field, forms.ModelChoiceField)  # noqa: S101 - narrows the type
        room_field.queryset = rooms
        unit = self.fields["warranty_duration_unit"]
        assert isinstance(unit, forms.ChoiceField)  # noqa: S101 - narrows the type
        unit.choices = WarrantyUnit.choices  # no dash: a duration is in months or years
        unit.required = False
        if self.instance.pk:
            self.initial["links"] = links_to_text(self.instance.links)

    def clean(self) -> dict[str, Any]:
        data: dict[str, Any] = super().clean() or {}
        clean_new_contractor(self, data, self.home, field="contract_contractor")
        if not data.get("warranty_duration_unit"):
            data["warranty_duration_unit"] = WarrantyUnit.YEAR
        return data

    def clean_links(self) -> list[dict[str, str]]:
        links = text_to_links(self.cleaned_data.get("links") or "")
        try:
            validate_links(links)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return links


class PhotoForm(LabelAboveMixin, forms.Form):
    file = forms.FileField(
        label=_("Photo"),
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
    )
    caption = forms.CharField(label=_("Caption"), max_length=200, required=False)
    category = forms.ChoiceField(
        label=_("Category"),
        choices=PhotoCategory.choices,
        initial=PhotoCategory.GENERAL,
        help_text=_(
            "Behind the walls: pipes and cables photographed before the wall is closed, "
            "to know where not to drill. Other: say what it is in the caption."
        ),
    )

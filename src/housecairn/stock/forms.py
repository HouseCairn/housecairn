"""Forms of the stock screens. Validation only; rules live in `services`."""

from typing import Any

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from housecairn.core.forms import LabelAboveMixin, MoneyField, QuantityField
from housecairn.core.widgets import ChipSelectMultiple
from housecairn.rooms.models import Item, Room
from housecairn.stock.models import Article, MovementKind, Supplier, Unit


class ItemChoiceField(forms.ModelMultipleChoiceField[Item]):
    """The items of the home, grouped by room: the group is the room, the label the item."""

    widget = ChipSelectMultiple(group_by=lambda item: item.room.name)

    def label_from_instance(self, obj: Any) -> str:
        return str(obj.name)


class ArticleForm(LabelAboveMixin, forms.ModelForm[Article]):
    """The article's own fields, its compatible items and the photo of its label."""

    compatible_items = ItemChoiceField(
        label=_("Compatible items"),
        queryset=Item.objects.none(),
        required=False,
        help_text=_("The equipment this fits, such as the boiler a filter is made for."),
    )
    rooms = forms.ModelMultipleChoiceField(
        label=_("Rooms"),
        queryset=Room.objects.none(),
        required=False,
        widget=ChipSelectMultiple,
        help_text=_("Where it is used, besides the rooms of the equipment it fits."),
    )
    label = forms.FileField(
        label=_("Photo of the label"),
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
    )
    clear_label = forms.BooleanField(label=_("Remove the photo of the label"), required=False)

    class Meta:
        model = Article
        fields = [
            "name",
            "manufacturer_reference",
            "ean",
            "dimensions",
            "storage_location",
            "unit",
            "unit_other",
            "threshold",
            "unit_price",
            "compatible_items",
            "rooms",
            "notes",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}
        field_classes = {"threshold": QuantityField, "unit_price": MoneyField}

    def __init__(self, *args: Any, items: Any, rooms: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = self.fields["compatible_items"]
        assert isinstance(field, forms.ModelMultipleChoiceField)  # noqa: S101 - narrows the type
        field.queryset = items
        unit = self.fields["unit"]
        assert isinstance(unit, forms.ChoiceField)  # noqa: S101
        unit.choices = [("", "—"), *Unit.choices]
        room_field = self.fields["rooms"]
        assert isinstance(room_field, forms.ModelMultipleChoiceField)  # noqa: S101
        room_field.queryset = rooms
        if not (self.instance.pk and self.instance.label):
            del self.fields["clear_label"]


class ArticleChoiceField(forms.ModelMultipleChoiceField[Article]):
    """The articles of the home, by name."""

    widget = ChipSelectMultiple

    def label_from_instance(self, obj: Any) -> str:
        return str(obj.name)


class LinkArticlesForm(LabelAboveMixin, forms.Form):
    """Pick the articles of the stock that fit an item (the page of the item)."""

    articles = ArticleChoiceField(
        label=_("Stock items"),
        queryset=Article.objects.none(),
        help_text=_("The stock items of this home that this equipment uses."),
    )

    def __init__(self, *args: Any, articles: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = self.fields["articles"]
        assert isinstance(field, forms.ModelMultipleChoiceField)  # noqa: S101 - narrows the type
        field.queryset = articles


class SupplierForm(LabelAboveMixin, forms.ModelForm[Supplier]):
    class Meta:
        model = Supplier
        fields = ["name", "reference", "url", "last_price"]
        field_classes = {"last_price": MoneyField}


class MovementForm(LabelAboveMixin, forms.Form):
    """The quick form of the article page; the button pressed gives `kind`."""

    kind = forms.ChoiceField(choices=MovementKind.choices)
    quantity = QuantityField(label=_("Quantity"), min_value=0, initial=1)
    occurred_on = forms.DateField(
        label=_("Date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    note = forms.CharField(label=_("Note"), max_length=200, required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["occurred_on"].initial = timezone.localdate()

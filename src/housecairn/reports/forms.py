"""Forms of reports: two fields and a photo (docs/adr/0047)."""

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from housecairn.core.forms import LabelAboveMixin
from housecairn.reports.models import TITLE_MAX_LENGTH
from housecairn.rooms.models import Item, Room
from housecairn.stock.models import Article

PHOTO_ACCEPT = "image/jpeg,image/png,image/webp"
GROUP_TITLES = {"room": _("Rooms"), "item": _("Items"), "article": _("Stock")}


class ReportForm(LabelAboveMixin, forms.Form):
    title = forms.CharField(
        label=_("What is wrong?"),
        max_length=TITLE_MAX_LENGTH,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    place = forms.ChoiceField(label=_("Where?"), required=False)
    photo = forms.FileField(
        label=_("Photo"),
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": PHOTO_ACCEPT}),
    )

    def __init__(self, *args: Any, places: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.places = places
        groups: list[Any] = [("", [("", "—")])]
        for kind in ("room", "item", "article"):
            options = [
                (f"{kind}:{found.id}", self.option_label(kind, found)) for found in places[kind]
            ]
            if options:
                groups.append((str(GROUP_TITLES[kind]), options))
        place = self.fields["place"]
        assert isinstance(place, forms.ChoiceField)  # noqa: S101
        place.choices = groups

    @staticmethod
    def option_label(kind: str, found: Room | Item | Article) -> str:
        if kind == "item":
            return f"{found.room.name} · {found.name}"  # type: ignore[union-attr]
        return str(found.name)

    def chosen_place(self) -> tuple[Room | None, Item | None, Article | None]:
        """The room, the item or the stock item picked in the list, from its own home's lists."""
        kind, _sep, identifier = (self.cleaned_data.get("place") or "").partition(":")
        picked = next(
            (found for found in self.places.get(kind, []) if str(found.id) == identifier), None
        )
        return (
            picked if kind == "room" else None,
            picked if kind == "item" else None,
            picked if kind == "article" else None,
        )

"""The materials of a task in the task form, and the small form of the task page."""

from decimal import Decimal
from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from housecairn.core.forms import LabelAboveMixin, QuantityField
from housecairn.stock.models import Article, Unit
from housecairn.tasks import materials as material_services
from housecairn.tasks.forms.fields import NEW, AddInPlaceChoiceField

MIN_QUANTITY = Decimal("0.01")


class ArticleChoiceField(forms.ModelChoiceField[Article]):
    """A stock item of the home, by its name."""

    def label_from_instance(self, obj: Article) -> str:
        return obj.name


class ArticleAddField(AddInPlaceChoiceField):
    """The stock of the home by name, the last choice adding an item (a line of the task form)."""

    def label_from_instance(self, obj: Any) -> str:
        return str(obj.name)


# Shown empty at first, and added by "Add a line" without script: one line, the next ones
# come with + (with script) or that button.
EXTRA_LINES = 1


def _quantity() -> QuantityField:
    return QuantityField(label=_("Quantity"), min_value=MIN_QUANTITY, required=False)


class MaterialLineForm(LabelAboveMixin, forms.Form):
    """One line: a stock item and the quantity the task needs. An empty line is ignored."""

    article = ArticleAddField(
        label=_("Stock item"),
        queryset=Article.objects.none(),
        required=False,
        empty_label="—",
        new_label=_("Add a stock item…"),
    )
    # The fields of a new stock item, read only with the choice "Add a stock item…".
    new_article_name = forms.CharField(
        label=_("Name of the new stock item"), max_length=120, required=False
    )
    new_article_unit = forms.ChoiceField(
        label=_("Unit"), choices=[("", "—"), *Unit.choices], required=False
    )
    new_article_unit_other = forms.CharField(label=_("Which unit"), max_length=24, required=False)
    quantity = _quantity()
    field_order = [
        "article",
        "new_article_name",
        "new_article_unit",
        "new_article_unit_other",
        "quantity",
    ]

    def __init__(self, *args: Any, articles: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["article"].queryset = articles  # type: ignore[attr-defined]
        self.articles = articles

    def clean(self) -> dict[str, Any]:
        data: dict[str, Any] = super().clean() or {}
        adding = data.get("article") == NEW
        self._clean_new_article(data)
        quantity = data.get("quantity")
        chosen = adding or data.get("article") is not None
        if chosen and quantity is None and "quantity" not in self.errors:
            self.add_error("quantity", _("Give the quantity."))
        if not chosen and quantity is not None and "article" not in self.errors:
            self.add_error("article", _("Choose the stock item."))
        return data

    def _clean_new_article(self, data: dict[str, Any]) -> None:
        """ "Add a stock item…": its fields, checked; ignored with any other choice.

        `new_article` holds what to create, made by the view with the task. A name already
        in the stock of the home, whatever its case, is refused: that item is in the list.
        """
        name = " ".join((data.pop("new_article_name", "") or "").split())
        unit = data.pop("new_article_unit", "") or ""
        other = " ".join((data.pop("new_article_unit_other", "") or "").split())
        data["new_article"] = None
        if data.get("article") != NEW:
            return
        data["article"] = None
        if not name:
            self.add_error("new_article_name", _("Give the name of the new stock item."))
            return
        if any(a.name.casefold() == name.casefold() for a in self.articles):
            self.add_error(
                "new_article_name",
                _("%(name)s is already in the stock of this home: choose it in the list.")
                % {"name": name},
            )
            return
        if unit == Unit.OTHER and not other:
            self.add_error("new_article_unit_other", _("Say which unit."))
            return
        data["new_article"] = {"name": name, "unit": unit, "unit_other": other}


class BaseMaterialFormSet(forms.BaseFormSet[MaterialLineForm]):
    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        chosen = [f.cleaned_data["article"] for f in self.forms if f.cleaned_data.get("article")]
        if len(chosen) != len(set(chosen)):
            raise forms.ValidationError(_("A stock item is listed once for a task."))
        made: set[str] = set()
        for form in self.forms:
            new = form.cleaned_data.get("new_article")
            if not new:
                continue
            name = new["name"].casefold()
            if name in made:
                form.add_error(
                    "new_article_name",
                    _("%(name)s is already added by another line: a stock item is listed once.")
                    % {"name": new["name"]},
                )
            made.add(name)

    @property
    def is_open(self) -> bool:
        """Show the section unfolded: there are lines already, or something to correct."""
        failed = self.is_bound and (bool(self.non_form_errors()) or any(self.errors))
        return bool(self.initial_form_count()) or failed

    @property
    def can_add_more(self) -> bool:
        """Whether the page may offer more lines (the limit is `MAX_MATERIALS_PER_TASK`)."""
        return self.total_form_count() < material_services.MAX_MATERIALS_PER_TASK

    def lines(self) -> list[tuple[Article | dict[str, Any], Decimal]]:
        """The lines filled in, once the formset is valid.

        A line adding a stock item gives what to create (a dict) in place of the item.
        """
        return [
            (f.cleaned_data["article"] or f.cleaned_data["new_article"], f.cleaned_data["quantity"])
            for f in self.forms
            if f.cleaned_data.get("article") or f.cleaned_data.get("new_article")
        ]


MaterialFormSet = forms.formset_factory(
    MaterialLineForm,
    formset=BaseMaterialFormSet,
    extra=EXTRA_LINES,
    max_num=material_services.MAX_MATERIALS_PER_TASK,
    absolute_max=material_services.MAX_MATERIALS_PER_TASK,
)


class MaterialSaveForm(LabelAboveMixin, forms.Form):
    """The small form of the task page: add a stock item, or change the quantity of one."""

    article = ArticleChoiceField(
        label=_("Stock item"), queryset=Article.objects.none(), empty_label="—"
    )
    quantity = QuantityField(label=_("Quantity"), min_value=MIN_QUANTITY)

    def __init__(self, *args: Any, articles: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["article"].queryset = articles  # type: ignore[attr-defined]

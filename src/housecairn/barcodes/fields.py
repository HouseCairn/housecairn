"""The `ean` field of a stock item and of an item (docs/adr/0083)."""

from typing import Any

from django import forms
from django.db import models
from django.utils.translation import gettext_lazy as _

from housecairn.barcodes import gtin


class EanFormField(forms.CharField):
    """A code typed or scanned: spaces and dashes are taken out before it is checked."""

    def to_python(self, value: Any) -> str:
        return gtin.clean(super().to_python(value))

    def widget_attrs(self, widget: forms.Widget) -> dict[str, Any]:
        attrs = super().widget_attrs(widget)
        # Up to 14 digits, with the spaces some labels print between groups.
        attrs.update(
            {
                "inputmode": "numeric",
                "autocomplete": "off",
                "maxlength": "20",
                "data-barcode-field": "",
            }
        )
        return attrs


class EanField(models.CharField[str, str]):
    """Optional; empty or a valid EAN-8, UPC-A, EAN-13 or GTIN-14 (`barcodes.gtin`)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("verbose_name", _("barcode (EAN)"))
        kwargs.setdefault("max_length", gtin.MAX_LENGTH)
        kwargs.setdefault("blank", True)
        kwargs.setdefault("validators", [gtin.validate])
        kwargs.setdefault("help_text", _("The 8 to 14 digits under the bars. Scan it, or type it."))
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> Any:
        name, _path, args, kwargs = super().deconstruct()
        for key in ("verbose_name", "max_length", "blank", "validators", "help_text"):
            kwargs.pop(key, None)
        return name, "housecairn.barcodes.fields.EanField", args, kwargs

    def formfield(
        self, form_class: Any = None, choices_form_class: Any = None, **kwargs: Any
    ) -> Any:
        return super().formfield(**{"form_class": form_class or EanFormField, **kwargs})

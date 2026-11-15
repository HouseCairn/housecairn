"""Form fields shared by several areas of the application."""

from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils.formats import get_format
from django.utils.translation import gettext_lazy as _

_MONEY = {"inputmode": "decimal", "autocomplete": "off"}
# Spaces that group thousands: the space, the no-break space and the narrow ones of French.
_GROUPING = dict.fromkeys(map(ord, " \u00a0\u2009\u202f"))


class DecimalMarkField(forms.DecimalField):
    """A decimal number typed the way the person writes it: `12.5` in English, `12,5` in French.

    The decimal mark follows the language (`localize`). Where the mark is a point, a comma is
    refused, as it can as well group thousands, but the message says what is expected instead
    of the bare "Enter a number.": the person types the comma by habit and must not guess.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs["localize"] = True
        super().__init__(**kwargs)
        self.error_messages["decimal_point"] = _(
            "Enter a number with a point as the decimal mark, for example 12.5."
        )

    def widget_attrs(self, widget: forms.Widget) -> dict[str, str]:
        # A text box (the number box of the browser follows the language of the system, not
        # the one of the page), with the numeric keyboard on a phone.
        return {**super().widget_attrs(widget), "inputmode": "decimal"}

    def to_python(self, value: Any) -> Any:
        try:
            return super().to_python(value)
        except ValidationError as error:
            if (
                error.code == "invalid"
                and isinstance(value, str)
                and "," in value
                and get_format("DECIMAL_SEPARATOR") == "."
            ):
                raise ValidationError(
                    self.error_messages["decimal_point"], code="decimal_point"
                ) from error
            raise


class QuantityField(DecimalMarkField):
    """A quantity of stock or material: up to two decimals, the decimal mark of the language."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("max_digits", 10)
        kwargs.setdefault("decimal_places", 2)
        super().__init__(**kwargs)


class MoneyField(DecimalMarkField):
    """An amount typed the way the person writes it: `1250.50` in English, `1 250,50` in French.

    The spaces that group thousands are dropped here, since Django only drops the separator of
    the language when the instance asks for thousand separators. Two decimals at most, as the
    model keeps; a scientific notation passes only while it stays within the digits of an amount.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("widget", forms.TextInput(attrs=_MONEY))
        kwargs.setdefault("max_digits", 12)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", 0)
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> Any:
        if isinstance(value, str):
            value = value.translate(_GROUPING)
        return super().to_python(value)


class LabelAboveMixin:
    """A form of the site: its labels are above their fields, with no colon after them.

    Django ends a label with `:` (` :` in French) by default; under a label that sits above
    its field it is noise, and the app has none (design/patterns.md, Forms). Every form of
    the site takes this mixin first, so that `label_tag` and `as_p` draw the same labels.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)

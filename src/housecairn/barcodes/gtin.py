"""The barcode of a product: EAN-8, UPC-A, EAN-13 or GTIN-14 (docs/adr/0083).

A code is stored as it is read once spaces and dashes are taken out; it is compared as a
GTIN-14, padded with zeros on the left, so that a UPC-A and the EAN-13 it is printed as
(`0` in front) are the same product.
"""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

LENGTHS = frozenset({8, 12, 13, 14})
MAX_LENGTH = 14


def clean(raw: object) -> str:
    """The digits of a code typed or scanned, without spaces or dashes; not checked."""
    if not isinstance(raw, str):
        return ""
    return "".join(raw.split()).replace("-", "")


def check_digit(body: str) -> int:
    """The GS1 check digit of the digits before it (modulo 10, weights 3 and 1 from the right)."""
    total = 0
    for position, digit in enumerate(reversed(body)):
        total += int(digit) * (3 if position % 2 == 0 else 1)
    return (10 - total % 10) % 10


def is_valid(code: str) -> bool:
    """True for 8, 12, 13 or 14 ASCII digits whose last one is the check digit."""
    if len(code) not in LENGTHS or not code.isascii() or not code.isdigit():
        return False
    return check_digit(code[:-1]) == int(code[-1])


def as_gtin14(code: str) -> str:
    """The code as a GTIN-14, the form every code is compared in."""
    return code.rjust(MAX_LENGTH, "0")


def variants(code: str) -> set[str]:
    """The forms a stored code of the same product may have: 8, 12, 13 or 14 digits."""
    padded = as_gtin14(code)
    found = {code}
    for length in (8, 12, 13, 14):
        head, tail = padded[: MAX_LENGTH - length], padded[MAX_LENGTH - length :]
        if not head.strip("0"):
            found.add(tail)
    return found


def as_number(code: str) -> int:
    """The code as the integer the local catalogue is keyed by."""
    return int(code)


def validate(value: str) -> None:
    """Model validator: empty, or a valid code without spaces."""
    if not value:
        return
    if not is_valid(value):
        raise ValidationError(
            _(
                "Not a valid barcode: type the 8, 12, 13 or 14 digits under the bars "
                "(EAN-8, UPC-A, EAN-13 or GTIN-14)."
            ),
            code="invalid_ean",
        )

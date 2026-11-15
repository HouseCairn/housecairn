"""The settings of the barcode lookup, paired with their variables (docs/adr/0035, 0083).

Added to `housecairn.instance.registry` at the end of that module; like it, this module
imports no model, since `housecairn.settings` reads it before the database exists.
"""

from django.utils.translation import gettext_lazy as _

from housecairn.instance.registry import Kind, Section, Setting

SETTINGS: tuple[Setting, ...] = (
    Setting(
        "barcode_lookup_online",
        "HOUSECAIRN_BARCODE_LOOKUP_ONLINE",
        "HOUSECAIRN_BARCODE_LOOKUP_ONLINE",
        Kind.BOOL,
        Section.BARCODES,
        _("Online lookup of barcodes"),
        _(
            "Ask Open Products Facts, then Open Food Facts, what a scanned code is. Only the "
            "code is sent, nothing else. Off: a code is only kept."
        ),
        False,
    ),
)

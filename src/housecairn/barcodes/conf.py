"""The settings of the barcode lookup, paired with their variables (docs/adr/0035, 0083).

Added to `housecairn.instance.registry` at the end of that module; like it, this module
imports no model, since `housecairn.settings` reads it before the database exists.
"""

from django.utils.translation import gettext_lazy as _

from housecairn.instance.registry import Kind, Section, Setting

SETTINGS: tuple[Setting, ...] = (
    Setting(
        "barcode_catalogue_enabled",
        "HOUSECAIRN_BARCODE_CATALOGUE_ENABLED",
        "HOUSECAIRN_BARCODE_CATALOGUE_ENABLED",
        Kind.BOOL,
        Section.BARCODES,
        _("Local catalogue of barcodes"),
        _(
            "Look a scanned code up in a copy of the open Nudger catalogue kept on this "
            "server, refreshed every week; no lookup leaves the server. Downloading it takes "
            "hours and space on the disk: see the documentation."
        ),
        False,
    ),
    Setting(
        "barcode_lookup_online",
        "HOUSECAIRN_BARCODE_LOOKUP_ONLINE",
        "HOUSECAIRN_BARCODE_LOOKUP_ONLINE",
        Kind.BOOL,
        Section.BARCODES,
        _("Online lookup of barcodes"),
        _(
            "When a code is not in the local catalogue, ask Open Products Facts, then Open "
            "Food Facts. Only the code is sent, nothing else. Off: a code is only kept."
        ),
        False,
    ),
)

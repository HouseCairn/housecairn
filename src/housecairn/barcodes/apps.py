from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class BarcodesConfig(AppConfig):
    """Barcodes of stock items and items, and the lookup of a code (docs/adr/0083)."""

    name = "housecairn.barcodes"
    label = "barcodes"
    verbose_name = _("Barcodes")

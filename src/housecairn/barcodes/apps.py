from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _


class BarcodesConfig(AppConfig):
    """Barcodes of stock items and items, and the lookup of a code (docs/adr/0083)."""

    name = "housecairn.barcodes"
    label = "barcodes"
    verbose_name = _("Barcodes")

    def ready(self) -> None:
        from housecairn.barcodes import tasks

        post_migrate.connect(tasks.schedule, sender=self)

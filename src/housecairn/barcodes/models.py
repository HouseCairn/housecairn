"""The cache of the barcode lookup (docs/adr/0083)."""

from django.db import models
from django.utils.translation import gettext_lazy as _

from housecairn.barcodes import gtin


class LookupState(models.TextChoices):
    RUNNING = "running", _("In progress")
    DONE = "done", _("Found")
    NOT_FOUND = "not_found", _("Not found")
    FAILED = "failed", _("Could not be read")


class BarcodeLookup(models.Model):
    """What the open databases said of a code: a cache shared by the instance.

    Holds no home and no person: only the code went out, and only what came back is kept.
    """

    code = models.CharField(max_length=gtin.MAX_LENGTH, unique=True)  # as a GTIN-14
    state = models.CharField(max_length=16, choices=LookupState.choices)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=32, blank=True)
    name = models.CharField(max_length=200, blank=True)
    brand = models.CharField(max_length=120, blank=True)
    model = models.CharField(max_length=120, blank=True)
    category = models.CharField(max_length=200, blank=True)
    product_url = models.URLField(max_length=500, blank=True)
    author = models.CharField(max_length=120, blank=True)
    # The photo, already re-encoded without metadata (`rooms.images.process`).
    image = models.BinaryField(blank=True, default=b"", editable=False)

    class Meta:
        verbose_name = _("barcode lookup")
        verbose_name_plural = _("barcode lookups")

    def __str__(self) -> str:
        return self.code

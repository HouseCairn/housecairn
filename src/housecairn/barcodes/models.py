"""The local catalogue, its import and the cache of online lookups (docs/adr/0083)."""

from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy as _

from housecairn.barcodes import gtin


class RowidField(models.BigIntegerField[int, int]):
    """An integer primary key that SQLite stores as the row identifier, without a second index.

    SQLite makes a column the row identifier only when its type is exactly `integer`; Django
    writes `bigint` for a big integer, which would add a unique index the size of the table.
    """

    def db_type(self, connection: Any) -> str | None:
        if connection.vendor == "sqlite":
            return "integer"
        return super().db_type(connection)


class CatalogueProduct(models.Model):
    """A product of the Nudger catalogue (ODbL): only what a sheet is filled with."""

    gtin = RowidField(primary_key=True)
    brand = models.CharField(max_length=120, blank=True)
    model = models.CharField(max_length=120, blank=True)
    name = models.CharField(max_length=200, blank=True)
    categories = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = _("catalogue product")
        verbose_name_plural = _("catalogue products")

    def __str__(self) -> str:
        return gtin.as_gtin14(str(self.gtin))


class ImportState(models.TextChoices):
    DOWNLOADING = "downloading", _("Downloading")
    IMPORTING = "importing", _("Importing")
    DONE = "done", _("Done")
    FAILED = "failed", _("Failed")


class CatalogueImport(models.Model):
    """Where the import of an export stands, so that a run stopped half-way goes on.

    `source` and `size` say which file it is: another file starts over.
    """

    source = models.CharField(max_length=500)
    size = models.BigIntegerField(default=0)
    state = models.CharField(max_length=16, choices=ImportState.choices)
    all_categories = models.BooleanField(default=False)
    rows_read = models.BigIntegerField(default=0)
    rows_kept = models.BigIntegerField(default=0)
    started_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("catalogue import")
        verbose_name_plural = _("catalogue imports")

    def __str__(self) -> str:
        return f"{self.state} {self.rows_read}"


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

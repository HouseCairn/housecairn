"""The local copy of the Nudger catalogue: import by stream, resumed after a stop (docs/adr/0083).

The export is one ZIP (`gtin-open-data.zip`) holding one CSV. The CSV member is inflated and
parsed row by row, never read whole: memory stays the size of a batch whatever the size of the
file. Progress is a `CatalogueImport` row: a run that stops goes on from the rows already read
of the same file (same name and size); another file starts over.
"""

import csv
import io
import logging
import re
import time
import unicodedata
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from housecairn.barcodes import gtin
from housecairn.barcodes.models import CatalogueImport, CatalogueProduct, ImportState

logger = logging.getLogger("housecairn.barcodes")

SOURCE_URL = "https://static.nudger.fr/opendata/gtin-open-data.zip"
BATCH = 5000
COLUMNS = ("gtin", "brand", "model", "name", "categories")

# The categories useful in a house, as words their names start with, without accents and in
# capitals as in the export (chosen from its first two million rows,
# docs/administration/barcodes.md).
HOUSE_KEYWORDS: tuple[str, ...] = (
    "ELECTROMENAGER",
    "PETIT MENAGER",
    "GROS MENAGER",
    "BRICOLAGE",
    "OUTILLAGE",
    "HAND TOOLS",
    "POWER TOOLS",
    "DIY",
    "QUINCAILLERIE",
    "LUMINAIRE",
    "ECLAIRAGE",
    "LIGHTING",
    "AMPOULE",
    "PILE",
    "FILTRE",
    "CHAUFFAGE",
    "CLIMATIS",
    "VENTILATION",
    "PLOMBERIE",
    "SANITAIRE",
    "SALLE DE BAIN",
    "ROBINET",
    "ELECTRICITE",
    "DOMOTIQUE",
    "JARDIN",
    "GARDEN",
    "PISCINE",
    "ENTRETIEN",
    "NETTOYAGE",
    "PEINTURE",
    "DETECTEUR",
)
_HOUSE = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in HOUSE_KEYWORDS) + r")")


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def useful_in_a_house(categories: str) -> bool:
    return bool(_HOUSE.search(_plain(categories)))


@dataclass(frozen=True)
class Row:
    gtin: int
    brand: str
    model: str
    name: str
    categories: str


def _cut(text: str, length: int) -> str:
    return " ".join(text.split())[:length]


def parse(lines: Iterable[str]) -> Iterator[Row | None]:
    """Each data row of the CSV, or None for one that is not a product (bad code, short row).

    Every row read gives one value, kept or not, so that a count of values is a count of rows.
    """
    reader = csv.reader(lines)
    header = next(reader, None)
    if header is None:
        return
    index = {name.strip().lower(): position for position, name in enumerate(header)}
    missing = [c for c in COLUMNS if c not in index]
    if missing:
        raise ValueError(f"The export has no column {', '.join(missing)}.")
    width = max(index[c] for c in COLUMNS) + 1
    for values in reader:
        if len(values) < width:
            yield None
            continue
        code = gtin.clean(values[index["gtin"]])
        if not gtin.is_valid(code):
            yield None
            continue
        yield Row(
            gtin=gtin.as_number(code),
            brand=_cut(values[index["brand"]], 120),
            model=_cut(values[index["model"]], 120),
            name=_cut(values[index["name"]], 200),
            categories=_cut(values[index["categories"]], 300),
        )


def open_lines(path: Path) -> tuple[zipfile.ZipFile | None, io.TextIOBase]:
    """The text of the export, streamed: the first CSV member of a ZIP, or a CSV file."""
    if zipfile.is_zipfile(path):
        archive = zipfile.ZipFile(path)
        members = [m for m in archive.infolist() if m.filename.lower().endswith(".csv")]
        if not members:
            archive.close()
            raise ValueError("The archive holds no CSV file.")
        raw = archive.open(members[0])
        return archive, io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
    return None, open(path, encoding="utf-8", errors="replace", newline="")


def _progress(path: Path, all_categories: bool) -> CatalogueImport:
    """The import of this file to go on with, or a new one (the table is emptied then)."""
    source, size = path.name, path.stat().st_size
    current = CatalogueImport.objects.order_by("-started_at").first()
    if (
        current is not None
        and current.source == source
        and current.size == size
        and current.all_categories == all_categories
        and current.state == ImportState.IMPORTING
    ):
        return current
    CatalogueImport.objects.all().delete()
    CatalogueProduct.objects.all().delete()
    return CatalogueImport.objects.create(
        source=source,
        size=size,
        state=ImportState.IMPORTING,
        all_categories=all_categories,
        started_at=timezone.now(),
    )


def _write(batch: list[Row], progress: CatalogueImport, read: int, kept: int) -> None:
    with transaction.atomic():
        # A code given twice keeps its first row, in the batch as in the table.
        CatalogueProduct.objects.bulk_create(
            [
                CatalogueProduct(
                    gtin=r.gtin, brand=r.brand, model=r.model, name=r.name, categories=r.categories
                )
                for r in batch
            ],
            ignore_conflicts=True,
        )
        CatalogueImport.objects.filter(pk=progress.pk).update(rows_read=read, rows_kept=kept)


def import_file(
    path: Path, *, all_categories: bool = False, seconds: float | None = None
) -> CatalogueImport:
    """Import the export at `path`, from where the last run of the same file stopped.

    `seconds`: stop after about that long (a slice of the weekly task) with the state still
    `importing`; the next call goes on. Returns the progress row.
    """
    progress = _progress(path, all_categories)
    deadline = None if seconds is None else time.monotonic() + seconds
    archive, text = _open(path)
    read, kept = 0, progress.rows_kept
    batch: list[Row] = []
    try:
        for row in parse(text):
            read += 1
            if read <= progress.rows_read:
                continue  # already imported by a run that stopped
            if row is not None and (all_categories or useful_in_a_house(row.categories)):
                batch.append(row)
                kept += 1
            if read % BATCH == 0:
                _write(batch, progress, read, kept)
                batch = []
                if deadline is not None and time.monotonic() >= deadline:
                    progress.refresh_from_db()
                    return progress
        _write(batch, progress, read, kept)
    finally:
        text.close()
        if archive is not None:
            archive.close()
    CatalogueImport.objects.filter(pk=progress.pk).update(
        state=ImportState.DONE, finished_at=timezone.now()
    )
    progress.refresh_from_db()
    logger.info("Barcode catalogue imported: %s rows read, %s kept.", read, kept)
    return progress


_open = open_lines


def find(code: str) -> CatalogueProduct | None:
    """The product of the local catalogue with this code; never leaves the server."""
    if not gtin.is_valid(code):
        return None
    return CatalogueProduct.objects.filter(gtin=gtin.as_number(code)).first()

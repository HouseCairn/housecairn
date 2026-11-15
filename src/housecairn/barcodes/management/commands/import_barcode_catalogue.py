"""Import the Nudger export into the local catalogue (docs/adr/0083).

    manage.py import_barcode_catalogue --file gtin-open-data.zip
    manage.py import_barcode_catalogue --download

The file is read as a stream; a run that stops goes on where it was with the same file.
"""

import time
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from housecairn.barcodes import catalogue, download
from housecairn.barcodes.models import CatalogueProduct


class Command(BaseCommand):
    help = "Import the open Nudger catalogue of barcodes (ODbL) into the local table."

    def add_arguments(self, parser: CommandParser) -> None:
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--file", type=Path, help="The ZIP or CSV export, already downloaded.")
        source.add_argument(
            "--download",
            action="store_true",
            help="Download the export first (resumed if it stopped), into the data directory.",
        )
        parser.add_argument(
            "--all-categories",
            action="store_true",
            help="Keep every product, not only the categories useful in a house.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["download"]:
            self.stdout.write("Downloading the export (it can take hours)...")
            download.fetch(None)
            path = download.target()
        else:
            path = options["file"]
            if not path.is_file():
                raise CommandError(f"No file at {path}.")
        began = time.monotonic()
        try:
            progress = catalogue.import_file(path, all_categories=options["all_categories"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"{progress.rows_read} rows read, {progress.rows_kept} kept, "
            f"{CatalogueProduct.objects.count()} products in the catalogue, "
            f"{time.monotonic() - began:.0f} s."
        )

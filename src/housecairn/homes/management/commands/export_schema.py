"""Write, or check, the JSON Schema of the archive of a home (docs/adr/0048)."""

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from housecairn.homes import schema

PUBLISHED = Path("docs/_static/schemas/housecairn-export-v4.schema.json")


def render() -> str:
    return json.dumps(schema.schema(), indent=2, ensure_ascii=False) + "\n"


class Command(BaseCommand):
    help = "Write the JSON Schema of the export into the documentation, or check it (--check)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--check", action="store_true")
        parser.add_argument("--file", type=Path, default=PUBLISHED)

    def handle(self, *args: Any, **options: Any) -> None:
        path: Path = options["file"]
        if options["check"]:
            if not path.is_file() or path.read_text(encoding="utf-8") != render():
                raise CommandError(f"{path} is out of date: run manage.py export_schema.")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(), encoding="utf-8")
        self.stdout.write(str(path))

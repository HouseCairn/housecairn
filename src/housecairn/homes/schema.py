"""The JSON Schema of the archive of a home, built from the registry (docs/adr/0048).

`manage.py export_schema` writes it; the documentation publishes the result as
`docs/_static/schemas/housecairn-export-v4.schema.json`, and `tests/test_export_schema.py`
fails when the published file and the registry no longer agree, or when a real archive
does not validate.
"""

from typing import Any

from django.db import models

from housecairn.homes import exports

UUID = {"type": "string", "format": "uuid"}
ARCHIVE_PATH = {"type": "string", "pattern": r"^files/[a-z_]+/[0-9a-f-]{36}(\.[a-z0-9]+)?$"}


def _nullable(
    schema: dict[str, Any], f: models.Field[Any, Any], forced: bool = False
) -> dict[str, Any]:
    if not (f.null or forced):
        return schema
    if "type" in schema:
        return {**schema, "type": [schema["type"], "null"]}
    return {"anyOf": [schema, {"type": "null"}]}


def _field(f: models.Field[Any, Any], files: set[str], nulls: tuple[str, ...]) -> dict[str, Any]:
    if f.name in files:
        return {"anyOf": [ARCHIVE_PATH, {"type": "null"}]}
    if exports.person_reference(f):
        return _nullable({**UUID, "description": "a person of `people`"}, f)
    if isinstance(f, models.ForeignKey | models.UUIDField):
        return _nullable(dict(UUID), f)
    if isinstance(f, models.DateTimeField):
        return _nullable({"type": "string", "format": "date-time"}, f)
    if isinstance(f, models.DateField):
        return _nullable({"type": "string", "format": "date"}, f)
    if isinstance(f, models.TimeField):
        return _nullable({"type": "string", "format": "time"}, f)
    if isinstance(f, models.DecimalField):
        return _nullable({"type": "string", "pattern": r"^-?\d+(\.\d+)?$"}, f)
    if isinstance(f, models.BooleanField):
        return _nullable({"type": "boolean"}, f)
    if isinstance(f, models.IntegerField):
        return _nullable({"type": "integer"}, f)
    if isinstance(f, models.JSONField):
        return {}
    schema: dict[str, Any] = {"type": "string"}
    if getattr(f, "max_length", None):
        schema["maxLength"] = f.max_length
    if f.choices:
        schema["enum"] = sorted(
            {str(k) for k, _label in f.flatchoices} | ({""} if f.blank else set())
        )
    return _nullable(schema, f, f.name in nulls)


def _section(section: exports.Section) -> dict[str, Any]:
    files = {spec.field for spec in section.files}
    properties = {f.name: _field(f, files, section.nulls) for f in section.fields()}
    for relation in section.many_to_many():
        properties[relation.name] = {"type": "array", "items": UUID}
    schema: dict[str, Any] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": properties,
            "required": sorted(properties),
            "additionalProperties": False,
        },
    }
    if not section.restore:
        schema["description"] = f"Kept for the owners; not restored: {section.why_not_restored}."
    return schema


def schema() -> dict[str, Any]:
    documents = {}
    for app, sections in exports.SECTIONS.items():
        body: dict[str, Any] = {
            "type": "object",
            "properties": {
                "app": {"const": app},
                "sections": {
                    "type": "object",
                    "properties": {s.name: _section(s) for s in sections},
                    "additionalProperties": False,
                },
            },
            "required": ["app", "sections"],
        }
        if app == "homes":
            body["properties"]["home"] = {"type": "object"}
            body["properties"]["people"] = {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": UUID,
                        "email": {"type": "string"},
                        "name": {"type": "string"},
                    },
                    "required": ["id", "email", "name"],
                },
            }
            body["required"] = ["app", "sections", "home", "people"]
        documents[app] = body
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://docs.housecairn.app/_static/schemas/housecairn-export-v4.schema.json",
        "title": f"HouseCairn export of a home, format {exports.FORMAT_VERSION}",
        "description": (
            "`manifest` describes manifest.json; `documents` describes data/<app>.json, one per "
            "app the manifest lists. See Administration > Export and deletion."
        ),
        "$defs": {
            "manifest": {
                "type": "object",
                "properties": {
                    "format": {"const": exports.FORMAT},
                    "version": {"const": exports.FORMAT_VERSION},
                    "application_version": {"type": "string"},
                    "exported_at": {"type": "string", "format": "date-time"},
                    "home": UUID,
                    "apps": {"type": "array", "items": {"enum": list(exports.SECTIONS)}},
                    "entries": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "properties": {
                                "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                                "size": {"type": "integer", "minimum": 0},
                            },
                            "required": ["sha256", "size"],
                        },
                    },
                },
                "required": [
                    "format",
                    "version",
                    "application_version",
                    "exported_at",
                    "home",
                    "apps",
                    "entries",
                ],
            },
            "documents": documents,
        },
    }

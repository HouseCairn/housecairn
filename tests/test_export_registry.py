"""No table of the project escapes the export of a home without a written reason (docs/adr/0023).

The registry of `homes.exports` is filled by each app; nothing there stops a lot from adding
a table and forgetting its exporter. These tests go through every model of the project
(`apps.get_models()`, many-to-many tables included) and put each in one of three places:

- exported: the export of a home reads its table;
- left out of the archive on purpose, and erased with the home (`LEFT_OUT`);
- not data of a home at all: of the instance or of a person (`NOT_OF_A_HOME`).

A new table that is in none of them fails, with the name of the table.
"""

import re

import pytest
from django.apps import apps
from django.db import connection, models
from django.test.utils import CaptureQueriesContext

from housecairn.homes import exports, services
from housecairn.homes.models import Home
from housecairn.projects.models import Project
from housecairn.rooms.models import Item, Room
from housecairn.stock.models import Article

pytestmark = pytest.mark.django_db

# Data of a home that the archive leaves out on purpose. Each goes with the home: the test
# below checks it.
LEFT_OUT = {
    # The owners read it on the page of the home and from the API (`/audit/`);
    # docs/administration/data.md does not list it in the archive. Erased by `erase_home`.
    "audit.AuditEvent": "the audit log of the home",
    # Which homes a token of a person may reach. The token is the person's and is revoked
    # once no home is left; the archive never carries a secret.
    "apitokens.ApiToken_homes": "the homes an API token reaches",
    # The same for a connected phone (docs/adr/0078): the connection is the person's.
    "apitokens.AppConnection_homes": "the homes a connected phone reaches",
    # That a reminder about an occurrence went to a person, so that it goes once: a record
    # of sending, with no content of the home.
    "notifications.Delivery": "the reminders already sent",
    # An archive made in the background for a download, removed after a day (homes.jobs).
    "homes.ExportJob": "the archives made for a download",
    # The text of CSV files between the preview of an import and its confirmation,
    # removed once confirmed and within the hour after it expires (homes.csv_import).
    "homes.ImportPreview": "the files of an import waiting for its confirmation",
    # The change log of the sync API (docs/adr/0077): positions, changes and field clocks of
    # the home, for the phones. Erased with the home, which is why they cascade from it.
    "sync.HomeHead": "the position of the change log of the home",
    "sync.Change": "the changes of the home, for the sync API",
    "sync.RowClocks": "the clock of every field of a row of the home",
}

# Not data of a home: of the instance, or of a person (erased with the account,
# docs/administration/data.md), whatever home they open.
NOT_OF_A_HOME = {
    # The home itself is the `home` section, read from the object the export is given.
    "homes.Home": "the home itself",
    "accounts.User_groups": "Django groups of an account (unused)",
    "accounts.User_user_permissions": "Django permissions of an account (unused)",
    "accounts.InstanceSetup": "the first setup of the instance",
    "accounts.KnownDevice": "the browsers an account signed in from",
    "accounts.SetupCode": "the digest of the one-time code of the first-account screen",
    "apitokens.ApiToken": "the API tokens of a person",
    "apitokens.AppConnection": "the phones a person connected (docs/adr/0078)",
    "apitokens.ConnectionGrant": "the authorization code of a connected phone",
    "apitokens.ConnectionAccessToken": "the access tokens of a connected phone",
    "apitokens.ConnectionRefreshToken": "the refresh tokens of a connected phone",
    "instance.InstanceInvitation": "invitations to the instance",
    "instance.OidcProvider": "single sign-on providers of the instance",
    "instance.SettingValue": "settings of the instance",
    "tasks.DisplayPreference": "how a person lays out the Today screen",
    "notifications.Channel": "where a person receives reminders",
    "notifications.Preference": "when a person wants reminders",
    "notifications.Device": "a browser of a person that receives notifications (docs/adr/0066)",
    "notifications.PushKey": "the push key pair of the instance (docs/adr/0066)",
    "sync.ServerDevice": "the device id the server writes with (docs/adr/0077)",
    "sync.ErasedHome": "that a home was erased, which outlives the home (docs/adr/0077)",
    "sync.ErasedHomeConnection": "the apps told of an erased home, kept with it (docs/adr/0077)",
    "sync.Push": "the pushes of a connected phone, kept 30 days (docs/adr/0077)",
    "barcodes.CatalogueProduct": "the local copy of the open barcode catalogue (docs/adr/0083)",
    "barcodes.CatalogueImport": "where the import of the barcode catalogue stands (docs/adr/0083)",
    "barcodes.BarcodeLookup": "the cache of the online barcode lookups (docs/adr/0083)",
}


def project_models() -> list[type[models.Model]]:
    return [
        model
        for model in apps.get_models(include_auto_created=True)
        if model._meta.app_config is not None
        and model._meta.app_config.name.startswith("housecairn.")
    ]


def tables_read_by_the_export(home: Home) -> set[str]:
    """The tables the export of `home` reads, from the SQL it runs."""
    room = Room.objects.create(home=home, name="Kitchen")
    item = Item.objects.create(home=home, room=room, name="Boiler")
    article = Article.objects.create(home=home, name="Filter")
    article.compatible_items.set([item])
    article.rooms.set([room])
    project = Project.objects.create(home=home, title="Paint")
    project.rooms.set([room])
    # The rows above make the export also read the tables between stock items, projects,
    # rooms and items, which it reads only for rows that exist.
    with CaptureQueriesContext(connection) as context:
        exports.build_archive(home)
    sql = "\n".join(query["sql"] for query in context.captured_queries)
    return set(re.findall(r'(?:FROM|JOIN) "(\w+)"', sql))


def tables_deleted_by_erase_home(home: Home) -> set[str]:
    with CaptureQueriesContext(connection) as context:
        services.erase_home(home)
    sql = "\n".join(query["sql"] for query in context.captured_queries)
    return set(re.findall(r'DELETE FROM "(\w+)"', sql))


def cascades_from_home(model: type[models.Model], seen: frozenset[str] = frozenset()) -> bool:
    """True when deleting a home deletes the rows of `model`, by a chain of CASCADE keys."""
    if model is Home:
        return True
    for field in model._meta.concrete_fields:
        target = field.related_model
        if not field.is_relation or not isinstance(target, type) or target._meta.label in seen:
            continue
        if field.remote_field.on_delete is models.CASCADE and cascades_from_home(
            target, seen | {model._meta.label}
        ):
            return True
    return False


def test_every_table_is_exported_left_out_with_a_reason_or_not_of_a_home(
    alice_home: Home,
) -> None:
    read = tables_read_by_the_export(alice_home)
    forgotten = sorted(
        model._meta.label
        for model in project_models()
        if model._meta.db_table not in read
        and model._meta.label not in LEFT_OUT
        and model._meta.label not in NOT_OF_A_HOME
    )
    assert forgotten == [], (
        "The export of a home does not read these tables: register an exporter "
        "(homes.exports.register) or list them in this file with the reason."
    )


def test_the_lists_name_existing_tables_that_the_export_does_not_read(alice_home: Home) -> None:
    labels = {model._meta.label for model in project_models()}
    listed = set(LEFT_OUT) | set(NOT_OF_A_HOME)
    assert set(LEFT_OUT).isdisjoint(NOT_OF_A_HOME)
    assert listed <= labels
    read = tables_read_by_the_export(alice_home)
    exported_anyway = sorted(
        label for label in listed if apps.get_model(label)._meta.db_table in read
    )
    assert exported_anyway == [], "Exported now: take them off the lists."


def test_what_the_archive_leaves_out_goes_with_the_home(alice_home: Home) -> None:
    deleted = tables_deleted_by_erase_home(alice_home)
    kept = sorted(
        label
        for label in LEFT_OUT
        if not cascades_from_home(apps.get_model(label))
        and apps.get_model(label)._meta.db_table not in deleted
    )
    assert kept == []


def test_what_is_not_of_a_home_hangs_off_no_home() -> None:
    of_a_home = sorted(
        label
        for label in NOT_OF_A_HOME
        if label != "homes.Home"
        and (
            cascades_from_home(apps.get_model(label))
            or "home_id" in {f.attname for f in apps.get_model(label)._meta.concrete_fields}
        )
    )
    assert of_a_home == [], "These hold data of a home: export them or move them to LEFT_OUT."


def test_the_check_sees_a_table_with_no_exporter(alice_home: Home) -> None:
    """The first test would fail on an app whose exporter is gone."""
    sections = exports.SECTIONS["stock"]
    removed = next(s for s in sections if s.name == "stock_suppliers")
    sections.remove(removed)
    try:
        read = tables_read_by_the_export(alice_home)
    finally:
        sections.insert(1, removed)
    assert "stock_supplier" not in read


def test_every_reference_of_a_section_can_be_restored() -> None:
    """A key leads to the home, to a person, or to the table of another section; a file
    field has its FileSpec. Otherwise the restore could not rebuild the row (docs/adr/0048)."""
    exported = {section.model for _app, section in exports.all_sections()}
    unresolved = []
    for _app, section in exports.all_sections():
        spec_fields = section.file_fields()
        for f in section.fields():
            if isinstance(f, models.FileField) and f.name not in spec_fields:
                unresolved.append(f"{section.name}.{f.name}")
            if (
                f.is_relation
                and f.related_model is not Home
                and not exports.person_reference(f)
                and f.related_model not in exported
            ):
                unresolved.append(f"{section.name}.{f.name}")
        for relation in section.many_to_many():
            if relation.related_model not in exported:
                unresolved.append(f"{section.name}.{relation.name}")
    assert unresolved == []


def test_no_section_exports_a_secret() -> None:
    """The digests of invitations, feeds and tokens, and the tokens themselves, stay out."""
    written = {
        (section.model._meta.label, f.name)
        for _a, section in exports.all_sections()
        for f in section.fields()
    }
    for secret in (
        ("homes.Invitation", "digest"),
        ("calendar.Feed", "digest"),
        ("events.Event", "token"),
    ):
        assert secret not in written
    labels = {section.model._meta.label for _a, section in exports.all_sections()}
    for label in (
        "apitokens.ApiToken",
        "accounts.InstanceSetup",
        "accounts.SetupCode",
        "accounts.KnownDevice",
        "instance.OidcProvider",
        "notifications.Channel",
        "notifications.Device",
        "notifications.PushKey",
        "instance.SettingValue",
    ):
        assert label not in labels

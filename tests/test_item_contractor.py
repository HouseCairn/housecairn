"""The provider of the maintenance contract of an item is a contractor of the home (ADR 0058)."""

import csv
import html
import io
import json
import re
import uuid
import zipfile
from pathlib import Path
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.audit.models import AuditEvent
from housecairn.homes import csv_export, csv_import, restore
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.rooms import services
from housecairn.rooms.models import Item, Room
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Contractor
from tests.data_support import archive_bytes
from tests.test_back_links_more import back_links
from tests.test_restore_security import entries, rebuild

pytest_plugins = ("tests.rooms_fixtures",)
pytestmark = pytest.mark.django_db


@pytest.fixture
def acme(alice: User, alice_home: Home) -> Contractor:
    return task_services.create_contractor(alice, alice_home, name="Acme Heating", trade="plumber")


def item_url(name: str, home: Home, **kwargs: object) -> str:
    return reverse(f"rooms:item-{name}", kwargs={"home_id": home.id, **kwargs})


def form(room: Room, **fields: object) -> dict[str, object]:
    return {"room": str(room.id), "name": "Boiler", "kind": "equipment", **fields}


class TestTheLink:
    def test_the_contract_keeps_its_contractor(
        self, alice: User, owner_room: Room, acme: Contractor
    ) -> None:
        item = services.create_item(alice, owner_room, name="Boiler", contract_contractor=acme)
        assert Item.objects.get(pk=item.pk).contract_contractor == acme
        assert list(acme.maintained_items.all()) == [item]

    def test_the_model_refuses_a_contractor_of_another_home(
        self, alice: User, bob: User, bob_home: Home, owner_room: Room
    ) -> None:
        stranger = task_services.create_contractor(bob, bob_home, name="Elsewhere")
        item = Item(home=owner_room.home, room=owner_room, name="Boiler")
        item.contract_contractor = stranger
        with pytest.raises(ValidationError) as caught:
            item.full_clean()
        assert "contract_contractor" in caught.value.message_dict

    def test_the_service_refuses_a_contractor_of_another_home(
        self, alice: User, bob: User, bob_home: Home, owner_room: Room, owner_item: Item
    ) -> None:
        stranger = task_services.create_contractor(bob, bob_home, name="Elsewhere")
        with pytest.raises(ValidationError):
            services.create_item(alice, owner_room, name="Pump", contract_contractor=stranger)
        with pytest.raises(ValidationError):
            services.update_item(alice, owner_item, contract_contractor=stranger)
        assert not Item.objects.filter(contract_contractor=stranger).exists()

    def test_a_deleted_contractor_cannot_be_chosen(
        self, alice: User, owner_room: Room, acme: Contractor
    ) -> None:
        task_services.delete_contractor(alice, acme)
        with pytest.raises(ValidationError):
            services.create_item(alice, owner_room, name="Boiler", contract_contractor=acme)

    def test_deleting_the_contractor_clears_the_link_and_keeps_the_rest(
        self, alice: User, owner_room: Room, acme: Contractor
    ) -> None:
        item = services.create_item(
            alice,
            owner_room,
            name="Boiler",
            contract_contractor=acme,
            contract_number="C-1",
            contract_url="https://example.com/contract",
        )
        gone = services.create_item(alice, owner_room, name="Gone", contract_contractor=acme)
        services.delete_item(alice, gone)
        task_services.delete_contractor(alice, acme)
        item.refresh_from_db()
        assert item.contract_contractor is None
        assert (item.contract_number, item.contract_url) == ("C-1", "https://example.com/contract")
        # The item deleted before is cleared too: it comes back without a dangling link.
        assert Item.all_objects.get(pk=gone.pk).contract_contractor_id is None


class TestTheForm:
    def test_the_list_offers_the_contractors_of_the_home_and_adding_one(
        self,
        signed_in: Client,
        alice_home: Home,
        owner_room: Room,
        acme: Contractor,
        bob: User,
        bob_home: Home,
    ) -> None:
        task_services.create_contractor(bob, bob_home, name="Elsewhere")
        page = signed_in.get(item_url("create", alice_home, room_id=owner_room.id)).content.decode()
        select = re.search(
            r'<select[^>]*name="contract_contractor"[^>]*>(.*?)</select>', page, re.S
        )
        assert select
        options = re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', select.group(1))
        assert options == [("", "—"), (str(acme.id), "Acme Heating"), ("new", "Add a contractor…")]
        # The fields of the new one sit right under the list, shown by the choice.
        assert 'data-shown-when="contract_contractor=new"' in page
        assert page.index('name="contract_contractor"') < page.index('name="new_contractor_name"')
        assert page.index('name="new_contractor_name"') < page.index('name="contract_number"')
        assert "task-form.js" in page
        assert "data-in-place-form" in page

    def test_the_edit_form_selects_the_current_provider(
        self, signed_in: Client, alice: User, alice_home: Home, owner_item: Item, acme: Contractor
    ) -> None:
        services.update_item(alice, owner_item, contract_contractor=acme)
        page = signed_in.get(item_url("edit", alice_home, item_id=owner_item.id)).content.decode()
        assert re.search(rf'<option value="{acme.id}"[^>]*\bselected\b', page)

    def test_a_contractor_of_another_home_is_refused(
        self,
        signed_in: Client,
        alice_home: Home,
        owner_room: Room,
        bob: User,
        bob_home: Home,
    ) -> None:
        stranger = task_services.create_contractor(bob, bob_home, name="Elsewhere")
        response = signed_in.post(
            item_url("create", alice_home, room_id=owner_room.id),
            form(owner_room, contract_contractor=str(stranger.id)),
        )
        assert response.status_code == 200
        assert "contract_contractor" in response.context["form"].errors
        assert not Item.objects.filter(name="Boiler").exists()

    def test_adding_a_contractor_in_place_creates_it_with_the_item(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        response = signed_in.post(
            item_url("create", alice_home, room_id=owner_room.id),
            form(
                owner_room,
                contract_contractor="new",
                new_contractor_name="  Pipes   Ltd ",
                new_contractor_trade="plumber",
            ),
        )
        assert response.status_code == 302, response.context["form"].errors
        made = Contractor.objects.get(name="Pipes Ltd")
        assert made.trade == "plumber"
        assert Item.objects.get(name="Boiler").contract_contractor == made
        assert AuditEvent.objects.filter(action="contractor.created").count() == 1

    def test_adding_one_while_editing(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        response = signed_in.post(
            item_url("edit", alice_home, item_id=owner_item.id),
            form(
                owner_item.room,
                name=owner_item.name,
                contract_contractor="new",
                new_contractor_name="Pipes Ltd",
                new_contractor_trade="other",
                new_contractor_trade_other="Pool maintenance",
            ),
        )
        assert response.status_code == 302, response.context["form"].errors
        owner_item.refresh_from_db()
        assert owner_item.contract_contractor.name == "Pipes Ltd"
        assert owner_item.contract_contractor.trade_other == "Pool maintenance"

    def test_an_empty_known_or_trade_less_name_is_refused_and_creates_nothing(
        self, signed_in: Client, alice_home: Home, owner_room: Room, acme: Contractor
    ) -> None:
        address = item_url("create", alice_home, room_id=owner_room.id)
        cases = [
            ({"new_contractor_name": "  "}, "new_contractor_name", "Give the name"),
            ({"new_contractor_name": " acme  HEATING"}, "new_contractor_name", "is already"),
            (
                {"new_contractor_name": "Pool", "new_contractor_trade": "other"},
                "new_contractor_trade_other",
                "Say which trade",
            ),
        ]
        for extra, field, text in cases:
            response = signed_in.post(address, form(owner_room, contract_contractor="new", **extra))
            assert response.status_code == 200
            assert text in " ".join(response.context["form"].errors[field])
        assert Contractor.objects.count() == 1
        assert not Item.objects.filter(name="Boiler").exists()

    def test_the_fields_of_a_new_contractor_are_ignored_without_the_choice(
        self, signed_in: Client, alice_home: Home, owner_room: Room, acme: Contractor
    ) -> None:
        response = signed_in.post(
            item_url("create", alice_home, room_id=owner_room.id),
            form(
                owner_room,
                contract_contractor=str(acme.id),
                new_contractor_name="Never made",
            ),
        )
        assert response.status_code == 302
        assert not Contractor.objects.filter(name="Never made").exists()
        assert Item.objects.get(name="Boiler").contract_contractor == acme

    def test_an_item_refused_by_the_service_leaves_no_contractor_behind(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        with mock.patch.object(services, "create_item", side_effect=ValidationError("No.")):
            response = signed_in.post(
                item_url("create", alice_home, room_id=owner_room.id),
                form(owner_room, contract_contractor="new", new_contractor_name="Pipes Ltd"),
            )
        assert response.status_code == 200
        assert not Contractor.objects.filter(name="Pipes Ltd").exists()
        assert not AuditEvent.objects.filter(action="contractor.created").exists()

    def test_an_edit_refused_by_the_service_leaves_no_contractor_behind(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        with mock.patch.object(services, "update_item", side_effect=ValidationError("No.")):
            response = signed_in.post(
                item_url("edit", alice_home, item_id=owner_item.id),
                form(
                    owner_item.room,
                    name=owner_item.name,
                    contract_contractor="new",
                    new_contractor_name="Pipes Ltd",
                ),
            )
        assert response.status_code == 200
        assert not Contractor.objects.filter(name="Pipes Ltd").exists()

    def test_a_reader_adds_nothing(
        self, client: Client, reader: User, alice_home: Home, owner_room: Room
    ) -> None:
        client.force_login(reader)
        response = client.post(
            item_url("create", alice_home, room_id=owner_room.id),
            form(owner_room, contract_contractor="new", new_contractor_name="Pipes Ltd"),
        )
        assert response.status_code == 403
        assert not Contractor.objects.filter(name="Pipes Ltd").exists()


class TestTheScreens:
    def test_the_sheet_links_to_the_contractor_without_a_provider_no_link(
        self, signed_in: Client, alice: User, alice_home: Home, owner_item: Item, acme: Contractor
    ) -> None:
        services.update_item(alice, owner_item, contract_number="C-9")
        page = signed_in.get(item_url("detail", alice_home, item_id=owner_item.id)).content.decode()
        assert "C-9" in page
        assert f"contractors/{acme.id}/" not in page
        services.update_item(alice, owner_item, contract_contractor=acme)
        page = signed_in.get(item_url("detail", alice_home, item_id=owner_item.id)).content.decode()
        assert f"contractors/{acme.id}/" in page

    def test_the_sheet_of_the_contractor_leads_back_to_the_item(
        self, signed_in: Client, alice: User, alice_home: Home, owner_item: Item, acme: Contractor
    ) -> None:
        """Return rule (ADR 0053): the link carries `next`, the back link names the item."""
        services.update_item(alice, owner_item, contract_contractor=acme)
        here = item_url("detail", alice_home, item_id=owner_item.id)
        page = signed_in.get(here).content.decode()
        link = re.search(rf'<a href="([^"]*contractors/{acme.id}/[^"]*)">Acme Heating</a>', page)
        assert link
        sheet = signed_in.get(html.unescape(link.group(1))).content.decode()
        assert [(href, text) for href, text, _phone in back_links(sheet)] == [
            (here, owner_item.name)
        ]
        # Without `next` the back link leads to the list of contractors.
        bare = reverse(
            "tasks:contractor-detail", kwargs={"home_id": alice_home.id, "contractor_id": acme.id}
        )
        lists = reverse("tasks:contractors", kwargs={"home_id": alice_home.id})
        assert [
            href for href, _text, _phone in back_links(signed_in.get(bare).content.decode())
        ] == [lists]

    def test_the_task_from_the_item_starts_with_its_contractor(
        self, signed_in: Client, alice: User, alice_home: Home, owner_item: Item, acme: Contractor
    ) -> None:
        services.update_item(alice, owner_item, contract_contractor=acme)
        page = signed_in.get(
            reverse("tasks:create", kwargs={"home_id": alice_home.id}) + f"?item={owner_item.id}"
        ).content.decode()
        select = re.search(r'<select[^>]*name="contractor"[^>]*>(.*?)</select>', page, re.S)
        assert select
        assert re.search(rf'<option value="{acme.id}"[^>]*\bselected\b', select.group(1))


class TestTheApi:
    def url(self, home: Home, item: Item | None = None) -> str:
        if item is None:
            return reverse("api-v1:home-item-list", kwargs={"home_id": home.id})
        return reverse("api-v1:home-item-detail", kwargs={"home_id": home.id, "item_id": item.id})

    def test_a_contractor_of_the_home_is_set_read_and_cleared(
        self,
        api_client: APIClient,
        alice: User,
        alice_home: Home,
        owner_item: Item,
        acme: Contractor,
    ) -> None:
        api_client.force_authenticate(alice)
        done = api_client.patch(
            self.url(alice_home, owner_item), {"contract_contractor": str(acme.id)}, format="json"
        )
        assert done.status_code == 200, done.content
        assert done.json()["contract_contractor"] == str(acme.id)
        assert api_client.get(self.url(alice_home, owner_item)).json()[
            "contract_contractor"
        ] == str(acme.id)
        cleared = api_client.patch(
            self.url(alice_home, owner_item), {"contract_contractor": None}, format="json"
        )
        assert cleared.json()["contract_contractor"] is None

    def test_a_contractor_of_another_home_is_refused(
        self,
        api_client: APIClient,
        alice: User,
        bob: User,
        bob_home: Home,
        alice_home: Home,
        owner_item: Item,
    ) -> None:
        stranger = task_services.create_contractor(bob, bob_home, name="Elsewhere")
        api_client.force_authenticate(alice)
        refused = api_client.patch(
            self.url(alice_home, owner_item),
            {"contract_contractor": str(stranger.id)},
            format="json",
        )
        assert refused.status_code == 400
        assert "contract_contractor" in refused.json()
        owner_item.refresh_from_db()
        assert owner_item.contract_contractor is None

    def test_a_contractor_of_another_home_answers_like_an_unknown_id(
        self,
        api_client: APIClient,
        alice: User,
        bob: User,
        bob_home: Home,
        alice_home: Home,
        owner_item: Item,
    ) -> None:
        # The answer must not tell that the id exists in another home.
        stranger = task_services.create_contractor(bob, bob_home, name="Elsewhere")
        api_client.force_authenticate(alice)
        ids = (stranger.id, uuid.uuid4())
        answers = [
            api_client.patch(
                self.url(alice_home, owner_item),
                {"contract_contractor": str(contractor_id)},
                format="json",
            )
            for contractor_id in ids
        ]
        assert [a.status_code for a in answers] == [400, 400]
        foreign, unknown = (
            [
                message.replace(str(contractor_id), "<id>")
                for message in a.json()["contract_contractor"]
            ]
            for a, contractor_id in zip(answers, ids, strict=True)
        )
        assert foreign == unknown


class TestTheArchive:
    @pytest.fixture
    def linked(self, alice: User, alice_home: Home, owner_item: Item, acme: Contractor) -> Item:
        services.update_item(alice, owner_item, contract_contractor=acme)
        return owner_item

    def test_the_archive_is_in_version_4_and_names_the_contractor(
        self, media_root: Path, alice_home: Home, linked: Item, acme: Contractor
    ) -> None:
        data = archive_bytes(alice_home)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert json.loads(archive.read("manifest.json"))["version"] == 4
            item = json.loads(archive.read("data/rooms.json"))["sections"]["items"][0]
        assert item["contract_contractor"] == str(acme.id)
        assert "contract_provider" not in item

    def test_the_link_comes_back_with_a_restore(
        self, media_root: Path, alice: User, alice_home: Home, linked: Item, acme: Contractor
    ) -> None:
        data = archive_bytes(alice_home)
        home_services.erase_home(alice_home)
        restore.restore_bytes(alice, data)
        assert Item.objects.get(pk=linked.pk).contract_contractor_id == acme.pk

    def test_an_item_pointing_at_a_contractor_missing_from_the_archive_is_refused(
        self, media_root: Path, alice: User, alice_home: Home, linked: Item
    ) -> None:
        files = entries(archive_bytes(alice_home))
        document = json.loads(files["data/rooms.json"])
        document["sections"]["items"][0]["contract_contractor"] = str(uuid.uuid4())
        files["data/rooms.json"] = json.dumps(document).encode()
        home_services.erase_home(alice_home)
        with pytest.raises(restore.ArchiveRefusedError, match="refers to an object it does not"):
            restore.restore_bytes(alice, rebuild(files))

    def version_2(self, home: Home, providers: dict[str, str]) -> bytes:
        """The archive of `home` as version 2 wrote it: the provider is a text of the item."""
        files = entries(archive_bytes(home))
        manifest = json.loads(files["manifest.json"])
        manifest["version"] = 2
        files["manifest.json"] = json.dumps(manifest).encode()
        document = json.loads(files["data/rooms.json"])
        for row in document["sections"]["items"]:
            row.pop("contract_contractor")
            row["contract_provider"] = providers.get(row["name"], "")
        files["data/rooms.json"] = json.dumps(document).encode()
        tasks_doc = json.loads(files["data/tasks.json"])
        files["data/tasks.json"] = json.dumps(tasks_doc).encode()
        return rebuild(files)

    def test_a_version_2_archive_still_restores_and_follows_the_rule_of_the_migration(
        self,
        media_root: Path,
        alice: User,
        alice_home: Home,
        owner_room: Room,
        acme: Contractor,
    ) -> None:
        for name in ("Boiler", "Pump", "Tank", "Fan", "Plain"):
            services.create_item(alice, owner_room, name=name)
        data = self.version_2(
            alice_home,
            {
                "Boiler": "  acme   HEATING ",  # the contractor of the home, whatever its case
                "Pump": "Pool Co",  # nobody of that name: made
                "Tank": "pool co",  # the same one, shared
                "Fan": "x" * 150,  # longer than a name: whole in the note
            },
        )
        home_services.erase_home(alice_home)
        restore.restore_bytes(alice, data)
        by_name = {i.name: i.contract_contractor for i in Item.objects.all()}
        assert by_name["Boiler"].pk == acme.pk
        assert by_name["Plain"] is None
        assert by_name["Pump"] == by_name["Tank"]
        pool = by_name["Pump"]
        assert (pool.name, pool.trade, pool.trade_other) == ("Pool Co", "other", "Maintenance")
        long = by_name["Fan"]
        assert (long.name, long.note) == ("x" * 120, "x" * 150)
        assert Contractor.objects.count() == 3

    def test_a_version_2_archive_without_any_provider_adds_no_contractor(
        self, media_root: Path, alice: User, alice_home: Home, owner_item: Item
    ) -> None:
        data = self.version_2(alice_home, {})
        home_services.erase_home(alice_home)
        restore.restore_bytes(alice, data)
        assert Contractor.objects.count() == 0
        assert Item.objects.get(pk=owner_item.pk).contract_contractor is None

    def test_a_version_5_archive_is_refused(
        self, media_root: Path, alice: User, alice_home: Home
    ) -> None:
        files = entries(archive_bytes(alice_home))
        manifest = json.loads(files["manifest.json"])
        manifest["version"] = 5
        files["manifest.json"] = json.dumps(manifest).encode()
        with pytest.raises(restore.ArchiveRefusedError, match="version 5"):
            restore.restore_bytes(alice, rebuild(files))


class TestTheTables:
    def table(self, home: Home, name: str) -> list[dict[str, str]]:
        data, _name = csv_export.build_csv(home, csv_export.ENTITIES[name])
        return list(csv.DictReader(io.StringIO(data.decode().removeprefix(csv_export.BOM))))

    def import_items(self, actor: User, home: Home, text: str) -> csv_import.Report:
        return csv_import.run(actor, home, {"items": text}, confirm=True)

    def test_the_table_of_items_names_the_contractor_by_its_identifier(
        self, alice: User, alice_home: Home, owner_item: Item, acme: Contractor
    ) -> None:
        services.update_item(alice, owner_item, contract_contractor=acme)
        row = self.table(alice_home, "items")[0]
        assert row["contract_contractor"] == str(acme.id)
        assert "contract_provider" not in row

    def test_a_table_exported_and_imported_in_another_home_links_its_contractors(
        self, alice: User, alice_home: Home, owner_item: Item, acme: Contractor
    ) -> None:
        services.update_item(alice, owner_item, contract_contractor=acme)
        copy = home_services.create_home(alice, name="Copy")
        texts = {
            name: csv_export.build_csv(alice_home, csv_export.ENTITIES[name])[0].decode()
            for name in ("rooms", "contractors", "items")
        }
        report = csv_import.run(alice, copy, texts, confirm=True)
        assert report.written, report.as_data()
        moved = Item.objects.get(home=copy, name=owner_item.name)
        assert moved.contract_contractor.home_id == copy.id
        assert moved.contract_contractor.name == "Acme Heating"

    def test_a_provider_by_name_picks_the_contractor_or_makes_it(
        self, alice: User, alice_home: Home, owner_room: Room, acme: Contractor
    ) -> None:
        text = (
            "name,room,contract_contractor\r\n"
            f"Boiler,{owner_room.name},  acme heating\r\n"
            f"Pump,{owner_room.name},Pool Co\r\n"
            f"Tank,{owner_room.name},POOL  co\r\n"
        )
        report = self.import_items(alice, alice_home, text)
        assert report.written, report.as_data()
        by_name = {i.name: i.contract_contractor for i in Item.objects.all()}
        assert by_name["Boiler"] == acme
        assert by_name["Pump"] == by_name["Tank"]
        assert (by_name["Pump"].name, by_name["Pump"].trade) == ("Pool Co", "other")

    def test_a_file_of_before_with_a_provider_column_still_imports(
        self, alice: User, alice_home: Home, owner_room: Room, acme: Contractor
    ) -> None:
        text = (
            "name,room,contract_provider,contract_number\r\n"
            f"Boiler,{owner_room.name},Acme Heating,C-1\r\n"
            f"Pump,{owner_room.name},Pool Co,\r\n"
            f"Fan,{owner_room.name},,\r\n"
        )
        report = self.import_items(alice, alice_home, text)
        assert report.written, report.as_data()
        assert report.files[0].ignored_columns == []
        by_name = {i.name: i for i in Item.objects.all()}
        assert by_name["Boiler"].contract_contractor == acme
        assert by_name["Boiler"].contract_number == "C-1"
        assert by_name["Pump"].contract_contractor.name == "Pool Co"
        assert by_name["Fan"].contract_contractor is None

    def test_an_identifier_of_another_home_is_refused(
        self,
        alice: User,
        bob: User,
        bob_home: Home,
        alice_home: Home,
        owner_room: Room,
    ) -> None:
        stranger = task_services.create_contractor(bob, bob_home, name="Elsewhere")
        text = f"name,room,contract_contractor\r\nBoiler,{owner_room.name},{stranger.id}\r\n"
        report = self.import_items(alice, alice_home, text)
        assert not report.written
        assert report.refused == 1
        assert not Item.objects.filter(name="Boiler").exists()

    def test_the_preview_of_a_provider_by_name_writes_nothing(
        self, alice: User, alice_home: Home, owner_room: Room
    ) -> None:
        text = f"name,room,contract_contractor\r\nBoiler,{owner_room.name},Pool Co\r\n"
        report = csv_import.run(alice, alice_home, {"items": text}, confirm=False)
        assert report.can_confirm
        assert not Contractor.objects.exists()
        assert not Item.objects.exists()


def test_the_old_text_field_is_gone_from_the_model() -> None:
    assert "contract_provider" not in {f.name for f in Item._meta.get_fields()}
    assert not hasattr(task_services, "contractor_of_provider")


def test_the_export_registry_resolves_the_reference(alice_home: Home) -> None:
    from housecairn.homes import exports

    section = next(s for _a, s in exports.all_sections() if s.model is Item)
    field = next(f for f in section.fields() if f.name == "contract_contractor")
    assert field.related_model is Contractor
    assert field.null

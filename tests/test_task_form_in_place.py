"""The task form: a room, an item and a stock item added in place; the materials; the photos."""

import io
import re
from decimal import Decimal
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from PIL import Image

from housecairn.accounts.models import User
from housecairn.homes.models import Home
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Item, Room
from housecairn.stock import services as stock_services
from housecairn.stock.models import Article
from housecairn.tasks import files, materials, services
from housecairn.tasks.models import Attachment, Task, TaskMaterial
from tests.rooms_support import image_bytes, upload

pytest_plugins = ("tests.tasks_fixtures", "tests.stock_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root", "frozen_today")]

LINE_LIMIT = materials.MAX_MATERIALS_PER_TASK


def url(name: str, home: Home, **kwargs: object) -> str:
    return reverse(f"tasks:{name}", kwargs={"home_id": home.id, **kwargs})


def form(**fields: object) -> dict[str, object]:
    """A task form as the page sends it, with no stock item line."""
    return {
        "title": "Paint the wall",
        "priority": "normal",
        "repeat_unit": "month",
        "repeat_interval": "1",
        "hide_days_before": "0",
        **fields,
    }


def with_lines(*rows: dict[str, str], **fields: object) -> dict[str, object]:
    """The form with the given material lines (the keys of a line: article, quantity, ...)."""
    data = form(**fields)
    data.update(
        {
            "materials-TOTAL_FORMS": str(len(rows)),
            "materials-INITIAL_FORMS": "0",
            "materials-MIN_NUM_FORMS": "0",
            "materials-MAX_NUM_FORMS": str(LINE_LIMIT),
        }
    )
    for index, row in enumerate(rows):
        for key, value in row.items():
            data[f"materials-{index}-{key}"] = value
    return data


def line_count(page: str) -> int:
    return len(set(re.findall(r'name="materials-(\d+)-article"', page)))


class TestNewRoom:
    def test_a_room_is_added_with_the_task(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        response = signed_in.post(
            url("create", alice_home),
            form(room="new", new_room_name="  Back  kitchen ", new_room_kind="kitchen"),
        )
        assert response.status_code == 302
        task = Task.objects.get(title="Paint the wall")
        assert task.room is not None
        assert (task.room.name, task.room.kind) == ("Back kitchen", "kitchen")
        assert Room.objects.filter(home=alice_home).count() == 2

    def test_its_fields_are_ignored_without_the_choice(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        signed_in.post(
            url("create", alice_home),
            form(room=str(owner_room.id), new_room_name="Ignored", new_room_kind="garage"),
        )
        assert Task.objects.get(title="Paint the wall").room == owner_room
        assert not Room.objects.filter(name="Ignored").exists()

    @pytest.mark.parametrize(
        ("name", "error"),
        [
            ("  ", "Give the name of the new room."),
            ("living ROOM", "is already a room of this home"),
        ],
    )
    def test_an_empty_or_known_name_is_refused(
        self, signed_in: Client, alice_home: Home, owner_room: Room, name: str, error: str
    ) -> None:
        response = signed_in.post(url("create", alice_home), form(room="new", new_room_name=name))
        assert response.status_code == 200
        assert error in response.text
        assert not Task.objects.filter(title="Paint the wall").exists()
        assert Room.objects.filter(home=alice_home).count() == 1

    def test_the_field_is_a_list_of_the_home_ending_with_add(
        self, signed_in: Client, alice_home: Home, owner_room: Room, bob_home: Home, bob: User
    ) -> None:
        room_services.create_room(bob, bob_home, name="Not mine")
        page = signed_in.get(url("create", alice_home)).text
        field = page.split('name="room"')[1].split("</select>")[0]
        assert "Living room</option>" in field
        assert "Not mine" not in field
        assert field.rstrip().endswith('<option value="new">Add a room…</option>')
        assert 'data-shown-when="room=new"' in page
        kinds = page.split('name="new_room_kind"')[1].split("</select>")[0]
        assert "Kitchen</option>" in kinds
        assert "Outdoor</option>" in kinds
        # The choices are translated at each request, in the language of the reader.
        page = signed_in.get(url("create", alice_home), HTTP_ACCEPT_LANGUAGE="fr").text
        assert "Ajouter une pièce…" in page
        assert "Cuisine</option>" in page

    def test_a_form_refused_by_the_service_leaves_no_room_behind(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        with mock.patch.object(
            services, "create_task", side_effect=ValidationError({"title": "No."})
        ):
            response = signed_in.post(
                url("create", alice_home), form(room="new", new_room_name="Attic bedroom")
            )
        assert response.status_code == 200
        assert not Room.objects.filter(name="Attic bedroom").exists()

    def test_an_edit_refused_by_the_service_leaves_nothing_behind(
        self, signed_in: Client, alice_home: Home, owner_task: Task
    ) -> None:
        rooms, items, articles = Room.objects.count(), Item.objects.count(), Article.objects.count()
        data = with_lines(
            {"article": "new", "quantity": "1", "new_article_name": "Masking tape"},
            title=owner_task.title,
            room="new",
            new_room_name="Attic bedroom",
            item="new",
            new_item_name="Skylight",
        )
        with mock.patch.object(
            services, "update_task", side_effect=ValidationError({"title": "No."})
        ):
            response = signed_in.post(url("edit", alice_home, task_id=owner_task.id), data)
        assert response.status_code == 200
        assert not Room.objects.filter(name="Attic bedroom").exists()
        assert not Item.objects.filter(name="Skylight").exists()
        assert not Article.objects.filter(name="Masking tape").exists()
        assert (Room.objects.count(), Item.objects.count(), Article.objects.count()) == (
            rooms,
            items,
            articles,
        )

    def test_editing_a_task_can_add_a_room_too(
        self, signed_in: Client, alice_home: Home, owner_task: Task
    ) -> None:
        response = signed_in.post(
            url("edit", alice_home, task_id=owner_task.id),
            form(title=owner_task.title, room="new", new_room_name="Shed", new_room_kind="outdoor"),
        )
        assert response.status_code == 302
        owner_task.refresh_from_db()
        assert owner_task.room is not None
        assert (owner_task.room.name, owner_task.room.kind) == ("Shed", "outdoor")
        assert owner_task.item is None


class TestNewItem:
    def test_an_item_is_added_in_the_room_chosen(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        response = signed_in.post(
            url("create", alice_home),
            form(room=str(owner_room.id), item="new", new_item_name=" Radiator  tap "),
        )
        assert response.status_code == 302
        task = Task.objects.get(title="Paint the wall")
        assert task.item is not None
        assert (task.item.name, task.item.room) == ("Radiator tap", owner_room)
        assert task.room == owner_room

    def test_an_item_is_added_in_a_room_added_in_the_same_send(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        response = signed_in.post(
            url("create", alice_home),
            form(
                room="new",
                new_room_name="Pantry",
                new_room_kind="other",
                item="new",
                new_item_name="Freezer",
            ),
        )
        assert response.status_code == 302
        task = Task.objects.get(title="Paint the wall")
        assert task.item is not None
        assert task.room is not None
        assert task.room.name == "Pantry"
        assert task.item.room == task.room

    def test_without_a_room_the_item_asks_for_one(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        response = signed_in.post(url("create", alice_home), form(item="new", new_item_name="Fan"))
        assert response.status_code == 200
        assert "Choose the room of the new item." in response.text
        assert not Item.objects.filter(name="Fan").exists()
        assert not Task.objects.exists()

    def test_a_room_refused_does_not_ask_for_the_room_again(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        response = signed_in.post(
            url("create", alice_home), form(room="new", item="new", new_item_name="Fan")
        )
        assert "Give the name of the new room." in response.text
        assert "Choose the room of the new item." not in response.text

    @pytest.mark.parametrize(
        ("name", "error"),
        [
            ("", "Give the name of the new item."),
            ("boiler", "is already an item of this room"),
        ],
    )
    def test_an_empty_or_known_name_is_refused(
        self,
        signed_in: Client,
        alice_home: Home,
        owner_item: Item,
        name: str,
        error: str,
    ) -> None:
        response = signed_in.post(
            url("create", alice_home),
            form(room=str(owner_item.room_id), item="new", new_item_name=name),
        )
        assert response.status_code == 200
        assert error in response.text
        assert Item.objects.filter(home=alice_home).count() == 1

    def test_the_same_name_in_another_room_is_allowed(
        self, signed_in: Client, alice: User, alice_home: Home, owner_item: Item
    ) -> None:
        other = room_services.create_room(alice, alice_home, name="Cellar")
        response = signed_in.post(
            url("create", alice_home),
            form(room=str(other.id), item="new", new_item_name="Boiler"),
        )
        assert response.status_code == 302
        assert Item.objects.filter(home=alice_home, name="Boiler").count() == 2

    def test_a_known_item_does_not_go_in_a_new_room(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        response = signed_in.post(
            url("create", alice_home),
            form(room="new", new_room_name="Pantry", item=str(owner_item.id)),
        )
        assert response.status_code == 200
        assert "This item is not in this room." in response.text
        assert not Room.objects.filter(name="Pantry").exists()

    def test_the_field_is_a_list_with_the_room_of_each_item(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        page = signed_in.get(url("create", alice_home)).text
        field = page.split('name="item"')[1].split("</select>")[0]
        assert "Living room · Boiler</option>" in field
        assert field.rstrip().endswith('<option value="new">Add an item…</option>')
        assert 'data-shown-when="item=new"' in page
        page = signed_in.get(url("create", alice_home), HTTP_ACCEPT_LANGUAGE="fr").text
        assert "Ajouter un élément…" in page


class TestReadOnly:
    def test_a_reader_adds_nothing(
        self, client: Client, reader: User, alice_home: Home, owner_room: Room
    ) -> None:
        client.force_login(reader)
        address = url("create", alice_home)
        assert client.get(address).status_code == 403
        sent = with_lines(
            {"article": "new", "new_article_name": "Glue", "quantity": "1"},
            room="new",
            new_room_name="Pantry",
            item="new",
            new_item_name="Fan",
        )
        assert client.post(address, sent).status_code == 403
        assert Room.objects.filter(home=alice_home).count() == 1
        assert not Item.objects.filter(home=alice_home).exists()
        assert not Article.objects.filter(home=alice_home).exists()
        assert not Task.objects.exists()


class TestNewArticle:
    def test_a_stock_item_is_added_with_the_task(self, signed_in: Client, alice_home: Home) -> None:
        response = signed_in.post(
            url("create", alice_home),
            with_lines(
                {
                    "article": "new",
                    "new_article_name": " White  paint ",
                    "new_article_unit": "litre",
                    "quantity": "2.5",
                }
            ),
        )
        assert response.status_code == 302
        article = Article.objects.get(home=alice_home)
        assert (article.name, article.unit) == ("White paint", "litre")
        task = Task.objects.get(title="Paint the wall")
        assert [(m.article, str(m.quantity)) for m in TaskMaterial.objects.filter(task=task)] == [
            (article, "2.50")
        ]

    def test_a_known_and_a_new_item_in_two_lines(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        brush = stock_services.create_article(alice, alice_home, name="Brush", unit="piece")
        signed_in.post(
            url("create", alice_home),
            with_lines(
                {"article": str(brush.id), "quantity": "1"},
                {"article": "new", "new_article_name": "Tape", "quantity": "3"},
                {"article": "", "quantity": ""},
            ),
        )
        task = Task.objects.get(title="Paint the wall")
        assert sorted(m.article.name for m in TaskMaterial.objects.filter(task=task)) == [
            "Brush",
            "Tape",
        ]

    def test_the_unit_other_says_its_words(self, signed_in: Client, alice_home: Home) -> None:
        sent = {
            "article": "new",
            "new_article_name": "Sand",
            "new_article_unit": "other",
            "quantity": "1",
        }
        refused = signed_in.post(url("create", alice_home), with_lines(sent))
        assert "Say which unit." in refused.text
        assert not Article.objects.exists()
        signed_in.post(
            url("create", alice_home), with_lines({**sent, "new_article_unit_other": "bag"})
        )
        article = Article.objects.get(name="Sand")
        assert (article.unit, article.unit_other) == ("other", "bag")

    def test_the_fields_of_a_new_one_are_ignored_without_the_choice(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        signed_in.post(
            url("create", alice_home),
            with_lines({"article": "", "new_article_name": "Ignored", "quantity": ""}),
        )
        assert Task.objects.filter(title="Paint the wall").exists()
        assert not Article.objects.exists()

    @pytest.mark.parametrize(
        ("row", "error"),
        [
            ({"new_article_name": " ", "quantity": "1"}, "Give the name of the new stock item."),
            (
                {"new_article_name": "BRUSH", "quantity": "1"},
                "is already in the stock of this home",
            ),
            ({"new_article_name": "Tape", "quantity": ""}, "Give the quantity."),
        ],
    )
    def test_an_empty_known_or_quantityless_one_is_refused(
        self, signed_in: Client, alice: User, alice_home: Home, row: dict[str, str], error: str
    ) -> None:
        stock_services.create_article(alice, alice_home, name="Brush")
        response = signed_in.post(url("create", alice_home), with_lines({"article": "new", **row}))
        assert response.status_code == 200
        assert error in response.text
        assert not Task.objects.exists()
        assert Article.objects.filter(home=alice_home).count() == 1

    def test_two_lines_do_not_add_the_same_one(self, signed_in: Client, alice_home: Home) -> None:
        row = {"article": "new", "new_article_name": "Tape", "quantity": "1"}
        response = signed_in.post(
            url("create", alice_home), with_lines(row, {**row, "new_article_name": "tape"})
        )
        assert response.status_code == 200
        assert "is already added by another line" in response.text
        assert not Article.objects.exists()

    def test_a_task_refused_leaves_no_stock_item_behind(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        sent = with_lines({"article": "new", "new_article_name": "Tape", "quantity": "1"})
        with mock.patch.object(materials, "set_materials", side_effect=ValidationError("No.")):
            response = signed_in.post(url("create", alice_home), sent)
        assert response.status_code == 200
        assert not Article.objects.exists()
        assert not Task.objects.exists()

    def test_editing_a_task_can_add_one_and_keeps_the_others(
        self, signed_in: Client, alice: User, alice_home: Home, owner_task: Task
    ) -> None:
        glue = stock_services.create_article(alice, alice_home, name="Glue")
        materials.add_material(alice, owner_task, glue, Decimal(1))
        sent = with_lines(
            {"article": str(glue.id), "quantity": "1"},
            {"article": "new", "new_article_name": "Screws", "quantity": "20"},
            title=owner_task.title,
        )
        sent["materials-INITIAL_FORMS"] = "1"  # the page counts the line it showed
        signed_in.post(url("edit", alice_home, task_id=owner_task.id), sent)
        assert sorted(m.article.name for m in materials.materials_of(owner_task)) == [
            "Glue",
            "Screws",
        ]

    def test_the_field_is_a_list_of_the_stock_ending_with_add(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        stock_services.create_article(alice, alice_home, name="Brush")
        page = signed_in.get(url("create", alice_home)).text
        field = page.split('name="materials-0-article"')[1].split("</select>")[0]
        assert "Brush</option>" in field
        assert field.rstrip().endswith('<option value="new">Add a stock item…</option>')
        units = page.split('name="materials-0-new_article_unit"')[1].split("</select>")[0]
        assert "Litre</option>" in units
        assert 'data-shown-when="article=new"' in page
        assert 'data-shown-when="new_article_unit=other"' in page
        page = signed_in.get(url("create", alice_home), HTTP_ACCEPT_LANGUAGE="fr").text
        assert "Ajouter un article…" in page


class TestMaterialLines:
    def test_the_page_offers_one_empty_line_and_the_buttons(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        page = signed_in.get(url("create", alice_home)).text
        assert line_count(page) == 1
        assert f'data-max="{LINE_LIMIT}"' in page
        assert 'name="materials-add"' in page
        # The title is required: without formnovalidate the browser would refuse to add lines.
        assert re.search(r'<button[^>]*name="materials-add"[^>]*formnovalidate', page)
        # How to remove a line without script is said only to those without script.
        assert (
            "<span data-material-nojs>To remove a line, set its stock item to the dash.</span>"
            in page
        )
        # The script shows + and -; without it they stay hidden.
        assert re.search(r"<button[^>]*data-material-add[^>]*hidden", page)
        assert re.search(r"<button[^>]*data-material-remove[^>]*hidden", page)
        # The model of a new line is inert, in a template, with the placeholder of the prefix.
        template = page.split("<template data-material-template>")[1].split("</template>")[0]
        assert 'name="materials-__prefix__-article"' in template
        assert 'data-scope="materials-__prefix__-"' in template
        assert 'aria-live="polite"' in page.split("data-material-limit")[1][:400]

    def test_enter_saves_the_task_not_the_button_that_adds_lines(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        page = signed_in.get(url("create", alice_home)).text
        first = re.search(r'<button type="submit"[^>]*>', page)
        assert first is not None
        assert "materials-add" not in first.group(0)
        assert page.index('visually-hidden" tabindex="-1"') < page.index('name="materials-add"')

    def test_add_a_line_shows_the_form_again_with_one_more_and_saves_nothing(
        self, signed_in: Client, alice: User, alice_home: Home, owner_room: Room
    ) -> None:
        brush = stock_services.create_article(alice, alice_home, name="Brush")
        sent = with_lines(
            {"article": str(brush.id), "quantity": "2"},
            {"article": "new", "new_article_name": "Tape", "new_article_unit": "roll"},
            {"article": "", "quantity": ""},
            title="",
            notes="Keep me",
            room=str(owner_room.id),
        )
        sent["materials-add"] = "1"
        response = signed_in.post(url("create", alice_home), sent)
        assert response.status_code == 200
        page = response.text
        # 3 sent lines + 1 more, with what was typed, and no error shown for the empty title.
        assert line_count(page) == 4
        assert "Keep me" in page
        assert f'value="{brush.id}" selected' in page
        assert f'value="{owner_room.id}" selected' in page
        assert 'value="Tape"' in page
        assert 'class="errorlist' not in page
        assert "This field is required" not in page
        assert not Task.objects.exists()
        assert Article.objects.filter(home=alice_home).count() == 1

    def test_add_lines_stops_at_the_limit(self, signed_in: Client, alice_home: Home) -> None:
        rows = [{"article": "", "quantity": ""}] * (LINE_LIMIT - 1)
        sent = with_lines(*rows)
        sent["materials-add"] = "1"
        page = signed_in.post(url("create", alice_home), sent).text
        assert line_count(page) == LINE_LIMIT
        assert 'name="materials-add"' not in page
        assert f"A task lists at most {LINE_LIMIT} stock items." in page
        assert 'visually-hidden" tabindex="-1"' not in page

    def test_add_lines_on_a_made_up_count_is_harmless(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        sent = form()
        sent["materials-TOTAL_FORMS"] = "lots"
        sent["materials-add"] = "1"
        page = signed_in.post(url("create", alice_home), sent).text
        assert line_count(page) == 1
        sent["materials-TOTAL_FORMS"] = "100000"
        page = signed_in.post(url("create", alice_home), sent).text
        assert line_count(page) == LINE_LIMIT

    def test_add_lines_on_the_edit_form_changes_nothing_stored(
        self, signed_in: Client, alice: User, alice_home: Home, owner_task: Task
    ) -> None:
        sent = with_lines({"article": "", "quantity": ""}, title="Renamed")
        sent["materials-add"] = "1"
        response = signed_in.post(url("edit", alice_home, task_id=owner_task.id), sent)
        assert response.status_code == 200
        assert 'value="Renamed"' in response.text
        owner_task.refresh_from_db()
        assert owner_task.title != "Renamed"

    def test_a_reader_cannot_add_lines(
        self, client: Client, reader: User, alice_home: Home
    ) -> None:
        client.force_login(reader)
        sent = form()
        sent["materials-add"] = "1"
        assert client.post(url("create", alice_home), sent).status_code == 403

    def test_fifty_lines_are_saved(self, signed_in: Client, alice_home: Home) -> None:
        rows = [
            {"article": "new", "new_article_name": f"Item {n}", "quantity": "1"}
            for n in range(LINE_LIMIT)
        ]
        assert signed_in.post(url("create", alice_home), with_lines(*rows)).status_code == 302
        assert TaskMaterial.objects.count() == LINE_LIMIT


class TestPhotosOnThePage:
    def test_the_photos_of_the_item_are_shown_read_only(
        self, signed_in: Client, alice: User, alice_home: Home, owner_task: Task, owner_item: Item
    ) -> None:
        photo = room_services.add_photo(alice, upload=upload(image_bytes()), item=owner_item)
        page = signed_in.get(url("detail", alice_home, task_id=owner_task.id)).text
        assert "Photos of the item" in page
        assert (
            reverse(
                "rooms:photo-thumbnail", kwargs={"home_id": alice_home.id, "photo_id": photo.id}
            )
            in page
        )
        assert "Delete" not in page.split("Photos of the item")[1].split("Photos and files")[0]
        sheet = reverse(
            "rooms:item-detail", kwargs={"home_id": alice_home.id, "item_id": owner_item.id}
        )
        assert f'href="{sheet}?next=' in page
        assert "All photos of the item" in page

    def test_without_an_item_the_photos_of_the_room_are_shown(
        self, signed_in: Client, alice: User, alice_home: Home, owner_task: Task, owner_room: Room
    ) -> None:
        owner_task.item = None
        owner_task.save()
        photo = room_services.add_photo(alice, upload=upload(image_bytes()), room=owner_room)
        page = signed_in.get(url("detail", alice_home, task_id=owner_task.id)).text
        assert "Photos of the room" in page
        assert str(photo.id) in page
        assert "All photos of the room" in page

    def test_no_photo_no_heading(
        self, signed_in: Client, alice_home: Home, owner_task: Task
    ) -> None:
        page = signed_in.get(url("detail", alice_home, task_id=owner_task.id)).text
        assert "Photos of the item" not in page
        assert "Photos of the room" not in page

    def test_the_photos_of_another_home_are_never_shown(
        self,
        signed_in: Client,
        bob: User,
        bob_home: Home,
        alice_home: Home,
        owner_task: Task,
        owner_item: Item,
    ) -> None:
        foreign_room = room_services.create_room(bob, bob_home, name="Elsewhere")
        foreign_item = room_services.create_item(bob, foreign_room, name="Stranger")
        photo = room_services.add_photo(bob, upload=upload(image_bytes()), item=foreign_item)
        # The task cannot name it through the services: force the link in the database.
        Task.objects.filter(pk=owner_task.pk).update(item=foreign_item)
        page = signed_in.get(url("detail", alice_home, task_id=owner_task.id)).text
        assert str(photo.id) not in page
        assert "Photos of the item" not in page

    def test_a_reader_sees_them_too(
        self,
        client: Client,
        alice: User,
        reader: User,
        alice_home: Home,
        owner_task: Task,
        owner_item: Item,
    ) -> None:
        photo = room_services.add_photo(alice, upload=upload(image_bytes()), item=owner_item)
        client.force_login(reader)
        page = client.get(url("detail", alice_home, task_id=owner_task.id)).text
        assert str(photo.id) in page

    def test_at_most_six_are_shown_the_sheet_has_the_rest(
        self, signed_in: Client, alice: User, alice_home: Home, owner_task: Task, owner_item: Item
    ) -> None:
        for _ in range(8):
            room_services.add_photo(alice, upload=upload(image_bytes()), item=owner_item)
        page = signed_in.get(url("detail", alice_home, task_id=owner_task.id)).text
        block = page.split("Photos of the item")[1].split("All photos of the item")[0]
        assert block.count("<img") == 6


def mpo_bytes() -> bytes:
    """A multi-picture JPEG, as an iPhone writes (a main image and a second picture)."""
    first = Image.new("RGB", (64, 48), "red")
    second = Image.new("RGB", (32, 24), "blue")
    buffer = io.BytesIO()
    first.save(buffer, format="MPO", save_all=True, append_images=[second])
    return buffer.getvalue()


HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64


class TestPhotosSentFromAPhone:
    def test_a_multi_picture_jpeg_is_accepted_and_written_again_as_a_jpeg(
        self, signed_in: Client, alice_home: Home, owner_task: Task
    ) -> None:
        data = mpo_bytes()
        assert Image.open(io.BytesIO(data)).format == "MPO"
        response = signed_in.post(
            url("attachment-add", alice_home, task_id=owner_task.id),
            {"uploads": [upload(data, "IMG_0001.JPG")]},
        )
        assert response.status_code == 302
        attachment = Attachment.objects.get(task=owner_task)
        assert attachment.is_image
        assert attachment.content_type == "image/jpeg"

    def test_a_heic_photo_is_refused_with_its_own_message(
        self, signed_in: Client, alice_home: Home, owner_task: Task
    ) -> None:
        response = signed_in.post(
            url("attachment-add", alice_home, task_id=owner_task.id),
            {"uploads": [upload(HEIC, "IMG_0002.HEIC", "image/heic")]},
        )
        assert response.status_code == 200
        assert "HEIC photos are not accepted" in response.text
        assert "IMG_0002.HEIC" in response.text
        assert not Attachment.objects.filter(task=owner_task).exists()

    def test_a_heic_photo_sent_with_the_task_form_is_reported_and_the_task_kept(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        response = signed_in.post(
            url("create", alice_home),
            form(uploads=[upload(HEIC, "IMG_0003.HEIC", "image/heic")]),
            follow=True,
        )
        assert "HEIC photos are not accepted" in response.text
        assert Task.objects.filter(title="Paint the wall").exists()

    def test_other_unknown_files_keep_the_general_message(self) -> None:
        with pytest.raises(ValidationError, match="This type of file is not accepted"):
            files.detect(b"MZ\x90\x00\x03")

    def test_the_chooser_offers_what_a_phone_converts_to_jpeg(
        self, signed_in: Client, alice_home: Home, owner_task: Task
    ) -> None:
        # Naming the types, not "image/*", makes an iPhone hand over a JPEG rather than a HEIC.
        accept = files.ACCEPT_ATTRIBUTE
        assert "image/jpeg" in accept
        assert "image/*" not in accept
        assert "heic" not in accept.lower()
        for page in (
            signed_in.get(url("create", alice_home)).text,
            signed_in.get(url("attachment-add", alice_home, task_id=owner_task.id)).text,
        ):
            assert f'accept="{accept}"' in page


class TestNewArticleFitsTheItemOfTheTask:
    """A stock item made in the task form is compatible with the equipment of the task."""

    def line(self) -> dict[str, str]:
        return {"article": "new", "quantity": "1", "new_article_name": "Gasket"}

    def test_with_an_item_that_exists(
        self, signed_in: Client, alice_home: Home, owner_room: Room, owner_item: Item
    ) -> None:
        data = with_lines(self.line(), room=str(owner_room.id), item=str(owner_item.id))
        assert signed_in.post(url("create", alice_home), data).status_code == 302
        article = Article.objects.get(name="Gasket")
        assert list(article.compatible_items.all()) == [owner_item]

    def test_with_an_item_made_in_the_same_form(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        data = with_lines(
            self.line(), room=str(owner_room.id), item="new", new_item_name="Skylight"
        )
        assert signed_in.post(url("create", alice_home), data).status_code == 302
        article = Article.objects.get(name="Gasket")
        assert [i.name for i in article.compatible_items.all()] == ["Skylight"]

    def test_editing_a_task_with_an_item(
        self, signed_in: Client, alice_home: Home, owner_task: Task, owner_item: Item
    ) -> None:
        Task.objects.filter(id=owner_task.id).update(item=owner_item, room=owner_item.room)
        data = with_lines(
            self.line(),
            title=owner_task.title,
            room=str(owner_item.room_id),
            item=str(owner_item.id),
        )
        assert (
            signed_in.post(url("edit", alice_home, task_id=owner_task.id), data).status_code == 302
        )
        assert list(Article.objects.get(name="Gasket").compatible_items.all()) == [owner_item]

    def test_without_an_item_it_fits_nothing(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        data = with_lines(self.line(), room=str(owner_room.id))
        assert signed_in.post(url("create", alice_home), data).status_code == 302
        assert not Article.objects.get(name="Gasket").compatible_items.exists()

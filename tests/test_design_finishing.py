"""Finishing touches of the design (design/patterns.md): the icons of More, the library of
task templates drawn as lists, the empty photos of an item, \"Planned\" on the budget lines of a
project, \"Add from a template\" on a room, one action row, and the language of the sign-in page
chosen in one tap."""

import re
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from housecairn.accounts.models import User
from housecairn.core import more
from housecairn.homes.models import Home
from housecairn.projects.models import Project
from housecairn.rooms.models import Item, Room

pytest_plugins = ("tests.rooms_fixtures", "tests.projects_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root")]

ICONS = Path(more.__file__).resolve().parents[1] / "templates" / "components" / "icon.html"


def main_part(html: str) -> str:
    return html.split('<main id="main"', 1)[1]


def head_card(html: str) -> str:
    """The card under the title of a record page, which holds its action row."""
    return main_part(html).split('<section class="card">', 1)[1].split("</section>", 1)[0]


def button_rows(fragment: str) -> list[str]:
    return re.findall(r'<div class="button-row">(.*?)</div>', fragment, re.S)


class TestIconsOfMore:
    def test_every_entry_has_an_icon_the_icon_set_draws(
        self, alice: User, alice_home: Home
    ) -> None:
        admin = User.objects.create_user(
            email="root@example.com", password="x" * 12, is_staff=True, is_superuser=True
        )
        drawn = set(re.findall(r'name == "([a-z-]+)"', ICONS.read_text()))
        for who in (alice, admin):
            entries = [e for g in more.groups(who, alice_home.id) for e in g.entries]
            assert entries
            for entry in entries:
                assert entry.icon, f"{entry.label} has no icon"
                assert entry.icon in drawn, f"{entry.label}: icon {entry.icon} is not drawn"

    def test_the_page_draws_one_icon_per_entry_before_its_text(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        page = signed_in.get(reverse("more")).content.decode()
        rows = re.findall(r'<a class="row" href="[^"]*">(.*?)</a>', main_part(page), re.S)
        assert len(rows) >= 8
        for row in rows:
            assert row.lstrip().startswith('<svg class="icon row-icon"'), row[:80]
            assert row.count("<svg") == 2  # its icon and the chevron


class TestTemplatesPage:
    def page(self, home: Home) -> str:
        return reverse("tasks:templates", kwargs={"home_id": home.id})

    def test_the_library_is_lists_of_rows_not_native_fieldsets(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        html = main_part(signed_in.get(self.page(alice_home)).content.decode())
        assert "<fieldset" not in html
        assert "<legend" not in html
        groups = re.findall(r'<section aria-labelledby="templates-group-\d+">', html)
        assert len(groups) >= 3
        assert html.count('class="row template-row"') >= 10

    def test_each_box_is_ticked_by_its_label_which_covers_the_row(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        html = main_part(signed_in.get(self.page(alice_home)).content.decode())
        pairs = re.findall(r'name="templates" value="([^"]+)" id="([^"]+)"', html)
        assert pairs
        for _value, box_id in pairs:
            assert f'<label class="row-title" for="{box_id}">' in html
        css = (ICONS.parents[2] / "static" / "css" / "tasks.css").read_text()
        assert ".template-row .row-title::after" in css

    def test_the_titles_keep_the_colour_of_every_other_row_title(self) -> None:
        css = (ICONS.parents[2] / "static" / "css" / "tasks.css").read_text()
        rule = re.search(r"\.template-row \.row-title \{([^}]*)\}", css)
        assert rule, "the title of a template row must override the global label rule"
        assert "color: var(--hc-color-text);" in rule.group(1)
        assert "margin-bottom: 0;" in rule.group(1)

    def test_the_two_buttons_at_the_end_are_spaced(self) -> None:
        css = (ICONS.parents[2] / "static" / "css" / "components.css").read_text()
        assert re.search(
            r"\.button-block \+ \.button-block \{\s*margin-top: var\(--hc-space-2\);", css
        )

    def test_the_room_of_the_page_it_was_opened_from_is_chosen(
        self, signed_in: Client, owner_room: Room
    ) -> None:
        html = signed_in.get(self.page(owner_room.home), {"room": str(owner_room.id)}).text
        assert re.search(rf'<option value="{owner_room.id}" selected>', html)

    def test_a_room_of_another_home_or_garbage_chooses_nothing(
        self, signed_in: Client, alice_home: Home, bob_home: Home, bob: User
    ) -> None:
        from housecairn.rooms import services

        elsewhere = services.create_room(bob, bob_home, name="Elsewhere")
        for raw in (str(elsewhere.id), "not-a-uuid"):
            html = signed_in.get(self.page(alice_home), {"room": raw}).text
            field = html.split('name="room"', 1)[1].split("</select>", 1)[0]
            assert re.findall(r"<option [^>]*selected>", field) == ['<option value="" selected>']


class TestRoomPage:
    def test_it_offers_the_templates_for_this_room(
        self, signed_in: Client, owner_room: Room
    ) -> None:
        html = signed_in.get(
            reverse(
                "rooms:detail", kwargs={"home_id": owner_room.home_id, "room_id": owner_room.id}
            )
        ).text
        link = reverse("tasks:templates", kwargs={"home_id": owner_room.home_id})
        assert re.search(rf'href="{link}\?room={owner_room.id}[^"]*">Add from a template</a>', html)

    def test_a_reader_is_not_offered_it(
        self, client: Client, reader: User, owner_room: Room
    ) -> None:
        client.force_login(reader)
        html = client.get(
            reverse(
                "rooms:detail", kwargs={"home_id": owner_room.home_id, "room_id": owner_room.id}
            )
        ).text
        assert "Add from a template" not in html

    def test_the_actions_of_the_room_are_one_row(self, signed_in: Client, owner_room: Room) -> None:
        html = signed_in.get(
            reverse(
                "rooms:detail", kwargs={"home_id": owner_room.home_id, "room_id": owner_room.id}
            )
        ).text
        rows = button_rows(head_card(html))
        assert len(rows) == 1
        assert [
            bool(re.search(label, rows[0]))
            for label in ("Edit the room", "QR label", "Report a problem")
        ] == [
            True,
            True,
            True,
        ]

    def test_a_reader_sees_the_row_without_edit(
        self, client: Client, reader: User, owner_room: Room
    ) -> None:
        client.force_login(reader)
        html = client.get(
            reverse(
                "rooms:detail", kwargs={"home_id": owner_room.home_id, "room_id": owner_room.id}
            )
        ).text
        rows = button_rows(head_card(html))
        assert len(rows) == 1
        assert "Edit the room" not in rows[0]
        assert "Report a problem" in rows[0]


class TestItemPage:
    def page(self, item: Item) -> str:
        return reverse("rooms:item-detail", kwargs={"home_id": item.home_id, "item_id": item.id})

    def test_the_actions_of_the_item_are_one_row(self, signed_in: Client, owner_item: Item) -> None:
        rows = button_rows(head_card(signed_in.get(self.page(owner_item)).text))
        assert len(rows) == 1
        for label in ("Edit the item", "QR label", "Report a problem"):
            assert label in rows[0] or label.replace("QR label", "Make a QR label") in rows[0]

    def test_no_photo_shows_the_dashed_frame_then_the_action(
        self, signed_in: Client, owner_item: Item
    ) -> None:
        html = signed_in.get(self.page(owner_item)).text
        photos = html.split("<h2>Photos</h2>", 1)[1].split("<h2>", 1)[0]
        frame = photos.index('<p class="empty">No photo yet.</p>')
        assert frame < photos.index("Add a photo")

    def test_a_reader_sees_the_frame_without_the_action(
        self, client: Client, reader: User, owner_item: Item
    ) -> None:
        client.force_login(reader)
        photos = client.get(self.page(owner_item)).text.split("<h2>Photos</h2>", 1)[1]
        photos = photos.split("<h2>", 1)[0]
        assert '<p class="empty">No photo yet.</p>' in photos
        assert "Add a photo" not in photos


class TestPlannedOnBudgetLines:
    def test_the_lines_of_a_project_say_planned_as_an_amount(
        self, signed_in: Client, living_room_project: Project
    ) -> None:
        page = reverse(
            "projects:detail",
            kwargs={"home_id": living_room_project.home_id, "project_id": living_room_project.id},
        )
        html = main_part(signed_in.get(page, headers={"Accept-Language": "fr"}).text)
        table = html.split('<table class="table-stack">', 1)[1].split("</table>", 1)[0]
        assert '<th scope="col">Prévu</th>' in table
        assert 'data-label="Prévu"' in table
        assert "Prévue" not in table


class TestLanguageOfTheSignInPage:
    def test_choosing_a_language_is_one_tap_with_no_change_button(self, client: Client) -> None:
        html = client.get(reverse("account_login"), headers={"Accept-Language": "fr"}).text
        form = html.split('class="language-switch"', 1)[1].split("</form>", 1)[0]
        assert "<select" not in form
        assert "Changer" not in form
        buttons = re.findall(r"<button [^>]*>[^<]*</button>", form)
        assert [re.sub(r"<[^>]+>", "", b) for b in buttons] == ["English", "Français"]
        assert [("aria-current" in b) for b in buttons] == [False, True]

    def test_a_tap_sets_the_cookie(self, client: Client) -> None:
        response = client.post(reverse("set_language"), {"language": "en"})
        assert response.cookies["django_language"].value == "en"

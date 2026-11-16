"""The return rule (core/navigation.py, docs/developer/design-guide.md): people come back
to the screen they came from, `next` is checked, never nested, never shown.

The journeys are those measured by the navigation review: a task created from an item,
a task opened from Today; then hostile `next` values, the label of the back link, and
deletions.
"""

import re
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse

from housecairn.accounts.models import User
from housecairn.core import navigation
from housecairn.homes.models import Home
from housecairn.rooms import services as room_services
from housecairn.rooms.models import Item, Room
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Contractor, Task

pytest_plugins = ("tests.rooms_fixtures", "tests.tasks_fixtures")

pytestmark = pytest.mark.django_db

TASK_FORM = {
    "title": "Bleed the radiators",
    "priority": "normal",
    "repeat_mode": "once",
    "repeat_interval": "1",
    "repeat_unit": "month",
    "hide_days_before": "0",
}

HOSTILE = [
    "https://evil.example.com/",
    "//evil.example.com/",
    "/\\evil.example.com/",
    "javascript:alert(1)",
    "/h/x/?a=1\r\nSet-Cookie: x=1",
    "/rooms/\nx",
    "/" + "a" * 5000,
]


def back_links(html: str) -> list[tuple[str, str]]:
    """(href, text) of the back links of a page."""
    found = re.findall(r'<a class="back-link" href="([^"]*)"[^>]*>(.*?)</a>', html, re.S)
    return [
        (href.replace("&amp;", "&"), re.sub(r"<[^>]+>", "", text).strip()) for href, text in found
    ]


def href_of(html: str, path: str) -> str:
    """The first link of the page whose address starts with `path`."""
    match = re.search(rf'href="({re.escape(path)}[^"]*)"', html)
    assert match, path
    return match.group(1).replace("&amp;", "&")


def query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def rooms(name: str, **kwargs: object) -> str:
    return reverse(f"rooms:{name}", kwargs=kwargs)


def tasks(name: str, **kwargs: object) -> str:
    return reverse(f"tasks:{name}", kwargs=kwargs)


# ---------- the helper ----------


class TestBackUrl:
    def request(self, next_url: str, method: str = "get") -> object:
        factory = RequestFactory()
        if method == "post":
            return factory.post("/", {"next": next_url})
        return factory.get("/", {"next": next_url})

    def test_a_relative_path_is_kept(self) -> None:
        assert navigation.back_url(self.request("/h/1/today/"), "/d/") == "/h/1/today/"
        assert navigation.back_url(self.request("/x/", "post"), "/d/") == "/x/"

    @pytest.mark.parametrize("hostile", HOSTILE)
    def test_a_hostile_next_gives_the_default(self, hostile: str) -> None:
        assert navigation.back_url(self.request(hostile), "/d/") == "/d/"
        assert navigation.back_url(self.request(hostile, "post"), "/d/") == "/d/"

    def test_a_path_that_is_not_absolute_is_refused(self) -> None:
        assert navigation.back_url(self.request("rooms/"), "/d/") == "/d/"

    def test_a_nested_next_is_removed(self) -> None:
        nested = "/h/1/rooms/?next=" + quote("/h/1/today/?next=https://evil.example.com/")
        assert navigation.back_url(self.request(nested), "/d/") == "/h/1/rooms/"

    def test_a_transient_undo_is_removed(self) -> None:
        assert (
            navigation.back_url(self.request("/l/?status=open&undo=1"), "/d/") == "/l/?status=open"
        )

    def test_a_long_query_is_dropped_and_the_path_kept(self) -> None:
        target = "/h/1/tasks/?q=" + "a" * 2000
        assert navigation.back_url(self.request(target), "/d/") == "/h/1/tasks/"

    def test_with_next_replaces_any_next(self) -> None:
        assert navigation.with_next("/a/?next=/x/&b=1", "/c/") == "/a/?b=1&next=%2Fc%2F"

    def test_stay_keeps_a_safe_next(self) -> None:
        request = self.request("/h/1/rooms/")
        assert navigation.stay(request, "/h/1/tasks/3/") == "/h/1/tasks/3/?next=%2Fh%2F1%2Frooms%2F"

    def test_stay_drops_a_hostile_next(self) -> None:
        request = self.request("https://evil.example.com/")
        assert navigation.stay(request, "/h/1/tasks/3/") == "/h/1/tasks/3/"

    def test_stay_without_next_leaves_the_page_alone(self) -> None:
        assert navigation.stay(self.request(""), "/h/1/tasks/3/") == "/h/1/tasks/3/"


# ---------- a task created from an item ----------


class TestTaskFromItem:
    def test_the_link_carries_the_item_as_next(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        item_url = rooms("item-detail", home_id=alice_home.id, item_id=owner_item.id)
        html = signed_in.get(item_url).content.decode()
        link = href_of(html, tasks("create", home_id=alice_home.id))
        assert query(link)["next"] == [item_url]
        assert query(link)["item"] == [str(owner_item.id)]

    def test_the_form_goes_back_to_the_item_named_by_its_route(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        item_url = rooms("item-detail", home_id=alice_home.id, item_id=owner_item.id)
        form_url = (
            tasks("create", home_id=alice_home.id)
            + "?"
            + f"item={owner_item.id}&next={quote(item_url)}"
        )
        html = signed_in.get(form_url).content.decode()
        assert back_links(html) == [(item_url, "Boiler")]
        assert f'<input type="hidden" name="next" value="{item_url}">' in html

    def test_after_saving_the_person_is_back_on_the_item_with_added_open(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        item_url = rooms("item-detail", home_id=alice_home.id, item_id=owner_item.id)
        response = signed_in.post(
            tasks("create", home_id=alice_home.id),
            {**TASK_FORM, "item": owner_item.id, "next": item_url},
        )
        assert response.status_code == 302
        assert response["Location"] == item_url
        task = Task.objects.get(title="Bleed the radiators")
        html = signed_in.get(item_url).content.decode()
        task_url = tasks("detail", home_id=alice_home.id, task_id=task.id)
        opened = navigation.with_next(task_url, item_url)
        assert f'Task added · <a href="{opened}">Open</a>' in html
        # The task opened from the banner comes back to the item, not to Tasks.
        assert back_links(signed_in.get(opened).content.decode()) == [(item_url, "Boiler")]

    def test_an_error_keeps_next_in_the_form(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        item_url = rooms("item-detail", home_id=alice_home.id, item_id=owner_item.id)
        response = signed_in.post(
            tasks("create", home_id=alice_home.id), {**TASK_FORM, "title": "", "next": item_url}
        )
        assert response.status_code == 200
        html = response.content.decode()
        assert f'<input type="hidden" name="next" value="{item_url}">' in html
        assert back_links(html) == [(item_url, "Boiler")]

    def test_from_the_task_list_the_new_task_opens(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        listing = tasks("list", home_id=alice_home.id) + "?status=open"
        response = signed_in.post(
            tasks("create", home_id=alice_home.id), {**TASK_FORM, "next": listing}
        )
        task = Task.objects.get(title="Bleed the radiators")
        assert response["Location"] == tasks("detail", home_id=alice_home.id, task_id=task.id)

    def test_an_item_created_from_its_room_goes_back_to_the_room(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        room_url = rooms("detail", home_id=alice_home.id, room_id=owner_room.id)
        html = signed_in.get(room_url).content.decode()
        link = href_of(html, rooms("item-create", home_id=alice_home.id, room_id=owner_room.id))
        assert query(link)["next"] == [room_url]
        response = signed_in.post(
            link, {"name": "Radiator", "kind": "equipment", "room": owner_room.id, "next": room_url}
        )
        assert response["Location"] == room_url

    def test_a_room_created_from_the_room_list_opens(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        listing = rooms("list", home_id=alice_home.id)
        html = signed_in.get(listing).content.decode()
        link = href_of(html, rooms("create", home_id=alice_home.id))
        assert query(link)["next"] == [listing]
        response = signed_in.post(link, {"name": "Attic", "kind": "other", "next": listing})
        room = Room.objects.get(name="Attic")
        assert response["Location"] == rooms("detail", home_id=alice_home.id, room_id=room.id)

    def test_a_contractor_created_from_a_task_goes_back_to_it(
        self, signed_in: Client, alice_home: Home, owner_task: Task
    ) -> None:
        task_url = tasks("detail", home_id=alice_home.id, task_id=owner_task.id)
        response = signed_in.post(
            tasks("contractor-create", home_id=alice_home.id),
            {"name": "Plumber Co", "trade": "plumber", "next": task_url},
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        assert response["Location"] == task_url
        assert Contractor.objects.filter(name="Plumber Co").exists()


# ---------- a task opened from Today ----------


class TestTaskFromToday:
    def test_the_back_link_names_today_and_leads_there(
        self, signed_in: Client, alice_home: Home, owner_task: Task
    ) -> None:
        today = tasks("today", home_id=alice_home.id)
        detail = tasks("detail", home_id=alice_home.id, task_id=owner_task.id)
        html = signed_in.get(f"{detail}?next={quote(today)}").content.decode()
        assert back_links(html) == [(today, "Today")]

    def test_without_next_the_back_link_is_the_parent(
        self, signed_in: Client, alice_home: Home, owner_task: Task
    ) -> None:
        detail = tasks("detail", home_id=alice_home.id, task_id=owner_task.id)
        html = signed_in.get(detail).content.decode()
        assert back_links(html) == [(tasks("list", home_id=alice_home.id), "Tasks")]

    def test_the_links_of_the_task_carry_it_as_next(
        self, signed_in: Client, alice_home: Home, owner_task: Task
    ) -> None:
        today = tasks("today", home_id=alice_home.id)
        detail = tasks("detail", home_id=alice_home.id, task_id=owner_task.id)
        html = signed_in.get(f"{detail}?next={quote(today)}").content.decode()
        edit = href_of(html, tasks("edit", home_id=alice_home.id, task_id=owner_task.id))
        # Never nested: the task page is the origin of its edit form, without its own next.
        assert query(edit)["next"] == [detail]

    def test_an_edit_goes_back_to_next(
        self, signed_in: Client, alice_home: Home, owner_task: Task
    ) -> None:
        today = tasks("today", home_id=alice_home.id)
        response = signed_in.post(
            tasks("edit", home_id=alice_home.id, task_id=owner_task.id),
            {**TASK_FORM, "next": today},
        )
        assert response["Location"] == today


# ---------- hostile next and labels ----------


class TestHostileNext:
    @pytest.mark.parametrize("hostile", HOSTILE)
    def test_the_back_link_and_the_redirect_ignore_it(
        self, signed_in: Client, alice_home: Home, owner_task: Task, hostile: str
    ) -> None:
        detail = tasks("detail", home_id=alice_home.id, task_id=owner_task.id)
        html = signed_in.get(detail, {"next": hostile}).content.decode()
        assert back_links(html) == [(tasks("list", home_id=alice_home.id), "Tasks")]
        response = signed_in.post(
            tasks("edit", home_id=alice_home.id, task_id=owner_task.id),
            {**TASK_FORM, "next": hostile},
        )
        assert response["Location"] == detail

    def test_the_label_never_repeats_the_parameter(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        room_url = rooms("detail", home_id=alice_home.id, room_id=owner_room.id)
        forged = rooms("list", home_id=alice_home.id) + "?label=Pay-here&title=<b>Pay-here</b>"
        html = signed_in.get(room_url, {"next": forged}).content.decode()
        expected = (
            rooms("list", home_id=alice_home.id) + "?label=Pay-here&title=%3Cb%3EPay-here%3C%2Fb%3E"
        )
        assert back_links(html) == [(expected, "Rooms")]
        assert "Pay-here</a>" not in html
        assert "<b>Pay-here" not in html

    def test_a_record_of_another_home_is_not_named(
        self, signed_in: Client, alice_home: Home, owner_room: Room, bob: User, bob_home: Home
    ) -> None:
        secret = room_services.create_room(bob, bob_home, name="Bob's safe room")
        room_url = rooms("detail", home_id=alice_home.id, room_id=owner_room.id)
        foreign = rooms("detail", home_id=bob_home.id, room_id=secret.id)
        html = signed_in.get(room_url, {"next": foreign}).content.decode()
        assert "Bob's safe room" not in html
        assert back_links(html) == [(rooms("list", home_id=alice_home.id), "Rooms")]

    def test_an_unnamed_route_falls_back_to_the_parent(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        room_url = rooms("detail", home_id=alice_home.id, room_id=owner_room.id)
        html = signed_in.get(room_url, {"next": "/no/such/page/"}).content.decode()
        assert back_links(html) == [(rooms("list", home_id=alice_home.id), "Rooms")]


# ---------- deletions ----------


class TestDeletion:
    def test_a_task_deleted_from_its_own_page_lands_on_the_list(
        self, signed_in: Client, alice_home: Home, owner_task: Task
    ) -> None:
        detail = tasks("detail", home_id=alice_home.id, task_id=owner_task.id)
        html = signed_in.get(
            tasks("edit", home_id=alice_home.id, task_id=owner_task.id)
        ).content.decode()
        link = href_of(html, tasks("delete", home_id=alice_home.id, task_id=owner_task.id))
        confirm = signed_in.get(link).content.decode()
        assert '<input type="hidden" name="next"' in confirm
        response = signed_in.post(link, {"next": detail})
        assert response["Location"] == tasks("list", home_id=alice_home.id)

    def test_a_task_deleted_with_an_unrelated_next_goes_there(
        self, signed_in: Client, alice_home: Home, owner_task: Task
    ) -> None:
        today = tasks("today", home_id=alice_home.id)
        response = signed_in.post(
            tasks("delete", home_id=alice_home.id, task_id=owner_task.id), {"next": today}
        )
        assert response["Location"] == today

    def test_a_room_deleted_never_returns_to_one_of_its_items(
        self, signed_in: Client, alice_home: Home, owner_room: Room, owner_item: Item
    ) -> None:
        child = rooms("item-detail", home_id=alice_home.id, item_id=owner_item.id)
        response = signed_in.post(
            rooms("delete", home_id=alice_home.id, room_id=owner_room.id), {"next": child}
        )
        assert response["Location"] == rooms("list", home_id=alice_home.id)

    def test_an_item_deleted_from_its_edit_form_lands_on_its_room(
        self, signed_in: Client, alice_home: Home, owner_room: Room, owner_item: Item
    ) -> None:
        edit = rooms("item-edit", home_id=alice_home.id, item_id=owner_item.id)
        response = signed_in.post(
            rooms("item-delete", home_id=alice_home.id, item_id=owner_item.id), {"next": edit}
        )
        assert response["Location"] == rooms("detail", home_id=alice_home.id, room_id=owner_room.id)

    def test_the_cancel_of_the_confirmation_leads_to_next(
        self, signed_in: Client, alice_home: Home, owner_room: Room, owner_item: Item
    ) -> None:
        edit = rooms("item-edit", home_id=alice_home.id, item_id=owner_item.id)
        html = signed_in.get(
            rooms("item-delete", home_id=alice_home.id, item_id=owner_item.id), {"next": edit}
        ).content.decode()
        assert f'<a class="button button-secondary" href="{edit}">' in html

    def test_a_contractor_deleted_with_its_own_page_as_next_lands_on_the_list(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        contractor = task_services.create_contractor(alice, alice_home, name="Roofer")
        detail = tasks("contractor-detail", home_id=alice_home.id, contractor_id=contractor.id)
        response = signed_in.post(
            tasks("contractor-delete", home_id=alice_home.id, contractor_id=contractor.id),
            {"next": detail},
        )
        assert response["Location"] == tasks("contractors", home_id=alice_home.id)


# ---------- More, and the landmarks of the task list ----------


class TestMoreAndLandmarks:
    def test_a_link_back_to_more_is_for_the_phone_only(self, signed_in: Client) -> None:
        html = signed_in.get(reverse("more-security")).content.decode()
        assert re.search(r'<a class="back-link" href="/more/" data-phone-only>', html)

    def test_a_page_of_more_opened_from_elsewhere_goes_back_there(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        today = tasks("today", home_id=alice_home.id)
        html = signed_in.get(reverse("notifications:index"), {"next": today}).content.decode()
        assert back_links(html) == [(today, "Today")]
        assert "data-phone-only" not in html.split('class="back-link"', 1)[1].split(">", 1)[0]

    def test_the_two_searches_of_the_done_filter_have_distinct_names(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        html = signed_in.get(
            tasks("list", home_id=alice_home.id), {"status": "done"}
        ).content.decode()
        # One search landmark, and the tabs that filter next to it, each with a name of its own.
        searches = re.findall(r'role="search" aria-label="([^"]+)"', html)
        tabs = re.findall(r'<nav (?:id="[^"]*" )?class="tabs[^"]*" aria-label="([^"]+)"', html)
        assert len(searches) == 1
        assert len(tabs) == 2
        assert len(set(searches + tabs)) == 3


# ---------- Repeat is open when the task comes from an item (NAV-15) ----------


def repeat_is_open(html: str) -> bool:
    section = re.search(r"<details([^>]*)>\s*<summary>Repeat</summary>", html)
    assert section
    return " open" in section.group(1)


class TestRepeatOpenFromAnItem:
    def test_the_section_is_open_when_the_task_is_added_from_an_item(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        form_url = tasks("create", home_id=alice_home.id) + f"?item={owner_item.id}"
        assert repeat_is_open(signed_in.get(form_url).content.decode())

    def test_the_section_stays_closed_for_a_task_added_from_the_list(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        html = signed_in.get(tasks("create", home_id=alice_home.id)).content.decode()
        assert not repeat_is_open(html)

    def test_the_section_stays_closed_when_a_once_task_is_edited(
        self, signed_in: Client, alice_home: Home, owner_item: Item
    ) -> None:
        # Editing is not creating: an item does not open the section by itself.
        task = Task.objects.create(
            home=alice_home, title="Check", item=owner_item, room=owner_item.room
        )
        html = signed_in.get(tasks("edit", home_id=alice_home.id, task_id=task.id)).content.decode()
        assert not repeat_is_open(html)

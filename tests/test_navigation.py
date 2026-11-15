"""The shell of the application (docs/adr/0017): its five sections, their tabs, the entry
marked as current, the roots without a back link, and More in four groups."""

import re
from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from housecairn.accounts.models import User
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.rooms.models import Item, Room

pytest_plugins = ("tests.rooms_fixtures",)

pytestmark = pytest.mark.django_db


def more_groups(html: str) -> list[str]:
    """The headings of the groups of the More page, in order."""
    main = html.split('<main id="main"', 1)[1]
    return re.findall(r'<h2 id="more-[a-z]+">([^<]*)</h2>', main)


def body_class(html: str) -> str:
    """The class attribute of the body of a page."""
    found = re.search(r'<body class="([^"]*)"', html)
    assert found
    return found.group(1)


def main_part(html: str) -> str:
    return html.split('<main id="main"', 1)[1]


class TestMore:
    def test_a_person_sees_home_me_and_help(self, signed_in: Client, alice_home: Home) -> None:
        html = signed_in.get(reverse("more")).content.decode()
        assert more_groups(html) == ["Home", "Me", "Help"]

    def test_an_administrator_also_sees_the_instance(
        self, client: Client, site_admin: User
    ) -> None:
        client.force_login(site_admin)
        html = client.get(reverse("more")).content.decode()
        assert more_groups(html) == ["Home", "Me", "Instance", "Help"]
        assert reverse("instance:overview") in main_part(html)

    def test_the_instance_is_for_administrators_only(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        client.force_login(make_user("staff@example.com", is_staff=True))
        html = client.get(reverse("more")).content.decode()
        assert "Instance" not in more_groups(html)
        assert reverse("instance:overview") not in html

    def test_the_home_group_is_about_the_only_home(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        main = main_part(signed_in.get(reverse("more")).content.decode())
        kwargs = {"home_id": alice_home.id}
        for name in (
            "homes:detail",
            "homes:household",
            "reports:list",
            "tasks:contractors",
            "labels:list",
        ):
            assert reverse(name, kwargs=kwargs) in main, name
        assert reverse("homes:list") in main

    def test_with_two_homes_the_home_group_lists_them(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        home_services.create_home(alice, name="Cottage")
        main = main_part(signed_in.get(reverse("more")).content.decode())
        assert reverse("homes:list") in main
        assert reverse("homes:household", kwargs={"home_id": alice_home.id}) not in main

    def test_me_and_help(self, signed_in: Client) -> None:
        main = main_part(signed_in.get(reverse("more")).content.decode())
        for name in (
            "profile",
            "notifications:index",
            "display-preferences",
            "calendar:feeds",
            "more-security",
            "api-docs",
            "about",
            "account_logout",
        ):
            assert reverse(name) in main, name

    def test_deleting_the_account_is_not_on_more(self, signed_in: Client) -> None:
        main = main_part(signed_in.get(reverse("more")).content.decode())
        assert reverse("account_delete") not in main

    def test_security_ends_with_deleting_the_account(self, signed_in: Client) -> None:
        main = main_part(signed_in.get(reverse("more-security")).content.decode())
        assert reverse("mfa_index") in main
        assert reverse("account_change_password") in main
        assert main.index(reverse("account_delete")) > main.index(
            reverse("account_change_password")
        )
        assert "button-danger" in main

    def test_in_french(self, signed_in: Client, alice_home: Home) -> None:
        html = signed_in.get(reverse("more"), headers={"Accept-Language": "fr"}).content.decode()
        assert more_groups(html) == ["Logement", "Moi", "Aide"]
        security = signed_in.get(
            reverse("more-security"), headers={"Accept-Language": "fr"}
        ).content.decode()
        assert "Sécurité et connexion" in security


def bar(html: str) -> list[tuple[str, str, bool]]:
    """(label, address, current) of the five entries of the bar, in order."""
    nav = re.search(r'<nav class="nav".*?</nav>', html, re.S)
    assert nav, "no navigation"
    return [
        (label.strip(), href, "aria-current" in attrs)
        for href, attrs, label in re.findall(
            r'<a class="nav-link" href="([^"]*)"([^>]*)>\s*<svg.*?</svg>([^<]*)</a>', nav[0], re.S
        )
    ]


def lit(html: str) -> list[str]:
    return [label for label, _, here in bar(html) if here]


def drawer(html: str) -> str:
    block = re.search(r'<div class="nav-drawer">.*?</div>\s*</nav>', html, re.S)
    assert block, "no column content"
    return block[0]


class TestBar:
    def test_five_sections_in_order(self, signed_in: Client, alice_home: Home) -> None:
        html = signed_in.get(reverse("rooms:list", kwargs={"home_id": alice_home.id})).text
        assert [label for label, _, _ in bar(html)] == [
            "Today",
            "Rooms",
            "Projects",
            "Budget",
            "More",
        ]

    def test_in_a_home_each_entry_opens_the_root_of_its_section(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        kwargs = {"home_id": alice_home.id}
        html = signed_in.get(reverse("projects:list", kwargs=kwargs)).text
        assert [href for _, href, _ in bar(html)] == [
            reverse("tasks:today", kwargs=kwargs),
            reverse("rooms:list", kwargs=kwargs),
            reverse("projects:list", kwargs=kwargs),
            reverse("budget:overview", kwargs=kwargs),
            reverse("more"),
        ]

    def test_outside_a_home_each_entry_opens_its_entry_page(self, signed_in: Client) -> None:
        html = signed_in.get(reverse("more")).text
        assert [href for _, href, _ in bar(html)] == [
            reverse("index"),
            reverse("rooms"),
            reverse("projects"),
            reverse("budget"),
            reverse("more"),
        ]

    def test_in_french(self, signed_in: Client, alice_home: Home) -> None:
        html = signed_in.get(
            reverse("rooms:list", kwargs={"home_id": alice_home.id}),
            headers={"Accept-Language": "fr"},
        ).text
        assert [label for label, _, _ in bar(html)] == [
            "Aujourd'hui",
            "Pièces",
            "Projets",
            "Budget",
            "Plus",
        ]

    def test_the_column_unfolds_more(self, signed_in: Client, alice_home: Home) -> None:
        kwargs = {"home_id": alice_home.id}
        column = drawer(signed_in.get(reverse("rooms:list", kwargs=kwargs)).text)
        for name in ("homes:household", "reports:list", "tasks:contractors", "labels:list"):
            assert reverse(name, kwargs=kwargs) in column, name
        for name in ("profile", "notifications:index", "more-security", "about", "account_logout"):
            assert reverse(name) in column, name
        assert reverse("instance:overview") not in column
        assert reverse("account_delete") not in column

    def test_the_column_shows_the_instance_to_an_administrator(
        self, client: Client, site_admin: User
    ) -> None:
        client.force_login(site_admin)
        assert reverse("instance:overview") in drawer(client.get(reverse("more")).text)

    def test_the_column_marks_the_page_of_more(self, signed_in: Client, alice_home: Home) -> None:
        column = drawer(signed_in.get(reverse("notifications:index")).text)
        marked = re.findall(r'<a class="nav-sublink" href="([^"]*)" aria-current="page"', column)
        assert marked == [reverse("notifications:index")]

    def test_no_navigation_signed_out(self, client: Client) -> None:
        assert 'class="nav"' not in client.get(reverse("account_login")).text


class TestCurrentEntry:
    @pytest.fixture
    def pages(
        self, alice: User, alice_home: Home, owner_room: Room, owner_item: Item
    ) -> list[tuple[str, str]]:
        from housecairn.projects import services as project_services
        from housecairn.stock import services as stock_services
        from housecairn.tasks import services as task_services

        h = {"home_id": alice_home.id}
        task = task_services.create_task(alice, alice_home, title="Filter")
        project = project_services.create_project(alice, alice_home, title="Bathroom")
        article = stock_services.create_article(alice, alice_home, name="Bulbs")
        return [
            (reverse("tasks:today", kwargs=h), "Today"),
            (reverse("tasks:list", kwargs=h), "Today"),
            (reverse("tasks:detail", kwargs={**h, "task_id": task.id}), "Today"),
            (reverse("tasks:create", kwargs=h), "Today"),
            (reverse("calendar:week"), "Today"),
            (reverse("rooms:list", kwargs=h), "Rooms"),
            (reverse("rooms:detail", kwargs={**h, "room_id": owner_room.id}), "Rooms"),
            (reverse("rooms:item-detail", kwargs={**h, "item_id": owner_item.id}), "Rooms"),
            (reverse("stock:list", kwargs=h), "Rooms"),
            (reverse("stock:detail", kwargs={**h, "article_id": article.id}), "Rooms"),
            (reverse("projects:list", kwargs=h), "Projects"),
            (reverse("projects:detail", kwargs={**h, "project_id": project.id}), "Projects"),
            (reverse("budget:overview", kwargs=h), "Budget"),
            (reverse("budget:rooms", kwargs=h), "Budget"),
            (reverse("budget:projects", kwargs=h), "Budget"),
            (reverse("budget:tasks", kwargs=h), "Budget"),
            (reverse("more"), "More"),
            (reverse("homes:detail", kwargs=h), "More"),
            (reverse("homes:household", kwargs=h), "More"),
            (reverse("reports:list", kwargs=h), "More"),
            (reverse("labels:list", kwargs=h), "More"),
            (reverse("tasks:contractors", kwargs=h), "More"),
            (reverse("display-preferences"), "More"),
            (reverse("calendar:feeds"), "More"),
            (reverse("notifications:index"), "More"),
            (reverse("more-security"), "More"),
        ]

    def test_each_page_marks_the_section_of_its_place_in_the_tree(
        self, signed_in: Client, pages: list[tuple[str, str]]
    ) -> None:
        for url, section in pages:
            response = signed_in.get(url)
            assert response.status_code == 200, url
            assert lit(response.text) == [section], url


class TestRoots:
    def test_a_root_of_a_section_has_no_back_link(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        h = {"home_id": alice_home.id}
        roots = [
            reverse("tasks:today", kwargs=h),
            reverse("tasks:list", kwargs=h),
            reverse("tasks:list", kwargs=h) + "?status=done",
            reverse("calendar:week"),
            reverse("rooms:list", kwargs=h),
            reverse("stock:list", kwargs=h),
            reverse("projects:list", kwargs=h),
            reverse("projects:list", kwargs=h) + "?view=ideas",
            reverse("projects:list", kwargs=h) + "?view=finished",
            reverse("budget:overview", kwargs=h),
            reverse("budget:rooms", kwargs=h),
            reverse("budget:projects", kwargs=h),
            reverse("budget:tasks", kwargs=h),
            reverse("more"),
        ]
        for url in roots:
            response = signed_in.get(url)
            assert response.status_code == 200, url
            assert 'class="back-link"' not in response.text, url
            # The brand stays in the bar of a root, on a phone too (design/patterns.md, Header).
            assert "subpage" not in body_class(response.text), url

    def test_a_page_below_a_root_keeps_its_back_link(
        self, signed_in: Client, alice: User, alice_home: Home, owner_room: Room
    ) -> None:
        h = {"home_id": alice_home.id}
        for url in (
            reverse("rooms:detail", kwargs={**h, "room_id": owner_room.id}),
            reverse("tasks:create", kwargs=h),
            reverse("more-security"),
            reverse("notifications:index"),
        ):
            assert 'class="back-link"' in signed_in.get(url).text, url

    def test_a_page_below_a_root_starts_with_its_title_on_a_phone(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        # `subpage` hides the brand on a phone (shell.css): the page starts with its back link.
        h = {"home_id": alice_home.id}
        for url in (
            reverse("rooms:detail", kwargs={**h, "room_id": owner_room.id}),
            reverse("tasks:create", kwargs=h),
            reverse("reports:create", kwargs=h),
            reverse("more-security"),
        ):
            assert "subpage" in body_class(signed_in.get(url).text), url

    def test_a_form_keeps_its_button_at_hand(self, signed_in: Client, alice_home: Home) -> None:
        html = signed_in.get(reverse("tasks:create", kwargs={"home_id": alice_home.id})).text
        assert 'class="form-submit"' in html

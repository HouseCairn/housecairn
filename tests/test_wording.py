"""One term per concept, in both languages (NAV-14, docs/developer/design-guide.md, "Labels").

The wording is read on the screens themselves, with the language of the request.
"""

import re
from collections.abc import Callable
from html import unescape as html_unescape

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext, pgettext

from housecairn.accounts.models import User
from housecairn.homes.models import Home

pytest_plugins = ("tests.rooms_fixtures", "tests.tasks_fixtures")

pytestmark = pytest.mark.django_db


def tab_labels(html: str) -> list[str]:
    nav = re.search(r'<nav class="tabs[^"]*"[^>]*>(.*?)</nav>', html, re.S)
    assert nav
    return [
        html_unescape(t) for t in re.findall(r'<a class="tab"[^>]*>(.*?)</a>', nav.group(1), re.S)
    ]


def fr(url: str, client: Client) -> str:
    return client.get(url, headers={"Accept-Language": "fr"}).content.decode()


class TestDoneFilter:
    def test_the_filter_of_finished_tasks_agrees_with_tasks(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        html = fr(reverse("tasks:list", kwargs={"home_id": alice_home.id}), signed_in)
        # The state of the list is a segmented tab, after the tabs of the section.
        assert re.findall(r'<a class="tab"[^>]*>(.*?)</a>', html, re.S)[-2:] == [
            "À faire",
            "Faites",
        ]

    def test_the_context_keeps_done_on_a_button_in_the_singular(self) -> None:
        with translation.override("fr"):
            assert pgettext("task", "Done") == "Fait"
            assert pgettext("task filter", "Done") == "Faites"


class TestHomeAndInstanceTabs:
    def test_the_home_tabs_do_not_share_a_name_with_the_budget_or_the_instance(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        html = signed_in.get(reverse("homes:detail", kwargs={"home_id": alice_home.id})).text
        assert tab_labels(html) == ["Home settings", "Household and roles", "Home audit log"]
        assert "Overview" not in tab_labels(html)

    def test_in_french_the_two_audit_logs_say_what_they_are_about(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        html = fr(reverse("homes:detail", kwargs={"home_id": alice_home.id}), signed_in)
        assert tab_labels(html) == [
            "Réglages du logement",
            "Foyer et rôles",
            "Journal",
        ]
        # The tab is short to fit in 320 px; the title of the page stays whole.
        page = fr(reverse("homes:audit", kwargs={"home_id": alice_home.id}), signed_in)
        assert "<h1>Journal d'audit du logement</h1>" in page
        with translation.override("fr"):
            assert gettext("Instance audit log") == "Journal d'audit de l'instance"

    def test_the_instance_has_four_tabs_and_the_files_are_reached_from_the_overview(
        self, make_user: Callable[..., User], client: Client
    ) -> None:
        client.force_login(make_user("admin@example.com", is_staff=True, is_superuser=True))
        overview = client.get(reverse("instance:overview")).text
        assert tab_labels(overview) == [
            "Overview",
            "Settings",
            "Sign-in providers",
            "Instance audit log",
        ]
        assert f'href="{reverse("instance:usage")}"' in overview
        assert "<h1>Instance audit log</h1>" in client.get(reverse("instance:audit")).text

    def test_the_home_audit_page_is_titled_like_its_tab(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        html = signed_in.get(reverse("homes:audit", kwargs={"home_id": alice_home.id})).text
        assert "<h1>Home audit log</h1>" in html


class TestFrenchTerms:
    def test_home_is_logement_everywhere_and_never_accueil(self) -> None:
        with translation.override("fr"):
            assert gettext("Home") == "Logement"

    def test_materials_of_a_task_are_not_the_stock(self) -> None:
        with translation.override("fr"):
            assert gettext("Materials") == "Fournitures"
            assert gettext("Stock") == "Matériel"

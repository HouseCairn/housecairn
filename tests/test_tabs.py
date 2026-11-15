"""The tabs of the areas of the application: one component, the current tab marked."""

import re
from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from housecairn.accounts.models import User
from housecairn.homes.models import Home

pytest_plugins = ("tests.rooms_fixtures",)

pytestmark = pytest.mark.django_db


def tabs_of(html: str) -> list[tuple[str, bool]]:
    """(label, current) of the links of the tab list, in order."""
    block = re.search(r'<nav class="tabs[^"]*".*?</nav>', html, re.S)
    assert block, "no tab bar"
    return [
        (label, "aria-current" in attrs)
        for attrs, label in re.findall(r'<a class="tab" href="[^"]*"([^>]*)>([^<]*)</a>', block[0])
    ]


def current(html: str) -> list[str]:
    return [label for label, here in tabs_of(html) if here]


class TestSectionTabs:
    """Four tabs at most per section, sister views of one content (docs/adr/0017)."""

    def test_today(self, signed_in: Client, alice_home: Home) -> None:
        for view, label in [("tasks:today", "Today"), ("tasks:list", "All")]:
            html = signed_in.get(reverse(view, kwargs={"home_id": alice_home.id})).text
            assert [t for t, _ in tabs_of(html)] == ["Today", "All", "Calendar"]
            assert current(html) == [label]
        done = signed_in.get(
            reverse("tasks:list", kwargs={"home_id": alice_home.id}), {"status": "done"}
        ).text
        assert current(done) == ["All"]

    def test_the_calendar_is_a_tab_of_today(self, signed_in: Client, alice_home: Home) -> None:
        html = signed_in.get(reverse("calendar:week")).text
        assert [t for t, _ in tabs_of(html)] == ["Today", "All", "Calendar"]
        assert current(html) == ["Calendar"]
        assert f"{reverse('calendar:week')}?home={alice_home.id}" in html

    def test_a_calendar_of_all_homes_leads_back_to_all_homes(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        from housecairn.homes import services as home_services

        home_services.create_home(alice, name="Cottage")
        # Before a home is chosen, the calendar shows all homes, with its way back.
        every = signed_in.get(reverse("calendar:week")).text
        assert [t for t, _ in tabs_of(every)] == ["Today", "Calendar"]
        assert current(every) == ["Calendar"]
        one = signed_in.get(reverse("calendar:week"), {"home": str(alice_home.id)}).text
        assert current(one) == ["Calendar"]

    def test_rooms(self, signed_in: Client, alice_home: Home) -> None:
        for view, label in [
            ("rooms:list", "Rooms"),
            ("rooms:items", "Equipment"),
            ("stock:list", "Stock"),
        ]:
            html = signed_in.get(reverse(view, kwargs={"home_id": alice_home.id})).text
            assert [t for t, _ in tabs_of(html)] == ["Rooms", "Equipment", "Stock"]
            assert current(html) == [label]

    def test_projects(self, signed_in: Client, alice_home: Home) -> None:
        base = reverse("projects:list", kwargs={"home_id": alice_home.id})
        for query, label in [
            ("", "In progress"),
            ("?view=ideas", "Ideas"),
            ("?view=finished", "Finished"),
        ]:
            html = signed_in.get(base + query).text
            assert [t for t, _ in tabs_of(html)] == ["In progress", "Ideas", "Finished"]
            assert current(html) == [label]

    def test_budget(self, signed_in: Client, alice_home: Home) -> None:
        for view, label in [
            ("budget:overview", "Summary"),
            ("budget:rooms", "Rooms"),
            ("budget:projects", "Projects"),
            ("budget:tasks", "Upkeep"),
        ]:
            html = signed_in.get(reverse(view, kwargs={"home_id": alice_home.id})).text
            assert html.count('<nav class="tabs') == 1, view
            assert [t for t, _ in tabs_of(html)] == ["Summary", "Rooms", "Projects", "Upkeep"]
            assert current(html) == [label]

    def test_section_tabs_share_the_width(self, signed_in: Client, alice_home: Home) -> None:
        html = signed_in.get(reverse("budget:overview", kwargs={"home_id": alice_home.id})).text
        assert '<nav class="tabs tabs-fit" aria-label="Budget">' in html

    def test_in_french(self, signed_in: Client, alice_home: Home) -> None:
        kwargs = {"home_id": alice_home.id}
        fr = {"Accept-Language": "fr"}
        assert [
            t
            for t, _ in tabs_of(
                signed_in.get(reverse("tasks:today", kwargs=kwargs), headers=fr).text
            )
        ] == [
            "Aujourd&#x27;hui",
            "Toutes",
            "Calendrier",
        ]
        assert [
            t
            for t, _ in tabs_of(
                signed_in.get(reverse("rooms:list", kwargs=kwargs), headers=fr).text
            )
        ] == [
            "Pièces",
            "Équipements",
            "Matériel",
        ]
        projects = signed_in.get(reverse("projects:list", kwargs=kwargs), headers=fr).text
        assert [t for t, _ in tabs_of(projects)] == ["En cours", "Idées", "Terminés"]
        budget = signed_in.get(reverse("budget:overview", kwargs=kwargs), headers=fr).text
        assert [t for t, _ in tabs_of(budget)] == ["Synthèse", "Pièces", "Projets", "Entretien"]

    def test_contractors_and_preferences_have_no_section_tabs(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        contractors = reverse("tasks:contractors", kwargs={"home_id": alice_home.id})
        for url in (contractors, reverse("display-preferences")):
            assert 'class="tabs' not in signed_in.get(url).text, url

    def test_a_tab_is_a_real_link_for_the_keyboard(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        html = signed_in.get(reverse("tasks:today", kwargs={"home_id": alice_home.id})).text
        assert '<nav class="tabs tabs-fit" aria-label="Today">' in html


class TestHomeTabs:
    def test_an_owner_sees_the_overview_household_and_audit(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        for view, label in [
            ("homes:detail", "Home settings"),
            ("homes:household", "Household and roles"),
            ("homes:audit", "Home audit log"),
        ]:
            html = signed_in.get(reverse(view, kwargs={"home_id": alice_home.id})).content.decode()
            assert [t for t, _ in tabs_of(html)] == [
                "Home settings",
                "Household and roles",
                "Home audit log",
            ]
            assert current(html) == [label]

    def test_a_member_has_no_audit_tab(
        self, client: Client, alice_home: Home, member: User
    ) -> None:
        client.force_login(member)
        html = client.get(
            reverse("homes:household", kwargs={"home_id": alice_home.id})
        ).content.decode()
        assert [t for t, _ in tabs_of(html)] == ["Home settings", "Household and roles"]

    def test_a_member_page_keeps_the_household_tab_current(
        self, signed_in: Client, alice_home: Home, member: User
    ) -> None:
        membership = alice_home.memberships.get(user=member)
        html = signed_in.get(
            reverse("homes:member", kwargs={"home_id": alice_home.id, "member_id": membership.id})
        ).content.decode()
        assert current(html) == ["Household and roles"]


class TestInstanceTabs:
    @pytest.fixture
    def admin(self, make_user: Callable[..., User], client: Client) -> Client:
        client.force_login(make_user("admin@example.com", is_staff=True, is_superuser=True))
        return client

    def test_each_page_of_the_administration_marks_its_tab(self, admin: Client) -> None:
        for view, label in [
            ("instance:overview", "Overview"),
            ("instance:settings", "Settings"),
            ("instance:providers", "Sign-in providers"),
            # The files of the homes are a page of the overview: four tabs, not five.
            ("instance:usage", "Overview"),
            ("instance:audit", "Instance audit log"),
        ]:
            html = admin.get(reverse(view)).content.decode()
            assert [t for t, _ in tabs_of(html)] == [
                "Overview",
                "Settings",
                "Sign-in providers",
                "Instance audit log",
            ]
            assert current(html) == [label]

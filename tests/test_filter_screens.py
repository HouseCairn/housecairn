"""Filters, search and rows of the lists: segmented tabs, search as you type, one target a row.

The anatomy is `design/patterns.md` ("Filters and search"): a filter of state is a segmented tab,
never a pill or a list with a Filter button; several criteria go in a sheet "Filters (n)"; a
search field has no button and follows what is typed; a row has one target.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import translation

from housecairn.accounts.models import User
from housecairn.core.templatetags.tabs import filter_tabs
from housecairn.homes.models import Home
from housecairn.projects import services as project_services
from housecairn.projects.models import Project
from housecairn.reports import services as report_services
from housecairn.rooms.models import Room
from housecairn.stock import services as stock_services
from housecairn.tasks import services as task_services

pytest_plugins = ("tests.rooms_fixtures", "tests.projects_fixtures", "tests.stock_fixtures")
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root")]

STATIC = Path(__file__).resolve().parents[1] / "src" / "housecairn" / "static" / "css"


def page(client: Client, name: str, home: Home, **query: str) -> str:
    return client.get(reverse(name, kwargs={"home_id": home.id}), query).content.decode()


def tabs_of(html: str) -> list[tuple[str, bool]]:
    """Every tab of the page: (label, is the current one)."""
    return [
        (re.sub(r"\s+", " ", label).strip(), "aria-current" in attrs)
        for attrs, label in re.findall(r'<a class="tab"([^>]*)>(.*?)</a>', html, re.S)
    ]


def results(html: str) -> str:
    """What a search in place swaps: the region `#results`."""
    start = html.index('<div id="results">')
    return html[start:]


def targets_of_rows(region: str) -> list[int]:
    """The links, buttons and fields inside each `<li>` of the lists of a region."""
    return [
        len(re.findall(r"<(?:a|button|input|select)\b", row))
        for row in re.findall(r"<li\b.*?</li>", region, re.S)
    ]


@pytest.fixture
def two_projects(alice: User, alice_home: Home, owner_room: Room) -> tuple[Project, Project]:
    shed = project_services.create_project(
        alice, alice_home, title="Garden shed", status="in_progress", rooms=[owner_room]
    )
    roof = project_services.create_project(
        alice, alice_home, title="Roof", status="planned", priority="high"
    )
    return shed, roof


class TestTabs:
    def test_a_filter_of_state_is_a_segmented_tab_with_its_current_one_marked(
        self, rf: RequestFactory
    ) -> None:
        request = rf.get("/h/x/stock/", {"q": "filter", "page": "3", "low": "1"})
        shown = filter_tabs(
            {"request": request},
            "Filter",
            "low",
            "1",
            [("", "All"), ("1", "Low stock")],
            "",
            "q",
        )
        assert [(t.label, t.url, t.current) for t in shown["tabs"]] == [
            ("All", "/h/x/stock/?q=filter", False),
            ("Low stock", "/h/x/stock/?q=filter&low=1", True),
        ]

    def test_the_reports_filter_is_tabs_not_pills(
        self, signed_in: Client, alice_home: Home, reader: User
    ) -> None:
        report_services.create_report(reader, alice_home, title="Leak")
        html = page(signed_in, "reports:list", alice_home)
        assert tabs_of(html) == [
            ("Open", True),
            ("Closed", False),
            ("All", False),
            ("Mine", False),
        ]
        assert "pill-ghost" not in html
        mine = page(signed_in, "reports:list", alice_home, show="mine")
        assert [label for label, current in tabs_of(mine) if current] == ["Mine"]

    def test_the_state_of_the_task_list_is_tabs(self, signed_in: Client, alice_home: Home) -> None:
        html = page(signed_in, "tasks:list", alice_home)
        assert tabs_of(html)[-2:] == [("To do", True), ("Done", False)]
        assert 'id="filter-status"' not in html
        done = page(signed_in, "tasks:list", alice_home, status="done")
        assert tabs_of(done)[-2:] == [("To do", False), ("Done", True)]

    def test_the_stock_filter_is_tabs_and_keeps_the_words_searched(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        stock_services.create_article(alice, alice_home, name="Filter")
        html = page(signed_in, "stock:list", alice_home, q="fil")
        assert tabs_of(html)[-2:] == [("All", True), ("Low stock", False)]
        assert (
            f'href="{reverse("stock:list", kwargs={"home_id": alice_home.id})}?q=fil&amp;low=1"'
            in html
        )
        assert 'name="low" value="1"' not in html
        assert "Low stock only" not in html


class TestSearchAsYouType:
    def test_the_projects_search_has_a_magnifier_no_button_and_a_pause(
        self, signed_in: Client, alice_home: Home, two_projects: tuple[Project, Project]
    ) -> None:
        html = page(signed_in, "projects:list", alice_home)
        assert 'class="search-field"' in html
        assert 'class="icon search-icon"' in html
        assert 'type="search"' in html
        # 300 ms after the last key, and only when the words changed: one request for a word.
        assert 'hx-trigger="input changed delay:300ms"' in html
        assert 'hx-sync="closest form:replace"' in html
        # The answer replaces the list only, and the address follows (Back, bookmarks).
        assert 'hx-select="#results"' in html
        assert 'hx-push-url="true"' in html
        # No Search or Filter button for a person with script; the one for no script is hidden.
        assert ">Search</button>" not in html
        assert 'class="button-secondary button-small filters-apply"' in html

    def test_the_no_script_button_is_hidden_by_the_page_and_shown_without_script(self) -> None:
        app = (STATIC / "components.css").read_text()
        assert re.search(r"button\.filters-apply\s*{\s*display:\s*none;", app)
        assert re.search(
            r"button\.filters-apply\s*{\s*display:\s*inline-flex;",
            (STATIC / "no-script.css").read_text(),
        )

    def test_typing_is_answered_with_a_full_page_whose_results_are_the_list(
        self, signed_in: Client, alice_home: Home, two_projects: tuple[Project, Project]
    ) -> None:
        # The server filters; with script `hx-select` keeps `#results`, else the page is whole.
        html = page(signed_in, "projects:list", alice_home, q="shed")
        assert "Garden shed" in results(html)
        assert "Roof" not in results(html)
        assert "No project matches." in results(
            page(signed_in, "projects:list", alice_home, q="zz")
        )

    def test_the_stock_and_item_and_room_searches_have_no_button_either(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        for name in ("stock:list", "rooms:items", "rooms:list", "rooms:search"):
            html = page(signed_in, name, alice_home)
            assert 'class="search-field"' in html, name
            assert 'hx-trigger="input changed delay:300ms"' in html, name
            # A button to apply the sheet exists for no script, and is hidden by the page.
            visible_search = re.search(r"<button(?![^>]*filters-apply)[^>]*>\s*Search\s*<", html)
            assert visible_search is None, name

    def test_the_rooms_list_answers_a_search_in_place_with_rooms_and_items(
        self, signed_in: Client, alice: User, alice_home: Home, owner_room: Room
    ) -> None:
        html = page(signed_in, "rooms:list", alice_home, q=owner_room.name[:3])
        assert owner_room.name in results(html)
        assert 'id="results-status"' in html
        assert "1 result" in html
        nothing = page(signed_in, "rooms:list", alice_home, q="no such thing")
        assert "Nothing matches" in results(nothing)

    def test_the_result_count_is_said_to_screen_readers(
        self, signed_in: Client, alice_home: Home, two_projects: tuple[Project, Project]
    ) -> None:
        html = page(signed_in, "projects:list", alice_home)
        assert re.search(r'<div id="results-status"[^>]*role="status">2 results</div>', html)
        assert "1 result<" in page(signed_in, "projects:list", alice_home, q="shed")


class TestSheetOfFilters:
    def test_the_projects_criteria_are_in_a_sheet_with_their_count(
        self, signed_in: Client, alice_home: Home, two_projects: tuple[Project, Project]
    ) -> None:
        html = page(signed_in, "projects:list", alice_home)
        assert '<details class="filters">' in html
        assert re.search(r'id="filters-count"[^>]*></span>', html)
        for field in ("filter-status", "filter-room", "filter-priority"):
            assert f'id="{field}"' in html
        # A select follows the choice at once, with no button.
        assert html.count('hx-trigger="change"') == 3
        one = page(signed_in, "projects:list", alice_home, priority="high")
        assert re.search(r'id="filters-count"[^>]*> \(1\)</span>', one)
        two = page(signed_in, "projects:list", alice_home, priority="high", status="planned")
        assert re.search(r'id="filters-count"[^>]*> \(2\)</span>', two)

    def test_the_history_and_the_equipment_use_the_same_sheet(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        done = page(signed_in, "tasks:list", alice_home, status="done", room=str(owner_room.id))
        assert '<details class="filters">' in done
        assert re.search(r'id="filters-count"[^>]*> \(1\)</span>', done)
        assert "history-filter" not in done
        items = page(signed_in, "rooms:items", alice_home, room=str(owner_room.id))
        assert re.search(r'id="filters-count"[^>]*> \(1\)</span>', items)

    def test_the_open_tasks_have_one_filter_in_the_sheet(
        self, signed_in: Client, alice_home: Home, owner_room: Room
    ) -> None:
        html = page(signed_in, "tasks:list", alice_home, room=str(owner_room.id))
        assert re.search(r'id="filters-count"[^>]*> \(1\)</span>', html)
        assert 'class="filter-row"' not in html


class TestRowsHaveOneTarget:
    def test_a_project_row_is_one_link_and_the_compare_mode_one_label(
        self, signed_in: Client, alice_home: Home, two_projects: tuple[Project, Project]
    ) -> None:
        plain = results(page(signed_in, "projects:list", alice_home))
        assert targets_of_rows(plain) == [1, 1]
        assert 'type="checkbox"' not in plain
        compare = results(page(signed_in, "projects:list", alice_home, compare="1"))
        assert targets_of_rows(compare) == [1, 1]
        assert len(re.findall(r'<label class="row" for="compare-', compare)) == 2
        assert "Compare the ticked projects" in compare
        assert "Compare the ticked projects" not in plain
        assert "Compare projects" in plain

    def test_the_compare_mode_needs_two_projects_and_keeps_the_filters(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        project_services.create_project(alice, alice_home, title="Only one", status="in_progress")
        alone = results(page(signed_in, "projects:list", alice_home, compare="1"))
        assert "<label" not in alone
        assert "Compare projects" not in alone

    def test_a_task_of_a_project_is_one_link_and_can_be_taken_out_from_its_page(
        self, signed_in: Client, alice: User, alice_home: Home, living_room_project: Project
    ) -> None:
        task = task_services.create_task(alice, alice_home, title="Buy brushes")
        project_services.attach_task(alice, living_room_project, task)
        detail = signed_in.get(
            reverse(
                "projects:detail",
                kwargs={"home_id": alice_home.id, "project_id": living_room_project.id},
            )
        ).content.decode()
        tasks = detail.split("<h2>Tasks</h2>")[1].split("<details")[0]
        assert targets_of_rows(tasks) == [1]
        assert "Take out" not in detail
        task_page = reverse("tasks:detail", kwargs={"home_id": alice_home.id, "task_id": task.id})
        on_task = signed_in.get(task_page).content.decode()
        assert "Take out" in on_task
        taken = signed_in.post(
            reverse(
                "projects:task-detach",
                kwargs={
                    "home_id": alice_home.id,
                    "project_id": living_room_project.id,
                    "task_id": task.id,
                },
            ),
            {"next": task_page},
        )
        # Taken out from its own page, one stays on that page (the return rule is unchanged).
        assert taken["Location"] == task_page

    def test_a_budget_line_is_one_target_that_opens_its_form(
        self, signed_in: Client, alice_home: Home, living_room_project: Project
    ) -> None:
        detail = signed_in.get(
            reverse(
                "projects:detail",
                kwargs={"home_id": alice_home.id, "project_id": living_room_project.id},
            )
        ).content.decode()
        rows = detail.split("<tbody>")[1].split("</tbody>")[0]
        for row in re.findall(r"<tr>.*?</tr>", rows, re.S):
            assert row.count("<a ") == 1
            assert 'class="row-link"' in row
        assert '<span class="visually-hidden"> Paint</span>' not in detail

    def test_a_read_only_member_gets_no_link_on_a_budget_line(
        self, client: Client, reader: User, alice_home: Home, living_room_project: Project
    ) -> None:
        client.force_login(reader)
        detail = client.get(
            reverse(
                "projects:detail",
                kwargs={"home_id": alice_home.id, "project_id": living_room_project.id},
            )
        ).content.decode()
        assert 'class="row-link"' not in detail

    def test_the_budget_tables_have_one_link_a_row_and_no_underlined_title(
        self, signed_in: Client, alice_home: Home, living_room_project: Project
    ) -> None:
        projects = page(signed_in, "budget:projects", alice_home)
        assert re.search(
            r'<th scope="row"><a class="row-link" href="[^"]+">Paint the living room</a>', projects
        )
        css = (STATIC / "components.css").read_text()
        assert re.search(r"\.row-link\s*{[^}]*text-decoration:\s*none", css)
        assert ".row-link::after" in css

    def test_the_two_task_forms_of_a_project_are_two_buttons(
        self, signed_in: Client, alice: User, alice_home: Home, living_room_project: Project
    ) -> None:
        task_services.create_task(alice, alice_home, title="Loose task")
        detail = signed_in.get(
            reverse(
                "projects:detail",
                kwargs={"home_id": alice_home.id, "project_id": living_room_project.id},
            )
        ).content.decode()
        buttons = re.findall(
            r'<details class="button-details"[^>]*>\s*'
            r'<summary class="button button-secondary button-block">(.*?)</summary>',
            detail,
            re.S,
        )
        assert buttons == ["Add a task to the project", "Put an existing task in the project"]
        # No form is open until it is asked for, except one that came back with errors.
        assert '<details class="button-details" open>' not in detail
        refused = signed_in.post(
            reverse(
                "projects:task-add",
                kwargs={"home_id": alice_home.id, "project_id": living_room_project.id},
            ),
            {"title": ""},
        ).content.decode()
        assert '<details class="button-details" open>' in refused


class TestBudgetYear:
    def test_the_year_follows_its_choice_with_no_show_button_for_a_person_with_script(
        self, signed_in: Client, alice_home: Home, alice: User
    ) -> None:
        project_services.set_envelope(alice, alice_home, year=2025, amount=Decimal(1000))
        html = page(signed_in, "budget:overview", alice_home)
        select = re.search(r'<select id="budget-year"[^>]*>', html)
        assert select
        assert 'hx-trigger="change"' in select.group(0)
        assert re.search(r'<button type="submit" class="[^"]*filters-apply">Show</button>', html)
        # The file follows the year: it is inside what the choice swaps.
        assert "export=csv" in results(html)

    def test_the_overview_no_longer_repeats_the_tabs_as_links(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        html = page(signed_in, "budget:overview", alice_home)
        assert "By task of the upkeep" not in html
        assert ">By room<" not in html
        assert ">By project<" not in html


class TestFrench:
    def test_the_new_words_are_translated(
        self, signed_in: Client, alice_home: Home, two_projects: tuple[Project, Project]
    ) -> None:
        with translation.override("fr"):
            html = signed_in.get(
                reverse("projects:list", kwargs={"home_id": alice_home.id}),
                headers={"accept-language": "fr"},
            ).content.decode()
        assert "Filtres" in html
        assert "2 résultats" in html
        assert "Comparer les projets" in html
        stock = signed_in.get(
            reverse("stock:list", kwargs={"home_id": alice_home.id}),
            headers={"accept-language": "fr"},
        ).content.decode()
        assert tabs_of(stock)[-2:] == [("Tous", True), ("Stock bas", False)]

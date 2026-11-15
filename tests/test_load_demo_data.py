"""`manage.py load_demo_data`: a fictional home for screenshots and demonstrations."""

import datetime
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from housecairn.accounts.models import User
from housecairn.budget import services as budget
from housecairn.homes.management.commands.load_demo_data import load
from housecairn.homes.models import Home, Membership, Role
from housecairn.projects import services as project_services
from housecairn.projects.models import BudgetLine, PreviewState, Project, ProjectLink, Quote
from housecairn.rooms.models import Room
from housecairn.stock import services as stock
from housecairn.stock.models import Article
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Occurrence, Task

pytestmark = pytest.mark.django_db


def _names(home: Home) -> tuple[list[str], list[str], list[str]]:
    rooms = list(Room.objects.filter(home=home).order_by("position").values_list("name", flat=True))
    tasks = sorted(Task.objects.filter(home=home).values_list("title", flat=True))
    projects = sorted(Project.objects.filter(home=home).values_list("title", flat=True))
    return rooms, tasks, projects


def test_the_english_home_has_rooms_tasks_and_projects(alice: User) -> None:
    out = StringIO()
    call_command("load_demo_data", "--owner", "ALICE@example.com", stdout=out)
    home = Home.objects.get(name="Example house")
    assert out.getvalue().startswith("Example house (")
    rooms, tasks, projects = _names(home)
    assert rooms == ["Living room", "Kitchen", "Bathroom", "Hallway", "Utility room", "Garden"]
    assert "Test the smoke detector" in tasks
    assert len(tasks) == 8
    assert projects == ["Redo the bathroom", "Repaint the living room"]
    assert Membership.objects.get(home=home, user=alice).role == Role.OWNER
    planned = sorted(BudgetLine.objects.filter(home=home).values_list("planned", flat=True))
    assert [str(p) for p in planned] == ["240.00", "650.00", "1200.00", "3000.00"]


def test_the_french_home_has_french_names(alice: User) -> None:
    call_command(
        "load_demo_data", "--owner", "alice@example.com", "--language", "fr", stdout=StringIO()
    )
    home = Home.objects.get(name="Maison d'exemple")
    rooms, tasks, projects = _names(home)
    assert rooms == ["Salon", "Cuisine", "Salle de bain", "Entrée", "Buanderie", "Jardin"]
    assert "Tester le détecteur de fumée" in tasks
    assert projects == ["Refaire la salle de bain", "Repeindre le salon"]
    # No English name leaks into the French home.
    english = {"Living room", "Kitchen", "Test the smoke detector", "Repaint the living room"}
    assert not english & {*rooms, *tasks, *projects}


def test_dates_are_relative_to_today(alice: User) -> None:
    today = datetime.date(2030, 3, 15)
    home = load(alice, "en", today)
    due = Occurrence.objects.filter(task__home=home).values_list("task__title", "due_on")
    by_title = dict(due)
    assert by_title["Test the smoke detector"] == today - datetime.timedelta(days=3)
    assert by_title["Descaling"] == today
    assert by_title["Bleed the radiators"] == today + datetime.timedelta(days=20)
    assert by_title["Choose the tiles"] == today + datetime.timedelta(days=10)


def test_the_descaling_draws_a_filter_and_costs_31_60_a_year(alice: User) -> None:
    """Scenario A of the examples page: equipment, stock item, cost a year, low stock."""
    today = task_services.today()
    home = load(alice, "fr", today)
    task = Task.objects.get(home=home, title="Détartrage")
    assert task.item is not None
    assert task.item.name == "Machine expresso"
    assert task.room is not None
    assert task.room.name == "Cuisine"
    assert (task.repeat_unit, task.repeat_interval) == ("month", 3)
    row = budget.task_row(task, today)
    assert row is not None
    assert row.annual_cost == Decimal("31.60")
    article = Article.objects.get(home=home, name="Filtre à eau")
    assert article.unit_price == Decimal("7.90")
    assert stock.quantity_of(article) == Decimal("2")
    assert not stock.is_low(article, Decimal("2"))
    occurrence = Occurrence.objects.get(task=task, status="planned")
    task_services.complete(alice, occurrence)
    # One filter taken by Done: the stock is at its threshold, so it is flagged as low.
    left = stock.quantity_of(article)
    assert left == Decimal("1")
    assert stock.is_low(article, left)


def test_the_bathroom_project_compares_two_quotes(alice: User) -> None:
    """Scenario B: tasks, two quotes, one accepted on a line, links and the envelope."""
    today = datetime.date(2030, 3, 15)
    home = load(alice, "en", today)
    project = Project.objects.get(home=home, title="Redo the bathroom")
    assert [link.task.title for link in project_services.tasks_of(project)] == [
        "Choose the tiles",
        "Remove the old bathtub",
    ]
    quotes = {q.contractor.name: q for q in Quote.objects.filter(project=project)}
    assert set(quotes) == {"Linden Plumbing", "Bath Services"}
    accepted = quotes["Linden Plumbing"]
    assert accepted.status == "accepted"
    assert accepted.budget_line is not None
    assert accepted.budget_line.label == "Plumber (shower)"
    assert quotes["Bath Services"].status == "received"
    amounts = project_services.amounts_of(project)
    assert (amounts.planned, amounts.committed, amounts.paid) == (
        Decimal("4200.00"),
        Decimal("2850.00"),
        Decimal("480.00"),
    )
    links = ProjectLink.objects.filter(project=project)
    assert sorted(link.url for link in links) == [
        "https://example.com/terrazzo-tiles",
        "https://example.com/walk-in-shower",
    ]
    # Saved without reading the pages: loading the demo calls no network.
    assert {link.preview_state for link in links} == {PreviewState.NONE}
    envelope = project_services.envelopes_of(home).get(year=2030)
    assert envelope.amount == Decimal("6000.00")


def test_an_unknown_account_is_refused(db: None) -> None:
    with pytest.raises(CommandError, match="No account"):
        call_command("load_demo_data", "--owner", "nobody@example.com")
    assert not Home.objects.exists()


def test_a_second_run_in_the_same_language_is_refused(alice: User) -> None:
    call_command("load_demo_data", "--owner", "alice@example.com", stdout=StringIO())
    with pytest.raises(CommandError, match="already has"):
        call_command("load_demo_data", "--owner", "alice@example.com", stdout=StringIO())
    assert Home.objects.filter(name="Example house").count() == 1
    call_command(
        "load_demo_data", "--owner", "alice@example.com", "--language", "fr", stdout=StringIO()
    )
    assert Home.objects.count() == 2


def test_an_unknown_language_is_refused(alice: User) -> None:
    with pytest.raises(CommandError):
        call_command("load_demo_data", "--owner", "alice@example.com", "--language", "de")

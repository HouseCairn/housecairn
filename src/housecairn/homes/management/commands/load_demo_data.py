"""Fill a local instance with a fictional home, in English or in French.

For screenshots, demonstrations and manual tests: `manage.py load_demo_data --owner
<e-mail of an existing account> --language fr`. Every name is generic and invented;
dates are relative to today, so the Today screen always has something overdue, due
and coming. The names are data, not interface text: they are written here in both
languages instead of going through the translation catalogs.
"""

import datetime
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from housecairn.accounts.models import User
from housecairn.homes import services as homes
from housecairn.homes.models import Home
from housecairn.projects import links
from housecairn.projects import services as projects
from housecairn.rooms import services as rooms
from housecairn.rooms.models import Room
from housecairn.stock import services as stock
from housecairn.tasks import materials
from housecairn.tasks import services as tasks

# Room keys, with their kind and their name in each language.
ROOMS: dict[str, tuple[str, dict[str, str]]] = {
    "living": ("living", {"en": "Living room", "fr": "Salon"}),
    "kitchen": ("kitchen", {"en": "Kitchen", "fr": "Cuisine"}),
    "bathroom": ("bathroom", {"en": "Bathroom", "fr": "Salle de bain"}),
    "hallway": ("hallway", {"en": "Hallway", "fr": "Entrée"}),
    "utility": ("utility", {"en": "Utility room", "fr": "Buanderie"}),
    "garden": ("outdoor", {"en": "Garden", "fr": "Jardin"}),
}

# Title in each language, room key, days from today, other fields.
TASKS: list[tuple[dict[str, str], str, int, dict[str, Any]]] = [
    (
        {"en": "Test the smoke detector", "fr": "Tester le détecteur de fumée"},
        "hallway",
        -3,
        {
            "repeat_mode": "fixed",
            "repeat_unit": "month",
            "repeat_interval": 6,
            "priority": "high",
            "estimated_minutes": 5,
        },
    ),
    (
        {"en": "Clean the ventilation filter", "fr": "Nettoyer le filtre de la VMC"},
        "bathroom",
        0,
        {
            "repeat_mode": "after_last",
            "repeat_unit": "month",
            "repeat_interval": 3,
            "estimated_minutes": 15,
        },
    ),
    (
        {"en": "Boiler yearly service", "fr": "Entretien annuel de la chaudière"},
        "utility",
        6,
        {"repeat_mode": "fixed", "repeat_unit": "year", "repeat_interval": 1, "priority": "high"},
    ),
    (
        {"en": "Clear the gutters", "fr": "Nettoyer les gouttières"},
        "garden",
        12,
        {
            "repeat_mode": "fixed",
            "repeat_unit": "year",
            "repeat_interval": 1,
            "estimated_minutes": 60,
        },
    ),
    (
        {"en": "Bleed the radiators", "fr": "Purger les radiateurs"},
        "living",
        20,
        {
            "repeat_mode": "fixed",
            "repeat_unit": "year",
            "repeat_interval": 1,
            "estimated_minutes": 30,
        },
    ),
]

HOME_NAME = {"en": "Example house", "fr": "Maison d'exemple"}
PAINT_TITLE = {"en": "Repaint the living room", "fr": "Repeindre le salon"}
PAINT_GOAL = {"en": "Walls and ceiling, before winter.", "fr": "Murs et plafond, avant l'hiver."}
# Label in each language, nature, planned amount.
PAINT_LINES: list[tuple[dict[str, str], str, str]] = [
    ({"en": "Paint and primer", "fr": "Peinture et sous-couche"}, "material", "240"),
    ({"en": "Painter (ceiling)", "fr": "Peintre (plafond)"}, "labour", "650"),
]

# Scenario A of the examples page (docs/guides/examples.md): the descaling of the espresso
# machine takes one filter from the stock each time it is done. Four times a year at 7.90,
# the budget plans 31.60 for it.
MACHINE = {"en": "Espresso machine", "fr": "Machine expresso"}
DESCALING = {"en": "Descaling", "fr": "Détartrage"}
DESCALING_STEPS = {
    "en": "Fit a new filter, run the descaling programme, rinse twice.",
    "fr": "Poser un filtre neuf, lancer le programme de détartrage, rincer deux fois.",
}
FILTER = {"en": "Water filter", "fr": "Filtre à eau"}
FILTER_PLACE = {"en": "Kitchen, top cupboard", "fr": "Cuisine, placard du haut"}
FILTER_PRICE = "7.90"
# One Done brings the stock to its threshold, so it shows as low before it runs out.
FILTERS_IN_STOCK = "2"

# Scenario B: the bathroom project, with its tasks, two quotes compared (one accepted on a
# budget line), inspiration links and the envelope of the year.
BATHROOM = {"en": "Redo the bathroom", "fr": "Refaire la salle de bain"}
BATHROOM_GOAL = {
    "en": "A walk-in shower instead of the bathtub.",
    "fr": "Une douche à l'italienne à la place de la baignoire.",
}
# Title in each language, days from today.
BATHROOM_TASKS: list[tuple[dict[str, str], int]] = [
    ({"en": "Choose the tiles", "fr": "Choisir le carrelage"}, 10),
    ({"en": "Remove the old bathtub", "fr": "Déposer l'ancienne baignoire"}, 30),
]
# Label in each language, nature, planned amount, paid amount.
BATHROOM_LINES: list[tuple[dict[str, str], str, str, str]] = [
    ({"en": "Tiles", "fr": "Carrelage et faïence"}, "material", "1200", "480"),
    ({"en": "Plumber (shower)", "fr": "Plombier (douche)"}, "labour", "3000", "0"),
]
# Contractor in each language, amount, status, days since the quote was issued. The
# accepted one is committed on the labour line.
QUOTES: list[tuple[dict[str, str], str, str, int]] = [
    ({"en": "Linden Plumbing", "fr": "Plomberie des Tilleuls"}, "2850", "accepted", 12),
    ({"en": "Bath Services", "fr": "Bains Services"}, "3400", "received", 8),
]
# Address, title in each language.
INSPIRATION: list[tuple[str, dict[str, str]]] = [
    (
        "https://example.com/walk-in-shower",
        {"en": "Walk-in shower ideas", "fr": "Idées de douche à l'italienne"},
    ),
    ("https://example.com/terrazzo-tiles", {"en": "Terrazzo tiles", "fr": "Carrelage terrazzo"}),
]
ENVELOPE = "6000"
LANGUAGES = ("en", "fr")


def load(owner: User, language: str, today: datetime.date) -> Home:
    """Create the demo home of `owner` in `language`, with rooms, tasks and projects."""
    with transaction.atomic():
        home = homes.create_home(owner, name=HOME_NAME[language])
        made = {
            key: rooms.create_room(owner, home, name=names[language], kind=kind)
            for key, (kind, names) in ROOMS.items()
        }
        for titles, room, days, fields in TASKS:
            tasks.create_task(
                owner,
                home,
                due_on=today + datetime.timedelta(days=days),
                title=titles[language],
                room=made[room],
                **fields,
            )
        paint = projects.create_project(
            owner,
            home,
            rooms=[made["living"]],
            title=PAINT_TITLE[language],
            goal=PAINT_GOAL[language],
            status="planned",
            priority="normal",
            estimated_minutes=960,
        )
        for labels, nature, planned in PAINT_LINES:
            projects.create_line(
                owner,
                paint,
                label=labels[language],
                nature=nature,
                planned=Decimal(planned),
                paid=Decimal("0"),
            )
        _descaling(owner, home, made["kitchen"], language, today)
        _bathroom(owner, home, made["bathroom"], language, today)
        projects.set_envelope(owner, home, year=today.year, amount=Decimal(ENVELOPE))
    return home


def _descaling(owner: User, home: Home, kitchen: Room, language: str, today: datetime.date) -> None:
    """Scenario A: the espresso machine, its descaling task and the filter in stock."""
    machine = rooms.create_item(owner, kitchen, name=MACHINE[language], kind="equipment")
    task = tasks.create_task(
        owner,
        home,
        due_on=today,
        title=DESCALING[language],
        room=kitchen,
        item=machine,
        procedure=DESCALING_STEPS[language],
        repeat_mode="after_last",
        repeat_unit="month",
        repeat_interval=3,
        estimated_minutes=15,
    )
    article = stock.create_article(
        owner,
        home,
        compatible_items=[machine],
        name=FILTER[language],
        storage_location=FILTER_PLACE[language],
        unit="piece",
        unit_price=Decimal(FILTER_PRICE),
        threshold=Decimal("1"),
    )
    stock.record_movement(
        owner,
        article,
        kind="entry",
        quantity=Decimal(FILTERS_IN_STOCK),
        occurred_on=today - datetime.timedelta(days=30),
    )
    materials.add_material(owner, task, article, Decimal("1"))


def _bathroom(owner: User, home: Home, bathroom: Room, language: str, today: datetime.date) -> None:
    """Scenario B: the bathroom project, from its tasks to what is planned, committed, paid."""
    project = projects.create_project(
        owner,
        home,
        rooms=[bathroom],
        title=BATHROOM[language],
        goal=BATHROOM_GOAL[language],
        status="planned",
        priority="normal",
    )
    for titles, days in BATHROOM_TASKS:
        projects.add_task(
            owner,
            project,
            due_on=today + datetime.timedelta(days=days),
            title=titles[language],
            room=bathroom,
        )
    lines = [
        projects.create_line(
            owner,
            project,
            label=labels[language],
            nature=nature,
            planned=Decimal(planned),
            paid=Decimal(paid),
        )
        for labels, nature, planned, paid in BATHROOM_LINES
    ]
    for names, amount, status, days in QUOTES:
        contractor = tasks.create_contractor(owner, home, name=names[language], trade="plumber")
        issued = today - datetime.timedelta(days=days)
        projects.create_quote(
            owner,
            project,
            contractor=contractor,
            status=status,
            budget_line=lines[1] if status == "accepted" else None,
            amount=Decimal(amount),
            issued_on=issued,
            valid_until=issued + datetime.timedelta(days=90),
        )
    for url, titles in INSPIRATION:
        # Saved without reading the page: loading the demo never calls the network.
        links._save(owner, project, url, titles[language], False)


class Command(BaseCommand):
    help = "Add a fictional demo home to an existing account, in English or in French."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--owner", required=True, help="E-mail of an existing account.")
        parser.add_argument("--language", choices=LANGUAGES, default="en")

    def handle(self, *args: Any, **options: Any) -> None:
        owner = User.objects.filter(email__iexact=options["owner"]).first()
        if owner is None:
            raise CommandError("No account with this e-mail: create it first.")
        language: str = options["language"]
        if Home.objects.filter(memberships__user=owner, name=HOME_NAME[language]).exists():
            raise CommandError(f"This account already has the home {HOME_NAME[language]!r}.")
        home = load(owner, language, datetime.date.today())
        self.stdout.write(f"{home.name} ({home.pk})")

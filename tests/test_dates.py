"""One date format per language across the application (docs/adr/0068)."""

import datetime
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.template import Context, Template
from django.test import Client
from django.urls import reverse
from django.utils import timezone, translation

from housecairn.accounts.models import User
from housecairn.core import dates
from housecairn.homes.models import Home
from housecairn.notifications import reminders
from housecairn.notifications.models import Preference
from housecairn.tasks import agenda, services
from housecairn.tasks.models import Occurrence, Task

pytest_plugins = ("tests.tasks_fixtures",)

SRC = Path(settings.BASE_DIR)
DAY = datetime.date(2026, 11, 8)
MOMENT = datetime.datetime(2026, 11, 8, 14, 30, tzinfo=datetime.UTC)
# A day and a month in digits, whatever the separator: `02/08/2027`, `8.2.2027`, `08-02-27`.
NUMERIC_DATE = re.compile(r"(?<![\d.:/-])\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}(?![\d.:/-])")
# The other way each language writes a date, the one of Django's own formats: an abbreviated
# English month followed by a point (`Feb. 8, 2027`), a French month in full where the format of
# the application abbreviates it (`8 février 2027`, but `1 mars 2027` has no short form).
SECOND_FORMAT = {
    "en": re.compile(r"\b(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\. \d{1,2}\b"),
    "fr": re.compile(
        r"\b\d{1,2} (?:janvier|février|avril|juillet|septembre|octobre|novembre|décembre) \d{4}"
    ),
}
# What `|date:` may still format in a template: a machine value (an input, an anchor) or a
# part of a calendar (the month, the year, a weekday name). A date read as such is `|day`.
DATE_FILTER_ALLOWED = {"Y-m-d", "F Y", "Y", "F", "l", "D"}


class TestFormats:
    @pytest.mark.parametrize(
        ("language", "day", "moment", "month_day", "weekday"),
        [
            ("en", "Nov 8, 2026", "Nov 8, 2026, 2:30\u202fPM", "Nov 8", "Sun, Nov 8"),
            ("fr", "8 nov. 2026", "8 nov. 2026, 14:30", "8 nov.", "dim. 8 nov."),
        ],
    )
    def test_one_format_by_language(
        self, language: str, day: str, moment: str, month_day: str, weekday: str
    ) -> None:
        with translation.override(language), timezone.override(datetime.UTC):
            assert dates.day(DAY) == day
            assert dates.moment(MOMENT) == moment
            assert dates.month_day(DAY) == month_day
            assert dates.weekday_day(DAY) == weekday

    @pytest.mark.parametrize("language", ["en", "fr"])
    def test_the_month_is_in_letters_and_the_day_is_not_padded(self, language: str) -> None:
        with translation.override(language):
            text = dates.day(datetime.date(2027, 2, 8))
        assert not re.search(r"\d+[/.-]\d+", text)
        assert re.fullmatch(r"(?:\d{1,2} \D+|\D+ \d{1,2},) 2027", text), text

    def test_no_value_shows_nothing(self) -> None:
        assert (dates.day(None), dates.moment(None), dates.weekday_day(None)) == ("", "", "")
        page = Template("{% load dates %}[{{ a|day }}][{{ a|moment }}][{{ a|weekday_day }}]")
        assert page.render(Context({"a": None})) == "[][][]"

    def test_the_filters_use_the_same_formats(self) -> None:
        page = Template("{% load dates %}{{ d|day }}|{{ m|moment }}|{{ d|weekday_day }}")
        with translation.override("fr"), timezone.override(datetime.UTC):
            shown = page.render(Context({"d": DAY, "m": MOMENT}))
        assert shown == "8 nov. 2026|8 nov. 2026, 14:30|dim. 8 nov."

    def test_a_moment_is_shown_in_the_time_zone_of_the_request(self) -> None:
        page = Template("{% load dates %}{{ m|moment }}")
        with translation.override("fr"), timezone.override("Pacific/Auckland"):
            assert page.render(Context({"m": MOMENT})) == "9 nov. 2026, 03:30"


class TestNoNumericDate:
    """Nothing in the sources writes a date with the day and the month in digits."""

    def test_the_templates_format_a_date_only_through_the_filters(self) -> None:
        offenders = []
        for path in sorted((SRC / "templates").rglob("*")):
            if not path.is_file():
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for found in re.finditer(r"\|date:\s*(['\"])(.*?)\1", line):
                    if found.group(2) not in DATE_FILTER_ALLOWED:
                        offenders.append(f"{path.relative_to(SRC)}:{number}: {found.group(0)}")
                if re.search(r"\|time\b|\|timesince|\|timeuntil|naturalday|naturaltime", line):
                    offenders.append(f"{path.relative_to(SRC)}:{number}: {line.strip()[:80]}")
        assert offenders == []

    def test_the_code_does_not_ask_for_the_short_formats(self) -> None:
        offenders = [
            str(path.relative_to(SRC))
            for path in sorted(SRC.rglob("*.py"))
            if "migrations" not in path.parts
            and re.search(
                r"SHORT_DATE|SHORT_DATETIME|DATE_INPUT|%[dm]/%[dm]", path.read_text("utf-8")
            )
        ]
        assert offenders == []

    def test_the_javascript_does_not_format_dates(self) -> None:
        offenders = [
            str(path.relative_to(SRC))
            for path in sorted((SRC / "static").rglob("*.js"))
            if "vendor" not in path.parts
            and re.search(
                r"toLocale\w*String|Intl\.DateTimeFormat|toDateString", path.read_text("utf-8")
            )
        ]
        assert offenders == []


def visible_text(html: str) -> str:
    """The words of a page: no tags, so no attribute (`value="2027-02-08"`) is read as a date."""
    return re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S))


@pytest.mark.django_db
@pytest.mark.usefixtures("media_root", "frozen_today")
class TestScreens:
    @pytest.fixture
    def weekly(self, alice: User, alice_home: Home) -> Task:
        return services.create_task(
            alice,
            alice_home,
            title="Bleed the radiators",
            due_on=datetime.date(2027, 2, 8),
            repeat_mode="fixed",
            repeat_unit="week",
            repeat_interval=1,
        )

    def pages(self, home: Home, task: Task) -> dict[str, str]:
        return {
            "detail": reverse("tasks:detail", kwargs={"home_id": home.id, "task_id": task.id}),
            "list": reverse("tasks:list", kwargs={"home_id": home.id}),
            "today": reverse("tasks:today", kwargs={"home_id": home.id}),
            "edit": reverse("tasks:edit", kwargs={"home_id": home.id, "task_id": task.id}),
        }

    def test_the_task_page_writes_every_date_the_same_way(
        self, signed_in: Client, alice_home: Home, weekly: Task
    ) -> None:
        text = visible_text(
            signed_in.get(self.pages(alice_home, weekly)["detail"]).content.decode()
        )
        assert "Feb 8, 2027" in text  # due on
        assert "Feb 15, 2027, Feb 22, 2027, Mar 1, 2027" in text  # then
        assert NUMERIC_DATE.findall(text) == []

    def test_the_same_pages_in_french(
        self, signed_in: Client, alice_home: Home, weekly: Task
    ) -> None:
        response = signed_in.get(
            self.pages(alice_home, weekly)["detail"], HTTP_ACCEPT_LANGUAGE="fr"
        )
        text = visible_text(response.content.decode())
        assert "8 févr. 2027" in text
        assert "15 févr. 2027, 22 févr. 2027, 1 mars 2027" in text
        assert NUMERIC_DATE.findall(text) == []

    @pytest.mark.parametrize("name", ["detail", "list", "today", "edit"])
    @pytest.mark.parametrize("language", ["en", "fr"])
    def test_no_screen_of_a_task_shows_a_numeric_date(
        self, signed_in: Client, alice_home: Home, weekly: Task, name: str, language: str
    ) -> None:
        response = signed_in.get(
            self.pages(alice_home, weekly)[name], HTTP_ACCEPT_LANGUAGE=language
        )
        assert response.status_code == 200
        assert NUMERIC_DATE.findall(visible_text(response.content.decode())) == []

    @pytest.mark.parametrize("name", ["detail", "list", "today", "edit"])
    @pytest.mark.parametrize("language", ["en", "fr"])
    def test_no_screen_of_a_task_shows_a_second_format(
        self, signed_in: Client, alice_home: Home, weekly: Task, name: str, language: str
    ) -> None:
        response = signed_in.get(
            self.pages(alice_home, weekly)[name], HTTP_ACCEPT_LANGUAGE=language
        )
        assert response.status_code == 200
        text = visible_text(response.content.decode())
        assert SECOND_FORMAT[language].findall(text) == []


@pytest.mark.django_db
@pytest.mark.usefixtures("frozen_today")
class TestReminders:
    @pytest.mark.parametrize(("language", "shown"), [("en", "Feb 8, 2027"), ("fr", "8 févr. 2027")])
    def test_a_reminder_writes_the_due_date_in_the_format_of_the_screens(
        self, alice: User, alice_home: Home, language: str, shown: str
    ) -> None:
        task = services.create_task(
            alice, alice_home, title="Bleed the radiators", due_on=datetime.date(2027, 2, 8)
        )
        occurrence = Occurrence.objects.get(task=task)
        agenda.update_preferences(alice, language=language)
        prefs = Preference(user=alice)
        _title, body = reminders.reminder_message(occurrence, prefs)
        assert shown in body
        assert NUMERIC_DATE.findall(body) == []
        assert SECOND_FORMAT[language].findall(body) == []

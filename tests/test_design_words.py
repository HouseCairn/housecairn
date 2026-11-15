"""The words that the design audit found wrong on the web, as a French reader sees them
(design/patterns.md, Glossary and Forms): the filters of the reports, the status of a project,
the memory of a room, and the labels of the forms, which end with no colon."""

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext, pgettext

from housecairn.homes.models import Home
from housecairn.projects.models import ProjectStatus
from housecairn.reports.forms import ReportForm
from housecairn.tasks.forms.occurrence import PostponeForm

pytestmark = pytest.mark.django_db


def test_the_filters_of_the_reports_are_plural_and_masculine() -> None:
    with translation.override("fr"):
        assert pgettext("reports filter", "Open") == "Ouverts"
        assert pgettext("reports filter", "Closed") == "Classés"
        assert pgettext("reports filter", "All") == "Tous"


def test_the_list_of_reports_shows_its_filters_in_french(
    signed_in: Client, alice_home: Home
) -> None:
    url = reverse("reports:list", kwargs={"home_id": alice_home.id})
    html = signed_in.get(url, HTTP_ACCEPT_LANGUAGE="fr").text
    for label in ("Ouverts", "Classés", "Tous"):
        assert f">{label}<" in html, label
    assert ">Toutes<" not in html


def test_a_planned_project_is_prevu() -> None:
    with translation.override("fr"):
        assert str(ProjectStatus.PLANNED.label) == "Prévu"


def test_paint_left_agrees_with_peinture() -> None:
    with translation.override("fr"):
        assert gettext("Paint left in stock") == "Peinture restante en stock"


@pytest.mark.parametrize("language", ["en", "fr"])
def test_a_label_ends_with_no_colon(language: str) -> None:
    with translation.override(language):
        tags = [
            str(PostponeForm()["due_on"].label_tag()),
            str(ReportForm(places={"room": [], "item": [], "article": []})["title"].label_tag()),
        ]
    for tag in tags:
        assert tag.endswith("</label>"), tag
        assert ":</label>" not in tag, tag
        assert "\u00a0:" not in tag, tag

"""The screens of the interactive guide (docs/adr/0084, docs/screens/guide.md).

The guide pages read the example home of the person; the forms its steps open are filled in
from their address, each value checked by its field; the banner is on every page of the example
home; and Delete the example home deletes that home only, after one confirmation.
"""

import json
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from django.http import QueryDict
from django.test import Client
from django.urls import reverse
from django.utils import timezone, translation

from housecairn.accounts.models import User
from housecairn.guide import content, links, prefill, services, states, terms
from housecairn.guide.models import StepTick
from housecairn.homes import services as home_services
from housecairn.homes.models import Home, Invitation, Membership, Role
from housecairn.projects.models import BudgetLine, Project
from housecairn.rooms import services as room_services
from housecairn.rooms.forms import ItemForm
from housecairn.rooms.models import Item, Room
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Task
from tests.example_support import make_example

pytestmark = pytest.mark.django_db

GLOSSARY = Path(__file__).resolve().parents[1] / "design" / "glossary.json"
SCENARIO_2 = reverse("guide:scenario", kwargs={"scenario": "rooms-equipment"})


@pytest.fixture
def client_of() -> object:
    def signed(user: User) -> Client:
        client = Client()
        client.force_login(user)
        return client

    return signed


@pytest.fixture
def alice_client(alice: User) -> Client:
    client = Client()
    client.force_login(alice)
    return client


@pytest.fixture
def example(alice: User) -> Home:
    return services.create_example_home(alice, language="en")


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def _delete_url(home: Home) -> str:
    return reverse("guide:delete", kwargs={"home_id": home.pk})


# ---------- the pages of the guide ----------


def test_the_guide_needs_a_signed_in_person(client: Client) -> None:
    response = client.get(reverse("guide:index"))
    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


def test_the_guide_lists_the_five_scenarios_with_their_progress(alice_client: Client) -> None:
    html = alice_client.get(reverse("guide:index")).content.decode()
    for scenario_id in ("create-home", "rooms-equipment", "recurring-task", "report-problem"):
        assert reverse("guide:scenario", kwargs={"scenario": scenario_id}) in html
    assert reverse("guide:scenario", kwargs={"scenario": "project-budget"}) in html
    assert "0 of 3" in html  # create a home: three steps, none done
    assert "Delete the example home" not in html  # there is none yet


def test_a_scenario_counts_its_ticks(alice_client: Client, example: Home) -> None:
    html = alice_client.get(reverse("guide:index")).content.decode()
    assert "1 of 3" in html  # the example home ticks step 1.1
    assert "Delete the example home" in html


def test_an_unknown_scenario_answers_404(alice_client: Client) -> None:
    assert (
        alice_client.get(reverse("guide:scenario", kwargs={"scenario": "nope"})).status_code == 404
    )


def test_step_one_opens_the_new_home_form_filled_in_from_the_guide(alice_client: Client) -> None:
    html = alice_client.get(reverse("guide:scenario", kwargs={"scenario": "create-home"})).content
    match = re.search(rb'href="([^"]*/h/new/\?[^"]*)"', html)
    assert match is not None
    query = _query(match[1].decode().replace("&amp;", "&"))
    assert query["guide"] == ["1"]
    assert query["name"] == ["Example house"]
    assert query["currency"] == ["EUR"]
    assert query["next"] == [reverse("guide:scenario", kwargs={"scenario": "create-home"})]


def test_the_steps_of_a_later_scenario_wait_for_the_example_home(alice_client: Client) -> None:
    html = alice_client.get(SCENARIO_2).content.decode()
    assert "Add what the earlier scenarios make" in html
    assert "Fill in for me" not in html
    assert "Add the example home first" in html


def test_a_step_links_to_its_form_with_next_back_to_the_scenario(
    alice_client: Client, example: Home
) -> None:
    html = alice_client.get(SCENARIO_2).content.decode().replace("&amp;", "&")
    create = reverse("rooms:create", kwargs={"home_id": example.pk})
    fill = re.search(rf'href="({re.escape(create)}\?[^"]*name=[^"]*)"', html)
    assert fill is not None
    query = _query(fill[1])
    assert query == {"name": ["Kitchen"], "kind": ["kitchen"], "next": [SCENARIO_2]}


def test_open_leads_to_the_same_form_empty(alice_client: Client, example: Home) -> None:
    html = alice_client.get(SCENARIO_2).content.decode().replace("&amp;", "&")
    create = reverse("rooms:create", kwargs={"home_id": example.pk})
    assert f'href="{create}?next={SCENARIO_2.replace("/", "%2F")}"' in html


def test_a_step_without_a_form_opens_its_screen(example: Home) -> None:
    step = next(s for s in content.steps() if s["id"] == "create-home.3")
    found = links.for_step(step, example, "en", SCENARIO_2)
    assert found.fill is None
    assert found.open is not None
    assert found.open.startswith(reverse("tasks:today", kwargs={"home_id": example.pk}) + "?")


def test_a_finished_scenario_says_done(alice: User, alice_client: Client, example: Home) -> None:
    for step_id in ("create-home.2", "create-home.3"):
        services.tick_by_hand(alice, example, step_id)
    html = alice_client.get(reverse("guide:index")).content.decode()
    first = html.split("1. Create a home")[1].split("</li>")[0]
    assert "pill-success" in first
    assert "of 3" not in first


def test_a_scenario_leads_to_the_previous_and_the_next(alice_client: Client) -> None:
    html = alice_client.get(SCENARIO_2).content.decode()
    assert f'href="{reverse("guide:scenario", kwargs={"scenario": "create-home"})}"' in html
    assert f'href="{reverse("guide:scenario", kwargs={"scenario": "recurring-task"})}"' in html


def test_nothing_to_catch_up_once_the_earlier_steps_are_ticked(
    alice: User, alice_client: Client, example: Home
) -> None:
    services.fill_example(alice, example, up_to=3, language="en")
    html = alice_client.get(reverse("guide:scenario", kwargs={"scenario": "recurring-task"}))
    assert b"Add what the earlier scenarios make" not in html.content


def test_a_step_on_a_row_already_there_opens_the_form_of_that_row(
    alice: User, alice_client: Client, example: Home
) -> None:
    services.fill_example(alice, example, up_to=3, language="en")
    machine = Item.objects.get(home=example, name="Espresso machine")
    machine.brand = ""
    machine.save()
    step = next(s for s in content.steps() if s["id"] == "rooms-equipment.4")
    found = links.for_step(step, example, "en", SCENARIO_2)
    edit = reverse("rooms:item-edit", kwargs={"home_id": example.pk, "item_id": machine.pk})
    assert found.fill is not None
    assert found.fill.startswith(edit + "?")
    assert _query(found.fill) == {"brand": ["Brewmark"], "model": ["BM-200"], "next": [SCENARIO_2]}


def test_a_line_waits_for_its_project(example: Home) -> None:
    step = next(s for s in content.steps() if s["id"] == "project-budget.2")
    assert links.for_step(step, example, "en", SCENARIO_2) == links.Links(open=None, fill=None)


# ---------- the new home made from the guide ----------


def test_the_new_home_form_from_the_guide_is_filled_in_with_nothing_ticked(
    alice_client: Client,
) -> None:
    url = links.new_home("fr", SCENARIO_2)
    html = alice_client.get(url).content.decode()
    assert 'value="Maison d&#x27;exemple"' in html
    assert '<input type="hidden" name="guide" value="1">' in html
    assert 'name="equipment"' in html
    assert not re.search(r'name="equipment"[^>]*checked', html)


def test_the_new_home_form_from_the_guide_makes_the_example_home(
    alice: User, alice_client: Client
) -> None:
    data = {"guide": "1", "name": "Example house", "currency": "EUR", "hemisphere": "north"}
    response = alice_client.post(f"{reverse('homes:create')}?next={SCENARIO_2}", data)
    assert response.status_code == 302
    assert response["Location"] == SCENARIO_2
    assert services.example_home_of(alice) is not None
    assert not home_services.homes_for(alice).exists()  # no real home was made


def test_a_second_example_home_is_refused_by_the_form(alice_client: Client, example: Home) -> None:
    data = {"guide": "1", "name": "Again", "currency": "EUR", "hemisphere": "north"}
    response = alice_client.post(reverse("homes:create"), data)
    assert response.status_code == 200
    assert b"You already have an example home" in response.content
    assert Home.objects.filter(is_example=True).count() == 1


def test_the_new_home_form_without_the_flag_makes_a_real_home(
    alice: User, alice_client: Client
) -> None:
    data = {"name": "Example house", "currency": "EUR", "hemisphere": "north"}
    assert alice_client.post(reverse("homes:create"), data).status_code == 302
    assert home_services.homes_for(alice).count() == 1
    assert services.example_home_of(alice) is None


# ---------- forms filled in from their address ----------


def test_a_form_shows_the_values_of_its_query(alice_client: Client, example: Home) -> None:
    url = reverse("rooms:create", kwargs={"home_id": example.pk})
    html = alice_client.get(f"{url}?name=Kitchen&kind=kitchen").content.decode()
    assert 'value="Kitchen"' in html
    assert '<option value="kitchen" selected>' in html


@pytest.mark.parametrize(
    "query",
    ["kind=not-a-kind", "name=" + "x" * 300, "name=", "room=not-a-uuid", "unknown=1"],
)
def test_a_value_its_field_refuses_is_left_out(example: Home, query: str) -> None:
    form = ItemForm(rooms=Room.objects.filter(home=example), home=example, brands=[])
    assert prefill.apply(form, QueryDict(query), ("name", "kind", "room")) == []
    assert form.initial.get("name") is None
    assert form.initial.get("kind") is None


def test_a_value_its_field_accepts_is_shown(example: Home) -> None:
    room = Room.objects.create(home=example, name="Kitchen", kind="kitchen")
    form = ItemForm(rooms=Room.objects.filter(home=example), home=example, brands=[])
    query = QueryDict(f"name=Drill&kind=equipment&room={room.pk}&brand=Acme")
    assert prefill.apply(form, query, ("name", "kind", "room")) == ["name", "kind", "room"]
    assert "brand" not in form.initial  # a field the view does not name is not read


def test_a_hostile_value_is_shown_escaped(alice_client: Client, example: Home) -> None:
    url = reverse("rooms:create", kwargs={"home_id": example.pk})
    html = alice_client.get(url, {"name": '"><script>alert(1)</script>'}).content.decode()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_a_room_of_another_home_is_not_chosen(
    alice_client: Client, alice: User, example: Home, bob_home: Home, bob: User
) -> None:
    bobs_room = room_services.create_room(bob, bob_home, name="Garage", kind="garage")
    url = reverse("rooms:item-add", kwargs={"home_id": example.pk})
    html = alice_client.get(f"{url}?room={bobs_room.pk}&name=Drill").content.decode()
    assert f'<option value="{bobs_room.pk}"' not in html
    assert 'value="Drill"' in html


def test_the_query_opens_no_form_the_person_may_not_use(
    client_of: object, alice: User, alice_home: Home, make_user: object
) -> None:
    reader = make_user()  # type: ignore[operator]
    Membership.objects.create(home=alice_home, user=reader, role=Role.READ_ONLY)
    url = reverse("rooms:create", kwargs={"home_id": alice_home.pk})
    plain = client_of(reader).get(url)  # type: ignore[operator]
    filled = client_of(reader).get(f"{url}?name=Kitchen&kind=kitchen")  # type: ignore[operator]
    assert filled.status_code == plain.status_code
    assert filled.status_code in (403, 404)


def test_a_post_still_checks_what_the_query_filled_in(alice_client: Client, example: Home) -> None:
    url = reverse("rooms:create", kwargs={"home_id": example.pk})
    response = alice_client.post(f"{url}?name=Kitchen&kind=kitchen", {"name": "", "kind": ""})
    assert response.status_code == 200
    assert b"data-form-errors" in response.content
    assert not Room.objects.filter(home=example).exists()


def test_a_form_saved_from_the_guide_comes_back_to_the_scenario(
    alice_client: Client, example: Home
) -> None:
    url = reverse("rooms:create", kwargs={"home_id": example.pk})
    response = alice_client.post(f"{url}?next={SCENARIO_2}", {"name": "Kitchen", "kind": "kitchen"})
    assert response.status_code == 302
    assert response["Location"] == SCENARIO_2
    page = alice_client.get(SCENARIO_2).content.decode()
    assert "rooms-equipment.1" in page
    assert states.ticked(example) >= {"rooms-equipment.1"}


@pytest.mark.parametrize(
    ("route", "kwargs_of", "query", "expected"),
    [
        ("tasks:create", lambda h: {}, "title=Descaling&repeat_interval=3", 'value="Descaling"'),
        ("reports:create", lambda h: {}, "title=The+tap+drips", 'value="The tap drips"'),
        ("projects:create", lambda h: {}, "title=Redo+the+bathroom", 'value="Redo the bathroom"'),
        ("projects:envelope", lambda h: {}, "amount=6000", 'value="6000'),
    ],
)
def test_each_form_of_the_guide_is_filled_in(
    alice_client: Client, example: Home, route: str, kwargs_of: object, query: str, expected: str
) -> None:
    url = reverse(route, kwargs={"home_id": example.pk, **kwargs_of(example)})  # type: ignore[operator]
    html = alice_client.get(f"{url}?{query}").content.decode()
    assert expected in html


def test_the_line_and_the_quote_forms_are_filled_in(
    alice: User, alice_client: Client, example: Home
) -> None:
    services.fill_example(alice, example, up_to=6, language="en")
    project = Project.objects.get(home=example)
    line = BudgetLine.objects.get(project=project, label="Plumber (shower)")
    url = reverse("projects:line-create", kwargs={"home_id": example.pk, "project_id": project.pk})
    assert 'value="Tiles"' in alice_client.get(f"{url}?label=Tiles").content.decode()
    url = reverse("projects:quote-create", kwargs={"home_id": example.pk, "project_id": project.pk})
    html = alice_client.get(f"{url}?contractor_name=Linden&budget_line={line.pk}").content.decode()
    assert 'value="Linden"' in html
    assert f'<option value="{line.pk}" selected>' in html


def test_the_task_form_takes_the_repetition_of_the_guide(
    alice: User, alice_client: Client, example: Home
) -> None:
    services.fill_example(alice, example, up_to=3, language="en")
    task = task_services.create_task(alice, example, title="Descaling")
    step = next(s for s in content.steps() if s["id"] == "recurring-task.2")
    found = links.for_step(step, example, "en", SCENARIO_2)
    assert found.fill is not None
    assert reverse("tasks:edit", kwargs={"home_id": example.pk, "task_id": task.pk}) in found.fill
    html = alice_client.get(found.fill).content.decode()
    assert re.search(r'name="repeats"[^>]*checked', html)
    assert re.search(r'name="repeat_interval"[^>]*value="3"', html)


@pytest.mark.parametrize(
    "route",
    ["rooms:create", "rooms:item-add", "tasks:create", "reports:create", "projects:create"],
)
def test_the_query_fills_in_no_form_of_a_real_home(
    alice_client: Client, alice_home: Home, route: str
) -> None:
    url = reverse(route, kwargs={"home_id": alice_home.pk})
    query = "name=Planted&title=Planted&kind=kitchen"
    html = alice_client.get(f"{url}?{query}").content.decode()
    assert 'value="Planted"' not in html
    assert '<option value="kitchen" selected>' not in html


def test_the_query_changes_no_edit_form_of_a_real_room(
    alice: User, alice_client: Client, alice_home: Home
) -> None:
    room = room_services.create_room(alice, alice_home, name="Study", kind="office")
    item = room_services.create_item(alice, room, name="Desk", kind="equipment")
    edit_room = reverse("rooms:edit", kwargs={"home_id": alice_home.pk, "room_id": room.pk})
    edit_item = reverse("rooms:item-edit", kwargs={"home_id": alice_home.pk, "item_id": item.pk})
    html = alice_client.get(f"{edit_room}?name=Planted").content.decode()
    assert 'value="Study"' in html
    assert 'value="Planted"' not in html
    html = alice_client.get(f"{edit_item}?name=Planted&brand=Planted").content.decode()
    assert 'value="Desk"' in html
    assert 'value="Planted"' not in html


def test_the_query_fills_in_the_edit_form_of_the_example_home(
    alice: User, alice_client: Client, example: Home
) -> None:
    room = room_services.create_room(alice, example, name="Study", kind="office")
    edit = reverse("rooms:edit", kwargs={"home_id": example.pk, "room_id": room.pk})
    assert 'value="Planted"' in alice_client.get(f"{edit}?name=Planted").content.decode()


def test_a_new_home_is_filled_in_from_the_query_only_when_the_guide_opens_it(
    alice_client: Client,
) -> None:
    url = reverse("homes:create")
    assert 'value="Planted"' not in alice_client.get(f"{url}?name=Planted").content.decode()
    assert 'value="Planted"' in alice_client.get(f"{url}?name=Planted&guide=1").content.decode()


# ---------- catching up and ticking by hand ----------


def test_catching_up_makes_the_example_home_and_the_rows_before_the_scenario(
    alice: User, alice_client: Client
) -> None:
    url = reverse("guide:fill", kwargs={"scenario": "recurring-task"})
    response = alice_client.post(url)
    assert response.status_code == 302
    home = services.example_home_of(alice)
    assert home is not None
    assert Room.objects.filter(home=home).count() == 2
    assert not Task.objects.filter(home=home).exists()  # nothing of scenario 3 itself


def test_the_first_scenario_has_nothing_to_catch_up(alice_client: Client) -> None:
    url = reverse("guide:fill", kwargs={"scenario": "create-home"})
    assert alice_client.post(url).status_code == 404


def test_a_reading_step_is_ticked_by_hand(alice_client: Client, example: Home) -> None:
    response = alice_client.post(reverse("guide:tick"), {"step": "create-home.2"})
    assert response.status_code == 302
    assert StepTick.objects.filter(home=example, step_id="create-home.2").exists()


@pytest.mark.parametrize("step", ["create-home.1", "rooms-equipment.1", "nope.1", ""])
def test_a_step_with_a_state_is_never_ticked_by_hand(
    alice_client: Client, example: Home, step: str
) -> None:
    assert alice_client.post(reverse("guide:tick"), {"step": step}).status_code == 404
    assert not StepTick.objects.filter(home=example, step_id=step).exists()


def test_ticking_by_hand_needs_an_example_home(alice_client: Client) -> None:
    assert alice_client.post(reverse("guide:tick"), {"step": "create-home.2"}).status_code == 404


# ---------- the banner and Delete the example home ----------


def test_the_banner_is_on_every_page_of_the_example_home(
    alice_client: Client, example: Home
) -> None:
    for route in ("homes:detail", "tasks:today", "rooms:list", "projects:list"):
        html = alice_client.get(reverse(route, kwargs={"home_id": example.pk})).content.decode()
        assert "example-banner" in html, route
        assert "Delete the example home" in html, route


def test_no_banner_on_a_real_home(alice_client: Client, alice_home: Home, example: Home) -> None:
    html = alice_client.get(reverse("homes:detail", kwargs={"home_id": alice_home.pk})).content
    assert b"example-banner" not in html


def test_a_page_of_the_example_home_ticks_the_steps_now_true(
    alice: User, alice_client: Client, example: Home
) -> None:
    Room.objects.create(home=example, name="Kitchen", kind="kitchen")  # not through the guide
    alice_client.get(reverse("rooms:list", kwargs={"home_id": example.pk}))
    assert StepTick.objects.filter(home=example, step_id="rooms-equipment.1").exists()


def test_delete_shows_one_confirmation_on_the_same_page(
    alice_client: Client, example: Home
) -> None:
    page = reverse("rooms:list", kwargs={"home_id": example.pk})
    html = alice_client.get(f"{page}?delete-example=1").content.decode()
    assert f'action="{_delete_url(example)}"' in html
    assert "Delete the example home?" in html
    assert 'name="confirmation"' not in html  # no name to type
    assert "Export" not in html.split("delete-example-question")[1].split("</form>")[0]
    assert f'href="{page}"' in html  # Cancel: the same page, without the confirmation


def test_the_example_home_is_deleted_in_one_post(
    alice: User, alice_client: Client, example: Home
) -> None:
    response = alice_client.post(_delete_url(example), {"next": SCENARIO_2})
    assert response.status_code == 302
    assert response["Location"] == SCENARIO_2
    assert not Home.all_objects.filter(pk=example.pk).exists()


def test_after_deleting_from_a_page_of_the_home_the_person_lands_on_the_guide(
    alice_client: Client, example: Home
) -> None:
    page = reverse("rooms:list", kwargs={"home_id": example.pk})
    response = alice_client.post(_delete_url(example), {"next": page})
    assert response["Location"] == reverse("guide:index")


def test_delete_has_no_page_of_its_own(alice_client: Client, example: Home) -> None:
    assert alice_client.get(_delete_url(example)).status_code == 405
    assert Home.all_objects.filter(pk=example.pk).exists()


def test_a_real_home_is_never_deleted_this_way(
    alice_client: Client, alice_home: Home, example: Home
) -> None:
    response = alice_client.post(_delete_url(alice_home))
    assert response.status_code == 404
    assert Home.all_objects.filter(pk=alice_home.pk).exists()
    assert Home.all_objects.filter(pk=example.pk).exists()


def test_the_example_home_of_another_account_answers_404(
    client_of: object, bob: User, example: Home
) -> None:
    unknown = reverse("guide:delete", kwargs={"home_id": "00000000-0000-0000-0000-000000000001"})
    bob_client = client_of(bob)  # type: ignore[operator]
    services.create_example_home(bob, language="en")
    theirs = bob_client.post(_delete_url(example))
    nothing = bob_client.post(unknown)
    assert theirs.status_code == nothing.status_code == 404
    assert Home.all_objects.filter(pk=example.pk).exists()


@pytest.mark.parametrize("role", [Role.MEMBER, Role.READ_ONLY])
def test_a_member_who_is_not_its_owner_cannot_delete_it(
    client_of: object, make_user: object, example: Home, role: Role
) -> None:
    # No form or service adds a member to an example home; one forced here still gets a 404.
    member = make_user()  # type: ignore[operator]
    Membership.objects.create(home=example, user=member, role=role)
    response = client_of(member).post(_delete_url(example))  # type: ignore[operator]
    assert response.status_code == 404
    assert Home.all_objects.filter(pk=example.pk).exists()


def test_an_administrator_who_is_not_a_member_cannot_delete_it(
    client_of: object, make_user: object, example: Home
) -> None:
    admin = make_user(is_staff=True, is_superuser=True)  # type: ignore[operator]
    response = client_of(admin).post(_delete_url(example))  # type: ignore[operator]
    assert response.status_code == 404
    assert Home.all_objects.filter(pk=example.pk).exists()


def test_someone_signed_out_cannot_delete_it(client: Client, example: Home) -> None:
    response = client.post(_delete_url(example))
    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]
    assert Home.all_objects.filter(pk=example.pk).exists()


def test_the_page_of_the_example_home_offers_no_typed_deletion(
    alice_client: Client, example: Home
) -> None:
    html = alice_client.get(reverse("homes:detail", kwargs={"home_id": example.pk})).content
    assert reverse("homes:delete", kwargs={"home_id": example.pk}).encode() not in html
    assert reverse("homes:archive", kwargs={"home_id": example.pk}).encode() not in html
    assert b"Delete the example home" in html


# ---------- defence in depth: only its owner has the example home ----------


@pytest.mark.parametrize("role", [Role.MEMBER, Role.READ_ONLY])
def test_a_member_forced_into_the_example_home_has_no_guide_on_it(
    client_of: object, make_user: object, example: Home, role: Role
) -> None:
    # Nothing adds a member to an example home; one forced in the database gets none of it.
    member = make_user()  # type: ignore[operator]
    Membership.objects.create(home=example, user=member, role=role)
    assert services.example_home_of(member) is None
    client = client_of(member)  # type: ignore[operator]
    page = client.get(reverse("homes:detail", kwargs={"home_id": example.pk}))
    assert b"example-banner" not in page.content
    guide = client.get(reverse("guide:index")).content.decode()
    assert "Delete the example home" not in guide
    assert "0 of 3" in guide
    assert client.post(reverse("guide:tick"), {"step": "create-home.2"}).status_code == 404
    assert not StepTick.objects.filter(home=example, step_id="create-home.2").exists()
    client.post(reverse("guide:fill", kwargs={"scenario": "rooms-equipment"}))
    assert not Room.objects.filter(home=example).exists()  # nothing was added to the owner's


def test_an_invitation_to_an_example_home_opens_nothing(
    alice: User, example: Home, make_user: object
) -> None:
    # `invite` refuses an example home; a row forced in the database is still a dead link.
    raw, digest = home_services._new_secret()
    Invitation.objects.create(
        home=example,
        email="new@example.com",
        role=Role.MEMBER,
        invited_by=alice,
        digest=digest,
        expires_at=timezone.now() + timedelta(days=1),
        sent_at=timezone.now(),
        delivered_by_email=True,
    )
    guest = make_user("new@example.com")  # type: ignore[operator]
    assert home_services.find_invitation(raw) is not None  # a live link, but for an example
    with pytest.raises(Invitation.DoesNotExist):
        home_services.accept_invitation(guest, raw)
    assert not Membership.objects.filter(home=example, user=guest).exists()


# ---------- a hostile `next` leads nowhere outside the site ----------

HOSTILE_NEXT = [
    "https://evil.example/x",
    "//evil.example/x",
    "/\\evil.example/x",
    "javascript:alert(1)",
]


@pytest.mark.parametrize("hostile", HOSTILE_NEXT)
def test_deleting_the_example_ignores_a_hostile_next(
    alice_client: Client, example: Home, hostile: str
) -> None:
    response = alice_client.post(_delete_url(example), {"next": hostile})
    assert response.status_code == 302
    assert response["Location"] == reverse("guide:index")
    assert not Home.all_objects.filter(pk=example.pk).exists()


@pytest.mark.parametrize("hostile", HOSTILE_NEXT)
def test_ticking_by_hand_ignores_a_hostile_next(
    alice_client: Client, example: Home, hostile: str
) -> None:
    response = alice_client.post(reverse("guide:tick"), {"step": "create-home.2", "next": hostile})
    assert response.status_code == 302
    assert response["Location"] == reverse("guide:scenario", kwargs={"scenario": "create-home"})
    assert StepTick.objects.filter(home=example, step_id="create-home.2").exists()


@pytest.mark.parametrize("hostile", HOSTILE_NEXT)
@pytest.mark.parametrize("where", ["query", "body"])
def test_a_form_opened_from_a_step_ignores_a_hostile_next(
    alice_client: Client, example: Home, hostile: str, where: str
) -> None:
    url = reverse("rooms:create", kwargs={"home_id": example.pk})
    data = {"name": "Kitchen", "kind": "kitchen"}
    if where == "query":
        response = alice_client.post(f"{url}?{urlencode({'next': hostile})}", data)
    else:
        response = alice_client.post(url, {**data, "next": hostile})
    room = Room.objects.get(home=example, name="Kitchen")
    assert response.status_code == 302
    assert response["Location"] == reverse(
        "rooms:detail", kwargs={"home_id": example.pk, "room_id": room.pk}
    )


# ---------- the forms carry the token of the site ----------


@pytest.fixture
def strict_client(alice: User) -> Client:
    """A client that checks the CSRF token as a browser's post is checked."""
    client = Client(enforce_csrf_checks=True)
    client.force_login(alice)
    return client


def _token_of_form(html: str, action: str) -> str | None:
    """The CSRF token inside the form that posts to `action`, not one of another form."""
    form = re.search(rf'<form[^>]*action="{re.escape(action)}"[^>]*>(.*?)</form>', html, re.S)
    assert form is not None, f"no form posts to {action}"
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', form[1])
    return token[1] if token else None


def test_the_deletion_of_the_example_needs_the_token_the_page_carries(
    strict_client: Client, example: Home
) -> None:
    page = reverse("rooms:list", kwargs={"home_id": example.pk})
    html = strict_client.get(f"{page}?delete-example=1").content.decode()
    token = _token_of_form(html, _delete_url(example))
    assert token is not None  # the form of the confirmation itself carries one
    refused = strict_client.post(_delete_url(example), {"next": page})
    assert refused.status_code == 403
    assert Home.all_objects.filter(pk=example.pk).exists()
    done = strict_client.post(_delete_url(example), {"csrfmiddlewaretoken": token})
    assert done.status_code == 302
    assert done["Location"] == reverse("guide:index")
    assert not Home.all_objects.filter(pk=example.pk).exists()


def test_mark_as_done_needs_the_token_the_page_carries(
    strict_client: Client, example: Home
) -> None:
    page = reverse("guide:scenario", kwargs={"scenario": "create-home"})
    html = strict_client.get(page).content.decode()
    token = _token_of_form(html, reverse("guide:tick"))
    assert token is not None
    data = {"step": "create-home.2", "next": page}
    assert strict_client.post(reverse("guide:tick"), data).status_code == 403
    assert not StepTick.objects.filter(home=example, step_id="create-home.2").exists()
    done = strict_client.post(reverse("guide:tick"), {**data, "csrfmiddlewaretoken": token})
    assert done.status_code == 302
    assert done["Location"] == page
    assert StepTick.objects.filter(home=example, step_id="create-home.2").exists()


def test_adding_what_the_earlier_scenarios_make_needs_the_token_the_page_carries(
    alice: User, strict_client: Client
) -> None:
    fill = reverse("guide:fill", kwargs={"scenario": "rooms-equipment"})
    token = _token_of_form(strict_client.get(SCENARIO_2).content.decode(), fill)
    assert token is not None
    assert strict_client.post(fill).status_code == 403
    assert services.example_home_of(alice) is None  # nothing was made
    done = strict_client.post(fill, {"csrfmiddlewaretoken": token})
    assert done.status_code == 302
    assert done["Location"] == SCENARIO_2
    assert services.example_home_of(alice) is not None


# ---------- the entries ----------


def test_more_has_the_guide_in_help(alice_client: Client) -> None:
    html = alice_client.get(reverse("more")).content.decode()
    assert f'href="{reverse("guide:index")}"' in html


def test_the_welcome_page_of_an_account_with_no_home_offers_the_guide(
    alice_client: Client,
) -> None:
    html = alice_client.get(reverse("index")).content.decode()
    assert 'id="guide-card-title"' in html
    assert f'href="{reverse("guide:index")}"' in html


def test_the_welcome_page_of_a_visitor_offers_no_guide(client: Client) -> None:
    assert reverse("guide:index").encode() not in client.get(reverse("index")).content


def test_an_empty_today_offers_the_guide(alice_client: Client, alice_home: Home) -> None:
    html = alice_client.get(reverse("tasks:today", kwargs={"home_id": alice_home.pk})).content
    assert b'id="guide-card-title"' in html


def test_a_today_with_tasks_offers_no_guide(
    alice: User, alice_client: Client, alice_home: Home
) -> None:
    task_services.create_task(alice, alice_home, title="Smoke alarm", due_on=task_services.today())
    html = alice_client.get(reverse("tasks:today", kwargs={"home_id": alice_home.pk})).content
    assert b'id="guide-card-title"' not in html


def test_my_homes_lists_the_example_home_after_the_real_ones_with_its_mark(
    alice_client: Client, alice_home: Home, example: Home
) -> None:
    html = alice_client.get(reverse("homes:list")).content.decode()
    assert html.index(alice_home.name) < html.index(example.name)
    assert "Example" in html.split(example.name)[1].split("</li>")[0]


def test_the_guide_shows_only_the_example_home_of_the_person(
    client_of: object, bob: User, example: Home
) -> None:
    html = client_of(bob).get(reverse("guide:index")).content.decode()  # type: ignore[operator]
    assert str(example.pk) not in html
    assert "0 of 3" in html


# ---------- the texts ----------


def test_the_terms_cited_by_the_guide_are_those_of_the_glossary() -> None:
    glossary = {term["id"]: term for term in json.loads(GLOSSARY.read_text())["terms"]}
    cited = set(terms.CITED.findall(json.dumps(content.document()["scenarios"])))
    assert cited == set(terms.TERMS)
    for term_id, (context, message) in terms.TERMS.items():
        web = glossary[term_id]["web"][0]
        assert (web.get("msgctxt", ""), web["msgid"]) == (context, message), term_id


@pytest.mark.parametrize("language", ["en", "fr"])
def test_a_cited_term_is_written_in_the_language_of_the_reader(language: str) -> None:
    glossary = {term["id"]: term for term in json.loads(GLOSSARY.read_text())["terms"]}
    with translation.override(language):
        rendered = terms.render("Press {{done}} on {{today}} & <b>")
    assert f"<strong>{glossary['done'][language]}</strong>" in rendered.replace("&#x27;", "'")
    assert "&amp; &lt;b&gt;" in rendered


def test_the_example_rows_made_by_the_guide_tick_every_step_with_a_state(alice: User) -> None:
    home = make_example(alice)
    with_state = {s["id"] for s in content.steps() if "done_when" in s}
    assert with_state <= set(states.tick(home)) | states.ticked(home)

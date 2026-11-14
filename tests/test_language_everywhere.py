"""The language a person chose applies to everything they read (docs/adr/0072).

Each test here failed before the fix it names.
"""

import datetime
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation

from housecairn.accounts.models import User
from housecairn.apitokens import services as token_services
from housecairn.calendar import feeds, ics
from housecairn.core import offline
from housecairn.homes.models import Home
from housecairn.instance import settings_services as instance_services
from housecairn.notifications import reminders
from housecairn.notifications.forms import PreferenceForm
from housecairn.notifications.models import Preference
from housecairn.tasks import agenda
from housecairn.tasks import services as task_services
from housecairn.tasks.models import Occurrence
from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db

SAVE = {"today_group": "urgency", "horizon_days": "7"}


@pytest.fixture(autouse=True)
def _back_to_english() -> Iterator[None]:
    yield
    translation.activate("en")


@pytest.fixture
def admin_user(make_user: Callable[..., User]) -> User:
    return make_user("admin@example.com", is_staff=True, is_superuser=True)


@pytest.fixture
def signed_in(client: Client, alice: User) -> Client:
    client.force_login(alice)
    return client


def reminder_of(user: User, home: Home) -> tuple[str, str]:
    task = task_services.create_task(
        user, home, title="Bleed the radiators", due_on=datetime.date(2027, 2, 8)
    )
    return reminders.reminder_message(Occurrence.objects.get(task=task), Preference(user=user))


class TestOneLanguageForWhatIsSent:
    """Reminders, notifications and feeds read the language of the display preferences."""

    def test_a_reminder_is_in_the_language_chosen_for_the_pages(
        self, alice: User, alice_home: Home
    ) -> None:
        agenda.update_preferences(alice, language="fr")
        title, _body = reminder_of(alice, alice_home)
        assert title == "Rappel HouseCairn"

    def test_without_a_choice_a_reminder_takes_the_default_of_the_instance(
        self, alice: User, alice_home: Home, admin_user: User
    ) -> None:
        instance_services.update(admin_user, {"default_language": "fr"})
        translation.activate("en")  # a worker has no request to ask
        title, _body = reminder_of(alice, alice_home)
        assert title == "Rappel HouseCairn"

    def test_the_calendar_feed_is_in_the_language_chosen(
        self, alice: User, alice_home: Home
    ) -> None:
        agenda.update_preferences(alice, language="fr")
        feed, _raw = feeds.create_feed(alice, None)
        body = ics.render(feed, "https://home.example.com").decode()
        assert "Tous mes logements" in body

    def test_the_notifications_screen_has_no_language_of_its_own(self) -> None:
        assert "language" not in PreferenceForm().fields
        assert not hasattr(Preference(), "language")


class TestTheChoiceIsFound:
    def test_more_says_where_the_language_is(self, signed_in: Client) -> None:
        content = signed_in.get(reverse("more"), HTTP_ACCEPT_LANGUAGE="en").content.decode()
        assert "Language, and how the Today screen is laid out for you" in content

    def test_the_profile_leads_to_it(self, signed_in: Client, alice: User) -> None:
        agenda.update_preferences(alice, language="fr")
        content = signed_in.get(reverse("profile")).content.decode()
        assert "Français" in content
        assert f"{reverse('display-preferences')}?next=/me/profile/" in content


class TestTheChangeAppliesAtOnce:
    def test_the_answer_to_the_change_is_in_the_new_language(self, signed_in: Client) -> None:
        response = signed_in.post(
            reverse("display-preferences"),
            {**SAVE, "language": "fr", "next": reverse("profile")},
            HTTP_ACCEPT_LANGUAGE="en",
            follow=True,
        )
        content = response.content.decode()
        assert "Préférences enregistrées." in content
        assert 'lang="fr"' in content

    def test_a_choice_sets_the_cookie_and_automatic_removes_it(self, signed_in: Client) -> None:
        url = reverse("display-preferences")
        chosen = signed_in.post(url, {**SAVE, "language": "fr"})
        assert chosen.cookies["django_language"].value == "fr"
        automatic = signed_in.post(url, {**SAVE, "language": ""}, HTTP_ACCEPT_LANGUAGE="en")
        assert automatic.cookies["django_language"].value == ""
        page = signed_in.get(reverse("more"), HTTP_ACCEPT_LANGUAGE="en").content.decode()
        assert 'lang="en"' in page


class TestSigningIn:
    def test_the_page_after_signing_in_and_its_message_speak_the_choice(
        self, client: Client, alice: User
    ) -> None:
        agenda.update_preferences(alice, language="fr")
        response = client.post(
            reverse("account_login"),
            {"login": alice.email, "password": PASSWORD},
            HTTP_ACCEPT_LANGUAGE="en",
            follow=True,
        )
        content = response.content.decode()
        assert 'lang="fr"' in content
        assert "Connexion avec" in content
        assert "Successfully signed in" not in content


class TestApiToken:
    def test_a_call_with_a_token_answers_in_the_language_chosen(
        self, alice: User, alice_home: Home
    ) -> None:
        agenda.update_preferences(alice, language="fr")
        _token, raw = token_services.issue_token(
            alice, name="x", scopes=["read"], homes=[alice_home]
        )
        api = Client(HTTP_AUTHORIZATION=f"Bearer {raw}")
        response = api.get(reverse("api-v1:display-preferences"), HTTP_ACCEPT_LANGUAGE="en")
        assert response.status_code == 200
        assert response["Content-Language"] == "fr"


class TestOffline:
    def today(self, client: Client, home: Home, **headers: Any) -> Any:
        return client.get(reverse("tasks:today", kwargs={"home_id": home.id}), **headers)

    def test_the_key_of_the_pages_kept_changes_with_the_language(
        self, signed_in: Client, alice: User, alice_home: Home
    ) -> None:
        english = self.today(signed_in, alice_home)[offline.HEADER]
        agenda.update_preferences(alice, language="fr")
        french = self.today(signed_in, alice_home)[offline.HEADER]
        assert len(english) == len(french) == 32
        assert english != french

    def test_the_page_after_a_change_of_language_purges_once(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        signed_in.post(reverse("display-preferences"), {**SAVE, "language": "fr"})
        assert self.today(signed_in, alice_home)[offline.HEADER] == offline.PURGE
        assert self.today(signed_in, alice_home)[offline.HEADER] != offline.PURGE

    def test_saving_without_changing_the_language_does_not_purge(
        self, signed_in: Client, alice_home: Home
    ) -> None:
        signed_in.post(reverse("display-preferences"), SAVE, HTTP_ACCEPT_LANGUAGE="en")
        response = self.today(signed_in, alice_home, HTTP_ACCEPT_LANGUAGE="en")
        assert response[offline.HEADER] != offline.PURGE

    @pytest.mark.parametrize(("lang", "expected"), [("fr", 'lang="fr"'), ("en", 'lang="en"')])
    def test_the_offline_page_exists_in_each_language(
        self, client: Client, lang: str, expected: str
    ) -> None:
        content = client.get(reverse("offline"), {"lang": lang}).content.decode()
        assert expected in content

    def test_an_unknown_language_gives_the_page_of_the_request(self, client: Client) -> None:
        content = client.get(
            reverse("offline"), {"lang": "../de"}, HTTP_ACCEPT_LANGUAGE="en"
        ).content.decode()
        assert 'lang="en"' in content


class TestSignedOut:
    def test_the_sign_in_page_offers_the_languages(self, client: Client) -> None:
        content = client.get(reverse("account_login")).content.decode()
        assert f'action="{reverse("set_language")}"' in content
        assert '<option value="fr" lang="fr">Français</option>' in content

    def test_choosing_a_language_signed_out_sets_the_cookie(self, client: Client) -> None:
        login = reverse("account_login")
        response = client.post(
            reverse("set_language"), {"language": "fr", "next": login}, HTTP_ACCEPT_LANGUAGE="en"
        )
        assert response.status_code == 302
        assert response.cookies["django_language"].value == "fr"
        assert 'lang="fr"' in client.get(login, HTTP_ACCEPT_LANGUAGE="en").content.decode()

    def test_signed_in_pages_do_not_show_it(self, signed_in: Client) -> None:
        content = signed_in.get(reverse("more")).content.decode()
        assert reverse("set_language") not in content

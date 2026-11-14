"""The test message of a channel is sent by the worker, not during the request (docs/adr/0070).

The tasks run at once in the tests (`HOUSECAIRN_TASKS_SYNC`). Here the queue is replaced by
a list, so that a test decides when, and whether, the worker gets to the task.
"""

import datetime
import socket
from collections.abc import Iterator
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from housecairn.accounts.models import User
from housecairn.notifications import outbound, services, tasks, views
from housecairn.notifications.models import Channel, SendState
from housecairn.tasks import agenda

pytestmark = pytest.mark.django_db

URL = "tgram://123456789:fixture-not-a-token/987654"
Queued = list[tuple[tuple[Any, ...], dict[str, Any]]]


@pytest.fixture(autouse=True)
def _on() -> Iterator[None]:
    with override_settings(HOUSECAIRN_NOTIFICATIONS_ENABLED=True):
        yield


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> Queued:
    """The worker never comes: what was queued is kept, nothing is run."""
    calls: Queued = []
    monkeypatch.setattr(
        "django_q.tasks.async_task", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    return calls


@pytest.fixture
def delivered(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """What went out to the service, without any network."""
    calls: list[tuple[str, str]] = []

    def fake(sealed: str, *, title: str, body: str) -> outbound.Outcome:
        calls.append((title, body))
        return outbound.Outcome.SENT

    monkeypatch.setattr(outbound, "deliver", fake)
    return calls


@pytest.fixture
def channel(alice: User) -> Channel:
    return services.add_channel(alice, name="Phone", url=URL)


@pytest.fixture
def signed_in(client: Client, alice: User) -> Client:
    client.force_login(alice)
    return client


def page_url(name: str, channel: Channel) -> str:
    return reverse(f"notifications:{name}", kwargs={"channel_id": channel.id})


def run_worker(queued: Queued) -> None:
    """Do what the worker would, for what is waiting."""
    for args, _kwargs in queued:
        assert args[0] == "housecairn.notifications.tasks.send_test"
        tasks.send_test(*args[1:])
    queued.clear()


def age(channel: Channel, seconds: int) -> None:
    Channel.objects.filter(pk=channel.pk).update(
        test_started_at=timezone.now() - datetime.timedelta(seconds=seconds)
    )


class TestTheRequestDoesNotWait:
    def test_the_web_queues_the_test_and_sends_nothing(
        self,
        signed_in: Client,
        channel: Channel,
        queued: Queued,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def forbidden(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("the request sent the message itself")

        monkeypatch.setattr(outbound, "deliver", forbidden)
        answer = signed_in.post(page_url("test", channel))
        assert answer.status_code == 302
        assert answer["Location"] == page_url("channel", channel)
        channel.refresh_from_db()
        assert channel.test_state == SendState.RUNNING
        assert channel.test_started_at is not None
        [(args, kwargs)] = queued
        assert args[0] == "housecairn.notifications.tasks.send_test"
        assert args[1] == str(channel.pk)
        # The worker stops the task before the deadline after which the test counts as failed.
        assert kwargs["timeout"] == services.TEST_TASK_SECONDS < services.TEST_DEADLINE_SECONDS
        # A task stopped at its timeout is not given again by django-q2 after its `retry`.
        assert kwargs["ack_failure"] is True

    def test_the_api_answers_202_and_sends_nothing(
        self,
        api_client: APIClient,
        alice: User,
        channel: Channel,
        queued: Queued,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(outbound, "deliver", lambda *a, **k: pytest.fail("sent in the request"))
        api_client.force_authenticate(alice)
        answer = api_client.post(reverse("api-v1:notification-channel-test", args=[channel.id]))
        assert answer.status_code == 202
        assert answer.json() == {
            "state": "running",
            "detail": str(services.TEST_RUNNING),
        }
        detail = api_client.get(reverse("api-v1:notification-channel-detail", args=[channel.id]))
        assert detail.json()["test_state"] == "running"
        assert len(queued) == 1

    def test_the_worker_sends_and_the_result_is_recorded(
        self,
        signed_in: Client,
        api_client: APIClient,
        alice: User,
        channel: Channel,
        queued: Queued,
        delivered: list[tuple[str, str]],
    ) -> None:
        signed_in.post(page_url("test", channel))
        assert delivered == []
        run_worker(queued)
        assert [title for title, _body in delivered] == ["HouseCairn test"]
        channel.refresh_from_db()
        assert channel.test_state == SendState.SENT
        assert channel.test_finished_at is not None
        assert "The test message was sent." in signed_in.get(page_url("channel", channel)).text
        api_client.force_authenticate(alice)
        detail = api_client.get(reverse("api-v1:notification-channel-detail", args=[channel.id]))
        assert detail.json()["test_state"] == "sent"

    def test_the_message_is_in_the_language_of_the_person(
        self, alice: User, channel: Channel, queued: Queued, delivered: list[tuple[str, str]]
    ) -> None:
        agenda.update_preferences(alice, language="fr")
        services.start_test(alice, channel)
        run_worker(queued)
        assert delivered[0][0] == "Test HouseCairn"

    def test_a_failure_of_the_service_is_recorded_as_a_failure(
        self,
        alice: User,
        channel: Channel,
        queued: Queued,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(outbound, "deliver", lambda *a, **k: outbound.Outcome.FAILED)
        services.start_test(alice, channel)
        run_worker(queued)
        channel.refresh_from_db()
        assert channel.test_state == SendState.FAILED

    def test_an_error_in_the_worker_is_a_failure_not_a_test_that_runs_for_ever(
        self,
        alice: User,
        channel: Channel,
        queued: Queued,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def broken(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(outbound, "deliver", broken)
        services.start_test(alice, channel)
        run_worker(queued)
        channel.refresh_from_db()
        assert channel.test_state == SendState.FAILED
        assert channel.test_finished_at is not None


class TestTheProtectionsHoldInTheWorker:
    """The same guard as ever, now around a send that the worker makes."""

    def test_a_private_network_is_refused_and_never_called(
        self, alice: User, queued: Queued, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            outbound,
            "_resolve",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.16.1.20", 443))],
        )
        connected: list[Any] = []
        monkeypatch.setattr(socket.socket, "connect", lambda self, a: connected.append(a))
        local = services.add_channel(alice, name="Local", url="gotifys://gotify.example.com/t")
        services.start_test(alice, local)
        run_worker(queued)
        local.refresh_from_db()
        assert local.test_state == SendState.PRIVATE
        assert connected == []
        assert services.test_result(local) == str(services.REFUSALS["private"])

    def test_a_name_that_resolves_to_a_forbidden_address_is_not_called(
        self, alice: User, queued: Queued, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            outbound,
            "_resolve",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.1.1", 443))],
        )
        connected: list[Any] = []
        monkeypatch.setattr(socket.socket, "connect", lambda self, a: connected.append(a))
        metadata = services.add_channel(
            alice, name="Metadata", url="gotifys://hooks.example.com/token"
        )
        services.start_test(alice, metadata)
        run_worker(queued)
        metadata.refresh_from_db()
        assert metadata.test_state == SendState.FAILED
        assert connected == []


class TestAlwaysAnEnd:
    def test_a_test_that_ends_never_is_failed_after_its_delay(
        self, alice: User, channel: Channel, queued: Queued
    ) -> None:
        services.start_test(alice, channel)
        age(channel, services.TEST_DEADLINE_SECONDS - 3)
        channel.refresh_from_db()
        assert services.expire_test(channel).test_state == SendState.RUNNING
        age(channel, services.TEST_DEADLINE_SECONDS + 3)
        channel.refresh_from_db()
        assert services.expire_test(channel).test_state == SendState.FAILED
        assert channel.test_finished_at is not None
        assert services.test_result(channel) == str(services.TEST_RESULTS[False])

    def test_every_way_to_read_a_test_applies_the_delay(
        self,
        signed_in: Client,
        api_client: APIClient,
        alice: User,
        channel: Channel,
        queued: Queued,
    ) -> None:
        def lose_the_task() -> None:
            Channel.objects.filter(pk=channel.pk).update(
                test_state=SendState.RUNNING,
                test_started_at=timezone.now()
                - datetime.timedelta(seconds=services.TEST_DEADLINE_SECONDS + 3),
            )

        services.start_test(alice, channel)
        lose_the_task()
        page = signed_in.get(page_url("channel", channel)).text
        assert "could not be sent" in page
        assert "hx-get" not in page
        lose_the_task()
        assert "could not be sent" in signed_in.get(page_url("test-status", channel)).text
        lose_the_task()
        api_client.force_authenticate(alice)
        detail = api_client.get(reverse("api-v1:notification-channel-detail", args=[channel.id]))
        assert detail.json()["test_state"] == "failed"

    def test_a_result_that_comes_after_the_delay_changes_nothing(
        self, alice: User, channel: Channel, queued: Queued, delivered: list[tuple[str, str]]
    ) -> None:
        services.start_test(alice, channel)
        age(channel, services.TEST_DEADLINE_SECONDS + 3)
        channel.refresh_from_db()
        services.expire_test(channel)
        run_worker(queued)
        channel.refresh_from_db()
        assert channel.test_state == SendState.FAILED
        assert delivered == []  # a task that comes late sends nothing

    def test_a_result_that_comes_while_the_delay_runs_out_changes_nothing(
        self, alice: User, channel: Channel, queued: Queued, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def slow(sealed: str, *, title: str, body: str) -> outbound.Outcome:
            # The service answers after the deadline: the page already said "failed".
            age(channel, services.TEST_DEADLINE_SECONDS + 3)
            services.expire_test(Channel.objects.get(pk=channel.pk))
            return outbound.Outcome.SENT

        monkeypatch.setattr(outbound, "deliver", slow)
        services.start_test(alice, channel)
        run_worker(queued)
        channel.refresh_from_db()
        assert channel.test_state == SendState.FAILED

    def test_the_result_of_an_older_test_does_not_end_a_newer_one(
        self, alice: User, channel: Channel, queued: Queued, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def slow(sealed: str, *, title: str, body: str) -> outbound.Outcome:
            # Meanwhile the first test timed out and the person started another.
            age(channel, services.TEST_DEADLINE_SECONDS + 3)
            services.start_test(alice, Channel.objects.get(pk=channel.pk))
            return outbound.Outcome.SENT

        monkeypatch.setattr(outbound, "deliver", slow)
        services.start_test(alice, channel)
        first = queued[0]
        queued.clear()
        tasks.send_test(*first[0][1:])
        channel.refresh_from_db()
        assert channel.test_state == SendState.RUNNING
        assert len(queued) == 1  # the second test is still waiting for its worker

    def test_the_documented_deadlines_are_the_ones_in_force(self) -> None:
        # Pinned: the guides, the screens and the API name 30 seconds.
        assert services.TEST_DEADLINE_SECONDS == 30
        assert services.TEST_TASK_SECONDS == 25

    def test_a_test_is_running_at_29_seconds_and_failed_at_31(
        self, alice: User, channel: Channel, queued: Queued
    ) -> None:
        services.start_test(alice, channel)
        age(channel, 29)
        channel.refresh_from_db()
        assert services.expire_test(channel).test_state == SendState.RUNNING
        age(channel, 31)
        channel.refresh_from_db()
        assert services.expire_test(channel).test_state == SendState.FAILED

    def test_a_task_given_again_after_the_deadline_sends_nothing_and_leaves_it_failed(
        self, alice: User, channel: Channel, queued: Queued, delivered: list[tuple[str, str]]
    ) -> None:
        # django-q2 gives back a task stopped at its timeout once `retry` has passed.
        services.start_test(alice, channel)
        age(channel, services.TEST_DEADLINE_SECONDS + 400)  # nobody read the state meanwhile
        run_worker(queued)
        assert delivered == []
        channel.refresh_from_db()
        assert channel.test_state == SendState.FAILED
        assert channel.test_finished_at is not None

    def test_a_task_given_again_after_a_result_sends_nothing_more(
        self, alice: User, channel: Channel, queued: Queued, delivered: list[tuple[str, str]]
    ) -> None:
        services.start_test(alice, channel)
        [(args, _kwargs)] = queued
        run_worker(queued)
        assert len(delivered) == 1
        tasks.send_test(*args[1:])
        assert len(delivered) == 1

    def test_a_test_asked_again_after_a_lost_task_starts_again(
        self, alice: User, channel: Channel, queued: Queued
    ) -> None:
        services.start_test(alice, channel)
        age(channel, services.TEST_DEADLINE_SECONDS + 3)
        channel.refresh_from_db()
        services.start_test(alice, channel)
        assert len(queued) == 2
        channel.refresh_from_db()
        assert channel.test_state == SendState.RUNNING

    def test_a_test_already_running_is_not_started_twice_nor_counted(
        self, alice: User, channel: Channel, queued: Queued
    ) -> None:
        for _ in range(services.TESTS_PER_MINUTE + 3):
            services.start_test(alice, channel)
        assert len(queued) == 1
        run_worker(queued)
        channel.refresh_from_db()
        assert channel.test_state != SendState.RUNNING

    def test_a_task_for_a_test_that_is_not_running_does_nothing(
        self, channel: Channel, delivered: list[tuple[str, str]]
    ) -> None:
        tasks.send_test(str(channel.pk), "en")
        assert delivered == []
        channel.refresh_from_db()
        assert channel.test_state == SendState.NONE

    def test_a_deleted_channel_is_not_an_error(self, delivered: list[tuple[str, str]]) -> None:
        tasks.send_test("00000000-0000-0000-0000-000000000000", "en")
        assert delivered == []


class TestTheScreen:
    def test_the_page_shows_the_test_running_and_asks_again(
        self, signed_in: Client, alice: User, channel: Channel, queued: Queued
    ) -> None:
        shown = signed_in.post(page_url("test", channel), follow=True).text
        status = page_url("test-status", channel)
        assert 'role="status"' in shown
        assert "The test message is being sent." in shown
        assert f'hx-get="{status}?n=1"' in shown
        assert 'hx-trigger="load delay:2s"' in shown

    def test_the_fragment_goes_on_asking_a_few_times_only(
        self, signed_in: Client, alice: User, channel: Channel, queued: Queued
    ) -> None:
        services.start_test(alice, channel)
        status = page_url("test-status", channel)
        last = views.TEST_POLLS
        middle = signed_in.get(status, {"n": last - 1})
        assert f'hx-get="{status}?n={last}"' in middle.text
        done = signed_in.get(status, {"n": last})
        assert "hx-get" not in done.text
        assert "Reload this page in a moment" in done.text
        # A silly count is no count.
        assert 'hx-get="' in signed_in.get(status, {"n": "many"}).text
        assert "hx-get" not in signed_in.get(status, {"n": 10**9}).text

    def test_the_fragment_stops_asking_once_the_test_ended(
        self, signed_in: Client, alice: User, channel: Channel, queued: Queued
    ) -> None:
        services.start_test(alice, channel)
        status = page_url("test-status", channel)
        assert "hx-get" in signed_in.get(status).text
        run_worker(queued)
        answer = signed_in.get(status)
        assert "hx-get" not in answer.text
        assert "The test message was sent." in answer.text or "could not be sent" in answer.text
        assert answer["Cache-Control"] == "no-store" or "no-store" in answer["Cache-Control"]

    def test_without_script_reloading_the_page_shows_the_result(
        self, signed_in: Client, alice: User, channel: Channel, queued: Queued
    ) -> None:
        signed_in.post(page_url("test", channel))
        before = signed_in.get(page_url("channel", channel)).text
        assert "Reload this page" not in before  # with script it updates by itself
        assert "being sent" in before
        run_worker(queued)
        after = signed_in.get(page_url("channel", channel)).text
        assert "being sent" not in after
        assert "hx-get" not in after

    def test_nothing_is_shown_for_a_channel_never_tested(
        self, signed_in: Client, channel: Channel
    ) -> None:
        page = signed_in.get(page_url("channel", channel)).text
        assert 'id="channel-test"' in page
        assert "test message" not in page.split('id="channel-test"')[1].split("</div>")[0]

    def test_a_channel_of_someone_else_has_no_status(
        self, client: Client, bob: User, channel: Channel
    ) -> None:
        client.force_login(bob)
        assert client.get(page_url("test-status", channel)).status_code == 404
        assert client.post(page_url("test", channel)).status_code == 404

    def test_a_refusal_before_the_queue_is_said(
        self, signed_in: Client, channel: Channel, queued: Queued
    ) -> None:
        with override_settings(HOUSECAIRN_NOTIFICATIONS_ENABLED=False):
            shown = signed_in.post(page_url("test", channel), follow=True).text
        assert "turned off" in shown
        assert queued == []

    def test_the_limit_is_still_said(
        self, signed_in: Client, alice: User, channel: Channel, queued: Queued
    ) -> None:
        for _ in range(services.TESTS_PER_MINUTE):
            services.start_test(alice, channel)
            Channel.objects.filter(pk=channel.pk).update(test_state=SendState.SENT)
        with pytest.raises(ValidationError, match="Too many"):
            services.start_test(alice, channel)
        shown = signed_in.post(page_url("test", channel), follow=True).text
        assert "Too many tests" in shown

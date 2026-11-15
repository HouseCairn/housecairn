"""Erasing a home writes the row of the erased homes and takes its change log with it."""

import uuid
from collections.abc import Callable

import pytest
from django.db import transaction
from django.utils import timezone

from housecairn.accounts.models import User
from housecairn.apitokens.models import AppConnection
from housecairn.homes import services
from housecairn.homes.models import Home, Membership, Role
from housecairn.sync import changes, erased
from housecairn.sync.models import Change, ErasedHome, ErasedHomeConnection, HomeHead, RowClocks

pytestmark = pytest.mark.django_db


def make_log(home: Home) -> None:
    for _ in range(3):
        changes.record_change(home, "rooms.room", uuid.uuid4(), changes.PUT, {"name": "x"})


def test_erasing_a_home_writes_one_row_in_the_table_of_erased_homes(alice_home: Home) -> None:
    home_id = alice_home.pk
    before = timezone.now()
    services.erase_home(alice_home)
    row = ErasedHome.objects.get(pk=home_id)
    assert before <= row.erased_at <= timezone.now()
    assert not row.connections.exists()
    assert ErasedHome.objects.count() == 1


def test_erasing_a_home_erases_its_positions_changes_and_clocks(alice_home: Home) -> None:
    other = Home.objects.create(name="Other")
    make_log(alice_home)
    make_log(other)
    services.erase_home(alice_home)
    assert not Change.objects.filter(home_id=alice_home.pk).exists()
    assert not HomeHead.objects.filter(home_id=alice_home.pk).exists()
    assert not RowClocks.objects.filter(home_id=alice_home.pk).exists()
    assert Change.objects.filter(home=other).count() == 3
    assert not ErasedHome.objects.filter(pk=other.pk).exists()


def test_an_owner_who_deletes_the_home_leaves_the_row_too(alice: User, alice_home: Home) -> None:
    home_id = alice_home.pk
    services.delete_home(alice, alice_home, confirmation=alice_home.name)
    assert ErasedHome.objects.filter(pk=home_id).exists()
    assert not Home.all_objects.filter(pk=home_id).exists()


def test_a_deletion_that_fails_leaves_no_row(alice: User, alice_home: Home) -> None:
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        services.delete_home(alice, alice_home, confirmation="not its name")
    assert not ErasedHome.objects.exists()


def test_a_deletion_rolled_back_with_its_transaction_leaves_no_row(alice_home: Home) -> None:

    def fail_after_erasing() -> None:
        with transaction.atomic():
            services.erase_home(alice_home)
            raise RuntimeError

    with pytest.raises(RuntimeError):
        fail_after_erasing()
    assert not ErasedHome.objects.exists()
    assert Home.objects.filter(pk=alice_home.pk).exists()


def test_a_failure_while_erasing_leaves_no_row_even_outside_a_transaction(
    alice_home: Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest import mock

    broken = mock.MagicMock()
    broken.filter.return_value.delete.side_effect = RuntimeError
    monkeypatch.setattr(services.Home, "all_objects", broken)
    with pytest.raises(RuntimeError):
        services.erase_home(alice_home)
    assert not ErasedHome.objects.exists()


def test_the_connections_bound_to_the_home_are_kept_with_the_row(
    alice_home: Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    monkeypatch.setattr(erased, "connection_ids_of", lambda home_id: ids)
    services.erase_home(alice_home)
    kept = ErasedHomeConnection.objects.values_list("connection_id", flat=True)
    assert sorted(str(pk) for pk in kept) == sorted(ids)


def test_the_connected_apps_bound_to_the_home_are_read(alice: User, alice_home: Home) -> None:
    bound = AppConnection.objects.create(user=alice)
    bound.homes.add(alice_home)
    revoked = AppConnection.objects.create(user=alice, revoked_at=timezone.now())
    revoked.homes.add(alice_home)
    AppConnection.objects.create(user=alice)  # bound to no home
    other = Home.objects.create(name="Other")
    elsewhere = AppConnection.objects.create(user=alice)
    elsewhere.homes.add(other)
    assert erased.connection_ids_of(alice_home.pk) == sorted([str(bound.pk), str(revoked.pk)])


def test_erasing_a_home_keeps_the_connections_bound_to_it(alice: User, alice_home: Home) -> None:
    bound = AppConnection.objects.create(user=alice)
    bound.homes.add(alice_home)
    services.erase_home(alice_home)
    assert [
        str(pk) for pk in ErasedHomeConnection.objects.values_list("connection_id", flat=True)
    ] == [str(bound.pk)]


def test_a_member_removed_before_the_erasure_is_not_told(
    alice: User, bob: User, alice_home: Home, add_member: Callable[[Home, User, Role], Membership]
) -> None:
    membership = add_member(alice_home, bob, Role.MEMBER)
    kept = AppConnection.objects.create(user=alice)
    kept.homes.add(alice_home)
    left = AppConnection.objects.create(user=bob)
    left.homes.add(alice_home)
    assert erased.connection_ids_of(alice_home.pk) == sorted([str(kept.pk), str(left.pk)])
    services.remove_member(alice, membership)  # bob's connection stays bound to the home
    assert erased.connection_ids_of(alice_home.pk) == [str(kept.pk)]
    services.erase_home(alice_home)
    told = ErasedHomeConnection.objects.values_list("connection_id", flat=True)
    assert [str(pk) for pk in told] == [str(kept.pk)]


def test_a_connection_bound_twice_is_told_once(alice: User, alice_home: Home) -> None:
    bound = AppConnection.objects.create(user=alice)
    bound.homes.add(alice_home)
    erased.record_erased(alice_home.pk)
    erased.record_erased(alice_home.pk)
    assert ErasedHomeConnection.objects.count() == 1


def test_erasing_twice_keeps_one_row_and_the_latest_time(alice_home: Home) -> None:
    erased.record_erased(alice_home.pk)
    first = ErasedHome.objects.get().erased_at
    erased.record_erased(alice_home.pk)
    assert ErasedHome.objects.count() == 1
    assert ErasedHome.objects.get().erased_at >= first


def test_the_row_of_an_erased_home_has_no_foreign_key_to_the_home() -> None:
    field = ErasedHome._meta.pk
    assert field is not None
    assert not field.is_relation

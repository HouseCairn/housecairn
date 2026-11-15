"""The hybrid logical clock of the sync protocol (docs/adr/0077, mobile ADR 0003).

The vectors of `tests/fixtures/sync_clock.json` are shared with the mobile repository, which
replays them: a change to a rule is a change to the file, in both repositories. The properties
(docs/adr/0019) state what the clock promises whatever the wall clock does.
"""

import base64
import datetime
import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from housecairn.sync import clock

VECTORS: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "sync_clock.json").read_text(encoding="utf-8")
)

MAX_MS = (1 << 48) - 1
instants = st.integers(min_value=0, max_value=MAX_MS)
counters = st.integers(min_value=0, max_value=(1 << 16) - 1)
clocks = st.builds(lambda ms, c: (ms << 16) | c, instants, counters)
# A clock with room to grow, so that properties are not about the overflow of 64 bits.
roomy = st.builds(lambda ms, c: (ms << 16) | c, st.integers(0, MAX_MS - 1), counters)
devices = st.binary(min_size=16, max_size=16)
walls = st.integers(min_value=0, max_value=MAX_MS - 1)
# A wall clock with room to be read ahead by up to 10**9 ms (more than the 24 h of drift
# the tests add) and still stay a 64-bit clock once shifted.
roomy_walls = st.integers(min_value=0, max_value=MAX_MS - 1 - 10**9)


def kulkarni_send(last: int, wall: int) -> int:
    """The stepwise rule of Kulkarni et al., Logical Physical Clocks, for a local event."""
    phys, c = last >> 16, last & 0xFFFF
    new_l = max(phys, wall)
    new_c = c + 1 if new_l == phys else 0
    if new_c > 0xFFFF:  # the counter carries into the milliseconds
        new_l, new_c = new_l + 1, 0
    return (new_l << 16) | new_c


def kulkarni_receive(last: int, remote: int, wall: int) -> int:
    phys, c = last >> 16, last & 0xFFFF
    lm, cm = remote >> 16, remote & 0xFFFF
    new_l = max(phys, lm, wall)
    if new_l == phys == lm:
        new_c = max(c, cm) + 1
    elif new_l == phys:
        new_c = c + 1
    elif new_l == lm:
        new_c = cm + 1
    else:
        new_c = 0
    if new_c > 0xFFFF:
        new_l, new_c = new_l + 1, 0
    return (new_l << 16) | new_c


def device(name: str) -> bytes:
    return bytes.fromhex(next(d["hex"] for d in VECTORS["devices"] if d["name"] == name))


# The shared vectors


@pytest.mark.parametrize("vector", VECTORS["clocks"], ids=lambda v: v["text"])
def test_vector_clocks_are_written_and_read_as_sixteen_hexadecimal_digits(
    vector: dict[str, Any],
) -> None:
    value = clock.make_clock(vector["ms"], vector["counter"])
    assert clock.format_clock(value) == vector["text"]
    assert clock.parse_clock(vector["text"]) == value
    assert clock.physical_ms(value) == vector["ms"]
    assert clock.counter_of(value) == vector["counter"]


@pytest.mark.parametrize("vector", VECTORS["devices"], ids=lambda v: v["name"])
def test_vector_device_ids_are_base64url_without_padding(vector: dict[str, Any]) -> None:
    raw = bytes.fromhex(vector["hex"])
    assert clock.device_to_b64(raw) == vector["b64"]
    assert clock.device_from_b64(vector["b64"]) == raw
    assert len(vector["b64"]) == 22
    assert base64.urlsafe_b64decode(vector["b64"] + "==") == raw


@pytest.mark.parametrize("text", VECTORS["invalid_clocks"])
def test_vector_malformed_clocks_are_refused(text: str) -> None:
    with pytest.raises(ValueError, match="16 lower-case"):
        clock.parse_clock(text)


@pytest.mark.parametrize("text", VECTORS["invalid_devices"])
def test_vector_malformed_device_ids_are_refused(text: str) -> None:
    with pytest.raises(ValueError, match="22 characters"):
        clock.device_from_b64(text)


@pytest.mark.parametrize("vector", VECTORS["order"], ids=lambda v: v["name"])
def test_vector_order_of_stamps_is_clock_then_device_bytes(vector: dict[str, Any]) -> None:
    a = clock.parse_clock(vector["a"][0]), clock.device_from_b64(vector["a"][1])
    b = clock.parse_clock(vector["b"][0]), clock.device_from_b64(vector["b"][1])
    assert clock.wins(a, b) is (vector["winner"] == "a")
    assert clock.wins(b, a) is (vector["winner"] == "b")


@pytest.mark.parametrize("vector", VECTORS["tick"], ids=lambda v: v["name"])
def test_vector_local_event(vector: dict[str, Any]) -> None:
    last = clock.parse_clock(vector["last"])
    assert clock.format_clock(clock.tick(last, vector["wall_ms"])) == vector["expected"]


@pytest.mark.parametrize("vector", VECTORS["receive"], ids=lambda v: v["name"])
def test_vector_received_clock(vector: dict[str, Any]) -> None:
    result = clock.receive(
        clock.parse_clock(vector["last"]), clock.parse_clock(vector["remote"]), vector["wall_ms"]
    )
    assert clock.format_clock(result) == vector["expected"]


def test_the_vectors_cover_the_cases_the_rules_name() -> None:
    names = {v["name"] for v in VECTORS["order"]}
    assert "equal clocks: device bytes decide" in names
    assert "the same stamp is a repeat" in names
    assert any("not adopted" in v["name"] for v in VECTORS["receive"])
    assert any("went back" in v["name"] for v in VECTORS["tick"])


# Properties


@given(last=roomy, wall=walls)
def test_a_local_event_is_after_the_last_clock_and_not_before_the_wall_clock(
    last: int, wall: int
) -> None:
    """Monotone: whatever the wall clock does, the next clock is greater than the last."""
    made = clock.tick(last, wall)
    assert made > last
    assert made >> 16 >= wall


@given(last=roomy, wall=walls)
def test_a_local_event_follows_the_stepwise_rule(last: int, wall: int) -> None:
    assert clock.tick(last, wall) == kulkarni_send(last, wall)


@given(last=roomy, walls_=st.lists(walls, min_size=1, max_size=20))
def test_a_wall_clock_that_goes_back_never_makes_the_clock_go_back(
    last: int, walls_: list[int]
) -> None:
    """Any sequence of wall clock readings, going back included, gives a strictly rising clock."""
    seen = last
    for wall in walls_:
        made = clock.tick(seen, wall)
        assert made > seen
        seen = made


@given(last=roomy, remote=roomy, wall=walls)
def test_a_received_clock_follows_the_stepwise_rule_when_it_is_adopted(
    last: int, remote: int, wall: int
) -> None:
    if remote >> 16 > wall + clock.DRIFT_LIMIT_MS:
        return
    assert clock.receive(last, remote, wall) == kulkarni_receive(last, remote, wall)


@given(last=roomy, remote=roomy, wall=walls)
def test_a_received_clock_is_after_both_clocks_unless_it_is_too_far_ahead(
    last: int, remote: int, wall: int
) -> None:
    made = clock.receive(last, remote, wall)
    assert made > last
    if remote >> 16 <= wall + clock.DRIFT_LIMIT_MS:
        assert made > remote


@given(
    last=roomy,
    wall=roomy_walls,
    ahead=st.integers(clock.DRIFT_LIMIT_MS + 1, 10**9),
    c=counters,
)
def test_a_clock_more_than_a_day_ahead_is_not_adopted(
    last: int, wall: int, ahead: int, c: int
) -> None:
    """A device with a wrong date cannot push every other device into the future."""
    remote = ((wall + ahead) << 16) | c
    assert clock.receive(last, remote, wall) == clock.tick(last, wall)


@given(last=roomy, wall=roomy_walls, ahead=st.integers(0, clock.DRIFT_LIMIT_MS), c=counters)
def test_a_clock_up_to_a_day_ahead_is_adopted(last: int, wall: int, ahead: int, c: int) -> None:
    remote = ((wall + ahead) << 16) | c
    assert clock.receive(last, remote, wall) > remote


@given(a=clocks, b=clocks, da=devices, db=devices)
def test_the_order_of_stamps_is_total_and_ties_go_to_the_greater_device(
    a: int, b: int, da: bytes, db: bytes
) -> None:
    sa, sb = (a, da), (b, db)
    if sa == sb:
        assert not clock.wins(sa, sb)
        return
    assert clock.wins(sa, sb) != clock.wins(sb, sa)
    if a == b:
        assert clock.wins(sa, sb) is (da > db)
    else:
        assert clock.wins(sa, sb) is (a > b)


@given(value=clocks)
def test_the_text_of_a_clock_round_trips_and_sorts_like_the_number(value: int) -> None:
    text = clock.format_clock(value)
    assert len(text) == 16
    assert clock.parse_clock(text) == value
    other = min(value + 1, clock.MAX_CLOCK)
    assert (text < clock.format_clock(other)) is (value < other)


@given(raw=devices)
def test_a_device_id_round_trips_through_its_text(raw: bytes) -> None:
    assert clock.device_from_b64(clock.device_to_b64(raw)) == raw


@given(raw=st.binary().filter(lambda b: len(b) != 16))
def test_only_sixteen_bytes_make_a_device_id(raw: bytes) -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        clock.device_to_b64(raw)


# Edges


def test_the_counter_overflows_into_the_milliseconds() -> None:
    last = clock.make_clock(1_000, 0xFFFF)
    assert clock.tick(last, 0) == clock.make_clock(1_001, 0)


def test_the_clock_has_a_largest_value() -> None:
    with pytest.raises(OverflowError):
        clock.tick(clock.MAX_CLOCK, 0)
    with pytest.raises(ValueError, match="64-bit"):
        clock.format_clock(clock.MAX_CLOCK + 1)
    with pytest.raises(ValueError, match="16 bits"):
        clock.make_clock(0, 1 << 16)
    with pytest.raises(ValueError, match="16 bits"):
        clock.make_clock(-1, 0)


def test_the_stored_stamp_must_be_beaten_strictly() -> None:
    stamp = (clock.make_clock(5, 1), device("one"))
    assert clock.wins(stamp, None)
    assert not clock.wins(stamp, stamp)


def test_a_stamp_needs_a_device_of_sixteen_bytes() -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        clock.stamp_of("0" * 16, b"short")


def test_an_instant_reads_as_the_clock_of_its_milliseconds() -> None:
    instant = datetime.datetime(2026, 11, 14, 12, 0, 0, 999_999, tzinfo=datetime.UTC)
    expected_ms = int(instant.timestamp()) * 1000 + 999
    assert clock.clock_of_instant(instant) == expected_ms << 16
    with pytest.raises(ValueError, match="aware"):
        clock.clock_of_instant(datetime.datetime(2026, 1, 1))


def test_an_instant_in_another_zone_reads_the_same() -> None:
    utc = datetime.datetime(2026, 11, 14, 12, 0, tzinfo=datetime.UTC)
    paris = utc.astimezone(datetime.timezone(datetime.timedelta(hours=1)))
    assert clock.clock_of_instant(paris) == clock.clock_of_instant(utc)


def test_the_wall_clock_is_in_milliseconds() -> None:
    before = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    assert before - 1000 <= clock.wall_ms() <= before + 1000

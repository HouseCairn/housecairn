"""The hybrid logical clock of the sync protocol, and the order of its stamps (docs/adr/0077).

These rules are those of mobile ADR 0003 and of the mobile protocol (sections 1 and 2.2); the
vectors of `tests/fixtures/sync_clock.json` pin them for both repositories.

A clock is a 64-bit unsigned integer, `(milliseconds since 1970-01-01 UTC << 16) | counter`,
written as 16 lower-case hexadecimal digits so that the order of the strings is the order of
the numbers. A stamp is a clock with the id of the device that wrote it (16 bytes); two equal
clocks are ordered by the bytes of their device ids.
"""

import base64
import datetime
import re
import time

COUNTER_BITS = 16
MAX_CLOCK = (1 << 64) - 1
# A received clock further than this ahead of the local time is accepted, not adopted.
DRIFT_LIMIT_MS = 24 * 60 * 60 * 1000
DEVICE_BYTES = 16
ZERO_CLOCK = "0" * 16

_CLOCK = re.compile(r"[0-9a-f]{16}")
_DEVICE = re.compile(r"[A-Za-z0-9_-]{22}")

# A stamp: the clock as a number and the bytes of the device id.
Stamp = tuple[int, bytes]


def wall_ms() -> int:
    """The wall clock, in milliseconds since the epoch."""
    return time.time_ns() // 1_000_000


def format_clock(value: int) -> str:
    if not 0 <= value <= MAX_CLOCK:
        raise ValueError("A clock is a 64-bit unsigned integer.")
    return f"{value:016x}"


def parse_clock(text: str) -> int:
    """Read 16 lower-case hexadecimal digits; anything else is a malformed clock."""
    if not isinstance(text, str) or _CLOCK.fullmatch(text) is None:
        raise ValueError("A clock is 16 lower-case hexadecimal digits.")
    return int(text, 16)


def make_clock(ms: int, counter: int = 0) -> int:
    """Build a clock from its milliseconds and its 16-bit counter."""
    if ms < 0 or not 0 <= counter < (1 << COUNTER_BITS):
        raise ValueError("Milliseconds must be positive and the counter fit in 16 bits.")
    return _checked((ms << COUNTER_BITS) | counter)


def physical_ms(value: int) -> int:
    """The upper 48 bits of a clock: milliseconds since 1970-01-01 UTC."""
    return value >> COUNTER_BITS


def counter_of(value: int) -> int:
    return value & ((1 << COUNTER_BITS) - 1)


def tick(last: int, wall: int) -> int:
    """The clock of a local event: after `last`, and not before the wall clock.

    Kulkarni et al., *Logical Physical Clocks*: `l' = max(l, pt)`, the counter restarts at
    zero when the wall clock moved on and grows by one when it did not. As one number this is
    `max(last + 1, wall << 16)`; a counter that overflows carries into the milliseconds.
    """
    return _checked(max(last + 1, wall << COUNTER_BITS))


def receive(last: int, remote: int, wall: int) -> int:
    """The local clock after reading a clock written elsewhere.

    The result is after `last`, after `remote` and not before the wall clock (Kulkarni et
    al.). A `remote` more than 24 hours ahead of the wall clock is not adopted: a device with
    a wrong date cannot push everybody into the future, and the result is that of a local
    event.
    """
    if physical_ms(remote) > wall + DRIFT_LIMIT_MS:
        return tick(last, wall)
    return _checked(max(max(last, remote) + 1, wall << COUNTER_BITS))


def _checked(value: int) -> int:
    if value > MAX_CLOCK:
        raise OverflowError("The clock reached its largest value.")
    return value


def wins(new: Stamp, stored: Stamp | None) -> bool:
    """Whether a write stamped `new` replaces a field stamped `stored`.

    Last writer wins per field: the greater clock, then the greater device id. The same stamp
    is a repeat and does not win.
    """
    return stored is None or new > stored


def stamp_of(clock: str, device: bytes) -> Stamp:
    if len(device) != DEVICE_BYTES:
        raise ValueError("A device id is 16 bytes.")
    return parse_clock(clock), device


def device_to_b64(device: bytes) -> str:
    """A device id as the protocol writes it: base64url without padding (22 characters)."""
    if len(device) != DEVICE_BYTES:
        raise ValueError("A device id is 16 bytes.")
    return base64.urlsafe_b64encode(device).rstrip(b"=").decode("ascii")


def device_from_b64(text: str) -> bytes:
    if not isinstance(text, str) or _DEVICE.fullmatch(text) is None:
        raise ValueError("A device id is 22 characters of base64url, without padding.")
    device = base64.urlsafe_b64decode(text + "==")
    if device_to_b64(device) != text:  # trailing bits must be zero: one text per id
        raise ValueError("A device id is 22 characters of base64url, without padding.")
    return device


def clock_of_instant(instant: datetime.datetime) -> int:
    """The clock that reads an `updated_at` (counter zero): a field with no clock of its own.

    The milliseconds are truncated, not rounded, so that the clock is never after the instant.
    """
    if instant.tzinfo is None:
        raise ValueError("An instant must be aware of its time zone.")
    delta = instant - datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
    return make_clock(delta // datetime.timedelta(milliseconds=1))

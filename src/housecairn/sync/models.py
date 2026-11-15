"""The change log of the homes, for the sync API of the mobile app (docs/adr/0077, 0079).

Four tables, all internal: no screen, no route, no API resource. They follow the home: erasing
a home erases its positions, its changes and its clocks; only the table of erased homes
(`ErasedHome`) lives outside it, to say that a home was erased.
"""

from django.db import models
from django.utils import timezone

from housecairn.sync.clock import DEVICE_BYTES, ZERO_CLOCK

CLOCK_LENGTH = 16
ENTITY_LENGTH = 64


class ServerDevice(models.Model):
    """The device id the server writes with: 16 random bytes, made once per instance.

    The first migration of the app makes the single row; an instance backup carries it. It is
    given by `GET /api/v1/sync/info` as `server_device`, and breaks a tie between two equal
    clocks the way the phone does, by comparing the bytes.
    """

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    device = models.BinaryField(max_length=DEVICE_BYTES, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="sync_one_server_device")
        ]

    def __str__(self) -> str:
        return "server device"


class HomeHead(models.Model):
    """The head of the change log of one home: its last position and its clock.

    Writing a change takes a lock on the row of the home (`changes.record_change`), then
    reads and moves this row, so that the order of `pos` is the order of the commits.
    """

    home = models.OneToOneField(
        "homes.Home", on_delete=models.CASCADE, primary_key=True, related_name="+"
    )
    # The position of the last change; the next one is `pos + 1`.
    pos = models.PositiveBigIntegerField(default=0)
    # The highest position the retention purge removed: a cursor below it is expired.
    floor = models.PositiveBigIntegerField(default=0)
    # The last clock the server made or read for this home (16 hexadecimal digits).
    clock = models.CharField(max_length=CLOCK_LENGTH, default=ZERO_CLOCK)

    def __str__(self) -> str:
        return f"{self.home_id} at {self.pos}"


class Change(models.Model):
    """One entry of the change feed of a home: a row, the fields written, the clock, the device."""

    class Kind(models.TextChoices):
        PUT = "put"
        DELETE = "delete"
        RESTORE = "restore"

    home = models.ForeignKey("homes.Home", on_delete=models.CASCADE, related_name="+")
    pos = models.PositiveBigIntegerField()
    # `<app>.<model>` in lower case, as the protocol names the entity types.
    entity = models.CharField(max_length=ENTITY_LENGTH)
    row_id = models.UUIDField()
    kind = models.CharField(max_length=7, choices=Kind.choices)
    # The clock of the operation (16 hexadecimal digits) and the 16 bytes of its device.
    clock = models.CharField(max_length=CLOCK_LENGTH)
    device = models.BinaryField(max_length=DEVICE_BYTES)
    # The fields that were written, with their values: those that lost are not here.
    fields = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["home", "pos"], name="sync_change_position")]
        indexes = [models.Index(fields=["home", "entity", "row_id"], name="sync_change_row")]

    def __str__(self) -> str:
        return f"{self.home_id} #{self.pos} {self.kind} {self.entity}"


class RowClocks(models.Model):
    """The clock of every field of one row, and the fields the server does not know.

    `clocks` maps a field to `[clock, device]` (the device in base64url). `extra` maps a field
    the server has no column for to `[value, clock, device]`: it merges by the same rule and is
    given back by the feed and the snapshot (docs/adr/0077, need 11). A field with no entry in
    `clocks` reads as the clock of the row's `updated_at` (`changes.record_change`).
    """

    home = models.ForeignKey("homes.Home", on_delete=models.CASCADE, related_name="+")
    entity = models.CharField(max_length=ENTITY_LENGTH)
    row_id = models.UUIDField()
    clocks = models.JSONField(default=dict)
    extra = models.JSONField(default=dict)
    # When the server applied the deletion of the row (empty while it is alive): the tombstone
    # stays 400 days from then (`retention`).
    tombstoned_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["home", "entity", "row_id"], name="sync_row_clocks")
        ]

    def __str__(self) -> str:
        return f"{self.entity} {self.row_id}"


class Push(models.Model):
    """A push a phone made, kept 30 days so that a push sent again runs once (docs/adr/0077).

    Per connection: the number the phone gave the push, the SHA-256 of its body and the answer
    the server gave. The same number with the same body gets that answer again without being
    applied; with another body, 409 `push_conflict`.
    """

    connection = models.ForeignKey(
        "apitokens.AppConnection", on_delete=models.CASCADE, related_name="+"
    )
    number = models.PositiveBigIntegerField()
    digest = models.CharField(max_length=64)
    answer = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["connection", "number"], name="sync_push_number")
        ]

    def __str__(self) -> str:
        return f"{self.connection_id} push {self.number}"


class ErasedHome(models.Model):
    """A home that was erased, outside the home: erasing it erased its changes too.

    The connected apps that were bound to the home when it was erased, and whose account was
    still a member of it, are the rows of `ErasedHomeConnection`; they alone get 410
    `home_gone`, anyone else 404.
    """

    # The id the home had.
    id = models.UUIDField(primary_key=True, editable=False)
    erased_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return str(self.id)


class ErasedHomeConnection(models.Model):
    """One connected app told that a home was erased: a row per erased home and connection.

    A table and not a list in the row of the home, so that `GET /sync/homes/` finds the homes
    erased for one connection by an index, whatever the number of erased homes of the instance.
    The connection is a plain identifier, not a foreign key: the row outlives the connection.
    """

    erased = models.ForeignKey(ErasedHome, on_delete=models.CASCADE, related_name="connections")
    connection_id = models.UUIDField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["erased", "connection_id"], name="sync_erased_home_connection"
            )
        ]

    def __str__(self) -> str:
        return f"{self.erased_id} for {self.connection_id}"

"""The unit price of the draws and the plan of the occurrences, sent once (docs/adr/0092).

The phone now reads `tasks.materialdraw.unit_price`, `tasks.occurrence.planned_labour` and
`planned_material`. The rows that held a value before this version (set when the task was
marked done, or by the migrations `tasks.0013` and `tasks.0014`) were never in the feed: a
phone whose cursor is past them would not read them until the row changed again. Each live
row with a value gets one change of the server with those fields, after the head of its home,
so that every phone reads them in its next page of changes; a phone that starts from the
snapshot reads them in the row. As `sync.changes.record_change` writes a change of the server:
a clock after the head of the home and after the `updated_at` of the row, the server's device,
and the clock of each field in the clocks of the row. Nothing for the example home of the
guide (docs/adr/0084), whose rows never leave the device.
"""

import os
from decimal import Decimal
from typing import Any

from django.db import migrations
from django.db.models import Q

from housecairn.sync import clock

CENT = Decimal("0.01")


def _amount(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(CENT), "f")


def send_the_kept_costs(apps: Any, schema_editor: Any) -> None:
    Home = apps.get_model("homes", "Home")
    HomeHead = apps.get_model("sync", "HomeHead")
    Change = apps.get_model("sync", "Change")
    RowClocks = apps.get_model("sync", "RowClocks")
    ServerDevice = apps.get_model("sync", "ServerDevice")
    MaterialDraw = apps.get_model("tasks", "MaterialDraw")
    Occurrence = apps.get_model("tasks", "Occurrence")

    found: dict[Any, list[tuple[str, Any, Any, dict[str, Any]]]] = {}
    draws = MaterialDraw._base_manager.filter(deleted_at__isnull=True, unit_price__isnull=False)
    for home_id, pk, updated_at, price in draws.values_list(
        "home_id", "id", "updated_at", "unit_price"
    ):
        found.setdefault(home_id, []).append(
            ("tasks.materialdraw", pk, updated_at, {"unit_price": _amount(price)})
        )
    occurrences = Occurrence._base_manager.filter(deleted_at__isnull=True).filter(
        Q(planned_labour__isnull=False) | Q(planned_material__isnull=False)
    )
    for home_id, pk, updated_at, labour, material in occurrences.values_list(
        "home_id", "id", "updated_at", "planned_labour", "planned_material"
    ):
        found.setdefault(home_id, []).append(
            (
                "tasks.occurrence",
                pk,
                updated_at,
                {"planned_labour": _amount(labour), "planned_material": _amount(material)},
            )
        )
    if not found:
        return
    homes = set(
        Home._base_manager.filter(pk__in=list(found), is_example=False).values_list(
            "pk", flat=True
        )
    )
    server, _created = ServerDevice.objects.get_or_create(
        pk=1, defaults={"device": os.urandom(clock.DEVICE_BYTES)}
    )
    device = bytes(server.device)
    device_b64 = clock.device_to_b64(device)
    for home_id in sorted(homes, key=str):
        head, _created = HomeHead.objects.get_or_create(home_id=home_id)
        last = clock.parse_clock(head.clock)
        # By entity type, then id: the order of the positions does not depend on the database.
        for entity, pk, updated_at, fields in sorted(
            found[home_id], key=lambda row: (row[0], str(row[1]))
        ):
            made = clock.tick(max(last, clock.clock_of_instant(updated_at)), clock.wall_ms())
            last = made
            stamp = clock.format_clock(made)
            clocks, _created = RowClocks.objects.get_or_create(
                home_id=home_id, entity=entity, row_id=pk
            )
            for name in fields:
                clocks.clocks[name] = [stamp, device_b64]
            clocks.save(update_fields=["clocks"])
            head.pos += 1
            Change.objects.create(
                home_id=home_id,
                pos=head.pos,
                entity=entity,
                row_id=pk,
                kind="put",
                clock=stamp,
                device=device,
                fields=fields,
            )
        head.clock = clock.format_clock(last)
        head.save(update_fields=["pos", "clock"])


class Migration(migrations.Migration):
    dependencies = [
        ("homes", "0008_home_is_example"),
        ("sync", "0005_member_views"),
        ("tasks", "0014_plan_kept_when_done"),
    ]

    operations = [
        migrations.RunPython(send_the_kept_costs, migrations.RunPython.noop),
    ]

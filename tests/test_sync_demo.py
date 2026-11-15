"""The reset of a demonstration instance keeps the device id of the server (docs/adr/0071, 0077)."""

import datetime
import uuid
from pathlib import Path
from typing import Any

import pytest

from housecairn.demo import reset
from housecairn.homes.models import Home
from housecairn.instance import config
from housecairn.sync import changes
from housecairn.sync.models import Change, ServerDevice

pytestmark = pytest.mark.django_db


def test_a_reset_keeps_the_device_of_the_server_and_empties_the_change_log(
    settings: Any, tmp_path: Path
) -> None:
    settings.MEDIA_ROOT = tmp_path
    settings.HOUSECAIRN_DEMO = True
    config.invalidate()
    try:
        reset.reset(language="en", today=datetime.date(2026, 11, 12))
        device = changes.server_device()
        home = Home.objects.get()
        # The demonstration data records its own changes, as any write of a covered app.
        demo = Change.objects.count()
        row = uuid.uuid4()
        changes.record_change(home, "rooms.room", row, changes.PUT, {"name": "x"})
        assert Change.objects.count() == demo + 1
        reset.reset(language="en", today=datetime.date(2026, 11, 12))
        assert bytes(ServerDevice.objects.get(pk=1).device) == device
        assert not Change.objects.filter(row_id=row).exists()
        assert Change.objects.count() == demo
    finally:
        config.invalidate()

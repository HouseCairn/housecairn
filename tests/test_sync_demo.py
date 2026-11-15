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
        marker = changes.record_change(home, "rooms.room", uuid.uuid4(), changes.PUT, {"name": "x"})
        assert marker.change is not None
        reset.reset(language="en", today=datetime.date(2026, 11, 12))
        assert bytes(ServerDevice.objects.get(pk=1).device) == device
        # The log of the old home went with it; the new one starts again from position 1.
        assert not Change.objects.filter(pk=marker.change.pk).exists()
        assert not Change.objects.filter(home_id=home.pk).exists()
        assert Change.objects.filter(pos=1).exists()
    finally:
        config.invalidate()

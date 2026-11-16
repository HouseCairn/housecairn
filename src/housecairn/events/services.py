"""Recording inbound events. Reacting to them (arming tasks) comes with J3."""

import json
from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils.translation import gettext as _

from housecairn.accounts.models import User
from housecairn.apitokens.models import ApiToken
from housecairn.events.models import Event
from housecairn.homes import services as home_services
from housecairn.homes.models import Home


def record_event(
    actor: User,
    home: Home,
    *,
    kind: str,
    payload: dict[str, Any],
    token: ApiToken | None = None,
) -> Event:
    if home.is_example:
        # The example home of the guide receives no event (docs/adr/0084).
        raise PermissionDenied
    if not home_services.can_write(actor, home):
        raise PermissionDenied
    size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    if size > settings.HOUSECAIRN_EVENT_PAYLOAD_MAX_BYTES:
        raise ValidationError(_("Event payload is too large."))
    event: Event = Event.objects.create(
        home=home, kind=kind, payload=payload, received_by=actor, token=token
    )
    return event


def register_export() -> None:
    """Events are part of the export of their home (docs/adr/0023, 0048).

    The token an event came with is not: a token is a secret of a person.
    """
    from housecairn.homes import exports

    exports.register("events", exports.Section("events", Event, exclude=("token",)))

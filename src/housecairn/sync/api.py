"""The sync API, reading (lot M4 of docs/adr/0079): the homes, the feed and the snapshot.

Every view is a `SyncAPIView`: only an access token of a connected app with the scope `sync`
opens it, over the transport rule of docs/adr/0078, and its answers carry `HC-Sync: 1`. A home
is read only when it is bound to the connection and the account is still a member
(`homes_for_request`), and when the sign-in it requires was made (docs/adr/0057). Any other home
answers 404, never 403, so that a phone learns nothing of a home it was not given; an erased
home answers 410 `home_gone`, to the connections bound to it when it was erased only.
"""

import json
import uuid
from typing import Any

from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import exceptions, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from housecairn.apitokens.credentials import connection_of
from housecairn.apitokens.models import AppConnection
from housecairn.apitokens.sync import SyncAPIView
from housecairn.homes import services as home_services
from housecairn.homes import sso
from housecairn.homes.models import Home
from housecairn.homes.scoping import allow, homes_for_request, open_homes_for_request
from housecairn.sync import reading

READ_RATE_ACTION = "housecairn_sync_read"
SNAPSHOT_TYPE = "application/x-ndjson"


class CodedError(exceptions.APIException):
    """An error whose body carries a `code` the app words itself (docs/adr/0077)."""

    def __init__(self, status_code: int, detail: Any, code: str) -> None:
        self.status_code = status_code
        super().__init__(detail, code)
        self.housecairn_code = code


def home_gone() -> CodedError:
    return CodedError(status.HTTP_410_GONE, _("This home was erased."), "home_gone")


def cursor_expired() -> CodedError:
    return CodedError(
        status.HTTP_410_GONE,
        _("This position is older than the oldest change kept: read the snapshot."),
        "cursor_expired",
    )


def _connection(request: Request) -> AppConnection:
    connection = connection_of(request)
    if connection is None:  # CredentialFitsView lets nothing else in
        raise exceptions.NotAuthenticated
    return connection


def _limited(request: Request) -> None:
    """Reads per minute per connection (docs/adr/0077, need 11)."""
    if not allow(request, READ_RATE_ACTION, key=f"connection:{_connection(request).pk}"):
        raise exceptions.Throttled


def readable_home(request: Request, home_id: uuid.UUID) -> Home:
    """The home of the route, as this connection may read it.

    Erased while bound to this connection: 410 `home_gone`. Not one of its homes, or one its
    account left: 404. A home that requires a sign-in this connection was not made with: 403
    `sso_required`, as for an API token.
    """
    if reading.was_bound_when_erased(home_id, _connection(request).pk):
        raise home_gone()
    home = homes_for_request(request).filter(pk=home_id).first()
    if home is None:
        raise exceptions.NotFound
    home_services.check_open(home, sso.signed_in_providers(request))
    return home


def _integer(request: Request, name: str, default: int) -> int:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    if not raw.isascii() or not raw.isdigit() or len(raw) > 20:
        raise exceptions.ParseError(
            _("%(name)s is a whole number, 0 or more.") % {"name": name}
        )
    return int(raw)


_MEMBER = inline_serializer(
    "SyncMember",
    {
        "id": serializers.UUIDField(),
        "name": serializers.CharField(),
        "role": serializers.CharField(),
    },
)


class SyncHomesView(SyncAPIView):
    """`GET /api/v1/sync/homes/`: the homes of the connection, and those erased since."""

    @extend_schema(
        operation_id="sync_homes_list",
        responses=inline_serializer(
            "SyncHomes",
            {
                "homes": inline_serializer(
                    "SyncHome",
                    {
                        "id": serializers.UUIDField(),
                        "name": serializers.CharField(),
                        "role": serializers.CharField(),
                        "archived_at": serializers.CharField(
                            allow_null=True, help_text="An HLC, 16 hexadecimal digits."
                        ),
                        "head": serializers.IntegerField(),
                        "members": _MEMBER,
                    },
                    many=True,
                ),
                "gone": inline_serializer(
                    "SyncGoneHome",
                    {"id": serializers.UUIDField(), "at": serializers.DateTimeField()},
                    many=True,
                ),
            },
        ),
        description=(
            "The homes bound to the connection that its account may open, archived ones "
            "included, with the account's role, the members (never their address) and the "
            "position of the last change (`head`); `gone` lists the homes erased while they "
            "were bound to this connection."
        ),
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        _limited(request)
        connection = _connection(request)
        homes = open_homes_for_request(request).order_by("name", "id")
        return Response(
            {
                "homes": [reading.home_entry(home, request.user) for home in homes],
                "gone": reading.gone_for(connection.pk),
            }
        )


class SyncChangesView(SyncAPIView):
    """`GET /api/v1/sync/homes/{h}/changes?after={pos}&limit={n}`: the feed of a home."""

    @extend_schema(
        operation_id="sync_changes_list",
        parameters=[
            OpenApiParameter(
                "after", int, description="The last position the phone has read; 0 at first."
            ),
            OpenApiParameter(
                "limit",
                int,
                description=(
                    f"The most changes in the page, {reading.DEFAULT_LIMIT} by default, "
                    f"{reading.MAX_LIMIT} at most."
                ),
            ),
        ],
        responses={
            200: inline_serializer(
                "SyncChanges",
                {
                    "changes": inline_serializer(
                        "SyncChange",
                        {
                            "pos": serializers.IntegerField(),
                            "device": serializers.CharField(
                                help_text="The device id of the write, 16 bytes in base64url."
                            ),
                            "op": serializers.DictField(
                                help_text="The operation of the mobile protocol: t, h, id, k, c, f."
                            ),
                        },
                        many=True,
                    ),
                    "head": serializers.IntegerField(),
                    "more": serializers.BooleanField(),
                },
            ),
            400: OpenApiResponse(description="A cursor or a limit that is not a whole number, or a cursor beyond the head."),
            404: OpenApiResponse(description="Not a home of this connection."),
            410: OpenApiResponse(description="`cursor_expired` or `home_gone`."),
        },
        description=(
            "The changes of the home after the position `after`, in position order, every "
            "entity type together. 410 `cursor_expired` when `after` is older than the oldest "
            "change kept: the phone starts again from the snapshot."
        ),
    )
    def get(self, request: Request, home_id: uuid.UUID, *args: Any, **kwargs: Any) -> Response:
        _limited(request)
        home = readable_home(request, home_id)
        after = _integer(request, "after", 0)
        limit = min(_integer(request, "limit", reading.DEFAULT_LIMIT), reading.MAX_LIMIT)
        if limit < 1:
            raise exceptions.ParseError(_("limit is 1 or more."))
        try:
            page = reading.changes_after(home.pk, after, limit)
        except reading.CursorExpiredError:
            raise cursor_expired() from None
        except reading.CursorOutOfRangeError:
            raise exceptions.ParseError(
                _("This position is beyond the last change of the home.")
            ) from None
        return Response({"changes": page.changes, "head": page.head, "more": page.more})


class SyncSnapshotView(SyncAPIView):
    """`GET /api/v1/sync/homes/{h}/snapshot`: the state of a home, in JSON lines."""

    @extend_schema(
        operation_id="sync_snapshot_retrieve",
        responses={
            (200, SNAPSHOT_TYPE): OpenApiResponse(
                OpenApiTypes.STR,
                description=(
                    'JSON lines: one row per line, `{"t", "h", "id", "f": {field: [value, '
                    'clock, device]}}`, tombstones within the retention included, then '
                    '`{"members": [...]}`, then `{"covers": pos}`.'
                ),
            ),
            404: OpenApiResponse(description="Not a home of this connection."),
            410: OpenApiResponse(description="`home_gone`."),
        },
        description=(
            "Every row of the home with every field and its clock, so that a new phone, or "
            "one whose cursor expired, starts from it and then reads the feed after `covers`."
        ),
    )
    def get(self, request: Request, home_id: uuid.UUID, *args: Any, **kwargs: Any) -> HttpResponse:
        _limited(request)
        home = readable_home(request, home_id)
        body = "".join(
            json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n"
            for line in reading.snapshot_lines(home)
        )
        return HttpResponse(body.encode(), content_type=f"{SNAPSHOT_TYPE}; charset=utf-8")

"""The sync API, reading (lot M4 of docs/adr/0079): the homes, the feed and the snapshot.

Every view is a `SyncAPIView`: only an access token of a connected app with the scope `sync`
opens it, over the transport rule of docs/adr/0078, and its answers carry `HC-Sync: 1`. A home
is read only when it is bound to the connection and the account is still a member
(`homes_for_request`), and when the sign-in it requires was made (docs/adr/0057). Any other home
answers 404, never 403, so that a phone learns nothing of a home it was not given; an erased
home answers 410 `home_gone`, to the connections bound to it when it was erased only.
"""

import hashlib
import json
import uuid
from typing import Any, cast

from django.core.exceptions import RequestDataTooBig
from django.db import transaction
from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import exceptions, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from housecairn.accounts.models import User
from housecairn.apitokens.credentials import connection_of
from housecairn.apitokens.models import AppConnection
from housecairn.apitokens.sync import SyncAPIView
from housecairn.homes import services as home_services
from housecairn.homes import sso
from housecairn.homes.models import Home
from housecairn.homes.scoping import allow, homes_for_request, open_homes_for_request
from housecairn.sync import changes as sync_changes
from housecairn.sync import clock, limits, pushing, reading
from housecairn.sync.models import Push

READ_RATE_ACTION = limits.READ_ACTION
SNAPSHOT_RATE_ACTION = limits.SNAPSHOT_ACTION
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


def _snapshot_limited(request: Request) -> None:
    """Snapshots per minute per connection, on top of the reads: it is built in memory."""
    if not allow(request, SNAPSHOT_RATE_ACTION, key=f"connection:{_connection(request).pk}"):
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
        raise exceptions.ParseError(_("%(name)s is a whole number, 0 or more.") % {"name": name})
    return int(raw)


# The answers every route of the sync API may give before it reads its home.
REFUSED: dict[int, OpenApiResponse] = {
    401: OpenApiResponse(description="No valid access token: missing, expired or revoked."),
    403: OpenApiResponse(
        description=(
            "`sync_needs_connection` (an API token or a session), the scope `sync` missing, "
            "`insecure_transport`, or `sso_required`."
        )
    ),
    429: OpenApiResponse(description="Too many requests from this connection."),
}

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
        responses={
            200: inline_serializer(
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
                            "member_id": serializers.UUIDField(
                                help_text=(
                                    "The `id` of the entry of `members` that is the account "
                                    "of the connection."
                                )
                            ),
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
            **REFUSED,
        },
        description=(
            "The homes bound to the connection that its account may open, archived ones "
            "included, with the account's role, the members (never their address), which of them "
            "is the account (`member_id`) and the "
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
                "homes": [reading.home_entry(home, cast(User, request.user)) for home in homes],
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
            400: OpenApiResponse(
                description="A cursor or limit not a whole number, or past the head."
            ),
            404: OpenApiResponse(description="Not a home of this connection."),
            410: OpenApiResponse(description="`cursor_expired` or `home_gone`."),
            **REFUSED,
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
                    "clock, device]}}`, tombstones within the retention included, then "
                    '`{"members": [...]}`, then `{"covers": pos}`.'
                ),
            ),
            404: OpenApiResponse(description="Not a home of this connection."),
            410: OpenApiResponse(description="`home_gone`."),
            **REFUSED,
        },
        description=(
            "Every row of the home with every field and its clock, so that a new phone, or "
            "one whose cursor expired, starts from it and then reads the feed after `covers`. "
            "Limited apart from the other reads, to a few a minute per connection (429)."
        ),
    )
    def get(self, request: Request, home_id: uuid.UUID, *args: Any, **kwargs: Any) -> HttpResponse:
        _limited(request)
        _snapshot_limited(request)
        home = readable_home(request, home_id)
        body = "".join(
            json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n"
            for line in reading.snapshot_lines(home)
        )
        return HttpResponse(body.encode(), content_type=f"{SNAPSHOT_TYPE}; charset=utf-8")


# ---------- writing: the push (lot M5a of docs/adr/0079) ----------

PUSH_RATE_ACTION = limits.PUSH_ACTION
JSON_TYPE = "application/json"
MAX_PUSH_NUMBER = (1 << 63) - 1


def push_conflict() -> CodedError:
    return CodedError(
        status.HTTP_409_CONFLICT,
        _("This push number was used with another body."),
        "push_conflict",
    )


def too_large() -> CodedError:
    return CodedError(
        status.HTTP_400_BAD_REQUEST,
        _("A push holds at most %(ops)s operations and %(bytes)s bytes.")
        % {"ops": pushing.MAX_OPS, "bytes": pushing.MAX_BODY_BYTES},
        "too_large",
    )


def malformed(detail: Any) -> CodedError:
    return CodedError(status.HTTP_400_BAD_REQUEST, detail, "malformed")


def _push_limited(request: Request) -> None:
    """Pushes per minute per connection, apart from the reads (docs/adr/0077, need 11)."""
    if not allow(request, PUSH_RATE_ACTION, key=f"connection:{_connection(request).pk}"):
        raise exceptions.Throttled


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON.")


def _push_body(request: Request) -> tuple[bytes, dict[str, Any]]:
    """The raw bytes of the push and its JSON, within the limits; 400, never 500."""
    if request.content_type.split(";")[0].strip().lower() != JSON_TYPE:
        raise exceptions.UnsupportedMediaType(request.content_type)
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > pushing.MAX_BODY_BYTES:
        raise too_large()
    try:
        raw = request._request.body
    except RequestDataTooBig:
        raise too_large() from None
    if len(raw) > pushing.MAX_BODY_BYTES:
        raise too_large()
    try:
        body = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise malformed(_("The body is not JSON.")) from None
    if not isinstance(body, dict):
        raise malformed(_("The body is a JSON object."))
    return raw, body


def _whole(body: dict[str, Any], name: str, low: int, high: int) -> int:
    value = body.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise malformed(_("%(name)s is a whole number.") % {"name": name})
    return value


def _device_of(connection: AppConnection, text: Any) -> bytes:
    """The phone's device id: fixed by the first push of the connection, then refused if other.

    It may be neither the server's device id, which would let a phone win every tie as the
    web, nor that of another connection still in use. A revoked connection no longer counts:
    a phone signed out then in again keeps its device id (one install, one device id).
    """
    try:
        device = clock.device_from_b64(text)
    except ValueError:
        raise malformed(_("device is a device id, 22 characters of base64url.")) from None
    encoded = clock.device_to_b64(device)
    if device == sync_changes.server_device():
        raise malformed(_("device is the server's own device id."))
    if connection.device_id and connection.device_id != encoded:
        raise CodedError(
            status.HTTP_409_CONFLICT,
            _("This connection pushes with another device id."),
            "wrong_device",
        )
    if not connection.device_id:
        others = AppConnection.objects.filter(
            device_id=encoded, revoked_at__isnull=True, deleted_at__isnull=True
        ).exclude(pk=connection.pk)
        if others.exists():
            raise CodedError(
                status.HTTP_409_CONFLICT,
                _("This device id belongs to another connection."),
                "wrong_device",
            )
        connection.device_id = encoded
        connection.save(update_fields=["device_id"])
    return device


_RESULT = inline_serializer(
    "SyncPushResult",
    {
        "i": serializers.IntegerField(help_text="The index of the operation in `ops`."),
        "status": serializers.ChoiceField(choices=["applied", "ignored", "rejected"]),
        "code": serializers.CharField(
            required=False,
            help_text=(
                "With `rejected`: forbidden, unknown_type, invalid_reference, wrong_home, "
                "id_taken or clock_ahead (a clock more than 24 hours ahead of the server's)."
            ),
        ),
        "lost": serializers.DictField(
            required=False,
            help_text="For each field that lost: [value, clock, device] of the write that won.",
        ),
        "other_version": serializers.DictField(
            required=False, help_text="The phone's long texts that lost, to keep aside."
        ),
    },
    many=True,
)


class SyncPushView(SyncAPIView):
    """`POST /api/v1/sync/homes/{h}/push`: the operations of a phone, applied field by field."""

    @extend_schema(
        operation_id="sync_push_create",
        request=inline_serializer(
            "SyncPush",
            {
                "push": serializers.IntegerField(
                    min_value=1, help_text="The number of the push, per connection."
                ),
                "sv": serializers.IntegerField(help_text="The schema version of the phone."),
                "device": serializers.CharField(
                    help_text="The phone's device id, 16 bytes in base64url."
                ),
                "ops": serializers.ListField(
                    child=serializers.DictField(),
                    max_length=pushing.MAX_OPS,
                    help_text="Operations of the mobile protocol: t, h, id, k, c, f.",
                ),
            },
        ),
        responses={
            200: inline_serializer(
                "SyncPushAnswer", {"head": serializers.IntegerField(), "results": _RESULT}
            ),
            400: OpenApiResponse(
                description=(
                    "`malformed` (with the `index` of the operation), `too_large` or "
                    "`sync_version`; nothing was applied."
                )
            ),
            404: OpenApiResponse(description="Not a home of this connection."),
            409: OpenApiResponse(description="`push_conflict` or `wrong_device`."),
            410: OpenApiResponse(description="`home_gone`: nothing was applied."),
            415: OpenApiResponse(description="The body is not `application/json`."),
            **REFUSED,
        },
        description=(
            "Apply the operations of the phone in one transaction, each field only if its "
            "clock and device beat the stored ones. A push sent again with the same number "
            "and body gets the first answer and is not applied again."
        ),
    )
    def post(self, request: Request, home_id: uuid.UUID, *args: Any, **kwargs: Any) -> Response:
        _push_limited(request)
        home = readable_home(request, home_id)
        connection = _connection(request)
        raw, body = _push_body(request)
        number = _whole(body, "push", 1, MAX_PUSH_NUMBER)
        sv = _whole(body, "sv", 0, MAX_PUSH_NUMBER)
        ops = body.get("ops")
        if not isinstance(ops, list):
            raise malformed(_("ops is a list of operations."))
        if len(ops) > pushing.MAX_OPS:
            raise too_large()
        digest = hashlib.sha256(raw).hexdigest()
        user = cast(User, request.user)
        try:
            with transaction.atomic():
                # One push of a connection at a time: the number is read and kept under the lock.
                AppConnection.objects.select_for_update().only("pk").get(pk=connection.pk)
                kept = Push.objects.filter(connection=connection, number=number).first()
                if kept is not None:
                    if kept.digest != digest:
                        raise push_conflict()
                    return Response(kept.answer)
                if sv < pushing.MIN_SV:
                    raise CodedError(
                        status.HTTP_400_BAD_REQUEST,
                        _("This version of the app reads but does not write: update it."),
                        "sync_version",
                    )
                device = _device_of(connection, body.get("device"))
                role = home_services.role_of(user, home)
                answer = pushing.apply(
                    home, pushing.Pusher(user, role.value if role else "", device), ops
                )
                Push.objects.create(
                    connection=connection, number=number, digest=digest, answer=answer
                )
        except pushing.MalformedError as error:
            return Response(
                {"detail": error.message, "code": "malformed", "index": error.index},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(answer)

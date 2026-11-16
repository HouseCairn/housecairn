"""The sync API (docs/adr/0079): the homes, the feed and the snapshot (M4), the push (M5a), the
homes made by a phone (M5c) and the files (M6).

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
from pathlib import PurePosixPath
from typing import Any, cast

from django.core.exceptions import RequestDataTooBig
from django.db import transaction
from django.http import FileResponse, HttpResponse
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
from housecairn.apitokens.oauth import SCOPE_HOMES_CREATE
from housecairn.apitokens.sync import SyncAPIView
from housecairn.homes import services as home_services
from housecairn.homes import sso
from housecairn.homes.forms import HomeForm
from housecairn.homes.models import Hemisphere, Home
from housecairn.homes.scoping import allow, homes_for_request, open_homes_for_request
from housecairn.sync import changes as sync_changes
from housecairn.sync import clock, creating, files, limits, pushing, reading
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


class SyncHomeSerializer(serializers.Serializer[Any]):
    """A home as `GET /api/v1/sync/homes/` lists it, and as `POST` gives it (schema only)."""

    id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.CharField()
    archived_at = serializers.CharField(allow_null=True, help_text="An HLC, 16 hexadecimal digits.")
    head = serializers.IntegerField()
    member_id = serializers.UUIDField(
        help_text="The `id` of the entry of `members` that is the account of the connection."
    )
    members = _MEMBER


SYNC_HOME = SyncHomeSerializer()

# The answers of `POST /api/v1/sync/homes/` before it reads its body.
REFUSED_CREATE: dict[int, OpenApiResponse] = {
    **REFUSED,
    403: OpenApiResponse(
        description=(
            "`cannot_create_homes` (the scope `homes.create` or the right to create homes "
            "missing), `sync_needs_connection` (an API token or a session), the scope `sync` "
            "missing, or `insecure_transport`."
        )
    ),
}

NEW_HOME_MAX_BYTES = 4096


def _new_home(request: Request) -> tuple[uuid.UUID, dict[str, str]]:
    """The id and the fields of a new home, validated as the web form of a home validates them.

    400 `malformed`, with `fields` when the web would refuse a field; never 500.
    """
    if request.content_type.split(";")[0].strip().lower() != JSON_TYPE:
        raise exceptions.UnsupportedMediaType(request.content_type)
    try:
        raw = request._request.body
    except RequestDataTooBig:
        raise malformed(_("The body is not a home.")) from None
    if len(raw) > NEW_HOME_MAX_BYTES:
        raise malformed(_("The body is not a home."))
    try:
        body = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise malformed(_("The body is not JSON.")) from None
    if not isinstance(body, dict):
        raise malformed(_("The body is a JSON object."))
    text = body.get("id")
    home_id: uuid.UUID | None = None
    if isinstance(text, str) and len(text) == 36:
        try:
            home_id = uuid.UUID(text)
        except ValueError:
            home_id = None
        if home_id is not None and str(home_id) != text.lower():
            home_id = None
    if home_id is None:
        raise malformed(_("id is a UUID in its canonical text form."))
    data: dict[str, str] = {}
    for name in creating.FIELDS:
        value = body.get(name)
        if value is None and name == "hemisphere":
            continue  # left out, the default one, as the web form says
        if not isinstance(value, str):
            raise malformed(_("%(name)s is a text.") % {"name": name})
        data[name] = value
    form = HomeForm(data=data)
    if not form.is_valid():
        raise MalformedHomeError(form.errors.get_json_data())
    return home_id, {name: str(form.cleaned_data[name]) for name in creating.FIELDS}


class MalformedHomeError(Exception):
    """A field the web form of a home refuses: 400 `malformed`, with the messages per field."""

    def __init__(self, errors: dict[str, list[dict[str, str]]]) -> None:
        super().__init__("malformed")
        self.detail = _("A field of the home is not valid.")
        self.fields = {
            name: [error["message"] for error in items] for name, items in errors.items()
        }


class SyncHomesView(SyncAPIView):
    """`GET /api/v1/sync/homes/`: the homes of the connection, and those erased since."""

    @extend_schema(
        operation_id="sync_homes_list",
        responses={
            200: inline_serializer(
                "SyncHomes",
                {
                    "homes": SyncHomeSerializer(many=True),
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

    @extend_schema(
        operation_id="sync_homes_create",
        request=inline_serializer(
            "SyncNewHome",
            {
                "id": serializers.UUIDField(help_text="The home's id, made by the phone."),
                "name": serializers.CharField(max_length=120),
                "currency": serializers.CharField(
                    min_length=3, max_length=3, help_text="An ISO 4217 code, such as EUR."
                ),
                "hemisphere": serializers.ChoiceField(
                    choices=Hemisphere.choices,
                    required=False,
                    help_text="`north` when left out, as on the web.",
                ),
            },
        ),
        responses={
            201: SYNC_HOME,
            200: OpenApiResponse(SYNC_HOME, description="The same home, sent again."),
            400: OpenApiResponse(
                description="`malformed`: a body that is not a home, with `fields` when a "
                "field is refused as the web refuses it."
            ),
            409: OpenApiResponse(
                description=(
                    "`id_taken`: the id is held, by a home with other fields or by anything "
                    "else on the server. The answer does not say which."
                )
            ),
            415: OpenApiResponse(description="The body is not `application/json`."),
            **REFUSED_CREATE,
        },
        description=(
            "Create a home of the phone, with the phone's id, its account the owner, bound to "
            "the connection, and give it as `GET` lists it. Needs the scope `homes.create` and "
            "the right to create homes given at consent. Sent again with the same fields, it "
            "gives the same home (200)."
        ),
    )
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        _push_limited(request)
        connection = _connection(request)
        scopes = str(getattr(request.auth, "scope", "")).split()
        if SCOPE_HOMES_CREATE not in scopes or not connection.may_create_homes:
            raise CodedError(
                status.HTTP_403_FORBIDDEN,
                _("This connection may not create homes."),
                "cannot_create_homes",
            )
        try:
            home_id, fields = _new_home(request)
        except MalformedHomeError as error:
            return Response(
                {"detail": error.detail, "code": "malformed", "fields": error.fields},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = cast(User, request.user)
        try:
            made = creating.create(
                user, connection, open_homes_for_request(request), home_id, fields
            )
        except creating.IdTakenError:
            raise CodedError(
                status.HTTP_409_CONFLICT, _("This id is already taken."), "id_taken"
            ) from None
        return Response(
            reading.home_entry(made.home, user),
            status=status.HTTP_201_CREATED if made.created else status.HTTP_200_OK,
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


# ---------- files (lot M6 of docs/adr/0079) ----------

OCTET_STREAM = "application/octet-stream"
SHA256_HEADER = "HTTP_HC_SHA256"
THUMBNAIL = "thumbnail"
# A file is read from the request in pieces of this size, never more than the limit plus one.
READ_CHUNK = 64 * 1024


def file_too_large(limit: int) -> CodedError:
    return CodedError(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        _("A file holds at most %(bytes)s bytes.") % {"bytes": limit},
        "too_large",
    )


def _file_body(request: Request) -> tuple[bytes, str]:
    """The bytes of an upload and their digest, checked against `HC-Sha256`.

    The length the request announces is checked first, and the body is read up to the limit
    plus one byte only: a file above `file_max_bytes` is refused before it is read in full.
    """
    from housecairn.rooms import conf

    limit = conf.photo_max_bytes()
    declared = request.META.get(SHA256_HEADER, "")
    if not isinstance(declared, str) or files.SHA256.fullmatch(declared) is None:
        raise malformed(
            _("HC-Sha256 is the SHA-256 of the file, 64 lower-case hexadecimal digits.")
        )
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > limit:
        raise file_too_large(limit)
    if length == 0:
        raise malformed(_("The file is empty."))
    media = request.content_type.split(";")[0].strip().lower()
    if not media or media.startswith(("multipart/", "application/x-www-form-urlencoded")):
        raise CodedError(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            _("The body is the file itself, sent as application/octet-stream or its own type."),
            "unsupported_type",
        )
    stream = request._request
    read = bytearray()
    while len(read) <= limit:
        chunk = stream.read(min(READ_CHUNK, limit + 1 - len(read)))
        if not chunk:
            break
        read.extend(chunk)
    if len(read) > limit:
        raise file_too_large(limit)
    data = bytes(read)
    if not data:
        raise malformed(_("The file is empty."))
    if hashlib.sha256(data).hexdigest() != declared:
        raise malformed(_("HC-Sha256 is not the SHA-256 of the bytes received."))
    return data, declared


def _file_response(located: files.Located, *, thumbnail: bool) -> FileResponse:
    """The stored file or its thumbnail, sent as the web sends a photo or an attachment.

    The type is the one found when the file was checked, never the sender's; the name is the
    id. An image is shown inline, anything else is a download. `nosniff` and the sandboxed
    policy stop a browser from running it.
    """
    name = located.thumbnail if thumbnail else located.name
    content_type = "image/jpeg" if thumbnail else located.content_type
    try:
        handle = located.storage.open(name, "rb")
    except (FileNotFoundError, ValueError):
        raise exceptions.NotFound from None
    response = FileResponse(handle, content_type=content_type)
    extension = ".jpg" if thumbnail else PurePosixPath(located.name).suffix
    disposition = "inline" if located.is_image else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{located.file_id}{extension}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cache-Control"] = "private, max-age=3600"
    return response


class SyncFileView(SyncAPIView):
    """`PUT` and `GET /api/v1/sync/homes/{h}/files/{file_id}`: the files of the phone."""

    @extend_schema(
        operation_id="sync_file_upload",
        request={OCTET_STREAM: OpenApiTypes.BINARY},
        parameters=[
            OpenApiParameter(
                "HC-Sha256",
                str,
                location=OpenApiParameter.HEADER,
                required=True,
                description="The SHA-256 of the body, 64 lower-case hexadecimal digits.",
            )
        ],
        responses={
            201: OpenApiResponse(description="The file is kept, under the phone's id."),
            200: OpenApiResponse(description="The same bytes were uploaded before."),
            400: OpenApiResponse(
                description="`malformed`: `HC-Sha256` missing, or not the digest of the body."
            ),
            403: OpenApiResponse(
                description="`forbidden` for a read-only member, or as for every route."
            ),
            404: OpenApiResponse(description="Not a home of this connection."),
            409: OpenApiResponse(
                description=(
                    "`file_conflict`: the id holds other bytes, or is held by anything else. "
                    "The answer does not say which."
                )
            ),
            410: OpenApiResponse(description="`home_gone`."),
            413: OpenApiResponse(
                description=(
                    "`too_large` above `file_max_bytes` of `info.limits`, refused before the "
                    "body is read in full; `quota_exceeded` when the home has no room left."
                )
            ),
            415: OpenApiResponse(
                description="`unsupported_type`: not an image or a document the web accepts."
            ),
            **{code: REFUSED[code] for code in (401, 429)},
        },
        description=(
            "Upload a file of the phone under the id the phone made, before the push whose "
            "operation refers to it. An image is checked and written again as on the web (its "
            "metadata dropped); a document must be one the web accepts for an attachment. "
            "Sent again with the same bytes, it answers 200."
        ),
    )
    def put(
        self, request: Request, home_id: uuid.UUID, file_id: uuid.UUID, *args: Any, **kwargs: Any
    ) -> Response:
        _push_limited(request)
        home = readable_home(request, home_id)
        if not home_services.can_write(cast(User, request.user), home):
            raise CodedError(
                status.HTTP_403_FORBIDDEN, _("A read-only member sends no file."), "forbidden"
            )
        data, digest = _file_body(request)
        try:
            created = files.receive(home, file_id, data, digest)
        except files.ConflictError:
            raise CodedError(
                status.HTTP_409_CONFLICT,
                _("This file id is already taken by other bytes."),
                "file_conflict",
            ) from None
        except files.UnsupportedError as error:
            raise CodedError(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, error.message, "unsupported_type"
            ) from None
        except files.QuotaError as error:
            raise CodedError(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, error.message, "quota_exceeded"
            ) from None
        return Response(
            {"file": str(file_id), "sha256": digest},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="sync_file_download",
        parameters=[
            OpenApiParameter(
                "size",
                str,
                enum=[THUMBNAIL],
                description="`thumbnail`: a small JPEG of an image, made by the server.",
            )
        ],
        responses={
            (200, OCTET_STREAM): OpenApiResponse(
                OpenApiTypes.BINARY,
                description=(
                    "The file the server keeps, with its own type: an image inline, anything "
                    "else as a download."
                ),
            ),
            400: OpenApiResponse(description="`size` other than `thumbnail`."),
            404: OpenApiResponse(
                description=(
                    "Not a home of this connection, or no file of that id in the home (not "
                    "uploaded yet, or a thumbnail of a document)."
                )
            ),
            410: OpenApiResponse(description="`home_gone`."),
            **REFUSED,
        },
        description=(
            "A file of the home by its id: one a phone uploaded, or the file of a photo, an "
            "attachment or a file of a project made on the web, named by the id of its row."
        ),
    )
    def get(
        self, request: Request, home_id: uuid.UUID, file_id: uuid.UUID, *args: Any, **kwargs: Any
    ) -> FileResponse:
        _limited(request)
        home = readable_home(request, home_id)
        size = request.query_params.get("size")
        if size not in (None, THUMBNAIL):
            raise exceptions.ParseError(_("size is thumbnail, or left out."))
        located = files.locate(home.pk, file_id)
        if located is None or (size == THUMBNAIL and not located.thumbnail):
            raise exceptions.NotFound
        return _file_response(located, thumbnail=size == THUMBNAIL)

"""API of notifications: the caller's channels and reminder preferences (docs/adr/0043).

Same rules as the web, through `services`. An API token reaches these endpoints only with
the `write` scope (a `read` token reads nothing here, an `events` token nothing at all):
a channel makes the server send messages, which reading must not allow. Adding, changing,
deleting or testing a channel and changing the preferences belong to the person: a token,
which stands for an integration tied to homes, gets a 403 there (`PersonOnlyToChange`).
"""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import exceptions, mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from housecairn.accounts.api import PersonOnlyToChange
from housecairn.apitokens.models import ApiToken, Scope
from housecairn.apitokens.permissions import TokenScopePermission
from housecairn.notifications import devices, services
from housecairn.notifications.models import Channel, Device, Preference


class WriteTokenOnly(permissions.BasePermission):
    message = "This token does not have the scope this request needs."

    def has_permission(self, request: Request, view: Any) -> bool:
        token = request.auth
        return not isinstance(token, ApiToken) or token.has_scope(Scope.WRITE)


# The default checks as well: a token also needs `read` to read.
PERMISSIONS = [permissions.IsAuthenticated, TokenScopePermission, WriteTokenOnly]


def _invalid(exc: DjangoValidationError) -> exceptions.ValidationError:
    if hasattr(exc, "error_dict"):
        return exceptions.ValidationError(exc.message_dict)
    return exceptions.ValidationError({"detail": exc.messages})


class ChannelSerializer(serializers.ModelSerializer[Channel]):
    url = serializers.CharField(
        write_only=True,
        max_length=2000,
        help_text="Apprise address, such as `ntfys://ntfy.sh/topic`. Never returned.",
    )

    test_state = serializers.SerializerMethodField(
        help_text="Where the last test message stands: `running`, `sent`, `failed` or "
        "`private` (refused: private network); empty if the channel was never tested. A test "
        "that has not ended 30 seconds after it started is `failed`."
    )

    class Meta:
        model = Channel
        fields = [
            "id",
            "name",
            "url",
            "scheme",
            "service",
            "enabled",
            "test_state",
            "created_at",
        ]
        read_only_fields = ["id", "scheme", "service", "created_at"]

    @extend_schema_field(OpenApiTypes.STR)
    def get_test_state(self, channel: Channel) -> str:
        return str(services.expire_test(channel).test_state)


class ChannelUpdateSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=60, required=False)
    enabled = serializers.BooleanField(required=False)


class TestSerializer(serializers.Serializer[Any]):
    state = serializers.CharField(
        help_text="`running`: the worker is sending it; read `test_state` of the channel "
        "later. A test already running is not started twice."
    )
    detail = serializers.CharField(help_text="One of a few fixed sentences.")


class ChannelViewSet(
    PersonOnlyToChange,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet[Channel],
):
    serializer_class = ChannelSerializer
    pagination_class = None
    permission_classes = PERMISSIONS

    def get_queryset(self) -> Any:
        return services.channels_of(self.request.user)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = ChannelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            channel = services.add_channel(
                request.user,
                name=serializer.validated_data["name"],
                url=serializer.validated_data["url"],
            )
        except DjangoValidationError as exc:
            raise _invalid(exc) from None
        return Response(ChannelSerializer(channel).data, status=201)

    @extend_schema(request=ChannelUpdateSerializer, responses=ChannelSerializer)
    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        channel = self.get_object()
        serializer = ChannelUpdateSerializer(data=request.data)
        # A field that cannot change is refused, not ignored: a 200 would say it changed.
        fixed = sorted(set(request.data) - set(serializer.fields))
        if fixed:
            raise exceptions.ValidationError(
                {
                    f: [str(services.ADDRESS_FIXED if f == "url" else services.FIELD_FIXED)]
                    for f in fixed
                }
            )
        serializer.is_valid(raise_exception=True)
        try:
            services.update_channel(request.user, channel, **serializer.validated_data)
        except DjangoValidationError as exc:
            raise _invalid(exc) from None
        return Response(ChannelSerializer(channel).data)

    def perform_destroy(self, instance: Channel) -> None:
        services.delete_channel(self.request.user, instance)

    @extend_schema(request=None, responses={202: TestSerializer})
    @action(detail=True, methods=["post"])
    def test(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Queue a real test message (202). The worker sends it; read `test_state` later.

        The answer never carries the service's own.
        """
        channel = self.get_object()
        try:
            channel = services.start_test(request.user, channel)
        except DjangoValidationError as exc:
            raise _invalid(exc) from None
        return Response(
            {"state": channel.test_state, "detail": services.test_result(channel)},
            status=status.HTTP_202_ACCEPTED,
        )


class PreferenceSerializer(serializers.ModelSerializer[Preference]):
    class Meta:
        model = Preference
        fields = sorted(services.PREFERENCE_FIELDS)


class PreferencesView(PersonOnlyToChange, APIView):
    """When the caller wants reminders: delay, time, quiet hours, summary."""

    permission_classes = PERMISSIONS

    @extend_schema(responses=PreferenceSerializer)
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return Response(PreferenceSerializer(services.preferences_of(request.user)).data)  # type: ignore[arg-type]

    @extend_schema(request=PreferenceSerializer, responses=PreferenceSerializer)
    def patch(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        current = services.preferences_of(request.user)  # type: ignore[arg-type]
        serializer = PreferenceSerializer(current, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            prefs = services.update_preferences(request.user, **serializer.validated_data)
        except DjangoValidationError as exc:
            raise _invalid(exc) from None
        return Response(PreferenceSerializer(prefs).data)


class ExportSerializer(serializers.Serializer[Any]):
    devices = serializers.ListField(
        child=serializers.DictField(), help_text="Name and push service: never the endpoint."
    )
    channels = serializers.ListField(
        child=serializers.DictField(), help_text="Name, scheme, service: never the address."
    )
    preferences = serializers.DictField()


class ExportView(APIView):
    """What the account keeps for notifications, without any channel address."""

    permission_classes = PERMISSIONS

    @extend_schema(responses=ExportSerializer)
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return Response(services.export_of(request.user))


class DeviceSerializer(serializers.ModelSerializer[Device]):
    """A device that receives notifications by Web Push (docs/adr/0066).

    The subscription of the browser is written once and never returned: the endpoint is a
    capability, and the keys are the device's.
    """

    endpoint = serializers.CharField(
        write_only=True, max_length=1024, help_text="`PushSubscription.endpoint`. Never returned."
    )
    p256dh = serializers.CharField(
        write_only=True, max_length=200, help_text="`keys.p256dh` of the subscription."
    )
    auth = serializers.CharField(
        write_only=True, max_length=200, help_text="`keys.auth` of the subscription."
    )

    class Meta:
        model = Device
        fields = [
            "id",
            "name",
            "service",
            "endpoint",
            "p256dh",
            "auth",
            "created_at",
            "last_sent_at",
        ]
        read_only_fields = ["id", "service", "created_at", "last_sent_at"]


class DeviceTestSerializer(serializers.Serializer[Any]):
    detail = serializers.CharField()


class DeviceUpdateSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=60)


class DeviceViewSet(
    PersonOnlyToChange,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet[Device],
):
    """The caller's devices. Adding, renaming and removing one belong to the person with
    the session (a token gets a 403); a token with `write` may read the list."""

    serializer_class = DeviceSerializer
    pagination_class = None
    permission_classes = PERMISSIONS

    def get_queryset(self) -> Any:
        return devices.devices_of(self.request.user)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = DeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            device = devices.subscribe(request.user, **serializer.validated_data)
        except DjangoValidationError as exc:
            raise _invalid(exc) from None
        return Response(DeviceSerializer(device).data, status=201)

    @extend_schema(request=DeviceUpdateSerializer, responses=DeviceSerializer)
    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        device = self.get_object()
        serializer = DeviceUpdateSerializer(data=request.data)
        fixed = sorted(set(request.data) - set(serializer.fields))
        if fixed:
            raise exceptions.ValidationError({f: [str(services.FIELD_FIXED)] for f in fixed})
        serializer.is_valid(raise_exception=True)
        try:
            devices.rename(request.user, device, name=serializer.validated_data["name"])
        except DjangoValidationError as exc:
            raise _invalid(exc) from None
        return Response(DeviceSerializer(device).data)

    def perform_destroy(self, instance: Device) -> None:
        devices.revoke(self.request.user, instance)

    @extend_schema(request=None, responses=DeviceTestSerializer)
    @action(detail=True, methods=["post"])
    def test(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Queue a test notification; the background worker sends it."""
        device = self.get_object()
        try:
            devices.test(request.user, device)
        except DjangoValidationError as exc:
            raise _invalid(exc) from None
        return Response({"detail": str(devices.TEST_QUEUED)}, status=202)

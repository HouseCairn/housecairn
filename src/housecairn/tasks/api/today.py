"""API v1 of Today, My home and the display preferences (docs/adr/0037)."""

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from rest_framework import exceptions, serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from housecairn.accounts.api import PersonOnlyToChange
from housecairn.homes.scoping import active_homes_for_request
from housecairn.rooms.api import (
    HomeResourceMixin,
    _uuid_param,
    _validation,
)
from housecairn.rooms.models import Item
from housecairn.tasks import agenda
from housecairn.tasks.api.occurrences import OccurrenceSerializer
from housecairn.tasks.models import (
    DisplayPreference,
)


class PreferencesSerializer(serializers.ModelSerializer[DisplayPreference]):
    language = serializers.ChoiceField(
        choices=[("", "automatic"), *settings.LANGUAGES],
        required=False,
        help_text="Interface language of the person: a language the instance ships, or empty "
        "to follow the browser, then the default of the instance.",
    )

    class Meta:
        model = DisplayPreference
        fields = [
            "today_group",
            "horizon_days",
            "show_overdue",
            "show_upcoming",
            "show_season",
            "language",
        ]

    def to_representation(self, instance: DisplayPreference) -> dict[str, Any]:
        data: dict[str, Any] = super().to_representation(instance)
        # A value written behind the model's back is not a choice the instance ships.
        if data["language"] not in {code for code, _name in settings.LANGUAGES}:
            data["language"] = ""
        return data


class SectionSerializer(serializers.Serializer[Any]):
    key = serializers.CharField(
        help_text="`overdue`, `today`, `upcoming`, `room`, `person`, `day` or `season`."
    )
    title = serializers.CharField(help_text="Heading, in the language of the request.")
    ref = serializers.CharField(
        allow_null=True, help_text="The room or the person of a `room` or `person` section."
    )
    day = serializers.DateField(allow_null=True, help_text="The date of a `day` section.")
    occurrences = OccurrenceSerializer(many=True, source="rows")


class TodaySerializer(serializers.Serializer[Any]):
    preferences = PreferencesSerializer()
    sections = SectionSerializer(many=True)


class TodayView(HomeResourceMixin, APIView):
    """What to do in a home, laid out by the display preferences of the caller."""

    @extend_schema(responses=TodaySerializer)
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        home = self.get_home()
        prefs = agenda.preferences_of(request.user)  # type: ignore[arg-type]
        context = {"request": request, "home": home}
        sections = agenda.today_sections(home, prefs)
        return Response(
            TodaySerializer({"preferences": prefs, "sections": sections}, context=context).data
        )


class WarrantySerializer(serializers.ModelSerializer[Item]):
    room = serializers.UUIDField(source="room_id", read_only=True)

    class Meta:
        model = Item
        fields = ["id", "name", "room", "warranty_ends_on"]


class DigestSerializer(serializers.Serializer[Any]):
    home = serializers.UUIDField(source="home.id")
    name = serializers.CharField(source="home.name")
    mine = OccurrenceSerializer(many=True, help_text="Open tasks assigned to the caller.")
    overdue = OccurrenceSerializer(many=True, help_text="Late for others or for nobody.")
    unassigned = OccurrenceSerializer(many=True, help_text="Open, not late, nobody in charge.")
    warranties = WarrantySerializer(many=True, help_text="Warranties ending in 60 days.")


class MyHomeView(APIView):
    """What concerns the caller, in one home or in all of theirs."""

    @extend_schema(
        parameters=[OpenApiParameter("home", OpenApiTypes.UUID, description="Only this home.")],
        responses=DigestSerializer(many=True),
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        homes = list(active_homes_for_request(request).order_by("name"))
        wanted = _uuid_param(request, "home")
        if wanted is not None:
            homes = [h for h in homes if h.id == wanted]
            if not homes:
                raise exceptions.NotFound
        digests = agenda.my_home(request.user, homes)  # type: ignore[arg-type]
        return Response(DigestSerializer(digests, many=True, context={"request": request}).data)


class PreferencesView(PersonOnlyToChange, APIView):
    """The display preferences of the caller. A token reads them; only the person changes them."""

    @extend_schema(responses=PreferencesSerializer)
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return Response(PreferencesSerializer(agenda.preferences_of(request.user)).data)  # type: ignore[arg-type]

    @extend_schema(request=PreferencesSerializer, responses=PreferencesSerializer)
    def patch(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = PreferencesSerializer(
            agenda.preferences_of(request.user),  # type: ignore[arg-type]
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        try:
            prefs = agenda.update_preferences(request.user, **serializer.validated_data)
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(PreferencesSerializer(prefs).data)

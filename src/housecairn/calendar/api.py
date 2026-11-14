"""API of the calendar: a range of dates, and the caller's feeds (docs/adr/0052).

The range is read through the same function as the screens. Feeds are the caller's own and
only a person signed in with a session reaches them: a token, of whatever scope, is refused.
"""

import dataclasses
import datetime
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import exceptions, permissions, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from housecairn.apitokens.credentials import Credential, api_token_of, credential_of
from housecairn.apitokens.models import Scope
from housecairn.apitokens.permissions import CredentialFitsView
from housecairn.calendar import feeds, services
from housecairn.calendar.models import Feed
from housecairn.core.links import external_url
from housecairn.homes.scoping import open_homes_for_request, site_url
from housecairn.tasks.models import Priority, Status


class SessionOnly(permissions.BasePermission):
    message = "A feed is managed by a person signed in, not by an API token."

    def has_permission(self, request: Request, view: Any) -> bool:
        return credential_of(request) is Credential.SESSION


class EntrySerializer(serializers.Serializer[Any]):
    date = serializers.DateField(source="day")
    status = serializers.ChoiceField(choices=Status.choices)
    projected = serializers.BooleanField(
        help_text="Computed from the repeat rule, not created yet."
    )
    title = serializers.CharField()
    task = serializers.UUIDField(source="task_id")
    occurrence = serializers.UUIDField(source="occurrence_id", allow_null=True)
    home = serializers.UUIDField(source="home.id")
    room = serializers.UUIDField(source="room_id", allow_null=True)
    assignee = serializers.UUIDField(source="assignee_id", allow_null=True)
    person = serializers.CharField(help_text="Display name of the person in charge, or empty.")
    priority = serializers.ChoiceField(choices=Priority.choices)
    can_complete = serializers.BooleanField(
        help_text=(
            "Open, created, and the caller may change it: a reader may not, and neither may "
            "a token without the `write` scope."
        )
    )


class CalendarSerializer(serializers.Serializer[Any]):
    start = serializers.DateField()
    end = serializers.DateField()
    truncated = serializers.BooleanField(help_text="More entries than the limit: narrow the range.")
    results = EntrySerializer(many=True)


class CalendarView(APIView):
    """The occurrences of the caller's homes from one date to another, projections included."""

    @extend_schema(
        parameters=[
            OpenApiParameter("from", OpenApiTypes.DATE, required=True),
            OpenApiParameter(
                "to", OpenApiTypes.DATE, required=True, description="At most 400 days after `from`."
            ),
            OpenApiParameter("home", OpenApiTypes.UUID, description="Only this home."),
            OpenApiParameter("room", OpenApiTypes.UUID),
            OpenApiParameter("person", OpenApiTypes.STR, description="A member's id, or `none`."),
            OpenApiParameter("priority", OpenApiTypes.STR, enum=list(Priority.values)),
        ],
        responses=CalendarSerializer,
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        params = request.query_params
        bounds: dict[str, datetime.date] = {}
        for name in ("from", "to"):
            try:
                bounds[name] = datetime.date.fromisoformat(params.get(name, ""))
            except ValueError:
                raise exceptions.ValidationError(
                    {name: ["A date is required (YYYY-MM-DD)."]}
                ) from None
        if not 0 <= (bounds["to"] - bounds["from"]).days <= services.MAX_SPAN_DAYS:
            raise exceptions.ValidationError(
                {"to": [f"From 0 to {services.MAX_SPAN_DAYS} days after `from`."]}
            )
        try:
            filters = services.filters_from(params, strict=True)
        except ValueError as exc:
            raise exceptions.ValidationError({str(exc): ["Not a valid value."]}) from None
        homes = list(open_homes_for_request(request).order_by("name"))
        if params.get("home"):
            homes = [h for h in homes if str(h.id) == params["home"].lower()]
            if not homes:
                raise exceptions.NotFound
        result = services.entries(request.user, homes, bounds["from"], bounds["to"], filters)  # type: ignore[arg-type]
        entries = result.entries
        token = api_token_of(request)
        if credential_of(request) is not Credential.SESSION and not (
            token is not None and token.has_scope(Scope.WRITE)
        ):
            # The role of the person would allow it, the token does not: nothing to complete.
            entries = [dataclasses.replace(e, can_write=False) for e in entries]
        payload = {
            "start": bounds["from"],
            "end": bounds["to"],
            "truncated": result.truncated,
            "results": entries,
        }
        return Response(CalendarSerializer(payload).data)


class FeedSerializer(serializers.ModelSerializer[Feed]):
    home = serializers.UUIDField(source="home_id", allow_null=True)
    url = serializers.CharField(
        read_only=True,
        required=False,
        help_text="The secret address. Only in the answer that creates or regenerates a feed.",
    )

    class Meta:
        model = Feed
        fields = ["id", "home", "created_at", "regenerated_at", "url"]
        read_only_fields = fields


class FeedCreateSerializer(serializers.Serializer[Any]):
    home = serializers.UUIDField(
        allow_null=True, required=False, help_text="A home, or empty for the combined feed."
    )


def _shown(request: Request, feed: Feed, raw: str | None = None) -> dict[str, Any]:
    data = dict(FeedSerializer(feed).data)
    data.pop("url", None)
    if raw is not None:
        data["url"] = external_url(
            reverse("calendar:feed", kwargs={"token": raw}), site_url(request)
        )
    return data


def _feed_of(request: Request, feed_id: Any) -> Feed:
    found: Feed = get_object_or_404(feeds.feeds_of(request.user), pk=feed_id)
    return found


class FeedListView(APIView):
    permission_classes = [CredentialFitsView, permissions.IsAuthenticated, SessionOnly]

    @extend_schema(responses=FeedSerializer(many=True))
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return Response([_shown(request, f) for f in feeds.feeds_of(request.user)])

    @extend_schema(request=FeedCreateSerializer, responses={201: FeedSerializer})
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = FeedCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wanted = serializer.validated_data.get("home")
        home = None
        if wanted is not None:
            home = get_object_or_404(feeds.feed_homes(request.user), pk=wanted)  # type: ignore[arg-type]
        try:
            feed, raw = feeds.create_feed(request.user, home)
        except DjangoValidationError as exc:
            raise exceptions.ValidationError({"detail": exc.messages}) from None
        return Response(_shown(request, feed, raw), status=status.HTTP_201_CREATED)


class FeedDetailView(APIView):
    permission_classes = [CredentialFitsView, permissions.IsAuthenticated, SessionOnly]

    @extend_schema(responses=FeedSerializer)
    def get(self, request: Request, feed_id: Any, *args: Any, **kwargs: Any) -> Response:
        return Response(_shown(request, _feed_of(request, feed_id)))

    @extend_schema(responses={204: None})
    def delete(self, request: Request, feed_id: Any, *args: Any, **kwargs: Any) -> Response:
        feeds.revoke_feed(request.user, _feed_of(request, feed_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class FeedRegenerateView(APIView):
    """Give a feed a new address; the old one stops working at once."""

    permission_classes = [CredentialFitsView, permissions.IsAuthenticated, SessionOnly]

    @extend_schema(request=None, responses=FeedSerializer)
    def post(self, request: Request, feed_id: Any, *args: Any, **kwargs: Any) -> Response:
        feed, raw = feeds.regenerate_feed(request.user, _feed_of(request, feed_id))
        return Response(_shown(request, feed, raw))

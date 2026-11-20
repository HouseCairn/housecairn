"""API v1 of QR labels. Validation only; rules live in `services`."""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.http.response import HttpResponseBase
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field
from rest_framework import exceptions, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from housecairn.homes import services as home_services
from housecairn.homes import sso
from housecairn.homes.scoping import homes_for_request, site_url
from housecairn.labels import printing, services
from housecairn.labels.models import Label, LabelKind
from housecairn.labels.views import pdf_response
from housecairn.rooms.api import BinaryRenderer, HomeResourceMixin, _validation


class LabelSerializer(serializers.ModelSerializer[Label]):
    target = serializers.SerializerMethodField(
        help_text="Identifier of the room, item or stock item."
    )
    name = serializers.CharField(read_only=True, help_text="Name of the object, as printed.")
    place = serializers.CharField(
        read_only=True,
        help_text="Where it is, as printed: the room of an item, the place of a stock item.",
    )
    url = serializers.SerializerMethodField(
        help_text="The address the QR code holds, built from the public address of the instance."
    )

    class Meta:
        model = Label
        fields = ["id", "kind", "target", "name", "place", "code", "url", "created_at"]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.UUID)
    def get_target(self, label: Label) -> str:
        return str(label.target_id)

    @extend_schema_field(OpenApiTypes.URI)
    def get_url(self, label: Label) -> str:
        return services.address(site_url(self.context["request"]), label)


class LabelCreateSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=LabelKind.choices)
    target = serializers.UUIDField(help_text="Identifier of the room, item or stock item.")


class LabelPrintSerializer(serializers.Serializer[Any]):
    labels = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        max_length=printing.MAX_LABELS,
        help_text="Identifiers of the labels to print, in the order they come out.",
    )
    layout = serializers.ChoiceField(
        choices=[(code, str(layout.title)) for code, layout in printing.LAYOUTS.items()],
        default=printing.DEFAULT_LAYOUT,
    )


class LabelViewSet(
    HomeResourceMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet[Label],
):
    """The QR labels of a home. Every member lists, reads and prints; making, revoking and
    regenerating need a role that writes. The `code` is what the sticker carries, so it is
    shown to the members of the home only."""

    serializer_class = LabelSerializer
    queryset = Label.objects.none()
    lookup_field = "id"
    lookup_url_kwarg = "label_id"
    http_method_names = ["get", "post", "delete", "head", "options"]
    # Printing the labels that exist reads the home: still open in an archived one
    # (docs/adr/0089).
    archived_home_writes = frozenset({"print_labels"})

    def get_queryset(self) -> models.QuerySet[Label]:
        labels = services.labels_of(self.get_home())
        kind = self.request.query_params.get("kind")
        return labels.filter(kind=kind) if kind else labels

    @extend_schema(parameters=[OpenApiParameter("kind", str, enum=LabelKind.values)])
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request=LabelCreateSerializer,
        responses={200: LabelSerializer, 201: LabelSerializer},
        description="The active label of the object; 201 when it was just made, 200 when "
        "the object already had one.",
    )
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = LabelCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            label, created = services.create_label(
                request.user,
                self.get_home(),
                kind=serializer.validated_data["kind"],
                target_id=serializer.validated_data["target"],
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(
            self.get_serializer(label).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(description="Revoke the label: its sticker no longer opens anything.")
    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        services.revoke_label(request.user, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=None, responses={201: LabelSerializer})
    @action(detail=True, methods=["post"], url_path="regenerate", url_name="regenerate")
    def regenerate(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Revoke the label and make another for the same object, with a new code."""
        label = services.regenerate_label(request.user, self.get_object())
        return Response(self.get_serializer(label).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=LabelPrintSerializer,
        responses={(200, "application/pdf"): OpenApiTypes.BINARY},
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="print",
        url_name="print",
        renderer_classes=[BinaryRenderer],
    )
    def print_labels(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """The PDF of the chosen labels, A4 sheet or 62 mm roll, the same as the screen's."""
        serializer = LabelPrintSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wanted = serializer.validated_data["labels"]
        found = {label.id: label for label in services.labels_of(self.get_home())}
        if any(label_id not in found for label_id in wanted):
            raise exceptions.NotFound
        try:
            data = services.render_pdf(
                site_url(request),
                [found[label_id] for label_id in wanted],
                serializer.validated_data["layout"],
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return pdf_response(data)


class LabelResolveSerializer(serializers.Serializer[Any]):
    home = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=LabelKind.choices)
    target = serializers.UUIDField()


class LabelResolveView(APIView):
    """Where a scanned code leads: the home, the kind and the identifier of the object.

    For a code that does not exist, one that was revoked and one of a home the caller is not
    in (or that the token does not cover), the answer is the same 404.
    """

    @extend_schema(
        operation_id="labels_resolve",
        parameters=[OpenApiParameter("code", str, OpenApiParameter.PATH)],
        responses={200: LabelResolveSerializer},
    )
    def get(self, request: Request, code: str) -> Response:
        label = services.resolve(code, homes_for_request(request))
        if label is None:
            raise exceptions.NotFound
        home_services.check_open(label.home, sso.signed_in_providers(request))
        return Response({"home": label.home_id, "kind": label.kind, "target": label.target_id})

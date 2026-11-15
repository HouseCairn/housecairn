"""API v1 of stock items, suppliers and movements. Validation only; rules live in `services`."""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.http.response import HttpResponseBase
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field
from rest_framework import exceptions, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.reverse import reverse

from housecairn.core.pagination import DefaultCursorPagination
from housecairn.homes.models import Home
from housecairn.rooms import conf
from housecairn.rooms.api import (
    BinaryRenderer,
    HomeResourceMixin,
    PayloadTooLarge,
    _uuid_param,
    _validation,
)
from housecairn.rooms.models import Item, Room
from housecairn.stock import services
from housecairn.stock.files import label_response
from housecairn.stock.models import Article, Movement, MovementKind, Supplier, Unit

QUERY = OpenApiParameter(
    "q", str, description="Only names, manufacturer references and suppliers containing this text."
)
ARTICLE_PARAM = OpenApiParameter(
    "article", OpenApiTypes.UUID, description="Only lines of this stock item."
)


def _check_size(request: Request) -> None:
    """Refuse an oversized body before reading any of it."""
    declared = int(request.META.get("CONTENT_LENGTH") or 0)
    if declared > conf.photo_max_bytes() + conf.MULTIPART_OVERHEAD:
        raise PayloadTooLarge


class StockPagination(DefaultCursorPagination):
    """Stock items by name, the way the list shows them."""

    ordering = ("name", "created_at", "id")  # type: ignore[assignment]


class MovementPagination(DefaultCursorPagination):
    """Movements, the latest first."""

    ordering = ("-occurred_on", "-created_at", "-id")  # type: ignore[assignment]


# ---------- stock items ----------


class ArticleSerializer(serializers.ModelSerializer[Article]):
    quantity = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
        help_text="The sum of the movements; change it with a movement.",
    )
    low = serializers.SerializerMethodField(
        help_text="True when the quantity is at or below the threshold."
    )
    unit = serializers.ChoiceField(
        choices=[("", "none"), *Unit.choices],
        required=False,
        help_text="A unit of the list, or `other` with `unit_other` saying which.",
    )
    unit_label = serializers.CharField(
        read_only=True, help_text="The unit as people read it, in the language of the request."
    )
    compatible_items: serializers.PrimaryKeyRelatedField[Item] = serializers.PrimaryKeyRelatedField(
        many=True,
        required=False,
        queryset=Item.objects.none(),
        help_text="Items of the home this stock item fits.",
    )
    rooms: serializers.PrimaryKeyRelatedField[Room] = serializers.PrimaryKeyRelatedField(
        many=True,
        required=False,
        queryset=Room.objects.none(),
        help_text="Rooms of the home where it is used, besides those of the items it fits.",
    )
    label_url = serializers.SerializerMethodField(
        help_text="Address of the photo of the label, or null."
    )
    label = serializers.FileField(  # type: ignore[assignment]
        write_only=True, required=False, help_text="A JPEG, PNG or WebP photo of the label."
    )
    clear_label = serializers.BooleanField(
        write_only=True, required=False, help_text="True removes the photo of the label."
    )

    class Meta:
        model = Article
        fields = [
            "id",
            "name",
            "manufacturer_reference",
            "ean",
            "dimensions",
            "storage_location",
            "unit",
            "unit_other",
            "unit_label",
            "threshold",
            "unit_price",
            "quantity",
            "low",
            "compatible_items",
            "rooms",
            "notes",
            "label",
            "clear_label",
            "label_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        home: Home | None = self.context.get("home")
        if home is not None:
            self.fields["compatible_items"].child_relation.queryset = Item.objects.filter(  # type: ignore[attr-defined]
                home=home
            )
            self.fields["rooms"].child_relation.queryset = Room.objects.filter(home=home)  # type: ignore[attr-defined]

    def get_low(self, article: Article) -> bool:
        return services.is_low(article, article.quantity)  # type: ignore[attr-defined]

    @extend_schema_field(OpenApiTypes.URI)
    def get_label_url(self, article: Article) -> str | None:
        if not article.label:
            return None
        return str(
            reverse(
                "api-v1:home-article-label",
                kwargs={"home_id": article.home_id, "article_id": article.id},
                request=self.context["request"],
            )
        )


class ArticleViewSet(
    HomeResourceMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet[Article],
):
    """Stock items of a home, with their quantity. The photo of the label is sent as
    `multipart/form-data` (`label`), checked by its content, written again without its
    metadata (location included) and limited in size."""

    serializer_class = ArticleSerializer
    queryset = Article.objects.none()
    pagination_class = StockPagination
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    lookup_field = "id"
    lookup_url_kwarg = "article_id"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self) -> models.QuerySet[Article]:
        item = _uuid_param(self.request, "item")
        room = _uuid_param(self.request, "room")
        raw_low = self.request.query_params.get("low", "").lower()
        articles = services.search_articles(
            self.get_home(),
            self.request.query_params.get("q", ""),
            low=raw_low in {"1", "true"},
            item=item,
            room=room,
        )
        if self.action == "list":
            # The related identifiers of a whole page in one query per relation, not per row.
            articles = articles.prefetch_related("compatible_items", "rooms")
        return articles

    @extend_schema(
        parameters=[
            QUERY,
            OpenApiParameter(
                "low", bool, description="True: only those at or below their threshold."
            ),
            OpenApiParameter(
                "item", OpenApiTypes.UUID, description="Only those compatible with this item."
            ),
            OpenApiParameter(
                "room",
                OpenApiTypes.UUID,
                description="Only those that concern this room: listed on it, fitting an item "
                "of it, or its paint left over.",
            ),
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().list(request, *args, **kwargs)

    def _fresh(self, article: Article) -> Article:
        return services.articles_of(self.get_home()).get(id=article.id)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        _check_size(request)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.pop("clear_label", None)
        try:
            article = services.create_article(
                request.user,
                self.get_home(),
                compatible_items=data.pop("compatible_items", []),
                rooms=data.pop("rooms", []),
                label=data.pop("label", None),
                **data,
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(
            self.get_serializer(self._fresh(article)).data, status=status.HTTP_201_CREATED
        )

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        _check_size(request)
        article = self.get_object()
        serializer = self.get_serializer(article, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        try:
            services.update_article(
                request.user,
                article,
                compatible_items=data.pop("compatible_items", None),
                rooms=data.pop("rooms", None),
                label=data.pop("label", None),
                clear_label=data.pop("clear_label", False),
                **data,
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(self._fresh(article)).data)

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        services.delete_article(request.user, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(responses={(200, "image/*"): OpenApiTypes.BINARY})
    @action(
        detail=True,
        methods=["get"],
        url_path="label",
        url_name="label",
        renderer_classes=[BinaryRenderer],
    )
    def label_photo(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """The photo of the label, as stored: JPEG, PNG or WebP, served inline and never sniffed."""
        return label_response(self.get_object())


# ---------- suppliers ----------


class SupplierSerializer(serializers.ModelSerializer[Supplier]):
    article = serializers.PrimaryKeyRelatedField(
        queryset=Article.objects.none(),
        help_text="The stock item; it cannot be changed once the supplier exists.",
    )

    class Meta:
        model = Supplier
        fields = [
            "id",
            "article",
            "name",
            "reference",
            "url",
            "last_price",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        home: Home | None = self.context.get("home")
        if home is not None:
            self.fields["article"].queryset = Article.objects.filter(home=home)  # type: ignore[attr-defined]


class SupplierViewSet(
    HomeResourceMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet[Supplier],
):
    """Suppliers of the stock items of a home. `last_price` is in the currency of the home."""

    serializer_class = SupplierSerializer
    queryset = Supplier.objects.none()
    lookup_field = "id"
    lookup_url_kwarg = "supplier_id"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self) -> models.QuerySet[Supplier]:
        suppliers = services.suppliers_of(self.get_home())
        article = _uuid_param(self.request, "article")
        return suppliers.filter(article_id=article) if article is not None else suppliers

    @extend_schema(parameters=[ARTICLE_PARAM])
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().list(request, *args, **kwargs)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        article = data.pop("article")
        try:
            supplier = services.create_supplier(request.user, article, **data)
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(supplier).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        supplier = self.get_object()
        serializer = self.get_serializer(supplier, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if data.pop("article", supplier.article) != supplier.article:
            raise exceptions.ValidationError({"article": ["A supplier cannot change stock item."]})
        try:
            supplier = services.update_supplier(request.user, supplier, **data)
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(supplier).data)

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        services.delete_supplier(request.user, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------- movements ----------


class MovementSerializer(serializers.ModelSerializer[Movement]):
    article = serializers.PrimaryKeyRelatedField(queryset=Article.objects.none())
    quantity = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0,
        help_text="The amount added or taken; for an inventory, the quantity counted.",
    )
    occurred_on = serializers.DateField(required=False, help_text="Defaults to today.")
    occurrence = serializers.UUIDField(
        source="occurrence_id",
        read_only=True,
        allow_null=True,
        help_text="The occurrence of a task whose closing, or taking back, wrote the line.",
    )

    class Meta:
        model = Movement
        fields = [
            "id",
            "article",
            "kind",
            "quantity",
            "delta",
            "occurred_on",
            "note",
            "occurrence",
            "created_at",
        ]
        read_only_fields = ["id", "delta", "created_at"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        home: Home | None = self.context.get("home")
        if home is not None:
            self.fields["article"].queryset = Article.objects.filter(home=home)  # type: ignore[attr-defined]


class MovementViewSet(
    HomeResourceMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet[Movement],
):
    """The stock ledger of a home. A line is never changed or deleted: correct a mistake with
    a new line, for example an inventory. `delta` is the effect of the line on the stock."""

    serializer_class = MovementSerializer
    queryset = Movement.objects.none()
    pagination_class = MovementPagination
    lookup_field = "id"
    lookup_url_kwarg = "movement_id"
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self) -> models.QuerySet[Movement]:
        movements = services.movements_of(self.get_home())
        article = _uuid_param(self.request, "article")
        if article is not None:
            movements = movements.filter(article_id=article)
        kind = self.request.query_params.get("kind")
        return movements.filter(kind=kind) if kind else movements

    @extend_schema(
        parameters=[ARTICLE_PARAM, OpenApiParameter("kind", str, enum=MovementKind.values)]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().list(request, *args, **kwargs)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        try:
            movement = services.record_movement(
                request.user,
                data["article"],
                kind=data["kind"],
                quantity=data["quantity"],
                occurred_on=data.get("occurred_on"),
                note=data.get("note", ""),
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(movement).data, status=status.HTTP_201_CREATED)

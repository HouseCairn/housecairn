"""API v1 of rooms, items and photos. Validation only; rules live in `services`."""

import json
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_field,
    inline_serializer,
)
from rest_framework import exceptions, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.renderers import BaseRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from housecairn.core.pagination import DefaultCursorPagination
from housecairn.homes.models import Home
from housecairn.homes.scoping import HomeScopedMixin
from housecairn.rooms import conf, services
from housecairn.rooms.files import photo_response
from housecairn.rooms.models import (
    Item,
    Photo,
    PhotoCategory,
    Room,
    validate_links,
)
from housecairn.stock.models import Article
from housecairn.tasks.models import Contractor

QUERY = OpenApiParameter("q", str, description="Only names containing this text.")


def _validation(exc: DjangoValidationError) -> serializers.ValidationError:
    if hasattr(exc, "error_dict"):
        return serializers.ValidationError(
            {field: [str(m) for m in messages] for field, messages in exc.message_dict.items()}
        )
    return serializers.ValidationError({"detail": exc.messages})


def _uuid_param(request: Request, name: str) -> UUID | None:
    raw = request.query_params.get(name)
    if raw is None:
        return None
    try:
        return UUID(raw)
    except ValueError:
        raise exceptions.ValidationError({name: ["Not a valid UUID."]}) from None


class PayloadTooLarge(exceptions.APIException):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_detail = "This file is too large."
    default_code = "too_large"


class RoomPagination(DefaultCursorPagination):
    """Rooms in the order of the home: by position, not by creation time."""

    ordering = ("position", "created_at", "id")  # type: ignore[assignment]


class BinaryRenderer(BaseRenderer):
    """Lets a view answer with the bytes of a file, whatever `Accept` asks for.

    The views return a file response of their own; only an error (a 404, a 403)
    reaches `render`, and is written as JSON like every other answer of the API.
    """

    media_type = "*/*"
    format = "binary"
    charset = None
    render_style = "binary"

    def render(
        self, data: Any, accepted_media_type: str | None = None, renderer_context: Any = None
    ) -> bytes:
        if renderer_context and (response := renderer_context.get("response")) is not None:
            response["Content-Type"] = "application/json"
        return json.dumps(data).encode()


class HomeResourceMixin(HomeScopedMixin):
    """API views of a home's resources: the home from the address, then its data."""

    def get_serializer_context(self) -> dict[str, Any]:
        context: dict[str, Any] = super().get_serializer_context()  # type: ignore[misc]
        if getattr(self, "swagger_fake_view", False):
            return context
        context["home"] = self.get_home()
        return context


# ---------- rooms ----------


class RoomSerializer(serializers.ModelSerializer[Room]):
    item_count = serializers.IntegerField(read_only=True)
    paint_article = serializers.PrimaryKeyRelatedField(
        queryset=Article.objects.none(),
        required=False,
        allow_null=True,
        help_text="The tin of paint left over: a stock item of the same home.",
    )
    paint_article_name = serializers.CharField(
        source="paint_article.name", read_only=True, default=None
    )

    class Meta:
        model = Room
        fields = [
            "id",
            "name",
            "kind",
            "position",
            "notes",
            "paint_colour",
            "paint_brand",
            "paint_reference",
            "paint_batch",
            "paint_article",
            "paint_article_name",
            "floor_covering",
            "wall_covering",
            "memory_notes",
            "item_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "position", "item_count", "created_at", "updated_at"]
        extra_kwargs = {
            "name": {
                "required": False,
                "allow_blank": True,
                "help_text": "Left empty, the name is the label of the type (not for `other`).",
            }
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        home = self.context.get("home")
        if home is not None:
            self.fields["paint_article"].queryset = Article.objects.filter(home=home)  # type: ignore[attr-defined]


class RoomViewSet(
    HomeResourceMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet[Room],
):
    """Rooms of a home, with their memory (paint, coverings, notes).

    Listed in their order; `POST .../rooms/reorder/` changes it. Deleting a room
    deletes its items and photos (they can be restored by the instance owner, not
    through the API).
    """

    serializer_class = RoomSerializer
    # For the schema only (types of the path parameters); get_queryset scopes.
    queryset = Room.objects.none()
    pagination_class = RoomPagination
    lookup_field = "id"
    lookup_url_kwarg = "room_id"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self) -> models.QuerySet[Room]:
        rooms = services.rooms_of(self.get_home())
        query = self.request.query_params.get("q", "").strip()
        return rooms.filter(name__icontains=query) if query else rooms

    @extend_schema(parameters=[QUERY])
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().list(request, *args, **kwargs)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            room = services.create_room(request.user, self.get_home(), **serializer.validated_data)
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        room = self.get_queryset().get(id=room.id)
        return Response(self.get_serializer(room).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        room = self.get_object()
        serializer = self.get_serializer(room, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            services.update_room(request.user, room, **serializer.validated_data)
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(self.get_queryset().get(id=room.id)).data)

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        services.delete_room(request.user, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        request=inline_serializer(
            "RoomOrder",
            {"order": serializers.ListField(child=serializers.UUIDField(), max_length=500)},
        ),
        responses={200: RoomSerializer(many=True)},
    )
    @action(detail=False, methods=["post"])
    def reorder(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Put the listed rooms first, in that order; the others keep their order after them."""
        home = self.get_home()
        raw = request.data.get("order") if isinstance(request.data, dict) else None
        field = serializers.ListField(child=serializers.UUIDField(), max_length=500)
        ids = field.run_validation(raw)
        try:
            services.reorder_rooms(request.user, home, ids)
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(self.get_queryset(), many=True).data)


# ---------- items ----------


class LinkSerializer(serializers.Serializer[Any]):
    url = serializers.URLField(max_length=500)
    label = serializers.CharField(  # type: ignore[assignment]
        max_length=100, required=False, allow_blank=True
    )


class ItemSerializer(serializers.ModelSerializer[Item]):
    room = serializers.PrimaryKeyRelatedField(
        queryset=Room.objects.none(),
        help_text="The room the item is in; change it to move the item.",
    )
    contract_contractor = serializers.PrimaryKeyRelatedField(
        queryset=Contractor.objects.none(),
        allow_null=True,
        required=False,
        help_text="The contractor of the home who services it (the provider of its "
        "maintenance contract), or null.",
    )
    links = LinkSerializer(many=True, required=False)

    class Meta:
        model = Item
        fields = [
            "id",
            "room",
            "name",
            "kind",
            "brand",
            "model",
            "serial_number",
            "ean",
            "purchased_on",
            "installed_on",
            "warranty_duration",
            "warranty_duration_unit",
            "warranty_provider",
            "warranty_provider_name",
            "warranty_starts_on",
            "warranty_ends_on",
            "contract_contractor",
            "contract_number",
            "contract_url",
            "links",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        home: Home | None = self.context.get("home")
        if home is not None:
            self.fields["room"].queryset = Room.objects.filter(home=home)  # type: ignore[attr-defined]
            self.fields["contract_contractor"].queryset = Contractor.objects.filter(  # type: ignore[attr-defined]
                home=home
            )

    def validate_links(self, value: list[dict[str, str]]) -> list[dict[str, str]]:
        try:
            validate_links(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return value


class ItemViewSet(
    HomeResourceMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet[Item],
):
    """Items (equipment or surfaces) of a home. Patching `room` moves an item."""

    serializer_class = ItemSerializer
    queryset = Item.objects.none()
    lookup_field = "id"
    lookup_url_kwarg = "item_id"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self) -> models.QuerySet[Item]:
        items = services.items_of(self.get_home())
        room = _uuid_param(self.request, "room")
        if room is not None:
            items = items.filter(room_id=room)
        query = self.request.query_params.get("q", "").strip()
        return items.filter(name__icontains=query) if query else items

    @extend_schema(
        parameters=[
            QUERY,
            OpenApiParameter("room", OpenApiTypes.UUID, description="Only items of this room."),
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().list(request, *args, **kwargs)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        room = data.pop("room")
        try:
            item = services.create_item(request.user, room, **data)
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(item).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        item = self.get_object()
        serializer = self.get_serializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        room = data.pop("room", None)
        try:
            item = services.update_item(request.user, item, room=room, **data)
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(item).data)

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        services.delete_item(request.user, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------- photos ----------


class PhotoSerializer(serializers.ModelSerializer[Photo]):
    room: serializers.PrimaryKeyRelatedField[Room] = serializers.PrimaryKeyRelatedField(
        read_only=True, allow_null=True
    )
    item: serializers.PrimaryKeyRelatedField[Item] = serializers.PrimaryKeyRelatedField(
        read_only=True, allow_null=True
    )
    content_url = serializers.SerializerMethodField(help_text="Address of the image.")
    thumbnail_url = serializers.SerializerMethodField(help_text="Address of the thumbnail.")

    class Meta:
        model = Photo
        fields = [
            "id",
            "room",
            "item",
            "category",
            "caption",
            "content_type",
            "size_bytes",
            "width",
            "height",
            "content_url",
            "thumbnail_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "content_type",
            "size_bytes",
            "width",
            "height",
            "created_at",
            "updated_at",
        ]

    def _url(self, name: str, photo: Photo) -> str:
        request = self.context["request"]
        return str(
            reverse(name, kwargs={"home_id": photo.home_id, "photo_id": photo.id}, request=request)
        )

    @extend_schema_field(OpenApiTypes.URI)
    def get_content_url(self, photo: Photo) -> str:
        return self._url("api-v1:home-photo-content", photo)

    @extend_schema_field(OpenApiTypes.URI)
    def get_thumbnail_url(self, photo: Photo) -> str:
        return self._url("api-v1:home-photo-thumbnail", photo)


class PhotoUploadSerializer(serializers.Serializer[Any]):
    file = serializers.FileField(help_text="A JPEG, PNG or WebP image.")
    room = serializers.UUIDField(required=False, help_text="The room, or `item`, not both.")
    item = serializers.UUIDField(required=False, help_text="The item, or `room`, not both.")
    caption = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    category = serializers.ChoiceField(choices=PhotoCategory.choices, default=PhotoCategory.GENERAL)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if ("room" in attrs) == ("item" in attrs):
            raise serializers.ValidationError("Give either room or item.")
        return attrs


class PhotoViewSet(
    HomeResourceMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet[Photo],
):
    """Photos of rooms and items. Upload as `multipart/form-data`; the image is checked by
    its content, written again without its metadata (location included) and limited in size."""

    serializer_class = PhotoSerializer
    queryset = Photo.objects.none()
    # JSON for the caption and the category; the upload itself is multipart.
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    lookup_field = "id"
    lookup_url_kwarg = "photo_id"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self) -> models.QuerySet[Photo]:
        photos = services.photos_of(self.get_home())
        room = _uuid_param(self.request, "room")
        item = _uuid_param(self.request, "item")
        if room is not None:
            photos = photos.filter(room_id=room)
        if item is not None:
            photos = photos.filter(item_id=item)
        category = self.request.query_params.get("category")
        return photos.filter(category=category) if category else photos

    @extend_schema(
        parameters=[
            OpenApiParameter("room", OpenApiTypes.UUID, description="Only photos of this room."),
            OpenApiParameter("item", OpenApiTypes.UUID, description="Only photos of this item."),
            OpenApiParameter("category", str, enum=PhotoCategory.values),
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request={"multipart/form-data": PhotoUploadSerializer},
        responses={201: PhotoSerializer},
    )
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        home = self.get_home()
        # Refuse an oversized body before reading any of it.
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
        if declared > conf.photo_max_bytes() + conf.MULTIPART_OVERHEAD:
            raise PayloadTooLarge
        serializer = PhotoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        room: Room | None = None
        item: Item | None = None
        if "room" in data:
            room = get_object_or_404(Room.objects.filter(home=home), id=data["room"])
        else:
            item = get_object_or_404(Item.objects.filter(home=home), id=data["item"])
        try:
            photo = services.add_photo(
                request.user,
                upload=data["file"],
                room=room,
                item=item,
                caption=data["caption"],
                category=data["category"],
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(photo).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        photo = self.get_object()
        serializer = self.get_serializer(photo, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changes = {
            k: v for k, v in serializer.validated_data.items() if k in {"caption", "category"}
        }
        try:
            photo = services.update_photo(request.user, photo, **changes)
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(photo).data)

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        services.delete_photo(request.user, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(responses={(200, "image/*"): OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"], renderer_classes=[BinaryRenderer])
    def content(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """The image, as stored: JPEG, PNG or WebP, served inline and never sniffed."""
        return photo_response(self.get_object())

    @extend_schema(responses={(200, "image/jpeg"): OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"], renderer_classes=[BinaryRenderer])
    def thumbnail(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """A JPEG thumbnail of the photo."""
        return photo_response(self.get_object(), thumbnail=True)


# ---------- search ----------


class SearchView(HomeResourceMixin, APIView):
    """Rooms and items of a home whose name contains `q`, up to 50 of each."""

    @extend_schema(
        parameters=[OpenApiParameter("q", str, required=True)],
        responses=inline_serializer(
            "SearchResults",
            {
                "rooms": RoomSerializer(many=True),
                "items": ItemSerializer(many=True),
            },
        ),
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        home = self.get_home()
        result = services.search(home, request.query_params.get("q", ""))
        context = {"request": request, "home": home}
        return Response(
            {
                "rooms": RoomSerializer(result.rooms, many=True, context=context).data,
                "items": ItemSerializer(result.items, many=True, context=context).data,
            }
        )

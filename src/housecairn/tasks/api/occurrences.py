"""API v1 of occurrences (docs/adr/0004, 0031, 0033)."""

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.db.models.functions import Coalesce, TruncDate
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_field,
)
from rest_framework import exceptions, mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from housecairn.accounts.names import person_name
from housecairn.core.pagination import DefaultCursorPagination
from housecairn.rooms.api import (
    HomeResourceMixin,
    _uuid_param,
    _validation,
)
from housecairn.tasks import agenda, costs, materials, services
from housecairn.tasks.api.common import _FAR, DATE_PARAMS, _date_param
from housecairn.tasks.models import (
    CLOSED_STATUSES,
    OPEN_STATUSES,
    MaterialDraw,
    Occurrence,
    Status,
)


class OccurrencePagination(DefaultCursorPagination):
    """The oldest due date first, so that the arrears come first; no date last."""

    ordering = ("sort_due", "created_at", "id")  # type: ignore[assignment]

    def get_ordering(self, request: Request, queryset: Any, view: Any) -> tuple[str, ...]:
        """`?ordering=recent` is the History order: the latest closed first."""
        if request.query_params.get("ordering") == "recent":
            return ("-closed_on", "-created_at", "-id")
        return tuple(super().get_ordering(request, queryset, view))


class DrawSerializer(serializers.Serializer[Any]):
    """What an occurrence took from the stock for one stock item (docs/adr/0046)."""

    article = serializers.UUIDField()
    article_name = serializers.CharField()
    unit = serializers.CharField()
    wanted = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="The quantity the occurrence is held to have used.",
    )
    drawn = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="What the stock could give: never more than it held.",
    )
    missing = serializers.DecimalField(
        max_digits=10, decimal_places=2, help_text="`wanted` minus `drawn`: what the stock lacked."
    )
    unit_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        allow_null=True,
        help_text="The unit price of the stock item when it was taken, kept by the server; "
        "null when it had none (it then counts for nothing). Null in `short`.",
    )
    value = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="`drawn` times `unit_price`, rounded to the cent; 0 without a price.",
    )


class UsedSerializer(serializers.Serializer[Any]):
    article = serializers.UUIDField(help_text="A stock item the occurrence took.")
    quantity = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0"), help_text="Really used."
    )


def _draw_rows(draws: Iterable[MaterialDraw | materials.Shortage]) -> list[dict[str, Any]]:
    return [
        {
            "article": d.article.id,
            "article_name": d.article.name,
            "unit": d.article.unit_label,
            "wanted": d.wanted,
            "drawn": d.drawn,
            "missing": d.missing,
            "unit_price": d.unit_price if isinstance(d, MaterialDraw) else None,
            "value": (d.value if isinstance(d, MaterialDraw) else Decimal(0)).quantize(
                costs.CENT, rounding=ROUND_HALF_UP
            ),
        }
        for d in draws
    ]


class OccurrenceSerializer(serializers.ModelSerializer[Occurrence]):
    task_title = serializers.CharField(source="task.title", read_only=True)
    room = serializers.UUIDField(source="task.room_id", read_only=True, allow_null=True)
    item = serializers.UUIDField(source="task.item_id", read_only=True, allow_null=True)
    completed_by = serializers.UUIDField(source="completed_by_id", read_only=True, allow_null=True)
    completed_by_name = serializers.SerializerMethodField(
        help_text="The name shown for who closed it (display name, never the address)."
    )
    assignee_name = serializers.SerializerMethodField(
        help_text="The name shown for the assignee of the task."
    )
    is_overdue = serializers.SerializerMethodField()
    stock = serializers.SerializerMethodField(
        help_text="What a done occurrence took from the stock, per stock item."
    )
    stock_cost = serializers.SerializerMethodField(
        help_text="The value of the stock a done occurrence used: for each stock item taken, "
        "the quantity times its unit price when it was taken, added and rounded once to the "
        "cent (docs/adr/0090). 0.00 when nothing of value was taken. Read only."
    )
    real_cost = serializers.SerializerMethodField(
        help_text="The real cost: `stock_cost` plus `actual_cost`, the amount typed for the "
        "rest (labour, purchases outside the stock). Null when neither says anything. "
        "Read only; `actual_cost` stays the amount typed."
    )
    used = UsedSerializer(
        many=True,
        write_only=True,
        required=False,
        help_text="With `PATCH`: the quantity really used of the stock items taken; the stock "
        "follows by entries or exits.",
    )

    class Meta:
        model = Occurrence
        fields = [
            "id",
            "task",
            "task_title",
            "room",
            "item",
            "status",
            "due_on",
            "visible_from",
            "postponed_from",
            "completed_on",
            "completed_by",
            "completed_by_name",
            "assignee_name",
            "actual_cost",
            "stock_cost",
            "real_cost",
            "completion_note",
            "is_overdue",
            "stock",
            "used",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "task",
            "status",
            "due_on",
            "visible_from",
            "postponed_from",
            "created_at",
            "updated_at",
        ]

    def get_completed_by_name(self, occurrence: Occurrence) -> str:
        return person_name(occurrence.completed_by)

    def get_assignee_name(self, occurrence: Occurrence) -> str:
        return person_name(occurrence.task.assignee)

    def get_is_overdue(self, occurrence: Occurrence) -> bool:
        return occurrence.is_overdue(services.today())

    @extend_schema_field(DrawSerializer(many=True))
    def get_stock(self, occurrence: Occurrence) -> list[dict[str, Any]]:
        if occurrence.status != Status.DONE:
            return []
        prefetched = getattr(occurrence, "draw_lines", None)
        rows = _draw_rows(prefetched if prefetched is not None else materials.draws_of(occurrence))
        return list(DrawSerializer(rows, many=True).data)

    def _used(self, occurrence: Occurrence) -> costs.StockUsed:
        prefetched = getattr(occurrence, "all_draws", None)
        if prefetched is not None:
            return costs.from_draws(occurrence, prefetched)
        return costs.stock_used_by(occurrence)

    @extend_schema_field(serializers.DecimalField(max_digits=14, decimal_places=2))
    def get_stock_cost(self, occurrence: Occurrence) -> str:
        return f"{self._used(occurrence).amount:.2f}"

    @extend_schema_field(serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True))
    def get_real_cost(self, occurrence: Occurrence) -> str | None:
        total = costs.real_cost(occurrence.actual_cost, self._used(occurrence).amount)
        return None if total is None else f"{total:.2f}"


class CompleteSerializer(serializers.Serializer[Any]):
    completed_on = serializers.DateField(
        required=False, help_text="Defaults to today; cannot be in the future."
    )
    actual_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, min_value=0
    )
    note = serializers.CharField(max_length=4000, required=False, allow_blank=True, default="")


class PostponeSerializer(serializers.Serializer[Any]):
    due_on = serializers.DateField(help_text="The new date; not in the past.")


class CostSerializer(serializers.Serializer[Any]):
    total = serializers.DecimalField(max_digits=14, decimal_places=2)
    with_cost = serializers.IntegerField(
        help_text="Occurrences that have a real cost: an amount typed, or stock of value used."
    )
    count = serializers.IntegerField(help_text="Occurrences selected.")


class ClosingSerializer(serializers.Serializer[Any]):
    occurrence = OccurrenceSerializer()
    next = OccurrenceSerializer(allow_null=True, help_text="The occurrence created; null if none.")
    short = DrawSerializer(
        many=True,
        help_text="The stock items the stock could not give in full: the closing went through, "
        "the stock is at zero for them (docs/adr/0046).",
    )


class OccurrenceViewSet(
    HomeResourceMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet[Occurrence],
):
    """Each time a task has to be done, with its status.

    `complete` (a single call, for every task) closes an occurrence as done and creates
    the next when the task repeats; `undo` takes it back, the created occurrence included.
    `PATCH` completes what is known of a task that is done: date, real cost, note.
    """

    serializer_class = OccurrenceSerializer
    queryset = Occurrence.objects.none()
    pagination_class = OccurrencePagination
    lookup_field = "id"
    lookup_url_kwarg = "occurrence_id"
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self) -> models.QuerySet[Occurrence]:
        rows = (
            services.occurrences_of(self.get_home())
            .annotate(
                sort_due=Coalesce("due_on", models.Value(_FAR)),
                closed_on=Coalesce("completed_on", TruncDate("updated_at")),
            )
            .prefetch_related(
                models.Prefetch(
                    "draws",
                    queryset=MaterialDraw.objects.filter(
                        article__deleted_at__isnull=True
                    ).select_related("article"),
                    to_attr="draw_lines",
                ),
                # Every live draw, an article deleted since included: its value was spent.
                models.Prefetch("draws", queryset=MaterialDraw.objects.all(), to_attr="all_draws"),
            )
        )
        params = self.request.query_params
        for name, field in (
            ("task", "task_id"),
            ("room", "task__room_id"),
            ("item", "task__item_id"),
            ("assignee", "task__assignee_id"),
            ("contractor", "task__contractor_id"),
            ("completed_by", "completed_by_id"),
        ):
            value = _uuid_param(self.request, name)
            if value is not None:
                rows = rows.filter(**{field: value})
        match params.get("status"):
            case "open":
                rows = rows.filter(status__in=OPEN_STATUSES)
            case "closed":
                rows = rows.filter(status__in=[Status.DONE, Status.SKIPPED])
            case "planned" | "postponed" | "done" | "skipped" as one:
                rows = rows.filter(status=one)
            case None | "":
                pass
            case _:
                raise exceptions.ValidationError(
                    {"status": ["Use planned, postponed, done, skipped, open or closed."]}
                )
        if params.get("overdue") in ("1", "true"):
            rows = rows.filter(status__in=OPEN_STATUSES, due_on__lt=services.today())
        if (before := _date_param(self.request, "due_before")) is not None:
            rows = rows.filter(due_on__lte=before)
        if (after := _date_param(self.request, "due_after")) is not None:
            rows = rows.filter(due_on__gte=after)
        if (found := params.get("q", "").strip()[:200]) != "":
            rows = rows.filter(
                models.Q(task__title__icontains=found) | models.Q(completion_note__icontains=found)
            )
        if (closed_after := _date_param(self.request, "closed_after")) is not None:
            rows = rows.filter(closed_on__gte=closed_after, status__in=CLOSED_STATUSES)
        if (closed_before := _date_param(self.request, "closed_before")) is not None:
            rows = rows.filter(closed_on__lte=closed_before, status__in=CLOSED_STATUSES)
        if params.get("include_hidden") not in ("1", "true"):
            rows = rows.filter(
                ~models.Q(status__in=OPEN_STATUSES)
                | models.Q(visible_from__isnull=True)
                | models.Q(visible_from__lte=services.today())
            )
        return rows

    @extend_schema(
        parameters=[
            OpenApiParameter("task", OpenApiTypes.UUID),
            OpenApiParameter("room", OpenApiTypes.UUID),
            OpenApiParameter("item", OpenApiTypes.UUID),
            OpenApiParameter("assignee", OpenApiTypes.UUID),
            OpenApiParameter("contractor", OpenApiTypes.UUID),
            OpenApiParameter(
                "completed_by", OpenApiTypes.UUID, description="Who closed the occurrence."
            ),
            OpenApiParameter(
                "q", str, description="Words of the task title or of the note of the occurrence."
            ),
            OpenApiParameter(
                "closed_after", OpenApiTypes.DATE, description="Closed on or after this date."
            ),
            OpenApiParameter(
                "closed_before", OpenApiTypes.DATE, description="Closed on or before this date."
            ),
            OpenApiParameter(
                "ordering",
                str,
                enum=["recent"],
                description="`recent`: the latest closed first, as the History screen.",
            ),
            OpenApiParameter(
                "status",
                str,
                enum=["planned", "postponed", "done", "skipped", "open", "closed"],
            ),
            OpenApiParameter("overdue", bool, description="Open and past their date."),
            OpenApiParameter(
                "include_hidden",
                bool,
                description="Also the open occurrences hidden until a few days before their date.",
            ),
            *DATE_PARAMS,
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().list(request, *args, **kwargs)

    @extend_schema(
        parameters=[OpenApiParameter("status", str, enum=["done", "skipped", "closed"])],
        responses=CostSerializer,
        description=(
            "The real cost entered on the occurrences that the same filters as the list "
            "select: the History total. Add `status=closed` for what is done or skipped."
        ),
    )
    @action(detail=False, methods=["get"])
    def cost(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        total = agenda.cost_of(self.get_queryset())
        return Response(CostSerializer(total).data)

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        occurrence = self.get_object()
        serializer = self.get_serializer(occurrence, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        used = data.pop("used", None)
        try:
            services.update_occurrence(
                request.user,
                occurrence,
                used=None if used is None else {u["article"]: u["quantity"] for u in used},
                **data,
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        occurrence.draw_lines = list(materials.draws_of(occurrence))  # type: ignore[attr-defined]
        occurrence.all_draws = list(MaterialDraw.objects.filter(occurrence=occurrence))  # type: ignore[attr-defined]
        return Response(self.get_serializer(occurrence).data)

    def _closing(self, request: Request, closing: services.Closing) -> Response:
        context = self.get_serializer_context()
        return Response(
            {
                "occurrence": OccurrenceSerializer(closing.occurrence, context=context).data,
                "next": OccurrenceSerializer(closing.next, context=context).data
                if closing.next
                else None,
                "short": DrawSerializer(_draw_rows(closing.short), many=True).data,
            }
        )

    @extend_schema(request=CompleteSerializer, responses=ClosingSerializer)
    @action(detail=True, methods=["post"])
    def complete(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Mark done and create the next occurrence, in one call. `undo` takes it back."""
        occurrence = self.get_object()
        serializer = CompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            closing = services.complete(
                request.user,
                occurrence,
                on=data.get("completed_on"),
                actual_cost=data.get("actual_cost"),
                note=data["note"],
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return self._closing(request, closing)

    @extend_schema(request=None, responses=ClosingSerializer)
    @action(detail=True, methods=["post"])
    def skip(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Ignore this time: closed without being done; the next occurrence is created."""
        try:
            closing = services.skip(request.user, self.get_object())
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return self._closing(request, closing)

    @extend_schema(request=PostponeSerializer, responses=OccurrenceSerializer)
    @action(detail=True, methods=["post"])
    def postpone(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Move the open occurrence to another day (status `postponed`)."""
        occurrence = self.get_object()
        serializer = PostponeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            moved = services.postpone(request.user, occurrence, serializer.validated_data["due_on"])
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(moved).data)

    @extend_schema(request=None, responses=OccurrenceSerializer)
    @action(detail=True, methods=["post"])
    def undo(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Take back a `complete` or a `skip`: the occurrence is open again, the next one is gone.

        Refused (400) once the next occurrence has been changed.
        """
        try:
            reopened = services.undo(request.user, self.get_object())
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(reopened).data)

"""API v1 of tasks (docs/adr/0004, 0031)."""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_field,
)
from rest_framework import exceptions, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.reverse import reverse

from housecairn.accounts.models import User
from housecairn.accounts.names import person_name
from housecairn.core.pagination import DefaultCursorPagination
from housecairn.homes.models import Home
from housecairn.reports import services as report_services
from housecairn.rooms.api import (
    HomeResourceMixin,
    _uuid_param,
    _validation,
)
from housecairn.rooms.models import Item, Room
from housecairn.tasks import durations, recurrence, services
from housecairn.tasks.api.common import DATE_PARAMS, _date_param
from housecairn.tasks.api.materials import MaterialSummarySerializer
from housecairn.tasks.models import (
    OPEN_STATUSES,
    Contractor,
    Occurrence,
    Season,
    Task,
    TaskMaterial,
)


class LinkedReportSerializer(serializers.Serializer[Any]):
    """A report a task was made from (docs/adr/0047)."""

    id = serializers.UUIDField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True, help_text="When it was reported.")
    has_photo = serializers.BooleanField(read_only=True)
    photo_url = serializers.URLField(
        read_only=True,
        allow_null=True,
        help_text="Address of the photo, served by the report's protected endpoint, or null.",
    )


class TaskSerializer(serializers.ModelSerializer[Task]):
    room = serializers.PrimaryKeyRelatedField(
        queryset=Room.objects.none(), required=False, allow_null=True
    )
    item = serializers.PrimaryKeyRelatedField(
        queryset=Item.objects.none(), required=False, allow_null=True
    )
    assignee = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        required=False,
        allow_null=True,
        help_text="A member of the home.",
    )
    assignee_name = serializers.SerializerMethodField(
        help_text="The name shown for the assignee (display name, never the address)."
    )
    estimated_minutes = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=durations.MIN_MINUTES,
        max_value=durations.MAX_MINUTES,
        help_text="Estimated duration in minutes, from 1 to 525600 (one year). The screens "
        "take a number and a unit and store minutes.",
    )
    estimated_text = serializers.SerializerMethodField(
        help_text="The estimated duration as people read it, in the language of the request "
        '("1 h 30", "2 days").'
    )
    contractor = serializers.PrimaryKeyRelatedField(
        queryset=Contractor.objects.none(), required=False, allow_null=True
    )
    season = serializers.ChoiceField(
        choices=[("", "all year"), *Season.choices, (recurrence.HEATING, "heating (deprecated)")],
        required=False,
        help_text="`spring`, `summer`, `autumn` and `winter` follow the hemisphere of the home "
        "and set the two months below; `custom` takes the months given; empty is the whole "
        "year. Months given without a season make a custom one. `heating`, no longer offered, "
        "is still accepted and stored as `custom` with the months of the heating season "
        "(October to April in the north, April to October in the south).",
    )
    due_on = serializers.DateField(
        source="open_due_on",
        required=False,
        allow_null=True,
        help_text="Due date of the open occurrence. Writing it moves that occurrence.",
    )
    materials = MaterialSummarySerializer(
        many=True,
        read_only=True,
        source="material_lines",
        help_text="The stock items the task needs; change them in `task-materials`.",
    )
    open_occurrence = serializers.UUIDField(
        source="open_occurrence_id",
        read_only=True,
        allow_null=True,
        help_text="The occurrence to do now; null when the task is done for good.",
    )

    reports = serializers.SerializerMethodField(
        help_text="The reports the task was made from that the caller may see, the most "
        "recent first. Read-only; the photo is sent by the endpoint of the report."
    )

    class Meta:
        model = Task
        fields = [
            "id",
            "title",
            "reports",
            "room",
            "item",
            "priority",
            "due_on",
            "open_occurrence",
            "materials",
            "estimated_minutes",
            "estimated_text",
            "labour_budget",
            "material_budget",
            "notes",
            "procedure",
            "assignee",
            "assignee_name",
            "contractor",
            "repeat_mode",
            "repeat_unit",
            "repeat_interval",
            "season",
            "season_start_month",
            "season_end_month",
            "hide_days_before",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        home: Home | None = self.context.get("home")
        if home is not None:
            self.fields["room"].queryset = Room.objects.filter(home=home)  # type: ignore[attr-defined]
            self.fields["item"].queryset = Item.objects.filter(home=home)  # type: ignore[attr-defined]
            self.fields["assignee"].queryset = services.members_for_forms(home)  # type: ignore[attr-defined]
            self.fields["contractor"].queryset = Contractor.objects.filter(home=home)  # type: ignore[attr-defined]

    @extend_schema_field(LinkedReportSerializer(many=True))
    def get_reports(self, task: Task) -> list[dict[str, Any]]:
        found = getattr(task, "visible_reports", None)
        request = self.context["request"]
        if found is None:  # a task serialized without the list's prefetch
            found = list(report_services.visible_reports(request.user, task.home).filter(task=task))
        rows = [
            {
                "id": report.id,
                "created_at": report.created_at,
                "has_photo": report.has_photo,
                "photo_url": str(
                    reverse(
                        "api-v1:home-report-photo",
                        kwargs={"home_id": report.home_id, "report_id": report.id},
                        request=request,
                    )
                )
                if report.has_photo
                else None,
            }
            for report in found
        ]
        return list(LinkedReportSerializer(rows, many=True).data)

    def get_assignee_name(self, task: Task) -> str:
        return person_name(task.assignee)

    @extend_schema_field(OpenApiTypes.STR)
    def get_estimated_text(self, task: Task) -> str:
        return durations.label(task.estimated_minutes) if task.estimated_minutes else ""


class FromTemplatesSerializer(serializers.Serializer[Any]):
    templates = serializers.ListField(
        child=serializers.CharField(max_length=40),
        min_length=1,
        max_length=100,
        help_text="Identifiers of templates of the library.",
    )
    room = serializers.UUIDField(required=False, allow_null=True, help_text="A room of the home.")
    due_on = serializers.DateField(required=False, allow_null=True, help_text="The first date.")

    def validate_templates(self, value: list[str]) -> list[str]:
        from housecairn.tasks import task_templates

        found = task_templates.library()
        unknown = [t for t in dict.fromkeys(value) if found.get(t) is None]
        if unknown:
            raise serializers.ValidationError(
                "Unknown templates: " + ", ".join(unknown) + ". See GET /api/v1/task-templates/."
            )
        return value

    def validate_room(self, value: Any) -> Room | None:
        if value is None:
            return None
        room: Room | None = Room.objects.filter(home=self.context["home"], id=value).first()
        if room is None:
            raise serializers.ValidationError("This room is not in this home.")
        return room


class TaskViewSet(
    HomeResourceMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet[Task],
):
    """Tasks of a home. Only `title` is required.

    A task has one open occurrence at a time, which `due_on` shows and moves; closing
    it (`.../occurrences/<id>/complete/`) creates the next one when the task repeats.
    """

    serializer_class = TaskSerializer
    queryset = Task.objects.none()
    pagination_class = DefaultCursorPagination
    lookup_field = "id"
    lookup_url_kwarg = "task_id"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self) -> models.QuerySet[Task]:
        open_rows = Occurrence.objects.filter(task=models.OuterRef("pk"), status__in=OPEN_STATUSES)
        tasks = (
            services.tasks_of(self.get_home())
            .annotate(
                open_occurrence_id=models.Subquery(open_rows.values("id")[:1]),
                open_due_on=models.Subquery(open_rows.values("due_on")[:1]),
            )
            .prefetch_related(
                report_services.reports_of_task(self.get_home(), self.request.user),
                models.Prefetch(
                    "materials",
                    queryset=TaskMaterial.objects.filter(
                        article__deleted_at__isnull=True
                    ).select_related("article"),
                    to_attr="material_lines",
                ),
            )
        )
        params = self.request.query_params
        for name, field in (
            ("room", "room_id"),
            ("item", "item_id"),
            ("assignee", "assignee_id"),
            ("contractor", "contractor_id"),
        ):
            value = _uuid_param(self.request, name)
            if value is not None:
                tasks = tasks.filter(**{field: value})
        match params.get("status"):
            case "open":
                tasks = tasks.filter(open_occurrence_id__isnull=False)
            case "closed":
                tasks = tasks.filter(open_occurrence_id__isnull=True)
            case None | "":
                pass
            case _:
                raise exceptions.ValidationError({"status": ["Use open or closed."]})
        match params.get("due"):
            case "none":
                # Active (not deleted, with an open occurrence) and nothing planned: an open
                # occurrence without a date. A closed task has no open occurrence at all.
                tasks = tasks.filter(open_occurrence_id__isnull=False, open_due_on__isnull=True)
            case None | "":
                pass
            case _:
                raise exceptions.ValidationError({"due": ["Use none."]})
        if (before := _date_param(self.request, "due_before")) is not None:
            tasks = tasks.filter(open_due_on__lte=before)
        if (after := _date_param(self.request, "due_after")) is not None:
            tasks = tasks.filter(open_due_on__gte=after)
        query = params.get("q", "").strip()
        return tasks.filter(title__icontains=query) if query else tasks

    @extend_schema(
        parameters=[
            OpenApiParameter("q", str, description="Only titles containing this text."),
            OpenApiParameter("status", str, enum=["open", "closed"]),
            OpenApiParameter("room", OpenApiTypes.UUID),
            OpenApiParameter("item", OpenApiTypes.UUID),
            OpenApiParameter("assignee", OpenApiTypes.UUID),
            OpenApiParameter("contractor", OpenApiTypes.UUID),
            OpenApiParameter(
                "due",
                str,
                enum=["none"],
                description="`none`: only the active tasks with no due date, whose open "
                "occurrence has no date; closed tasks are left out.",
            ),
            *DATE_PARAMS,
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().list(request, *args, **kwargs)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        due_on = data.pop("open_due_on", None)
        try:
            task = services.create_task(request.user, self.get_home(), due_on=due_on, **data)
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(
            self.get_serializer(self.get_queryset().get(id=task.id)).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        task = self.get_object()
        serializer = self.get_serializer(task, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        extra = {"due_on": data.pop("open_due_on")} if "open_due_on" in data else {}
        try:
            services.update_task(request.user, task, **extra, **data)
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(self.get_queryset().get(id=task.id)).data)

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        services.delete_task(request.user, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        request=FromTemplatesSerializer,
        responses={201: TaskSerializer(many=True)},
        description=(
            "One task per template of the library (`GET /api/v1/task-templates/`), in the "
            "language of the request, repeating after the last time it is done, with the "
            "optional `room` and first date `due_on`. All or none: an unknown template is a "
            "400 and creates nothing. A role that writes, and a token with the `write` scope."
        ),
    )
    @action(detail=False, methods=["post"], url_path="from-templates")
    def from_templates(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        from housecairn.tasks import task_templates

        home = self.get_home()
        serializer = FromTemplatesSerializer(data=request.data, context={"home": home})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            tasks = task_templates.add_templates(
                request.user,
                home,
                data["templates"],
                room=data.get("room"),
                due_on=data.get("due_on"),
            )
        except ValueError:
            # Checked by FromTemplatesSerializer; never send the text of an exception.
            raise exceptions.ValidationError({"templates": ["Unknown template."]}) from None
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        rows = self.get_queryset().filter(id__in=[t.id for t in tasks]).order_by("created_at")
        return Response(self.get_serializer(rows, many=True).data, status=status.HTTP_201_CREATED)

"""API of the budget (docs/adr/0003, 0051): the figures of the screens, read only.

Amounts are strings with two decimals, never JSON numbers a client could round. Every
view takes `?year=` (this year by default) and `?export=csv` for the same figures as a file.
"""

from decimal import Decimal
from typing import Any

from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field
from rest_framework import exceptions, serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from housecairn.budget import csvfile, services
from housecairn.homes.scoping import open_homes_for_request
from housecairn.projects.api import MONEY_SCHEMA
from housecairn.projects.models import ENVELOPE_YEARS
from housecairn.rooms.api import HomeResourceMixin
from housecairn.tasks import services as task_services

YEAR = OpenApiParameter("year", int, description="Defaults to this year.")
EXPORT = OpenApiParameter(
    "export",
    str,
    enum=["csv"],
    description="`csv` answers with the same figures as a file (text/csv).",
)


@extend_schema_field(MONEY_SCHEMA)
class MoneyOut(serializers.Field[Decimal, str, str, Any]):
    """An amount worked out by the server: a string with two decimals."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs["read_only"] = True
        super().__init__(**kwargs)

    def to_representation(self, value: Decimal) -> str:
        return f"{value:.2f}"


@extend_schema_field({"type": "string", "enum": ["over", "under", "on"]})
class StateField(serializers.CharField):
    """Where a budget stands, in a word: over, under or on budget."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs["read_only"] = True
        super().__init__(**kwargs)


class AmountsSerializer(serializers.Serializer[Any]):
    """The amounts of a row. Gap: planned less engaged; negative, the budget is exceeded."""

    planned = MoneyOut()
    committed = MoneyOut()
    paid = MoneyOut()
    engaged = MoneyOut(help_text="Shown as Used: the more of committed and paid, line by line.")
    gap = MoneyOut()
    to_pay = MoneyOut(help_text="Committed and not paid yet, line by line.")
    budget_state = StateField(source="state")


class LineSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField(read_only=True, allow_null=True)
    name = serializers.CharField(source="label", read_only=True)
    amounts = AmountsSerializer(read_only=True)


class RoomLineSerializer(LineSerializer):
    projects = AmountsSerializer(source="extra.projects", read_only=True)
    upkeep = AmountsSerializer(source="extra.upkeep", read_only=True)


class ProjectLineSerializer(LineSerializer):
    counted_in = serializers.IntegerField(source="extra.year", read_only=True)
    status = serializers.CharField(source="extra.status", read_only=True)
    tasks_planned = MoneyOut(
        source="extra.tasks_planned",
        help_text="What the tasks of the project plan in all, each time done or open. "
        "Planned counts it beyond the budget lines of the same nature, never twice.",
    )


class TaskLineSerializer(LineSerializer):
    room = serializers.CharField(source="extra.room", read_only=True)
    estimate = MoneyOut(source="extra.estimate", allow_null=True, help_text="Each time.")
    annual_cost = MoneyOut(source="extra.annual_cost", allow_null=True)
    stock_estimate = MoneyOut(
        source="extra.stock_estimate",
        help_text="Each time: the part of the estimate that comes from the stock items "
        "of the task (quantity times unit price). A plan, not a payment.",
    )
    unpriced_materials = serializers.IntegerField(
        source="extra.unpriced_materials",
        read_only=True,
        help_text="Stock items of the task with no unit price: they count for nothing.",
    )
    total_paid = MoneyOut(source="extra.total_paid")
    missing_costs = serializers.IntegerField(
        source="extra.missing_costs", read_only=True, help_text="Done this year, no cost typed."
    )


class ReportSerializer(serializers.Serializer[Any]):
    year = serializers.IntegerField(read_only=True)
    currency = serializers.CharField(read_only=True)
    years = serializers.ListField(child=serializers.IntegerField(), read_only=True)
    total = AmountsSerializer(read_only=True)


class RoomReportSerializer(ReportSerializer):
    lines = RoomLineSerializer(many=True, read_only=True)


class ProjectReportSerializer(ReportSerializer):
    lines = ProjectLineSerializer(many=True, read_only=True)


class TaskReportSerializer(ReportSerializer):
    lines = TaskLineSerializer(many=True, read_only=True)
    unlisted = serializers.IntegerField(
        source="note.unlisted", read_only=True, help_text="Tasks with no estimate and no cost."
    )


class NatureLineSerializer(serializers.Serializer[Any]):
    nature = serializers.CharField(source="label", read_only=True)
    amounts = AmountsSerializer(read_only=True)


class YearLineSerializer(serializers.Serializer[Any]):
    year = serializers.IntegerField(read_only=True)
    envelope = MoneyOut(allow_null=True)
    projects = AmountsSerializer(read_only=True)
    upkeep = AmountsSerializer(read_only=True)
    total = AmountsSerializer(read_only=True)


class EnvelopeReportSerializer(serializers.Serializer[Any]):
    year = serializers.IntegerField(read_only=True)
    envelope = MoneyOut(allow_null=True, help_text="Null: none set for the year.")
    projects = AmountsSerializer(read_only=True)
    upkeep = AmountsSerializer(read_only=True)
    total = AmountsSerializer(read_only=True)
    left_to_plan = MoneyOut(allow_null=True, help_text="Envelope less planned.")
    left_to_spend = MoneyOut(allow_null=True, help_text="Envelope less engaged.")
    sure_budget = MoneyOut(
        allow_null=True, help_text="Reserved for a budget read from Sure; always null for now."
    )


class RollingSerializer(serializers.Serializer[Any]):
    since = serializers.DateField(read_only=True)
    until = serializers.DateField(read_only=True)
    upkeep = AmountsSerializer(read_only=True)
    missing = serializers.IntegerField(read_only=True, help_text="Done without a cost typed.")


class OverviewSerializer(serializers.Serializer[Any]):
    year = serializers.IntegerField(read_only=True)
    currency = serializers.CharField(read_only=True)
    years = serializers.ListField(child=serializers.IntegerField(), read_only=True)
    envelope = EnvelopeReportSerializer(read_only=True)
    by_nature = NatureLineSerializer(many=True, read_only=True)
    by_year = YearLineSerializer(many=True, read_only=True)
    rolling = RollingSerializer(read_only=True)


class CombinedHomeSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    envelope = EnvelopeReportSerializer(read_only=True)


class CombinedSerializer(serializers.Serializer[Any]):
    currency = serializers.CharField(read_only=True)
    envelope = MoneyOut(allow_null=True, help_text="Sum of the envelopes set, if any.")
    total = AmountsSerializer(read_only=True)
    homes = CombinedHomeSerializer(many=True, read_only=True)


def _year(request: Request, default: int) -> int:
    raw = request.query_params.get("year")
    if raw is None:
        return default
    try:
        year = int(raw)
    except ValueError:
        raise exceptions.ValidationError({"year": ["Not a valid year."]}) from None
    if not ENVELOPE_YEARS[0] <= year <= ENVELOPE_YEARS[1]:
        raise exceptions.ValidationError({"year": ["Not a valid year."]})
    return year


class BudgetApiView(HomeResourceMixin, APIView):
    """A budget view of one home: the home from the address, a 404 when it is not mine."""

    serializer: type[serializers.Serializer[Any]]
    kind = ""

    def table(self, ledger: services.Ledger, year: int) -> tuple[list[str], list[list[Any]]]:
        raise NotImplementedError

    def data(self, ledger: services.Ledger, year: int) -> dict[str, Any]:
        raise NotImplementedError

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response | HttpResponse:
        ledger = services.load(self.get_home())
        year = _year(request, ledger.today.year)
        if request.query_params.get("export") == "csv":
            head, rows = self.table(ledger, year)
            return csvfile.response(self.kind, head, rows, year=year)
        return Response(self.serializer(self.data(ledger, year)).data)


def _report(report: services.Report) -> dict[str, Any]:
    return {
        "year": report.year,
        "currency": report.currency,
        "years": report.years,
        "lines": report.lines,
        "total": report.total,
        "note": report.note,
    }


class RoomsView(BudgetApiView):
    """The budget by room: projects shared evenly between their rooms, and the upkeep."""

    serializer = RoomReportSerializer
    kind = "rooms"

    def table(self, ledger: services.Ledger, year: int) -> tuple[list[str], list[list[Any]]]:
        return csvfile.rooms(services.by_room(ledger, year))

    def data(self, ledger: services.Ledger, year: int) -> dict[str, Any]:
        return _report(services.by_room(ledger, year))

    @extend_schema(parameters=[YEAR, EXPORT], responses=RoomReportSerializer)
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response | HttpResponse:
        return super().get(request, *args, **kwargs)


class ProjectsView(BudgetApiView):
    """The budget of every project, each counted in the year it starts."""

    serializer = ProjectReportSerializer
    kind = "projects"

    def table(self, ledger: services.Ledger, year: int) -> tuple[list[str], list[list[Any]]]:
        return csvfile.projects(services.by_project(ledger))

    def data(self, ledger: services.Ledger, year: int) -> dict[str, Any]:
        return _report(services.by_project(ledger))

    @extend_schema(parameters=[EXPORT], responses=ProjectReportSerializer)
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response | HttpResponse:
        return super().get(request, *args, **kwargs)


class TasksView(BudgetApiView):
    """The upkeep, task by task: estimate, cost a year, the year and what was paid."""

    serializer = TaskReportSerializer
    kind = "tasks"

    def table(self, ledger: services.Ledger, year: int) -> tuple[list[str], list[list[Any]]]:
        return csvfile.tasks(services.by_task(ledger, year))

    def data(self, ledger: services.Ledger, year: int) -> dict[str, Any]:
        return _report(services.by_task(ledger, year))

    @extend_schema(parameters=[YEAR, EXPORT], responses=TaskReportSerializer)
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response | HttpResponse:
        return super().get(request, *args, **kwargs)


class OverviewView(BudgetApiView):
    """The envelope of the year, then the figures by nature, by year and over 12 months."""

    serializer = OverviewSerializer
    kind = "overview"

    def table(self, ledger: services.Ledger, year: int) -> tuple[list[str], list[list[Any]]]:
        return csvfile.overview(
            services.by_year(ledger), services.by_nature(ledger, year), services.rolling(ledger)
        )

    def data(self, ledger: services.Ledger, year: int) -> dict[str, Any]:
        return {
            "year": year,
            "currency": ledger.currency,
            "years": ledger.years(),
            "envelope": services.envelope(ledger, year),
            "by_nature": services.by_nature(ledger, year),
            "by_year": services.by_year(ledger),
            "rolling": services.rolling(ledger),
        }

    @extend_schema(parameters=[YEAR, EXPORT], responses=OverviewSerializer)
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response | HttpResponse:
        return super().get(request, *args, **kwargs)


class CombinedView(APIView):
    """Every home of the caller, added by currency. Currencies are never converted."""

    @extend_schema(parameters=[YEAR], responses=CombinedSerializer(many=True))
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        homes = list(open_homes_for_request(request).order_by("name"))
        year = _year(request, task_services.today().year)
        rows = [
            {
                "currency": currency,
                "envelope": entry["envelope"],
                "total": entry["total"],
                "homes": [
                    {"id": home.id, "name": home.name, "envelope": report}
                    for home, report in entry["homes"]
                ],
            }
            for currency, entry in services.combined(homes, year).items()
        ]
        return Response(CombinedSerializer(rows, many=True).data)

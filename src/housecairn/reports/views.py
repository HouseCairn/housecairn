"""Web views of reports. Rules live in `services`."""

import uuid
from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.translation import gettext as _
from django.utils.translation import pgettext
from django.views import View
from django.views.generic import FormView, TemplateView

from housecairn.core import navigation
from housecairn.guide.prefill import PrefillMixin
from housecairn.homes import services as home_services
from housecairn.reports import forms, services
from housecairn.reports.files import photo_response
from housecairn.reports.models import CLOSED_STATUSES, Report, ReportStatus
from housecairn.rooms import conf
from housecairn.rooms.models import Item, Room
from housecairn.rooms.views import HomeRoomsMixin
from housecairn.stock.models import Article

PAGE_SIZE = 30
SHOWN = ("open", "closed", "all", "mine")


class HomeReportsMixin(HomeRoomsMixin):
    @cached_property
    def sees_all(self) -> bool:
        """Every report, or only one's own: by the role, archived or not (docs/adr/0089)."""
        return services.sees_every_report(self.request.user, self.home)

    def page(self, **extra: Any) -> dict[str, Any]:
        return super().page(sees_all=self.sees_all, **extra)

    def get_report(self) -> Report:
        report: Report = get_object_or_404(
            services.visible_reports(self.request.user, self.home),
            id=self.kwargs["report_id"],
        )
        return report


class ReportListView(HomeReportsMixin, TemplateView):
    template_name = "reports/report_list.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        shown = self.request.GET.get("show", "")
        if shown not in SHOWN:
            shown = "open" if self.sees_all else "mine"
        rows = services.visible_reports(self.request.user, self.home)
        if shown == "open":
            rows = rows.filter(status=ReportStatus.OPEN)
        elif shown == "closed":
            rows = rows.filter(status__in=CLOSED_STATUSES)
        elif shown == "mine":
            rows = rows.filter(reporter_id=self.request.user.pk)
        try:
            number = int(self.request.GET.get("page", "1"))
        except ValueError:
            number = 1
        page = Paginator(rows, PAGE_SIZE).get_page(number)
        reporters = services.reporter_labels(page.object_list)
        for report in page.object_list:
            report.reporter_label = reporters[report.id]
        filters = (
            [
                ("open", pgettext("reports filter", "Open")),
                ("closed", pgettext("reports filter", "Closed")),
                ("all", pgettext("reports filter", "All")),
                ("mine", _("Mine")),
            ]
            if self.sees_all
            else []
        )
        return super().get_context_data(
            **self.page(page=page, shown=shown, filters=filters),
            **kwargs,
        )


class ReportCreateView(PrefillMixin, HomeReportsMixin, FormView[Any]):
    prefill_fields = ("title",)
    template_name = "reports/report_form.html"
    form_class = forms.ReportForm

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # The form of a report is not offered in an archived home (docs/adr/0089).
        home_services.check_not_archived(self.home)
        return super().get(request, *args, **kwargs)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Refuse an oversized body before reading any of it.
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
        if declared > conf.photo_max_bytes() + conf.MULTIPART_OVERHEAD:
            return HttpResponse(_("This file is too large."), status=413, content_type="text/plain")
        return super().post(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["places"] = services.places_for_forms(self.home)
        return kwargs

    def get_initial(self) -> dict[str, Any]:
        """`?room=`, `?item=` or `?article=` (from the page of an object) fills in "Where"."""
        for kind in ("room", "item", "article"):
            raw = self.request.GET.get(kind, "")
            if not raw:
                continue
            try:
                return {"place": f"{kind}:{uuid.UUID(raw)}"}
            except ValueError:
                return {}
        return {}

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(**self.page(max_bytes=conf.photo_max_bytes()), **kwargs)

    def form_valid(self, form: forms.ReportForm) -> HttpResponse:
        room, item, article = form.chosen_place()
        try:
            report = services.create_report(
                self.request.user,
                self.home,
                title=form.cleaned_data["title"],
                room=room,
                item=item,
                article=article,
                photo=form.cleaned_data.get("photo") or None,
            )
        except ValidationError as exc:
            if hasattr(exc, "error_dict"):
                for field, errors in exc.error_dict.items():
                    for error in errors:
                        form.add_error(field if field in form.fields else None, error)
            else:
                form.add_error(None, exc)
            return self.form_invalid(form)
        return redirect(
            navigation.after_create(
                self.request,
                reverse("reports:detail", kwargs={"home_id": self.home.id, "report_id": report.id}),
                "reports:list",
                added=_("Problem reported"),
                saved=_("Thank you: the problem is reported."),
            )
        )


class ReportDetailView(HomeReportsMixin, TemplateView):
    template_name = "reports/report_detail.html"

    def where_url(self, where: Any) -> str:
        """The page of the room, item or stock item the report is about."""
        if isinstance(where, Room):
            return reverse("rooms:detail", kwargs={"home_id": self.home.id, "room_id": where.id})
        if isinstance(where, Item):
            return reverse(
                "rooms:item-detail", kwargs={"home_id": self.home.id, "item_id": where.id}
            )
        if isinstance(where, Article):
            return reverse("stock:detail", kwargs={"home_id": self.home.id, "article_id": where.id})
        return ""

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        report = self.get_report()
        task = report.task if report.task and report.task.deleted_at is None else None
        return super().get_context_data(
            **self.page(
                report=report,
                task=task,
                reporter=services.reporter_name(report.reporter),
                mine=report.reporter_id == self.request.user.pk,
                where=report.where,
                where_url=self.where_url(report.where),
            ),
            **kwargs,
        )


class ReportPhotoView(HomeReportsMixin, View):
    thumbnail = False

    def get(self, request: HttpRequest, **kwargs: Any) -> HttpResponseBase:
        return photo_response(self.get_report(), thumbnail=self.thumbnail)


class ReportThumbnailView(ReportPhotoView):
    thumbnail = True


class ReportActionView(HomeReportsMixin, View):
    """Base of the buttons of a report: change it, then show it again."""

    def act(self, report: Report) -> str:
        raise NotImplementedError

    def post(self, request: HttpRequest, **kwargs: Any) -> HttpResponseBase:
        report = self.get_report()
        try:
            message = self.act(report)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, message)
            task = getattr(self, "task", None)
            if task is not None:
                return redirect("tasks:detail", home_id=self.home.id, task_id=task.id)
        # An action in place: the report again, still knowing where it was opened from.
        page = reverse("reports:detail", kwargs={"home_id": self.home.id, "report_id": report.id})
        return redirect(navigation.stay(request, page))


class ReportToTaskView(ReportActionView):
    task: Any = None

    def act(self, report: Report) -> str:
        self.task = services.make_task(self.request.user, report)
        return _("Task added from the report.")


class ReportCloseView(ReportActionView):
    def act(self, report: Report) -> str:
        services.close_report(
            self.request.user,
            report,
            resolution=self.request.POST.get("resolution", ""),
        )
        return _("Report closed.")


class ReportReopenView(ReportActionView):
    def act(self, report: Report) -> str:
        services.reopen_report(self.request.user, report)
        return _("Report reopened.")

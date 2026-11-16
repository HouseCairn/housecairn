"""Web views of projects, their budget lines, quotes, photos and tasks. Rules live in `services`."""

from functools import cached_property
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View
from django.views.generic import FormView, TemplateView

from housecairn.core import navigation
from housecairn.core.templatetags.tabs import PROJECT_VIEWS
from housecairn.homes import current
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.homes.scoping import WebHomeScopedMixin
from housecairn.projects import forms, services
from housecairn.projects import links as link_services
from housecairn.projects.files import file_response
from housecairn.projects.models import (
    STATUS_ORDER,
    BudgetLine,
    Project,
    ProjectFile,
    ProjectLink,
    ProjectStatus,
    ProjectTask,
    Quote,
    QuoteStatus,
)
from housecairn.rooms import conf
from housecairn.tasks import services as task_services
from housecairn.tasks.models import OPEN_STATUSES, Occurrence

MAX_COMPARED = 6


class EntryView(LoginRequiredMixin, TemplateView):
    """The Projects entry of the navigation: the projects of the current home.

    No home to open: the page that offers to add one (docs/developer/design-guide.md,
    "Current home").
    """

    template_name = "projects/choose_home.html"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        target = current.section_root(request, "projects:list")
        if target:
            return redirect(target)
        return self.render_to_response({"homes": []})


class HomeProjectsMixin(WebHomeScopedMixin):
    """A page of one home: the home from the address, and whether the user may write."""

    require_write = False
    request: HttpRequest

    def get_home(self, *, allow_locked: bool = False) -> Home:
        home = super().get_home(allow_locked=allow_locked)
        if self.require_write and not home_services.can_write(self.request.user, home):
            raise PermissionDenied
        return home

    @cached_property
    def home(self) -> Home:
        return self.get_home()

    @cached_property
    def can_write(self) -> bool:
        return home_services.can_write(self.request.user, self.home)

    def get_project(self) -> Project:
        project: Project = get_object_or_404(
            services.projects_of(self.home), id=self.kwargs["project_id"]
        )
        return project

    def get_line(self) -> BudgetLine:
        line: BudgetLine = get_object_or_404(
            services.lines_of(self.home).select_related("project"), id=self.kwargs["line_id"]
        )
        return line

    def get_quote(self) -> Quote:
        quote: Quote = get_object_or_404(
            services.quotes_of(self.home).select_related("project"), id=self.kwargs["quote_id"]
        )
        return quote

    def get_file(self) -> ProjectFile:
        stored: ProjectFile = get_object_or_404(
            services.files_of(self.home).select_related("project"), id=self.kwargs["file_id"]
        )
        return stored

    def get_photo(self) -> ProjectFile:
        """A photo or a file of a quote: not the picture of a link, which goes with its link."""
        stored: ProjectFile = get_object_or_404(
            services.photos_of(self.home).select_related("project"), id=self.kwargs["file_id"]
        )
        return stored

    def get_link(self) -> ProjectLink:
        link: ProjectLink = get_object_or_404(
            services.inspiration_links_of(self.home).select_related("project"),
            id=self.kwargs["link_id"],
        )
        return link

    def page(self, **extra: Any) -> dict[str, Any]:
        return {"home": self.home, "can_write": self.can_write, **extra}

    def project_url(self, project: Project) -> str:
        return reverse(
            "projects:detail", kwargs={"home_id": self.home.id, "project_id": project.id}
        )

    def back(self, project: Project) -> HttpResponse:
        """After a form about the project: back to `next`, else to the project."""
        return redirect(navigation.back_url(self.request, self.project_url(project)))

    def stay(self, project: Project) -> HttpResponse:
        """After an action in place: the project again, still knowing where it was opened from."""
        return redirect(navigation.stay(self.request, self.project_url(project)))

    def gone(self, parent: str, *ids: Any) -> HttpResponse:
        """After a deletion: `next` unless it is about what is gone, else `parent`."""
        return redirect(navigation.after_delete(self.request, parent, ids))


def _form_errors(form: Any, exc: ValidationError) -> None:
    if hasattr(exc, "error_dict"):
        for field, errors in exc.error_dict.items():
            for error in errors:
                form.add_error(field if field in form.fields else None, error)
    else:
        form.add_error(None, exc)


def _refuse_large(request: HttpRequest) -> HttpResponse | None:
    """Refuse an oversized body before reading any of it."""
    declared = int(request.META.get("CONTENT_LENGTH") or 0)
    if declared > conf.photo_max_bytes() + conf.MULTIPART_OVERHEAD:
        return HttpResponse(_("This file is too large."), status=413, content_type="text/plain")
    return None


# ---------- list, comparison, envelope ----------


class ProjectListView(HomeProjectsMixin, TemplateView):
    template_name = "projects/project_list.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        filters = forms.FilterForm(self.request.GET)
        chosen = filters.cleaned_data if filters.is_valid() else {}
        projects = list(
            services.search_projects(
                self.home,
                query=chosen.get("q", ""),
                status=chosen.get("status", ""),
                room=chosen.get("room"),
                priority=chosen.get("priority", ""),
                season=chosen.get("season", ""),
            ).distinct()
        )
        # The tab (docs/adr/0017): going on, ideas or finished; a status filter narrows it.
        shown = self.request.GET.get("view", "")
        shown = shown if shown in PROJECT_VIEWS else ""
        allowed = PROJECT_VIEWS[shown]
        projects = [p for p in projects if p.status in allowed]
        totals = services.amounts_by_project(projects)
        groups = []
        for status in STATUS_ORDER:
            rows = [(p, totals[p.id]) for p in projects if p.status == status]
            if rows:
                groups.append((ProjectStatus(status).label, rows))
        year = task_services.today().year
        # Compare is a mode of the list (design/patterns.md, platform adaptation 12): the rows
        # become boxes to tick, each row one target; the plain list has rows that open.
        compare = self.request.GET.get("compare") == "1" and len(projects) > 1
        keep = [
            (key, value) for key, value in self.request.GET.items() if key != "compare" and value
        ]
        return super().get_context_data(
            **self.page(
                groups=groups,
                total=len(projects),
                compare=compare,
                compare_url=f"{self.request.path}?{urlencode([*keep, ('compare', '1')])}",
                done_url=f"{self.request.path}?{urlencode(keep)}" if keep else self.request.path,
                filters_count=sum(1 for key in ("status", "room", "priority") if chosen.get(key)),
                filtered=any(chosen.values()),
                chosen=chosen,
                rooms=home_rooms(self.home),
                statuses=[(v, label) for v, label in ProjectStatus.choices if v in allowed],
                view=shown,
                envelope=services.envelope_summary(self.home, year),
                everything=not projects and not any(chosen.values()) and not shown,
            ),
            **kwargs,
        )


def home_rooms(home: Home) -> Any:
    from housecairn.rooms.models import Room

    return Room.objects.filter(home=home).order_by("position", "name")


class CompareView(HomeProjectsMixin, TemplateView):
    template_name = "projects/compare.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ids: list[UUID] = []
        for raw in self.request.GET.getlist("p")[:MAX_COMPARED]:
            try:
                ids.append(UUID(raw))
            except ValueError:
                continue
        rows = services.compare(self.home, ids)
        return super().get_context_data(**self.page(rows=rows), **kwargs)


class EnvelopeView(HomeProjectsMixin, FormView[forms.EnvelopeForm]):
    require_write = True
    template_name = "projects/envelope_form.html"
    form_class = forms.EnvelopeForm

    def get_initial(self) -> dict[str, Any]:
        year = task_services.today().year
        found = services.envelopes_of(self.home).filter(year=year).first()
        return {"year": year, "amount": found.amount if found else None}

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(**self.page(), **kwargs)

    def form_valid(self, form: forms.EnvelopeForm) -> HttpResponse:
        try:
            services.set_envelope(
                self.request.user,
                self.home,
                year=form.cleaned_data["year"],
                amount=form.cleaned_data["amount"],
            )
        except ValidationError as exc:
            _form_errors(form, exc)
            return self.form_invalid(form)
        messages.success(self.request, _("Envelope saved."))
        return redirect(
            navigation.back_url(
                self.request, reverse("projects:list", kwargs={"home_id": self.home.id})
            )
        )


# ---------- projects ----------


class ProjectFormMixin(HomeProjectsMixin, FormView[forms.ProjectForm]):
    require_write = True
    template_name = "projects/project_form.html"
    form_class = forms.ProjectForm

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["home"] = self.home
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(**self.page(), **kwargs)

    @staticmethod
    def values(form: forms.ProjectForm) -> dict[str, Any]:
        data = dict(form.cleaned_data)
        data.pop("rooms")
        return data


class ProjectCreateView(ProjectFormMixin):
    def form_valid(self, form: forms.ProjectForm) -> HttpResponse:
        data = self.values(form)
        budget = data.pop("budget", None)
        try:
            project = services.create_project(
                self.request.user,
                self.home,
                rooms=form.cleaned_data["rooms"],
                budget=budget,
                **data,
            )
        except ValidationError as exc:
            _form_errors(form, exc)
            return self.form_invalid(form)
        return redirect(
            navigation.after_create(
                self.request,
                self.project_url(project),
                "projects:list",
                added=_("Project added"),
                saved=_("Project added."),
            )
        )


class ProjectEditView(ProjectFormMixin):
    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_project()
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(project=self.get_project(), **kwargs)

    def form_valid(self, form: forms.ProjectForm) -> HttpResponse:
        project = self.get_project()
        try:
            services.update_project(
                self.request.user, project, rooms=form.cleaned_data["rooms"], **self.values(form)
            )
        except ValidationError as exc:
            _form_errors(form, exc)
            return self.form_invalid(form)
        messages.success(self.request, _("Project saved."))
        return self.back(project)


class ProjectDetailView(HomeProjectsMixin, TemplateView):
    template_name = "projects/project_detail.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        project = self.get_project()
        links = services.tasks_of(project)
        open_occurrences = {
            o.task_id: o
            for o in Occurrence.objects.filter(
                task__in=[link.task_id for link in links], status__in=OPEN_STATUSES
            )
        }
        lines = services.line_summaries(project)
        files = list(project.files.filter(link__isnull=True))
        quotes = list(services.quotes_of(self.home).filter(project=project))
        return super().get_context_data(
            **self.page(
                project=project,
                rooms=list(project.rooms.all()),
                tasks=[(link, open_occurrences.get(link.task_id)) for link in links],
                lines=lines,
                total=services.amounts_of(project),
                quotes=[(q, [f for f in files if f.quote_id == q.id]) for q in quotes],
                photos=[f for f in files if f.quote_id is None],
                has_lines=bool(lines),
                statuses=[(s.value, s.label) for s in QuoteStatus if s != QuoteStatus.ACCEPTED],
                task_form=kwargs.pop("task_form", None) or forms.TaskAddForm(home=self.home),
                attach_form=kwargs.pop("attach_form", None)
                or forms.TaskAttachForm(tasks=services.attachable_tasks(self.home)),
                photo_form=kwargs.pop("photo_form", None) or forms.PhotoForm(),
                max_bytes=conf.photo_max_bytes(),
                inspiration_links=[
                    link_services.expire_preview(link)
                    for link in services.inspiration_links_of(self.home).filter(project=project)
                ],
                polls_max=LINK_POLLS,
                link_form=kwargs.pop("link_form", None) or forms.LinkForm(auto_id="id_link_%s"),
                preview_enabled=link_services.preview_enabled(),
            ),
            **kwargs,
        )


class ConfirmDeleteView(HomeProjectsMixin, TemplateView):
    """A page that asks, then a POST that deletes: no script is needed to confirm."""

    require_write = True
    template_name = "projects/confirm_delete.html"
    message: Any = ""

    def target(self) -> Any:
        raise NotImplementedError  # pragma: no cover

    def name_of(self, target: Any) -> str:
        return str(target)

    def cancel_url(self, target: Any) -> str:
        raise NotImplementedError  # pragma: no cover

    def remove(self, target: Any) -> HttpResponse:
        raise NotImplementedError  # pragma: no cover

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        target = self.target()
        return super().get_context_data(
            **self.page(
                name=self.name_of(target),
                cancel_url=navigation.back_url(self.request, self.cancel_url(target)),
                message=self.message,
            ),
            **kwargs,
        )

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return self.remove(self.target())


class ProjectDeleteView(ConfirmDeleteView):
    message = gettext_lazy(
        "The project, its budget lines, quotes and photos disappear. Its tasks stay: they "
        "are only taken out of the project."
    )

    def target(self) -> Project:
        return self.get_project()

    def cancel_url(self, target: Project) -> str:
        return reverse("projects:detail", kwargs={"home_id": self.home.id, "project_id": target.id})

    def remove(self, target: Project) -> HttpResponse:
        ids = [target.id, *target.lines.values_list("id", flat=True)]
        ids += [*target.quotes.values_list("id", flat=True)]
        ids += [
            *target.files.values_list("id", flat=True),
            *target.links.values_list("id", flat=True),
        ]
        services.delete_project(self.request.user, target)
        messages.success(self.request, _("Project deleted."))
        return self.gone(reverse("projects:list", kwargs={"home_id": self.home.id}), *ids)


# ---------- tasks of a project ----------


class PostView(HomeProjectsMixin, View):
    require_write = True
    http_method_names = ["post"]

    def refuse(self, **bound: Any) -> HttpResponse:
        """The project page itself (422), with the refused form bound and its summary."""
        page = ProjectDetailView()
        page.setup(self.request, *self.args, **self.kwargs)
        return page.render_to_response(page.get_context_data(**bound), status=422)


class TaskAddView(PostView):
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        project = self.get_project()
        form = forms.TaskAddForm(request.POST, home=self.home)
        if not form.is_valid():
            return self.refuse(task_form=form)
        data = form.cleaned_data
        try:
            services.add_task(
                request.user,
                project,
                title=data["title"],
                due_on=data["due_on"],
                room=data["room"],
            )
        except ValidationError as exc:
            _form_errors(form, exc)
            return self.refuse(task_form=form)
        messages.success(request, _("Task added to the project."))
        return self.stay(project)


class TaskAttachView(PostView):
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        project = self.get_project()
        form = forms.TaskAttachForm(request.POST, tasks=services.attachable_tasks(self.home))
        if not form.is_valid():
            return self.refuse(attach_form=form)
        try:
            services.attach_task(request.user, project, form.cleaned_data["task"])
        except ValidationError as exc:
            _form_errors(form, exc)
            return self.refuse(attach_form=form)
        messages.success(request, _("Task put in the project."))
        return self.stay(project)


class TaskDetachView(PostView):
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        project = self.get_project()
        link = get_object_or_404(ProjectTask, project=project, task_id=self.kwargs["task_id"])
        services.detach_task(request.user, link)
        messages.success(request, _("Task taken out of the project."))
        # Taken out from the page of the task, one stays on that page.
        back = navigation.safe_next(request)
        task_page = reverse(
            "tasks:detail", kwargs={"home_id": project.home_id, "task_id": link.task_id}
        )
        if back and urlsplit(back).path == task_page:
            return redirect(back)
        return self.stay(project)


# ---------- budget lines ----------


class LineFormMixin(HomeProjectsMixin, FormView[forms.LineForm]):
    require_write = True
    template_name = "projects/line_form.html"
    form_class = forms.LineForm


class LineCreateView(LineFormMixin):
    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(**self.page(project=self.get_project()), **kwargs)

    def form_valid(self, form: forms.LineForm) -> HttpResponse:
        project = self.get_project()
        try:
            services.create_line(self.request.user, project, **form.cleaned_data)
        except ValidationError as exc:
            _form_errors(form, exc)
            return self.form_invalid(form)
        messages.success(self.request, _("Budget line added."))
        return self.back(project)


class LineEditView(LineFormMixin):
    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_line()
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        line = self.get_line()
        return super().get_context_data(**self.page(project=line.project, line=line), **kwargs)

    def form_valid(self, form: forms.LineForm) -> HttpResponse:
        line = self.get_line()
        try:
            services.update_line(self.request.user, line, **form.cleaned_data)
        except ValidationError as exc:
            _form_errors(form, exc)
            return self.form_invalid(form)
        messages.success(self.request, _("Budget line saved."))
        return self.back(line.project)


class LineDeleteView(ConfirmDeleteView):
    message = gettext_lazy(
        "The budget line disappears. The quotes accepted on it go back to Received: they "
        "commit nothing any more."
    )

    def target(self) -> BudgetLine:
        return self.get_line()

    def cancel_url(self, target: BudgetLine) -> str:
        return reverse(
            "projects:detail", kwargs={"home_id": self.home.id, "project_id": target.project_id}
        )

    def remove(self, target: BudgetLine) -> HttpResponse:
        ids = [target.id]
        services.delete_line(self.request.user, target)
        messages.success(self.request, _("Budget line deleted."))
        return self.gone(self.project_url(target.project), *ids)


# ---------- quotes ----------


class QuoteFormMixin(HomeProjectsMixin, FormView[forms.QuoteForm]):
    require_write = True
    template_name = "projects/quote_form.html"
    form_class = forms.QuoteForm

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return _refuse_large(request) or super().post(request, *args, **kwargs)

    def project_of_page(self) -> Project:
        raise NotImplementedError  # pragma: no cover

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project_of_page()
        kwargs["contractors"] = task_services.contractors_of(self.home)
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(
            **self.page(project=self.project_of_page(), max_bytes=conf.photo_max_bytes()),
            **kwargs,
        )

    def save(self, form: forms.QuoteForm, project: Project, quote: Quote | None) -> Quote:
        data = dict(form.cleaned_data)
        upload = data.pop("upload")
        name = data.pop("contractor_name")
        contractor = services.contractor_named(self.request.user, self.home, name)
        line = data.pop("budget_line")
        status = data.pop("status")
        if quote is None:
            saved = services.create_quote(
                self.request.user,
                project,
                contractor=contractor,
                status=status,
                budget_line=line,
                **data,
            )
        else:
            saved = services.update_quote(
                self.request.user,
                quote,
                contractor=contractor,
                status=status,
                budget_line=line,
                **data,
            )
        if upload:
            services.add_file(self.request.user, project, upload=upload, quote=saved)
        return saved

    def run(self, form: forms.QuoteForm, quote: Quote | None) -> HttpResponse:
        project = self.project_of_page()
        try:
            with transaction.atomic():
                self.save(form, project, quote)
        except ValidationError as exc:
            _form_errors(form, exc)
            return self.form_invalid(form)
        messages.success(self.request, _("Quote saved."))
        return self.back(project)


class QuoteCreateView(QuoteFormMixin):
    def project_of_page(self) -> Project:
        return self.get_project()

    def form_valid(self, form: forms.QuoteForm) -> HttpResponse:
        return self.run(form, None)


class QuoteEditView(QuoteFormMixin):
    def project_of_page(self) -> Project:
        return self.get_quote().project

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_quote()
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        quote = self.get_quote()
        return super().get_context_data(quote=quote, files=list(quote.files.all()), **kwargs)

    def form_valid(self, form: forms.QuoteForm) -> HttpResponse:
        return self.run(form, self.get_quote())


class QuoteAcceptView(PostView):
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        quote = self.get_quote()
        form = forms.AcceptForm(request.POST, project=quote.project)
        if not form.is_valid():
            messages.error(request, _("Choose the budget line the quote is committed on."))
            return self.stay(quote.project)
        try:
            services.accept_quote(request.user, quote, form.cleaned_data["budget_line"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Quote accepted and committed on the budget line."))
        return self.stay(quote.project)


class QuoteStatusView(PostView):
    """Received, rejected or requested again; accepting has its own button."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        quote = self.get_quote()
        form = forms.StatusForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Choose a status."))
            return self.stay(quote.project)
        services.update_quote(request.user, quote, status=form.cleaned_data["status"])
        messages.success(request, _("Quote saved."))
        return self.stay(quote.project)


class QuoteDeleteView(ConfirmDeleteView):
    message = gettext_lazy("The quote and its files disappear. What it committed is released.")

    def target(self) -> Quote:
        return self.get_quote()

    def name_of(self, target: Quote) -> str:
        return target.contractor.name if target.contractor else _("this quote")

    def cancel_url(self, target: Quote) -> str:
        return reverse(
            "projects:detail", kwargs={"home_id": self.home.id, "project_id": target.project_id}
        )

    def remove(self, target: Quote) -> HttpResponse:
        ids = [target.id]
        ids += [*target.files.values_list("id", flat=True)]
        services.delete_quote(self.request.user, target)
        messages.success(self.request, _("Quote deleted."))
        return self.gone(self.project_url(target.project), *ids)


# ---------- photos and files ----------


class FileAddView(PostView):
    """Add a photo to the project."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        project = self.get_project()
        refused = _refuse_large(request)
        if refused is not None:
            return refused
        form = forms.PhotoForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.refuse(photo_form=form)
        try:
            services.add_file(request.user, project, upload=form.cleaned_data["photo"])
        except ValidationError as exc:
            _form_errors(form, exc)
            return self.refuse(photo_form=form)
        messages.success(request, _("Photo added."))
        return self.stay(project)


class FileView(HomeProjectsMixin, View):
    """A photo or a file of a quote: only to the members of its home."""

    http_method_names = ["get", "head"]
    thumbnail = False

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        return file_response(self.get_file(), thumbnail=self.thumbnail)


class ThumbnailView(FileView):
    thumbnail = True


class FileDeleteView(ConfirmDeleteView):
    message = gettext_lazy("The file disappears from the project and from the disk.")

    def target(self) -> ProjectFile:
        return self.get_photo()

    def name_of(self, target: ProjectFile) -> str:
        return target.name

    def cancel_url(self, target: ProjectFile) -> str:
        return reverse(
            "projects:detail", kwargs={"home_id": self.home.id, "project_id": target.project_id}
        )

    def remove(self, target: ProjectFile) -> HttpResponse:
        ids = [target.id]
        services.delete_file(self.request.user, target)
        messages.success(self.request, _("File deleted."))
        return self.gone(self.project_url(target.project), *ids)


# ---------- links of inspiration ----------

# The row of a link being read asks for itself again every two seconds, at most this many
# times; then it says to reload the page (docs/adr/0070).
LINK_POLLS = 12


class LinkAddView(PostView):
    """Add a link; the worker reads its page, if the instance allows it (docs/adr/0070)."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        project = self.get_project()
        form = forms.LinkForm(request.POST, auto_id="id_link_%s")
        if not form.is_valid():
            return self.refuse(link_form=form)
        if not link_services.preview_allowed(request, project):
            form.add_error(None, _("Too many links added in a short time. Try again in a minute."))
            return self.refuse(link_form=form)
        try:
            link_services.add_link(
                request.user,
                project,
                url=form.cleaned_data["url"],
                title=form.cleaned_data["title"],
            )
        except ValidationError as exc:
            _form_errors(form, exc)
            return self.refuse(link_form=form)
        messages.success(request, _("Link added."))
        return self.stay(project)


class LinkStatusView(HomeProjectsMixin, TemplateView):
    """The row of a link as a fragment, that the page asks for again while its page is read."""

    template_name = "projects/_link_row.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        try:
            polls = max(0, min(int(self.request.GET.get("n", "0")), LINK_POLLS))
        except ValueError:
            polls = 0
        link = link_services.expire_preview(self.get_link())
        # The fragment is not a page one comes back to: deleting returns to the project.
        delete_url = navigation.with_next(
            reverse("projects:link-delete", kwargs={"home_id": self.home.id, "link_id": link.id}),
            self.project_url(link.project),
        )
        return super().get_context_data(
            **self.page(link=link, polls=polls, polls_max=LINK_POLLS, delete_url=delete_url),
            **kwargs,
        )

    def render_to_response(self, context: dict[str, Any], **response_kwargs: Any) -> HttpResponse:
        response = super().render_to_response(context, **response_kwargs)
        response["Cache-Control"] = "no-store"
        return response


class LinkDeleteView(ConfirmDeleteView):
    message = gettext_lazy("The link and its picture disappear from the project and from the disk.")

    def target(self) -> ProjectLink:
        return self.get_link()

    def name_of(self, target: ProjectLink) -> str:
        return target.title or target.url

    def cancel_url(self, target: ProjectLink) -> str:
        return reverse(
            "projects:detail", kwargs={"home_id": self.home.id, "project_id": target.project_id}
        )

    def remove(self, target: ProjectLink) -> HttpResponse:
        ids = [target.id]
        link_services.delete_link(self.request.user, target)
        messages.success(self.request, _("Link deleted."))
        return self.gone(self.project_url(target.project), *ids)

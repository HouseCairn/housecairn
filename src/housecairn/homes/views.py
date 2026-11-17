"""Web views of homes, households and invitations. Rules live in `services`."""

from collections.abc import Callable
from typing import Any

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import models, transaction
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import DetailView, FormView, ListView

from housecairn.accounts.models import User
from housecairn.accounts.names import person_name
from housecairn.accounts.services import email_is_configured
from housecairn.audit import services as audit_services
from housecairn.core import navigation
from housecairn.guide.links import GUIDE_FLAG
from housecairn.guide.prefill import PrefillMixin
from housecairn.homes import csv_export, csv_import, exports, forms, jobs, restore, services, sso
from housecairn.homes.models import ExportStatus, Home, Invitation, Membership
from housecairn.homes.scoping import (
    WebHomeScopedMixin,
    allow,
    delivery_for,
    homes_for_request,
    invitation_link,
)
from housecairn.instance.config import get_setting

# The tables a member downloads as CSV (homes.csv_export), as the home page names them.
CSV_LABELS: dict[str, Callable[[], str]] = {
    "rooms": lambda: _("Rooms"),
    "items": lambda: _("Items"),
    "tasks": lambda: _("Tasks"),
    "history": lambda: _("History of the tasks"),
    "stock": lambda: _("Stock"),
    "contractors": lambda: _("Contractors"),
    "reports": lambda: _("Reports"),
}

DELIVERY_FAILED = _lazy(
    "The invitation is saved, but the email could not be sent. "
    "Try Send again later, or ask the administrator to check the outgoing email settings."
)


class HomeListView(WebHomeScopedMixin, ListView[Home]):
    template_name = "homes/home_list.html"
    context_object_name = "homes"

    def get_queryset(self) -> models.QuerySet[Home]:
        # Every home of the user, those locked behind a sign-in provider too:
        # the list says which provider opens them (docs/adr/0021).
        return homes_for_request(self.request).order_by("name")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        providers = sso.signed_in_providers(self.request)
        rows = [
            {
                "home": home,
                "locked": not services.is_open(home, providers),
                "provider": sso.provider_named(home.required_provider, self.request),
            }
            for home in context["homes"]
        ]
        # Archived homes are listed apart, below the others (docs/adr/0079).
        context["rows"] = [row for row in rows if not row["home"].is_archived]
        context["archived_rows"] = [row for row in rows if row["home"].is_archived]
        # The example home of the guide is no home of the list of homes (docs/adr/0084): it
        # is shown after them, marked, from the one place that reads it.
        from housecairn.guide import services as guide_services

        context["example"] = guide_services.example_home_of(self.request.user)  # type: ignore[arg-type]
        return context


class HomeCreateView(PrefillMixin, LoginRequiredMixin, FormView[forms.NewHomeForm]):
    """A new home. Opened from the guide (`?guide=1`), the form makes the example home.

    The guide fills it in with the example (`guide.links.new_home`) and leaves "What the home
    has" unticked, as it always opens, so that no template task ticks a step of scenario 3
    ahead unless the person ticks one (docs/adr/0084). The form
    carries the flag in a hidden field; the guide's service sets the mark, and refuses a second
    example home.
    """

    template_name = "homes/home_form.html"
    form_class = forms.NewHomeForm
    prefill_fields = ("name", "currency", "hemisphere")

    def from_guide(self) -> bool:
        data = self.request.POST if self.request.method == "POST" else self.request.GET
        return data.get(GUIDE_FLAG) == "1"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(from_guide=self.from_guide(), **kwargs)

    def form_valid(self, form: forms.NewHomeForm) -> HttpResponse:
        from housecairn.tasks import task_templates

        if self.from_guide():
            return self.example_made(form)
        # The home and the tasks of the equipment checked, together or not at all.
        with transaction.atomic():
            home = services.create_home(
                self.request.user,  # type: ignore[arg-type]
                name=form.cleaned_data["name"],
                currency=form.cleaned_data["currency"],
                hemisphere=form.cleaned_data["hemisphere"],
            )
            task_templates.add_equipment(
                self.request.user, home, form.cleaned_data.get("equipment") or []
            )
        return redirect(
            navigation.after_create(
                self.request,
                reverse("homes:detail", kwargs={"home_id": home.id}),
                "homes:list",
                added=_("Home added"),
                saved=_("Home added."),
            )
        )

    def example_made(self, form: forms.NewHomeForm) -> HttpResponse:
        from housecairn.guide import services as guide_services
        from housecairn.tasks import task_templates

        try:
            with transaction.atomic():
                home = guide_services.create_example_home(
                    self.request.user,  # type: ignore[arg-type]
                    name=form.cleaned_data["name"],
                    currency=form.cleaned_data["currency"],
                    hemisphere=form.cleaned_data["hemisphere"],
                )
                task_templates.add_equipment(
                    self.request.user, home, form.cleaned_data.get("equipment") or []
                )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)
        return redirect(
            navigation.after_create(
                self.request,
                reverse("homes:detail", kwargs={"home_id": home.id}),
                "homes:list",
                added=_("Example home added"),
                saved=_("Example home added."),
            )
        )


class HomeDetailView(WebHomeScopedMixin, DetailView[Home]):
    template_name = "homes/home_detail.html"
    context_object_name = "home"

    def get_object(self, queryset: models.QuerySet[Home] | None = None) -> Home:
        return self.get_home()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["role"] = services.role_of(self.request.user, self.object)
        context["member_count"] = services.members(self.object).count()
        context["can_write"] = services.can_write(self.request.user, self.object)
        context["csv_tables"] = [(name, label()) for name, label in CSV_LABELS.items()]
        return context


class HomeEditView(WebHomeScopedMixin, FormView[forms.HomeForm]):
    """Name, currency and hemisphere of the home, for those who may write in it."""

    template_name = "homes/home_form.html"
    form_class = forms.HomeForm

    def get_form_kwargs(self) -> dict[str, Any]:
        home = self.get_home()
        if not services.can_write(self.request.user, home):
            raise PermissionDenied
        return {**super().get_form_kwargs(), "instance": home}

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(home=self.get_home(), editing=True, **kwargs)

    def form_valid(self, form: forms.HomeForm) -> HttpResponse:
        home = self.get_home()
        services.update_home(self.request.user, home, **form.cleaned_data)
        messages.success(self.request, _("Home saved."))
        return redirect(
            navigation.back_url(self.request, reverse("homes:detail", kwargs={"home_id": home.id}))
        )


class HouseholdView(WebHomeScopedMixin, DetailView[Home]):
    """Members of the home and, for owners, invitations and the sign-in rule."""

    template_name = "homes/household.html"
    context_object_name = "home"

    def get_object(self, queryset: models.QuerySet[Home] | None = None) -> Home:
        return self.get_home()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        home = self.object
        owner = services.is_owner(self.request.user, home)
        providers = sso.declared_providers(self.request)
        context.update(
            members=services.members(home),
            is_owner=owner,
            me=self.request.user,
            invitations=services.pending_invitations(home) if owner else [],
            invite_form=kwargs.get("invite_form") or forms.InviteForm(),
            providers=providers,
            provider_form=kwargs.get("provider_form")
            or forms.ProviderForm(
                providers=providers, initial={"provider": home.required_provider}
            ),
            required_provider=sso.provider_named(home.required_provider, self.request)
            if home.required_provider
            else None,
            email_configured=email_is_configured(),
            invitation_days=get_setting("invitation_lifetime_days"),
            shared_link=self.request.session.pop("housecairn_shared_invitation", None),
        )
        return context


class HomeAuditView(WebHomeScopedMixin, DetailView[Home]):
    """What happened in the home: roles, invitations, sign-in rule, export. Owners only."""

    template_name = "homes/audit.html"
    context_object_name = "home"
    paginate_by = 50

    def get_object(self, queryset: models.QuerySet[Home] | None = None) -> Home:
        return self.get_home()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        entries = audit_services.home_log(self.request.user, self.object.id)
        context["is_owner"] = True  # the log is for owners only: the tab bar shows its own tab
        page = Paginator(entries, self.paginate_by).get_page(self.request.GET.get("page"))
        page.object_list = audit_services.name_people(page.object_list)
        context["page"] = page
        return context


class HomeExportView(WebHomeScopedMixin, View):
    """Download everything of the home as one archive. Owners only."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        home = self.get_home()
        exports.check_owner(request.user, home)
        if jobs.is_large(home):
            # Too large for one request: the worker makes it, the page of the job hands it.
            job = jobs.start_export(request.user, home)
            return redirect(
                reverse("homes:export-job", kwargs={"home_id": home.id, "job_id": job.id})
            )
        archive, name = exports.export_home(request.user, home)
        return FileResponse(
            archive, as_attachment=True, filename=name, content_type="application/zip"
        )


class HomeExportJobView(WebHomeScopedMixin, View):
    """The archive made in the background: being made, ready to download, or gone."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        home = self.get_home()
        try:
            job = jobs.job_of(request.user, home, kwargs["job_id"])
        except jobs.ExportUnavailableError:
            raise Http404 from None
        return TemplateResponse(
            request,
            "homes/export_job.html",
            {"home": home, "job": job, "ready": job.status == ExportStatus.READY},
        )


class HomeExportDownloadView(WebHomeScopedMixin, View):
    """Hand the archive of a job: a POST, so that no preview or prefetch uses a download."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        home = self.get_home()
        try:
            archive, name = jobs.open_download(request.user, home, kwargs["job_id"])
        except jobs.ExportUnavailableError:
            raise Http404 from None
        return FileResponse(
            archive, as_attachment=True, filename=name, content_type="application/zip"
        )


class HomeCsvView(WebHomeScopedMixin, View):
    """One table of the home as a CSV file, for every member who may read it."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        home = self.get_home()
        entity = csv_export.ENTITIES.get(kwargs["entity"])
        if entity is None:
            raise Http404
        data, name = csv_export.build_csv(home, entity)
        response = HttpResponse(data, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response


class HomeImportView(WebHomeScopedMixin, FormView[forms.ImportForm]):
    """Import CSV tables into the home of the address: upload, preview, then confirm.

    The preview runs the import and rolls it back, and keeps the text it checked on the
    server (`csv_import.keep_preview`); the confirmation sends back only its signed token,
    runs the import again on that text and writes it in one transaction, all or nothing.
    Owners and members only.
    """

    template_name = "homes/import.html"
    form_class = forms.ImportForm
    # The upload of the files, as declared, beyond which nothing reads it
    # (`core.middleware.RequestTooLargeMiddleware`): the files and their multipart framing.
    max_request_bytes = csv_import.MAX_BYTES * 2

    @classmethod
    def request_too_large(cls, request: HttpRequest, **kwargs: Any) -> HttpResponse:
        """The screen and its reason, for a post too large to be read."""
        view = cls()
        view.setup(request, **kwargs)
        view.home = view.get_home()
        if not services.can_write(request.user, view.home):
            raise PermissionDenied
        form = forms.ImportForm(data={})
        form.is_valid()
        form.add_error(None, csv_import.too_large_message())
        response = view.render_to_response(view.get_context_data(form=form))
        response.status_code = 413
        return response

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        self.home = getattr(self, "home", None) or self.get_home()
        if not services.can_write(self.request.user, self.home):
            raise PermissionDenied
        context["home"] = self.home
        context["max_rows"] = csv_import.MAX_ROWS
        context["max_megabytes"] = csv_import.MAX_BYTES // (1024 * 1024)
        return context

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.home = self.get_home()
        if not services.can_write(request.user, self.home):
            raise PermissionDenied
        return super().get(request, *args, **kwargs)

    def form_valid(self, form: forms.ImportForm) -> HttpResponse:
        self.home = self.get_home()
        if self.request.POST.get("confirm") == "1":
            return self.confirm(form)
        try:
            texts = form.texts()
            if not texts:
                form.add_error(None, _("Choose at least one file."))
                return self.form_invalid(form)
            report = csv_import.run(self.request.user, self.home, texts, confirm=False)
        except csv_import.ImportRefusedError as exc:
            form.add_error(None, exc.messages)
            return self.form_invalid(form)
        preview = ""
        if report.can_confirm:
            preview = csv_import.keep_preview(self.request.user, self.home, texts)
        return self.render_to_response(
            self.get_context_data(form=form, report=report, preview=preview)
        )

    def confirm(self, form: forms.ImportForm) -> HttpResponse:
        """Write the text a preview of this person kept, in this home, then forget it."""
        try:
            preview, texts = csv_import.take_preview(
                self.request.user, self.home, form.cleaned_data["preview"]
            )
        except csv_import.PreviewInvalidError:
            form.add_error(None, csv_import.preview_invalid_message())
            return self.form_invalid(form)
        # Taken once: a second confirmation of the same preview finds nothing.
        deleted, _rows = type(preview).all_objects.filter(pk=preview.pk).delete()
        if not deleted:  # pragma: no cover - two confirmations at the same instant
            form.add_error(None, csv_import.preview_invalid_message())
            return self.form_invalid(form)
        report = csv_import.run(self.request.user, self.home, texts, confirm=True)
        if report.written:
            messages.success(
                self.request,
                ngettext("Imported: %(count)s row.", "Imported: %(count)s rows.", report.accepted)
                % {"count": report.accepted},
            )
            return redirect(
                navigation.back_url(
                    self.request, reverse("homes:detail", kwargs={"home_id": self.home.id})
                )
            )
        # The home changed since the preview: what is refused now is shown, nothing written.
        return self.render_to_response(self.get_context_data(form=form, report=report))


class HomeRestoreView(LoginRequiredMixin, FormView[forms.RestoreForm]):
    """Restore the archive of a home as a new home, owned by whoever restores it.

    The restored home does not become the current home: the person opens it from the list.
    """

    template_name = "homes/restore.html"
    form_class = forms.RestoreForm

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
        if declared > restore.max_unpacked_bytes():
            return HttpResponse(_("This file is too large."), status=413, content_type="text/plain")
        return super().post(request, *args, **kwargs)

    def form_valid(self, form: forms.RestoreForm) -> HttpResponse:
        as_copy = form.cleaned_data["mode"] == forms.RestoreForm.COPY
        try:
            result = restore.restore_home(
                self.request.user,
                form.cleaned_data["archive"],
                as_copy=as_copy,
                name=form.cleaned_data["name"] if as_copy else "",
            )
        except restore.CopyNotAllowedError as exc:
            form.add_error("mode", exc.message)
            response = self.form_invalid(form)
            response.status_code = 403
            return response
        except ValidationError as exc:
            form.add_error("archive", exc.messages)
            return self.form_invalid(form)
        if as_copy:
            message = _("Home restored as a copy: %(name)s. You are its only member.") % {
                "name": result.home.name
            }
        else:
            message = _("Home restored: %(name)s.") % {"name": result.home.name}
        if result.unattributed:
            message += " " + _(
                "Left unattributed, with no account here: %(names)s. Invite them again."
            ) % {"names": ", ".join(n or "?" for n in result.unattributed)}
        messages.success(self.request, message)
        return redirect("homes:list")


class HomeDeleteView(WebHomeScopedMixin, FormView[forms.DeleteHomeForm]):
    """Delete the home for good, after typing its name. Owners only."""

    template_name = "homes/delete.html"
    form_class = forms.DeleteHomeForm

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        home = self.get_home()
        if not services.is_owner(self.request.user, home):
            raise PermissionDenied
        context["home"] = home
        context["member_count"] = services.members(home).count()
        return context

    def form_valid(self, form: forms.DeleteHomeForm) -> HttpResponse:
        home = self.get_home()
        try:
            services.delete_home(
                self.request.user, home, confirmation=form.cleaned_data["confirmation"]
            )
        except ValidationError as exc:
            form.add_error("confirmation", exc)
            return self.form_invalid(form)
        messages.success(self.request, _("The home was deleted."))
        return redirect(navigation.after_delete(self.request, reverse("homes:list"), [home.id]))


class FormRefused(Exception):  # noqa: N818 - control flow, not an error
    """An action of the household refused its form: the page comes back with it, bound."""

    def __init__(self, **forms_by_name: Any) -> None:
        super().__init__()
        self.forms = forms_by_name


class HomeActionView(WebHomeScopedMixin, View):
    """Base of the household's POST actions: run, then back to the household."""

    http_method_names = ["post"]

    def locked_ok(self) -> bool:
        """Whether this action is allowed on a home whose sign-in requirement is not met."""
        return False

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        home = self.get_home(allow_locked=self.locked_ok())
        try:
            response = self.act(home)
        except FormRefused as refused:
            return self.refuse(home, **refused.forms)
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
            return self.household(home)
        except services.DeliveryFailedError:
            messages.warning(request, DELIVERY_FAILED)
            return self.household(home)
        return response or self.household(home)

    def refuse(self, home: Home, **bound: Any) -> HttpResponse:
        """The page of the form itself (422), with the refused form bound and its summary."""
        page = self.page_of_form(home)
        page.setup(self.request, *self.args, **self.kwargs)
        response: HttpResponse = page.render_to_response(page.get_context_data(**bound), status=422)
        return response

    def page_of_form(self, home: Home) -> Any:
        """The view that draws the form of this action; the household unless said otherwise."""
        page = HouseholdView()
        page.object = home
        return page

    def household(self, home: Home) -> HttpResponse:
        """The household again, still knowing where it was opened from (an action in place)."""
        page = reverse("homes:household", kwargs={"home_id": home.id})
        return redirect(navigation.stay(self.request, page))

    def act(self, home: Home) -> HttpResponse | None:  # pragma: no cover - abstract
        raise NotImplementedError

    def membership(self, home: Home) -> Membership:
        membership: Membership = get_object_or_404(
            Membership.objects.filter(home=home), id=self.kwargs["member_id"]
        )
        return membership

    def invitation(self, home: Home) -> Invitation:
        invitation: Invitation = get_object_or_404(
            Invitation.objects.filter(home=home), id=self.kwargs["invitation_id"]
        )
        return invitation


def _share_link(request: HttpRequest, raw: str | None) -> None:
    """Show a link once, on the next household page, when it was not emailed."""
    if raw is not None:
        request.session["housecairn_shared_invitation"] = invitation_link(request, raw)


class InviteView(HomeActionView):
    def act(self, home: Home) -> HttpResponse | None:
        if not services.is_owner(self.request.user, home):
            raise PermissionDenied
        form = forms.InviteForm(self.request.POST)
        if not form.is_valid():
            raise FormRefused(invite_form=form)
        if not allow(self.request, "housecairn_invite"):
            form.add_error(None, _("Too many invitations in a short time. Try again later."))
            raise FormRefused(invite_form=form)
        try:
            _invitation, raw = services.invite(
                self.request.user,
                home,
                email=form.cleaned_data["email"],
                role=form.cleaned_data["role"],
                delivery=delivery_for(self.request),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            raise FormRefused(invite_form=form) from exc
        _share_link(self.request, raw)
        messages.success(
            self.request,
            _("Invitation sent.") if raw is None else _("Invitation created: share its link."),
        )
        return None


class InvitationResendView(HomeActionView):
    def act(self, home: Home) -> HttpResponse | None:
        if not allow(self.request, "housecairn_invite"):
            raise ValidationError(_("Too many invitations in a short time. Try again later."))
        _invitation, raw = services.resend_invitation(
            self.request.user,
            self.invitation(home),
            delivery=delivery_for(self.request),
        )
        _share_link(self.request, raw)
        messages.success(
            self.request,
            _("Invitation sent again.") if raw is None else _("New link created: share it."),
        )
        return None


class InvitationRevokeView(HomeActionView):
    def act(self, home: Home) -> HttpResponse | None:
        services.revoke_invitation(self.request.user, self.invitation(home))
        messages.success(self.request, _("Invitation revoked."))
        return None


class MemberView(WebHomeScopedMixin, DetailView[Membership]):
    """One member: their role and, for owners, what can be changed."""

    template_name = "homes/member.html"
    context_object_name = "membership"

    def get_object(self, queryset: models.QuerySet[Membership] | None = None) -> Membership:
        home = self.get_home()
        membership: Membership = get_object_or_404(
            Membership.objects.filter(home=home).select_related("user", "home"),
            id=self.kwargs["member_id"],
        )
        return membership

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        membership = self.object
        context.update(
            home=membership.home,
            is_owner=services.is_owner(self.request.user, membership.home),
            is_me=membership.user_id == self.request.user.pk,
            role_form=kwargs.get("role_form") or forms.RoleForm(initial={"role": membership.role}),
        )
        return context


class MemberRoleView(HomeActionView):
    def page_of_form(self, home: Home) -> Any:
        page = MemberView()
        page.object = self.membership(home)
        return page

    def act(self, home: Home) -> HttpResponse | None:
        form = forms.RoleForm(self.request.POST)
        if not form.is_valid():
            raise FormRefused(role_form=form)
        services.change_role(self.request.user, self.membership(home), form.cleaned_data["role"])
        messages.success(self.request, _("Role changed."))
        return None


class ConfirmedActionView(HomeActionView):
    """A POST action that is first confirmed on a page of its own (GET)."""

    http_method_names = ["get", "post"]
    confirm_template = "homes/confirm.html"

    def confirmation(self, home: Home) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        home = self.get_home(allow_locked=self.locked_ok())
        return TemplateResponse(
            request, self.confirm_template, {"home": home, **self.confirmation(home)}
        ).render()


class MemberRemoveView(ConfirmedActionView):
    def _is_me(self) -> bool:
        return Membership.objects.filter(
            id=self.kwargs["member_id"], user=self.request.user
        ).exists()

    def locked_ok(self) -> bool:
        # Leaving never needs the provider: nobody is held in a home.
        return self._is_me()

    def confirmation(self, home: Home) -> dict[str, Any]:
        membership = self.membership(home)
        me = membership.user_id == self.request.user.pk
        return {
            "title": _("Leave this home?") if me else _("Remove this member?"),
            "text": _(
                "You will lose access to %(home)s. Someone has to invite you again to return."
            )
            % {"home": home.name}
            if me
            else _("%(who)s will lose access to %(home)s.")
            % {"who": person_name(membership.user), "home": home.name},
            "action": reverse("homes:member-remove", args=[home.id, membership.id]),
            "button": _("Leave") if me else _("Remove"),
            "back": reverse("homes:member", args=[home.id, membership.id]),
            "back_label": person_name(membership.user),
        }

    def act(self, home: Home) -> HttpResponse | None:
        membership = self.membership(home)
        services.remove_member(self.request.user, membership)
        if membership.user_id == self.request.user.pk:
            messages.success(self.request, _("You left %(home)s.") % {"home": home.name})
            return redirect("homes:list")
        messages.success(self.request, _("Member removed."))
        return None


class ArchiveView(ConfirmedActionView):
    """Archive the home, after a confirmation. Owners only (docs/adr/0079)."""

    def confirmation(self, home: Home) -> dict[str, Any]:
        if not services.is_owner(self.request.user, home):
            raise PermissionDenied
        return {
            "title": _("Archive this home?"),
            "text": _(
                "%(home)s will be listed apart, hidden from Today and from the home selector, "
                "send no reminder, and its calendar feeds will be empty. Nothing is deleted: "
                "you can open it, change it and bring it back whenever you want."
            )
            % {"home": home.name},
            "action": reverse("homes:archive", args=[home.id]),
            "button": _("Archive"),
            "button_class": "button-secondary",
            "back": reverse("homes:detail", args=[home.id]),
            "back_label": home.name,
        }

    def act(self, home: Home) -> HttpResponse | None:
        services.archive_home(self.request.user, home)
        messages.success(self.request, _("Home archived."))
        return redirect(
            navigation.back_url(self.request, reverse("homes:detail", kwargs={"home_id": home.id}))
        )


class UnarchiveView(ConfirmedActionView):
    """Bring an archived home back, after a confirmation. Owners only (docs/adr/0079)."""

    def confirmation(self, home: Home) -> dict[str, Any]:
        if not services.is_owner(self.request.user, home):
            raise PermissionDenied
        return {
            "title": _("Bring this home back?"),
            "text": _(
                "%(home)s will be listed again, shown on Today and in the home selector, and "
                "its reminders will resume."
            )
            % {"home": home.name},
            "action": reverse("homes:unarchive", args=[home.id]),
            "button": _("Bring back"),
            "button_class": "button-secondary",
            "back": reverse("homes:detail", args=[home.id]),
            "back_label": home.name,
        }

    def act(self, home: Home) -> HttpResponse | None:
        services.unarchive_home(self.request.user, home)
        messages.success(self.request, _("Home brought back."))
        return redirect(
            navigation.back_url(self.request, reverse("homes:detail", kwargs={"home_id": home.id}))
        )


class LeaveView(ConfirmedActionView):
    def locked_ok(self) -> bool:
        return True

    def confirmation(self, home: Home) -> dict[str, Any]:
        return {
            "title": _("Leave this home?"),
            "text": _(
                "You will lose access to %(home)s. Someone has to invite you again to return."
            )
            % {"home": home.name},
            "action": reverse("homes:leave", args=[home.id]),
            "button": _("Leave"),
            "back": reverse("homes:household", args=[home.id]),
        }

    def act(self, home: Home) -> HttpResponse | None:
        services.leave_home(self.request.user, home)
        messages.success(self.request, _("You left %(home)s.") % {"home": home.name})
        return redirect("homes:list")


class UnlockView(HomeActionView):
    """Stop requiring a sign-in provider the instance no longer offers."""

    def locked_ok(self) -> bool:
        return True

    def act(self, home: Home) -> HttpResponse | None:
        services.lift_unavailable_requirement(
            self.request.user,
            home,
            declared=[p.id for p in sso.declared_providers(self.request)],
        )
        messages.success(self.request, _("Sign-in requirement removed."))
        return redirect("homes:household", home_id=home.id)


class TransferView(HomeActionView):
    def act(self, home: Home) -> HttpResponse | None:
        services.transfer_ownership(
            self.request.user,
            home,
            self.membership(home),
        )
        messages.success(self.request, _("Ownership transferred. You are now a member."))
        return None


class RequiredProviderView(HomeActionView):
    def act(self, home: Home) -> HttpResponse | None:
        providers = sso.declared_providers(self.request)
        form = forms.ProviderForm(self.request.POST, providers=providers)
        if not form.is_valid():
            raise FormRefused(provider_form=form)
        services.set_required_provider(
            self.request.user,
            home,
            form.cleaned_data["provider"],
            declared=[p.id for p in providers],
            signed_in_with=sso.signed_in_providers(self.request),
        )
        messages.success(self.request, _("Sign-in requirement saved."))
        return None


class InvitationView(View):
    """The page an invitation link opens: join, or create an account to join.

    Every link that opens nothing (unknown, used, revoked, expired) gets the
    same page, and opening links is rate limited per client address.
    """

    http_method_names = ["get", "post"]

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if not allow(request, "housecairn_invitation"):
            return TemplateResponse(request, "429.html", status=429).render()
        invitation = services.find_invitation(kwargs["token"])
        if invitation is None:
            return TemplateResponse(request, "homes/invitation_invalid.html", status=404).render()
        self.invitation = invitation
        self.token: str = kwargs["token"]
        return super().dispatch(request, *args, **kwargs)

    def context(self, **extra: Any) -> dict[str, Any]:
        user = self.request.user
        same_address = user.is_authenticated and services.same_address(
            user.email, self.invitation.email
        )
        return {
            "invitation": self.invitation,
            "home": self.invitation.home,
            "same_address": same_address,
            # A link handed over by hand proves nothing: the account verifies the
            # address first (docs/adr/0020).
            "needs_verification": same_address
            and not self.invitation.delivered_by_email
            and not services.has_verified_address(user, self.invitation.email),  # type: ignore[arg-type]
            "email_configured": email_is_configured(),
            "account_exists": False,
            "providers": sso.declared_providers(self.request),
            "next_url": self.request.path,
            **extra,
        }

    def render(self, **extra: Any) -> HttpResponse:
        return TemplateResponse(
            self.request, "homes/invitation.html", self.context(**extra)
        ).render()

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.signup_form() if self.may_create_account() else None
        return self.render(form=form)

    def may_create_account(self) -> bool:
        """Only a link that went by email creates an account (docs/adr/0020)."""
        return not self.request.user.is_authenticated and self.invitation.delivered_by_email

    def signup_form(self, data: Any = None) -> forms.InvitationSignupForm:
        return forms.InvitationSignupForm(data, email=self.invitation.email)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if request.user.is_authenticated:
            return self.accept(request.user)
        if not self.may_create_account():
            # The same page as a visit: it says nothing about accounts.
            return self.render(form=None)
        form = self.signup_form(request.POST)
        if not form.is_valid():
            return self.render(form=form)
        try:
            user = services.create_account_for_invitation(
                self.token, password=form.cleaned_data["password1"]
            )
        except ValidationError:
            return self.render(form=None, account_exists=True)
        except PermissionDenied:
            return self.render(form=None)
        except Invitation.DoesNotExist as exc:
            raise Http404 from exc
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, _("Welcome to %(home)s.") % {"home": self.invitation.home.name})
        return HttpResponseRedirect(
            reverse("homes:detail", kwargs={"home_id": self.invitation.home_id})
        )

    def accept(self, user: User) -> HttpResponse:
        try:
            membership = services.accept_invitation(user, self.token)
        except PermissionDenied:
            return self.render()
        except Invitation.DoesNotExist as exc:
            raise Http404 from exc
        messages.success(self.request, _("You joined %(home)s.") % {"home": membership.home.name})
        return redirect("homes:detail", home_id=membership.home_id)


# ---------- current home ----------


# The root a section opens on in a home, for a page whose route needs more than a home.
_SECTION_ROOTS = {
    "today": "tasks:today",
    "rooms": "rooms:list",
    "projects": "projects:list",
    "budget": "budget:overview",
}


def _switched(request: HttpRequest, target: Home | None) -> str:
    """Where the selector leads: the same screen in the home chosen, else its section root.

    A page whose route needs only a home (a section root, a tab) opens again in the
    home chosen. A record of the previous home (a room, a task) does not exist in the
    next one: its section root opens there. A page without a home in its address (More,
    the calendar) opens again, without a `?home=` that would override the choice.
    "All homes" (`target` None) is the page of all homes, Today.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    from django.urls import Resolver404

    from housecairn.core.context_processors import section_of
    from housecairn.core.prefix import resolve_url

    if target is None:
        return reverse("index")
    origin = navigation.safe_next(request)
    if origin is None:
        return reverse("tasks:today", kwargs={"home_id": target.pk})
    parts = urlsplit(origin)
    try:
        match = resolve_url(origin)
    except Resolver404:
        return reverse("tasks:today", kwargs={"home_id": target.pk})
    if "home_id" not in match.kwargs:
        if match.view_name == "index":
            return reverse("tasks:today", kwargs={"home_id": target.pk})
        query = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("home", "room", "person")]
        return urlunsplit(parts._replace(query=urlencode(query)))
    if set(match.kwargs) == {"home_id"}:
        return reverse(match.view_name, kwargs={"home_id": target.pk})
    if match.view_name.startswith("stock:"):
        return reverse("stock:list", kwargs={"home_id": target.pk})
    root = _SECTION_ROOTS.get(section_of(match))
    if root is None:
        return reverse("more")
    return reverse(root, kwargs={"home_id": target.pk})


class CurrentHomeView(LoginRequiredMixin, View):
    """The selector of the app bar: a post that makes a home, or all of them, current.

    Only a home the person may open now is accepted; any other value answers 404, so the
    existence of a home is never revealed. "All homes" needs two homes at least.
    """

    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        from housecairn.homes import current

        found = current.for_request(request)
        raw = request.POST.get("home", "")
        if raw == "all" and found.has_selector:
            current.choose(request, None)
            return redirect(_switched(request, None))
        target = found.find(raw) if raw else None
        if target is None:
            raise Http404
        current.choose(request, target)
        return redirect(_switched(request, target))

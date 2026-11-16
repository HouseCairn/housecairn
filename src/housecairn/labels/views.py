"""Web views of QR labels. Rules live in `services`."""

import uuid
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import TemplateView

from housecairn.core import navigation
from housecairn.homes import services as home_services
from housecairn.homes import sso
from housecairn.homes.scoping import homes_for_request, site_url, sso_required_response
from housecairn.labels import printing, qr, services
from housecairn.labels.models import LabelKind
from housecairn.rooms.views import HomeRoomsMixin


def parse_selection(raw: list[str]) -> list[tuple[str, uuid.UUID]]:
    """`kind:uuid` strings as sent by the checkboxes; ValidationError on anything else."""
    selection: list[tuple[str, uuid.UUID]] = []
    for value in raw:
        kind, _sep, identifier = value.partition(":")
        try:
            selection.append((kind, uuid.UUID(identifier)))
        except ValueError:
            raise ValidationError(_("Choose the objects from the list.")) from None
    return selection


def pdf_response(data: bytes) -> HttpResponse:
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="housecairn-labels.pdf"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


class OpenView(LoginRequiredMixin, View):
    """What a scanned sticker opens: `/l/<code>/`.

    Signed out, the person signs in and comes back. Signed in, a member of the home goes to
    the page of the object; anyone else gets the same 404 as for a code that does not exist.
    """

    def get(self, request: HttpRequest, code: str) -> HttpResponseBase:
        label = services.resolve(code, homes_for_request(request))
        if label is None:
            raise Http404
        try:
            home_services.check_open(label.home, sso.signed_in_providers(request))
        except home_services.SsoRequiredError as exc:
            return sso_required_response(request, exc)
        return redirect(services.target_url(label))


class LabelsMixin(HomeRoomsMixin):
    """A page of one home's labels."""

    def get_home(self, *, allow_locked: bool = False) -> Any:
        home = super().get_home(allow_locked=allow_locked)
        if home.is_example:
            raise Http404  # the example home of the guide has no QR label (docs/adr/0084)
        return home

    def get_label(self) -> Any:
        return get_object_or_404(services.labels_of(self.home), id=self.kwargs["label_id"])

    def layouts(self) -> list[printing.Layout]:
        return list(printing.LAYOUTS.values())


class LabelListView(LabelsMixin, TemplateView):
    template_name = "labels/label_list.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        entries = services.entries_of(self.home)
        sections = [
            (kind.label, [e for e in entries if e.kind == kind.value]) for kind in LabelKind
        ]
        return super().get_context_data(
            **self.page(
                sections=[(title, rows) for title, rows in sections if rows],
                layouts=self.layouts(),
                default_layout=printing.DEFAULT_LAYOUT,
                max_labels=printing.MAX_LABELS,
            ),
            **kwargs,
        )


class LabelDetailView(LabelsMixin, TemplateView):
    template_name = "labels/label_detail.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        label = self.get_label()
        address = services.address(site_url(self.request), label)
        return super().get_context_data(
            **self.page(
                label=label,
                address=address,
                code_svg=qr.svg(address, title=_("QR code of %(name)s") % {"name": label.name}),
                layouts=self.layouts(),
                default_layout=printing.DEFAULT_LAYOUT,
                target_url=services.target_url(label),
            ),
            **kwargs,
        )


class LabelPrintView(LabelsMixin, View):
    """The PDF of the labels ticked on the list, or of the one label of its page."""

    def post(self, request: HttpRequest, **kwargs: Any) -> HttpResponseBase:
        try:
            selection = parse_selection(request.POST.getlist("target"))
            labels = services.labels_to_print(request.user, self.home, selection)
            data = services.render_pdf(
                site_url(request), labels, request.POST.get("layout", printing.DEFAULT_LAYOUT)
            )
        except PermissionDenied:
            messages.error(
                request,
                _("Only members who can edit make a new label: print the ones that exist."),
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            return pdf_response(data)
        return redirect("labels:list", home_id=self.home.id)


class LabelCreateView(LabelsMixin, View):
    """Make the label of an object, from its page."""

    def post(self, request: HttpRequest, **kwargs: Any) -> HttpResponseBase:
        try:
            selection = parse_selection([request.POST.get("target", "")])
            label, created = services.create_label(
                request.user, self.home, kind=selection[0][0], target_id=selection[0][1]
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("labels:list", home_id=self.home.id)
        if created:
            messages.success(request, _("QR label made."))
        # The label opens, and its back link leads to the page it was made from.
        page = reverse("labels:detail", kwargs={"home_id": self.home.id, "label_id": label.id})
        return redirect(navigation.stay(request, page))


class LabelRegenerateView(LabelsMixin, View):
    def post(self, request: HttpRequest, **kwargs: Any) -> HttpResponseBase:
        label = services.regenerate_label(request.user, self.get_label())
        messages.success(
            request, _("The old sticker no longer opens anything. Print the new label.")
        )
        page = reverse("labels:detail", kwargs={"home_id": self.home.id, "label_id": label.id})
        return redirect(navigation.stay(request, page))


class LabelRevokeView(LabelsMixin, View):
    def post(self, request: HttpRequest, **kwargs: Any) -> HttpResponseBase:
        label = self.get_label()
        services.revoke_label(request.user, label)
        messages.success(request, _("QR label revoked: the sticker no longer opens anything."))
        parent = reverse("labels:list", kwargs={"home_id": self.home.id})
        return redirect(navigation.after_delete(request, parent, [label.id]))

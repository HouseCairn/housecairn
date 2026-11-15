"""Web views of the barcode lookup: what a scanned code is, and its photo (docs/adr/0083).

The form of a stock item or an item asks, through HTMX, what a code is once it is typed or
scanned. The answer is a fragment that lists what was found and offers to fill the empty fields
of the form with it: nothing is written until the person saves the form, and a field already
filled is never changed. While the open databases are asked in the background, the fragment
asks again every two seconds, at most `POLLS` times (like the link preview, ADR 0070).
"""

from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.urls import reverse
from django.views.generic import TemplateView

from housecairn.accounts.services import is_instance_admin
from housecairn.barcodes import gtin, photos, services
from housecairn.barcodes.models import BarcodeLookup, LookupState
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.homes.scoping import WebHomeScopedMixin, allow

POLLS = 12
TARGETS = ("item", "article")


def fill_values(answer: services.Answer, target: str) -> dict[str, str]:
    """The fields of the form of `target` and what the answer puts in each, if it is empty."""
    category = answer.category
    if target == "article":
        name = answer.name
        if answer.brand and answer.brand.casefold() not in name.casefold():
            name = f"{answer.brand} {name}".strip()
        values = {
            "name": name[:120],
            "manufacturer_reference": answer.model[:120],
            "notes": category,
        }
    else:
        values = {
            "name": answer.name[:120],
            "brand": answer.brand[:120],
            "model": answer.model[:120],
            "notes": category,
        }
    return {key: value for key, value in values.items() if value}


class LookupView(WebHomeScopedMixin, TemplateView):
    """POST: look a code up (and queue the online lookup); GET: where that lookup stands."""

    template_name = "barcodes/_lookup.html"
    request: HttpRequest

    def writable_home(self) -> Home:
        home = self.get_home()
        if not home_services.can_write(self.request.user, home):
            raise PermissionDenied
        return home

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        home = self.writable_home()
        code = gtin.clean(request.POST.get("ean", ""))
        target = request.POST.get("target", "")
        if not code:
            return self.render(home, None, target, polls=0)
        if not gtin.is_valid(code):
            return self.render(home, None, target, polls=0, invalid=True)
        # Only a lookup that may leave the server counts, per person and per home (ADR 0050).
        if services.online_enabled() and not allow(
            request, "housecairn_barcode_lookup", key=str(home.id)
        ):
            return self.render(home, None, target, polls=0, limited=True)
        return self.render(home, services.start(code), target, polls=0)

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        home = self.writable_home()
        code = gtin.clean(request.GET.get("ean", ""))
        target = request.GET.get("target", "")
        try:
            polls = max(0, min(int(request.GET.get("n", "0")), POLLS))
        except ValueError:
            polls = 0
        if not gtin.is_valid(code):
            return self.render(home, None, target, polls=0, invalid=bool(code))
        return self.render(home, services.state(code), target, polls=polls)

    def render(
        self,
        home: Home,
        answer: services.Answer | None,
        target: str,
        *,
        polls: int,
        invalid: bool = False,
        limited: bool = False,
    ) -> HttpResponse:
        target = target if target in TARGETS else "item"
        admin = is_instance_admin(self.request.user)
        context = {
            "home": home,
            "answer": answer,
            "target": target,
            "fill": fill_values(answer, target) if answer is not None else {},
            "polls": polls,
            "next_poll": polls + 1,
            "polls_max": POLLS,
            "invalid": invalid,
            "limited": limited,
            "offer_online": (
                admin
                and answer is not None
                and answer.state == "off"
                and not services.online_enabled()
            ),
            "settings_url": reverse("instance:settings", kwargs={"section": "barcodes"}),
            "photo_field": photos.FIELD,
        }
        response = self.render_to_response(context)
        response["Cache-Control"] = "no-store"
        return response


class PhotoView(WebHomeScopedMixin, TemplateView):
    """The photo found for a code, shown in the fragment before the person keeps it."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.get_home()  # a member of the home only
        code = str(kwargs["code"])
        if not gtin.is_valid(code):
            raise Http404
        row = BarcodeLookup.objects.filter(
            code=gtin.as_gtin14(code), state=LookupState.DONE
        ).first()
        if row is None or not row.image:
            raise Http404
        data = bytes(row.image)
        response = HttpResponse(data, content_type=content_type(data))
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Disposition"] = 'inline; filename="barcode"'
        return response


def content_type(data: bytes) -> str:
    """The type of a photo re-encoded by `rooms.images.process`: JPEG, PNG or WebP."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"

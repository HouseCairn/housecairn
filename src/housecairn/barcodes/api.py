"""API of the barcode lookup, for the mobile application (docs/adr/0083).

The same rules as the web form: a member who may write the home asks, only the code is looked
up, in the open databases and in the background, when the instance allows it.
`POST .../barcodes/lookup/` starts a lookup; `GET .../barcodes/<code>/` says where it stands
(ask again every two seconds while `state` is `running`); `GET .../barcodes/<code>/photo/` is the
photo found, to show with its attribution. Nothing is written to the home: the application fills
its own form, the empty fields only, and saves the record as usual.
"""

from typing import Any

from django.http import Http404, HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import exceptions, serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from housecairn.barcodes import gtin, providers, services
from housecairn.barcodes.models import BarcodeLookup, LookupState
from housecairn.barcodes.views import content_type
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.homes.scoping import HomeScopedMixin, allow

ANSWER = inline_serializer(
    "BarcodeAnswer",
    {
        "code": serializers.CharField(),
        "state": serializers.ChoiceField(
            choices=["running", "done", "not_found", "failed", "off"],
            help_text="`off`: the online lookup is off and the code is not in the cache. "
            "`running`: ask again in two seconds.",
        ),
        "source": serializers.CharField(
            help_text="The provider that knew the code: "
            + ", ".join(p.name for p in providers.PROVIDERS)
            + "."
        ),
        "source_label": serializers.CharField(),
        "name": serializers.CharField(),
        "brand": serializers.CharField(),
        "model": serializers.CharField(),
        "category": serializers.CharField(),
        "product_url": serializers.CharField(),
        "author": serializers.CharField(help_text="Who added the product, when the source says."),
        "license": serializers.CharField(),
        "license_url": serializers.CharField(),
        "has_photo": serializers.BooleanField(),
        "online_enabled": serializers.BooleanField(),
    },
)


def _data(answer: services.Answer) -> dict[str, Any]:
    return {
        "code": answer.code,
        "state": answer.state,
        "source": answer.source,
        "source_label": answer.source_label,
        "name": answer.name,
        "brand": answer.brand,
        "model": answer.model,
        "category": answer.category,
        "product_url": answer.product_url,
        "author": answer.author,
        "license": answer.license,
        "license_url": answer.license_url,
        "has_photo": answer.has_photo,
        "online_enabled": services.online_enabled(),
    }


class _BarcodeMixin(HomeScopedMixin):
    def writable_home(self) -> Home:
        home = self.get_home()
        if not home_services.can_write(self.request.user, home):
            raise exceptions.PermissionDenied
        return home


def _code(raw: Any) -> str:
    code = gtin.clean(raw if isinstance(raw, str) else "")
    if not gtin.is_valid(code):
        raise exceptions.ValidationError({"ean": ["Not a valid EAN-8, UPC-A, EAN-13 or GTIN-14."]})
    return code


class LookupView(_BarcodeMixin, APIView):
    """Look a code up in the open databases, in the background."""

    @extend_schema(
        request=inline_serializer("BarcodeLookupRequest", {"ean": serializers.CharField()}),
        responses=ANSWER,
    )
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        home = self.writable_home()
        code = _code(request.data.get("ean"))
        if services.online_enabled() and not allow(
            request, "housecairn_barcode_lookup", key=str(home.id)
        ):
            raise exceptions.Throttled
        return Response(_data(services.start(code)))


class StateView(_BarcodeMixin, APIView):
    """Where the lookup of a code stands."""

    @extend_schema(responses=ANSWER)
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        self.writable_home()
        return Response(_data(services.state(_code(kwargs["code"]))))


class PhotoView(_BarcodeMixin, APIView):
    """The photo found for a code (CC BY-SA 3.0: show it with its attribution)."""

    @extend_schema(responses={(200, "image/*"): OpenApiTypes.BINARY})
    def get(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponse:
        self.writable_home()
        code = _code(kwargs["code"])
        row = BarcodeLookup.objects.filter(
            code=gtin.as_gtin14(code), state=LookupState.DONE
        ).first()
        if row is None or not row.image:
            raise Http404
        data = bytes(row.image)
        response = HttpResponse(data, content_type=content_type(data))
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

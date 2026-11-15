"""The photo of an open database, kept with its credit when a sheet is saved (docs/adr/0083).

The photo was fetched, decoded and re-encoded without metadata by the lookup task
(`services.run`); here it is only attached, and only where there is no photo yet: a photo taken
by a member is never replaced. Its attribution (source, page, licence, author) is stored with it
and shown under it, as CC BY-SA 3.0 asks.
"""

from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest

from housecairn.barcodes import gtin, services

FIELD = "barcode_photo"


def wanted(request: HttpRequest, saved_code: str) -> str:
    """The code whose photo the form asked to keep, if it is the code the sheet now has."""
    asked = gtin.clean(request.POST.get(FIELD, ""))
    if not asked or not saved_code or not gtin.is_valid(asked):
        return ""
    if gtin.as_gtin14(asked) != gtin.as_gtin14(saved_code):
        return ""
    return asked


def keep(request: HttpRequest, record: Any, saved_code: str) -> bool:
    """After a sheet is saved: attach the photo the form asked to keep, if any.

    The sheet is already saved; a photo that cannot be added (quota, too many photos) leaves
    it as it is and says so.
    """
    from django.contrib import messages
    from django.core.exceptions import ValidationError
    from django.utils.translation import gettext as _

    from housecairn.stock.models import Article

    code = wanted(request, saved_code)
    if not code:
        return False
    try:
        if isinstance(record, Article):
            return attach_to_article(request.user, record, code)
        return attach_to_item(request.user, record, code)
    except ValidationError as exc:
        messages.warning(
            request,
            _("The sheet is saved, but the photo of the product could not be added: %(why)s")
            % {"why": " ".join(exc.messages)},
        )
        return False


def _upload(data: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile("barcode", data, content_type="application/octet-stream")


def attach_to_item(actor: Any, item: Any, code: str) -> bool:
    """Give the item the photo found for `code`, if it has no photo. True: attached."""
    from housecairn.rooms import services as rooms_services

    if not code or rooms_services.photos_for(item).exists():
        return False
    found = services.photo_of(code)
    if found is None:
        return False
    data, attribution = found
    rooms_services.add_photo(actor, upload=_upload(data), item=item, attribution=attribution)
    return True


def attach_to_article(actor: Any, article: Any, code: str) -> bool:
    """Give the stock item the photo found for `code` as its label, if it has none."""
    from housecairn.stock import services as stock_services

    if not code or article.label:
        return False
    found = services.photo_of(code)
    if found is None:
        return False
    data, attribution = found
    stock_services.update_article(
        actor, article, label=_upload(data), label_attribution=attribution
    )
    return True

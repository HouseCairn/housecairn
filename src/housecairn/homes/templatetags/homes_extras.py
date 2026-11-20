"""The banner of an archived home, on every page of it (docs/adr/0089)."""

from typing import Any

from django import template

from housecairn.homes import services
from housecairn.homes.scoping import OPENED_HOME

register = template.Library()


@register.inclusion_tag("homes/_archived_banner.html", takes_context=True)
def archived_banner(context: dict[str, Any]) -> dict[str, Any]:
    """The banner when the page is one of an archived home, else nothing.

    The home is the one the view opened from the address (`HomeScopedMixin.get_home`): a page
    of a home that is not archived costs no query. The page that refuses a change says the same
    thing itself, so it has no banner.
    """
    request = context.get("request")
    home = getattr(request, OPENED_HOME, None)
    if request is None or home is None or home.archived_at is None:
        return {}
    if context.get("archived_refused"):
        return {}
    return {"home": home, "is_owner": services.is_owner(request.user, home), "request": request}

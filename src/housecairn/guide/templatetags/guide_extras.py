"""The banner of the example home, on every page of it (docs/adr/0084)."""

from typing import Any

from django import template

from housecairn.guide import services, states

register = template.Library()


@register.inclusion_tag("guide/_banner.html", takes_context=True)
def example_banner(context: dict[str, Any]) -> dict[str, Any]:
    """The banner when the page is one of the example home of the person, else nothing.

    The banner is on every page of the example home, so the ticks of the guide are brought
    up to date here: a step whose state is now true gets its tick (`states.tick`).
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    match = getattr(request, "resolver_match", None)
    home_id = match.kwargs.get("home_id") if match else None
    if request is None or not home_id or user is None or not user.is_authenticated:
        return {}
    home = services.example_home_of(user)
    if home is None or str(home.pk) != str(home_id):
        return {}
    states.tick(home)
    from housecairn.guide.views import delete_context

    return {"delete": delete_context(request, home)}

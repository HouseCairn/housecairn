from typing import Any

from django import template

from housecairn.notifications import devices

register = template.Library()


@register.inclusion_tag("notifications/_device_banner.html", takes_context=True)
def device_banner(context: template.Context) -> dict[str, Any]:
    """The offer to receive the reminders on this device, on Today (docs/adr/0072).

    Nothing is rendered unless the instance sends to devices and a person is signed in; the
    script (static/js/webpush.js) decides the rest from the browser.
    """
    request = context.get("request")
    offer = devices.offer_for(getattr(request, "user", None))
    return {"offer": offer}

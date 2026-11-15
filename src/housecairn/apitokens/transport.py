"""HTTPS, and plain HTTP only between local addresses (docs/adr/0078).

The OAuth endpoints and every route of `/api/v1/sync/` answer over HTTPS. Plain HTTP is
accepted only when both hold:

1. the peer is local: `REMOTE_ADDR` as `core.proxy.ForwardedHeadersMiddleware` leaves it (the
   client a trusted proxy reports, else the peer itself), judged by `core.netguard.is_public`,
   the instance's one rule of what is on the open web;
2. the `Host` the phone typed is an IP literal, with or without a port, that is not public. A
   name is refused on plain HTTP, `.local` and `.home.arpa` included: a name says nothing of
   where the request went, and a multicast DNS answer can be forged by anyone on the network.

`request.is_secure()` counts `X-Forwarded-Proto` only from a trusted proxy (the middleware
removes it from any other peer), so a client cannot claim HTTPS. `OAUTHLIB_INSECURE_TRANSPORT`
is never set: `oauth.TransportRuleCore` gives oauthlib an `https` address only for a request
this rule accepted.
"""

import ipaddress

from django.http import HttpRequest
from django.utils.translation import gettext as _

from housecairn.core.netguard import is_public
from housecairn.core.proxy import parse_address


def _host_address(request: HttpRequest) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The IP literal of the Host header, without its port, or None for a name."""
    host = request.get_host()
    if host.startswith("["):
        literal = host[1 : host.find("]")] if "]" in host else ""
    elif host.count(":") == 1:
        literal = host.split(":", 1)[0]
    else:
        literal = host
    try:
        return ipaddress.ip_address(literal)
    except ValueError:
        return None


def refusal(request: HttpRequest) -> str | None:
    """None when the request may go on; else why plain HTTP is refused, worded for a person."""
    if request.is_secure():
        return None
    peer = parse_address(request.META.get("REMOTE_ADDR"))
    if peer is None or is_public(peer):
        return _("Plain HTTP is accepted only from the local network. Use HTTPS.")
    host = _host_address(request)
    if host is None:
        return _(
            "Plain HTTP is accepted only on the IP address of the server on the local "
            "network, not on a name. Use HTTPS, or the server's local IP address."
        )
    if is_public(host):
        return _("Plain HTTP is accepted only on a local address. Use HTTPS.")
    return None


def allowed(request: HttpRequest) -> bool:
    return refusal(request) is None

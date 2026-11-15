"""Which addresses the server may call, and a guarded fetch for a page a person typed.

Two kinds of caller share this module:

- the addresses a person may point the server at. `forbidden_address` is the rule of the
  test of an identity provider (docs/adr/0041): loopback, link-local, multicast,
  unspecified and reserved addresses are refused and private networks are allowed, since
  an identity provider often runs on the local network. The notifications start from it
  and refuse more (`notifications.outbound.refusal`, docs/adr/0059): the metadata services
  of cloud providers always, and every address that is not global (`is_public`) unless an
  administrator allows private networks. An IPv6 address that carries an IPv4 one is
  judged by the IPv4 address (`embedded_ipv4`);
- the preview of a link (docs/adr/0050), where a person types the address of a page on the
  open web and the server reads it. `is_public` is stricter: only global addresses, so no
  private network, no shared address space and no metadata service of a cloud provider.
  `fetch` applies it to every hop of the request.

`fetch` is built so that the check cannot be got around:

- the name is resolved once per hop and every address it gives must pass; the connection
  goes to an address that passed, never to the name again, so a resolver that answers
  differently the second time (rebinding) is not asked;
- a redirect is followed by the same code, hop after hop, at most `MAX_REDIRECTS` times,
  and only to `http` and `https`; the port must be 80 or 443 on every hop (`PUBLIC_PORTS`),
  so the server cannot be made to speak HTTP to a mail or database service;
- the whole request, name resolution apart, lasts `seconds` at most, however slowly the
  server answers: a watchdog shuts the sockets down at the deadline, so a read that
  blocks gets the end of the stream at once. Nothing outlives the call: the watchdog is
  stopped and joined, and every socket is closed, whatever happened;
- the body is read in pieces and never beyond `max_bytes`; the answer is not decoded or
  decompressed (`Accept-Encoding: identity`), so a small body cannot grow;
- nothing in the answer is run or loaded: the caller gets bytes.

A refusal is a `RefusedError` with a short reason code, never the address or the URL.
"""

import http.client
import ipaddress
import socket
import ssl
import threading
import time
from collections.abc import Callable, Container
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

Address = ipaddress.IPv4Address | ipaddress.IPv6Address

NAT64 = ipaddress.IPv6Network("64:ff9b::/96")  # RFC 6052: the IPv4 address is the last 32 bits
# Metadata services of cloud providers: AWS, Azure, GCP and others (link-local, refused
# anyway), Alibaba, Oracle, AWS in IPv6. `is_public` refuses them; the
# notifications refuse them even when an administrator allows private networks
# (docs/adr/0059).
SITE_LOCAL = ipaddress.IPv6Network("fec0::/10")  # RFC 3879: deprecated, yet "global" to Python
METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(a)
    for a in ("169.254.169.254", "100.100.100.200", "192.0.0.192", "fd00:ec2::254")
)
MAX_REDIRECTS = 3
# The ports a page of the open web is served on. Any other port is a service that was not
# meant to answer a web request (mail, databases, admin panels), so the address is refused
# on every hop, before the name is resolved.
PUBLIC_PORTS: Container[int] = frozenset({80, 443})
USER_AGENT = "HouseCairn link preview"


def embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 one carries: mapped, NAT64, 6to4 or Teredo (client)."""
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address in NAT64:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    if address.sixtofour is not None:
        return address.sixtofour
    if address.teredo is not None:
        return address.teredo[1]
    return None


def forbidden_address(address: Address) -> bool:
    """Addresses an issuer may not point to: the instance itself and link-local services.

    An IPv6 address that carries an IPv4 one is judged by the IPv4 address: through a
    NAT64 gateway or a 6to4 relay, it reaches that address. Private networks stay allowed:
    an identity provider often runs on the local network.
    """
    if isinstance(address, ipaddress.IPv6Address):
        embedded = embedded_ipv4(address)
        if embedded is not None:
            address = embedded
    return bool(
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    )


def is_public(address: Address) -> bool:
    """True for a global address: not private, shared, loopback, link-local, site-local,
    multicast, reserved, unspecified or a metadata service, also when an IPv6 address
    carries it.

    The IPv6 site-local range fec0::/10 (deprecated by RFC 3879) is private in all but
    name, yet Python calls it global: it is refused here.
    """
    if isinstance(address, ipaddress.IPv6Address):
        embedded = embedded_ipv4(address)
        if embedded is not None:
            address = embedded
    return bool(
        address.is_global
        and not (isinstance(address, ipaddress.IPv6Address) and address in SITE_LOCAL)
        and not address.is_multicast
        and address not in METADATA_ADDRESSES
        and not forbidden_address(address)
    )


class RefusedError(Exception):
    """The request was not made or not completed; `reason` is a short code, never an address.

    Codes: `scheme`, `credentials`, `port`, `address`, `unresolved`, `redirects`, `timeout`,
    `connect`, `tls`, `status`, `too_large`.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Fetched:
    url: str  # the address of the last hop, to resolve what the page points to
    content_type: str
    charset: str | None
    body: bytes
    truncated: bool


# Name resolution, replaced by the tests that need a name to point somewhere.
_resolve = socket.getaddrinfo


def _shut(sock: socket.socket) -> None:
    """Shut a socket down in both directions: a thread blocked reading it gets the end of
    the stream. A socket already closed (descriptor -1) is left alone."""
    if sock.fileno() == -1:
        return
    with suppress(OSError):
        socket.socket.shutdown(sock, socket.SHUT_RDWR)


class _Session:
    """The deadline of one `fetch` and every socket it opened."""

    def __init__(self, seconds: float) -> None:
        self.deadline = time.monotonic() + seconds
        self.expired = threading.Event()
        self._sockets: list[socket.socket] = []
        self._lock = threading.Lock()
        self._closed = False
        self._timer = threading.Timer(seconds, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def left(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or self.expired.is_set():
            self.expired.set()
            raise RefusedError("timeout")
        return remaining

    def over(self) -> bool:
        """Whether the time is up: the watchdog fired, or the deadline passed a moment before it."""
        return self.expired.is_set() or time.monotonic() >= self.deadline

    def _expire(self) -> None:
        self.expired.set()
        with self._lock:
            sockets = list(self._sockets)
        for sock in sockets:
            _shut(sock)

    def track(self, sock: socket.socket) -> None:
        with self._lock:
            if self._closed:
                sock.close()
                raise RefusedError("timeout")
            self._sockets.append(sock)
        if self.expired.is_set():
            _shut(sock)

    def close(self) -> None:
        self._timer.cancel()
        if self._timer.is_alive():
            self._timer.join()
        with self._lock:
            self._closed = True
            sockets, self._sockets = self._sockets, []
        for sock in sockets:
            with suppress(OSError):
                sock.close()


class _Pinned(http.client.HTTPConnection):
    """A connection to an address that was checked, TLS verified against the host name."""

    def __init__(
        self, session: _Session, host: str, port: int, address: str, family: int, tls: bool
    ) -> None:
        super().__init__(host, port, timeout=session.left())
        self.default_port = 443 if tls else 80
        self._session = session
        self._address = address
        self._family = family
        self._tls = tls

    def connect(self) -> None:
        sock = socket.socket(self._family, socket.SOCK_STREAM)
        self._session.track(sock)
        sock.settimeout(self._session.left())
        sock.connect((self._address, self.port))
        if self._tls:
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            wrapped = context.wrap_socket(sock, server_hostname=self.host)
            self._session.track(wrapped)
            sock = wrapped
        self.sock = sock


def _addresses(host: str, port: int, allowed: Callable[[Address], bool]) -> list[tuple[int, str]]:
    """The (family, address) pairs of a name, all of which must be allowed."""
    try:
        infos = _resolve(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise RefusedError("unresolved") from None
    found: list[tuple[int, str]] = []
    for info in infos:
        text = str(info[4][0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(text)
        except ValueError:
            raise RefusedError("address") from None
        if not allowed(address):
            raise RefusedError("address")
        if (info[0], text) not in found:
            found.append((info[0], text))
    if not found:
        raise RefusedError("unresolved")
    return found


def _hop(
    session: _Session,
    url: str,
    *,
    accept: str,
    max_bytes: int,
    truncate: bool,
    allowed: Callable[[Address], bool],
    user_agent: str = USER_AGENT,
) -> Fetched | str:
    """One request. Returns the answer, or the address a redirect points to."""
    try:
        parts = urlsplit(url)
        scheme, hostname, port = parts.scheme.lower(), parts.hostname, parts.port
    except ValueError:
        raise RefusedError("scheme") from None
    if scheme not in ("http", "https"):
        raise RefusedError("scheme")
    if parts.username is not None or parts.password is not None:
        raise RefusedError("credentials")
    if not hostname:
        raise RefusedError("scheme")
    try:
        host = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        raise RefusedError("unresolved") from None
    number = port or (443 if scheme == "https" else 80)
    if number not in PUBLIC_PORTS:
        raise RefusedError("port")
    targets = _addresses(host, number, allowed)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    last: RefusedError = RefusedError("connect")
    for family, address in targets:
        connection = _Pinned(session, host, number, address, family, scheme == "https")
        try:
            try:
                connection.request(
                    "GET",
                    path,
                    headers={
                        "User-Agent": user_agent,
                        "Accept": accept,
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
            except ssl.SSLError:
                last = RefusedError("tls")
                continue
            except (http.client.HTTPException, OSError, ValueError):
                if session.over():
                    raise RefusedError("timeout") from None
                last = RefusedError("connect")
                continue
            try:
                return _answer(session, response, url, max_bytes=max_bytes, truncate=truncate)
            finally:
                # The response reads through its own file object, which keeps the descriptor
                # open after the connection is closed: close it here, not when it is collected.
                response.close()
        finally:
            connection.close()
    raise last


def _answer(
    session: _Session,
    response: http.client.HTTPResponse,
    url: str,
    *,
    max_bytes: int,
    truncate: bool,
) -> Fetched | str:
    if response.status in (301, 302, 303, 307, 308):
        location = response.getheader("Location")
        if not location or len(location) > 2048:
            raise RefusedError("status")
        return urljoin(url, location)
    if response.status != 200:
        raise RefusedError("status")
    declared = response.getheader("Content-Length")
    if declared and declared.isdigit() and int(declared) > max_bytes and not truncate:
        raise RefusedError("too_large")
    chunks: list[bytes] = []
    total = 0
    truncated = False
    try:
        while True:
            session.left()
            part = response.read1(min(65536, max_bytes + 1 - total))
            if not part:
                break
            chunks.append(part)
            total += len(part)
            if total > max_bytes:
                if not truncate:
                    raise RefusedError("too_large")
                truncated = True
                break
    except ssl.SSLError:
        raise RefusedError("tls") from None
    except (http.client.HTTPException, OSError, ValueError):
        if session.over():
            raise RefusedError("timeout") from None
        raise RefusedError("connect") from None
    if session.over():
        raise RefusedError("timeout")
    body = b"".join(chunks)[:max_bytes]
    return Fetched(
        url=url,
        content_type=response.headers.get_content_type(),
        charset=response.headers.get_content_charset(),
        body=body,
        truncated=truncated,
    )


def fetch(
    url: str,
    *,
    max_bytes: int,
    seconds: float,
    accept: str = "*/*",
    truncate: bool = False,
    allowed: Callable[[Address], bool] | None = None,
    max_redirects: int = MAX_REDIRECTS,
    user_agent: str = USER_AGENT,
) -> Fetched:
    """GET a page or a file on the open web; `RefusedError` with a reason code if it cannot be.

    `truncate`: stop at `max_bytes` and return what came (the head of a page); otherwise
    a longer answer is refused. `allowed` is the rule for an address, `is_public` unless a
    test needs a local server. `user_agent` names the caller (the barcode lookup gives its own,
    as the open databases ask, docs/adr/0083).
    """
    rule: Callable[[Address], bool] = allowed or is_public
    session = _Session(seconds)
    try:
        current = url
        for _hop_number in range(max_redirects + 1):
            result: Any = _hop(
                session,
                current,
                accept=accept,
                max_bytes=max_bytes,
                truncate=truncate,
                allowed=rule,
                user_agent=user_agent,
            )
            if isinstance(result, Fetched):
                return result
            current = result
        raise RefusedError("redirects")
    finally:
        session.close()


def post(
    url: str,
    *,
    body: bytes,
    headers: dict[str, str],
    seconds: float,
    allowed: Callable[[Address], bool] | None = None,
) -> int:
    """POST `body` to an HTTPS address on port 443; the status of the answer.

    For the push services of browsers (docs/adr/0066): the same rules as `fetch` (every
    address of the name must pass `allowed`, `is_public` by default; the connection goes
    to an address that passed; `seconds` in all), and stricter ones: `https` only, port 443
    only, no redirect (a 3xx is returned as a status, never followed). At most a few
    kilobytes of the answer are read, then dropped.
    """
    rule: Callable[[Address], bool] = allowed or is_public
    try:
        parts = urlsplit(url)
        scheme, hostname, port = parts.scheme.lower(), parts.hostname, parts.port
    except ValueError:
        raise RefusedError("scheme") from None
    if scheme != "https" or not hostname:
        raise RefusedError("scheme")
    if parts.username is not None or parts.password is not None:
        raise RefusedError("credentials")
    if (port or 443) != 443:
        raise RefusedError("port")
    try:
        host = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        raise RefusedError("unresolved") from None
    session = _Session(seconds)
    try:
        targets = _addresses(host, 443, rule)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        last: RefusedError = RefusedError("connect")
        for family, address in targets:
            connection = _Pinned(session, host, 443, address, family, True)
            try:
                try:
                    connection.request(
                        "POST",
                        path,
                        body=body,
                        headers={
                            **headers,
                            "Content-Length": str(len(body)),
                            "User-Agent": "HouseCairn",
                            "Connection": "close",
                        },
                    )
                    response = connection.getresponse()
                    try:
                        response.read(8192)
                    finally:
                        response.close()
                except ssl.SSLError:
                    last = RefusedError("tls")
                    continue
                except (http.client.HTTPException, OSError, ValueError):
                    if session.over():
                        raise RefusedError("timeout") from None
                    last = RefusedError("connect")
                    continue
                if session.over():
                    raise RefusedError("timeout")
                return int(response.status)
            finally:
                connection.close()
        raise last
    finally:
        session.close()

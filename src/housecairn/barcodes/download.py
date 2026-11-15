"""Downloading the Nudger export, resumed with HTTP ranges (docs/adr/0083).

The file is written in pieces to `<data dir>/barcodes/gtin-open-data.zip.part`, then renamed
when complete. A download that stops goes on from the size of the part with a `Range` request.
The address is fixed (`catalogue.SOURCE_URL`); every address of its name must be public
(`netguard.is_public`), and the connection goes to an address that passed, as in `netguard`.
"""

import http.client
import socket
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings

from housecairn.barcodes import catalogue, online
from housecairn.core import netguard

PIECE = 1024 * 1024


def folder() -> Path:
    path = Path(settings.DATA_DIR) / "barcodes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def target() -> Path:
    return folder() / "gtin-open-data.zip"


class _Pinned(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, family: int, timeout: float) -> None:
        super().__init__(host, 443, timeout=timeout)
        self._address = address
        self._family = family

    def connect(self) -> None:
        sock = socket.socket(self._family, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect((self._address, 443))
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.sock = context.wrap_socket(sock, server_hostname=self.host)


def fetch(
    seconds: float | None = None, *, url: str = catalogue.SOURCE_URL, allowed: Any = None
) -> bool:
    """Download for at most `seconds` (None: to the end); True once the file is complete."""
    final = target()
    part = final.with_suffix(".zip.part")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise netguard.RefusedError("scheme")
    host = parts.hostname
    rule = allowed or netguard.is_public
    family, address = netguard._addresses(host, 443, rule)[0]
    deadline = None if seconds is None else time.monotonic() + seconds
    have = part.stat().st_size if part.exists() else 0
    connection = _Pinned(host, address, family, timeout=30)
    try:
        headers = {"User-Agent": online.user_agent(), "Accept-Encoding": "identity"}
        if have:
            headers["Range"] = f"bytes={have}-"
        connection.request("GET", parts.path, headers=headers)
        response = connection.getresponse()
        if response.status == 416:  # nothing left: the part is the whole file
            part.replace(final)
            return True
        if response.status == 200:
            have = 0  # the server sent the whole file again
        elif response.status != 206:
            raise netguard.RefusedError("status")
        with open(part, "ab" if have else "wb") as out:
            while True:
                piece = response.read(PIECE)
                if not piece:
                    break
                out.write(piece)
                if deadline is not None and time.monotonic() >= deadline:
                    return False
    finally:
        connection.close()
    part.replace(final)
    return True

"""Asking the providers, in their order, what a code is (docs/adr/0083).

Only the code leaves the server: the address carries it and nothing else, the request has no
cookie and no other header than the `User-Agent` and `Accept`. Every call goes through
`core.netguard.fetch` (public addresses only, every redirect checked, size and time limited);
an answer is JSON read with `json.loads`, never run. The providers are in
`barcodes.providers`.
"""

import logging
import time
from typing import Any

from housecairn.barcodes import providers
from housecairn.barcodes.providers import Found, user_agent
from housecairn.core import netguard

logger = logging.getLogger("housecairn.barcodes")

IMAGE_MAX_BYTES = 5 * 1024 * 1024
# All the providers together, then the photo: the task stays well under its 25 seconds (ADR 0070).
LOOKUP_SECONDS = 6.0
IMAGE_SECONDS = 6.0


class LookupFailedError(Exception):
    """No provider could be read (network, guard, time): not the same as "not found"."""


def lookup(code: str, *, allowed: Any = None) -> Found | None:
    """The first provider that knows the code, or None; `LookupFailedError` if none answered."""
    deadline = time.monotonic() + LOOKUP_SECONDS
    answered = False
    for provider in providers.PROVIDERS:
        if not provider.enabled():
            continue
        left = deadline - time.monotonic()
        if left <= 0.5:
            break
        try:
            found = provider.lookup(code, seconds=left, allowed=allowed)
        except providers.UnavailableError as unavailable:
            logger.info("A barcode source could not be read (%s: %s).", provider.name, unavailable)
            continue
        answered = True
        if found is not None:
            return found
    if not answered:
        raise LookupFailedError
    return None


def image(url: str, *, allowed: Any = None) -> bytes | None:
    """The bytes of the photo of a product, or None; re-encoded by the caller."""
    try:
        return netguard.fetch(
            url,
            max_bytes=IMAGE_MAX_BYTES,
            seconds=IMAGE_SECONDS,
            accept="image/jpeg,image/png,image/webp",
            allowed=allowed,
            user_agent=user_agent(),
        ).body
    except netguard.RefusedError as refused:
        logger.info("The photo of a product could not be read (%s).", refused.reason)
        return None

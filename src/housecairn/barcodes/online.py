"""Asking Open Products Facts, then Open Food Facts, what a code is (docs/adr/0083).

Only the code leaves the server: the address carries it and nothing else, the request has no
cookie and no other header than those below. Every call goes through `core.netguard.fetch`
(public addresses only, every redirect checked, size and time limited); the answer is JSON
read with `json.loads`, never run.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from housecairn.core import netguard

logger = logging.getLogger("housecairn.barcodes")

ANSWER_MAX_BYTES = 1024 * 1024
IMAGE_MAX_BYTES = 5 * 1024 * 1024
# Both sources together, then the photo: the task stays well under its 25 seconds (ADR 0070).
LOOKUP_SECONDS = 6.0
IMAGE_SECONDS = 6.0
FIELDS = "code,product_name,brands,categories,image_front_url,image_url,creator"
LICENSE = "CC BY-SA 3.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/3.0/"


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    api: str  # with {code}
    page: str  # with {code}


SOURCES: tuple[Source, ...] = (
    Source(
        "openproductsfacts",
        "Open Products Facts",
        "https://world.openproductsfacts.org/api/v2/product/{code}.json?fields=" + FIELDS,
        "https://world.openproductsfacts.org/product/{code}",
    ),
    Source(
        "openfoodfacts",
        "Open Food Facts",
        "https://world.openfoodfacts.org/api/v2/product/{code}.json?fields=" + FIELDS,
        "https://world.openfoodfacts.org/product/{code}",
    ),
)
BY_KEY = {s.key: s for s in SOURCES}


def user_agent() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        number = version("housecairn")
    except PackageNotFoundError:
        number = "dev"
    return f"HouseCairn/{number} (+https://github.com/HouseCairn/housecairn)"


@dataclass(frozen=True)
class Found:
    source: Source
    name: str
    brand: str
    category: str
    image_url: str
    author: str
    product_url: str


def _text(value: Any, length: int) -> str:
    return " ".join(value.split())[:length] if isinstance(value, str) else ""


def parse(source: Source, code: str, body: bytes) -> Found | None:
    """What one source said of the code; None when it does not know it."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("status") != 1:
        return None
    product = data.get("product")
    if not isinstance(product, dict):
        return None
    brands = _text(product.get("brands"), 300)
    categories = _text(product.get("categories"), 600)
    image = _text(product.get("image_front_url"), 500) or _text(product.get("image_url"), 500)
    if image and not image.startswith("https://"):
        image = ""
    found = Found(
        source=source,
        name=_text(product.get("product_name"), 200),
        brand=brands.split(",")[0].strip()[:120],
        category=categories.split(",")[-1].strip()[:200] if categories else "",
        image_url=image,
        author=_text(product.get("creator"), 120),
        product_url=source.page.format(code=code),
    )
    if not (found.name or found.brand or found.image_url):
        return None
    return found


class LookupFailedError(Exception):
    """No source could be read (network, guard, time): not the same as "not found"."""


def lookup(code: str, *, allowed: Any = None) -> Found | None:
    """The first source that knows the code, or None; `LookupFailedError` if none answered."""
    deadline = time.monotonic() + LOOKUP_SECONDS
    answered = False
    for source in SOURCES:
        left = deadline - time.monotonic()
        if left <= 0.5:
            break
        try:
            fetched = netguard.fetch(
                source.api.format(code=code),
                max_bytes=ANSWER_MAX_BYTES,
                seconds=left,
                accept="application/json",
                allowed=allowed,
                user_agent=user_agent(),
            )
        except netguard.RefusedError as refused:
            if refused.reason == "status":
                answered = True  # a 404 is an answer: the source does not know the code
            logger.info("A barcode source could not be read (%s).", refused.reason)
            continue
        answered = True
        found = parse(source, code, fetched.body)
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

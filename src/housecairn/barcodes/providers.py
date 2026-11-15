"""The sources a scanned code is looked up in, in their order (docs/adr/0083).

A provider is a class with a `name` (the key kept in the cache), an `attribution` (who the
data and the photos come from, under which licence) and `lookup(ean)`, which says what the
source knows of a code: a `Found`, `None` when it does not know it, or `UnavailableError` when
it could not be read. `PROVIDERS` is the order of the lookup; the first that knows the code
answers.

Adding a source is adding a class here and an instance to `PROVIDERS`. A source that needs an
account (a free key, say) reads it from a setting of the instance in `enabled()`, and is
skipped while it has none; nothing else changes. Every provider calls out through
`core.netguard.fetch` and sends the code, and nothing else.
"""

import json
from dataclasses import dataclass
from typing import Any, ClassVar

from housecairn.core import netguard

ANSWER_MAX_BYTES = 1024 * 1024


def user_agent() -> str:
    """How HouseCairn introduces itself, as the Open Food Facts project asks every application."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        number = version("housecairn")
    except PackageNotFoundError:
        number = "dev"
    return f"HouseCairn/{number} (+https://github.com/HouseCairn/housecairn)"


@dataclass(frozen=True)
class Attribution:
    """Who a source's data and photos come from, shown with them."""

    label: str  # shown to people, e.g. "Open Food Facts"
    url: str  # the home page of the source
    license: str  # the licence of the photos, e.g. "CC BY-SA 3.0"
    license_url: str


@dataclass(frozen=True)
class Found:
    """What a source knows of a code; empty strings for what it does not say."""

    provider: "Provider"
    name: str = ""
    brand: str = ""
    model: str = ""
    category: str = ""
    image_url: str = ""  # https only
    author: str = ""  # who added the product, when the source says
    product_url: str = ""  # the page of the product at the source


class UnavailableError(Exception):
    """The source could not be read (network, guard, time): not the same as "not found"."""


class Provider:
    """One source of the barcode lookup. Subclasses set `name` and `attribution`."""

    name: ClassVar[str]
    attribution: ClassVar[Attribution]

    def enabled(self) -> bool:
        """False to skip this source, for example while its account is not set."""
        return True

    def lookup(self, ean: str, *, seconds: float, allowed: Any = None) -> Found | None:
        """What the source knows of `ean` (a valid code, as published), within `seconds`.

        `allowed` is passed on to `netguard.fetch` (tests use it to refuse every address).
        """
        raise NotImplementedError


def text(value: Any, length: int) -> str:
    """A string of the answer on one line, cut to `length`; "" for anything else."""
    return " ".join(value.split())[:length] if isinstance(value, str) else ""


class OpenFactsProvider(Provider):
    """A database of the Open Food Facts project: one read API, the same answer for all."""

    host: ClassVar[str]
    FIELDS = "code,product_name,brands,categories,image_front_url,image_url,creator"

    def api_url(self, ean: str) -> str:
        return f"https://{self.host}/api/v2/product/{ean}.json?fields={self.FIELDS}"

    def page_url(self, ean: str) -> str:
        return f"https://{self.host}/product/{ean}"

    def lookup(self, ean: str, *, seconds: float, allowed: Any = None) -> Found | None:
        try:
            fetched = netguard.fetch(
                self.api_url(ean),
                max_bytes=ANSWER_MAX_BYTES,
                seconds=seconds,
                accept="application/json",
                allowed=allowed,
                user_agent=user_agent(),
            )
        except netguard.RefusedError as refused:
            if refused.reason == "status":
                return None  # a 404 is an answer: the source does not know the code
            raise UnavailableError(refused.reason) from refused
        return self.parse(ean, fetched.body)

    def parse(self, ean: str, body: bytes) -> Found | None:
        """What the answer says of the code; None when the source does not know it."""
        try:
            data = json.loads(body)
        except ValueError:
            return None
        if not isinstance(data, dict) or data.get("status") != 1:
            return None
        product = data.get("product")
        if not isinstance(product, dict):
            return None
        brands = text(product.get("brands"), 300)
        categories = text(product.get("categories"), 600)
        image = text(product.get("image_front_url"), 500) or text(product.get("image_url"), 500)
        if image and not image.startswith("https://"):
            image = ""
        found = Found(
            provider=self,
            name=text(product.get("product_name"), 200),
            brand=brands.split(",")[0].strip()[:120],
            category=categories.split(",")[-1].strip()[:200] if categories else "",
            image_url=image,
            author=text(product.get("creator"), 120),
            product_url=self.page_url(ean),
        )
        if not (found.name or found.brand or found.image_url):
            return None
        return found


CC_BY_SA_3 = ("CC BY-SA 3.0", "https://creativecommons.org/licenses/by-sa/3.0/")


class OpenProductsFacts(OpenFactsProvider):
    name = "openproductsfacts"
    host = "world.openproductsfacts.org"
    attribution = Attribution(
        "Open Products Facts", "https://world.openproductsfacts.org", *CC_BY_SA_3
    )


class OpenFoodFacts(OpenFactsProvider):
    name = "openfoodfacts"
    host = "world.openfoodfacts.org"
    attribution = Attribution("Open Food Facts", "https://world.openfoodfacts.org", *CC_BY_SA_3)


PROVIDERS: tuple[Provider, ...] = (OpenProductsFacts(), OpenFoodFacts())


def by_name(name: str) -> Provider | None:
    """The provider a cached answer came from, if it still exists."""
    return next((p for p in PROVIDERS if p.name == name), None)

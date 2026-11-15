"""The online lookup of a code and its providers, with simulated sources (docs/adr/0083).

No test calls a real server: the sources are local HTTP servers or `netguard.fetch` replaced.
"""

import io
import json
from typing import Any

import pytest
from django.utils import timezone
from PIL import Image

from housecairn.barcodes import online, providers, services
from housecairn.barcodes.models import BarcodeLookup
from housecairn.core import netguard

pytestmark = pytest.mark.django_db


@pytest.fixture
def online_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "online_enabled", lambda: True)


def _answer(product: dict[str, Any] | None) -> bytes:
    if product is None:
        return json.dumps({"status": 0, "status_verbose": "product not found"}).encode()
    return json.dumps({"status": 1, "product": product}).encode()


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (40, 30), "red")
    exif = Image.Exif()
    exif[0x010F] = "Example camera"
    image.save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


class _Sources:
    """`netguard.fetch` replaced: records every address asked, answers from a table."""

    def __init__(self, answers: dict[str, bytes | Exception]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, **kwargs: Any) -> netguard.Fetched:
        self.asked.append((url, kwargs))
        for start, answer in self.answers.items():
            if url.startswith(start):
                if isinstance(answer, Exception):
                    raise answer
                return netguard.Fetched(url, "application/json", None, answer, False)
        raise netguard.RefusedError("status")


OPF = "https://world.openproductsfacts.org/api/v2/product/"
OFF = "https://world.openfoodfacts.org/api/v2/product/"
PHOTO = "https://images.example.com/front.jpg"


class TestOnline:
    def test_off_by_default_nothing_is_queued(self, monkeypatch: pytest.MonkeyPatch) -> None:
        queued: list[object] = []
        monkeypatch.setattr(services, "_queue", queued.append)
        assert services.start("4006381333931").state == "off"
        assert queued == []
        assert not BarcodeLookup.objects.exists()

    def test_off_on_a_demonstration_instance(self, settings: Any) -> None:
        settings.HOUSECAIRN_DEMO = True
        assert not services.online_enabled()

    def test_the_request_queues_the_task_and_answers_at_once(
        self, online_on: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queued: list[BarcodeLookup] = []
        monkeypatch.setattr(services, "_queue", queued.append)
        answer = services.start("4006381333931")
        assert answer.state == "running"
        assert len(queued) == 1
        assert queued[0].code == "04006381333931"

    def test_open_products_facts_first_then_open_food_facts(
        self, online_on: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sources = _Sources(
            {
                OPF: _answer(None),
                OFF: _answer(
                    {
                        "product_name": "Kettle descaler",
                        "brands": "Acme, Acme Home",
                        "categories": "Cleaning, Descalers",
                        "image_front_url": PHOTO,
                        "creator": "example-contributor",
                    }
                ),
                PHOTO: _jpeg(),
            }
        )
        monkeypatch.setattr(netguard, "fetch", sources)
        monkeypatch.setattr(services, "_queue", lambda row: None)
        services.start("4006381333931")
        row = BarcodeLookup.objects.get()
        services.run(str(row.pk), row.started_at.isoformat())
        row.refresh_from_db()
        assert row.state == "done"
        assert (row.source, row.name, row.brand, row.category) == (
            "openfoodfacts",
            "Kettle descaler",
            "Acme",
            "Descalers",
        )
        assert row.product_url == "https://world.openfoodfacts.org/product/4006381333931"
        assert [url.split("?")[0] for url, _ in sources.asked] == [
            OPF + "4006381333931.json",
            OFF + "4006381333931.json",
            PHOTO,
        ]
        # Only the code went out, with the application's own User-Agent and a strict limit.
        for _url, options in sources.asked:
            assert options["user_agent"].startswith("HouseCairn/")
            assert options["seconds"] <= online.LOOKUP_SECONDS
            assert options["max_bytes"] <= online.IMAGE_MAX_BYTES
        # The photo is re-encoded, without its metadata.
        stored = Image.open(io.BytesIO(bytes(row.image)))
        assert not stored.getexif()
        photo, credit = services.photo_of("4006381333931")  # type: ignore[misc]
        assert photo
        assert credit["license"] == "CC BY-SA 3.0"
        assert credit["author"] == "example-contributor"
        assert credit["source"] == "Open Food Facts"

    def test_the_cache_answers_the_second_scan(
        self, online_on: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sources = _Sources({OPF: _answer({"product_name": "Filter", "brands": "Acme"})})
        monkeypatch.setattr(netguard, "fetch", sources)
        monkeypatch.setattr(services, "_queue", lambda row: None)
        services.start("4006381333931")
        row = BarcodeLookup.objects.get()
        services.run(str(row.pk), row.started_at.isoformat())
        asked = len(sources.asked)
        again = services.start("4006381333931")
        assert again.state == "done"
        assert again.name == "Filter"
        assert len(sources.asked) == asked

    def test_not_found(self, online_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(netguard, "fetch", _Sources({OPF: _answer(None), OFF: _answer(None)}))
        monkeypatch.setattr(services, "_queue", lambda row: None)
        services.start("4006381333931")
        row = BarcodeLookup.objects.get()
        services.run(str(row.pk), row.started_at.isoformat())
        row.refresh_from_db()
        assert row.state == "not_found"

    def test_no_source_answered_is_a_failure(
        self, online_on: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        refused = netguard.RefusedError("address")
        monkeypatch.setattr(netguard, "fetch", _Sources({OPF: refused, OFF: refused}))
        monkeypatch.setattr(services, "_queue", lambda row: None)
        services.start("4006381333931")
        row = BarcodeLookup.objects.get()
        services.run(str(row.pk), row.started_at.isoformat())
        row.refresh_from_db()
        assert row.state == "failed"

    def test_the_real_guard_refuses_a_private_address(self) -> None:
        """The sources are called through the guard of `core.netguard`, unchanged."""
        with pytest.raises(online.LookupFailedError):
            online.lookup("4006381333931", allowed=lambda address: False)

    def test_a_late_task_does_nothing(
        self, online_on: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(services, "_queue", lambda row: None)
        called: list[str] = []
        monkeypatch.setattr(online, "lookup", lambda code, **kw: called.append(code))
        services.start("4006381333931")
        row = BarcodeLookup.objects.get()
        services.run(str(row.pk), "2000-01-01T00:00:00+00:00")
        assert called == []

    @pytest.mark.parametrize(
        "body",
        [b"not json", b"[]", json.dumps({"status": 1, "product": "x"}).encode()],
    )
    def test_a_strange_answer_is_not_a_product(self, body: bytes) -> None:
        assert providers.OpenProductsFacts().parse("4006381333931", body) is None

    def test_an_image_address_that_is_not_https_is_dropped(self) -> None:
        found = providers.OpenProductsFacts().parse(
            "4006381333931",
            _answer({"product_name": "Filter", "image_front_url": "http://example.com/a.jpg"}),
        )
        assert found is not None
        assert found.image_url == ""


class _Account(providers.Provider):
    """A provider that needs an account, as a free one may later: skipped while it has none."""

    name = "example"
    attribution = providers.Attribution(
        "Example", "https://example.com", "CC BY 4.0", "https://example.com/licence"
    )

    def __init__(self, key: str = "") -> None:
        self.key = key
        self.asked: list[str] = []

    def enabled(self) -> bool:
        return bool(self.key)

    def lookup(self, ean: str, *, seconds: float, allowed: Any = None) -> providers.Found | None:
        self.asked.append(ean)
        return providers.Found(provider=self, name="Example product", model="EX-1")


class TestProviders:
    def test_the_order_is_open_products_facts_then_open_food_facts(self) -> None:
        assert [p.name for p in providers.PROVIDERS] == ["openproductsfacts", "openfoodfacts"]
        for provider in providers.PROVIDERS:
            assert provider.attribution.license == "CC BY-SA 3.0"
            assert provider.attribution.license_url.startswith("https://creativecommons.org/")

    def test_a_new_provider_is_added_to_the_list_and_nothing_else(
        self, online_on: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        extra = _Account(key="a free key")
        monkeypatch.setattr(providers, "PROVIDERS", (*providers.PROVIDERS, extra))
        monkeypatch.setattr(netguard, "fetch", _Sources({OPF: _answer(None), OFF: _answer(None)}))
        monkeypatch.setattr(services, "_queue", lambda row: None)
        services.start("4006381333931")
        row = BarcodeLookup.objects.get()
        services.run(str(row.pk), row.started_at.isoformat())
        assert extra.asked == ["4006381333931"]
        answer = services.state("4006381333931")
        assert (answer.state, answer.source, answer.source_label) == ("done", "example", "Example")
        assert (answer.name, answer.model) == ("Example product", "EX-1")
        assert (answer.license, answer.license_url) == ("CC BY 4.0", "https://example.com/licence")

    def test_a_provider_without_its_account_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        extra = _Account()
        monkeypatch.setattr(providers, "PROVIDERS", (extra, *providers.PROVIDERS))
        monkeypatch.setattr(netguard, "fetch", _Sources({OPF: _answer({"product_name": "Filter"})}))
        found = online.lookup("4006381333931")
        assert found is not None
        assert found.provider.name == "openproductsfacts"
        assert extra.asked == []

    def test_an_answer_of_a_provider_taken_out_is_not_credited(self, online_on: None) -> None:
        BarcodeLookup.objects.create(
            code="04006381333931",
            state="done",
            started_at=timezone.now(),
            finished_at=timezone.now(),
            source="gone",
            name="Filter",
            image=b"x",
        )
        assert services.photo_of("4006381333931") is None

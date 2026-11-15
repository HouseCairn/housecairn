"""The local catalogue and the online lookup of a code, with simulated sources (docs/adr/0083).

No test calls a real server: the sources are local HTTP servers or `netguard.fetch` replaced.
"""

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from housecairn.barcodes import catalogue, online, services
from housecairn.barcodes.models import BarcodeLookup, CatalogueImport, CatalogueProduct
from housecairn.core import netguard

pytestmark = pytest.mark.django_db

HEADER = (
    '"gtin","brand","model","name","last_updated","gs1_country","gtinType","offers_count",'
    '"min_price","currency","categories","url"\n'
)
DISHWASHER = (
    '"4006381333931","ACME","DW-60","Dishwasher 60 cm","1","DE","GTIN_13","0","","",'
    '"ELECTROMENAGER | LAVE-VAISSELLE","https://example.com/a"\n'
)
JACKET = (
    '"036000291452","MOTO","J1","Riding jacket","1","US","UPC","0","","",'
    '"EQUIPEMENT DU MOTARD | BLOUSON MOTO","https://example.com/b"\n'
)
BAD = '"4006381333932","X","","Wrong check digit","1","DE","GTIN_13","0","","","PILES",""\n'
BULB = (
    '"96385074","LUX","E27","LED bulb","1","FR","EAN_8","0","","",'
    '"Éclairage > Ampoule","https://example.com/c"\n'
)


def _export(tmp_path: Path, rows: str, *, zipped: bool = True) -> Path:
    if not zipped:
        path = tmp_path / "export.csv"
        path.write_text(HEADER + rows, encoding="utf-8")
        return path
    path = tmp_path / "gtin-open-data.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("open4goods-gtin-dataset.csv", HEADER + rows)
    return path


@pytest.fixture
def catalogue_on(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "catalogue_enabled", lambda: True)


@pytest.fixture
def online_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "online_enabled", lambda: True)


class TestImport:
    def test_keeps_the_categories_useful_in_a_house(self, tmp_path: Path) -> None:
        progress = catalogue.import_file(_export(tmp_path, DISHWASHER + JACKET + BAD + BULB))
        assert progress.state == "done"
        assert progress.rows_read == 4
        assert set(CatalogueProduct.objects.values_list("gtin", flat=True)) == {
            4006381333931,
            96385074,
        }

    def test_all_categories(self, tmp_path: Path) -> None:
        catalogue.import_file(
            _export(tmp_path, DISHWASHER + JACKET, zipped=False), all_categories=True
        )
        assert CatalogueProduct.objects.count() == 2

    def test_a_stopped_import_goes_on_where_it_was(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(catalogue, "BATCH", 1)
        path = _export(tmp_path, DISHWASHER + BULB + JACKET)
        # The first slice stops after its first batch.
        first = catalogue.import_file(path, seconds=0)
        assert first.state == "importing"
        assert first.rows_read == 1
        seen: list[int] = []
        original = catalogue._write

        def counting(batch: list[catalogue.Row], *args: Any) -> None:
            seen.extend(r.gtin for r in batch)
            original(batch, *args)

        monkeypatch.setattr(catalogue, "_write", counting)
        done = catalogue.import_file(path)
        assert done.state == "done"
        assert 4006381333931 not in seen  # not read again
        assert CatalogueProduct.objects.count() == 2
        assert CatalogueImport.objects.count() == 1

    def test_another_file_starts_over(self, tmp_path: Path) -> None:
        catalogue.import_file(_export(tmp_path, DISHWASHER))
        other = tmp_path / "other"
        other.mkdir()
        catalogue.import_file(_export(other, BULB, zipped=False))
        assert list(CatalogueProduct.objects.values_list("gtin", flat=True)) == [96385074]

    def test_a_file_without_the_columns_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "x.csv"
        path.write_text('"a","b"\n"1","2"\n', encoding="utf-8")
        with pytest.raises(ValueError, match="no column"):
            catalogue.import_file(path)

    def test_the_lookup_finds_a_upc_a_by_its_ean_13(
        self, tmp_path: Path, catalogue_on: None
    ) -> None:
        catalogue.import_file(_export(tmp_path, JACKET), all_categories=True)
        answer = services.start("0036000291452")
        assert answer.state == "done"
        assert answer.source == "nudger"
        assert answer.brand == "MOTO"
        assert not BarcodeLookup.objects.exists()  # nothing left the server


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
        assert online.parse(online.SOURCES[0], "4006381333931", body) is None

    def test_an_image_address_that_is_not_https_is_dropped(self) -> None:
        found = online.parse(
            online.SOURCES[0],
            "4006381333931",
            _answer({"product_name": "Filter", "image_front_url": "http://example.com/a.jpg"}),
        )
        assert found is not None
        assert found.image_url == ""

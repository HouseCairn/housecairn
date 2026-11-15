"""Looking a code up in the open databases, in the background (ADR 0083).

`start` answers at once: from the cache when the providers were asked less than `FRESH` ago,
otherwise it saves a `running` row and queues `run`, which the worker runs under the guard of
`core.netguard` (docs/adr/0070); the providers and their order are in `barcodes.providers`.
Nothing here knows a home or a person: the cache is the instance's, and only the code went out.
"""

import datetime
import logging
from dataclasses import dataclass, field
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from housecairn.barcodes import gtin, online, providers
from housecairn.barcodes.models import BarcodeLookup, LookupState
from housecairn.demo import mode
from housecairn.instance.config import get_setting

logger = logging.getLogger("housecairn.barcodes")

FRESH = datetime.timedelta(days=30)
FRESH_NOT_FOUND = datetime.timedelta(days=1)
# A task that has not finished by then has failed, whatever became of the worker (ADR 0070).
DEADLINE = datetime.timedelta(seconds=30)
TASK_TIMEOUT = 25


def online_enabled() -> bool:
    return bool(get_setting("barcode_lookup_online")) and not mode.is_on()


@dataclass(frozen=True)
class Answer:
    """What is known of a code, for the screen and the API."""

    code: str
    state: str  # running, done, not_found, failed, off
    source: str = ""
    source_label: str = ""
    name: str = ""
    brand: str = ""
    model: str = ""
    category: str = ""
    product_url: str = ""
    author: str = ""
    has_photo: bool = False
    license: str = ""
    license_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def final(self) -> bool:
        return self.state != LookupState.RUNNING


def _from_row(row: BarcodeLookup) -> Answer:
    provider = providers.by_name(row.source) if row.source else None
    credit = provider.attribution if provider else None
    return Answer(
        code=row.code,
        state=row.state,
        source=row.source,
        source_label=credit.label if credit else row.source,
        name=row.name,
        brand=row.brand,
        model=row.model,
        category=row.category,
        product_url=row.product_url,
        author=row.author,
        has_photo=bool(row.image),
        license=credit.license if credit else "",
        license_url=credit.license_url if credit else "",
    )


def expire(row: BarcodeLookup) -> BarcodeLookup:
    """A `running` row older than the deadline is `failed`: nothing waits for ever."""
    if row.state == LookupState.RUNNING and row.started_at < timezone.now() - DEADLINE:
        BarcodeLookup.objects.filter(
            pk=row.pk, state=LookupState.RUNNING, started_at=row.started_at
        ).update(state=LookupState.FAILED, finished_at=timezone.now())
        row.refresh_from_db()
    return row


def _fresh(row: BarcodeLookup) -> bool:
    if row.state == LookupState.RUNNING:
        return True
    if row.finished_at is None:
        return False
    keep = FRESH if row.state == LookupState.DONE else FRESH_NOT_FOUND
    return row.finished_at >= timezone.now() - keep


def start(code: str) -> Answer:
    """What is known of `code` now; queues the online lookup if it is on and needed.

    `code` must be valid (`gtin.is_valid`); the caller checks rights and rate limits first.
    """
    key = gtin.as_gtin14(code)
    row = BarcodeLookup.objects.filter(code=key).first()
    if row is not None:
        row = expire(row)
        if _fresh(row):
            return _from_row(row)
    if not online_enabled():
        return Answer(code=code, state="off")
    now = timezone.now()
    try:
        with transaction.atomic():
            if row is None:
                row = BarcodeLookup.objects.create(
                    code=key, state=LookupState.RUNNING, started_at=now
                )
            else:
                BarcodeLookup.objects.filter(pk=row.pk).update(
                    state=LookupState.RUNNING,
                    started_at=now,
                    finished_at=None,
                    source="",
                    name="",
                    brand="",
                    model="",
                    category="",
                    product_url="",
                    author="",
                    image=b"",
                )
                row.refresh_from_db()
    except IntegrityError:  # two scans of the same code at once: the other one asks
        row = BarcodeLookup.objects.get(code=key)
        return _from_row(row)
    _queue(row)
    return _from_row(row)


def _queue(row: BarcodeLookup) -> None:
    from django_q.tasks import async_task

    transaction.on_commit(
        lambda: async_task(
            "housecairn.barcodes.services.run",
            str(row.pk),
            row.started_at.isoformat(),
            timeout=TASK_TIMEOUT,
            ack_failure=True,
        )
    )


def state(code: str) -> Answer:
    """The answer for a code already started, for the screen that asks again."""
    row = BarcodeLookup.objects.filter(code=gtin.as_gtin14(code)).first()
    if row is None:
        return Answer(code=code, state="off" if not online_enabled() else LookupState.FAILED)
    return _from_row(expire(row))


def run(row_id: str, started: str, *, allowed: Any = None) -> None:
    """Task of the worker: ask the providers, keep the first answer and its photo."""
    from housecairn.rooms import images

    row = BarcodeLookup.objects.filter(pk=row_id, state=LookupState.RUNNING).first()
    if row is None or row.started_at.isoformat() != started:
        return
    if expire(row).state != LookupState.RUNNING:
        return
    same = {"pk": row.pk, "state": LookupState.RUNNING, "started_at": row.started_at}
    if not online_enabled():
        BarcodeLookup.objects.filter(**same).delete()
        return
    try:
        found = online.lookup(gtin.as_published(row.code), allowed=allowed)
    except online.LookupFailedError:
        BarcodeLookup.objects.filter(**same).update(
            state=LookupState.FAILED, finished_at=timezone.now()
        )
        return
    except Exception:
        logger.exception("A barcode lookup failed: %s", row.pk)
        BarcodeLookup.objects.filter(**same).update(
            state=LookupState.FAILED, finished_at=timezone.now()
        )
        return
    if found is None:
        BarcodeLookup.objects.filter(**same).update(
            state=LookupState.NOT_FOUND, finished_at=timezone.now()
        )
        return
    picture = b""
    if found.image_url:
        data = online.image(found.image_url, allowed=allowed)
        if data:
            try:
                picture = images.process(data).data
            except Exception:
                logger.info("The photo of a product could not be kept (invalid).")
    BarcodeLookup.objects.filter(**same).update(
        state=LookupState.DONE,
        finished_at=timezone.now(),
        source=found.provider.name,
        name=found.name,
        brand=found.brand,
        model=found.model,
        category=found.category,
        product_url=found.product_url,
        author=found.author,
        image=picture,
    )


def photo_of(code: str) -> tuple[bytes, dict[str, str]] | None:
    """The photo found for a code and its attribution, or None."""
    if not gtin.is_valid(code):
        return None
    row = BarcodeLookup.objects.filter(code=gtin.as_gtin14(code), state=LookupState.DONE).first()
    if row is None or not row.image:
        return None
    provider = providers.by_name(row.source)
    if provider is None:  # a source taken out since: its photo cannot be credited
        return None
    credit = provider.attribution
    return bytes(row.image), {
        "source": credit.label,
        "source_url": row.product_url,
        "license": credit.license,
        "license_url": credit.license_url,
        "author": row.author,
    }

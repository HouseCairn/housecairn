"""Links of inspiration of a project, with a preview read once (docs/adr/0050).

A link is an address, a title and, when the page gave one, a picture stored like a photo of
the project. Adding a link saves it at once with its address. Reading the page, which can
take seconds, is a task of the background worker queued just after (docs/adr/0070), and only
if the instance allows it (`link_preview_enabled`, docs/adr/0035): the request never waits
for another server. The worker fills the title (unless the person typed one) and the picture
when it is done. Any failure leaves the plain address: adding a link never fails because a
page could not be read, and a reading that has not ended after `PREVIEW_DEADLINE_SECONDS`
is marked failed, never left "being read".
"""

import datetime
import logging
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.request import Request

from housecairn.audit import services as audit
from housecairn.homes.scoping import allow
from housecairn.instance.config import get_setting
from housecairn.projects import changelog, preview
from housecairn.projects.models import (
    MAX_LINKS_PER_PROJECT,
    PreviewState,
    Project,
    ProjectFile,
    ProjectLink,
)
from housecairn.projects.services import (
    AnyUser,
    _erase_files,
    _require_write,
    save_stored,
)
from housecairn.rooms import images
from housecairn.tasks import files as task_files

logger = logging.getLogger("housecairn.projects")

MAX_URL_LENGTH = 500
# A reading that has not ended this long after it started is a failure, whatever became of the
# worker. The reading itself is bounded by `preview.PREVIEW_SECONDS`.
PREVIEW_DEADLINE_SECONDS = 30
# The task is stopped by the worker a little before the deadline.
PREVIEW_TASK_SECONDS = 25


def preview_enabled() -> bool:
    return bool(get_setting("link_preview_enabled"))


def preview_allowed(request: HttpRequest | Request, project: Project) -> bool:
    """Take one use of the preview limits (per person and per home) before a link is added.

    Only an addition that makes the server read a page counts: with previews off nothing
    leaves the server. A person who cannot write is refused first and uses nothing.
    False: the limit is reached, and the caller must neither read nor add anything.
    """
    _require_write(request.user, project.home)
    if not preview_enabled():
        return True
    return allow(request, "housecairn_link_preview", key=str(project.home_id))


def clean_url(raw: str) -> str:
    """The address, trimmed, http or https only and without credentials; ValidationError."""
    text = raw.strip() if isinstance(raw, str) else ""
    invalid = ValidationError(
        {"url": _("Give the address of a web page, such as https://example.com.")}
    )
    if not text or len(text) > MAX_URL_LENGTH or any(c.isspace() or ord(c) < 32 for c in text):
        raise invalid
    try:
        parts = urlsplit(text)
        host = parts.hostname
    except ValueError:
        raise invalid from None
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise invalid
    if parts.username is not None or parts.password is not None:
        raise ValidationError({"url": _("The address cannot hold a user name or a password.")})
    return text


def add_link(actor: AnyUser, project: Project, *, url: str, title: str = "") -> ProjectLink:
    """Add a link and queue the reading of its page; the link is saved with its address.

    A title given by the person is kept; otherwise the worker fills the one the page gave,
    if any. The link returned says where the reading stands (`preview_state`).
    """
    from django_q.tasks import async_task

    _require_write(actor, project.home)
    address = clean_url(url)
    given = preview.clean_title(title) if isinstance(title, str) else ""
    if ProjectLink.objects.filter(project=project).count() >= MAX_LINKS_PER_PROJECT:
        raise ValidationError(
            _("At most %(max)d links for a project."), params={"max": MAX_LINKS_PER_PROJECT}
        )
    reading = preview_enabled()
    link = _save(actor, project, address, given, reading)
    if reading:
        async_task(
            "housecairn.projects.links.read_preview",
            str(link.pk),
            timeout=PREVIEW_TASK_SECONDS,
            # A task stopped at its timeout is not given again: the deadline ends the reading.
            ack_failure=True,
        )
        link.refresh_from_db()
    return link


@transaction.atomic
def _save(actor: AnyUser, project: Project, address: str, title: str, reading: bool) -> ProjectLink:
    link = ProjectLink(
        home=project.home,
        project=project,
        url=address,
        title=title,
        preview_state=PreviewState.RUNNING if reading else PreviewState.NONE,
        preview_started_at=timezone.now() if reading else None,
    )
    link.full_clean(exclude=["image"])
    link.save()
    changelog.saved(link)
    audit.record_object(
        "project_link.added",
        actor=actor,
        home_id=project.home_id,
        title=project.title,
        domain=preview.domain_of(address),
    )
    return link


def expire_preview(link: ProjectLink) -> ProjectLink:
    """Mark a reading that has run past its deadline as failed; the link, up to date.

    The update is conditional, so a result that arrives in the meantime is not overwritten.
    """
    started = link.preview_started_at
    if link.preview_state == PreviewState.RUNNING and (
        started is None
        or timezone.now() - started > datetime.timedelta(seconds=PREVIEW_DEADLINE_SECONDS)
    ):
        ProjectLink.objects.filter(
            pk=link.pk, preview_state=PreviewState.RUNNING, preview_started_at=started
        ).update(preview_state=PreviewState.FAILED)
        link.refresh_from_db()
    return link


def read_preview(link_id: str) -> None:
    """Task of the worker: read the page of a link, then fill its title and its picture.

    The same guard as ever (`preview.read`, `core.netguard`): public addresses only, every
    redirect checked, size and time limited. The task runs outside any transaction; the
    link is read again before it is changed, since the person may have deleted it meanwhile.
    """
    link = ProjectLink.objects.filter(pk=link_id, preview_state=PreviewState.RUNNING).first()
    if link is None:
        return
    # A task can come late: the worker gives it again after `retry` if it was stopped at its
    # timeout (django-q2 does not acknowledge that failure unless asked). The deadline applies
    # to the row as it is now, and a reading past it reads nothing.
    if expire_preview(link).preview_state != PreviewState.RUNNING:
        return
    started = link.preview_started_at
    if not preview_enabled():
        # The administrator turned the preview off after the link was queued: nothing leaves
        # the server any more, so the page is not read and the row goes back to "not read".
        ProjectLink.objects.filter(
            pk=link.pk, preview_state=PreviewState.RUNNING, preview_started_at=started
        ).update(preview_state=PreviewState.NONE)
        return
    state = PreviewState.DONE
    try:
        found = preview.read(link.url)
        if found.failed:
            state = PreviewState.FAILED
        else:
            _fill(link.pk, started, found)
    except Exception:
        logger.exception("The page of a link could not be read: %s", link.pk)
        state = PreviewState.FAILED
    ProjectLink.objects.filter(
        pk=link.pk, preview_state=PreviewState.RUNNING, preview_started_at=started
    ).update(preview_state=state)


@transaction.atomic
def _fill(link_id: object, started: datetime.datetime | None, found: preview.Preview) -> None:
    """Keep what the page gave: its title if the person typed none, and its picture."""
    link = (
        ProjectLink.objects.select_related("project", "home")
        .filter(pk=link_id, preview_state=PreviewState.RUNNING, preview_started_at=started)
        .first()
    )
    if link is None:  # deleted, or already ended past its deadline
        return
    before = changelog.stored(link)
    changed = []
    if found.title and not link.title:
        link.title = found.title
        changed.append("title")
    if found.image is not None:
        link.image = _store_picture(link.project, link, found.image)
        if link.image is not None:
            changed.append("image")
    if changed:
        link.save(update_fields=[*changed, "updated_at"])
        changelog.saved(link, before)


def _store_picture(project: Project, link: ProjectLink, data: bytes) -> ProjectFile | None:
    """Check, clean and store the picture the page designated; None if it cannot be.

    The same rules as a photo (docs/adr/0030): the type is found by decoding, SVG and every
    other format are refused, the picture is written again without metadata, and the quotas
    of the home count it. A refusal leaves the link without a picture, not without a link.
    """
    try:
        processed = images.process(data)
        stored = ProjectFile(
            home=project.home,
            project=project,
            name=task_files.clean_name(link.title or "link") or "link",
            content_type=processed.content_type,
            is_image=True,
            size_bytes=len(processed.data),
            width=processed.width,
            height=processed.height,
        )
        with transaction.atomic():
            save_stored(stored, processed.data, processed.thumbnail, processed.extension)
    except ValidationError:
        logger.info("The picture of a link could not be kept (invalid).")
        return None
    return stored


@transaction.atomic
def delete_link(actor: AnyUser, link: ProjectLink) -> None:
    """Soft-delete the link and erase its picture from the disk."""
    _require_write(actor, link.home)
    doomed = [link.image] if link.image is not None else []
    link.soft_delete()
    changelog.deleted(link)
    for stored in doomed:
        stored.soft_delete()
        changelog.deleted(stored)
    _erase_files(doomed)
    audit.record_object(
        "project_link.deleted",
        actor=actor,
        home_id=link.home_id,
        title=link.project.title,
        domain=preview.domain_of(link.url),
    )

"""Homes and memberships: every business table hangs off a home (docs/adr/0006)."""

from typing import Any, ClassVar, Self, TypeVar

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from housecairn.core.models import BaseModel, SoftDeleteManager, SoftDeleteQuerySet

_M = TypeVar("_M", bound=models.Model)


class Role(models.TextChoices):
    OWNER = "owner", _("Owner")
    MEMBER = "member", _("Member")
    READ_ONLY = "read_only", _("Read only")


WRITE_ROLES = frozenset({Role.OWNER, Role.MEMBER})


class Hemisphere(models.TextChoices):
    NORTH = "north", _("Northern")
    SOUTH = "south", _("Southern")


class Home(BaseModel):
    name = models.CharField(_("name"), max_length=120)
    currency = models.CharField(
        _("currency"),
        max_length=3,
        default="EUR",
        validators=[RegexValidator(r"^[A-Z]{3}$", _("Use an ISO 4217 code, such as EUR."))],
        help_text=_("ISO 4217 code of the currency used for this home's budget."),
    )
    # Which seasons the predefined ones mean (docs/adr/0045); never guessed from a place.
    hemisphere = models.CharField(
        _("hemisphere"),
        max_length=5,
        choices=Hemisphere.choices,
        default=Hemisphere.NORTH,
        # Also in the schema, so that a row written by an older state of the model has one.
        db_default=Hemisphere.NORTH,
        help_text=_(
            "Where the home is, for the seasons: spring in the north starts in March, "
            "in the south in September."
        ),
    )
    # Identifier of a single sign-on provider declared by the instance
    # administrator; when set, members open this home only in a session signed
    # in through that provider (docs/adr/0021). Empty: no requirement.
    required_provider = models.CharField(
        _("required sign-in provider"), max_length=100, blank=True, default=""
    )
    # When an owner archived the home (docs/adr/0079); empty: not archived. An archived home
    # is listed apart, sends no reminder and has an empty calendar feed, but stays readable,
    # editable and exportable when opened.
    archived_at = models.DateTimeField(_("archived at"), null=True, blank=True, editable=False)
    # The mark of the example home of the interactive guide (docs/adr/0084), set only by
    # `guide.services.create_example_home`. No form, no API field and no sync operation writes
    # it; a real home never becomes an example and the other way round. An example home never
    # leaves the instance: no change, no export, no backup, no list of homes (`real_homes`).
    is_example = models.BooleanField(
        _("Example home"), default=False, db_default=False, editable=False
    )

    class Meta:
        verbose_name = _("home")
        verbose_name_plural = _("homes")

    def __str__(self) -> str:
        return self.name

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class Membership(BaseModel):
    home = models.ForeignKey(Home, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(_("role"), max_length=16, choices=Role.choices, default=Role.MEMBER)

    class Meta:
        verbose_name = _("membership")
        verbose_name_plural = _("memberships")
        constraints = [
            models.UniqueConstraint(
                fields=["home", "user"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_active_membership",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.home} ({self.role})"


class Invitation(BaseModel):
    """An invitation to join a home with a role (docs/adr/0020).

    The link carries a random secret of which only the SHA-256 digest is
    stored, as for API tokens. An invitation is used once: accepted, revoked or
    expired, it opens nothing. Sending it again replaces the secret, so an
    older link stops working.
    """

    home = models.ForeignKey(Home, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField(_("email address"))
    role = models.CharField(_("role"), max_length=16, choices=Role.choices, default=Role.MEMBER)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    digest = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField(_("expires at"))
    sent_at = models.DateTimeField(_("sent at"))
    # True when the link went to the address by email and was shown to nobody:
    # opening it then proves the address belongs to whoever accepts.
    delivered_by_email = models.BooleanField(default=False, editable=False)
    accepted_at = models.DateTimeField(_("accepted at"), null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    revoked_at = models.DateTimeField(_("revoked at"), null=True, blank=True)

    class Meta:
        verbose_name = _("invitation")
        verbose_name_plural = _("invitations")
        constraints = [
            # One invitation at a time per home and address, settled by the
            # database: of two simultaneous requests, one fails. Lapsed ones
            # are revoked when a new invitation is made (services.invite).
            models.UniqueConstraint(
                Lower("email"),
                "home",
                condition=models.Q(
                    accepted_at__isnull=True, revoked_at__isnull=True, deleted_at__isnull=True
                ),
                name="unique_pending_invitation",
            )
        ]

    def __str__(self) -> str:
        return f"{self.email} → {self.home} ({self.role})"

    @property
    def is_pending(self) -> bool:
        """Neither accepted, revoked, deleted nor expired."""
        return (
            self.accepted_at is None
            and self.revoked_at is None
            and self.deleted_at is None
            and self.expires_at > timezone.now()
        )


class HomeOwnedQuerySet(SoftDeleteQuerySet[_M]):
    def in_homes(self, homes: models.QuerySet[Home]) -> Self:
        return self.filter(home__in=homes)


class HomeOwnedManager(SoftDeleteManager[_M]):
    def get_queryset(self) -> HomeOwnedQuerySet[_M]:
        return HomeOwnedQuerySet(self.model, using=self._db).alive()

    def in_homes(self, homes: models.QuerySet[Home]) -> HomeOwnedQuerySet[_M]:
        return self.get_queryset().in_homes(homes)


class HomeOwnedModel(BaseModel):
    """Base for every business model: belongs to exactly one home."""

    home = models.ForeignKey(Home, on_delete=models.CASCADE, related_name="+")

    objects: ClassVar[HomeOwnedManager[Any]] = HomeOwnedManager()

    class Meta:
        abstract = True


class ExportStatus(models.TextChoices):
    PENDING = "pending", _("Being made")
    READY = "ready", _("Ready")
    FAILED = "failed", _("Failed")


class ExportJob(BaseModel):
    """An archive of a home made in the background, kept a short time (docs/adr/0048).

    Its file is in `MEDIA_ROOT/exports/<home id>/`, which goes with the home. It is
    downloaded by an owner of the home, signed in, at most `downloads_left` times and
    until `expires_at`; then the file is removed.
    """

    home = models.ForeignKey(Home, on_delete=models.CASCADE, related_name="export_jobs")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    status = models.CharField(
        _("status"), max_length=10, choices=ExportStatus.choices, default=ExportStatus.PENDING
    )
    file_name = models.CharField(max_length=200, blank=True, editable=False)
    size_bytes = models.PositiveBigIntegerField(default=0)
    downloads_left = models.PositiveSmallIntegerField(default=3)
    expires_at = models.DateTimeField(_("expires at"))
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("export of a home")
        verbose_name_plural = _("exports of homes")


class ImportPreview(BaseModel):
    """The text of CSV files checked by a preview, kept until it is confirmed (docs/adr/0048).

    The confirmation reads the text from here rather than from the browser: a form of the
    files' text would be up to three times their size once encoded, past what Django reads
    (`DATA_UPLOAD_MAX_MEMORY_SIZE`). It belongs to one person and one home, is named by a
    signed token, expires after `csv_import.PREVIEW_MINUTES` minutes, and is removed once
    confirmed, when the same person previews again in the same home, and every hour.
    """

    home = models.ForeignKey(Home, on_delete=models.CASCADE, related_name="import_previews")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    # Table -> text of its file, as the preview read it.
    texts = models.JSONField(default=dict)
    # SHA-256 of `texts`, also in the signed token: the text confirmed is the text previewed.
    digest = models.CharField(max_length=64)
    expires_at = models.DateTimeField(_("expires at"))

    class Meta:
        verbose_name = _("preview of an import")
        verbose_name_plural = _("previews of imports")

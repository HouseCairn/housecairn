"""The ticks of the interactive guide, on the example home (docs/adr/0084)."""

import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from housecairn.homes.models import Home


class StepTick(models.Model):
    """A step of the guide that is done: stored as soon as its state is true, and it stays.

    It hangs off the example home, so that deleting the home deletes its ticks. It is never
    synced and never exported: an example home never leaves the instance.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    home = models.ForeignKey(Home, on_delete=models.CASCADE, related_name="step_ticks")
    # The id of a step of `guide.json` (`create-home.1`).
    step_id = models.CharField(_("step"), max_length=64)
    ticked_at = models.DateTimeField(_("ticked at"), default=timezone.now)

    class Meta:
        verbose_name = _("tick of a step")
        verbose_name_plural = _("ticks of steps")
        constraints = [
            models.UniqueConstraint(fields=["home", "step_id"], name="unique_step_tick"),
        ]

    def __str__(self) -> str:
        return f"{self.step_id} @ {self.home_id}"

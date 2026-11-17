"""What the sync API gives one member of a home: the rows the site shows them (docs/adr/0086).

A read-only member sees on the site the reports they made and no others
(`reports.services.visible_among`, the one rule, read here and not copied). The feed, the
snapshot and the files of the sync API give their phone the same: the rows of the other reports,
their changes and their photos are left out, as if they did not exist, and a photo of one
answers 404 as an id nobody holds. Every other row of the home is given to every member.

The view is read after the head of the home, in the same transaction: a change of role commits
its new view and the position it starts at together (`changes.view_changed`), so a page that
holds the new head was read with the new view.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from django.db import models

from housecairn.accounts.models import User
from housecairn.homes.models import Home
from housecairn.reports import services as report_services
from housecairn.reports.models import Report
from housecairn.sync.models import MemberView

REPORT = "reports.report"


@dataclass
class View:
    """What one member reads of a home: every report, or those of `reports` only."""

    # The reports the member may see; None: every report of the home.
    reports: models.QuerySet[Report] | None
    # The first position a cursor of the member's phones may be at (`MemberView`).
    since: int = 0
    _ids: set[uuid.UUID] | None = field(default=None, repr=False)

    @property
    def is_whole(self) -> bool:
        return self.reports is None

    def report_ids(self) -> set[uuid.UUID]:
        """The ids of the reports the member may see, read once."""
        if self.reports is None:  # pragma: no cover - asked of a narrowed view only
            raise ValueError("Every report is visible.")
        if self._ids is None:
            self._ids = set(self.reports.values_list("pk", flat=True))
        return self._ids

    def hides(self, entity: str, row_id: Any) -> bool:
        """Whether the row `row_id` of type `entity` is left out for this member."""
        if self.reports is None or entity != REPORT:
            return False
        return row_id not in self.report_ids()

    def changes(self, changes: models.QuerySet[Any]) -> models.QuerySet[Any]:
        """The changes of the feed this member may read, filtered in the query.

        In the query and not after it, so that a page holds `limit` changes the member reads,
        and `more` says whether another such change follows.
        """
        if self.reports is None:
            return changes
        return changes.filter(
            ~models.Q(entity=REPORT) | models.Q(row_id__in=self.reports.values("pk"))
        )


def view_of(user: User, home: Home) -> View:
    """The view of `user` in `home` now, read from the rule of the site."""
    since = (
        MemberView.objects.filter(home_id=home.pk, user_id=user.pk)
        .values_list("since", flat=True)
        .first()
    ) or 0
    if report_services.sees_every_report(user, home):
        return View(None, since)
    # Every row the server keeps, deleted ones included: a phone gets the tombstone of its
    # own report as it got the report.
    kept = Report._base_manager.filter(home_id=home.pk)
    return View(report_services.visible_among(user, home, kept), since)

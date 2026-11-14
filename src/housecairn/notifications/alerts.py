"""Alerts that come from an event rather than a due date (docs/adr/0043, 0047).

Today there is one: a problem was reported. It goes, in the background, to every member
of the home who may write, has an active account and has a channel on, except the person
who reported it. The message holds the title the reporter typed, the home and a link, as a
reminder holds the title of a task: nothing else leaves the instance. Quiet hours apply: a
person who asked for silence at night finds the report on Today in the morning.
"""

import logging
from collections import defaultdict
from uuid import UUID

from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from housecairn.core.language import language_of
from housecairn.core.links import external_url
from housecairn.homes.models import WRITE_ROLES, Membership
from housecairn.instance.config import get_setting
from housecairn.notifications import devices, outbound, reminders, services
from housecairn.notifications.models import Channel, Preference

logger = logging.getLogger("housecairn.notifications")


def _link(home_id: object, report_id: object) -> str:
    site = str(get_setting("site_url") or "")
    path = reverse("reports:detail", kwargs={"home_id": home_id, "report_id": report_id})
    return external_url(path, site)


def report_created(report_id: str) -> int:
    """Send the alert for one report; the number of people it was sent to."""
    from housecairn.reports.models import Report

    if not services.is_enabled():
        return 0
    report = Report.objects.select_related("home").filter(pk=report_id).first()
    if report is None:
        return 0
    people = (
        Membership.objects.filter(home=report.home, role__in=WRITE_ROLES, user__is_active=True)
        .exclude(user_id=report.reporter_id)
        .values_list("user_id", flat=True)
    )
    channels: dict[UUID, list[Channel]] = defaultdict(list)
    for channel in Channel.objects.filter(enabled=True, user_id__in=list(people)).select_related(
        "user"
    ):
        channels[channel.user_id].append(channel)
    allowed = set(people)
    pushed = {k: v for k, v in devices.reachable().items() if k in allowed}
    for user_id in pushed:
        channels[user_id]  # a device is a way to reach the person, as a channel is
    if not channels:
        return 0
    prefs = {p.user_id: p for p in Preference.objects.filter(user_id__in=list(channels))}
    now = timezone.now()
    sent = 0
    for user_id, user_channels in channels.items():
        person = user_channels[0].user if user_channels else pushed[user_id][0].user
        pref = prefs.get(user_id) or Preference(user=person)
        if reminders.is_quiet(pref, now.astimezone(reminders.zone_of(pref))):
            continue
        with translation.override(language_of(person)):
            title = _("HouseCairn: problem reported")
            body = _("%(title)s, %(home)s") % {
                "title": reminders.one_line(report.title),
                "home": reminders.one_line(report.home.name),
            }
        text = body
        link = _link(report.home_id, report.id)
        if link:
            body = f"{body}\n{link}"
        results = [outbound.send(c.url, title=title, body=body) for c in user_channels]
        if user_id in pushed:
            path = reverse(
                "reports:detail", kwargs={"home_id": report.home_id, "report_id": report.id}
            )
            results += devices.send_all(
                pushed[user_id], kind="report", title=title, body=text, path=path
            )
        if any(results):  # every channel is tried, whatever the first said
            sent += 1
    return sent

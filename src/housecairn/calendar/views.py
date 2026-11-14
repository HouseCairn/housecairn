"""Web views of the calendar (week, month, year), its feeds and the feed itself (ADR 0052).

Rules live in `services` and `feeds`. The three period views only choose a range and lay
it out; Done and Undo are the buttons of the tasks, which return to the page they came from.
"""

import calendar as pycalendar
import datetime
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from dateutil.relativedelta import relativedelta
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseNotModified
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.utils.decorators import method_decorator
from django.utils.http import parse_etags
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from django.views.generic import TemplateView

from housecairn.accounts.models import User
from housecairn.calendar import feeds, ics, services
from housecairn.calendar.models import Feed
from housecairn.core.links import external_url
from housecairn.homes import current
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.homes.scoping import allow, site_url
from housecairn.rooms.models import Room
from housecairn.tasks.models import CLOSED_STATUSES, Occurrence, Priority

FEED_RATE_ACTION = "housecairn_calendar_feed"
FEED_CACHE = "private, max-age=900"
SPAN_YEARS = 100


def _date(raw: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


def _query(params: dict[str, str], **extra: str) -> str:
    return urlencode({k: v for k, v in {**params, **extra}.items() if v})


def _add_months(day: datetime.date, count: int) -> datetime.date:
    year, month = divmod(day.year * 12 + day.month - 1 + count, 12)
    return datetime.date(year, month + 1, 1)


class IndexView(LoginRequiredMixin, View):
    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return redirect("calendar:week")


class PeriodView(LoginRequiredMixin, TemplateView):
    """A range of days of the homes and with the filters chosen in the query."""

    granularity = ""
    template_name = "calendar/calendar.html"
    request: HttpRequest

    def span(self, anchor: datetime.date) -> tuple[datetime.date, datetime.date]:
        raise NotImplementedError  # pragma: no cover

    def step(self, anchor: datetime.date, direction: int) -> datetime.date:
        raise NotImplementedError  # pragma: no cover

    def layout(self, anchor: datetime.date, items: list[services.Entry]) -> dict[str, Any]:
        raise NotImplementedError  # pragma: no cover

    def chosen_homes(self, homes: list[Home]) -> list[Home]:
        """The home of `?home=`, else the current home, else all homes ("All homes").

        A home in the address that is not one of the person's answers 404; one that is
        becomes the current home, as a page of a home does (`homes.current`).
        """
        raw = self.request.GET.get("home", "")
        try:
            wanted = UUID(raw) if raw else None
        except ValueError:
            wanted = None
        if wanted is not None:
            chosen = [h for h in homes if h.id == wanted]
            if not chosen:
                raise Http404
            current.remember(self.request, chosen[0])
            return chosen
        found = current.for_request(self.request)
        if found.all_homes or found.home is None:
            return homes
        return [found.home]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        user: User = self.request.user  # type: ignore[assignment]
        get = self.request.GET
        homes = current.for_request(self.request).homes
        chosen = self.chosen_homes(homes)
        today = services.today_of(user)
        reach = relativedelta(years=SPAN_YEARS)
        anchor = min(max(_date(get.get("date", "")) or today, today - reach), today + reach)
        start, end = self.span(anchor)
        result = services.entries(user, chosen, start, end, services.filters_from(get))
        params = {k: get.get(k, "") for k in ("home", "room", "person", "priority")}
        base = reverse(f"calendar:{self.granularity}")
        context: dict[str, Any] = {
            "granularity": self.granularity,
            "anchor": anchor,
            "today": today,
            "homes": homes,
            "chosen_homes": chosen,
            "rooms": Room.objects.filter(home__in=chosen).select_related("home").order_by("name"),
            "people": services.people_of(chosen),
            "priorities": Priority.choices,
            "filters": params,
            "truncated": result.truncated,
            "prev_url": f"{base}?{_query(params, date=self.step(anchor, -1).isoformat())}",
            "next_url": f"{base}?{_query(params, date=self.step(anchor, 1).isoformat())}",
            "today_url": base + ("?" + _query(params) if _query(params) else ""),
            "week_base": reverse("calendar:week")
            + "?"
            + "".join(f"{_query(params)}&" if _query(params) else ""),
            "views": [
                (
                    name,
                    label,
                    f"{reverse(f'calendar:{name}')}?{_query(params, date=anchor.isoformat())}",
                )
                for name, label in (
                    ("week", _("Week")),
                    ("month", _("Month")),
                    ("year", _("Year")),
                )
            ],
            "undo": self.undo_banner(chosen),
            "undo_return": f"{self.request.path}?"
            + urlencode([(k, v) for k, v in get.items() if k != "undo"]),
            **self.layout(anchor, result.entries),
        }
        return super().get_context_data(**{**context, **kwargs})

    def undo_banner(self, homes: list[Home]) -> Occurrence | None:
        """The occurrence just closed, whose id came back in `?undo=`, if it can be undone."""
        try:
            pk = UUID(self.request.GET.get("undo", ""))
        except ValueError:
            return None
        found: Occurrence | None = (
            Occurrence.objects.filter(pk=pk, home__in=homes, status__in=CLOSED_STATUSES)
            .select_related("home")
            .first()
        )
        if found is None or not home_services.can_write(self.request.user, found.home):
            return None
        return found


class WeekView(PeriodView):
    granularity = "week"

    def span(self, anchor: datetime.date) -> tuple[datetime.date, datetime.date]:
        first = services.week_start(anchor)
        return first, first + datetime.timedelta(days=6)

    def step(self, anchor: datetime.date, direction: int) -> datetime.date:
        return anchor + datetime.timedelta(days=7 * direction)

    def layout(self, anchor: datetime.date, items: list[services.Entry]) -> dict[str, Any]:
        first, _last = self.span(anchor)
        grouped = services.by_day(items)
        days = [first + datetime.timedelta(days=n) for n in range(7)]
        return {"days": [(day, grouped.get(day, [])) for day in days], "total": len(items)}


class MonthView(PeriodView):
    granularity = "month"

    def span(self, anchor: datetime.date) -> tuple[datetime.date, datetime.date]:
        first = anchor.replace(day=1)
        last = first.replace(day=pycalendar.monthrange(first.year, first.month)[1])
        return services.week_start(first), services.week_start(last) + datetime.timedelta(days=6)

    def step(self, anchor: datetime.date, direction: int) -> datetime.date:
        return _add_months(anchor, direction)

    def layout(self, anchor: datetime.date, items: list[services.Entry]) -> dict[str, Any]:
        start, end = self.span(anchor)
        grouped = services.by_day(items)
        weeks = []
        for offset in range(0, (end - start).days + 1, 7):
            week = []
            for n in range(7):
                day = start + datetime.timedelta(days=offset + n)
                week.append(
                    {
                        "date": day,
                        "in_month": day.month == anchor.month,
                        "entries": grouped.get(day, []),
                    }
                )
            weeks.append(week)
        listed = [(day, rows) for day, rows in sorted(grouped.items()) if day.month == anchor.month]
        return {"weeks": weeks, "days": listed, "total": sum(len(rows) for _d, rows in listed)}


class YearView(PeriodView):
    granularity = "year"

    def span(self, anchor: datetime.date) -> tuple[datetime.date, datetime.date]:
        return datetime.date(anchor.year, 1, 1), datetime.date(anchor.year, 12, 31)

    def step(self, anchor: datetime.date, direction: int) -> datetime.date:
        return datetime.date(anchor.year + direction, 1, 1)

    def layout(self, anchor: datetime.date, items: list[services.Entry]) -> dict[str, Any]:
        months = []
        for number in range(1, 13):
            first = datetime.date(anchor.year, number, 1)
            rows = [e for e in items if e.day.month == number]
            months.append(
                {
                    "date": first,
                    "total": len(rows),
                    "counts": services.counts_of(rows),
                    "url": reverse("calendar:month") + "?" + _query({}, date=first.isoformat()),
                }
            )
        return {"months": months, "total": len(items)}


# ---------- feeds ----------


@method_decorator(never_cache, name="dispatch")
class FeedsView(LoginRequiredMixin, TemplateView):
    """The feeds of the person, one line per home and the combined one."""

    template_name = "calendar/feeds.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        user: User = self.request.user  # type: ignore[assignment]
        existing = {f.home_id: f for f in feeds.feeds_of(user)}
        rows = [(None, existing.get(None))] + [
            (home, existing.get(home.id))
            for home in feeds.feed_homes(user)
            # An archived home offers no new feed; one that has a feed keeps its line, to
            # regenerate or revoke it (ADR 0079).
            if home.archived_at is None or home.id in existing
        ]
        return super().get_context_data(rows=rows, **kwargs)

    def address(self, raw: str) -> str:
        return external_url(reverse("calendar:feed", kwargs={"token": raw}), site_url(self.request))

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        home = None
        if request.POST.get("home"):
            try:
                home = get_object_or_404(feeds.feed_homes(request.user), pk=request.POST["home"])  # type: ignore[arg-type]
            except ValidationError:
                raise Http404 from None
        try:
            feed, raw = feeds.create_feed(request.user, home)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("calendar:feeds")
        return self.render_to_response(
            self.get_context_data(new_feed=feed, new_url=self.address(raw))
        )


class FeedActionView(LoginRequiredMixin, View):
    request: HttpRequest

    def get_feed(self) -> Feed:
        found: Feed = get_object_or_404(
            feeds.feeds_of(self.request.user), id=self.kwargs["feed_id"]
        )
        return found


@method_decorator(never_cache, name="dispatch")
class FeedRegenerateView(FeedActionView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        feed, raw = feeds.regenerate_feed(request.user, self.get_feed())
        page = FeedsView(request=request, kwargs={})
        address = page.address(raw)
        return page.render_to_response(page.get_context_data(new_feed=feed, new_url=address))


class FeedRevokeView(FeedActionView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        feeds.revoke_feed(request.user, self.get_feed())
        messages.success(request, _("Feed revoked: its address no longer works."))
        return redirect("calendar:feeds")


def _neutral_not_found() -> HttpResponse:
    """The same answer for a secret that never existed and for one that was revoked."""
    response = HttpResponse("Not found\n", status=404, content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "no-store"
    return response


@require_safe
def feed(request: HttpRequest, token: str) -> HttpResponse:
    """The iCalendar document at a secret address. Not signed in: the secret is the key.

    It opens this feed and nothing else; it is not an API token. Rate limited by client address.
    """
    if not allow(request, FEED_RATE_ACTION):
        limited = HttpResponse("Too many requests\n", status=429, content_type="text/plain")
        limited["Retry-After"] = "60"
        limited["Cache-Control"] = "no-store"
        return limited
    found = feeds.find_feed(token)
    if found is None:
        return _neutral_not_found()
    body, etag = ics.cached_render(found, site_url(request))
    if etag in parse_etags(request.headers.get("If-None-Match", "")):
        response: HttpResponse = HttpResponseNotModified()
    else:
        response = HttpResponse(body, content_type="text/calendar; charset=utf-8")
        response["Content-Disposition"] = 'inline; filename="housecairn.ics"'
    response["ETag"] = etag
    patch_cache_control(response, private=True, max_age=900)
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response

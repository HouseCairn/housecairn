"""The pages of the interactive guide: the scenarios, the steps, and the example home's end.

The guide is a reading of the data of the example home (docs/adr/0084): it stores no current
step, only the ticks, which `states.tick` writes as soon as a state is true. Every page here
reads the example home of the person, never a home from the address, so that nothing done
from these pages can reach a real home.
"""

from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import TemplateView

from housecairn.accounts.models import User
from housecairn.core import navigation
from housecairn.guide import content, links, services, states, terms
from housecairn.homes import services as home_services
from housecairn.homes.models import Home

# The query that shows the confirmation of Delete the example home, in place of its button.
CONFIRM_DELETE = "delete-example"


def _language() -> str:
    return content.language_in_file(translation.get_language())


def _numbered() -> list[tuple[int, dict[str, Any]]]:
    return list(enumerate(content.scenarios(), start=1))


def _scenario(scenario_id: str) -> tuple[int, dict[str, Any]]:
    for number, scenario in _numbered():
        if scenario["id"] == scenario_id:
            return number, scenario
    raise Http404


def _home_of(request: HttpRequest) -> Home | None:
    """The example home of the person, its ticks brought up to date."""
    home = services.example_home_of(request.user)  # type: ignore[arg-type]
    if home is not None:
        states.tick(home)
    return home


def _counted(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    """The steps a scenario counts in its progress: the optional ones do not."""
    return [step for step in scenario["steps"] if not step.get("optional")]


def confirming(request: HttpRequest) -> bool:
    """Whether the page shows the confirmation of Delete the example home."""
    return request.GET.get(CONFIRM_DELETE) == "1"


def delete_context(request: HttpRequest, home: Home) -> dict[str, Any]:
    """What the button Delete the example home and its confirmation need, on any page."""
    path = request.path
    query = request.GET.copy()
    query.pop(CONFIRM_DELETE, None)
    here = f"{path}?{query.urlencode()}" if query else path
    query[CONFIRM_DELETE] = "1"
    return {
        "example_home": home,
        "confirm_url": f"{path}?{query.urlencode()}",
        "cancel_url": here,
        "confirming": confirming(request),
        "delete_url": reverse("guide:delete", kwargs={"home_id": home.pk}),
        "next": navigation.clean_next(here),
    }


class GuideView(LoginRequiredMixin, TemplateView):
    """The five scenarios, each with its progress, and Delete the example home."""

    template_name = "guide/index.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        home = _home_of(self.request)
        ticked = states.ticked(home) if home else set()
        language = _language()
        rows = []
        for number, scenario in _numbered():
            counted = _counted(scenario)
            done = sum(1 for step in counted if step["id"] in ticked)
            rows.append(
                {
                    "number": number,
                    "title": content.text(scenario["title"], language),
                    "summary": terms.render(content.text(scenario["summary"], language)),
                    "url": reverse("guide:scenario", kwargs={"scenario": scenario["id"]}),
                    "done": done,
                    "total": len(counted),
                    "finished": done == len(counted),
                }
            )
        context = super().get_context_data(home=home, scenarios=rows, **kwargs)
        if home is not None:
            context["delete"] = delete_context(self.request, home)
        return context


class ScenarioView(LoginRequiredMixin, TemplateView):
    """The steps of one scenario, ticked by the evaluator, each leading to its screen."""

    template_name = "guide/scenario.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        number, scenario = _scenario(self.kwargs["scenario"])
        home = _home_of(self.request)
        ticked = states.ticked(home) if home else set()
        language = _language()
        here = reverse("guide:scenario", kwargs={"scenario": scenario["id"]})
        steps = []
        for step in scenario["steps"]:
            to = links.for_step(step, home, language, here)
            steps.append(
                {
                    "id": step["id"],
                    "number": step["id"].rsplit(".", 1)[-1],
                    "title": content.text(step["title"], language),
                    "explain": terms.render(content.text(step["explain"], language)),
                    "ticked": step["id"] in ticked,
                    "manual": bool(step.get("manual")),
                    "optional": bool(step.get("optional")),
                    "open": to.open,
                    "fill": to.fill,
                }
            )
        earlier = [
            step
            for _number, other in _numbered()[: number - 1]
            for step in _counted(other)
            if "done_when" in step
        ]
        catch_up = number > 1 and (home is None or any(s["id"] not in ticked for s in earlier))
        scenarios = _numbered()
        return super().get_context_data(
            home=home,
            number=number,
            scenario_id=scenario["id"],
            title=content.text(scenario["title"], language),
            summary=terms.render(content.text(scenario["summary"], language)),
            steps=steps,
            catch_up=catch_up,
            previous=_neighbour(scenarios, number - 1, language),
            following=_neighbour(scenarios, number + 1, language),
            **kwargs,
        )


def _neighbour(
    scenarios: list[tuple[int, dict[str, Any]]], number: int, language: str
) -> dict[str, Any] | None:
    if not 1 <= number <= len(scenarios):
        return None
    scenario = scenarios[number - 1][1]
    return {
        "title": content.text(scenario["title"], language),
        "url": reverse("guide:scenario", kwargs={"scenario": scenario["id"]}),
    }


class FillView(LoginRequiredMixin, View):
    """Add what the earlier scenarios make: the example home, then the rows it lacks."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        number, scenario = _scenario(kwargs["scenario"])
        if number < 2:
            raise Http404
        user: User = request.user  # type: ignore[assignment]
        home = services.example_home_of(user)
        if home is None:
            home = services.create_example_home(user, language=_language())
        try:
            services.fill_example(user, home, up_to=number, language=_language())
        except PermissionDenied as exc:
            raise Http404 from exc
        messages.success(request, _("The earlier scenarios are done in the example home."))
        return redirect("guide:scenario", scenario=scenario["id"])


class TickView(LoginRequiredMixin, View):
    """Tick by hand a step that has no state of its own (a reading step, an undo)."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        step_id = request.POST.get("step", "")
        scenario_id = step_id.split(".", 1)[0]
        _scenario(scenario_id)
        home = services.example_home_of(request.user)  # type: ignore[arg-type]
        if home is None:
            raise Http404
        try:
            services.tick_by_hand(request.user, home, step_id)  # type: ignore[arg-type]
        except (ValueError, PermissionDenied) as exc:
            raise Http404 from exc
        return redirect(
            navigation.back_url(
                request, reverse("guide:scenario", kwargs={"scenario": scenario_id})
            )
        )


class DeleteExampleView(LoginRequiredMixin, View):
    """Delete the example home: one press, one confirmation, no name to type, no export.

    Only the example home of the person, who made it and owns it, goes this way: any other
    home of the address, real or of another account, answers the same 404 as a home that
    does not exist, and `remove_example` refuses a real home even so.
    """

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        home = services.example_home_of(request.user)  # type: ignore[arg-type]
        if (
            home is None
            or str(home.pk) != str(kwargs["home_id"])
            or not home_services.is_owner(request.user, home)
        ):
            raise Http404
        try:
            services.remove_example(request.user, home)  # type: ignore[arg-type]
        except (ValueError, PermissionDenied, ValidationError) as exc:  # pragma: no cover
            raise Http404 from exc
        messages.success(request, _("The example home was deleted."))
        return redirect(navigation.after_delete(request, reverse("guide:index"), [home.pk]))

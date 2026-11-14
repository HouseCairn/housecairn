import hashlib
import json
from functools import cache
from importlib import metadata
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.exceptions import DisallowedHost
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import get_script_prefix, reverse
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control, never_cache
from django.views.decorators.http import require_safe
from django.views.defaults import bad_request as default_bad_request
from django.views.generic import TemplateView

from housecairn.core import more, offline, services
from housecairn.core.language import shipped
from housecairn.core.prefix import STATIC_PATH
from housecairn.instance.config import instance_name


class IndexView(TemplateView):
    """The one home of the application (docs/developer/design-guide.md, Today).

    Signed in, it is Today: Today of the current home, or all the homes of the person
    together when they chose "All homes" in the selector (and before they chose a home).
    The welcome page is for a signed-out visitor and for an account with no home to open.
    """

    template_name = "core/index.html"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if request.user.is_authenticated:
            from housecairn.homes import current
            from housecairn.tasks.views import MyHomeView

            chosen = current.for_request(request)
            if chosen.all_homes:
                return cast(HttpResponse, MyHomeView.as_view()(request))
            if chosen.home is not None:
                return redirect("tasks:today", home_id=chosen.home.pk)
        return super().get(request, *args, **kwargs)


class MoreView(LoginRequiredMixin, TemplateView):
    """Everything that is not a daily gesture, kept out of the navigation bar."""

    template_name = "core/more.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from housecairn.homes import current

        # The Home group is about the home being viewed; More has none in its address,
        # so it is the current home (docs/developer/design-guide.md, "Current home").
        home = current.for_request(self.request).home
        home_id = home.pk if home else None
        from housecairn.reports.services import waiting_for

        return super().get_context_data(
            groups=more.for_page(
                self.request.user, home_id, "more", waiting_for(self.request, home_id)
            ),
            **kwargs,
        )


class SecurityView(LoginRequiredMixin, TemplateView):
    """Security and sign-in: second factor, password, addresses; deleting the account last."""

    template_name = "core/security.html"


class AboutView(TemplateView):
    template_name = "core/about.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        try:
            version = metadata.version("housecairn")
        except metadata.PackageNotFoundError:  # pragma: no cover - run from a bare checkout
            version = "dev"
        context.update(version=version, source_url=settings.HOUSECAIRN_SOURCE_URL)
        return context


@never_cache
@require_safe
def healthz(request: HttpRequest) -> HttpResponse:
    """Liveness and database check, without authentication or details."""
    if services.database_is_available():
        return JsonResponse({"status": "ok"})
    return JsonResponse({"status": "unavailable"}, status=503)


@cache_control(max_age=3600, public=True)
@require_safe
def manifest(request: HttpRequest) -> HttpResponse:
    """Web app manifest: name, colours and icons, so the app can be added to a home screen.

    What each browser needs to offer the installation is in docs/adr/0065: a name, a
    `start_url`, `display: standalone`, icons of 192 and 512 pixels. `id` keeps the
    installed application the same if its start address ever changes; the `maskable` icons
    fill the round or rounded shape of Android launchers without a white frame.
    """
    icons = [
        {
            "src": static(f"img/brand/{name}-{size}.png"),
            "sizes": f"{size}x{size}",
            "type": "image/png",
            "purpose": purpose,
        }
        for name, purpose in (("icon", "any"), ("icon-maskable", "maskable"))
        for size in (192, 512)
    ]
    data = {
        # One installed application per instance and prefix (docs/adr/0065).
        "id": reverse("index"),
        # The name of the instance (docs/adr/0042); JSON escapes it.
        "name": instance_name(),
        "short_name": instance_name(),
        "description": _("What needs doing in the house, where, when, with what and for how much."),
        "lang": request.LANGUAGE_CODE,
        "start_url": reverse("index"),
        # Under a path prefix, the installed application stays in it (docs/adr/0055).
        "scope": get_script_prefix(),
        "display": "standalone",
        "background_color": "#F7F5F1",
        "theme_color": "#F7F5F1",
        "icons": icons,
    }
    # Escaped like `json_script`: the name of the instance is chosen by an administrator.
    body = json.dumps(data).translate(
        {ord("<"): "\\u003c", ord(">"): "\\u003e", ord("&"): "\\u0026"}
    )
    return HttpResponse(body, content_type="application/manifest+json")


def bad_request(request: HttpRequest, exception: Exception) -> HttpResponse:
    """The 400 page; for a host name the instance refuses, it says what to change.

    Any other 400 is Django's own page, unchanged. Here the page gets only the sentence
    of `HOUSECAIRN_DISALLOWED_HOST_HINT` (empty: the generic one): not the `Host` header
    that was refused, not `ALLOWED_HOSTS`, not the message of the exception.
    """
    if not isinstance(exception, DisallowedHost):
        return default_bad_request(request, exception)
    return render(
        request,
        "400.html",
        {"disallowed_host": True, "host_hint": settings.HOUSECAIRN_DISALLOWED_HOST_HINT},
        status=400,
    )


# Kept by the service worker when it is installed, so that the offline page has its look
# (docs/adr/0065). Every other static file is kept the first time a page asks for it.
PRECACHE: tuple[str, ...] = (
    "css/tokens.css",
    "css/fonts.css",
    "css/app.css",
    "img/brand/favicon.svg",
    "img/brand/housecairn-icon.svg",
    "js/offline.js",
)
# The service worker itself, part of its version: a change of its code is a new version.
SERVICE_WORKER_TEMPLATE = "core/service-worker.js"


def _static_version() -> str:
    """A fingerprint of the static files: a new one makes a new cache of static files.

    In the image, the manifest of the hashed names (collectstatic) changes with any file;
    without it (development, tests) the files found are read.
    """
    digest = hashlib.sha256()
    manifest_name = getattr(staticfiles_storage, "manifest_name", None)
    if manifest_name and staticfiles_storage.exists(manifest_name):
        with staticfiles_storage.open(manifest_name) as handle:
            digest.update(handle.read())
    else:
        for finder in finders.get_finders():
            for path, storage in sorted(finder.list([]), key=lambda found: found[0]):
                digest.update(path.encode())
                with storage.open(path) as handle:
                    digest.update(handle.read())
    digest.update((Path(settings.BASE_DIR) / "templates" / SERVICE_WORKER_TEMPLATE).read_bytes())
    return digest.hexdigest()[:16]


@cache
def _cached_static_version() -> str:
    return _static_version()


@require_safe
def service_worker(request: HttpRequest) -> HttpResponse:
    """The service worker, at the root of the application so that its scope is all of it.

    Under a path prefix the root is the prefix, and so is the scope (docs/adr/0055, 0065).
    `no-cache`: the browser asks again at each visit, and a new version is installed at once.
    """
    scope = get_script_prefix()
    offline_pages = {
        code: f"{reverse('offline')}?{urlencode({'lang': code})}"
        for code, _name in settings.LANGUAGES
    }
    config = {
        "scope": scope,
        "version": _static_version() if settings.DEBUG else _cached_static_version(),
        "precache": [static(name) for name in PRECACHE]
        + [reverse("offline"), *offline_pages.values()],
        "offline": reverse("offline"),
        # The offline page in each language, for the person's (docs/adr/0074).
        "offlinePages": offline_pages,
        "start": reverse("index"),
        "signOut": reverse("account_logout"),
        "static": scope + STATIC_PATH,
        "header": offline.HEADER,
        "purge": offline.PURGE,
        # Shown with a notification on the device (docs/adr/0066).
        "icon": static("img/brand/icon-192.png"),
    }
    body = render_to_string(SERVICE_WORKER_TEMPLATE, {"config": json.dumps(config)})
    response = HttpResponse(body, content_type="text/javascript; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = scope
    return response


@require_safe
def offline_page(request: HttpRequest) -> HttpResponse:
    """The page shown offline for any page that was not kept.

    The service worker keeps it for everyone who uses the browser, beyond a sign-out: it is
    rendered without the request, so with nothing of the person who installed it (no
    navigation, no home, no form token).
    """
    asked = request.GET.get("lang")
    with translation.override(asked if shipped(asked) else translation.get_language()):
        body = render_to_string("core/offline.html", {"start_url": reverse("index")})
    return HttpResponse(body)

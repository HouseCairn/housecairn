"""Root URLs. Address conventions are in docs/adr/0010."""

from allauth.account.decorators import secure_admin_login
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from django.views.i18n import set_language
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerSplitView

from housecairn.accounts import views as account_views
from housecairn.budget import views as budget_views
from housecairn.core import views as core_views
from housecairn.homes import views as home_views
from housecairn.instance import views as instance_views
from housecairn.labels import views as label_views
from housecairn.projects import views as project_views
from housecairn.rooms import views as room_views
from housecairn.stock import views as stock_views
from housecairn.tasks import views as task_views

# The administration site signs in through allauth's page only, with its second factor
# and its rate limits: Django's own form would take the password alone (docs/adr/0022).
# Signed out, /admin/login/ redirects there and reads no password; signed in, it lets
# staff through and answers 403 to anyone else.
admin.site.login = secure_admin_login(admin.site.login)  # type: ignore[method-assign]

handler400 = "housecairn.core.views.bad_request"

urlpatterns = [
    path("", core_views.IndexView.as_view(), name="index"),
    path("healthz", core_views.healthz, name="healthz"),
    path("manifest.webmanifest", core_views.manifest, name="manifest"),
    # The service worker at the root, so that its scope is the application (docs/adr/0065).
    path("service-worker.js", core_views.service_worker, name="service-worker"),
    path("offline/", core_views.offline_page, name="offline"),
    # Signed out, the language of the pages (docs/adr/0074).
    path("language/", set_language, name="set_language"),
    path("more/", core_views.MoreView.as_view(), name="more"),
    path("more/security/", core_views.SecurityView.as_view(), name="more-security"),
    path("about/", core_views.AboutView.as_view(), name="about"),
    path("h/", include("housecairn.homes.urls")),
    # Rooms, items and photos of a home (docs/adr/0029); /rooms/ is the navigation entry.
    path("h/<uuid:home_id>/", include("housecairn.rooms.urls")),
    path("rooms/", room_views.EntryView.as_view(), name="rooms"),
    # Tasks of a home (docs/adr/0031); /tasks/ is the navigation entry.
    path("h/<uuid:home_id>/", include("housecairn.tasks.urls")),
    path("tasks/", task_views.EntryView.as_view(), name="tasks"),
    # My home (what concerns the person, in every home) and their display preferences
    # (docs/adr/0037).
    path(
        "me/",
        RedirectView.as_view(pattern_name="index", query_string=True),
        name="my-home",
    ),
    path(
        "me/preferences/",
        task_views.PreferencesView.as_view(),
        name="display-preferences",
    ),
    path("me/profile/", account_views.ProfileView.as_view(), name="profile"),
    # Notification channels and reminders of the person (docs/adr/0043).
    path("me/notifications/", include("housecairn.notifications.urls")),
    # API tokens of the person: list, creation (secret shown once), revocation.
    path("me/tokens/", include("housecairn.apitokens.urls")),
    # Calendar (week, month, year) and its secret feeds (docs/adr/0052).
    path("calendar/", include("housecairn.calendar.urls")),
    # Stock items, suppliers and movements of a home (docs/adr/0038); /stock/ is the entry.
    path("h/<uuid:home_id>/", include("housecairn.stock.urls")),
    path("stock/", stock_views.EntryView.as_view(), name="stock"),
    # QR labels of a home (docs/adr/0047). A sticker opens /l/<code>/, which carries
    # no home: signed in or not, the answer never says whether the code exists.
    path("h/<uuid:home_id>/", include("housecairn.labels.urls")),
    # Problem reports of a home (docs/adr/0047): every member reports, a writer follows up.
    path("h/<uuid:home_id>/", include("housecairn.reports.urls")),
    path("l/<str:code>/", label_views.OpenView.as_view(), name="label-open"),
    # Projects, budget lines and quotes of a home (docs/adr/0050); /projects/ is the entry.
    path("h/<uuid:home_id>/", include("housecairn.projects.urls")),
    path("projects/", project_views.EntryView.as_view(), name="projects"),
    # The budget of a home, by room, project and task (docs/adr/0051); /budget/ is the entry.
    path("h/<uuid:home_id>/", include("housecairn.budget.urls")),
    path("budget/", budget_views.EntryView.as_view(), name="budget"),
    # Invitation links, opened signed in or not (docs/adr/0020).
    path("invitations/<str:token>/", home_views.InvitationView.as_view(), name="invitation"),
    # Instance invitation links: an account, without a home (docs/adr/0022).
    path(
        "join/<str:token>/",
        instance_views.InstanceInvitationView.as_view(),
        name="instance-invitation",
    ),
    path("instance/", include("housecairn.instance.urls")),
    # The first administrator, with the code of the process log (docs/adr/0056).
    path("setup/", account_views.SetupView.as_view(), name="setup"),
    # Password reset sends an email: no page without an outgoing mail server.
    # These patterns come first and carry no name, so allauth's own names still
    # reverse to the same addresses.
    path("accounts/delete/", account_views.AccountDeleteView.as_view(), name="account_delete"),
    # Sign-in opens the session before its passkey challenge (docs/adr/0018). Same address
    # and name as allauth's, so that its help link (core.help) still finds it.
    path("accounts/login/", account_views.login, name="account_login"),
    # Sign-up carries the anti-robot check (docs/adr/0024); same address, allauth's name.
    path("accounts/signup/", account_views.SignupView.as_view()),
    # Adding an address sends it a verification: no page without an outgoing mail server.
    path("accounts/email/", account_views.email_addresses),
    path("accounts/password/reset/", account_views.password_reset),
    path("accounts/password/reset/done/", account_views.password_reset_done),
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
    path("api/v1/", include("housecairn.api_urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path(
        "api/docs/",
        SpectacularSwaggerSplitView.as_view(
            url_name="api-schema", template_name="api/swagger_ui.html"
        ),
        name="api-docs",
    ),
]

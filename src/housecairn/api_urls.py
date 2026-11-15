"""API v1 routes (docs/adr/0004). Mounted at /api/v1/ with the namespace "api-v1"."""

from django.urls import include, path
from rest_framework.routers import SimpleRouter

from housecairn.accounts.api import AccountDeleteView, MeView
from housecairn.apitokens.api import TokenDetailView, TokenListView
from housecairn.apitokens.sync import SyncInfoView
from housecairn.barcodes import api as barcodes_api
from housecairn.budget import api as budget_api
from housecairn.events.api import EventCreateView
from housecairn.homes.api import AcceptInvitationView, HomeViewSet, InvitationViewSet, MemberViewSet
from housecairn.instance.api import (
    InstanceAuditView,
    InstanceEmailTestView,
    InstanceInvitationViewSet,
    InstanceProviderCheckView,
    InstanceProvidersView,
    InstanceProviderView,
    InstanceSettingResetView,
    InstanceSettingsView,
    InstanceUsageView,
    UnlockHomesView,
)
from housecairn.labels import api as labels_api
from housecairn.projects import api as projects_api
from housecairn.reports import api as reports_api
from housecairn.rooms import api as rooms_api
from housecairn.stock import api as stock_api
from housecairn.tasks import api as tasks_api
from housecairn.tasks.api.templates import TemplateLibraryView

app_name = "api-v1"

router = SimpleRouter(trailing_slash=True)
router.register("homes", HomeViewSet, basename="home")
router.register(r"homes/(?P<home_id>[0-9a-f-]{36})/members", MemberViewSet, basename="home-member")
router.register(
    r"homes/(?P<home_id>[0-9a-f-]{36})/invitations", InvitationViewSet, basename="home-invitation"
)
router.register(
    r"homes/(?P<home_id>[0-9a-f-]{36})/rooms", rooms_api.RoomViewSet, basename="home-room"
)
router.register(
    r"homes/(?P<home_id>[0-9a-f-]{36})/items", rooms_api.ItemViewSet, basename="home-item"
)
router.register(
    r"homes/(?P<home_id>[0-9a-f-]{36})/photos", rooms_api.PhotoViewSet, basename="home-photo"
)
for prefix, viewset, basename in (
    ("tasks", tasks_api.TaskViewSet, "home-task"),
    ("occurrences", tasks_api.OccurrenceViewSet, "home-occurrence"),
    ("contractors", tasks_api.ContractorViewSet, "home-contractor"),
    ("articles", stock_api.ArticleViewSet, "home-article"),
    ("suppliers", stock_api.SupplierViewSet, "home-supplier"),
    ("movements", stock_api.MovementViewSet, "home-movement"),
    ("attachments", tasks_api.AttachmentViewSet, "home-attachment"),
    ("task-materials", tasks_api.MaterialViewSet, "home-task-material"),
    ("labels", labels_api.LabelViewSet, "home-label"),
    ("reports", reports_api.ReportViewSet, "home-report"),
    ("projects", projects_api.ProjectViewSet, "home-project"),
    ("budget-lines", projects_api.BudgetLineViewSet, "home-budget-line"),
    ("quotes", projects_api.QuoteViewSet, "home-quote"),
    ("project-files", projects_api.ProjectFileViewSet, "home-project-file"),
    ("project-links", projects_api.ProjectLinkViewSet, "home-project-link"),
    ("project-tasks", projects_api.ProjectTaskViewSet, "home-project-task"),
):
    router.register(rf"homes/(?P<home_id>[0-9a-f-]{{36}})/{prefix}", viewset, basename=basename)

router.register("instance/invitations", InstanceInvitationViewSet, basename="instance-invitation")
router.register("instance/audit", InstanceAuditView, basename="instance-audit")

urlpatterns = [
    path("instance/settings/", InstanceSettingsView.as_view(), name="instance-settings"),
    path(
        "instance/settings/email-test/", InstanceEmailTestView.as_view(), name="instance-email-test"
    ),
    path(
        "instance/settings/<slug:key>/",
        InstanceSettingResetView.as_view(),
        name="instance-setting-reset",
    ),
    path("instance/providers/", InstanceProvidersView.as_view(), name="instance-provider-list"),
    path(
        "instance/providers/<slug:slug>/",
        InstanceProviderView.as_view(),
        name="instance-provider-detail",
    ),
    path(
        "instance/provider-check/",
        InstanceProviderCheckView.as_view(),
        name="instance-provider-check",
    ),
    path("instance/unlock/", UnlockHomesView.as_view(), name="instance-unlock"),
    path("instance/usage/", InstanceUsageView.as_view(), name="instance-usage"),
    path("account/delete/", AccountDeleteView.as_view(), name="account-delete"),
    path("me/", MeView.as_view(), name="me"),
    path("me/tokens/", TokenListView.as_view(), name="token-list"),
    path("me/tokens/<uuid:token_id>/", TokenDetailView.as_view(), name="token-detail"),
    path("me/home/", tasks_api.MyHomeView.as_view(), name="my-home"),
    path("task-templates/", TemplateLibraryView.as_view(), name="task-template-list"),
    path(
        "me/display-preferences/", tasks_api.PreferencesView.as_view(), name="display-preferences"
    ),
    path("labels/resolve/<str:code>/", labels_api.LabelResolveView.as_view(), name="label-resolve"),
    path("homes/<uuid:home_id>/today/", tasks_api.TodayView.as_view(), name="home-today"),
    path(
        "homes/<uuid:home_id>/barcodes/lookup/",
        barcodes_api.LookupView.as_view(),
        name="home-barcode-lookup",
    ),
    path(
        "homes/<uuid:home_id>/barcodes/<str:code>/",
        barcodes_api.StateView.as_view(),
        name="home-barcode",
    ),
    path(
        "homes/<uuid:home_id>/barcodes/<str:code>/photo/",
        barcodes_api.PhotoView.as_view(),
        name="home-barcode-photo",
    ),
    path(
        "homes/<uuid:home_id>/envelope/", projects_api.EnvelopeView.as_view(), name="home-envelope"
    ),
    path("budget/", budget_api.CombinedView.as_view(), name="budget-combined"),
    path("homes/<uuid:home_id>/budget/", budget_api.OverviewView.as_view(), name="home-budget"),
    path(
        "homes/<uuid:home_id>/budget/rooms/",
        budget_api.RoomsView.as_view(),
        name="home-budget-rooms",
    ),
    path(
        "homes/<uuid:home_id>/budget/projects/",
        budget_api.ProjectsView.as_view(),
        name="home-budget-projects",
    ),
    path(
        "homes/<uuid:home_id>/budget/tasks/",
        budget_api.TasksView.as_view(),
        name="home-budget-tasks",
    ),
    *router.urls,
    path("homes/<uuid:home_id>/events/", EventCreateView.as_view(), name="home-events"),
    path("homes/<uuid:home_id>/search/", rooms_api.SearchView.as_view(), name="home-search"),
    path("invitations/accept/", AcceptInvitationView.as_view(), name="invitation-accept"),
    path("", include("housecairn.notifications.api_urls")),  # docs/adr/0043
    path("", include("housecairn.calendar.api_urls")),  # docs/adr/0052
    # The sync API of the mobile app (docs/adr/0077, 0078).
    path("sync/info", SyncInfoView.as_view(), name="sync-info"),
    path("sync/", include("housecairn.sync.urls")),
]

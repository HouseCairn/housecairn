"""Web routes of homes. Stable addresses: /h/<home_id>/... (docs/adr/0010)."""

from django.urls import path

from housecairn.homes import views

app_name = "homes"

urlpatterns = [
    path("", views.HomeListView.as_view(), name="list"),
    path("new/", views.HomeCreateView.as_view(), name="create"),
    # The selector of the current home, in the app bar (design guide, "Current home").
    path("current/", views.CurrentHomeView.as_view(), name="current"),
    path("<uuid:home_id>/", views.HomeDetailView.as_view(), name="detail"),
    path("<uuid:home_id>/edit/", views.HomeEditView.as_view(), name="edit"),
    path("<uuid:home_id>/household/", views.HouseholdView.as_view(), name="household"),
    path("restore/", views.HomeRestoreView.as_view(), name="restore"),
    path("<uuid:home_id>/export/", views.HomeExportView.as_view(), name="export"),
    path(
        "<uuid:home_id>/export/<uuid:job_id>/",
        views.HomeExportJobView.as_view(),
        name="export-job",
    ),
    path(
        "<uuid:home_id>/export/<uuid:job_id>/download/",
        views.HomeExportDownloadView.as_view(),
        name="export-download",
    ),
    path(
        "<uuid:home_id>/export/<slug:entity>.csv",
        views.HomeCsvView.as_view(),
        name="export-csv",
    ),
    path("<uuid:home_id>/import/", views.HomeImportView.as_view(), name="import"),
    path("<uuid:home_id>/archive/", views.ArchiveView.as_view(), name="archive"),
    path("<uuid:home_id>/unarchive/", views.UnarchiveView.as_view(), name="unarchive"),
    path("<uuid:home_id>/delete/", views.HomeDeleteView.as_view(), name="delete"),
    path("<uuid:home_id>/audit/", views.HomeAuditView.as_view(), name="audit"),
    path("<uuid:home_id>/household/invite/", views.InviteView.as_view(), name="invite"),
    path(
        "<uuid:home_id>/household/sign-in/",
        views.RequiredProviderView.as_view(),
        name="required-provider",
    ),
    path("<uuid:home_id>/household/leave/", views.LeaveView.as_view(), name="leave"),
    path("<uuid:home_id>/household/unlock/", views.UnlockView.as_view(), name="unlock"),
    path(
        "<uuid:home_id>/household/members/<uuid:member_id>/",
        views.MemberView.as_view(),
        name="member",
    ),
    path(
        "<uuid:home_id>/household/members/<uuid:member_id>/role/",
        views.MemberRoleView.as_view(),
        name="member-role",
    ),
    path(
        "<uuid:home_id>/household/members/<uuid:member_id>/remove/",
        views.MemberRemoveView.as_view(),
        name="member-remove",
    ),
    path(
        "<uuid:home_id>/household/members/<uuid:member_id>/make-owner/",
        views.TransferView.as_view(),
        name="transfer",
    ),
    path(
        "<uuid:home_id>/household/invitations/<uuid:invitation_id>/resend/",
        views.InvitationResendView.as_view(),
        name="invitation-resend",
    ),
    path(
        "<uuid:home_id>/household/invitations/<uuid:invitation_id>/revoke/",
        views.InvitationRevokeView.as_view(),
        name="invitation-revoke",
    ),
]

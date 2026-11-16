"""Routes of the sync API under `/api/v1/sync/` (docs/adr/0077), included by `api_urls.py`.

`info` is a view of `apitokens` (lot M2) and stays routed there; these are the routes of the
`sync` app, reading first (lot M4), then the push (lot M5a), then the files (lot M6).
"""

from django.urls import path

from housecairn.sync import api

urlpatterns = [
    path("homes/", api.SyncHomesView.as_view(), name="sync-homes"),
    path("homes/<uuid:home_id>/changes", api.SyncChangesView.as_view(), name="sync-changes"),
    path("homes/<uuid:home_id>/snapshot", api.SyncSnapshotView.as_view(), name="sync-snapshot"),
    path("homes/<uuid:home_id>/push", api.SyncPushView.as_view(), name="sync-push"),
    path(
        "homes/<uuid:home_id>/files/<uuid:file_id>",
        api.SyncFileView.as_view(),
        name="sync-file",
    ),
]

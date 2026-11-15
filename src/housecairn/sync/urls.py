"""Routes of the sync API under `/api/v1/sync/` (docs/adr/0077), included by `api_urls.py`.

`info` is a view of `apitokens` (lot M2) and stays routed there; these are the routes of the
`sync` app, reading first (lot M4).
"""

from django.urls import path

from housecairn.sync import api

urlpatterns = [
    path("homes/", api.SyncHomesView.as_view(), name="sync-homes"),
    path("homes/<uuid:home_id>/changes", api.SyncChangesView.as_view(), name="sync-changes"),
    path("homes/<uuid:home_id>/snapshot", api.SyncSnapshotView.as_view(), name="sync-snapshot"),
]

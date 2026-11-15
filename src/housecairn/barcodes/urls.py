"""Web routes of the barcode lookup, under /h/<home_id>/ (docs/adr/0083)."""

from django.urls import path

from housecairn.barcodes import views

app_name = "barcodes"

urlpatterns = [
    path("barcodes/lookup/", views.LookupView.as_view(), name="lookup"),
    path("barcodes/<str:code>/photo/", views.PhotoView.as_view(), name="photo"),
]

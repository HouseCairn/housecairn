"""Addresses of the interactive guide (docs/adr/0084)."""

from django.urls import path

from housecairn.guide import views

app_name = "guide"

urlpatterns = [
    path("", views.GuideView.as_view(), name="index"),
    path("tick/", views.TickView.as_view(), name="tick"),
    path("delete/<uuid:home_id>/", views.DeleteExampleView.as_view(), name="delete"),
    path("<slug:scenario>/", views.ScenarioView.as_view(), name="scenario"),
    path("<slug:scenario>/fill/", views.FillView.as_view(), name="fill"),
]

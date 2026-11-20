"""Web routes of stock, under /h/<home_id>/ (docs/adr/0010, 0038)."""

from django.urls import path

from housecairn.stock import views

app_name = "stock"

urlpatterns = [
    path("stock/", views.ArticleListView.as_view(), name="list"),
    path("stock/new/", views.ArticleCreateView.as_view(), name="create"),
    path(
        "stock/for-item/<uuid:item_id>/link/",
        views.ItemArticleLinkView.as_view(),
        name="item-link",
    ),
    path(
        "stock/for-item/<uuid:item_id>/new/",
        views.ItemArticleCreateView.as_view(),
        name="item-create",
    ),
    path(
        "stock/for-item/<uuid:item_id>/unlink/<uuid:article_id>/",
        views.ItemArticleUnlinkView.as_view(),
        name="item-unlink",
    ),
    path("stock/<uuid:article_id>/", views.ArticleDetailView.as_view(), name="detail"),
    path("stock/<uuid:article_id>/edit/", views.ArticleEditView.as_view(), name="edit"),
    path("stock/<uuid:article_id>/delete/", views.ArticleDeleteView.as_view(), name="delete"),
    path("stock/<uuid:article_id>/label/", views.LabelView.as_view(), name="label"),
    path(
        "stock/<uuid:article_id>/movements/new/",
        views.MovementAddView.as_view(),
        name="movement-add",
    ),
    path(
        "stock/<uuid:article_id>/suppliers/new/",
        views.SupplierCreateView.as_view(),
        name="supplier-create",
    ),
    path(
        "stock/suppliers/<uuid:supplier_id>/edit/",
        views.SupplierEditView.as_view(),
        name="supplier-edit",
    ),
    path(
        "stock/suppliers/<uuid:supplier_id>/delete/",
        views.SupplierDeleteView.as_view(),
        name="supplier-delete",
    ),
]

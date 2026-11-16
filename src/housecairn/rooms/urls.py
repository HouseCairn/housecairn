"""Web routes of rooms, items and photos, under /h/<home_id>/ (docs/adr/0010, 0029)."""

from django.urls import path

from housecairn.rooms import views

app_name = "rooms"

urlpatterns = [
    path("rooms/", views.RoomListView.as_view(), name="list"),
    path("rooms/new/", views.RoomCreateView.as_view(), name="create"),
    path("rooms/<uuid:room_id>/", views.RoomDetailView.as_view(), name="detail"),
    path("rooms/<uuid:room_id>/edit/", views.RoomEditView.as_view(), name="edit"),
    path("rooms/<uuid:room_id>/memory/", views.RoomMemoryView.as_view(), name="memory"),
    path("rooms/<uuid:room_id>/delete/", views.RoomDeleteView.as_view(), name="delete"),
    path("rooms/<uuid:room_id>/move/", views.RoomMoveView.as_view(), name="move"),
    path("rooms/<uuid:room_id>/items/new/", views.ItemCreateView.as_view(), name="item-create"),
    path("rooms/<uuid:room_id>/photos/new/", views.PhotoAddView.as_view(), name="room-photo-add"),
    path("items/", views.ItemListView.as_view(), name="items"),
    path("items/new/", views.ItemAddView.as_view(), name="item-add"),
    path("items/<uuid:item_id>/", views.ItemDetailView.as_view(), name="item-detail"),
    path("items/<uuid:item_id>/edit/", views.ItemEditView.as_view(), name="item-edit"),
    path("items/<uuid:item_id>/delete/", views.ItemDeleteView.as_view(), name="item-delete"),
    path(
        "items/<uuid:item_id>/photos/new/", views.ItemPhotoAddView.as_view(), name="item-photo-add"
    ),
    path("photos/<uuid:photo_id>/", views.PhotoFileView.as_view(), name="photo"),
    path(
        "photos/<uuid:photo_id>/thumbnail/",
        views.PhotoThumbnailView.as_view(),
        name="photo-thumbnail",
    ),
    path("photos/<uuid:photo_id>/delete/", views.PhotoDeleteView.as_view(), name="photo-delete"),
    path("search/", views.SearchView.as_view(), name="search"),
]

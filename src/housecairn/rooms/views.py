"""Web views of rooms, items and photos. Rules live in `services`."""

from functools import cached_property
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View
from django.views.generic import FormView, TemplateView

from housecairn.barcodes import photos as barcode_photos
from housecairn.core import navigation
from housecairn.core.autosave import AutosaveMixin
from housecairn.homes import current
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.homes.scoping import WebHomeScopedMixin
from housecairn.rooms import conf, forms, services
from housecairn.rooms.files import photo_response
from housecairn.rooms.models import BEHIND_WALLS_HELP, Item, Photo, PhotoCategory, Room
from housecairn.tasks import services as tasks_services


class EntryView(LoginRequiredMixin, TemplateView):
    """The Rooms entry of the navigation: the rooms of the current home.

    No home to open: the page that offers to add one (docs/developer/design-guide.md,
    "Current home").
    """

    template_name = "rooms/choose_home.html"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        target = current.section_root(request, "rooms:list")
        if target:
            return redirect(target)
        return self.render_to_response({"homes": []})


class HomeRoomsMixin(WebHomeScopedMixin):
    """A page of one home: the home from the address, and whether the user may write."""

    require_write = False
    request: HttpRequest

    def get_home(self, *, allow_locked: bool = False) -> Home:
        home = super().get_home(allow_locked=allow_locked)
        if self.require_write and not home_services.can_write(self.request.user, home):
            raise PermissionDenied
        return home

    @cached_property
    def home(self) -> Home:
        return self.get_home()

    @cached_property
    def can_write(self) -> bool:
        return home_services.can_write(self.request.user, self.home)

    def get_room(self) -> Room:
        room: Room = get_object_or_404(
            services.rooms_of(self.home),
            id=self.kwargs["room_id"],
        )
        return room

    def get_item(self) -> Item:
        item: Item = get_object_or_404(
            services.items_of(self.home),
            id=self.kwargs["item_id"],
        )
        return item

    def page(self, **extra: Any) -> dict[str, Any]:
        return {"home": self.home, "can_write": self.can_write, **extra}


def _url(name: str, **kwargs: object) -> str:
    return reverse(f"rooms:{name}", kwargs=kwargs)


def _errors(form: Any, exc: ValidationError) -> None:
    form.add_error(None, exc)


# ---------- rooms ----------


class RoomListView(HomeRoomsMixin, TemplateView):
    template_name = "rooms/room_list.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        rooms = list(services.rooms_of(self.home))
        reorder = self.can_write and self.request.GET.get("reorder") == "1" and len(rooms) > 1
        # What is typed in the search field replaces the list in place, rooms and items together
        # (the same answer as the Search page, which the form still opens without script).
        query = self.request.GET.get("q", "").strip()[:120]
        return super().get_context_data(
            **self.page(
                rooms=rooms,
                reorder=reorder,
                query=query,
                results=services.search(self.home, query) if query else None,
            ),
            **kwargs,
        )


class RoomFormMixin(HomeRoomsMixin, FormView[Any]):
    require_write = True

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(**self.page(), **kwargs)


class RoomCreateView(RoomFormMixin):
    template_name = "rooms/room_form.html"
    form_class = forms.RoomForm

    def form_valid(self, form: forms.RoomForm) -> HttpResponse:
        try:
            room = services.create_room(self.request.user, self.home, **form.cleaned_data)
        except ValidationError as exc:
            _errors(form, exc)
            return self.form_invalid(form)
        return redirect(
            navigation.after_create(
                self.request,
                _url("detail", home_id=self.home.id, room_id=room.id),
                "rooms:list",
                added=_("Room added"),
                saved=_("Room added."),
            )
        )


class RoomDetailView(HomeRoomsMixin, TemplateView):
    template_name = "rooms/room_detail.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        room = self.get_room()
        everything = services.photos_for(room)
        chosen = self.request.GET.get("category", "")
        chosen = chosen if chosen in PhotoCategory.values else ""
        photos = list(everything.filter(category=chosen) if chosen else everything)
        present = set(everything.values_list("category", flat=True))
        return super().get_context_data(
            **self.page(
                room=room,
                items=room.items.order_by("name", "id"),
                photos=photos,
                category=chosen,
                categories=[
                    (value, label) for value, label in PhotoCategory.choices if value in present
                ],
                behind_walls_help=BEHIND_WALLS_HELP,
            ),
            **kwargs,
        )


class RoomEditView(AutosaveMixin, RoomFormMixin):
    template_name = "rooms/room_form.html"
    form_class: Any = forms.RoomForm

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_room()
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(room=self.get_room(), **kwargs)

    def form_valid(self, form: forms.RoomForm) -> HttpResponse:
        try:
            room = services.update_room(self.request.user, self.get_room(), **form.cleaned_data)
        except ValidationError as exc:
            _errors(form, exc)
            return self.form_invalid(form)
        messages.success(self.request, _("Room saved."))
        return redirect(
            navigation.back_url(self.request, _url("detail", home_id=self.home.id, room_id=room.id))
        )


class RoomMemoryView(RoomEditView):
    template_name = "rooms/room_memory_form.html"
    form_class = forms.RoomMemoryForm


class ConfirmDeleteView(HomeRoomsMixin, TemplateView):
    """A page that asks, then a POST that deletes: no script is needed to confirm."""

    require_write = True
    template_name = "rooms/confirm_delete.html"
    message: Any = ""

    def target(self) -> Room | Item:
        raise NotImplementedError  # pragma: no cover

    def cancel_url(self, target: Any) -> str:
        raise NotImplementedError  # pragma: no cover

    def remove(self, target: Any) -> HttpResponse:
        raise NotImplementedError  # pragma: no cover

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        target = self.target()
        cancel_url = navigation.back_url(self.request, self.cancel_url(target))
        return super().get_context_data(
            **self.page(target=target, cancel_url=cancel_url, message=self.message),
            **kwargs,
        )

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return self.remove(self.target())


class RoomDeleteView(ConfirmDeleteView):
    message = gettext_lazy("The room, its items and their photos disappear from the screens.")

    def target(self) -> Room:
        return self.get_room()

    def cancel_url(self, target: Room) -> str:
        return _url("detail", home_id=self.home.id, room_id=target.id)

    def remove(self, target: Room) -> HttpResponse:
        gone = [target.id, *target.items.values_list("id", flat=True)]
        gone += list(Photo.objects.filter(room=target).values_list("id", flat=True))
        services.delete_room(self.request.user, target)
        messages.success(self.request, _("Room deleted."))
        parent = _url("list", home_id=self.home.id)
        return redirect(navigation.after_delete(self.request, parent, gone))


class RoomMoveView(HomeRoomsMixin, View):
    """Move a room up or down the list."""

    require_write = True
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        try:
            services.move_room(request.user, self.get_room(), request.POST.get("direction", ""))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect(f"{_url('list', home_id=self.home.id)}?reorder=1")


# ---------- items ----------


class ItemFormMixin(RoomFormMixin):
    template_name = "rooms/item_form.html"
    form_class = forms.ItemForm

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["rooms"] = services.rooms_of(self.home)
        kwargs["home"] = self.home
        kwargs["brands"] = services.brands_of(self.home)
        return kwargs

    def contractor_of(self, data: dict[str, Any]) -> None:
        """Add a contractor… chosen: the new one is made here, with the item."""
        new = data.pop("new_contractor", None)
        if new:
            data["contract_contractor"] = tasks_services.create_contractor(
                self.request.user, self.home, **new
            )


class ItemCreateView(ItemFormMixin):
    # Route name of the list a creation opens the new item from (the guide's rule on `next`).
    section_list = "rooms:list"

    def get_initial(self) -> dict[str, Any]:
        return {"room": self.get_room()}

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(room=self.get_room(), **kwargs)

    def form_valid(self, form: forms.ItemForm) -> HttpResponse:
        data = dict(form.cleaned_data)
        room = data.pop("room")
        try:
            # One unit: a form refused leaves no contractor, and no audit line, behind.
            with transaction.atomic():
                self.contractor_of(data)
                item = services.create_item(self.request.user, room, **data)
        except ValidationError as exc:
            _errors(form, exc)
            return self.form_invalid(form)
        barcode_photos.keep(self.request, item, item.ean)
        return redirect(
            navigation.after_create(
                self.request,
                _url("item-detail", home_id=self.home.id, item_id=item.id),
                self.section_list,
                added=_("Item added"),
                saved=_("Item added."),
            )
        )


class ItemAddView(ItemCreateView):
    """Add an item from the Equipment list: the room is a choice of the form.

    `?room=<room id>`, set by the list when it is filtered on a room, preselects it.
    """

    section_list = "rooms:items"

    def get_room(self) -> Room | None:  # type: ignore[override]
        wanted = self.request.GET.get("room", "")
        return next((r for r in services.rooms_of(self.home) if str(r.pk) == wanted), None)

    def get_initial(self) -> dict[str, Any]:
        room = self.get_room()
        return {"room": room} if room else {}


class ItemListView(HomeRoomsMixin, TemplateView):
    """Every item of the home, all rooms together, filtered by room and by text."""

    template_name = "rooms/item_list.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        rooms = list(services.rooms_of(self.home))
        wanted = self.request.GET.get("room", "")
        room = next((r for r in rooms if str(r.pk) == wanted), None)
        query = self.request.GET.get("q", "")[:120]
        rows = services.item_rows(self.home, room=room, query=query)
        return super().get_context_data(
            **self.page(
                rows=rows,
                rooms=rooms,
                room=room,
                query=query,
                filters_count=1 if room else 0,
                everything=room is None and not query.strip(),
            ),
            **kwargs,
        )


class ItemDetailView(HomeRoomsMixin, TemplateView):
    template_name = "rooms/item_detail.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        item = self.get_item()
        today = timezone.localdate()
        return super().get_context_data(
            **self.page(
                item=item,
                photos=list(services.photos_for(item)),
                warranty=item.warranty_state(today),
                today=today,
                # The maintenance log and the cost of ownership arrive with the
                # tasks: nothing is shown until there are entries.
                journal=[],
            ),
            **kwargs,
        )


class ItemEditView(AutosaveMixin, ItemFormMixin):
    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_item()
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        item = self.get_item()
        return super().get_context_data(item=item, room=item.room, **kwargs)

    def form_valid(self, form: forms.ItemForm) -> HttpResponse:
        data = dict(form.cleaned_data)
        room = data.pop("room")
        item = self.get_item()
        try:
            with transaction.atomic():
                self.contractor_of(data)
                services.update_item(self.request.user, item, room=room, **data)
        except ValidationError as exc:
            _errors(form, exc)
            return self.form_invalid(form)
        barcode_photos.keep(self.request, item, item.ean)
        messages.success(self.request, _("Item saved."))
        return redirect(
            navigation.back_url(
                self.request, _url("item-detail", home_id=self.home.id, item_id=item.id)
            )
        )


class ItemDeleteView(ConfirmDeleteView):
    message = gettext_lazy("The item and its photos disappear from the screens.")

    def target(self) -> Item:
        return self.get_item()

    def cancel_url(self, target: Item) -> str:
        return _url("item-detail", home_id=self.home.id, item_id=target.id)

    def remove(self, target: Item) -> HttpResponse:
        gone = [target.id, *Photo.objects.filter(item=target).values_list("id", flat=True)]
        services.delete_item(self.request.user, target)
        messages.success(self.request, _("Item deleted."))
        parent = _url("detail", home_id=self.home.id, room_id=target.room_id)
        return redirect(navigation.after_delete(self.request, parent, gone))


# ---------- photos ----------


class PhotoAddView(RoomFormMixin):
    """Upload a photo of a room (`target = "room"`) or of an item (`target = "item"`)."""

    template_name = "rooms/photo_form.html"
    form_class = forms.PhotoForm
    target = "room"

    def get_target(self) -> Room | Item:
        return self.get_room() if self.target == "room" else self.get_item()

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.get_target()
        return super().get(request, *args, **kwargs)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Refuse an oversized body before reading any of it.
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
        if declared > conf.photo_max_bytes() + conf.MULTIPART_OVERHEAD:
            return HttpResponse(_("This file is too large."), status=413, content_type="text/plain")
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        target = self.get_target()
        return super().get_context_data(
            target=target,
            back_url=self.back_url(target),
            max_bytes=conf.photo_max_bytes(),
            **kwargs,
        )

    def back_url(self, target: Room | Item) -> str:
        if isinstance(target, Room):
            return _url("detail", home_id=self.home.id, room_id=target.id)
        return _url("item-detail", home_id=self.home.id, item_id=target.id)

    def form_valid(self, form: forms.PhotoForm) -> HttpResponse:
        target = self.get_target()
        data = form.cleaned_data
        try:
            services.add_photo(
                self.request.user,
                upload=data["file"],
                room=target if isinstance(target, Room) else None,
                item=target if isinstance(target, Item) else None,
                caption=data["caption"],
                category=data["category"],
            )
        except ValidationError as exc:
            form.add_error("file", exc)
            return self.form_invalid(form)
        messages.success(self.request, _("Photo added."))
        return HttpResponseRedirect(navigation.back_url(self.request, self.back_url(target)))


class ItemPhotoAddView(PhotoAddView):
    target = "item"


class PhotoFileView(HomeRoomsMixin, View):
    """The photo itself, or its thumbnail: only to the members of its home."""

    thumbnail = False
    http_method_names = ["get", "head"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        photo = get_object_or_404(services.photos_of(self.home), id=self.kwargs["photo_id"])
        return photo_response(photo, thumbnail=self.thumbnail)


class PhotoThumbnailView(PhotoFileView):
    thumbnail = True


class PhotoDeleteView(HomeRoomsMixin, View):
    require_write = True
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        photo: Photo = get_object_or_404(services.photos_of(self.home), id=self.kwargs["photo_id"])
        services.delete_photo(request.user, photo)
        messages.success(request, _("Photo deleted."))
        if photo.room_id:
            return redirect("rooms:detail", home_id=self.home.id, room_id=photo.room_id)
        return redirect("rooms:item-detail", home_id=self.home.id, item_id=photo.item_id)


# ---------- search ----------


class SearchView(HomeRoomsMixin, TemplateView):
    template_name = "rooms/search.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        query = self.request.GET.get("q", "").strip()
        return super().get_context_data(
            **self.page(query=query, results=services.search(self.home, query)), **kwargs
        )

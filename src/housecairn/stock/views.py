"""Web views of stock items, suppliers and movements. Rules live in `services`."""

from functools import cached_property
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, pgettext
from django.views import View
from django.views.generic import FormView, TemplateView

from housecairn.barcodes import photos as barcode_photos
from housecairn.core import navigation
from housecairn.core.autosave import AutosaveMixin
from housecairn.core.form_errors import banner_messages
from housecairn.homes import current
from housecairn.homes import services as home_services
from housecairn.homes.models import Home
from housecairn.homes.scoping import WebHomeScopedMixin
from housecairn.rooms import conf
from housecairn.rooms.models import Item
from housecairn.rooms.services import items_of, rooms_of
from housecairn.stock import forms, services
from housecairn.stock.files import label_response
from housecairn.stock.models import Article, Supplier
from housecairn.tasks import materials as task_materials


class EntryView(LoginRequiredMixin, TemplateView):
    """The Stock entry of the navigation: the stock of the current home.

    No home to open: the page that offers to add one (docs/developer/design-guide.md,
    "Current home").
    """

    template_name = "stock/choose_home.html"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        target = current.section_root(request, "stock:list")
        if target:
            return redirect(target)
        return self.render_to_response({"homes": []})


class HomeStockMixin(WebHomeScopedMixin):
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

    def get_article(self) -> Article:
        article: Article = get_object_or_404(
            services.articles_of(self.home), id=self.kwargs["article_id"]
        )
        return article

    def get_supplier(self) -> Supplier:
        supplier: Supplier = get_object_or_404(
            services.suppliers_of(self.home), id=self.kwargs["supplier_id"]
        )
        return supplier

    def page(self, **extra: Any) -> dict[str, Any]:
        return {"home": self.home, "can_write": self.can_write, **extra}


def _url(name: str, **kwargs: object) -> str:
    return reverse(f"stock:{name}", kwargs=kwargs)


# ---------- articles ----------


class ArticleListView(HomeStockMixin, TemplateView):
    template_name = "stock/article_list.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        query = self.request.GET.get("q", "").strip()
        low = self.request.GET.get("low") == "1"
        rows = [
            {"article": article, "low": services.is_low(article, article.quantity)}  # type: ignore[attr-defined]
            for article in services.search_articles(self.home, query, low=low)
        ]
        everything = not query and not low
        return super().get_context_data(
            **self.page(
                rows=rows,
                query=query,
                low=low,
                low_key="1" if low else "",
                low_choices=[
                    ("", pgettext("stock filter", "All")),
                    ("1", pgettext("stock filter", "Low stock")),
                ],
                everything=everything,
            ),
            **kwargs,
        )


class ArticleFormMixin(HomeStockMixin, FormView[Any]):
    require_write = True
    template_name = "stock/article_form.html"
    form_class = forms.ArticleForm

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Refuse an oversized body before reading any of it.
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
        if declared > conf.photo_max_bytes() + conf.MULTIPART_OVERHEAD:
            return HttpResponse(_("This file is too large."), status=413, content_type="text/plain")
        return super().post(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["items"] = items_of(self.home).order_by("room__position", "room__name", "name")
        kwargs["rooms"] = rooms_of(self.home)
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(**self.page(max_bytes=conf.photo_max_bytes()), **kwargs)

    def form_invalid(self, form: Any) -> HttpResponse:
        return super().form_invalid(form)

    def fail(self, form: Any, exc: ValidationError) -> HttpResponse:
        if hasattr(exc, "error_dict"):
            for field, errors in exc.error_dict.items():
                for error in errors:
                    form.add_error(field if field in form.fields else None, error)
        else:
            form.add_error(None, exc)
        return self.form_invalid(form)


class ArticleCreateView(ArticleFormMixin):
    def form_valid(self, form: forms.ArticleForm) -> HttpResponse:
        data = dict(form.cleaned_data)
        data.pop("clear_label", None)
        try:
            article = services.create_article(
                self.request.user,
                self.home,
                compatible_items=data.pop("compatible_items"),
                rooms=data.pop("rooms"),
                label=data.pop("label") or None,
                **data,
            )
        except ValidationError as exc:
            return self.fail(form, exc)
        barcode_photos.keep(self.request, article, article.ean)
        return redirect(
            navigation.after_create(
                self.request,
                _url("detail", home_id=self.home.id, article_id=article.id),
                "stock:list",
                added=_("Stock item added"),
                saved=_("Stock item added."),
            )
        )


class ArticleEditView(AutosaveMixin, ArticleFormMixin):
    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_article()
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(article=self.get_article(), **kwargs)

    def form_valid(self, form: forms.ArticleForm) -> HttpResponse:
        data = dict(form.cleaned_data)
        article = self.get_article()
        try:
            services.update_article(
                self.request.user,
                article,
                compatible_items=data.pop("compatible_items"),
                rooms=data.pop("rooms"),
                label=data.pop("label") or None,
                clear_label=data.pop("clear_label", False),
                **data,
            )
        except ValidationError as exc:
            return self.fail(form, exc)
        barcode_photos.keep(self.request, article, article.ean)
        messages.success(self.request, _("Stock item saved."))
        return redirect(
            navigation.back_url(
                self.request, _url("detail", home_id=self.home.id, article_id=article.id)
            )
        )


class ArticleDetailView(HomeStockMixin, TemplateView):
    template_name = "stock/article_detail.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        article = self.get_article()
        quantity = article.quantity  # type: ignore[attr-defined]
        return super().get_context_data(
            **self.page(
                article=article,
                quantity=quantity,
                low=services.is_low(article, quantity),
                suppliers=article.suppliers.all(),
                movements=article.movements.select_related("occurrence__task"),
                rooms=article.rooms.all(),
                compatible_items=article.compatible_items.select_related("room").order_by(
                    "room__name", "name"
                ),
                movement_form=forms.MovementForm(),
                tasks=task_materials.tasks_using(article),
            ),
            **kwargs,
        )


# ---------- the stock of an item, from the page of the item ----------


class ItemStockMixin(HomeStockMixin):
    """A page of the stock that starts from an item of the home (docs/adr/0088)."""

    require_write = True

    def get_item(self) -> Item:
        item: Item = get_object_or_404(items_of(self.home), id=self.kwargs["item_id"])
        return item

    def item_url(self) -> str:
        return reverse(
            "rooms:item-detail", kwargs={"home_id": self.home.id, "item_id": self.kwargs["item_id"]}
        )


class ItemArticleLinkView(ItemStockMixin, FormView[Any]):
    """Link articles of the stock to an item: the same link as `compatible_items`."""

    template_name = "stock/item_link_form.html"
    form_class = forms.LinkArticlesForm

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["articles"] = services.articles_to_link(self.get_item())
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        item = self.get_item()
        return super().get_context_data(
            **self.page(item=item, nothing_to_link=not services.articles_to_link(item).exists()),
            **kwargs,
        )

    def form_valid(self, form: forms.LinkArticlesForm) -> HttpResponse:
        item = self.get_item()
        try:
            services.link_articles(self.request.user, item, form.cleaned_data["articles"])
        except ValidationError as exc:
            form.add_error("articles", exc)
            return self.form_invalid(form)
        messages.success(self.request, _("Stock items linked."))
        return redirect(navigation.back_url(self.request, self.item_url()))


class ItemArticleCreateView(ItemStockMixin, ArticleCreateView):
    """The form of a new article, with the item already chosen as compatible."""

    def get_initial(self) -> dict[str, Any]:
        return {**super().get_initial(), "compatible_items": [self.get_item().pk]}

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(item=self.get_item(), **kwargs)

    def form_valid(self, form: forms.ArticleForm) -> HttpResponse:
        # Whatever the form posted, the new article fits the item it was started from.
        item = self.get_item()
        chosen = [other for other in form.cleaned_data["compatible_items"] if other.pk != item.pk]
        form.cleaned_data["compatible_items"] = [item, *chosen]
        return super().form_valid(form)


class ItemArticleUnlinkView(ItemStockMixin, View):
    """Take an article out of the stock of an item; the article stays in the stock."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        item = self.get_item()
        article = get_object_or_404(services.articles_of(self.home), id=self.kwargs["article_id"])
        services.unlink_article(request.user, item, article)
        messages.success(request, _("Stock item taken out of this equipment."))
        return redirect(navigation.stay(request, self.item_url()) + "#stock")


class ConfirmDeleteView(HomeStockMixin, TemplateView):
    """A page that asks, then a POST that deletes: no script is needed to confirm."""

    require_write = True
    template_name = "stock/confirm_delete.html"
    message: Any = ""

    def target(self) -> Article | Supplier:
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


class ArticleDeleteView(ConfirmDeleteView):
    message = gettext_lazy(
        "The stock item, its suppliers and its movements disappear from the screens."
    )

    def target(self) -> Article:
        return self.get_article()

    def cancel_url(self, target: Article) -> str:
        return _url("detail", home_id=self.home.id, article_id=target.id)

    def remove(self, target: Article) -> HttpResponse:
        gone = [target.id, *target.suppliers.values_list("id", flat=True)]
        services.delete_article(self.request.user, target)
        messages.success(self.request, _("Stock item deleted."))
        parent = _url("list", home_id=self.home.id)
        return redirect(navigation.after_delete(self.request, parent, gone))


# ---------- label photo ----------


class LabelView(HomeStockMixin, View):
    """The photo of a label: only to the members of its home."""

    http_method_names = ["get", "head"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        return label_response(self.get_article())


# ---------- movements ----------


class MovementAddView(HomeStockMixin, View):
    """Add a line to the ledger from the article page; the button pressed gives its kind."""

    require_write = True
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        article = self.get_article()
        form = forms.MovementForm(request.POST)
        # An action in place: back to the article, which keeps where it was opened from.
        page = _url("detail", home_id=self.home.id, article_id=article.id)
        back = redirect(navigation.stay(request, page) + "#movement")
        if not form.is_valid():
            for message in banner_messages(form):
                messages.error(request, message)
            return back
        data = form.cleaned_data
        try:
            services.record_movement(
                request.user,
                article,
                kind=data["kind"],
                quantity=data["quantity"],
                occurred_on=data["occurred_on"],
                note=data["note"],
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Movement recorded."))
        return back


# ---------- suppliers ----------


class SupplierFormMixin(HomeStockMixin, FormView[Any]):
    require_write = True
    template_name = "stock/supplier_form.html"
    form_class = forms.SupplierForm

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(**self.page(), **kwargs)

    def done(self, article: Article) -> HttpResponse:
        page = _url("detail", home_id=self.home.id, article_id=article.id)
        return redirect(navigation.back_url(self.request, page))


class SupplierCreateView(SupplierFormMixin):
    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return super().get_context_data(article=self.get_article(), **kwargs)

    def form_valid(self, form: forms.SupplierForm) -> HttpResponse:
        article = self.get_article()
        try:
            services.create_supplier(self.request.user, article, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)
        messages.success(self.request, _("Supplier added."))
        return self.done(article)


class SupplierEditView(SupplierFormMixin):
    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_supplier()
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        supplier = self.get_supplier()
        return super().get_context_data(supplier=supplier, article=supplier.article, **kwargs)

    def form_valid(self, form: forms.SupplierForm) -> HttpResponse:
        supplier = self.get_supplier()
        try:
            services.update_supplier(self.request.user, supplier, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)
        messages.success(self.request, _("Supplier saved."))
        return self.done(supplier.article)


class SupplierDeleteView(ConfirmDeleteView):
    message = gettext_lazy("The supplier disappears from the stock item.")

    def target(self) -> Supplier:
        return self.get_supplier()

    def cancel_url(self, target: Supplier) -> str:
        return _url("detail", home_id=self.home.id, article_id=target.article_id)

    def remove(self, target: Supplier) -> HttpResponse:
        gone = [target.id]
        services.delete_supplier(self.request.user, target)
        messages.success(self.request, _("Supplier deleted."))
        parent = _url("detail", home_id=self.home.id, article_id=target.article_id)
        return redirect(navigation.after_delete(self.request, parent, gone))

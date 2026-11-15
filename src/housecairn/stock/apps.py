from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class StockConfig(AppConfig):
    name = "housecairn.stock"
    label = "stock"
    verbose_name = _("Stock")

    def ready(self) -> None:
        from housecairn.homes import files
        from housecairn.stock import cascades, exports, syncing

        exports.register()
        files.register_media_folder("stock")
        cascades.connect()
        syncing.register()

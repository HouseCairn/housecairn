from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ReportsConfig(AppConfig):
    name = "housecairn.reports"
    label = "reports"
    verbose_name = _("Reports")

    def ready(self) -> None:
        from housecairn.homes import files
        from housecairn.reports import cascades, exports

        exports.register()
        files.register_media_folder("reports")
        cascades.connect()
